"""media.py — keyframe extraction and hover-preview generation.

For each shot detected by the shots stage, this module validates and resumes:
  1. One WebP keyframe per ``shot.keyframe_times`` entry.
  2. One VP9 WebM hover-preview clip centred on the shot midpoint.

New artifacts are written to temporary siblings and atomically renamed, so an
interrupted ffmpeg process cannot masquerade as a completed cache entry.
Each shot also has a tiny, atomically published manifest containing the source
file identity, exact shot timing, extraction recipe, and artifact fingerprints.
Existing media is reused only when that identity still matches.

Usage::

    from pipeline.ingest.media import extract_media

    extract_media(film, shots, config)
    # Writes to:
    #   film.asset_dir / "keyframes" / "{shot_id}_{n}.webp"
    #   film.asset_dir / "previews"  / "{shot_id}.webm"
    #   film.asset_dir / "media-manifests" / "{shot_id_hash}.json"
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path
from uuid import uuid4

from PIL import Image, UnidentifiedImageError

from pipeline.config import Config
from pipeline.ingest.probe import FilmRecord
from pipeline.ingest.shots import Shot

# ---------------------------------------------------------------------------
# Display constants — not tunable thresholds
# ---------------------------------------------------------------------------

_KEYFRAME_MAX_WIDTH: int = 1280   # px — scale=1280:-1 preserves aspect ratio
_KEYFRAME_QUALITY: int = 82
_PREVIEW_HEIGHT: int = 480         # px — scale=-1:480 preserves aspect ratio
_PREVIEW_MAX_DURATION: float = 4.0 # seconds — cap on hover-preview length
_PREVIEW_CODEC: str = "libvpx-vp9"
_PREVIEW_CRF: int = 35
_PREVIEW_BITRATE: str = "0"
_KEYFRAME_START_PAD: float = 0.1  # seconds — avoids black frame at a hard cut
_MEDIA_CACHE_SCHEMA_VERSION: int = 1
_MEDIA_EXTRACTION_VERSION: int = 1


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def extract_media(film: FilmRecord, shots: list[Shot], config: Config) -> None:
    """Extract keyframe images and hover-preview clips for each shot.

    Writes to *film.asset_dir* (created if necessary):

    * ``keyframes/{shot_id}_{n}.webp``
        One WebP per ``shot.keyframe_times`` entry (max width 1280 px, q=82).
        The first keyframe seek is padded by :data:`_KEYFRAME_START_PAD` to
        avoid capturing the black transitional frame at a hard cut boundary.

    * ``previews/{shot_id}.webm``
        VP9 WebM clip, 480p, CRF 35, no audio, duration ``min(4s, shot
        duration)`` centred on the shot midpoint.

    Parameters
    ----------
    film:
        Probed film record — must have valid ``path`` and ``asset_dir``.
    shots:
        List of :class:`~pipeline.ingest.shots.Shot` objects with populated
        ``keyframe_times``.
    config:
        Pipeline configuration (reserved for future per-config overrides;
        display constants are module-level, not in ``config``).

    Returns
    -------
    None
        All output is written to *film.asset_dir*; nothing is returned.
    """
    if not shots:
        return

    kf_dir = film.asset_dir / "keyframes"
    preview_dir = film.asset_dir / "previews"
    manifest_dir = film.asset_dir / "media-manifests"
    kf_dir.mkdir(parents=True, exist_ok=True)
    preview_dir.mkdir(parents=True, exist_ok=True)
    manifest_dir.mkdir(parents=True, exist_ok=True)

    source_identity = _source_identity(film)
    total = len(shots)
    for index, shot in enumerate(shots, start=1):
        manifest_path = _media_manifest_path(manifest_dir, shot.shot_id)
        expected_identity = _media_cache_identity(source_identity, shot)
        manifest = _read_media_manifest(manifest_path)
        identity_matches = (
            manifest is not None
            and manifest.get("identity") == expected_identity
        )
        manifest_artifacts = (
            manifest.get("artifacts") if identity_matches else None
        )
        cached_artifacts = (
            manifest_artifacts
            if isinstance(manifest_artifacts, dict)
            else {}
        )
        keyframe_records = _extract_keyframes(
            film.path,
            shot,
            kf_dir,
            cached_records=cached_artifacts.get("keyframes"),
        )
        preview_record = _extract_preview(
            film.path,
            shot,
            preview_dir,
            cached_record=cached_artifacts.get("preview"),
        )
        if _source_identity(film) != source_identity:
            raise RuntimeError(
                f"source film changed while extracting media: {film.path}"
            )
        _write_media_manifest(
            manifest_path,
            {
                "schema_version": _MEDIA_CACHE_SCHEMA_VERSION,
                "identity": expected_identity,
                "artifacts": {
                    "keyframes": keyframe_records,
                    "preview": preview_record,
                },
            },
        )
        if index % 100 == 0 or index == total:
            print(f"[media] {index}/{total}", flush=True)


def keyframe_seek_time(shot: Shot, frame_index: int) -> float:
    """Return the exact ffmpeg seek used for one expected shot keyframe."""
    try:
        timestamp = float(shot.keyframe_times[frame_index])
    except IndexError as exc:
        raise ValueError(
            f"shot {shot.shot_id!r} has no keyframe index {frame_index}"
        ) from exc
    if frame_index == 0:
        return max(timestamp, shot.t_start + _KEYFRAME_START_PAD)
    return timestamp


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _source_identity(film: FilmRecord) -> dict:
    """Return the source fields that make extracted media reusable."""
    source_path = film.path.resolve(strict=True)
    stat = source_path.stat()
    if not source_path.is_file():
        raise FileNotFoundError(f"source film is not a file: {source_path}")
    return {
        "film_id": film.film_id,
        "path": str(source_path),
        "size_bytes": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
    }


def _media_manifest_path(manifest_dir: Path, shot_id: str) -> Path:
    """Return a short, safe per-shot manifest path beneath *manifest_dir*."""
    if (
        not shot_id
        or shot_id in {".", ".."}
        or "/" in shot_id
        or "\\" in shot_id
        or Path(shot_id).name != shot_id
    ):
        raise ValueError(f"unsafe shot_id for media cache: {shot_id!r}")
    # Film IDs and shot IDs are deliberately verbose. Hashing only the
    # sidecar filename keeps real Windows paths below legacy MAX_PATH limits;
    # the complete shot ID remains in the manifest identity.
    filename = hashlib.sha256(shot_id.encode("utf-8")).hexdigest()[:32]
    return manifest_dir / f"{filename}.json"


def _media_cache_identity(source_identity: dict, shot: Shot) -> dict:
    """Build the complete, JSON-stable identity for one shot's media."""
    return {
        "schema_version": _MEDIA_CACHE_SCHEMA_VERSION,
        "extraction_version": _MEDIA_EXTRACTION_VERSION,
        "source": dict(source_identity),
        "shot": {
            "shot_id": shot.shot_id,
            "t_start": float(shot.t_start),
            "t_end": float(shot.t_end),
            "keyframe_times": [
                float(timestamp) for timestamp in shot.keyframe_times
            ],
        },
        "keyframes": {
            "executable": "ffmpeg",
            "format": "webp",
            "seek": "fast-input",
            "first_frame_start_pad_seconds": _KEYFRAME_START_PAD,
            "frames_per_output": 1,
            "scale_width": _KEYFRAME_MAX_WIDTH,
            "scale_height": -1,
            "quality": _KEYFRAME_QUALITY,
        },
        "preview": {
            "executable": "ffmpeg",
            "format": "webm",
            "seek": "fast-input",
            "placement": "shot-midpoint-clamped-to-shot",
            "max_duration_seconds": _PREVIEW_MAX_DURATION,
            "scale_width": -1,
            "scale_height": _PREVIEW_HEIGHT,
            "video_codec": _PREVIEW_CODEC,
            "crf": _PREVIEW_CRF,
            "bitrate": _PREVIEW_BITRATE,
            "audio": False,
        },
    }


