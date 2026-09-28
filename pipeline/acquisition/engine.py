"""Restartable local download/import state machine; never runs inference itself."""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from dataclasses import asdict

from pipeline.ingest.subtitles import validate_external_srt

from pipeline.config import VIDEO_EXTENSIONS
from pipeline.intake import (
    canonical_film_filename, copy_file_no_replace, is_link_or_junction,
    move_file_no_replace, release_suggestion, resolve_external_sidecars,
)
from pipeline.ingest.probe import _content_hash
from pipeline.lab.store import DuplicateJob, LabStore
from .clients import ClientError
from .cleanup import remove_cancelled_download
from .sources import validate_relative_path

MANAGED_DIRECTORY = ".scene-recall-managed"
_EXECUTABLE_SUFFIXES = {".exe", ".com", ".bat", ".cmd", ".ps1", ".vbs", ".js", ".scr", ".msi", ".lnk", ".url", ".dll"}
_EXTRA = re.compile(r"\b(sample|trailer|featurette|extras?|bonus|interview|deleted)\b", re.I)
_PART = re.compile(r"\b(?:cd|disc|disk|dvd|part|pt)\s*\d+\b|\bs\d{1,2}e\d{1,3}\b", re.I)


def feature_matches_request(relative: str, title: str, year: int) -> bool:
    """Only obvious release identity; a generic filename can use its parent."""
    def tokens(text):
        return re.findall(r"[^\W_]+", text.casefold())

    wanted = tokens(title)
    if not wanted:
        return False
    path = Path(relative)
    labels = [release_suggestion(label) for label in
              [path.name, *[parent.name for parent in path.parents if parent.name]]]
    if any(found_year is not None and found_year != year for _, found_year, _ in labels):
        return False
    generic = {"video", "movie", "film", "feature", "main", "files", "data", "download", "downloads", "release"}
    for found_title, found_year, _ in labels:
        if tokens(found_title) == wanted:
            return True
        # A differently named, explicitly dated film cannot borrow a collection
        # folder's identity. Generic undated names can use a matching parent.
        if found_year is not None or not set(tokens(found_title)).issubset(generic):
            return False
    return False


def partial_feature(relative: str, title: str) -> bool:
    label = relative.replace("_", " ").replace(".", " ").replace("-", " ")
    # "Part 2" in a requested film title is legitimate; CD/part markers added
    # to its release are not evidence of a complete feature.
    for match in _PART.finditer(label):
        if not re.search(r"\b" + re.escape(match[0]) + r"\b", title, re.I):
            return True
    return False


def extra_video(relative: str, title: str) -> bool:
    label = " ".join(re.findall(r"[^\W_]+", relative.casefold()))
    title_label = " ".join(re.findall(r"[^\W_]+", title.casefold()))
    if title_label:
        label = re.sub(r"\b" + re.escape(title_label) + r"\b", " ", label)
    return bool(_EXTRA.search(label))


def safe_path(root: Path, relative: str) -> Path:
    """Resolve only ordinary entries within an acquisition-owned root."""
    relative = validate_relative_path(relative)
    if is_link_or_junction(root):
        raise ValueError("Managed storage must not be a symlink or junction")
    candidate = root.joinpath(*relative.split("/"))
    for part in [candidate, *candidate.parents]:
        if part == root.parent:
            break
        if is_link_or_junction(part):
            raise ValueError("A managed file path contains a symlink or junction")
    if not candidate.resolve().is_relative_to(root.resolve()):
        raise ValueError("Managed file path escapes its acquisition directory")
    return candidate


def fingerprint(path: Path) -> dict:
    stat = path.stat()
    if not path.is_file() or is_link_or_junction(path):
        raise ValueError("Expected an ordinary source file")
    return {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns,
            "device": stat.st_dev, "inode": stat.st_ino}


