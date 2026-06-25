"""probe.py — film ingestion: content hash, ffprobe metadata, asset dir setup.

Usage::

    from pipeline.ingest.probe import probe_film, FilmRecord

    record = probe_film(Path("/path/to/film.mkv"), config)
    print(record.film_id, record.duration, record.fps)
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

from pipeline.config import Config

# How many bytes to read from each end of the file for the content hash.
_HASH_CHUNK = 4 * 1024 * 1024  # 4 MB


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
    title: str          # From container metadata, or the stem of the filename


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
    title = _parse_title(meta, path)

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
    )


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
        if size <= _HASH_CHUNK * 2:
            # Small file: hash the whole thing.
            h.update(fh.read())
        else:
            # Large file: hash the head chunk.
            h.update(fh.read(_HASH_CHUNK))
            # Seek to the last chunk and hash it.
            fh.seek(-_HASH_CHUNK, 2)
            h.update(fh.read(_HASH_CHUNK))

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
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


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


def _parse_title(meta: dict, path: Path) -> str:
    """Return the film title.

    Uses the ``title`` tag from the container format metadata when present;
    falls back to the file stem (filename without extension).
    """
    tags = meta.get("format", {}).get("tags", {})
    # Tags can appear with different capitalisation depending on the muxer.
    for key in ("title", "TITLE", "Title"):
        value = tags.get(key, "").strip()
        if value:
            return value

    return path.stem
