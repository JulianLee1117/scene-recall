"""Offline, source-preserving browser playback preparation.

Only H.264/HEVC films whose selected audio needs conversion get a derived MP4.
Video packets are copied; audio becomes stereo AAC. HTTP callers only use the
filesystem-only lookup and never launch a probe, encoder or inference job.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import stat
import subprocess
import uuid
from fractions import Fraction
from pathlib import Path

from filelock import FileLock, Timeout

from pipeline.ingest.probe import FilmRecord


PROFILE = "video-copy-aac-stereo-v1"
_MANIFEST_VERSION = 1
_MAX_MANIFEST_BYTES = 32 * 1024
_PROBE_TIMEOUT_SECONDS = 15
_ENCODE_TIMEOUT_SECONDS = 1800
_SAMPLE_TIMEOUT_SECONDS = 30
_BROWSER_AUDIO_CODECS = frozenset({"aac", "mp3", "opus", "vorbis"})
_COPY_VIDEO_CODECS = frozenset({"h264", "hevc"})


class PlaybackPreparationError(RuntimeError):
    """The optional playback derivative could not be prepared or validated."""


class PlaybackSourceChanged(RuntimeError):
    """The immutable source changed; callers must not continue that ingestion."""


def _ordinary_path(path: Path) -> Path:
    """Return an absolute lexical path, rejecting links in every component."""
    path = Path(os.path.abspath(path))
    for part in (path, *path.parents):
        if part.is_symlink() or part.is_junction():
            raise PlaybackPreparationError(f"Playback path contains a link: {part}")
    return path


def _fingerprint(path: Path, *, include_path: bool = True) -> dict:
    path = _ordinary_path(path)
    info = path.stat()
    if not stat.S_ISREG(info.st_mode):
        raise PlaybackPreparationError(f"Playback expected an ordinary file: {path}")
    result = {"device": info.st_dev, "inode": info.st_ino,
              "size": info.st_size, "mtime_ns": info.st_mtime_ns}
    if include_path:
        result["path"] = str(path)
    return result


def _source_fingerprint(path: Path) -> dict:
    try:
        return _fingerprint(path)
    except (OSError, PlaybackPreparationError) as exc:
        raise PlaybackSourceChanged(f"Playback source is no longer an ordinary file: {path}") from exc


def _require_source(path: Path, expected: dict) -> None:
    if _source_fingerprint(path) != expected:
        raise PlaybackSourceChanged(f"Playback source changed during preparation: {path}")


def playback_directory(asset_dir: Path, playback_dir: Path | None = None,
                       *, film_id: str | None = None) -> Path:
    """Locate one profile without creating directories or following links."""
    if playback_dir is None:
        return _ordinary_path(asset_dir / "playback" / PROFILE)
    identity = film_id if film_id is not None else asset_dir.name
    if re.fullmatch(r"[A-Za-z0-9_-]+", identity) is None:
        raise PlaybackPreparationError("Playback film ID must be a single safe component")
    return _ordinary_path(playback_dir / identity / PROFILE)


def _read_manifest(directory: Path) -> dict:
    manifest_path = _ordinary_path(directory / "manifest.json")
    if not manifest_path.is_file() or manifest_path.stat().st_size > _MAX_MANIFEST_BYTES:
        raise PlaybackPreparationError("Playback manifest is missing or oversized")
    with manifest_path.open("rb") as handle:
        payload = handle.read(_MAX_MANIFEST_BYTES + 1)
    if len(payload) > _MAX_MANIFEST_BYTES:
        raise PlaybackPreparationError("Playback manifest is oversized")
    manifest = json.loads(payload)
    if not isinstance(manifest, dict):
        raise PlaybackPreparationError("Playback manifest must be an object")
    filename = manifest.get("filename")
    if (manifest.get("version") != _MANIFEST_VERSION or manifest.get("profile") != PROFILE
        or not isinstance(filename, str)
        or re.fullmatch(r"video-[0-9a-f]{32}\.mp4", filename) is None):
        raise PlaybackPreparationError("Playback manifest has an unsupported identity")
    return manifest


def _lookup_in_directory(source: Path, directory: Path) -> Path | None:
    try:
        manifest = _read_manifest(directory)
        output = _ordinary_path(directory / manifest["filename"])
        if (manifest.get("source") != _fingerprint(source)
            or manifest.get("output") != _fingerprint(output, include_path=False)
            or manifest["output"]["size"] <= 0):
            return None
        return output
    except (OSError, ValueError, TypeError, KeyError, PlaybackPreparationError):
        return None


def lookup_playback(source: Path, asset_dir: Path, playback_dir: Path | None = None,
                    *, film_id: str | None = None) -> Path | None:
    """Find a current derivative with bounded filesystem reads only.

    A missing, malformed, stale or linked artifact is a cache miss. The fixed
    output name must be an ordinary generated basename in the fixed profile
    directory. No source hashing or probing happens on this request path.
    """
    # The legacy receipt remains a fallback until an explicit migration has
    # published an independently validated configured copy.
    roots = [playback_dir, None] if playback_dir is not None else [None]
    for root in roots:
        try:
            directory = playback_directory(asset_dir, root, film_id=film_id)
        except PlaybackPreparationError:
            continue
        output = _lookup_in_directory(source, directory)
        if output is not None:
            return output
    return None


def playback_representation_token(path: Path) -> str:
    """Preserve a migrated byte representation while still validating its file.

    Old receipts use the established fingerprint token. Only the offline
    hash-verified relocator persists that token with the destination fingerprint.
    """
    fingerprint = _fingerprint(path, include_path=False)
    try:
        manifest = _read_manifest(path.parent)
        token = manifest.get("representation_token")
        if (manifest.get("filename") == path.name and manifest.get("output") == fingerprint
            and isinstance(token, str) and re.fullmatch(r"[0-9a-f]{24}", token)):
            return token
    except (OSError, ValueError, TypeError, PlaybackPreparationError):
        pass
    identity = [path.name, fingerprint["device"], fingerprint["inode"],
                fingerprint["size"], fingerprint["mtime_ns"]]
    return hashlib.sha256(json.dumps(identity).encode("utf-8")).hexdigest()[:24]


def _run(command: list[str], *, timeout: int) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(command, capture_output=True, check=True, timeout=timeout)
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or b"").decode("utf-8", errors="replace")[-2000:].strip()
        raise PlaybackPreparationError(f"Playback media command failed: {detail}") from exc
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PlaybackPreparationError(f"Playback media command failed: {exc}") from exc


def _probe_media(path: Path) -> dict:
    result = _run([
        "ffprobe", "-v", "error", "-protocol_whitelist", "file,pipe",
        "-show_entries", "format=duration,start_time:stream=index,codec_type,codec_name,codec_tag_string,start_time,duration,avg_frame_rate,r_frame_rate,channels:stream_disposition:stream_tags=language",
        "-of", "json", str(path),
    ], timeout=_PROBE_TIMEOUT_SECONDS)
    try:
        meta = json.loads(result.stdout)
        if not isinstance(meta, dict) or not isinstance(meta.get("streams"), list):
            raise ValueError("missing streams")
        return meta
    except (ValueError, TypeError) as exc:
        raise PlaybackPreparationError("Playback probe returned invalid metadata") from exc


def _flag(stream: dict, name: str) -> bool:
    disposition = stream.get("disposition")
    return isinstance(disposition, dict) and disposition.get(name) in (1, "1", True)


def _selected_streams(meta: dict) -> tuple[dict | None, dict | None]:
    streams = [stream for stream in meta.get("streams", []) if isinstance(stream, dict)]
    video = next((stream for stream in streams
                  if stream.get("codec_type") == "video" and not _flag(stream, "attached_pic")), None)
    audio = [stream for stream in streams if stream.get("codec_type") == "audio"]
    selected_audio = next((stream for stream in audio if _flag(stream, "default")),
                          audio[0] if audio else None)
    return video, selected_audio


def _stream_index(stream: dict) -> int:
    index = stream.get("index")
    if not isinstance(index, int) or isinstance(index, bool) or index < 0:
        raise PlaybackPreparationError("Playback probe returned an invalid stream index")
    return index


def _number(value, description: str) -> float:
    try:
        number = float(value)
        if math.isfinite(number):
            return number
    except (TypeError, ValueError):
        pass
    raise PlaybackPreparationError(f"Playback metadata lacks a finite {description}")


def _duration(meta: dict) -> float:
    duration = _number(meta.get("format", {}).get("duration"), "duration")
    if duration <= 0:
        raise PlaybackPreparationError("Playback duration must be positive")
    return duration


def _origin(meta: dict) -> float:
    return _number(meta.get("format", {}).get("start_time"), "container start time")


def _frame_tolerance(video: dict, film: FilmRecord) -> float:
    for value in (video.get("avg_frame_rate"), video.get("r_frame_rate"), str(film.fps)):
        try:
            fps = float(Fraction(value))
            if math.isfinite(fps) and fps > 0:
                return min(0.1, 1 / fps + 0.002)
        except (TypeError, ValueError, ZeroDivisionError):
            pass
    raise PlaybackPreparationError("Playback video has no usable frame rate")


def _decode_video_pts(path: Path, index: int, timestamp: float) -> float:
    # Sparse container seek tables (notably MPEG-TS) can land after the target
    # GOP. Start decoding a short distance earlier and discard pre-target frames
    # without resetting their timestamps; compare the actual displayed PTS.
    seek = max(0.0, timestamp - 2.0)
    result = _run([
        "ffmpeg", "-hide_banner", "-v", "info", "-nostdin", "-xerror",
        "-copyts", "-start_at_zero", "-threads", "2", "-filter_threads", "2",
        "-err_detect", "explode", "-hwaccel", "none", "-ss", f"{seek:.6f}",
        "-protocol_whitelist", "file,pipe", "-i", str(path), "-map", f"0:{index}",
        "-frames:v", "1", "-vf", f"select=gte(t\\,{timestamp:.6f}),showinfo", "-an", "-sn", "-dn",
        "-fps_mode", "passthrough", "-threads", "2", "-f", "null", "pipe:1",
    ], timeout=_SAMPLE_TIMEOUT_SECONDS)
    diagnostic = result.stderr.decode("utf-8", errors="replace")
    found = re.search(r"\[Parsed_showinfo[^\]]*\].*?\bpts_time:([\d.eE+-]+)", diagnostic)
    if found is None:
        raise PlaybackPreparationError(f"Playback video sample at {timestamp:.3f}s did not decode")
    return _number(found.group(1), "decoded video timestamp")


def _decode_audio_sample(path: Path, index: int, timestamp: float) -> None:
    result = _run([
        "ffmpeg", "-hide_banner", "-v", "error", "-nostdin", "-xerror",
        "-threads", "2", "-filter_threads", "2", "-err_detect", "explode",
        "-ss", f"{timestamp:.6f}", "-protocol_whitelist", "file,pipe", "-i", str(path),
        "-map", f"0:{index}", "-t", "0.25", "-vn", "-sn", "-dn",
        "-ac", "2", "-ar", "8000", "-threads", "2", "-f", "s16le", "pipe:1",
    ], timeout=_SAMPLE_TIMEOUT_SECONDS)
    if len(result.stdout) < 4:
        raise PlaybackPreparationError(f"Playback audio sample at {timestamp:.3f}s did not decode")


def _validate_output(source: Path, output: Path, meta: dict, video: dict,
                     audio: dict, film: FilmRecord) -> dict:
    derived = _probe_media(output)
    out_video, out_audio = _selected_streams(derived)
    if out_video is None or out_audio is None or (
        out_video.get("codec_name") != video.get("codec_name")
        or out_audio.get("codec_name") != "aac" or out_audio.get("channels") != 2
        or (video.get("codec_name") == "hevc" and out_video.get("codec_tag_string") != "hvc1")
    ):
        raise PlaybackPreparationError("Playback output does not have copied video and stereo AAC")
    duration = _duration(meta)
    output_duration = _duration(derived)
    if abs(output_duration - duration) > 0.1:
        raise PlaybackPreparationError("Playback output duration changed by more than 0.1 seconds")
    tolerance = _frame_tolerance(video, film)
    origin, output_origin = _origin(meta), _origin(derived)
    for original_stream, output_stream, allowed in ((video, out_video, tolerance), (audio, out_audio, 0.1)):
        original_start = _number(original_stream.get("start_time"), "stream start") - origin
        output_start = _number(output_stream.get("start_time"), "stream start") - output_origin
        if abs(original_start - output_start) > allowed:
            raise PlaybackPreparationError("Playback output changed the container-relative stream start")
    points = sorted({0.0, duration / 2, max(0.0, duration - 2.0)})
    samples = []
    for point in points:
        source_pts = _decode_video_pts(source, _stream_index(video), point)
        output_pts = _decode_video_pts(output, _stream_index(out_video), point)
        if abs(source_pts - output_pts) > tolerance:
            raise PlaybackPreparationError("Playback sampled video timestamps do not match the source timeline")
        _decode_audio_sample(output, _stream_index(out_audio), point)
        samples.append({"at": point, "source_video_pts": source_pts, "output_video_pts": output_pts})
    return {"duration": output_duration, "container_start": output_origin,
            "frame_tolerance": tolerance, "samples": samples}


def _encode_command(source: Path, temporary: Path, video: dict, audio: dict) -> list[str]:
    command = [
        "ffmpeg", "-hide_banner", "-v", "error", "-nostdin", "-n", "-xerror",
        "-copyts", "-start_at_zero", "-threads", "2", "-filter_threads", "2",
        "-protocol_whitelist", "file,pipe", "-i", str(source),
        "-map", f"0:{_stream_index(video)}", "-map", f"0:{_stream_index(audio)}",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ac", "2",
        "-threads", "2", "-sn", "-dn", "-disposition:a:0", "default",
        "-avoid_negative_ts", "disabled", "-movflags", "+faststart",
    ]
    if video.get("codec_name") == "hevc":
        command.extend(["-tag:v", "hvc1"])
    language = (audio.get("tags") or {}).get("language")
    if isinstance(language, str) and re.fullmatch(r"[A-Za-z]{2,3}", language):
        command.extend(["-metadata:s:a:0", f"language={language}"])
    return [*command, "-f", "mp4", str(temporary)]


def prepare_playback(film: FilmRecord, playback_dir: Path | None = None) -> Path | None:
    """Prepare or reuse a validated derivative; never alter the source film.

    Callers doing other film mutations must already hold film_operation_lock.
    This narrower lock also serializes independent playback preparations. Source
    mutation raises a distinct fatal error even if an encoder failure occurs.
    """
    source = Path(os.path.abspath(film.path))
    expected = _source_fingerprint(source)
    cached = lookup_playback(source, film.asset_dir, playback_dir, film_id=film.film_id)
    if cached is not None:
        _require_source(source, expected)
        return cached
    directory = playback_directory(film.asset_dir, playback_dir, film_id=film.film_id)
    temporary: Path | None = None
    temporary_manifest: Path | None = None
    try:
        directory.mkdir(parents=True, exist_ok=True)
        lock_path = _ordinary_path(directory / ".prepare.lock")
        with FileLock(lock_path, timeout=0, preserve_lock_file=True):
            _ordinary_path(directory)
            _require_source(source, expected)
            cached = lookup_playback(source, film.asset_dir, playback_dir, film_id=film.film_id)
            if cached is not None:
                _require_source(source, expected)
                return cached
            meta = _probe_media(source)
            _require_source(source, expected)
            video, audio = _selected_streams(meta)
            if video is None or audio is None or (
                video.get("codec_name") not in _COPY_VIDEO_CODECS
                or audio.get("codec_name") in _BROWSER_AUDIO_CODECS
            ):
                return None
            _duration(meta)
            _origin(meta)
            unique = uuid.uuid4().hex
            temporary = directory / f".video-{unique}.mp4"
            temporary_manifest = directory / f".manifest-{unique}.json"
            version = _run(["ffmpeg", "-version"], timeout=_PROBE_TIMEOUT_SECONDS).stdout
            ffmpeg_version = version.decode("utf-8", errors="replace").splitlines()[0][:1000]
            print(f"[playback] preparing {video['codec_name']} copy with stereo AAC", flush=True)
            _run(_encode_command(source, temporary, video, audio), timeout=_ENCODE_TIMEOUT_SECONDS)
            _require_source(source, expected)
            validation = _validate_output(source, temporary, meta, video, audio, film)
            _require_source(source, expected)
            output = _ordinary_path(directory / f"video-{unique}.mp4")
            manifest_path = _ordinary_path(directory / "manifest.json")
            manifest = {"version": _MANIFEST_VERSION, "profile": PROFILE, "source": expected,
                        "filename": output.name,
                        "video": {"index": _stream_index(video), "codec": video["codec_name"]},
                        "audio": {"index": _stream_index(audio), "codec": audio.get("codec_name"),
                                  "language": (audio.get("tags") or {}).get("language")},
                        "ffmpeg_version": ffmpeg_version, "validation": validation,
                        "output": _fingerprint(temporary, include_path=False)}
            payload = json.dumps(manifest, indent=2, allow_nan=False).encode("utf-8")
            if len(payload) > _MAX_MANIFEST_BYTES:
                raise PlaybackPreparationError("Playback manifest exceeded its bounded size")
            with temporary_manifest.open("xb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            _require_source(source, expected)
            # Publish an immutable data file first and the receipt last. Keep
            # former published outputs so already-open byte ranges stay valid.
            temporary.rename(output)
            _require_source(source, expected)
            os.replace(temporary_manifest, manifest_path)
            return output
    except PlaybackSourceChanged:
        raise
    except (OSError, ValueError, TypeError, IndexError, Timeout, PlaybackPreparationError) as exc:
        _require_source(source, expected)
        if isinstance(exc, PlaybackPreparationError):
            raise
        raise PlaybackPreparationError(f"Playback preparation failed: {exc}") from exc
    finally:
        for item in (temporary, temporary_manifest):
            if item is not None:
                try:
                    _ordinary_path(item).unlink(missing_ok=True)
                except (OSError, PlaybackPreparationError):
                    pass


def main(argv: list[str] | None = None) -> int:
    """Prepare one film outside the API and inference worker."""
    from pipeline.config import load_config
    from pipeline.ingest.locks import film_operation_lock, require_no_pending_film_relink
    from pipeline.ingest.probe import probe_film

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("film_path", type=Path)
    arguments = parser.parse_args(argv)
    config = load_config()
    candidate = probe_film(arguments.film_path, config)
    with film_operation_lock(candidate.asset_dir):
        film = probe_film(arguments.film_path, config)
        if film.film_id != candidate.film_id:
            raise PlaybackSourceChanged("Source identity changed while waiting for the film lock")
        require_no_pending_film_relink(film.asset_dir)
        output = prepare_playback(film, config.paths.playback_dir)
    print(output if output is not None else "Playback audio conversion is not needed or supported")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