def _windows_long_path_failure(path: Path, error: OSError) -> bool:
    """Diagnose only: never import through a path the normal pipeline cannot use."""
    if os.name != "nt" or getattr(error, "winerror", None) not in {2, 3, 206}:
        return False
    absolute = str(path.absolute())
    if len(absolute) < 260 or absolute.startswith("\\\\?\\"):
        return False
    extended = "\\\\?\\UNC\\" + absolute[2:] if absolute.startswith("\\\\") else "\\\\?\\" + absolute
    try:
        Path(extended).stat()
    except OSError:
        return False  # A genuinely missing/inaccessible file keeps its existing guard.
    return True


def inspect_media(path: Path) -> dict:
    """Bounded metadata and decoded-frame checks, not an identity/quality oracle."""
    try:
        result = subprocess.run([
            "ffprobe", "-v", "error", "-protocol_whitelist", "file,pipe", "-show_entries",
            "format=format_name,duration,start_time:stream=codec_type,codec_name,width,height,duration,start_time:stream_tags=DURATION",
            "-of", "json", str(path),
        ], capture_output=True, check=True, timeout=30)
        metadata = json.loads(result.stdout)

        def finite_number(value):
            try:
                number = float(value)
                return number if math.isfinite(number) else None
            except (TypeError, ValueError):
                return None

        container = metadata.get("format", {})
        duration = finite_number(container.get("duration"))
        streams = metadata.get("streams", [])
        video = next((stream for stream in streams if stream.get("codec_type") == "video"), None)
        if duration is None or duration <= 0 or video is None or video.get("width", 0) <= 0:
            raise ValueError("The selected file does not contain playable video")
        start, end = 0.0, duration
        origin = finite_number(container.get("start_time")) or 0.0
        video_start = finite_number(video.get("start_time"))
        relative_start = (video_start if video_start is not None else origin) - origin
        video_duration = finite_number(video.get("duration"))
        relative_end = None
        if video_duration is not None and video_duration > 0:
            relative_end = relative_start + video_duration
        elif "matroska" in str(container.get("format_name", "")).split(","):
            # Matroska often exposes only a track DURATION tag. FFmpeg writes
            # this as the ending timestamp, not a length to add to start_time.
            # Ignore malformed or out-of-container tags; never guess from frame
            # counts or retry a failed decode at a more convenient position.
            tag = video.get("tags", {}).get("DURATION")
            match = re.fullmatch(r"([0-9]{2,6}):([0-5][0-9]):([0-5][0-9](?:\.[0-9]{1,9})?)", tag) if isinstance(tag, str) else None
            if match:
                tagged_end = int(match[1]) * 3600 + int(match[2]) * 60 + float(match[3]) - origin
                if max(0.0, relative_start) < tagged_end <= duration:
                    relative_end = tagged_end
        if relative_end is not None:
            # Input -ss is relative to the container's start, not absolute PTS.
            # Sample the same first video stream as -map 0:v:0, so an audio tail
            # cannot move the last video check past EOF. Unknown epochs use the
            # existing zero-based convention; missing duration keeps fallback.
            start, end = max(0.0, relative_start), min(duration, relative_end)
            if end <= start:
                raise ValueError("The selected video stream has no playable interval within the media duration")
        for timestamp in (start, (start + end) / 2, max(start, end - 2)):
            decoded = subprocess.run([
                "ffmpeg", "-v", "error", "-nostdin", "-threads", "1",
                "-protocol_whitelist", "file,pipe", "-ss", str(timestamp), "-i", str(path), "-map", "0:v:0",
                "-frames:v", "1", "-vf", "scale=64:64", "-threads", "1",
                "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1",
            ], capture_output=True, check=True, timeout=30)
            if len(decoded.stdout) != 64 * 64 * 3:
                raise ValueError("A sampled position could not be decoded")
        return {"duration": duration, "streams": streams, "decode_samples": 3}
    except (subprocess.SubprocessError, OSError, json.JSONDecodeError, TypeError) as exc:
        raise ValueError("Media validation failed; check the download or select another video") from exc


