"""Explicit source-track timing for the bounded music editor.

Saved cuts are authoritative for Fill gaps; only explicit whole-edit generation
or timing actions replace them. A missing clip is time on the reel, not a deletion.
"""
from __future__ import annotations

import math
import hashlib
import json
import uuid
from copy import deepcopy

from pipeline.lab.models import EditorialDirection, MusicDirection
from pipeline.lab.limits import MAX_MODEL_SHOTS, MAX_TIMELINE_SLOTS


TIMELINE_CONTRACT = "song-moment-source-track-slots-v2"
PROVISIONAL_TIMING_CONTRACT = "local-rhythm-starter-v1"


def timing_fingerprint(timeline):
    """Freeze timing identity separately from replaceable directions or guides."""
    value = {"track_id": timeline["track_id"],
             "passage": {key: float(timeline["passage"][key]) for key in ("start", "end")},
             "slots": [[slot["id"], float(slot["start"]), float(slot["end"])] for slot in timeline["slots"]]}
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def provisional_timing_eligible(document):
    """Identify untouched local rhythm scaffolds without granting retiming permission."""
    timeline = document.get("music_timeline") or {}
    marker = timeline.get("provisional_timing") or {}
    if marker.get("contract") != PROVISIONAL_TIMING_CONTRACT or not timeline.get("slots"):
        return False
    if marker.get("fingerprint") != timing_fingerprint(timeline):
        return False
    track_id = (document.get("track") or {}).get("id")
    passage = document["passage"]
    rhythm = document.get("rhythm") or {}
    provenance = rhythm.get("provenance") or {}
    if not isinstance(provenance, dict):
        return False
    if (not track_id or timeline["track_id"] != track_id or timeline["passage"] != passage
            or provenance.get("track") != track_id or provenance.get("passage") != passage
            or rhythm.get("marker_source") == "user" or document["clips"]):
        return False
    for slot in timeline["slots"]:
        if (slot.get("direction_source") == "user" or (slot.get("direction") and slot.get("direction_source") != "ai")
                or any(slot.get(key) for key in ("clip_id", "feedback", "alternatives", "search_evidence",
                                                 "resolved_search", "reason", "search_error"))):
            return False
    return True


def section_for(start, end, segments):
    if not segments:
        return 0
    return max(range(len(segments)), key=lambda index: (
        max(0, min(segments[index]["end"], end) - max(segments[index]["start"], start)),
        -abs((segments[index]["start"] + segments[index]["end"]) / 2 - (start + end) / 2),
    ))


def clip_positions(document):
    """Playback positions for lock guards, including legacy sequential projects."""
    timeline = document.get("music_timeline")
    if timeline:
        return {slot["clip_id"]: (slot["start"], slot["end"])
                for slot in timeline["slots"] if slot.get("clip_id")}
    cursor = document["passage"]["start"]
    result = {}
    for clip in document["clips"]:
        end = cursor + clip["source_end"] - clip["source_start"]
        result[clip["id"]] = (cursor, end)
        cursor = end
    return result


def direction_for(slot, analysis):
    """Explicit slot intent wins; old slots retain their live section fallback."""
    if slot.get("direction"):
        return MusicDirection.model_validate(slot["direction"]).model_dump()
    segments = analysis.get("segments", [])
    if not segments or slot["section_index"] >= len(segments):
        raise ValueError("This slot needs an interpretation or its own search direction")
    section = segments[slot["section_index"]]
    return MusicDirection(query=section["query"], search_facet=section.get("search_facet", "all"),
                          purpose=section.get("imagery", ""), music_cue=section.get("feeling", ""),
                          timing_note="Legacy section context; choose a musical cue for this slot.").model_dump()


def refresh_directions(timeline, analysis):
    """Map each timed moment independently, preserving deliberate user overrides."""
    moments = analysis.get("edit_beats") or []
    for slot in timeline["slots"]:
        slot["section_index"] = section_for(slot["start"], slot["end"], analysis.get("segments", []))
        if slot.get("direction") and slot.get("direction_source") != "ai":
            continue
        if moments:
            moment = moments[section_for(slot["start"], slot["end"], moments)]
            slot["direction"] = MusicDirection.model_validate({key: moment[key] for key in EditorialDirection.model_fields}).model_dump()
            slot["direction_source"] = "ai"
            slot["needs_direction"] = False
            slot["resolved_search"] = None
            slot["search_evidence"] = None


def require_replan_unlocked(document):
    placed = set(clip_positions(document))
    if any(clip["locked"] and clip["id"] in placed for clip in document["clips"]):
        raise ValueError("Unlock placed clips before replanning cuts; their timing is protected")


def replan_timeline(document, *, timing_boundaries=None):
    """Explicit reset changes timing only; previous source selections remain in the bin."""
    require_replan_unlocked(document)
    previous = document.get("music_timeline") or {}
    user_slots = [slot for slot in previous.get("slots", [])
                  if slot.get("direction") and slot.get("direction_source") != "ai"]
    planning = {**document, "clips": [], "music_timeline": None}
    timeline = ensure_timeline(planning, prefer_moments=True, timing_boundaries=timing_boundaries)
    timeline["provisional_timing"] = None
    for slot in timeline["slots"]:
        overlapping = [old for old in user_slots if min(old["end"], slot["end"]) > max(old["start"], slot["start"])]
        if overlapping:
            old = overlapping[section_for(slot["start"], slot["end"], overlapping)]
            slot["direction"], slot["direction_source"] = deepcopy(old["direction"]), "user"
    document["music_timeline"] = timeline
    return timeline


