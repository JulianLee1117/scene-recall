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
    # Legacy samples use keyframes/{shot_id}_{n}.webp; current short-shot
    # samples and their manifests add a sampling-profile subdirectory.
    # Writes to:
    #   film.asset_dir / "keyframes" / "{profile}" / "{shot_id}_{n}.webp"
    #   film.asset_dir / "previews"  / "{shot_id}.webm"
    #   film.asset_dir / "media-manifests" / "{shot_id_hash}.json"
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import subprocess
import time
from fractions import Fraction
from pathlib import Path
from uuid import uuid4

from pipeline.config import Config
from pipeline.ingest.probe import FilmRecord
from pipeline.ingest.shots import SHORT_SHOT_SAMPLING_PROFILE, Shot

# ---------------------------------------------------------------------------
# Display constants — not tunable thresholds
# ---------------------------------------------------------------------------

_KEYFRAME_MAX_WIDTH: int = 1280   # px — scale=1280:-1 preserves display aspect ratio
_KEYFRAME_QUALITY: int = 82
_PREVIEW_HEIGHT: int = 480         # px — scale=-1:480 preserves display aspect ratio
_PREVIEW_MAX_DURATION: float = 4.0 # seconds — cap on hover-preview length
_PREVIEW_CODEC: str = "libvpx-vp9"
_PREVIEW_CRF: int = 35
_PREVIEW_BITRATE: str = "0"
_KEYFRAME_START_PAD: float = 0.1  # seconds — avoids black frame at a hard cut
_MEDIA_CACHE_SCHEMA_VERSION: int = 1
_MEDIA_EXTRACTION_VERSION: int = 2  # v2: correct anamorphic sample aspect ratio
NATIVE_TIMESTAMP_SOURCE = "decoded_container_relative_pts_v2"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def extract_media(
    film: FilmRecord, shots: list[Shot], config: Config, *, extract_previews: bool = True,
) -> None:
    """Extract keyframe images and hover-preview clips for each shot.

    Writes to *film.asset_dir* (created if necessary):

    * ``keyframes/{profile}/{shot_id}_{n}.webp`` for current short shots
        Up to three distinct decoded WebP samples (max width 1280 px, q=82).
        Updates ``shot.keyframe_times`` with retained player-relative timestamps.
        Legacy samples omit the profile directory and retain their first-seek
        padding of :data:`_KEYFRAME_START_PAD`.

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
    extract_previews:
        False retains matching existing previews without generating them,
        allowing a sampling-only backfill to avoid unnecessary video encoding.

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
        profile_dir = _sampling_directory(shot)
        shot_kf_dir = kf_dir / profile_dir if profile_dir else kf_dir
        shot_manifest_dir = manifest_dir / profile_dir if profile_dir else manifest_dir
        shot_kf_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = _media_manifest_path(shot_manifest_dir, shot.shot_id)
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
        if shot.sampling_profile:
            keyframe_records = _extract_native_keyframes(
                film.path, shot, shot_kf_dir, cached_artifacts.get("keyframes"),
            )
        else:
            keyframe_records = _extract_keyframes(
                film.path, shot, shot_kf_dir,
                cached_records=cached_artifacts.get("keyframes"),
            )
        preview_record = cached_artifacts.get("preview")
        # A sampling-only change cannot alter the established preview recipe.
        # Check the old manifest explicitly before deciding a re-encode is needed.
        if profile_dir and not preview_record:
            legacy = _read_media_manifest(_media_manifest_path(manifest_dir, shot.shot_id))
            if legacy and _preview_identity(legacy["identity"]) == _preview_identity(expected_identity):
                preview_record = legacy["artifacts"].get("preview")
        if extract_previews:
            preview_record = _extract_preview(
                film.path, shot, preview_dir, cached_record=preview_record,
            )
        elif not _artifact_matches(preview_dir / f"{shot.shot_id}.webm", preview_record):
            preview_record = None
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
        if shot.sampling_profile:
            shot.keyframe_times = [record["timestamp"] for record in keyframe_records]
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
    if frame_index == 0 and not shot.sampling_profile:
        return max(timestamp, shot.t_start + _KEYFRAME_START_PAD)
    return timestamp


def _sampling_directory(shot: Shot) -> str:
    if not shot.sampling_profile:
        return ""
    if shot.sampling_profile != SHORT_SHOT_SAMPLING_PROFILE:
        raise ValueError(f"Unsupported sampling profile: {shot.sampling_profile!r}")
    return shot.sampling_profile


def keyframe_paths(film: FilmRecord | Path, shot: Shot) -> list[Path]:
    """Resolve retained legacy or independently versioned sampling artifacts."""
    directory = (film if isinstance(film, Path) else film.asset_dir) / "keyframes"
    profile = _sampling_directory(shot)
    if profile:
        directory /= profile
    _media_manifest_path(directory, shot.shot_id)  # validate the supplied ID
    return [directory / f"{shot.shot_id}_{index}.webp" for index in range(len(shot.keyframe_times))]


def keyframe_timestamp_source(shot: Shot) -> str:
    return NATIVE_TIMESTAMP_SOURCE if shot.sampling_profile else "ingest_keyframe_seek_v1"


def keyframe_timestamp(film: FilmRecord, shot: Shot, frame_index: int) -> float:
    """Read the actual decoded PTS for current samples; preserve legacy seeks."""
    if not shot.sampling_profile:
        return keyframe_seek_time(shot, frame_index)
    directory = film.asset_dir / "media-manifests" / _sampling_directory(shot)
    manifest = _read_media_manifest(_media_manifest_path(directory, shot.shot_id))
    identity = _media_cache_identity(_source_identity(film), shot)
    if not manifest or manifest["identity"] != identity:
        raise ValueError(f"Current native sample manifest is unavailable for {shot.shot_id}")
    records = manifest["artifacts"].get("keyframes")
    paths = keyframe_paths(film, shot)
    if not paths or not _native_records_valid(shot, paths[0].parent, records) or len(paths) != len(records) or frame_index < 0 or frame_index >= len(records):
        raise ValueError(f"Native sample evidence is invalid for {shot.shot_id}")
    return float(records[frame_index]["timestamp"])


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
    identity = {
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
    if shot.sampling_profile:
        import av
        from PIL import __version__ as pillow_version

        _sampling_directory(shot)
        # Actual PTS is output evidence, never an input that invalidates resume.
        identity["shot"].pop("keyframe_times")
        identity["shot"]["sampling_profile"] = shot.sampling_profile
        identity["keyframes"] = {
            "decoder": "pyav", "pyav_version": av.__version__,
            "pillow_version": pillow_version, "format": "webp",
            "selection": "second-middle-penultimate; first-middle-last-if-under-five",
            "timestamp": "decoded-native-pts-relative-to-container-start", "maximum_frames": 3,
            "timestamp_origin": "container-start-time-or-zero-v1",
            "scale_width": _KEYFRAME_MAX_WIDTH, "quality": _KEYFRAME_QUALITY,
        }
    return identity


def _preview_identity(identity: dict) -> dict:
    shot = identity.get("shot", {})
    return {key: identity.get(key) for key in ("schema_version", "extraction_version", "source", "preview")} | {
        "shot": {key: shot.get(key) for key in ("shot_id", "t_start", "t_end")},
    }


def _native_records_valid(shot: Shot, directory: Path, records: object) -> bool:
    if not isinstance(records, list) or not 1 <= len(records) <= 3:
        return False
    previous = -math.inf
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            return False
        timestamp = record.get("timestamp")
        if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)) or not math.isfinite(timestamp) or not shot.t_start <= timestamp < shot.t_end or timestamp <= previous:
            return False
        raw_timestamp = record.get("source_pts")
        origin = record.get("timestamp_origin")
        if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) for value in (raw_timestamp, origin)):
            return False
        if not math.isclose(raw_timestamp - origin, timestamp, rel_tol=0.0, abs_tol=1e-9):
            return False
        if not _artifact_matches(directory / f"{shot.shot_id}_{index}.webp", record):
            return False
        previous = timestamp
    return True


def _decode_native_samples(path: Path, shot: Shot) -> tuple[Fraction, list[tuple[float, object, float]]]:
    """Decode a bounded shot, retaining only seven native candidate frames.

    The second and penultimate frames avoid cut edges when five frames exist.
    Tiny shots retain every available distinct instant up to three. We use
    actual PTS relative to the container's start, matching ordinary ffmpeg -ss
    and player time. Retained raw PTS/origin make that normalization auditable.
    Also returns the stream's sample aspect ratio: decoded frames are raw,
    square-pixel-assumed pixel grids, so anamorphic sources need it to display
    at their true proportions instead of stretching to the coded pixel shape.
    """
    deadline = time.monotonic() + 45
    for preroll in (0.0, 5.0):
        # Some inter-frame sources seek to a keyframe whose first decoded image
        # is already past this short shot. Reopen once with five seconds of
        # preroll; a shorter lookback can still land beyond the whole shot.
        # keep the same deadline and accept only actual frames inside the shot.
        sar, samples = _decode_native_samples_from(path, shot, deadline, preroll)
        if samples:
            return sar, samples
    raise ValueError(f"No native video frame inside the shot boundaries ({shot.shot_id}, {shot.t_start}-{shot.t_end}s)")


def _decode_native_samples_from(path: Path, shot: Shot, deadline: float, preroll: float) -> tuple[Fraction, list[tuple[float, object, float]]]:
    import av

    midpoint = (shot.t_start + shot.t_end) / 2.0
    first: list[tuple[float, object]] = []
    middle: list[tuple[float, object]] = []
    last: list[tuple[float, object]] = []
    count = 0
    with av.open(str(path)) as container:
        stream = container.streams.video[0]
        # A missing or zero SAR means "undefined"; ordinary square pixels.
        sample_aspect_ratio = stream.sample_aspect_ratio or Fraction(1)
        origin_fraction = Fraction(container.start_time, av.time_base) if container.start_time is not None else Fraction(0)
        origin = float(origin_fraction)
        stream.thread_type = "SLICE"
        stream.codec_context.thread_count = 2
        if float(stream.metadata.get("rotate", "0")) % 360:
            raise ValueError("Native sampling requires unrotated source video")
        # Negative container epochs are legal, but some demuxers reject a
        # negative seek. Decode from the newly opened beginning in that case;
        # clamping to zero would silently skip the original opening frames.
        player_seek = shot.t_start - preroll
        absolute_start = player_seek + origin
        # A retry reaching the opening decodes from the fresh container instead
        # of seeking to zero, which can itself skip leading reordered frames.
        if absolute_start >= 0 and (preroll == 0 or player_seek > 0):
            container.seek(math.floor(absolute_start / stream.time_base), stream=stream, backward=True)
        previous = -math.inf
        for frame in container.decode(stream):
            if time.monotonic() > deadline:
                raise TimeoutError("Native shot sampling exceeded its bounded decode time")
            if frame.pts is None:
                continue
            # Subtract exact rational time bases before converting to seconds;
            # float cancellation can otherwise drop a frame exactly on a bound.
            timestamp = float(frame.pts * stream.time_base - origin_fraction)
            if timestamp >= shot.t_end:
                break
            if timestamp < shot.t_start or timestamp <= previous:
                continue
            if any(str(side.type).endswith("DISPLAYMATRIX") for side in frame.side_data):
                raise ValueError("Native sampling requires unrotated source video")
            previous = timestamp
            count += 1
            if count > 4096:
                raise ValueError("Native short-shot sampling exceeded its frame bound")
            sample = (timestamp, frame)
            if len(first) < 2:
                first.append(sample)
            last = [*last, sample][-2:]
            middle = sorted([*middle, sample], key=lambda item: (round(abs(item[0] - midpoint), 9), item[0]))[:3]
        if not first:
            return sample_aspect_ratio, []
        if count < 3:
            selected = sorted({item[0]: item for item in [*first, *last]}.values(), key=lambda item: item[0])
        else:
            beginning = first[1] if count >= 5 else first[0]
            ending = last[-2] if count >= 5 else last[-1]
            centre = min((item for item in middle if beginning[0] < item[0] < ending[0]), key=lambda item: (round(abs(item[0] - midpoint), 9), item[0]))
            selected = [beginning, centre, ending]
        # Convert only selected frames; never buffer full-shot RGB images.
        return sample_aspect_ratio, [(timestamp, frame.to_image(), origin) for timestamp, frame in selected]


def _extract_native_keyframes(path: Path, shot: Shot, directory: Path, records: object) -> list[dict]:
    if _native_records_valid(shot, directory, records):
        return [dict(record) for record in records]
    from PIL import Image

    sample_aspect_ratio, samples = _decode_native_samples(path, shot)
    result = []
    for index, (timestamp, image, origin) in enumerate(samples):
        destination = directory / f"{shot.shot_id}_{index}.webp"
        temporary = destination.with_name(f".{uuid4().hex}.webp")
        try:
            image = image.convert("RGB")
            # Decoded frames are raw, square-pixel-assumed pixel grids; correct
            # for anamorphic sources before fitting the target width, or the
            # image stretches to the coded pixel shape instead of its true one.
            display_width = image.width * float(sample_aspect_ratio)
            size = (_KEYFRAME_MAX_WIDTH, max(1, round(image.height * _KEYFRAME_MAX_WIDTH / display_width)))
            if image.size != size:
                image = image.resize(size, Image.Resampling.LANCZOS)
            image.save(temporary, format="WEBP", quality=_KEYFRAME_QUALITY)
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)
        result.append({**_artifact_record(destination), "timestamp": timestamp, "source_pts": timestamp + origin, "timestamp_origin": origin})
    return result


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
            "-vf", ("scale=w='max(2,trunc(iw*sar/2)*2)':h=ih,setsar=1,"
                    f"scale={_KEYFRAME_MAX_WIDTH}:-1"),
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
        "-vf", ("scale=w='max(2,trunc(iw*sar/2)*2)':h=ih,setsar=1,"
                f"scale=-1:{_PREVIEW_HEIGHT}"),
        "-c:v", _PREVIEW_CODEC,
        "-crf", str(_PREVIEW_CRF),
        "-b:v", _PREVIEW_BITRATE,
        "-an",
        str(out_path),
    ]
    _run_atomic_ffmpeg(cmd, out_path)
    return _artifact_record(out_path)


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
