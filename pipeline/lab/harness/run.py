"""Harness v2 regeneration: concept, pools and assembly written as an ordinary timeline.

The output is the same project document every editor view already reads:
consecutive music slots with AI directions and resolved searches, one clip
per slot, up to six alternatives, and a timing receipt compatible with the
v1 timing-plan panel. Nothing here saves a project; the worker performs the
final checks and the single revision write.
"""

from __future__ import annotations

import tempfile
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable

from pipeline.lab.harness import assemble as asm
from pipeline.lab.harness import music_map
from pipeline.lab.harness.concept import plan_concept
from pipeline.lab.harness.pools import Candidate, gather
from pipeline.lab.harness.review import review

HARNESS_CONTRACT = "editor-harness-v2"


def load_music(document: dict[str, Any], config: Any) -> music_map.MusicMap:
    from pipeline.lab.music import _decode_audio, content_hash
    from pipeline.lab.store import LabStore

    track = LabStore(config.paths.state_dir).get_track(document["track"]["id"])
    source = Path(track["path"])
    if not source.is_file() or content_hash(source) != track["id"]:
        raise ValueError("Original music is missing or its content hash changed; import it again")
    passage = document["passage"]
    with tempfile.TemporaryDirectory(prefix="sr-map-") as temporary:
        signal, rate = _decode_audio(source, Path(temporary) / "passage.wav", passage["start"], passage["end"])
    return music_map.build(signal, rate, passage, document.get("rhythm"), document.get("analysis"))


def _film_titles(db: Any, film_ids: list[str]) -> list[str]:
    if not film_ids:
        return []
    from pipeline.evidence.library import list_films
    scope = set(film_ids)
    return [film.title for film in list_films(db) if film.film_id in scope]


def _evidence(candidate: Candidate, placement: asm.Placement) -> dict[str, Any]:
    from pipeline.lab.models import MusicMatchEvidence
    return MusicMatchEvidence(rank=max(1, candidate.rank or 1),
                              matched_text=(candidate.action or candidate.caption)[:1500],
                              matched_text_view="story" if candidate.action else "caption",
                              suggested_source_start=placement.source_start).model_dump(mode="json")


def _clip(placement: asm.Placement) -> dict[str, Any]:
    from pipeline.lab.models import ClipSelection
    candidate = placement.candidate
    return ClipSelection(id=str(uuid.uuid4()), film_id=candidate.film_id, unit_id=candidate.unit_id,
                         title=(candidate.action or candidate.caption)[:200], source_start=placement.source_start,
                         source_end=placement.source_start + (placement.end - placement.start)).model_dump(mode="json")


def _direction(act: dict[str, Any], placement: asm.Placement, reason: str, origin: float) -> dict[str, Any]:
    candidate = placement.candidate
    query = next((query for query in act["queries"] if query in candidate.queries), act["queries"][0])
    cue = (f"Its action peaks on the accent at {placement.accent['time'] - origin:.2f}s into the passage"
           if placement.accent is not None else f"{act.get('pace', 'balanced').capitalize()} section: {act.get('intent', '')}")
    return {"query": query[:400], "search_facet": "all", "purpose": str(act.get("intent") or "")[:600],
            "music_cue": cue[:600], "timing_note": reason[:600], "search_plan": None}


def _resolved(query: str, duration: float) -> dict[str, Any]:
    from pipeline.search.capabilities import CAPABILITY_VERSION
    return {"clauses": [{"kind": "text", "facet": "all", "text": query[:400], "reference_id": None}],
            "unverified_requirements": [], "references": [], "capability_version": CAPABILITY_VERSION,
            "min_duration": duration}


def _act_at(acts: list[dict[str, Any]], time: float) -> dict[str, Any]:
    return next((act for act in acts if act["start"] - 1e-6 <= time < act["end"] - 1e-6), acts[-1])


def placed_slot(placement: asm.Placement, alternatives: list[asm.Placement], previous: asm.Placement | None,
                act: dict[str, Any], origin: float) -> tuple[dict[str, Any], dict[str, Any]]:
    """A new clip and the slot fields presenting it: reason, evidence, AI direction and up to five alternatives."""
    reason = asm.reason(placement, previous)
    clip = _clip(placement)
    evidence = _evidence(placement.candidate, placement)
    rows = [{"clip": deepcopy(clip), "film_title": placement.candidate.film_title[:300], "reason": reason,
             "search_evidence": evidence}]
    rows += [{"clip": _clip(other), "film_title": other.candidate.film_title[:300],
              "reason": asm.reason(other, previous), "search_evidence": _evidence(other.candidate, other)}
             for other in alternatives[:5]]
    direction = _direction(act, placement, reason, origin)
    return clip, {"clip_id": clip["id"], "alternatives": rows, "reason": reason, "search_error": None,
                  "search_evidence": evidence, "direction": direction, "direction_source": "ai",
                  "needs_direction": False,
                  "resolved_search": _resolved(direction["query"], placement.end - placement.start)}