def _read_media_manifest(path: Path) -> dict | None:
    """Read a structurally valid current-schema manifest, or return ``None``."""
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(manifest, dict):
        return None
    if manifest.get("schema_version") != _MEDIA_CACHE_SCHEMA_VERSION:
        return None
    if not isinstance(manifest.get("identity"), dict):
        return None
    if not isinstance(manifest.get("artifacts"), dict):
        return None
    return manifest


def _write_media_manifest(path: Path, manifest: dict) -> None:
    """Atomically publish *manifest* after all shot artifacts are complete."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{uuid4().hex}.json")
    serialized = json.dumps(
        manifest,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _artifact_record(path: Path) -> dict:
    """Return a compact fingerprint for one completed media artifact."""
    stat = path.stat()
    return {
        "name": path.name,
        "size_bytes": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "sha256": _sha256_file(path),
    }


def _artifact_matches(path: Path, record: object) -> bool:
    """Return whether *path* exactly matches a cached artifact fingerprint."""
    if not isinstance(record, dict) or record.get("name") != path.name:
        return False
    try:
        stat = path.stat()
    except OSError:
        return False
    if not path.is_file() or stat.st_size <= 0:
        return False
    if record.get("size_bytes") != stat.st_size:
        return False
    if record.get("mtime_ns") != stat.st_mtime_ns:
        return False
    expected_digest = record.get("sha256")
    if not isinstance(expected_digest, str) or len(expected_digest) != 64:
        return False
    try:
        return _sha256_file(path) == expected_digest
    except OSError:
        return False


def _sha256_file(path: Path) -> str:
    """Hash a media artifact without loading the whole file into memory."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _extract_keyframes(
    film_path: Path,
    shot: Shot,
    kf_dir: Path,
    *,
    cached_records: object = None,
) -> list[dict]:
    """Extract one WebP still per ``shot.keyframe_times`` entry via ffmpeg.

    The first keyframe (n=0) is padded so that the seek is at least
    ``shot.t_start + _KEYFRAME_START_PAD`` to avoid capturing a black or
    transitional frame at the start of a hard cut.
    """
    records = cached_records if isinstance(cached_records, list) else []
    results: list[dict] = []
    for n, _timestamp in enumerate(shot.keyframe_times):
        out_path = kf_dir / f"{shot.shot_id}_{n}.webp"
        cached_record = records[n] if n < len(records) else None
        if _artifact_matches(out_path, cached_record):
            # The fingerprint includes a full content hash, so a match is
            # already proof of the published artifact; reuse its record
            # instead of re-hashing and re-decoding the same bytes.
            results.append(dict(cached_record))
            continue

        # Pad the seek for the first keyframe to avoid black frames at cuts.
        seek_t = keyframe_seek_time(shot, n)

        cmd = [
            "ffmpeg",
            "-y",
            "-nostdin",
            "-threads", "2",
            "-filter_threads", "1",
            "-ss", str(seek_t),
            "-i", str(film_path),
            "-frames:v", "1",
            "-vf", f"scale={_KEYFRAME_MAX_WIDTH}:-1",
            "-q:v", str(_KEYFRAME_QUALITY),
            str(out_path),
        ]
        _run_atomic_ffmpeg(cmd, out_path)
        results.append(_artifact_record(out_path))
    return results