class AcquisitionRunner:
    def __init__(self, service, *, lab_store=None, media_inspector=None, publication_check=None):
        self.service, self.store, self.config = service, service.store, service.config
        self.client = service.downloader
        self.lab = lab_store or LabStore(self.config.paths.state_dir)
        self.lab.initialize()
        self.media_inspector = media_inspector or inspect_media
        self.publication_check = publication_check or self._published

    def root(self, item) -> Path:
        if not re.fullmatch(r"[0-9a-f]{32}", item["id"]):
            raise ValueError("Invalid acquisition identity")
        incoming = self.config.paths.incoming_dir
        if is_link_or_junction(incoming):
            raise ValueError("Incoming storage must not be a symlink or junction")
        managed = incoming / MANAGED_DIRECTORY
        if is_link_or_junction(managed):
            raise ValueError("Managed storage must not be a symlink or junction")
        root = safe_path(managed, item["id"] + "/data")
        return root

    @staticmethod
    def tag(item):
        return "scene-recall-" + item["id"]

    def owned_info(self, item):
        info = self.client.info(item["info_hash"])
        if info is None:
            return None
        tags = {tag.strip() for tag in str(info.get("tags", "")).split(",")}
        if (self.tag(item) not in tags or info.get("category") != "scene-recall"
                or os.path.normcase(str(Path(info.get("save_path", "")).resolve()))
                != os.path.normcase(str(self.root(item).resolve()))):
            raise ValueError("This torrent is already managed elsewhere; its files and settings were left unchanged")
        return info

    def tick(self):
        self.store.heartbeat()
        items = self.store.list(active=True, limit=1000)
        downloading = sum(item["status"] == "downloading" for item in items)
        for item in items:
            item = self.store.get(item["id"])
            self.store.heartbeat()
            if item.get("cancellation_retry_at", 0) and item["cancellation_retry_at"] > time.time():
                continue
            if item["status"] == "needs_review" and not item["cancel_requested"]:
                continue
            if item["status"] == "queued" and downloading >= 2 and not item["cancel_requested"]:
                continue
            try:
                self.step(item)
            except (ClientError, ValueError, OSError, KeyError) as exc:
                # Client adapters redact secrets. File errors use a generic
                # message instead of arbitrary provider payloads or source URLs.
                message = (str(exc) if isinstance(exc, (ClientError, ValueError))
                           else "A required file or job is unavailable; inspect the acquisition and retry")
                latest = self.store.get(item["id"])
                if latest.get("cancel_requested") or latest["status"] == "cancelling":
                    if isinstance(exc, OSError):
                        message = "Cleanup could not access a file or drive. Close anything using the download and check that storage is connected."
                    self.store.patch(item["id"], status="cancelling", error=message,
                                     message="Cancellation cleanup will retry automatically",
                                     cancellation_retry_at=time.time() + 30)
                else:
                    self.store.patch(item["id"], status="failed", error=message,
                                     message="Acquisition needs attention", resume_status=item["status"])
            updated = self.store.get(item["id"])
            if item["status"] == "queued" and updated["status"] == "downloading":
                downloading += 1
        self.store.heartbeat()

    def step(self, item):
        if item["cancel_requested"] or item["status"] == "cancelling":
            return self._cancel(item)
        actions = {"queued": self._submit, "downloading": self._download,
                   "validating": self._validate, "importing": self._import,
                   "ingest_queued": self._ingest, "ingesting": self._ingest,
                   "cleanup": self._cleanup}
        if item["status"] in actions:
            actions[item["status"]](item)

    def _submit(self, item):
        root = self.root(item)
        root.mkdir(parents=True, exist_ok=True)
        marker = root.parent / ".acquisition.json"
        expected = {"id": item["id"], "info_hash": item["info_hash"]}
        if marker.exists():
            if json.loads(marker.read_text()) != expected:
                raise ValueError("Managed directory belongs to a different acquisition")
        else:
            with marker.open("x", encoding="utf-8") as out:
                json.dump(expected, out)
        info = self.owned_info(item)
        if info is None:
            declared_size = int(item["source"].get("total_size", 0))
            if declared_size > shutil.disk_usage(root).free:
                raise ValueError("There is not enough free space for this download")
            source = item["source"]
            torrent_data = None
            if source["kind"] != "magnet":
                descriptor = Path(source["path"])
                if not descriptor.resolve().is_relative_to((self.store.root / "torrents").resolve()):
                    raise ValueError("Stored torrent path escapes durable acquisition storage")
                torrent_data = descriptor.read_bytes()
            # A client add may become visible asynchronously even after its
            # request returns. Cancellation must reconcile that uncertain add
            # before interpreting an absent torrent as permission to delete.
            def before_submit():
                self.store.patch(item["id"], submission_attempted=True, submission_seen=False,
                                 detach_requested=False, cancel_detach_requested=False)
            if source["kind"] == "magnet":
                self.client.add_magnet(source["magnet"], str(root.resolve()), self.tag(item),
                                       before_submit=before_submit)
            else:
                self.client.add_torrent(torrent_data, str(root.resolve()), self.tag(item),
                                        before_submit=before_submit)
        self.store.patch(item["id"], status="downloading", submission_seen=info is not None,
                         message="Downloading with qBittorrent", error=None)

    def _download(self, item):
        info = self.owned_info(item)
        if info is None:
            if item.get("retry_requested"):
                self.store.patch(item["id"], status="queued", message="Reconciling the missing torrent before retrying")
                return
            raise ValueError("Torrent is no longer in qBittorrent; retry to reconcile the download")
        if not item.get("submission_seen"):
            self.store.patch(item["id"], submission_seen=True)
        state = str(info.get("state", ""))
        if item.get("retry_requested"):
            if state in {"pausedDL", "stoppedDL"}:
                self.client.start(item["info_hash"], tag=self.tag(item))
            self.store.patch(item["id"], retry_requested=False)
        if state in {"error", "missingFiles", "unknown"}:
            raise ValueError("qBittorrent reports an error or missing files; repair the download and retry")
        if not item.get("metadata_checked") and int(info.get("total_size", 0)) > 0:
            files = self.client.files(item["info_hash"])
            if files:
                try:
                    for file in files:
                        path = safe_path(self.root(item), str(file["name"]))
                        if path.suffix.lower() in _EXECUTABLE_SUFFIXES:
                            raise ValueError("This release contains executable files; choose a media-only release")
                except ValueError:
                    self.client.stop(item["info_hash"], tag=self.tag(item))
                    raise
                self.store.patch(item["id"], metadata_checked=True)
        remaining = max(0, int(info.get("total_size", 0)) - int(info.get("completed", 0)))
        if remaining > shutil.disk_usage(self.root(item)).free:
            self.client.stop(item["info_hash"], tag=self.tag(item))
            raise ValueError("There is not enough free space to finish this download")
        progress = max(0.0, min(1.0, float(info.get("progress", 0))))
        if not math.isfinite(progress):
            progress = 0.0
        changes = dict(progress=progress, download_rate=max(0, int(info.get("dlspeed", 0))),
                       eta_seconds=max(0, int(info.get("eta", 0))),
                       bytes_total=max(0, int(info.get("total_size", info.get("size", 0)))),
                       bytes_done=max(0, int(info.get("completed", 0))))
        if progress >= 1 and state not in {"checkingUP", "checkingDL", "checkingResumeData", "metaDL", "forcedMetaDL", "allocating"}:
            self.client.stop(item["info_hash"], tag=self.tag(item))
            self.store.patch(item["id"], **changes, status="validating",
                             message="Download complete; waiting for the client to stop before checking media")
        else:
            message = "Waiting for peers" if state == "stalledDL" else "Downloading with qBittorrent"
            if state in {"pausedDL", "stoppedDL"}:
                message = "Download is stopped in qBittorrent; resume it there to continue"
            self.store.patch(item["id"], **changes, message=message)

    def _inventory(self, item, info):
        root = self.root(item)
        if float(info.get("progress", 0)) < 1:
            raise ValueError("Download is no longer complete")
        files = self.client.files(item["info_hash"])
        if not files or len(files) > 10000:
            raise ValueError("Torrent has no usable bounded file inventory")
        result, paths = [], set()
        for file in files:
            relative = validate_relative_path(str(file["name"]))
            if relative.casefold() in paths:
                raise ValueError("Torrent contains duplicate file paths")
            paths.add(relative.casefold())
            path = safe_path(root, relative)
            if path.suffix.lower() in _EXECUTABLE_SUFFIXES:
                raise ValueError("This release contains executable files; choose a media-only release")
            try:
                if float(file.get("progress", 0)) < 1 or path.stat().st_size != int(file["size"]):
                    raise ValueError("The release contains incomplete files; finish downloading before import")
                result.append({"relative_path": relative, "name": path.name,
                               "size": path.stat().st_size, "fingerprint": fingerprint(path)})
            except OSError as exc:
                if _windows_long_path_failure(path, exc):
                    raise ValueError(
                        "Windows cannot access a downloaded file because its path is too long. "
                        "Shorten this release's folder or filenames through qBittorrent, keeping "
                        "the files in the same managed download location, then retry."
                    ) from None
                raise
        return result

    def _validate(self, item):
        info = self.owned_info(item)
        if info is None:
            raise ValueError("Torrent disappeared before its contents were verified")
        if info.get("state") not in {"stoppedUP", "pausedUP", "stoppedDL", "pausedDL"}:
            self.client.stop(item["info_hash"], tag=self.tag(item))
            return
        inventory = self._inventory(item, info)
        videos = [f for f in inventory if Path(f["relative_path"]).suffix.lower() in VIDEO_EXTENSIONS]
        if not videos:
            raise ValueError("No supported video was found (MKV, MP4, AVI, MOV, M4V or WebM). Archives and disc images must be extracted to a video before import.")
        videos.sort(key=lambda f: (-f["size"], f["relative_path"]))
        choice = item.get("video_choice")
        selected = next((f for f in videos if f["relative_path"] == choice), None)
        if choice and selected is None:
            raise ValueError("Reviewed video is no longer available")
        if selected is None:
            features = [f for f in videos if not extra_video(f["relative_path"], item["title"])]
            matching = [f for f in features if feature_matches_request(f["relative_path"], item["title"], item["year"])]
            if len(matching) == 1 and not partial_feature(matching[0]["relative_path"], item["title"]):
                selected = matching[0]
            if selected is None:
                return self._needs_review(item, videos, [], None,
                                          "Choose the complete main film; multiple files, split parts or unclear titles need review")
        root = self.root(item)
        source = safe_path(root, selected["relative_path"])
        before = fingerprint(source)
        media = self.media_inspector(source)
        if before != fingerprint(source):
            raise ValueError("The source changed during validation")
        # Scope generic subtitle association to the real release folder, while
        # all choices and scans remain bounded by this acquisition's root.
        first = selected["relative_path"].split("/")
        release = root / first[0] if len(first) > 1 else root
        automatic, candidates = resolve_external_sidecars(
            root.resolve(), source, release, media_duration=media.get("duration"),
            allow_generic_in_root=len(videos) == 1)
        decision = item.get("subtitle_decision")
        subtitle = automatic
        if decision is not None:
            if decision["action"] == "skip":
                subtitle = None
            elif decision["action"] == "auto":
                subtitle = automatic
            else:
                subtitle = next((c.path for c in candidates if c.path.relative_to(root).as_posix() == decision["relative_path"]), None)
                if subtitle is None and (automatic is None or automatic.relative_to(root).as_posix() != decision["relative_path"]):
                    raise ValueError("Subtitle choices changed; review this download again")
                subtitle = subtitle or automatic
        elif automatic is None and candidates:
            return self._needs_review(item, videos,
                [{"relative_path": c.path.relative_to(root).as_posix(), "excerpt": c.excerpt,
                  "validation": c.validation.summary} for c in candidates],
                selected["relative_path"], "Subtitles need checking; use Automatic or choose a track you know")
        validation = validate_external_srt(subtitle, media.get("duration")) if subtitle else None
        if validation and (not validation.valid or (
                (decision is None or decision["action"] == "auto") and not validation.automatic_eligible)):
            raise ValueError("Subtitle changed during validation; review this download again")
        if before != fingerprint(source):
            raise ValueError("The source changed during validation")
        films = self.config.paths.films_dir
        films.mkdir(parents=True, exist_ok=True)
        if is_link_or_junction(films) or source.stat().st_dev != films.stat().st_dev:
            raise ValueError("Incoming and films must be ordinary directories on the same drive")
        destination = films / canonical_film_filename(item["title"], item["year"], item["edition"], source.suffix)
        if any(child.name.casefold() == destination.name.casefold() for child in films.iterdir()):
            raise ValueError("A film with this canonical name already exists; it was left unchanged")
        plan = {"source": selected["relative_path"], "destination": str(destination.resolve()),
                "source_fingerprint": before, "film_id": _content_hash(source),
                "subtitle": subtitle.relative_to(root).as_posix() if subtitle else None,
                "subtitle_sha256": validation.sha256 if validation else None,
                "subtitle_validation": asdict(validation) if validation else None,
                "files": inventory, "media": media}
        self.store.patch(item["id"], status="importing", import_plan=plan,
                         review=None, message="Verified; preparing the canonical source")

    def _needs_review(self, item, videos, subtitles, selected, message):
        self.store.patch(item["id"], status="needs_review", message=message,
            review={"videos": [{k: v[k] for k in ("relative_path", "name", "size")} for v in videos],
                    "subtitles": subtitles, "selected_video": selected})

    def _import(self, item):
        plan, root = item["import_plan"], self.root(item)
        source = safe_path(root, plan["source"])
        destination = Path(plan["destination"])
        if (destination.parent.resolve() != self.config.paths.films_dir.resolve()
                or is_link_or_junction(self.config.paths.films_dir)
                or is_link_or_junction(destination)):
            raise ValueError("Canonical destination is no longer safe")
        info = self.owned_info(item)
        if info is not None:
            if info.get("state") not in {"stoppedUP", "pausedUP", "stoppedDL", "pausedDL"}:
                self.client.stop(item["info_hash"], tag=self.tag(item))
                return
            self.store.patch(item["id"], detach_requested=True)
            self.client.remove(item["info_hash"], delete_files=False, tag=self.tag(item))
            # Confirm detachment on the next tick before moving any client file.
            return
        if not item.get("detach_requested"):
            raise ValueError("Torrent disappeared before managed detachment; verify it before retrying")
        if source.exists():
            if fingerprint(source) != plan["source_fingerprint"]:
                raise ValueError("Verified source changed before import")
        elif not destination.exists() or fingerprint(destination) != plan["source_fingerprint"]:
            raise ValueError("The verified source could not be recovered")
        if plan["subtitle"]:
            subtitle = safe_path(root, plan["subtitle"])
            if hashlib.sha256(subtitle.read_bytes()).hexdigest() != plan["subtitle_sha256"]:
                raise ValueError("Verified subtitle changed before import")
            canonical_subtitle = destination.with_name(destination.stem + ".en.srt")
            if canonical_subtitle.exists():
                if is_link_or_junction(canonical_subtitle) or hashlib.sha256(canonical_subtitle.read_bytes()).hexdigest() != plan["subtitle_sha256"]:
                    raise ValueError("An incompatible canonical subtitle already exists")
            else:
                copy_file_no_replace(subtitle, canonical_subtitle, expected_sha256=plan["subtitle_sha256"])
        if source.exists():
            if destination.exists():
                # POSIX no-replace moves use link/unlink. Recover a crash
                # between those calls only when both names identify our exact
                # verified inode; an unrelated destination is never removed.
                if (not os.path.samefile(source, destination)
                        or fingerprint(destination) != plan["source_fingerprint"]):
                    raise ValueError("Canonical source already exists; neither file was overwritten")
                source.unlink()
            else:
                move_file_no_replace(source, destination)
        self.store.patch(item["id"], status="ingest_queued", film_path=str(destination),
                         film_id=plan["film_id"], message="Source imported; waiting for ingestion")

    def _ingest(self, item):
        if not item.get("ingest_job_id"):
            # Persisting the job id can be interrupted after enqueue. Reconcile
            # by canonical path before ever creating another hosted ingest job.
            candidates = [j for j in self.lab.ingest_snapshots()
                          if os.path.normcase(j["path"]) == os.path.normcase(item["film_path"])
                          and j["queued_at"] >= item["created_at"]]
            previous = candidates[-1] if candidates else None
            if previous and not (item.get("retry_requested") and previous["status"] not in {"queued", "running"}):
                job_id = previous["job_id"]
            else:
                try:
                    job_id = self.lab.enqueue("ingest", path=Path(item["film_path"]))["id"]
                except DuplicateJob:
                    job_id = next(j["job_id"] for j in reversed(self.lab.ingest_snapshots())
                                  if os.path.normcase(j["path"]) == os.path.normcase(item["film_path"]) and j["status"] in {"queued", "running"})
            item = self.store.patch(item["id"], ingest_job_id=job_id, retry_requested=False)
        job = self.lab.get_job(item["ingest_job_id"])
        if job["status"] in {"failed", "interrupted", "cancelled"}:
            self.store.patch(item["id"], status="failed", resume_status="ingest_queued",
                ingest_job_id=None, message="Ingestion needs attention; canonical source and staging are retained",
                error="Ingestion failed or stopped. Inspect its job details before explicitly retrying.")
        elif job["status"] == "completed":
            if not self.publication_check(item):
                raise ValueError("Ingestion completed but the film is not fully published; source files were retained")
            self.store.patch(item["id"], status="cleanup", message="Film is searchable; preserving release evidence")
        else:
            self.store.patch(item["id"], status="ingesting" if job["status"] == "running" else "ingest_queued",
                message="Ingesting the film" if job["status"] == "running" else "Waiting for the ingestion worker; Labs may be using it")

    def _published(self, item):
        from pipeline.index.writer import open_db, published_film_ids
        return item["film_id"] in published_film_ids(open_db(self.config))

    def _cleanup(self, item):
        plan, root = item["import_plan"], self.root(item)
        destination = Path(item["film_path"])
        if (is_link_or_junction(self.config.paths.films_dir)
                or fingerprint(destination) != plan["source_fingerprint"]
                or not self.publication_check(item)):
            raise ValueError("Canonical publication changed; staging cleanup was stopped")
        if item.get("cancellation_cleanup") == "complete":
            # An explicit retry can reuse a retained canonical film after the
            # user discarded its download staging. Do not invent an archive or
            # require intentionally deleted release files to reappear.
            if root.parent.exists():
                raise ValueError("Cancelled download storage unexpectedly reappeared")
            self.store.patch(item["id"], status="ready", progress=1.0,
                             message="Ready to search; cancelled download staging was already cleared")
            return
        # Keep the entire leftover release intact, including every raw subtitle
        # and unique extra. This clears staging without guessing which evidence
        # may matter later and never calls a torrent client's delete-files action.
        archive_root = self.config.paths.incoming_dir.parent / "evidence" / "managed-releases"
        if any(is_link_or_junction(p) for p in (archive_root, archive_root.parent)):
            raise ValueError("Evidence archive must not be a symlink or junction")
        archive_root.mkdir(parents=True, exist_ok=True)
        archive = archive_root / item["id"]
        if root.exists():
            self._verify_evidence(item, root.parent)
            if archive.exists():
                raise ValueError("Evidence archive destination already exists")
            if root.stat().st_dev != archive_root.stat().st_dev:
                raise ValueError("Managed evidence archive must be on the incoming drive")
            # Both resolved directory paths are fixed descendants of configured
            # storage and their entire contents were checked above.
            root.parent.rename(archive)
        else:
            self._verify_evidence(item, archive)
        self.store.patch(item["id"], status="ready", progress=1.0,
                         archive_path=str(archive), message="Ready to search; staging cleared and release evidence archived")

    def _verify_evidence(self, item, directory):
        """Apply the same journal checks before archival and after a restart."""
        if not directory.is_dir() or is_link_or_junction(directory):
            raise ValueError("Evidence ownership directory is unavailable")
        if {p.name for p in directory.iterdir()} != {"data", ".acquisition.json"}:
            raise ValueError("Acquisition ownership directory changed; cleanup was stopped")
        marker = directory / ".acquisition.json"
        if is_link_or_junction(marker) or not marker.is_file():
            raise ValueError("Evidence ownership marker is unavailable")
        if json.loads(marker.read_text()) != {"id": item["id"], "info_hash": item["info_hash"]}:
            raise ValueError("Evidence ownership could not be verified")
        root = safe_path(directory, "data")
        if not root.is_dir():
            raise ValueError("Release evidence is missing; cleanup was stopped")
        plan = item["import_plan"]
        expected = {f["relative_path"]: f for f in plan["files"] if f["relative_path"] != plan["source"]}
        actual = set()
        for folder, dirs, files in os.walk(root, followlinks=False):
            for name in dirs:
                if is_link_or_junction(Path(folder) / name):
                    raise ValueError("Evidence contains a junction; cleanup was stopped")
            for name in files:
                relative = (Path(folder) / name).relative_to(root).as_posix()
                if relative not in expected or fingerprint(safe_path(root, relative)) != expected[relative]["fingerprint"]:
                    raise ValueError("Evidence changed after validation; cleanup was stopped")
                actual.add(relative)
        if actual != set(expected):
            raise ValueError("Release evidence is missing; cleanup was stopped")

    def _cancel(self, item):
        # Recover a move whose final ledger write was interrupted. The canonical
        # file belongs to the library now, even before an ingestion job starts.
        plan = item.get("import_plan")
        if plan:
            destination = Path(plan["destination"])
            if (destination.parent.resolve() != self.config.paths.films_dir.resolve()
                    or is_link_or_junction(self.config.paths.films_dir)
                    or is_link_or_junction(destination)):
                raise ValueError("Canonical destination is no longer safe")
            if destination.exists():
                if fingerprint(destination) != plan["source_fingerprint"]:
                    raise ValueError("Canonical source changed; cancellation cleanup stopped")
                item = self.store.patch(item["id"], film_path=str(destination), film_id=plan["film_id"])
        # Enqueue can succeed immediately before a crash or a cancellation
        # request. Reconcile jobs by the same path/time ownership used by _ingest.
        job_ids = {item["ingest_job_id"]} if item.get("ingest_job_id") else set()
        if item.get("film_path"):
            job_ids.update(j["job_id"] for j in self.lab.ingest_snapshots()
                           if os.path.normcase(j["path"]) == os.path.normcase(item["film_path"])
                           and j["queued_at"] >= item["created_at"]
                           and j["status"] in {"queued", "running"})
        waiting = False
        for identity in job_ids:
            job = self.lab.get_job(identity)
            if job["status"] in {"queued", "running", "waiting_worker"}:
                self.lab.cancel(identity)
                waiting = True
        if waiting:
            self.store.patch(item["id"], status="cancelling", error=None,
                             message="Waiting for ingestion to stop before download cleanup")
            return
        info = self.owned_info(item)
        if info is not None:
            self.store.patch(item["id"], submission_seen=True)
            if info.get("state") not in {"stoppedUP", "pausedUP", "stoppedDL", "pausedDL"}:
                self.client.stop(item["info_hash"], tag=self.tag(item))
                self.store.patch(item["id"], status="cancelling", error=None,
                                 message="Waiting for the downloader to stop before cleanup")
                return
            self.store.patch(item["id"], cancel_detach_requested=True)
            self.client.remove(item["info_hash"], delete_files=False, tag=self.tag(item))
            self.store.patch(item["id"], status="cancelling", error=None,
                             message="Removing the stopped torrent before download cleanup")
            return  # Confirm detachment on a later tick, just as import does.
        if (item.get("submission_attempted") and not item.get("submission_seen")
                and not item.get("detach_requested") and not item.get("cancel_detach_requested")):
            self.store.patch(item["id"], status="cancelling",
                             message="Waiting for qBittorrent to resolve the submitted torrent before cleanup")
            return
        remove_cancelled_download(self.config.paths.incoming_dir, item,
                                  lambda owner: self.store.patch(item["id"], cancellation_owner=owner))
        self.store.patch(item["id"], status="cancelled", cancel_requested=False,
                         cancellation_cleanup="complete", cancellation_retry_at=None,
                         download_rate=0, eta_seconds=None, review=None, error=None,
                         resume_status="ingest_queued" if item.get("film_path") else "queued",
                         message=("Cancelled; download files removed. Imported film kept in the library."
                                  if item.get("film_path") else "Cancelled; download files removed"))
