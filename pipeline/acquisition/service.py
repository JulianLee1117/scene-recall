"""Shared acquisition commands. Network monitoring is owned by a separate worker."""
from __future__ import annotations

from pipeline.config import Config
from pipeline.intake import canonical_film_filename
from .cleanup import remove_cancelled_download
from .clients import ClientError, ProwlarrClient, QBittorrentClient
from .settings import load_settings
from .sources import parse_magnet, parse_torrent, validate_relative_path
from .store import AcquisitionConflict, AcquisitionStore, TERMINAL


class AcquisitionService:
    def __init__(self, config: Config, *, settings=None, downloader=None, search_client=None):
        self.config = config
        self.settings = settings or load_settings()
        self.store = AcquisitionStore(config.paths.state_dir)
        self.store.initialize()
        self.downloader = downloader or QBittorrentClient(self.settings)
        self.search_client = search_client or ProwlarrClient(self.settings)

    def status(self):
        downloader = {"configured": self.settings.downloads_enabled,
                      "available": False, "message": "Connect qBittorrent to enable downloads"}
        search = {"configured": self.settings.search_enabled,
                  "available": False, "message": "Connect Prowlarr to search releases; magnets and torrent files work independently"}
        if downloader["configured"]:
            try:
                version = self.downloader.status()["version"]
                downloader.update(available=True, message=f"qBittorrent {version}")
            except ClientError as exc:
                downloader["message"] = str(exc)
        if search["configured"]:
            # Configuration is separate from a live provider search; don't make
            # external indexer requests merely to display the library.
            search.update(available=True, message="Prowlarr configured")
        return {"downloader": downloader, "search": search,
                "monitor": self.store.monitor_status()}

    def list(self):
        return {"items": [self.store.public(item) for item in self.store.list(limit=200)]}

    def _add(self, source, *, title: str, year: int, edition: str = ""):
        if not self.settings.downloads_enabled:
            raise ClientError("Configure QBITTORRENT_URL before adding downloads")
        if len(self.store.list(active=True, limit=100)) >= 100:
            raise AcquisitionConflict("The acquisition queue already has 100 active items")
        title, edition = title.strip(), (edition or "").strip()
        if not title or len(title) > 180 or len(edition) > 80:
            raise ValueError("Enter a film title and a shorter edition label")
        if isinstance(year, bool) or not isinstance(year, int) or not 1888 <= year <= 2100:
            raise ValueError("Enter a year between 1888 and 2100")
        canonical_film_filename(title, year, edition, ".mkv")
        if source.torrent_bytes is not None:
            directory = self.store.root / "torrents"
            directory.mkdir(exist_ok=True)
            path = directory / (source.info_hash + ".torrent")
            try:
                with path.open("xb") as out:
                    out.write(source.torrent_bytes)
            except FileExistsError:
                if parse_torrent(path.read_bytes()).info_hash != source.info_hash:
                    raise AcquisitionConflict("Stored torrent descriptor does not match its identity") from None
            descriptor = {"kind": "torrent", "path": str(path), "total_size": source.total_size}
        else:
            descriptor = {"kind": "magnet", "magnet": source.magnet}
        return self.store.public(self.store.create(source.info_hash, title=title,
                    year=year, edition=edition, source=descriptor))

    def add_magnet(self, magnet: str, *, title: str, year: int, edition: str = ""):
        return self._add(parse_magnet(magnet), title=title, year=year, edition=edition)

    def add_torrent(self, data: bytes, filename: str, *, title: str, year: int, edition: str = ""):
        if not filename.lower().endswith(".torrent"):
            raise ValueError("Choose a .torrent file")
        return self._add(parse_torrent(data), title=title, year=year, edition=edition)

    def search(self, q: str):
        if not self.settings.search_enabled:
            raise ClientError("Configure PROWLARR_URL and PROWLARR_API_KEY to search releases")
        q = q.strip()
        if not 2 <= len(q) <= 200:
            raise ValueError("Search must contain 2–200 characters")
        return {"results": self.store.save_releases(self.search_client.search(q, limit=30))}

    def add_release(self, release_id: str, *, title: str, year: int, edition: str = ""):
        if not self.settings.search_enabled:
            raise ClientError("Prowlarr is not configured")
        source = self.search_client.fetch_release(self.store.release(release_id))
        return self._add(source, title=title, year=year, edition=edition)

    def cancel(self, identity: str, revision: int):
        item = self.store.get(identity)
        if item["status"] in {"ready", "cancelled"}:
            raise AcquisitionConflict("This acquisition is already stopped")
        if item["status"] == "cancelling":
            raise AcquisitionConflict("Cancellation and staging cleanup are already in progress")
        fallback = ("ingest_queued" if item.get("film_path") else
                    "importing" if item.get("import_plan") else "queued")
        stage = item.get("resume_status") if item["status"] == "failed" else item["status"]
        if not stage or stage in TERMINAL | {"cancelling"}:
            stage = fallback
        return self.store.public(self.store.patch(identity, revision=revision,
            status="cancelling", cancel_requested=True, cancellation_cleanup="pending",
            resume_status=stage, retry_requested=False, cancellation_retry_at=None, error=None,
            message="Cancelling; stopping work before cleaning downloaded staging files"))

    def retry(self, identity: str, revision: int):
        item = self.store.get(identity)
        if (item["status"] == "cancelling" or item.get("cancel_requested")
                or item.get("cancellation_cleanup") == "pending"):
            raise AcquisitionConflict("Wait for cancellation and staging cleanup to finish before retrying")
        if item["status"] not in {"failed", "cancelled"}:
            raise AcquisitionConflict("Only failed or cancelled acquisitions can be retried")
        if item["status"] == "cancelled" and item.get("cancellation_cleanup") == "complete":
            if item.get("film_path"):
                # Staging is deliberately gone, but the canonical movie and
                # publication lineage remain. The monitor uses this cleanup
                # receipt when completing a later successful ingestion.
                changes = dict(status="ingest_queued", resume_status="ingest_queued",
                               ingest_job_id=None, cancellation_retry_at=None)
                message = "Retry requested; the preserved canonical source will be reused"
            else:
                changes = dict(status="queued", resume_status="queued", import_plan=None,
                               metadata_checked=False, detach_requested=False, archive_path=None,
                               review=None, video_choice=None, subtitle_decision=None,
                               progress=None, download_rate=None, eta_seconds=None,
                               bytes_total=None, bytes_done=None, ingest_job_id=None,
                               film_path=None, film_id=None, cancellation_cleanup=None,
                               submission_attempted=False, submission_seen=False,
                               cancel_detach_requested=False, cancellation_owner=None,
                               cancellation_retry_at=None)
                message = "Retry requested; downloading again after staging cleanup"
            return self.store.public(self.store.patch(identity, revision=revision, **changes,
                error=None, cancel_requested=False, retry_requested=True, message=message))
        stage = item.get("resume_status") or ("importing" if item.get("import_plan") else "queued")
        if stage in TERMINAL or stage == "needs_review":
            stage = "validating"
        changes = {}
        if stage in {"ingest_queued", "ingesting"}:
            changes["ingest_job_id"] = None
        if stage == "validating":
            changes.update(video_choice=None, subtitle_decision=None)
        return self.store.public(self.store.patch(identity, revision=revision, **changes,
            status=stage, error=None, cancel_requested=False,
            retry_requested=True, message="Retry requested; verified work will be reused"))

    def review(self, identity: str, revision: int, video_path: str, subtitle_decision=None):
        item = self.store.get(identity)
        if item["status"] != "needs_review" or not item.get("review"):
            raise AcquisitionConflict("This acquisition does not need review")
        offered = item["review"]
        video_path = validate_relative_path(video_path)
        if video_path not in {v["relative_path"] for v in offered["videos"]}:
            raise ValueError("Choose one of the offered video files")
        decision = subtitle_decision
        if decision is not None:
            if hasattr(decision, "model_dump"):
                decision = decision.model_dump()
            if decision.get("action") == "use":
                selected = validate_relative_path(decision.get("relative_path", ""))
                if selected not in {v["relative_path"] for v in offered.get("subtitles", [])}:
                    raise ValueError("Choose one of the offered subtitles")
                if video_path != offered.get("selected_video"):
                    raise ValueError("Choose the video first so its subtitles can be reviewed")
                decision = {"action": "use", "relative_path": selected}
            elif decision.get("action") in {"skip", "auto"}:
                decision = {"action": decision["action"]}
            else:
                raise ValueError("Choose an English subtitle or skip subtitles")
        if (offered.get("subtitles") and video_path == offered.get("selected_video")
                and decision is None):
            raise ValueError("Choose an English subtitle or explicitly skip subtitles")
        return self.store.public(self.store.patch(identity, revision=revision,
            status="validating", video_choice=video_path, subtitle_decision=decision,
            review=None, message="Checking the reviewed selection"))

    def dismiss(self, identity: str, revision: int):
        """Forget a stopped acquisition, clearing its info_hash for reuse."""
        item = self.store.get(identity)
        if item["revision"] != revision:
            raise AcquisitionConflict("Acquisition changed; refresh and try again")
        if item["status"] not in TERMINAL:
            raise AcquisitionConflict("Only a stopped acquisition can be dismissed")
        remove_cancelled_download(self.config.paths.incoming_dir, item,
            lambda owner: self.store.patch(identity, cancellation_owner=owner))
        current = self.store.get(identity)
        self.store.delete(identity, current["revision"])