def _extract_preview(
    film_path: Path,
    shot: Shot,
    preview_dir: Path,
    *,
    cached_record: object = None,
) -> dict:
    """Extract a VP9 WebM hover-preview clip centred on the shot midpoint.

    Duration is ``min(_PREVIEW_MAX_DURATION, shot duration)``, centred on the
    midpoint.  ``-ss`` is placed before ``-i`` for fast input seeking.
    """
    out_path = preview_dir / f"{shot.shot_id}.webm"
    if _artifact_matches(out_path, cached_record):
        # A full-hash fingerprint match already proves the published clip;
        # skip the per-run ffprobe and re-hash.
        assert isinstance(cached_record, dict)
        return dict(cached_record)

    duration = shot.t_end - shot.t_start
    clip_dur = min(_PREVIEW_MAX_DURATION, duration)
    midpoint = shot.t_start + duration / 2.0
    half_dur = clip_dur / 2.0

    # Clamp seek start so we don't seek before the shot boundary.
    seek_start = max(shot.t_start, midpoint - half_dur)
    # Adjust the clip duration in case clamping shifted the start.
    actual_dur = min(clip_dur, shot.t_end - seek_start)

    cmd = [
        "ffmpeg",
        "-y",
        "-nostdin",
        "-threads", "2",
        "-filter_threads", "1",
        "-ss", str(seek_start),
        "-i", str(film_path),
        "-t", str(actual_dur),
        "-vf", f"scale=-1:{_PREVIEW_HEIGHT}",
        "-c:v", _PREVIEW_CODEC,
        "-crf", str(_PREVIEW_CRF),
        "-b:v", _PREVIEW_BITRATE,
        "-an",
        str(out_path),
    ]
    _run_atomic_ffmpeg(cmd, out_path)
    return _artifact_record(out_path)


def _is_valid_keyframe(path: Path) -> bool:
    """Return whether *path* is a complete decodable WebP image."""
    if not path.is_file() or path.stat().st_size <= 0:
        return False
    try:
        with Image.open(path) as image:
            if image.format != "WEBP":
                return False
            image.verify()
    except (OSError, SyntaxError, UnidentifiedImageError):
        return False
    return True


def _is_valid_preview(path: Path) -> bool:
    """Return whether ffprobe can read a positive-duration WebM preview."""
    if not path.is_file() or path.stat().st_size <= 0:
        return False
    result = subprocess.run(
        [
            "ffprobe",
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return False
    try:
        duration = float(json.loads(result.stdout)["format"]["duration"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return False
    return duration > 0.0


def _run_atomic_ffmpeg(cmd: list[str], destination: Path) -> None:
    """Run ffmpeg into a temporary sibling, then atomically publish the file."""
    temporary = destination.with_name(
        f".{uuid4().hex}{destination.suffix}"
    )
    temporary_cmd = [*cmd[:-1], str(temporary)]
    try:
        subprocess.run(temporary_cmd, capture_output=True, check=True)
        if not temporary.is_file() or temporary.stat().st_size <= 0:
            raise RuntimeError(f"ffmpeg produced no media at {destination}")
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