def ensure_timeline(document, *, prefer_moments=False, timing_boundaries=None):
    """Lazily migrate without discarding or regenerating saved clip selections."""
    timeline = document.get("music_timeline")
    if timeline:
        return timeline
    track = document.get("track")
    if not track:
        raise ValueError("Import music before creating a music timeline")
    passage = document["passage"]
    start, end = passage["start"], passage["end"]
    segments = (document.get("analysis") or {}).get("segments", [])
    moments = (document.get("analysis") or {}).get("edit_beats") or []
    slots = []

    def append(a, b, clip_id=None):
        slots.append({"id": str(uuid.uuid4()), "start": a, "end": b,
                      "section_index": section_for(a, b, segments), "clip_id": clip_id,
                      "alternatives": [], "reason": None, "search_error": None,
                      "direction": None, "direction_source": None, "needs_direction": False, "feedback": None,
                      "resolved_search": None, "search_evidence": None})

    cursor = start
    for clip in document["clips"]:
        boundary = cursor + clip["source_end"] - clip["source_start"]
        if boundary > end + 1 / 24 + 0.000001:
            raise ValueError("Existing clips exceed the selected music passage. Trim them or extend the passage before creating its timeline")
        boundary = min(boundary, end)
        append(cursor, boundary, clip["id"])
        cursor = boundary
    rhythm = document.get("rhythm") or {}
    raw = rhythm.get("markers", [])
    markers = sorted(set(float(value) for value in raw
                         if not isinstance(value, bool) and isinstance(value, (float, int))
                         and math.isfinite(value) and cursor < value < end))
    manual = rhythm.get("marker_source") == "user" and not prefer_moments
    # Source-specific edit moments own initial timing. Without them, section
    # boundaries are an honest legacy fallback; never invent a repeating cadence.
    boundaries = (markers if manual else timing_boundaries if timing_boundaries is not None
                  else [item["end"] for item in (moments or segments)])
    boundaries = sorted(set(value for value in boundaries if cursor < value <= end))
    while end - cursor >= 1 / 24 - 0.000001:
        boundary = next((value for value in boundaries if value - cursor >= 1 / 24 - 0.000001), end)
        if end - boundary < 1 / 24:
            boundary = end
        append(cursor, boundary)
        cursor = boundary
    if cursor < end and slots:
        # A sub-frame remainder belongs to the last slot; no cumulative drift.
        slots[-1]["end"] = end
    if not slots:
        raise ValueError("Choose a music passage at least one output frame long")
    if len(slots) > MAX_TIMELINE_SLOTS:
        raise ValueError(f"The music timeline is limited to {MAX_TIMELINE_SLOTS} slots; remove some cut markers")
    timeline = {"track_id": track["id"], "passage": dict(passage), "slots": slots, "provisional_timing": None}
    if moments:
        refresh_directions(timeline, document["analysis"])
    document["music_timeline"] = timeline
    return timeline


def draft_targets(document, slot_ids=None):
    """An explicit replacement retains the old selection until a valid choice."""
    timeline = document.get("music_timeline")
    if not timeline:
        if slot_ids is not None:
            raise ValueError("Create the music timeline before choosing slots to replace")
        return []
    slots = timeline["slots"]
    clips = {clip["id"]: clip for clip in document["clips"]}
    if slot_ids is None:
        return [slot for slot in slots if not slot.get("clip_id")]
    requested = set(slot_ids)
    if len(requested) != len(slot_ids) or requested - {slot["id"] for slot in slots}:
        raise ValueError("Selected music slots are missing or repeated; reload the timeline")
    selected = [slot for slot in slots if slot["id"] in requested]
    if any(clips.get(slot.get("clip_id"), {}).get("locked") for slot in selected):
        raise ValueError("Unlock the selected clip before requesting a replacement")
    return selected


def plan_targets(document, slot_ids=None):
    """Choose at most 32 exact slots without creating timing or changing sources."""
    timeline = document.get("music_timeline")
    if not timeline:
        raise ValueError("Create the music timeline before planning its shot directions")
    slots = timeline["slots"]
    if slot_ids is None:
        selected = [slot for slot in slots if not slot.get("clip_id")
                    and slot.get("direction_source") != "user"
                    and not (slot.get("direction") and slot.get("direction_source") is None)]
    else:
        requested = set(slot_ids)
        if len(requested) != len(slot_ids) or requested - {slot["id"] for slot in slots}:
            raise ValueError("Selected music slots are missing or repeated; reload the timeline")
        selected = [slot for slot in slots if slot["id"] in requested]
    if not selected:
        raise ValueError("No eligible empty slots need planning. Select unlocked slots explicitly to revise their directions")
    if len(selected) > MAX_MODEL_SHOTS:
        raise ValueError("Plan at most 32 shot directions at a time; select a smaller group of slots")
    clips = {clip["id"]: clip for clip in document["clips"]}
    if any(clips.get(slot.get("clip_id"), {}).get("locked") for slot in selected):
        raise ValueError("Unlock the selected clip before replanning its direction")
    return selected
