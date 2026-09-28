"""Pure scopes shared by long-song listening and editorial generation."""
from __future__ import annotations

import math

from pipeline.lab.limits import MAX_AUDIO_PART_SECONDS, MAX_AUDIO_PARTS, MAX_PASSAGE_SECONDS


def partition_passage(passage, fps=24):
    """Contiguous processing parts on the original output grid, not beat cuts."""
    start, end = passage["start"], passage["end"]
    duration = end - start
    if not math.isfinite(duration) or not 0 < duration <= MAX_PASSAGE_SECONDS:
        raise ValueError("Choose a music passage of at most ten minutes")
    count = max(1, math.ceil(duration / MAX_AUDIO_PART_SECONDS))
    if count > MAX_AUDIO_PARTS:
        raise ValueError("Choose a music passage of at most ten minutes")
    frames = round(duration * fps)
    boundaries = [start, *[start + round(frames * index / count) / fps for index in range(1, count)], end]
    parts = [{"start": left, "end": right} for left, right in zip(boundaries, boundaries[1:])]
    if any(not 0 < part["end"] - part["start"] <= MAX_AUDIO_PART_SECONDS for part in parts):
        raise ValueError("The selected music does not fit bounded processing parts")
    return parts
