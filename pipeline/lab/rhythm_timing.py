"""Local timing scaffolds, distinct from listening and creative shot planning.

Detected downbeats offer bar landmarks. Relative amplitude changes can favor an
earlier landmark; pacing bounds otherwise group bars into readable holds. This
is an editable heuristic, not phrase recognition or an accent detector.
"""
from __future__ import annotations

from statistics import median
from math import ceil, floor

from pipeline.lab.music_evidence import music_evidence
from pipeline.lab.limits import MAX_AUDIO_PART_SECONDS, MAX_MODEL_SHOTS, MAX_TIMELINE_SLOTS
from pipeline.lab.timeline import (PROVISIONAL_TIMING_CONTRACT, ensure_timeline, provisional_timing_eligible,
                                   replan_timeline, timing_fingerprint)


TIMING_CONTRACT = "measured-bar-amplitude-placeholders-v1"
# Minimum/maximum bar groups and meaningful normalized RMS contrast. Bounds
# limit placeholder length, not the song's meaning or eventual scene action.
PACING = {"patient": (3, 7, .15), "balanced": (2, 5, .10), "kinetic": (1, 3, .075), "rapid": (.5, 1.5, .075)}


def _amplitude_change(rhythm, at, span):
    values = rhythm.get("intensity") or []
    start, end = rhythm.get("waveform_start", 0), rhythm.get("waveform_end", 0)
    if not values or end <= start:
        return 0.
    width = (end - start) / len(values)

    def mean(left, right):
        first = max(0, floor((left - start) / width))
        last = min(len(values), ceil((right - start) / width))
        samples = [(values[i], max(0., min(right, start + (i + 1) * width) - max(left, start + i * width)))
                   for i in range(first, last)]
        weight = sum(weight for _, weight in samples)
        return sum(value * weight for value, weight in samples) / weight if weight else 0.

    return abs(mean(at - span, at) - mean(at, at + span))


def timing_suggestions(document):
    evidence = music_evidence(document, include_analysis=False)
    measured = evidence["measured"]
    start, end = document["passage"]["start"], document["passage"]["end"]
    pacing = evidence["planner_settings"]["pacing"]
    downbeats, beats = measured.get("downbeats", []), measured.get("beats", [])
    # A beat-only tracker output has no bar authority: use pulse groups but
    # retain their weaker provenance explicitly. Never label them downbeats.
    rapid = pacing == "rapid"
    landmarks = (beats or downbeats) if rapid else (downbeats or beats)
    using_downbeats = bool(downbeats) and not (rapid and beats)
    origin = "detected-downbeat" if using_downbeats else "detected-beat"
    result = {"contract": TIMING_CONTRACT, "track": evidence["track_id"],
              "passage": dict(document["passage"]), "pacing": pacing, "cuts": [],
              "note": "Editable bar groups and relative amplitude changes; not detected phrases or verified accents."}
    intervals = [b - a for a, b in zip(landmarks, landmarks[1:]) if b > a]
    if not intervals:
        result["note"] = "No usable beat grid. One empty passage is kept; add cuts manually or prepare Beat This! before trying again."
        return result
    bar = median(intervals) * (1 if using_downbeats or rapid else 4)
    minimum, maximum, contrast = PACING[pacing]
    minimum = max(.35, min(.8, minimum * bar)) if rapid else max(1., min(6., minimum * bar))
    maximum = max(minimum + 1 / 24, min(1.5, maximum * bar)) if rapid else max(minimum + 1., min(16., maximum * bar))
    rhythm = document.get("rhythm") or {}
    # The evidence adapter validates scope, finite samples and waveform bounds.
    has_amplitude = "relative_rms" in measured
    scores = {at: _amplitude_change(rhythm, at, bar) if has_amplitude else 0. for at in landmarks}
    cursor = start
    limit = MAX_MODEL_SHOTS if end - start <= MAX_AUDIO_PART_SECONDS and not rapid else MAX_TIMELINE_SLOTS
    # Traverse the complete measured grid before applying the output capacity.
    # Spending the allowance on the beginning leaves a huge final placeholder.
    while end - cursor > minimum * 2:
        choices = [at for at in landmarks if cursor + minimum - 1 / 24 <= at <= min(cursor + maximum, end - minimum)]
        after_gap = False
        if not choices:
            # Missing pulse guides are a held interval, not the end of the song.
            # Resume at the next actual landmark; never fill the gap with a grid.
            following = next((at for at in landmarks if cursor + minimum <= at <= end - minimum), None)
            if following is None:
                break
            choices = [following]
            after_gap = True
        strongest = max(choices, key=lambda at: scores[at])
        changed = scores[strongest] >= contrast
        if not changed and end - cursor <= maximum:
            break
        at = strongest if changed else choices[-1]
        time = start + round((at - start) * document.get("fps", 24)) / document.get("fps", 24)
        result["cuts"].append({"time": time, "landmark_time": at, "landmark_source": origin,
                               "basis": ("bar-after-gap" if using_downbeats else "pulse-after-gap") if after_gap else
                                        "relative-amplitude-change" if changed else "bar-group" if using_downbeats else "pulse-group",
                               "relative_amplitude_change": round(scores[at], 4)})
        cursor = time
    available = len(result["cuts"])
    if available > limit - 1:
        # Retain the opening and closing measured cuts, distributing the other
        # choices across all detected activity. This selects existing timestamps
        # rather than inventing equally spaced cut positions.
        result["cuts"] = [result["cuts"][round(index * (available - 1) / (limit - 2))]
                          for index in range(limit - 1)]
        result.update(capacity_limited=True, candidate_cut_count=available)
        result["note"] += f" The {limit}-shot capacity distributes retained measured cuts across the complete passage."
    return result


def prepare_timing(document, *, replan=False):
    """Publish guides without moving an existing edit; rebuild only explicitly."""
    suggestions = timing_suggestions(document)
    document["rhythm"] = {**(document.get("rhythm") or {}), "timing_suggestions": suggestions}
    existing = document.get("music_timeline")
    boundaries = [cut["time"] for cut in suggestions["cuts"]]
    timeline = (replan_timeline(document, timing_boundaries=boundaries) if replan else
                ensure_timeline(document, timing_boundaries=boundaries))
    if not existing or replan:
        for slot in timeline["slots"]:
            if not slot.get("clip_id") and slot.get("direction_source") != "user":
                slot["needs_direction"] = True
    if not existing and not replan:
        timeline["provisional_timing"] = {"contract": PROVISIONAL_TIMING_CONTRACT, "fingerprint": timing_fingerprint(timeline)}
        if not provisional_timing_eligible(document):
            timeline["provisional_timing"] = None
    return timeline
