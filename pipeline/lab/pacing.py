"""Technical timing limits, independent of musical preferences and shot density."""
from __future__ import annotations

import math

from pipeline.lab.limits import MAX_AUDIO_PART_SECONDS, MAX_MODEL_SHOTS, MAX_TIMELINE_SLOTS


def uses_bounded_generation(document):
    duration = document["passage"]["end"] - document["passage"]["start"]
    slots = (document.get("music_timeline") or {}).get("slots", [])
    return duration > MAX_AUDIO_PART_SECONDS or len(slots) > MAX_MODEL_SHOTS


def batch_slots(slots, *, max_shots=MAX_MODEL_SHOTS, preferred_seconds=MAX_AUDIO_PART_SECONDS):
    """Pack consecutive shots without adding cuts at processing limits.

    Ninety seconds is a preferred text scope, not a source-duration limit. A
    longer hold occupies a batch by itself. Returned slots retain their values
    and identity; callers construct their private views.
    """
    if isinstance(max_shots, bool) or not isinstance(max_shots, int) or not 1 <= max_shots <= MAX_MODEL_SHOTS:
        raise ValueError("A processing batch must contain at most 32 shots")
    if not math.isfinite(preferred_seconds) or preferred_seconds <= 0:
        raise ValueError("A preferred processing span must be positive")
    batches, current, previous_end = [], [], None
    for slot in slots:
        start, end = slot["start"], slot["end"]
        if (not math.isfinite(start) or not math.isfinite(end) or end <= start
                or (previous_end is not None and abs(start - previous_end) > 1e-6)):
            raise ValueError("Processing batches require consecutive positive shot ranges")
        if current and (len(current) >= max_shots or end - current[0]["start"] > preferred_seconds + 1e-6):
            batches.append(current)
            current = []
        current.append(slot)
        previous_end = end
    if current:
        batches.append(current)
    return batches


def validate_cuts(end_frames, passage, fps=24, *, max_shots=MAX_TIMELINE_SLOTS):
    """Validate exact coverage without quotas, beat snapping or timing repairs."""
    if not 1 <= len(end_frames) <= min(max_shots, MAX_TIMELINE_SLOTS):
        raise ValueError(f"Timing requires 1–{min(max_shots, MAX_TIMELINE_SLOTS)} shot positions")
    total_frames = round((passage["end"] - passage["start"]) * fps)
    cursor_frame, cursor_time = 0, passage["start"]
    bounds = []
    for index, frame in enumerate(end_frames, start=1):
        if isinstance(frame, bool) or not isinstance(frame, int) or not cursor_frame < frame <= total_frames:
            raise ValueError("Cuts must be strictly ordered output frames inside the passage: "
                             f"shot {index} returned {frame!r}; expected an integer after frame {cursor_frame} "
                             f"and at most {total_frames}, relative to the passage start")
        end = passage["end"] if frame == total_frames else passage["start"] + frame / fps
        if end - cursor_time < 1 / fps - 1e-6:
            raise ValueError(f"Each planned shot must contain at least one output frame of source time: shot {index}")
        bounds.append((cursor_time, end))
        cursor_frame, cursor_time = frame, end
    if cursor_frame != total_frames:
        raise ValueError("Cuts must cover the complete selected passage: "
                         f"final shot ended at frame {cursor_frame}, expected {total_frames}")
    return bounds


def timing_diagnostics(slots):
    durations = [slot["end"] - slot["start"] for slot in slots]
    return {"selected_shots": len(durations), "shortest_seconds": min(durations),
            "longest_seconds": max(durations), "average_seconds_selected": sum(durations) / len(durations),
            "uniform_timing": len(durations) >= 4 and max(durations) - min(durations) <= 1 / 24 + 1e-6}
