"""shots.py — shot boundary detection using TransNetV2.

Usage::

    from pipeline.ingest.shots import detect_shots, Shot

    shots = detect_shots(film, config)
    for s in shots:
        print(s.shot_id, s.t_start, s.t_end)

Pipeline:
  1. Run TransNetV2 on the film file to get per-frame cut probabilities.
  2. Convert frame-level predictions to (start_frame, end_frame) scene pairs.
  3. Convert frame indices to timestamps in seconds using film.fps.
  4. Flash/strobe filter: merge any shot whose duration < config.thresholds.flash_min_duration
     into the preceding shot (handles single-frame flashes and strobe cuts).
  5. Sub-segmentation: shots longer than config.thresholds.subsegment_min_duration
     are split into equal-length sub-segments.  Sub-segments carry the
     ``parent_shot_id`` of the original (unsplit) shot.
  6. Compute ``keyframe_times``:
       - 1 keyframe (midpoint) for shots < config.thresholds.keyframe_short_shot_s
       - 3 keyframes at 25 / 50 / 75 % for longer shots
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np

from pipeline.config import Config
from pipeline.ingest.probe import FilmRecord


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class Shot:
    """A single detected shot (or sub-segment) within a film.

    Attributes
    ----------
    shot_id:
        Unique identifier of the form ``{film_id}_{index:04d}``.
    t_start:
        Start time in seconds (float64).
    t_end:
        End time in seconds (float64).
    parent_shot_id:
        ``None`` for base shots; set to the parent shot's ``shot_id`` when
        this shot is a sub-segment produced by sub-segmentation.
    keyframe_times:
        Representative frame times within the shot:
        - 1 time (midpoint) when the shot duration is < config.thresholds.keyframe_short_shot_s
        - 3 times at the 25 / 50 / 75 % marks otherwise
    """

    shot_id: str
    t_start: float
    t_end: float
    parent_shot_id: Optional[str]
    keyframe_times: list[float] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def detect_shots(film: FilmRecord, config: Config) -> list[Shot]:
    """Detect shot boundaries in *film* and return a list of :class:`Shot`.

    Parameters
    ----------
    film:
        Probed film record (must have a valid ``path``, ``fps``, and
        ``duration``).
    config:
        Pipeline configuration; ``config.thresholds.subsegment_min_duration``
        controls when sub-segmentation is triggered.

    Returns
    -------
    list[Shot]
        Ordered list of shots / sub-segments covering the film.  Sub-segments
        are contiguous and span the same range as the original shot.
    """
    # --- 1. Run TransNetV2 ---
    from transnetv2_pytorch import TransNetV2

    model = TransNetV2()
    _video_frames, single_pred, _all_pred = model.predict_video(
        str(film.path), quiet=True
    )

    # --- 2. Frame predictions → scene boundaries ---
    single_np: np.ndarray = single_pred.cpu().detach().numpy()
    scene_boundaries: np.ndarray = TransNetV2.predictions_to_scenes(single_np)
    # scene_boundaries: int32 array of shape (N, 2) — [[start_frame, end_frame], ...]

    # --- 3. Frame indices → timestamps (seconds) ---
    fps = film.fps
    raw_shots: list[tuple[float, float]] = []
    for start_frame, end_frame in scene_boundaries:
        t_start = float(start_frame) / fps
        # +1 to include the full last frame; capped at film.duration
        t_end = min(float(end_frame + 1) / fps, film.duration)
        if t_end > t_start:
            raw_shots.append((t_start, t_end))

    # --- 4. Flash/strobe filter ---
    filtered = _merge_flash_shots(raw_shots, config.thresholds.flash_min_duration)

    # --- 5. Sub-segment + assign Shot objects ---
    threshold = float(config.thresholds.subsegment_min_duration)
    # Use a global counter so parent IDs and sub-segment IDs never collide.
    counter = 0
    result: list[Shot] = []

    for t_start, t_end in filtered:
        # Reserve the next counter slot as this base shot's ID.
        parent_id = f"{film.film_id}_{counter:04d}"
        counter += 1

        duration = t_end - t_start
        if duration > threshold:
            # Sub-segment the shot and emit sub-shots only.
            for sub_start, sub_end in _equal_split(t_start, t_end, threshold):
                sub_id = f"{film.film_id}_{counter:04d}"
                counter += 1
                result.append(
                    Shot(
                        shot_id=sub_id,
                        t_start=sub_start,
                        t_end=sub_end,
                        parent_shot_id=parent_id,
                        keyframe_times=_compute_keyframes(sub_start, sub_end, config.thresholds.keyframe_short_shot_s),
                    )
                )
        else:
            # Emit the base shot as-is.
            result.append(
                Shot(
                    shot_id=parent_id,
                    t_start=t_start,
                    t_end=t_end,
                    parent_shot_id=None,
                    keyframe_times=_compute_keyframes(t_start, t_end, config.thresholds.keyframe_short_shot_s),
                )
            )

    return result


# ---------------------------------------------------------------------------
# Internal helpers (exported so tests can exercise them directly)
# ---------------------------------------------------------------------------


def _merge_flash_shots(
    shots: list[tuple[float, float]],
    threshold: float,
) -> list[tuple[float, float]]:
    """Merge shots shorter than *threshold* into their predecessor.

    If the very first shot is short and there is no predecessor, it is kept
    as-is to avoid losing the film's opening frames.

    Parameters
    ----------
    shots:
        Ordered list of ``(t_start, t_end)`` pairs.
    threshold:
        Duration (seconds) below which a shot is treated as a flash/strobe
        artefact and merged into the preceding shot.

    Returns
    -------
    list[tuple[float, float]]
        Filtered list; every returned shot has a positive duration.
    """
    if not shots:
        return []

    result: list[tuple[float, float]] = [shots[0]]

    for t_start, t_end in shots[1:]:
        duration = t_end - t_start
        if duration < threshold:
            # Extend the previous shot to absorb the flash.
            prev_start, _prev_end = result[-1]
            result[-1] = (prev_start, t_end)
        else:
            result.append((t_start, t_end))

    return result


def _equal_split(
    t_start: float,
    t_end: float,
    min_duration: float,
) -> list[tuple[float, float]]:
    """Split *[t_start, t_end]* into equal sub-segments.

    The number of sub-segments is ``ceil(duration / min_duration)``, so each
    segment is shorter than or equal to *min_duration*.

    Parameters
    ----------
    t_start, t_end:
        Shot boundaries in seconds.
    min_duration:
        Target maximum sub-segment length (seconds).

    Returns
    -------
    list[tuple[float, float]]
        Contiguous, equal-length sub-segments covering *[t_start, t_end]*.
    """
    duration = t_end - t_start
    n = math.ceil(duration / min_duration)
    seg_dur = duration / n

    segments: list[tuple[float, float]] = []
    for i in range(n):
        seg_start = t_start + i * seg_dur
        # Use t_end for the last segment to avoid floating-point drift.
        seg_end = t_end if i == n - 1 else t_start + (i + 1) * seg_dur
        segments.append((seg_start, seg_end))

    return segments


def _compute_keyframes(t_start: float, t_end: float, threshold: float = 2.0) -> list[float]:
    """Return representative keyframe timestamps for a shot.

    Rules:
    - Duration  < *threshold* → 1 keyframe at the midpoint.
    - Duration >= *threshold* → 3 keyframes at 25 %, 50 %, 75 % of the shot.

    Parameters
    ----------
    t_start, t_end:
        Shot boundaries in seconds.
    threshold:
        Duration (seconds) below which the shot gets 1 keyframe at the
        midpoint; otherwise 3 keyframes are placed at 25/50/75% marks.

    Returns
    -------
    list[float]
        Ordered list of keyframe times within *[t_start, t_end]*.
    """
    duration = t_end - t_start
    if duration < threshold:
        return [t_start + duration / 2.0]
    return [
        t_start + 0.25 * duration,
        t_start + 0.50 * duration,
        t_start + 0.75 * duration,
    ]
