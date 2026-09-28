"""Ephemeral first-edit timing offers; committed projects keep ordinary slots.

The music proposal supplies ordered intentions. Nearby cuts are finalized only
after retrieval, on the renderer's passage-relative frame grid. Source length
establishes feasibility, never observed action completion.
"""
from __future__ import annotations

from copy import deepcopy
import math

from pipeline.lab.music_evidence import music_evidence


ASSEMBLY_CONTRACT = "bounded-source-aware-first-edit-v1"


def _nominal(document):
    return [[slot["id"], slot["start"], slot["end"]] for slot in document["music_timeline"]["slots"]]


def frame_time(scope, frame):
    # Inspection may review an existing fixed timeline with subframe cuts. Its
    # private scope preserves those exact times instead of quantizing user work.
    if frame in scope.get("fixed_times", {}):
        return scope["fixed_times"][frame]
    # Keep the original audio endpoint, including its subframe remainder.
    if frame == scope["total_frames"]:
        return scope["passage"]["end"]
    return scope["passage"]["start"] + frame / scope["fps"]


def initial_timing_scope(document, *, beat_guides=True):
    """Timing offers for the private plan of an explicit whole-edit generation."""
    if not isinstance(beat_guides, bool):
        raise ValueError("Beat-guide evaluation must be an explicit boolean")
    slots = document["music_timeline"]["slots"]
    if document["clips"] or any(slot.get("clip_id") or slot.get("direction_source") == "user" for slot in slots):
        raise ValueError("Source-aware first timing requires an untouched empty edit")
    origin, end, fps = document["passage"]["start"], document["passage"]["end"], document["fps"]
    frames = [0, *[round((slot["end"] - origin) * fps) for slot in slots]]
    # The final timestamp can lie before its rounded frame count. Reserve a
    # real frame of source time for the last clip, then propagate that bound.
    next_frame = math.floor((end - origin) * fps - 1 + 1e-5) + 1
    for index in range(len(frames) - 2, 0, -1):
        frames[index] = min(frames[index], next_frame - 1)
        next_frame = frames[index]
    if any(b <= a for a, b in zip(frames, frames[1:])):
        raise ValueError("Proposed musical moments do not fit the output frame grid")
    measured = music_evidence(document)["measured"]
    guides = (sorted(set(round((value - origin) * fps) for key in ("beats", "downbeats") for value in measured.get(key, [])))
              if beat_guides else [])
    boundaries = [[0]]
    for index, nominal in enumerate(frames[1:-1], start=1):
        # Neighboring movement bands do not overlap, so all combinations retain
        # order and at least one frame. There is no jitter or synthetic beat.
        lower = max(nominal - 2 * fps, (frames[index - 1] + nominal + 1) // 2)
        upper = min(nominal + 2 * fps, (nominal + frames[index + 1] - 1) // 2)
        if index == len(frames) - 2:
            upper = min(upper, math.floor((end - origin) * fps - 1 + 1e-5))
        options = {nominal, lower, upper}
        nearby = sorted((value for value in guides if lower <= value <= upper and value not in options),
                        key=lambda value: (abs(value - nominal), value))[:4]
        boundaries.append(sorted(options | set(nearby)))
    boundaries.append([frames[-1]])
    result = {"contract": ASSEMBLY_CONTRACT, "track_id": document["track"]["id"],
            "passage": deepcopy(document["passage"]), "fps": fps, "total_frames": frames[-1],
            "nominal": _nominal(document), "boundary_frames": boundaries,
            "note": "Ordered musical intentions; only these cuts may move. Legal source duration is not observed action timing."}
    if not beat_guides:
        result["evaluation"] = {"beat_guides": False}
    return result


def validate_scope(document, scope):
    if (scope.get("contract") != ASSEMBLY_CONTRACT or scope.get("track_id") != document["track"]["id"]
            or scope.get("passage") != document["passage"] or scope.get("fps") != document["fps"]
            or scope.get("nominal") != _nominal(document) or document["clips"]
            or any(slot.get("clip_id") or slot.get("direction_source") == "user" for slot in document["music_timeline"]["slots"])):
        raise ValueError("Source-aware timing no longer matches the untouched planning scope")


def timing_offer(scope, index):
    starts, ends = scope["boundary_frames"][index:index + 2]
    return {"start_frames": starts, "end_frames": ends,
            "min_duration": frame_time(scope, min(ends)) - frame_time(scope, max(starts)),
            "max_duration": frame_time(scope, max(ends)) - frame_time(scope, min(starts))}


def fit_cut_preferences(scope, offers, preferred_frames, source_durations, *, transition_allowed=None):
    """Fit selected sources jointly using only the already offered boundaries.

    Minimize total movement from the model's preferences, then changed-cut count,
    then the boundary sequence for deterministic ties. No source is substituted.
    At most seven boundary states per shot keep this bounded (32 * 7 * 7 edges).
    """
    if not offers or len(offers) != len(preferred_frames) or len(offers) != len(source_durations):
        raise ValueError("Selected-source timing requires every ordered shot")
    states = {0: (0, 0, ())}
    for index, (offer, preferred, available) in enumerate(zip(offers, preferred_frames, source_durations)):
        next_states = {}
        for end in offer["timing"]["end_frames"]:
            for start, (distance, changes, path) in states.items():
                duration = frame_time(scope, end) - frame_time(scope, start)
                if duration < 1 / scope["fps"] - 1e-6 or (available is not None and duration > available + 1e-6):
                    continue
                if transition_allowed is not None and not transition_allowed(index, start, end):
                    continue
                candidate = (distance + abs(end - preferred), changes + (end != preferred), (*path, end))
                if end not in next_states or candidate < next_states[end]:
                    next_states[end] = candidate
        if not next_states:
            raise ValueError(
                f"The selected sources cannot fit the offered cuts through shot {offer['slot'] + 1}. "
                "Choose longer footage or change the timing. Your saved edit is unchanged."
            )
        states = next_states
    if scope["total_frames"] not in states:
        raise ValueError("Selected-source timing does not cover the complete passage. Your saved edit is unchanged.")
    return list(states[scope["total_frames"]][2])


def validate_result(scope, document):
    """A narrowly validated timing transition, before normal fixed-edit guards."""
    slots = document["music_timeline"]["slots"]
    if (document["track"]["id"] != scope["track_id"] or document["passage"] != scope["passage"]
            or document["fps"] != scope["fps"]):
        raise ValueError("Source-aware selection changed the original music scope")
    if len(slots) != len(scope["nominal"]) or [slot["id"] for slot in slots] != [row[0] for row in scope["nominal"]]:
        raise ValueError("Source-aware selection changed the ordered musical intentions")
    if _nominal(document) == scope["nominal"]:
        return  # No fitting candidates: retain the exact proposed musical times.
    cursor = scope["passage"]["start"]
    for index, slot in enumerate(slots):
        legal = [frame_time(scope, frame) for frame in scope["boundary_frames"][index + 1]]
        if abs(slot["start"] - cursor) > 1e-7 or not any(abs(slot["end"] - value) <= 1e-7 for value in legal):
            raise ValueError("Source-aware selection used an unoffered cut or changed passage coverage")
        if slot["end"] - slot["start"] < 1 / scope["fps"] - 1e-6:
            raise ValueError("Source-aware selection produced a slot shorter than one frame")
        cursor = slot["end"]
