"""media.py — keyframe extraction and hover-preview generation.

For each shot detected by the shots stage, this module calls ffmpeg twice:
  1. One WebP keyframe per ``shot.keyframe_times`` entry.
  2. One VP9 WebM hover-preview clip centred on the shot midpoint.

Usage::

    from pipeline.ingest.media import extract_media

    extract_media(film, shots, config)
    # Writes to:
    #   film.asset_dir / "keyframes" / "{shot_id}_{n}.webp"
    #   film.asset_dir / "previews"  / "{shot_id}.webm"
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from pipeline.config import Config
from pipeline.ingest.probe import FilmRecord
from pipeline.ingest.shots import Shot

# ---------------------------------------------------------------------------
# Display constants — not tunable thresholds
# ---------------------------------------------------------------------------

_KEYFRAME_MAX_WIDTH: int = 1280   # px — scale=1280:-1 preserves aspect ratio
_PREVIEW_HEIGHT: int = 480         # px — scale=-1:480 preserves aspect ratio
_PREVIEW_MAX_DURATION: float = 4.0 # seconds — cap on hover-preview length
_KEYFRAME_START_PAD: float = 0.1  # seconds — avoids black frame at a hard cut


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
    kf_dir.mkdir(parents=True, exist_ok=True)
    preview_dir.mkdir(parents=True, exist_ok=True)

    for shot in shots:
        _extract_keyframes(film.path, shot, kf_dir)
        _extract_preview(film.path, shot, preview_dir)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _extract_keyframes(film_path: Path, shot: Shot, kf_dir: Path) -> None:
    """Extract one WebP still per ``shot.keyframe_times`` entry via ffmpeg.

    The first keyframe (n=0) is padded so that the seek is at least
    ``shot.t_start + _KEYFRAME_START_PAD`` to avoid capturing a black or
    transitional frame at the start of a hard cut.
    """
    for n, t in enumerate(shot.keyframe_times):
        # Pad the seek for the first keyframe to avoid black frames at cuts.
        if n == 0:
            seek_t = max(t, shot.t_start + _KEYFRAME_START_PAD)
        else:
            seek_t = t

        out_path = kf_dir / f"{shot.shot_id}_{n}.webp"
        cmd = [
            "ffmpeg",
            "-y",
            "-ss", str(seek_t),
            "-i", str(film_path),
            "-frames:v", "1",
            "-vf", f"scale={_KEYFRAME_MAX_WIDTH}:-1",
            "-q:v", "82",
            str(out_path),
        ]
        subprocess.run(cmd, capture_output=True, check=True)


def _extract_preview(film_path: Path, shot: Shot, preview_dir: Path) -> None:
    """Extract a VP9 WebM hover-preview clip centred on the shot midpoint.

    Duration is ``min(_PREVIEW_MAX_DURATION, shot duration)``, centred on the
    midpoint.  ``-ss`` is placed before ``-i`` for fast input seeking.
    """
    duration = shot.t_end - shot.t_start
    clip_dur = min(_PREVIEW_MAX_DURATION, duration)
    midpoint = shot.t_start + duration / 2.0
    half_dur = clip_dur / 2.0

    # Clamp seek start so we don't seek before the shot boundary.
    seek_start = max(shot.t_start, midpoint - half_dur)
    # Adjust the clip duration in case clamping shifted the start.
    actual_dur = min(clip_dur, shot.t_end - seek_start)

    out_path = preview_dir / f"{shot.shot_id}.webm"
    cmd = [
        "ffmpeg",
        "-y",
        "-ss", str(seek_start),
        "-i", str(film_path),
        "-t", str(actual_dur),
        "-vf", f"scale=-1:{_PREVIEW_HEIGHT}",
        "-c:v", "libvpx-vp9",
        "-crf", "35",
        "-b:v", "0",
        "-an",
        str(out_path),
    ]
    subprocess.run(cmd, capture_output=True, check=True)
