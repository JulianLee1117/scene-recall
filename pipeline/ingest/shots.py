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

import json
import math
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import BinaryIO, Optional
from uuid import uuid4

import numpy as np

from pipeline.config import Config
from pipeline.ingest.probe import FilmRecord


_TRANSNET_WIDTH = 48
_TRANSNET_HEIGHT = 27
_TRANSNET_CHANNELS = 3
_TRANSNET_FRAME_BYTES = (
    _TRANSNET_WIDTH * _TRANSNET_HEIGHT * _TRANSNET_CHANNELS
)
_TRANSNET_WINDOW = 100
_TRANSNET_STEP = 50
_TRANSNET_CONTEXT = 25
_SHOT_PROGRESS_INTERVAL = 10_000
_SHOT_DETECTION_VERSION = 2


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
    cached = _load_shot_cache(film, config)
    if cached is not None:
        print("[shots] skipped (cached)", flush=True)
        return cached

    # --- 1. Run TransNetV2 ---
    from transnetv2_pytorch import TransNetV2

    model = TransNetV2()
    single_np = _predict_video_streaming(
        model,
        film.path,
        expected_frames=max(1, round(film.duration * film.fps)),
    )

    # --- 2. Frame predictions → scene boundaries ---
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

    _save_shot_cache(film, config, result)
    return result


# ---------------------------------------------------------------------------
# Internal helpers (exported so tests can exercise them directly)
# ---------------------------------------------------------------------------


def _shot_cache_recipe(film: FilmRecord, config: Config) -> dict:
    """Return all source and recipe fields that affect detected shot rows."""
    try:
        stat = film.path.stat()
        source_size: int | None = stat.st_size
        source_mtime_ns: int | None = stat.st_mtime_ns
    except OSError:
        source_size = None
        source_mtime_ns = None
    return {
        "version": _SHOT_DETECTION_VERSION,
        "film_id": film.film_id,
        "source_path": str(film.path.resolve()),
        "source_size": source_size,
        "source_mtime_ns": source_mtime_ns,
        "duration": film.duration,
        "fps": film.fps,
        "flash_min_duration": config.thresholds.flash_min_duration,
        "subsegment_min_duration": config.thresholds.subsegment_min_duration,
        "keyframe_short_shot_s": config.thresholds.keyframe_short_shot_s,
    }


def _load_shot_cache(
    film: FilmRecord,
    config: Config,
) -> list[Shot] | None:
    """Load a complete compatible shot cache, otherwise return ``None``."""
    cache_path = film.asset_dir / "shots.json"
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if (
        not isinstance(payload, dict)
        or payload.get("recipe") != _shot_cache_recipe(film, config)
        or not isinstance(payload.get("shots"), list)
    ):
        return None
    try:
        shots = [Shot(**row) for row in payload["shots"]]
    except (TypeError, ValueError):
        return None
    if not shots or any(shot.t_end <= shot.t_start for shot in shots):
        return None
    return shots


def _save_shot_cache(
    film: FilmRecord,
    config: Config,
    shots: list[Shot],
) -> None:
    """Atomically publish shot boundaries for crash-safe downstream resume."""
    cache_path = film.asset_dir / "shots.json"
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = cache_path.with_name(f".{uuid4().hex}.json")
    payload = {
        "recipe": _shot_cache_recipe(film, config),
        "shots": [asdict(shot) for shot in shots],
    }
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(cache_path)
    finally:
        temporary.unlink(missing_ok=True)


def _read_exact_frame(stream: BinaryIO) -> bytes | None:
    """Read one raw TransNet frame, returning ``None`` at a clean EOF."""
    payload = bytearray()
    while len(payload) < _TRANSNET_FRAME_BYTES:
        chunk = stream.read(_TRANSNET_FRAME_BYTES - len(payload))
        if not chunk:
            if payload:
                raise RuntimeError("ffmpeg returned a truncated raw video frame")
            return None
        payload.extend(chunk)
    return bytes(payload)


