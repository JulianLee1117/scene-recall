"""Decode real presentation timestamps, never infer frame identity from a seek."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import math
import time

import av
from PIL import Image


@dataclass
class Sample:
    time: float
    end: float
    image: Image.Image


def samples(
    path: Path,
    start: float,
    end: float,
    *,
    fps: float = 4,
    native: bool = False,
    cancelled=lambda: False,
) -> list[Sample]:
    if (
        not all(math.isfinite(v) for v in (start, end, fps))
        or start < 0
        or not 0 < end - start <= 4.1
        or fps <= 0
    ):
        raise ValueError(
            "Decode windows must be finite, positive and at most four seconds"
        )
    result, pending = [], None
    deadline = time.monotonic() + 45
    with av.open(str(path)) as container:
        stream = container.streams.video[0]
        if stream.sample_aspect_ratio and stream.sample_aspect_ratio != 1:
            raise ValueError("Non-square source pixels require display normalization")
        if float(stream.metadata.get("rotate", "0")) % 360:
            raise ValueError("Rotated source media requires display normalization")
        container.seek(
            max(0, int(start / stream.time_base)), stream=stream, backward=True
        )
        next_time = start
        for frame in container.decode(stream):
            if cancelled():
                from pipeline.lab.media import JobCancelled

                raise JobCancelled("Matching cancelled")
            if time.monotonic() > deadline:
                raise TimeoutError("Source decoding exceeded its bounded time budget")
            if frame.pts is None:
                continue
            timestamp = float(frame.pts * stream.time_base)
            if pending is not None:
                pending.end = timestamp
                pending = None
            if timestamp >= end:
                break
            if timestamp + 1e-7 < start or (
                not native and timestamp + 1e-7 < next_time
            ):
                continue
            image = frame.to_image()
            # Side-data display matrices are applied nowhere in this profile.
            if any(
                str(side.type).endswith("DISPLAYMATRIX") for side in frame.side_data
            ):
                raise ValueError("Display-matrix video requires explicit normalization")
            duration = (
                float(frame.duration * stream.time_base)
                if frame.duration
                else 1 / float(stream.average_rate or 24)
            )
            pending = Sample(timestamp, min(end, timestamp + duration), image)
            result.append(pending)
            next_time = timestamp + 1 / fps
            if len(result) >= 256:
                break
    return [row for row in result if row.time < row.end <= end + 1e-7]


def at(
    path: Path, timestamp: float, lower: float, upper: float, cancelled=lambda: False
) -> Sample:
    rows = samples(
        path,
        max(lower, timestamp - 0.15),
        min(upper, timestamp + 0.15),
        native=True,
        cancelled=cancelled,
    )
    if not rows:
        raise ValueError("No decoded frame at the selected reference")
    return min(rows, key=lambda row: (abs(row.time - timestamp), row.time))


def outgoing_start(path, frame, lower, upper, cancelled=lambda: False):
    """Start on a native frame, avoiding the one-frame shift of end minus 1s.

    At 24000/1001 fps a one-second subtraction lies between frames. An accurate
    seek then skips the wanted first frame and can expose the next cut frame.
    """
    return at(path, max(lower, frame.end - 1), lower, upper, cancelled).time
