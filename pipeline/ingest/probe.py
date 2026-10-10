"""probe.py — film ingestion: content hash, ffprobe metadata, asset dir setup.

Usage::

    from pipeline.ingest.probe import probe_film, FilmRecord

    record = probe_film(Path("/path/to/film.mkv"), config)
    print(record.film_id, record.duration, record.fps)
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

from pipeline.config import Config

# How many bytes to read from each end of the file for the content hash.
_CONTENT_HASH_CHUNK_BYTES = 4 * 1024 * 1024  # 4 MB

# Subtitle codecs FFmpeg can convert directly into the SRT text consumed by
# the dialogue parser. Bitmap codecs such as PGS require OCR and therefore use
# the Whisper audio fallback instead.
_TEXT_SUBTITLE_CODECS = frozenset({
    "ass",
    "jacosub",
    "microdvd",
    "mov_text",
    "mpl2",
    "pjs",
    "realtext",
    "sami",
    "ssa",
    "stl",
    "subrip",
    "subviewer",
    "subviewer1",
    "text",
    "vplayer",
    "webvtt",
})


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class FilmRecord:
    """Metadata for a single film file, produced by :func:`probe_film`."""

    film_id: str        # SHA-256 hex digest of first+last 4 MB of the source
    path: Path          # Absolute path to the source file
    asset_dir: Path     # config.paths.assets_dir / film_id (created on disk)
    duration: float     # Total duration in seconds (float64)
    fps: float          # Frames per second of the primary video stream
    has_embedded_subs: bool  # True if at least one subtitle stream exists
    title: str          # From the authoritative filename stem
    text_subtitle_stream_index: int | None = None
    primary_audio_language_tag: str | None = None
    hdr_transfer: str | None = None   # PQ or HLG transfer of the video stream; None for SDR


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def probe_film(path: Path, config: Config) -> FilmRecord:
    """Probe *path* with ffprobe and return a :class:`FilmRecord`.

    Parameters
    ----------
    path:
        Path to the film file (any container ffprobe can read).
    config:
        Pipeline configuration.  ``config.paths.assets_dir`` is used to
        determine the asset directory for this film.

    Returns
    -------
    FilmRecord
        Populated record with the asset directory already created on disk.
    """
    path = path.resolve()

    film_id = _content_hash(path)
    meta = _ffprobe(path)
    duration = _parse_duration(meta)
    fps = _parse_fps(meta)
    has_subs = _has_subtitle_streams(meta)
    text_subtitle_stream_index = _text_subtitle_stream_index(meta)
    primary_audio_language_tag = _primary_audio_language_tag(meta)
    title = _parse_title(meta, path)
    hdr = hdr_transfer(meta)

    asset_dir = config.paths.assets_dir / film_id
    asset_dir.mkdir(parents=True, exist_ok=True)

    return FilmRecord(
        film_id=film_id,
        path=path,
        asset_dir=asset_dir,
        duration=duration,
        fps=fps,
        has_embedded_subs=has_subs,
        title=title,
        text_subtitle_stream_index=text_subtitle_stream_index,
        primary_audio_language_tag=primary_audio_language_tag,
        hdr_transfer=hdr,
    )


# Transfer characteristics that mark a high-dynamic-range master. Every stage decodes to 8-bit
# RGB through ffmpeg's default conversion, which has no tone mapping: an HDR source would index
# dim, desaturated keyframes, captions and embeddings. Such a file is refused at ingest.
_HDR_TRANSFERS = {"smpte2084": "PQ (HDR10)", "arib-std-b67": "HLG"}


def hdr_transfer(meta: dict) -> str | None:
    """The HDR transfer name of the first video stream, or None for an SDR source."""
    for stream in meta.get("streams", []) or []:
        if stream.get("codec_type") == "video":
            return _HDR_TRANSFERS.get(str(stream.get("color_transfer") or "").lower())
    return None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _content_hash(path: Path) -> str:
    """Return the SHA-256 hex digest of the first + last 4 MB of *path*.

    For files smaller than 8 MB, the entire file is hashed.
    """
    h = hashlib.sha256()
    size = path.stat().st_size

    with path.open("rb") as fh:
        if size <= _CONTENT_HASH_CHUNK_BYTES * 2:
            # Small file: hash the whole thing.
            h.update(fh.read())
        else:
            # Large file: hash the head chunk.
            h.update(fh.read(_CONTENT_HASH_CHUNK_BYTES))
            # Seek to the last chunk and hash it.
            fh.seek(-_CONTENT_HASH_CHUNK_BYTES, 2)
            h.update(fh.read(_CONTENT_HASH_CHUNK_BYTES))

    return h.hexdigest()


def _ffprobe(path: Path) -> dict:
    """Run ffprobe on *path* and return the parsed JSON output."""
    cmd = [
        "ffprobe",
        "-v", "quiet",
        "-print_format", "json",
        "-show_streams",
        "-show_format",
        str(path),
    ]
    # ffprobe emits UTF-8 JSON. Capture bytes so Windows does not decode the
    # pipe with its locale codec (for example cp1252, where byte 0x8d is
    # undefined even when it is part of a valid UTF-8 sequence).
    result = subprocess.run(cmd, capture_output=True, check=True)
    try:
        output = result.stdout.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RuntimeError(
            f"ffprobe returned non-UTF-8 JSON for {path.name}: {exc}"
        ) from exc
    try:
        metadata = json.loads(output)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"ffprobe returned invalid JSON for {path.name}: {exc}"
        ) from exc
    if not isinstance(metadata, dict):
        raise RuntimeError(
            f"ffprobe returned a non-object JSON document for {path.name}"
        )
    return metadata


def _parse_duration(meta: dict) -> float:
    """Extract duration (seconds, float) from ffprobe metadata.

    Preference order:
    1. ``format.duration`` (most reliable for container-level duration)
    2. ``duration`` field of the first video stream
    """
    fmt = meta.get("format", {})
    if "duration" in fmt:
        return float(fmt["duration"])

    for stream in meta.get("streams", []):
        if stream.get("codec_type") == "video" and "duration" in stream:
            return float(stream["duration"])

    raise ValueError("Could not determine duration from ffprobe output")


def _parse_fps(meta: dict) -> float:
    """Extract FPS from the first video stream.

    ffprobe reports FPS as a rational string like ``"30000/1001"`` or
    ``"30/1"``.  We parse it with :class:`fractions.Fraction` to get an
    exact float.

    Preference: ``r_frame_rate`` (real / coded frame rate) over
    ``avg_frame_rate`` (may be 0/0 for VFR streams).
    """
    for stream in meta.get("streams", []):
        if stream.get("codec_type") != "video":
            continue

        for key in ("r_frame_rate", "avg_frame_rate"):
            raw = stream.get(key, "")
            if raw and raw != "0/0":
                try:
                    return float(Fraction(raw))
                except (ValueError, ZeroDivisionError):
                    continue

    raise ValueError("Could not determine FPS from ffprobe output")


def _has_subtitle_streams(meta: dict) -> bool:
    """Return True if the file contains at least one subtitle stream."""
    return any(
        s.get("codec_type") == "subtitle"
        for s in meta.get("streams", [])
    )


def _text_subtitle_stream_index(meta: dict) -> int | None:
    """Select an unambiguous English dialogue track from container metadata.

    Language tags and dispositions cannot verify the subtitle contents or sync.
    Unknown language, partial tracks and unresolved ties use audio transcription.
    Ordinary tracks take precedence over accessibility variants; a unique
    default breaks a tie only within the preferred group.
    """
    candidates: list[tuple[int, bool, bool]] = []
    for stream in meta.get("streams", []):
        if not isinstance(stream, dict) or (
            stream.get("codec_type") != "subtitle"
            or stream.get("codec_name") not in _TEXT_SUBTITLE_CODECS
        ):
            continue
        index = stream.get("index")
        if not isinstance(index, int) or isinstance(index, bool) or index < 0:
            continue
        tags = stream.get("tags")
        if not isinstance(tags, dict):
            continue
        language = tags.get("language")
        if not isinstance(language, str) or language.strip().casefold() not in {"en", "eng"}:
            continue
        disposition = stream.get("disposition") or {}
        if not isinstance(disposition, dict):
            continue
        # ffprobe normally emits integer flags; accept their string equivalents
        # without treating a string "0" as an enabled flag.
        def flagged(name: str) -> bool:
            return disposition.get(name) in (1, "1", True)

        title = " ".join(str(tags.get(key) or "") for key in ("title", "handler_name"))
        if flagged("forced") or flagged("comment") or re.search(
            r"\b(?:forced|commentary|signs|songs)\b", title, re.IGNORECASE
        ):
            continue
        accessibility = flagged("hearing_impaired") or bool(re.search(
            r"\b(?:sdh|cc|hoh|hearing[\s_-]+impaired)\b", title, re.IGNORECASE
        ))
        candidates.append((index, accessibility, flagged("default")))

    ordinary = [candidate for candidate in candidates if not candidate[1]]
    preferred = ordinary or candidates
    if len(preferred) == 1:
        return preferred[0][0]
    defaults = [candidate for candidate in preferred if candidate[2]]
    return defaults[0][0] if len(defaults) == 1 else None


def _primary_audio_language_tag(meta: dict) -> str | None:
    """Return a trusted English tag on the first audio stream, if present.

    faster-whisper decodes the container's primary audio stream by default, so
    only that stream's tag can safely inform its language option. Other tags
    remain on model detection until a measured case justifies extending this
    deliberately narrow mapping.
    """
    for stream in meta.get("streams", []):
        if stream.get("codec_type") != "audio":
            continue
        tags = stream.get("tags")
        if not isinstance(tags, dict):
            return None
        language = tags.get("language")
        if not isinstance(language, str):
            return None
        normalized = language.strip().casefold()
        return normalized if normalized in {"en", "eng"} else None
    return None


def _parse_title(meta: dict, path: Path) -> str:
    """Return the film title.

    The canonical library filename is authoritative. Downloaded containers
    frequently carry release-group or encoder text in their ``title`` tag,
    while the ingest workflow normalizes the filename before publication.
    """
    del meta  # Container metadata is intentionally not a display-title source.
    return path.stem