def _predict_transnet_window(model: object, frames: list[np.ndarray]) -> np.ndarray:
    """Predict the non-overlapping centre 50 frames of one 100-frame window."""
    import torch

    batch = torch.from_numpy(np.stack(frames, axis=0)).unsqueeze(0)
    device = getattr(model, "device", torch.device("cpu"))
    batch = batch.to(device)
    with torch.inference_mode():
        single_frame_pred, _all_frames_pred = model.predict_raw(batch)
    return (
        single_frame_pred[0, _TRANSNET_CONTEXT:75, 0]
        .detach()
        .cpu()
        .numpy()
        .astype(np.float32, copy=False)
    )


def _predict_video_streaming(
    model: object,
    video_path: Path,
    *,
    expected_frames: int | None = None,
) -> np.ndarray:
    """Run TransNetV2 with bounded memory by streaming 48x27 frames.

    The upstream ``predict_video`` implementation captures and duplicates the
    complete raw film before inference.  A feature-length movie can therefore
    consume several gigabytes.  This keeps only one 100-frame context window
    while preserving TransNet's original 25/50/25 padding and overlap recipe.
    """
    command = [
        "ffmpeg",
        "-nostdin",
        "-v",
        "error",
        "-threads",
        "2",
        "-filter_threads",
        "1",
        "-i",
        str(video_path),
        "-map",
        "0:v:0",
        "-an",
        "-sn",
        "-dn",
        "-vf",
        f"scale={_TRANSNET_WIDTH}:{_TRANSNET_HEIGHT}",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "pipe:1",
    ]
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=_TRANSNET_FRAME_BYTES * 4,
    )
    if process.stdout is None or process.stderr is None:
        process.kill()
        process.wait()
        raise RuntimeError("could not open ffmpeg pipes for shot detection")

    frame_buffer: list[np.ndarray] = []
    prediction_chunks: list[np.ndarray] = []
    last_frame: np.ndarray | None = None
    frame_count = 0

    try:
        while True:
            raw_frame = _read_exact_frame(process.stdout)
            if raw_frame is None:
                break
            frame = np.frombuffer(raw_frame, dtype=np.uint8).reshape(
                _TRANSNET_HEIGHT,
                _TRANSNET_WIDTH,
                _TRANSNET_CHANNELS,
            )
            if last_frame is None:
                frame_buffer.extend([frame] * _TRANSNET_CONTEXT)
            frame_buffer.append(frame)
            last_frame = frame
            frame_count += 1

            if len(frame_buffer) == _TRANSNET_WINDOW:
                prediction_chunks.append(
                    _predict_transnet_window(model, frame_buffer)
                )
                frame_buffer = frame_buffer[_TRANSNET_STEP:]

            if frame_count % _SHOT_PROGRESS_INTERVAL == 0:
                if expected_frames:
                    progress = min(99, round(100 * frame_count / expected_frames))
                    print(f"[shots] {progress}% decoded", flush=True)
                else:
                    print(f"[shots] {frame_count} frames decoded", flush=True)

        stderr = process.stderr.read().decode("utf-8", errors="replace").strip()
        return_code = process.wait()
        if return_code != 0:
            detail = f": {stderr}" if stderr else ""
            raise RuntimeError(
                f"ffmpeg shot-detection decode failed ({return_code}){detail}"
            )
        if last_frame is None:
            raise RuntimeError("film contains no decodable video frames")

        predicted_frames = len(prediction_chunks) * _TRANSNET_STEP
        while predicted_frames < frame_count:
            frame_buffer.extend(
                [last_frame] * (_TRANSNET_WINDOW - len(frame_buffer))
            )
            prediction_chunks.append(
                _predict_transnet_window(model, frame_buffer)
            )
            predicted_frames += _TRANSNET_STEP
            frame_buffer = frame_buffer[_TRANSNET_STEP:]

        return np.concatenate(prediction_chunks, axis=0)[:frame_count]
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        process.stdout.close()
        process.stderr.close()


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
