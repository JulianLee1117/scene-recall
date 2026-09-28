"""Route frozen edits to whole-passage timing or fixed-shot generation.

Stages may cache derivations but never save projects. The worker performs one
final source/lock check and one revision write, or retains a stale proposal.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from pipeline.lab.models import GenerateOptions, ProjectDocument
from pipeline.lab.limits import MAX_MODEL_SHOTS
from pipeline.lab.timeline import draft_targets, ensure_timeline, require_replan_unlocked
from pipeline.lab.pacing import uses_bounded_generation


MAX_GENERATE_SLOTS = MAX_MODEL_SHOTS
_DIRECTION_FIELDS = ("direction", "direction_source", "needs_direction", "feedback")
_PROTECTED_FIELDS = (*_DIRECTION_FIELDS, "resolved_search", "search_evidence", "alternatives", "reason", "search_error")


def validate_generate_request(document, options=None, slot_ids=None):
    mode = GenerateOptions.model_validate(options or {}).mode
    if not document.get("track"):
        raise ValueError("Import music before generating an edit")
    if mode == "improve":
        if not isinstance(slot_ids, list) or len(slot_ids) != 1:
            raise ValueError("A targeted replacement requires exactly one selected music slot")
        targets = draft_targets(document, slot_ids)
    else:
        if slot_ids is not None:
            raise ValueError("Whole-edit generation cannot target selected slots; use targeted replacement mode to replace one slot")
        if mode == "regenerate":
            # Explicit whole-edit intent may replace cuts, never override locks.
            require_replan_unlocked(document)
        targets = draft_targets(document)
    long_passage = uses_bounded_generation(document)
    if not long_passage and mode != "regenerate" and len(targets) > MAX_GENERATE_SLOTS:
        raise ValueError("Generate edit is limited to 32 empty shots per request. Reduce the empty timeline or use targeted shot generation")
    return mode, targets


def _regeneration_document(original):
    """A private fresh plan; the saved edit and retained footage remain intact."""
    document = deepcopy(original)
    document.update(clips=[], music_timeline=None, direction_plan=None)
    # Listening describes the same audio even when editing preferences change.
    # Previous source selections and draft diagnostics are not listening evidence.
    if document.get("analysis"):
        document["analysis"].pop("draft", None)
        for part in document["analysis"].get("parts", []):
            part["analysis"].pop("draft", None)
    if document.get("editor_direction") is not None or (document.get("visual_plan") or {}).get("source") != "user":
        document["visual_plan"] = None
    return document


def _analysis_is_current(document):
    from pipeline.lab.music import validate_interpretation
    analysis = document.get("analysis") or {}
    provenance = analysis.get("provenance") or {}
    if not isinstance(provenance, dict) or provenance.get("track") != document["track"]["id"] or provenance.get("passage") != document["passage"]:
        return False
    try:
        validate_interpretation(analysis, document["passage"])
    except (KeyError, TypeError, ValueError):
        return False
    return True


def _user_direction(slot):
    return slot.get("direction_source") == "user" or (slot.get("direction") and slot.get("direction_source") is None)


def _needs_plan(slot, *, refresh_ai=False):
    from pipeline.search.capabilities import CAPABILITY_VERSION
    if _user_direction(slot):
        return False
    # Settings save does not rewrite a timeline. Explicit Fill gaps can refresh
    # AI-owned searches from the current direction while keeping written queries.
    if refresh_ai and not slot.get("clip_id"):
        return True
    resolved = slot.get("resolved_search") or {}
    return not (slot.get("direction") and slot.get("direction_source") == "ai" and not slot.get("needs_direction")
                and resolved.get("capability_version") == CAPABILITY_VERSION
                and abs(resolved.get("min_duration", 0) - (slot["end"] - slot["start"])) <= 0.0001)


def _stage_job(job, kind, document, slot_ids=None):
    snapshot = {**job.get("snapshot", {}), "document": document, "replan_timing": False}
    snapshot.pop("generate", None)
    snapshot.pop("slot_ids", None)
    if slot_ids is not None:
        snapshot["slot_ids"] = list(slot_ids)
    return {**job, "kind": kind, "document": document, "snapshot": snapshot}


def _guard_edit(original, proposed, mode, target_ids):
    """Defense in depth: no stage may move cuts or rewrite protected choices."""
    before = original.get("music_timeline")
    if not before:
        return
    after = proposed.get("music_timeline") or {}
    old_slots, new_slots = before["slots"], after.get("slots", [])
    if [(slot["id"], slot["start"], slot["end"]) for slot in old_slots] != [(slot["id"], slot["start"], slot["end"]) for slot in new_slots]:
        raise ValueError("Generation changed existing music cut positions")
    old_clips = {clip["id"]: clip for clip in original["clips"]}
    new_clips = {clip["id"]: clip for clip in proposed["clips"]}
    for old, new in zip(old_slots, new_slots):
        targeted = mode == "improve" and old["id"] in target_ids
        if old.get("clip_id") and not targeted:
            if old["clip_id"] != new.get("clip_id") or old_clips[old["clip_id"]] != new_clips.get(old["clip_id"]):
                raise ValueError("Generation changed an existing scene outside the requested shot")
        if (old.get("clip_id") or _user_direction(old)) and not targeted:
            fields = _PROTECTED_FIELDS if old.get("clip_id") else _DIRECTION_FIELDS
            if any(old.get(key) != new.get(key) for key in fields):
                raise ValueError("Generation changed a protected shot direction")


def _inspect_generation(document, config, db, progress, job_id, contexts, *, original=None, mode=None):
    """One optional review budget over the complete provisional edit."""
    from pipeline.lab.footage_review import inspect_edit
    from pipeline.lab.music import write_json
    from pipeline.lab.store import LabStore

    progress("Inspecting selected footage before the final edit check")
    write_json(config.paths.assets_dir / "lab" / "requests" / f"{job_id}-inspection-input.json",
               {"document": document, "contexts": contexts})
    store = LabStore(config.paths.state_dir)
    proposed, diagnostic = inspect_edit(document, config, db, progress, job_id, contexts=contexts,
                                       cancelled=lambda: store.is_cancelled(job_id))
    if mode == "improve" and original is not None:
        # A rejected replacement must preserve the existing scene, just as a
        # selector abstention does. Its old evidence is not the rejected sample.
        old_slots = {slot["id"]: slot for slot in original["music_timeline"]["slots"]}
        old_clips = {clip["id"]: clip for clip in original["clips"]}
        for detail in diagnostic.get("targets", []):
            old = old_slots.get(detail["slot_id"])
            slot = next(slot for slot in proposed["music_timeline"]["slots"] if slot["id"] == detail["slot_id"])
            if slot.get("clip_id") or not old or not old.get("clip_id"):
                continue
            for key in ("clip_id", "reason", "search_evidence"):
                slot[key] = deepcopy(old.get(key))
            if old["clip_id"] not in {clip["id"] for clip in proposed["clips"]}:
                proposed["clips"].append(deepcopy(old_clips[old["clip_id"]]))
            detail["reason"] = (detail["reason"][:430] + " Replacement rejected; the original scene is kept and was not inspected in this review.")
    return proposed, diagnostic


def run_generate_job(job, config, db, progress):
    from pipeline.lab.direction_planner import run_direction_job
    from pipeline.lab.music import content_hash, run_music_job
    from pipeline.lab.music_planner import fill_timeline
    from pipeline.lab.store import LabStore

    original = ProjectDocument.model_validate(job["document"]).model_dump(mode="json")
    if not original.get("music_timeline") and original["clips"]:
        # A legacy arrangement already owns its sequential timing. Establish
        # that local baseline before any optional audio/creative stage.
        ensure_timeline(original)
    document = deepcopy(original)
    snapshot = job.get("snapshot", {})
    mode, targets = validate_generate_request(document, snapshot.get("generate"), snapshot.get("slot_ids"))
    if mode == "regenerate" or uses_bounded_generation(original):
        from pipeline.lab.long_generation import run_long_generate_job
        return run_long_generate_job({**job, "document": original}, config, db, progress)
    progress("Checking the saved edit and requested shots")
    if mode == "fill" and document.get("music_timeline") and not targets:
        message = "Every shot is already filled. Select a shot, edit its search, and choose Find scenes for alternatives."
        progress(message)
        return None, {"unchanged": True, "message": message, "stages": []}

    # A reused interpretation must still refer to the retained original audio.
    track = LabStore(config.paths.state_dir).get_track(document["track"]["id"])
    source = Path(track["path"])
    progress("Checking the original music")
    if not source.is_file() or content_hash(source) != track["id"]:
        raise ValueError("Original music is missing or its content hash changed; import it again")

    stages = []
    if not _analysis_is_current(document):
        progress("Listening to this passage before planning the edit")
        document = run_music_job(_stage_job(job, "analyze", document), config, db, progress)
        stages.append("analyze")
        # Audio analysis can suggest directions; only requested creative work
        # may replace user directions or the directions of placed scenes.
        previous = {slot["id"]: slot for slot in (original.get("music_timeline") or {}).get("slots", [])}
        for slot in (document.get("music_timeline") or {}).get("slots", []):
            old = previous.get(slot["id"])
            if old and (old.get("clip_id") or _user_direction(old)):
                for key in _PROTECTED_FIELDS:
                    slot[key] = deepcopy(old.get(key))
        progress("Music interpretation ready; existing cuts and choices are preserved")
    else:
        progress("Using the current music interpretation")
    if document.get("music_timeline"):
        # Filling accepts the current cuts even if retrieval leaves gaps.
        document["music_timeline"]["provisional_timing"] = None
    ensure_timeline(document)
    mode, targets = validate_generate_request(document, snapshot.get("generate"), snapshot.get("slot_ids"))
    if len(targets) > MAX_GENERATE_SLOTS:
        raise ValueError("Generate edit is limited to 32 shots per request")
    if not targets:
        progress("Every shot is already filled; existing choices are unchanged")
        return None, {"unchanged": True, "message": "Every shot is already filled.", "stages": stages}
    target_ids = [slot["id"] for slot in targets]
    plan_ids = target_ids if mode == "improve" else [slot["id"] for slot in targets
        if _needs_plan(slot, refresh_ai=document.get("editor_direction") is not None)]
    if plan_ids:
        progress("Planning the requested shots with the whole edit in view")
        plan_job = _stage_job(job, "plan", document, plan_ids)
        document = run_direction_job(plan_job, config, db, progress)
        stages.append("plan")
        progress("Shot directions ready; finding scenes for the fixed timeline")
    else:
        progress("Using your saved shot directions")
    _guard_edit(original, document, mode, set(target_ids))
    progress("Finding scenes for the requested shots")
    selection_options = {}
    review_contexts = [] if config.lab.footage_inspection else None
    if review_contexts is not None:
        selection_options["review_contexts"] = review_contexts
    if snapshot.get("sequence_context"):
        selection_options["sequence_context"] = deepcopy(snapshot["sequence_context"])
    if snapshot.get("previous_sources"):
        selection_options["previous_sources"] = deepcopy(snapshot["previous_sources"])
    document = fill_timeline(document, config, db, progress, job["id"], target_ids, **selection_options)
    stages.append("draft")
    inspection = None
    if review_contexts is not None:
        document, inspection = _inspect_generation(document, config, db, progress, job["id"], review_contexts,
                                                    original=original, mode=mode)
        stages.append("inspect")
    progress("Checking the complete edit before saving one revision")
    document = ProjectDocument.model_validate(document).model_dump(mode="json")
    _guard_edit(original, document, mode, set(target_ids))
    remaining = sum(not slot.get("clip_id") for slot in document["music_timeline"]["slots"])
    failed = [{"slot_id": slot["id"], "error": slot.get("search_error") or "No fitting scene found"}
              for slot in document["music_timeline"]["slots"]
              if slot["id"] in target_ids and (slot.get("search_error") or not slot.get("clip_id"))]
    if mode == "improve" and failed:
        old_target = next(slot for slot in original["music_timeline"]["slots"] if slot["id"] == target_ids[0])
        message = ("No fitting replacement found; your original shot is kept." if old_target.get("clip_id") else
                   "No fitting scene found; the selected shot remains empty.")
    else:
        message = ("Requested scene replacement ready." if mode == "improve" else
                   f"Edit ready with {remaining} unfilled shots." if remaining else "Your edit is ready to play.")
    draft = document["analysis"].setdefault("draft", {})
    selected_count = sum(bool(slot.get("clip_id")) and not slot.get("search_error")
                         for slot in document["music_timeline"]["slots"] if slot["id"] in target_ids)
    abstained = [slot["id"] for slot in document["music_timeline"]["slots"]
                if slot["id"] in target_ids and (slot.get("search_error") or not slot.get("clip_id"))]
    draft.update(selected_count=selected_count, abstained_slot_ids=abstained,
                 unfilled_slot_ids=[slot["id"] for slot in document["music_timeline"]["slots"] if not slot.get("clip_id")])
    if inspection is not None:
        draft["footage_inspection"] = inspection
    return document, {"message": message, "stages": stages, "generation_mode": mode, "requested_slot_ids": target_ids,
                      "selection_contract": draft.get("selection_contract"), "timing_mode": draft.get("timing_mode", "fixed"),
                      "timing_adjustments": draft.get("timing_adjustments", []),
                      "candidate_count": draft.get("candidate_count", 0), "selected_count": selected_count,
                      "abstained_slot_ids": abstained,
                      **({"footage_inspection": inspection} if inspection is not None else {}),
                      "remaining_gaps": remaining, "failed_slots": failed}