def build_timeline(document: dict[str, Any], placements: list[asm.Placement], options: list[list[asm.Placement]],
                   acts: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Slots and clips for the assembled edit (alternatives exclude every chosen shot)."""
    from pipeline.lab.timeline import section_for

    segments = (document.get("analysis") or {}).get("segments", [])
    slots, clips = [], []
    previous = None
    for placement, alternatives in zip(placements, options):
        clip, fields = placed_slot(placement, alternatives, previous, _act_at(acts, placement.start),
                                   document["passage"]["start"])
        clips.append(clip)
        slots.append({"id": str(uuid.uuid4()), "start": placement.start, "end": placement.end,
                      "section_index": section_for(placement.start, placement.end, segments), **fields})
        previous = placement
    return slots, clips


def timing_receipt(document: dict[str, Any], slots: list[dict[str, Any]], acts: list[dict[str, Any]],
                   artifact_id: str) -> dict[str, Any]:
    """The v1 timing-plan receipt shape, so the existing timing panel shows the measured cuts."""
    from pipeline.lab.pacing import timing_diagnostics

    passage, fps = document["passage"], document["fps"]
    total = round((passage["end"] - passage["start"]) * fps)
    frames = [total if index == len(slots) - 1 else round((slot["end"] - passage["start"]) * fps)
              for index, slot in enumerate(slots)]
    notes = []
    for act in acts[:32]:
        first = round((act["start"] - passage["start"]) * fps)
        last = min(total, round((act["end"] - passage["start"]) * fps))
        if last > first:
            notes.append({"start_frame": first, "end_frame": last, "evidence_ids": [],
                          "reason": (f"Editorial choice: {act.get('intent') or 'act'} "
                                     f"({act.get('pace')} pace, {act.get('fame')} fame); cuts follow measured beats and accents.")[:500]})
    diagnostics = timing_diagnostics(slots)
    return {"contract": asm.ASSEMBLY_CONTRACT, "artifact_id": artifact_id, "track_id": document["track"]["id"],
            "passage": deepcopy(passage), "fps": fps, "end_frames": frames, "final_end_frames": frames,
            "notes": notes, "cache_reused": False, "nominal_timing": diagnostics, "final_timing": diagnostics}


def _document(document: dict[str, Any], planned: dict[str, Any], music: music_map.MusicMap,
              placements: list[asm.Placement], options: list[list[asm.Placement]],
              extra: dict[str, Any]) -> dict[str, Any]:
    from pipeline.lab.music import digest

    slots, clips = build_timeline(document, placements, options, planned["acts"])
    artifact_id = digest({"concept": planned["artifact_id"], "map": music.summary(),
                          "edit": [(p.candidate.unit_id, p.start, p.end, p.source_start) for p in placements]})
    result = deepcopy(document)
    result["clips"] = clips
    result["music_timeline"] = {"track_id": document["track"]["id"], "passage": deepcopy(document["passage"]),
                                "slots": slots, "provisional_timing": None}
    if (result.get("visual_plan") or {}).get("source") != "user":
        # Like v1 regeneration, the AI plan is shown as the arc; a user-authored plan is never replaced.
        result["visual_plan"] = {"arc": planned["concept"][:1200], "motifs": "; ".join(planned["motifs"])[:1000],
                                 "source": "ai"}
    result["direction_plan"] = {
        "contract": HARNESS_CONTRACT, "track_id": document["track"]["id"], "passage": deepcopy(document["passage"]),
        "concept": planned["concept"], "motifs": planned["motifs"], "concept_artifact": planned["artifact_id"],
        "acts": [{key: act[key] for key in ("start", "end", "intent", "queries", "fame", "pace", "planned")}
                 for act in planned["acts"]],
        "music_map": music.summary(), **extra,
        "timing_plan": timing_receipt(document, slots, planned["acts"], artifact_id)}
    return result


def _assemble_and_review(document, planned, music, acts, config, job_id, progress, exclude=()):
    progress("Assembling cuts, shots and source windows to the beat")
    placements = asm.assemble(music, acts, exclude_units=exclude)
    options = asm.alternatives(placements, acts, music)
    placements, reviewed = review(document, planned, placements, options, config, job_id, progress)
    if reviewed["applied"]:
        options = asm.alternatives(placements, acts, music)
    return placements, options, reviewed


def _critique_round(document, planned, music, acts, placements, options, config, db, job_id, progress, exclude):
    """Render the rough cut, have it watched, and re-assemble once around the flagged issues."""
    from dataclasses import replace

    from pipeline.lab.harness import critique
    from pipeline.lab.media import render_from_manifest, render_manifest
    from pipeline.lab.store import LabStore

    draft = _document(document, planned, music, placements, options, {})
    store = LabStore(config.paths.state_dir)
    identity = f"{job_id}-rough-cut"
    progress("Rendering a rough cut to watch")
    render_from_manifest(identity, render_manifest(draft, db, store, mode="preview"), config, db, store, progress)
    path = config.paths.assets_dir / "lab" / "renders" / identity / "output.mp4"
    direction = (document.get("editor_direction") or {}).get("instruction") or document.get("brief") or ""
    watched = critique.watch(path, direction, progress)
    rules = critique.constraints(watched, placements, acts, document["passage"]["start"])
    receipt = {"contract": critique.CRITIQUE_CONTRACT, "model": critique.MODEL, "summary": watched.summary,
               "issues": [issue.model_dump(mode="json") for issue in watched.issues], **rules}
    if not rules["banned"] and all(scale == 1.0 for scale in rules["pace_scales"]):
        progress("The rough cut needs no changes")
        return placements, options, None, receipt
    progress(f"Re-assembling around {len(watched.issues)} flagged issues")
    acts = [replace(act, pace_scale=scale) for act, scale in zip(acts, rules["pace_scales"])]
    placements, options, reviewed = _assemble_and_review(document, planned, music, acts, config, f"{job_id}-r2", progress,
                                                         exclude=set(exclude) | set(rules["banned"]))
    return placements, options, reviewed, receipt


def _user_owned(slot: dict[str, Any]) -> bool:
    return slot.get("direction_source") == "user" or (bool(slot.get("direction")) and slot.get("direction_source") is None)


def fill(document: dict[str, Any], config: Any, db: Any, progress: Callable[[str], None], job_id: str,
         target_ids: list[str]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Fill empty slots of an existing timeline: cuts and placed shots stay; the optimizer picks the rest.

    The edit's v2 concept is reused when it matches the passage. A slot with
    its own (user-written) search gets a one-slot act from that query. Placed
    shots are fixed neighbours, so new picks keep continuity with them.
    """
    from pipeline.lab.harness.pools import placed_candidates

    progress("Measuring the music's beats, accents and loudness")
    music = load_music(document, config)
    film_ids = list(document.get("film_ids") or [])
    saved_plan = document.get("direction_plan") or {}
    if (saved_plan.get("contract") == HARNESS_CONTRACT and saved_plan.get("passage") == document["passage"]
            and saved_plan.get("acts")):
        planned = {"concept": saved_plan.get("concept") or "", "motifs": saved_plan.get("motifs") or [],
                   "artifact_id": saved_plan.get("concept_artifact"), "acts": deepcopy(saved_plan["acts"])}
        progress("Using this edit's concept")
    else:
        planned = plan_concept(document, music, config, job_id, progress, film_titles=_film_titles(db, film_ids))
    slots = document["music_timeline"]["slots"]
    targets = set(target_ids)
    clips = {clip["id"]: clip for clip in document["clips"]}
    placed = {slot["clip_id"] for slot in slots if slot.get("clip_id") and slot["id"] not in targets}
    used_units = {clips[clip_id]["unit_id"] for clip_id in placed if clips.get(clip_id, {}).get("unit_id")}
    pools = gather(db, config, planned["acts"], film_ids=film_ids or None, exclude_units=used_units, progress=progress)
    acts = [asm.Act(spec["start"], spec["end"], pool, pace=spec["pace"], fame=spec["fame"], intent=spec["intent"])
            for spec, pool in zip(planned["acts"], pools)]
    overrides = {}
    for slot in slots:
        if slot["id"] in targets and _user_owned(slot) and slot.get("direction"):
            query = slot["direction"]["query"]
            pool = gather(db, config, [{"queries": [query], "fame": "any"}], film_ids=film_ids or None,
                          exclude_units=used_units, progress=progress)[0]
            overrides[(slot["start"], slot["end"])] = asm.Act(slot["start"], slot["end"], pool, pace="balanced",
                                                               intent=slot["direction"].get("purpose") or query)
    neighbours = placed_candidates(db, [clips[clip_id] for clip_id in placed if clip_id in clips])
    fixed = []
    for slot in slots:
        clip = clips.get(slot.get("clip_id") or "")
        if slot["id"] in targets or clip is None:
            continue
        # A placed clip always holds its span; without index evidence it is a plain, neutral neighbour.
        neighbour = neighbours.get(clip["id"]) or Candidate(
            unit_id=clip.get("unit_id") or clip["id"], film_id=clip["film_id"], film_title=clip.get("title") or "",
            t_start=clip["source_start"], t_end=clip["source_end"])
        fixed.append(asm.Fixed(slot["start"], slot["end"], neighbour, clip["source_start"]))
    boundaries = [slot["start"] for slot in slots] + [slots[-1]["end"]]
    progress("Choosing shots and source windows for the empty slots")
    placements = asm.assemble(music, acts, fixed=fixed, boundaries=boundaries, exclude_units=used_units,
                              overrides=overrides)
    options = asm.alternatives(placements, acts, music)
    result = deepcopy(document)
    by_start = {round(p.start, 4): (p, o) for p, o in zip(placements, options)}
    filled, peaks = 0, 0
    previous = None
    for slot in result["music_timeline"]["slots"]:
        chosen = by_start.get(round(slot["start"], 4))
        if slot["id"] in targets and chosen is not None and not chosen[0].fixed and chosen[0].candidate.unit_id:
            placement, alternatives = chosen
            clip, fields = placed_slot(placement, alternatives, previous, _act_at(planned["acts"], placement.start),
                                       document["passage"]["start"])
            if _user_owned(slot):              # the user's written search stays the slot's direction
                for key in ("direction", "direction_source", "needs_direction", "resolved_search"):
                    fields.pop(key)
            result["clips"].append(clip)
            slot.update(fields)
            filled += 1
            peaks += placement.accent is not None
        previous = chosen[0] if chosen is not None else None
    diagnostics = {"contract": HARNESS_CONTRACT, "assembly_contract": asm.ASSEMBLY_CONTRACT, "mode": "fill",
                   "filled": filled, "peaks_on_accents": peaks, "requested": len(targets),
                   "candidate_count": sum(len(pool) for pool in pools), "selected_count": filled,
                   "user_searches": sum(1 for act in overrides.values() if act.pool)}
    result["analysis"] = {**(result.get("analysis") or {}), "draft": {**diagnostics,
        "unfilled_slot_ids": [slot["id"] for slot in result["music_timeline"]["slots"] if not slot.get("clip_id")]}}
    progress(f"Filled {filled} of {len(targets)} shots; {peaks} action peaks land on musical accents")
    return result, diagnostics


def regenerate(document: dict[str, Any], config: Any, db: Any, progress: Callable[[str], None], job_id: str, *,
               previous_units: set[str] | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    """A complete new edit for the passage (the caller guarantees current listening and no locks)."""
    progress("Measuring the music's beats, accents and loudness")
    music = load_music(document, config)
    film_ids = list(document.get("film_ids") or [])
    planned = plan_concept(document, music, config, job_id, progress, film_titles=_film_titles(db, film_ids))
    pools = gather(db, config, planned["acts"], film_ids=film_ids or None, exclude_units=previous_units or set(),
                   progress=progress)
    if not any(pools):
        raise ValueError("No footage matched this concept; widen the film scope or the direction")
    acts = [asm.Act(spec["start"], spec["end"], pool, pace=spec["pace"], fame=spec["fame"], intent=spec["intent"])
            for spec, pool in zip(planned["acts"], pools)]
    placements, options, reviewed = _assemble_and_review(document, planned, music, acts, config, job_id, progress)
    extra: dict[str, Any] = {"review": reviewed}
    if getattr(config.lab, "harness_critique", False):
        placements, options, second, watched = _critique_round(document, planned, music, acts, placements, options,
                                                               config, db, job_id, progress, previous_units or set())
        extra["critique"] = watched
        if second is not None:
            extra["review_after_critique"] = second
    result = _document(document, planned, music, placements, options, extra)
    peaks = sum(p.accent is not None for p in placements)
    with_evidence = sum(c.peak_time is not None or bool(c.camera_segments) for pool in pools for c in pool)
    diagnostics = {"contract": HARNESS_CONTRACT, "assembly_contract": asm.ASSEMBLY_CONTRACT,
                   "concept_artifact": planned["artifact_id"], "shots": len(placements),
                   "films": len({p.candidate.film_id for p in placements}), "peaks_on_accents": peaks,
                   "pool_sizes": [len(pool) for pool in pools], "pool_evidence": with_evidence,
                   "previous_excluded": len(previous_units or ()), "review_swaps": len(reviewed["applied"]),
                   "critique_issues": len((extra.get("critique") or {}).get("issues") or []),
                   "candidate_count": sum(len(pool) for pool in pools), "selected_count": len(placements),
                   "unfilled_slot_ids": []}
    slots = result["music_timeline"]["slots"]
    result["analysis"] = {**(result.get("analysis") or {}),
                          "draft": {**diagnostics, "reasons": {slot["clip_id"]: slot["reason"] for slot in slots}}}
    progress(f"Assembled {len(placements)} shots from {diagnostics['films']} films; "
             f"{peaks} action peaks land on musical accents")
    return result, diagnostics
