"""Sequential bounded work inside one frozen, atomic long-song generation job."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from pipeline.lab.limits import MAX_AUDIO_PART_SECONDS, MAX_MODEL_SHOTS, MAX_SAVED_CLIPS
from pipeline.lab.pacing import batch_slots, timing_diagnostics
from pipeline.lab.source_timing import initial_timing_scope, validate_result
from pipeline.lab.models import ProjectDocument
from pipeline.lab.editorial_context import visual_plan_context
from pipeline.lab.timeline import ensure_timeline, section_for


LONG_GENERATION_CONTRACT = "music-led-batched-generation-v2"


def _placed(document):
    ids = {slot["clip_id"] for slot in (document.get("music_timeline") or {}).get("slots", []) if slot.get("clip_id")}
    return [deepcopy(clip) for clip in document["clips"] if clip["id"] in ids]


def _context(document, passage, index, count, db):
    from pipeline.lab.direction_planner import _source_context

    outline = [{"passage": deepcopy(part["passage"]), "summary": part["analysis"]["summary"],
                "meaning": deepcopy(part["analysis"].get("song_meaning"))}
               for part in document["analysis"].get("parts", [])]
    if not outline:
        outline = [{"passage": deepcopy(document["passage"]), "summary": document["analysis"]["summary"],
                    "meaning": deepcopy(document["analysis"].get("song_meaning"))}]
    slots = (document.get("music_timeline") or {}).get("slots", [])
    by_id = {clip["id"]: clip for clip in document["clips"]}
    before = [slot for slot in slots if slot["end"] <= passage["start"] + 1e-6][-3:]
    after = [slot for slot in slots if slot["start"] >= passage["end"] - 1e-6][:3]
    neighbors = [{"start": slot["start"], "end": slot["end"], "direction": deepcopy(slot.get("direction")),
                  "selected_source": _source_context(by_id[slot["clip_id"]], db) if slot.get("clip_id") else None}
                 for slot in before + after]
    return {"contract": LONG_GENERATION_CONTRACT, "whole_passage": deepcopy(document["passage"]),
            "part_passage": deepcopy(passage), "part_number": index + 1, "part_count": count,
            "song_outline": outline, "visual_plan": visual_plan_context(document),
            "neighboring_shots": neighbors,
            "pacing_plan": deepcopy((document.get("direction_plan") or {}).get("timing_plan")),
            "note": "Processing parts are bounded work scopes, not detected verses or required story resolutions. Neighbor footage is context, not an offered candidate."}


def _part_document(document, passage, analysis):
    from pipeline.lab.long_audio import rhythm_for_passage

    part = deepcopy(document)
    part.update(passage=deepcopy(passage), analysis=deepcopy(analysis), rhythm=rhythm_for_passage(document.get("rhythm") or {}, passage),
                music_timeline=None, clips=[], direction_plan=None, audio_fade_in_seconds=0,
                audio_fade_out_seconds=0, dialogue_clips=[])
    return part


def _part_job(job, document, index, context, *, previous_sources=None, slot_ids=None):
    snapshot = {**job.get("snapshot", {}), "document": document, "generate": {"mode": "fill"},
                "sequence_context": context, "previous_sources": deepcopy(previous_sources or [])}
    snapshot.pop("slot_ids", None)
    if slot_ids is not None:
        snapshot["slot_ids"] = list(slot_ids)
    return {**job, "id": f"{job['id']}-part-{index + 1:02}", "document": document, "snapshot": snapshot}


def _fixed_windows(document, targets):
    slots = document["music_timeline"]["slots"]
    wanted = {slot["id"] for slot in targets}
    windows, selected, left, right = [], [], None, None
    for index, slot in enumerate(slots):
        if slot["id"] not in wanted:
            continue
        if selected and (len(selected) >= MAX_MODEL_SHOTS or slot["end"] - slots[left]["start"] > MAX_AUDIO_PART_SECONDS):
            windows.append((left, right, selected))
            selected, left = [], None
        if left is None:
            left = index
        right = index
        selected.append(slot["id"])
    if selected:
        windows.append((left, right, selected))
    return windows


def _merge_fixed(document, result, left, right):
    old_slots = document["music_timeline"]["slots"][left:right + 1]
    new_slots = result["music_timeline"]["slots"]
    by_id = {clip["id"]: clip for clip in result["clips"]}
    replaced = {old["clip_id"]: by_id[new["clip_id"]] for old, new in zip(old_slots, new_slots)
                if old.get("clip_id") and new.get("clip_id") and old["clip_id"] != new["clip_id"]}
    document["clips"] = [deepcopy(replaced.get(clip["id"], clip)) for clip in document["clips"]]
    document["music_timeline"]["slots"][left:right + 1] = deepcopy(result["music_timeline"]["slots"])
    identities = {clip["id"] for clip in document["clips"]}
    document["clips"].extend(deepcopy(clip) for clip in result["clips"] if clip["id"] not in identities)
    for slot in document["music_timeline"]["slots"][left:right + 1]:
        slot["section_index"] = section_for(slot["start"], slot["end"], document["analysis"]["segments"])


def _harness_regenerate(original, document, config, db, progress, job, stages):
    """Whole-edit regeneration with harness v2: measured music, concept, pools and assembly."""
    from pipeline.lab.harness.run import HARNESS_CONTRACT, regenerate

    previous = {clip["unit_id"] for clip in _placed(original) if clip.get("unit_id")}
    document.update(clips=[], music_timeline=None, direction_plan=None)
    proposed, diagnostics = regenerate(document, config, db, progress, job["id"], previous_units=previous)
    stages.append("harness")
    # Earlier footage stays in the bin, as with v1 regeneration.
    proposed["clips"] = deepcopy(original["clips"]) + proposed["clips"]
    if len(proposed["clips"]) > MAX_SAVED_CLIPS:
        raise ValueError(f"Regeneration would exceed {MAX_SAVED_CLIPS} saved clips. Clear unused Saved clips first; the current edit is unchanged")
    progress("Checking the complete edit before saving one revision")
    proposed = ProjectDocument.model_validate(proposed).model_dump(mode="json")
    slots = proposed["music_timeline"]["slots"]
    return proposed, {"message": "Your edit is ready to play.", "contract": HARNESS_CONTRACT, "generation_mode": "regenerate",
                      "stages": stages, "timing_mode": "measured-assembly",
                      "timing_plan": proposed["direction_plan"]["timing_plan"],
                      "requested_slot_ids": [slot["id"] for slot in slots], "selected_count": len(slots),
                      "candidate_count": diagnostics["candidate_count"], "harness": diagnostics,
                      "remaining_gaps": 0, "failed_slots": []}


def run_long_generate_job(job, config, db, progress):
    from pipeline.lab.generation import (_PROTECTED_FIELDS, _analysis_is_current, _guard_edit, _inspect_generation, _needs_plan, _regeneration_document, _user_direction,
                                         _stage_job, validate_generate_request)
    from pipeline.lab.direction_planner import run_direction_job
    from pipeline.lab.timing_planner import run_timing_job
    from pipeline.lab.music import content_hash, run_music_job
    from pipeline.lab.music_planner import fill_timeline
    from pipeline.lab.store import LabStore

    original = ProjectDocument.model_validate(job["document"]).model_dump(mode="json")
    if not original.get("music_timeline") and original["clips"]:
        ensure_timeline(original)
    mode, targets = validate_generate_request(original, job.get("snapshot", {}).get("generate"), job.get("snapshot", {}).get("slot_ids"))
    if mode == "fill" and original.get("music_timeline") and not targets:
        return None, {"unchanged": True, "message": "Every shot is already filled.", "stages": []}
    whole = mode == "regenerate"
    if not whole and len(original["clips"]) + sum(not slot.get("clip_id") for slot in targets) > MAX_SAVED_CLIPS:
        raise ValueError(f"These slots would exceed the project's {MAX_SAVED_CLIPS} saved clips, including its bin. Remove unused saved clips before finding more scenes; the saved edit is unchanged")
    if whole and len(original["clips"]) >= MAX_SAVED_CLIPS:
        raise ValueError(f"Regeneration would exceed {MAX_SAVED_CLIPS} saved clips. Clear unused Saved clips first; the current edit is unchanged")
    document = _regeneration_document(original) if mode == "regenerate" else deepcopy(original)
    progress("Checking the saved edit and requested shots")
    stages = []
    track = LabStore(config.paths.state_dir).get_track(document["track"]["id"])
    source = Path(track["path"])
    if not source.is_file() or content_hash(source) != track["id"]:
        raise ValueError("Original music is missing or its content hash changed; import it again")
    if not _analysis_is_current(document):
        from pipeline.lab.long_audio import recover_numeric_analysis

        recovered = recover_numeric_analysis(document.get("analysis"), config,
            track_id=document["track"]["id"], passage=document["passage"])
        if recovered is not None:
            document["analysis"] = recovered
            progress("Restored the original cached music interpretation after saved-number normalization")
    if not _analysis_is_current(document):
        progress("Listening to this passage before planning the edit")
        document = run_music_job(_stage_job(job, "analyze", document), config, db, progress)
        stages.append("analyze")
        if mode != "regenerate":
            previous = {slot["id"]: slot for slot in (original.get("music_timeline") or {}).get("slots", [])}
            for slot in (document.get("music_timeline") or {}).get("slots", []):
                old = previous.get(slot["id"])
                if old and (old.get("clip_id") or _user_direction(old)):
                    for key in _PROTECTED_FIELDS:
                        slot[key] = deepcopy(old.get(key))
    else:
        progress("Using the current music interpretation")
    if not _analysis_is_current(document):
        raise ValueError("Current scoped music analysis is required for full-song generation")
    diagnostics = []
    inspection = None
    review_contexts = [] if config.lab.footage_inspection else None
    inspection_options = {"review_contexts": review_contexts} if review_contexts is not None else {}
    if whole and getattr(config.lab, "harness", "v1") == "v2":
        return _harness_regenerate(original, document, config, db, progress, job, stages)
    if whole:
        from pipeline.lab.long_audio import analysis_for_passage

        document.update(clips=[], music_timeline=None, direction_plan=None)
        progress("Planning musical pacing across the complete passage")
        document = run_timing_job(_stage_job(job, "plan", document), config, progress)
        stages.append("timing")
        slots = document["music_timeline"]["slots"]
        retained = original["clips"]
        if len(retained) + len(slots) > MAX_SAVED_CLIPS:
            raise ValueError(f"Regeneration would exceed {MAX_SAVED_CLIPS} saved clips. Clear unused Saved clips first; the current edit is unchanged")
        timing_plan = deepcopy(document["direction_plan"]["timing_plan"])
        groups = batch_slots(slots)
        plans, left = [], 0
        previous = _placed(original)
        for index, group in enumerate(groups):
            right = left + len(group) - 1
            passage = {"start": group[0]["start"], "end": group[-1]["end"]}
            target_ids = [slot["id"] for slot in group]
            context = _context(document, passage, index, len(groups), db)
            part = _part_document(document, passage, analysis_for_passage(document["analysis"], passage))
            part["music_timeline"] = {"track_id": document["track"]["id"], "passage": passage,
                                      "provisional_timing": None, "slots": deepcopy(group)}
            for slot in part["music_timeline"]["slots"]:
                slot["section_index"] = section_for(slot["start"], slot["end"], part["analysis"]["segments"])
            timing_scope = initial_timing_scope(part)
            part_job = _part_job(job, part, index, context, slot_ids=target_ids)
            plan_job = _stage_job(part_job, "plan", part, target_ids)
            plan_job["snapshot"]["timing_scope"] = deepcopy(timing_scope)
            progress(f"Planning shots · group {index + 1} of {len(groups)} · {len(group)} shots")
            part = run_direction_job(plan_job, config, db, progress)
            if "plan" not in stages:
                stages.append("plan")
            if not visual_plan_context(document):
                document["visual_plan"] = deepcopy(part.get("visual_plan"))
            baseline = deepcopy(part)
            progress(f"Finding footage · group {index + 1} of {len(groups)} · {len(group)} shots")
            selected = fill_timeline(part, config, db, progress, part_job["id"], target_ids,
                                    timing_scope=timing_scope, sequence_context=context,
                                    previous_sources=previous + _placed(document), **inspection_options)
            if "draft" not in stages:
                stages.append("draft")
            validate_result(timing_scope, selected)
            for before, after in zip(baseline["music_timeline"]["slots"], selected["music_timeline"]["slots"]):
                before["start"], before["end"] = after["start"], after["end"]
            _guard_edit(baseline, selected, "fill", set(target_ids))
            _merge_fixed(document, selected, left, right)
            plans.append({"part": index + 1, "passage": passage, "plan": deepcopy(part.get("direction_plan"))})
            diagnostics.append({"part": index + 1, "passage": passage, **deepcopy(selected["analysis"].get("draft", {}))})
            left = right + 1
        document["clips"] = deepcopy(retained) + document["clips"]
        if review_contexts is not None:
            document, inspection = _inspect_generation(document, config, db, progress, job["id"], review_contexts,
                                                        original=original, mode=mode)
            stages.append("inspect")
        final_slots = document["music_timeline"]["slots"]
        timing_plan["final_timing"] = timing_diagnostics(final_slots)
        timing_plan["final_end_frames"] = [round((slot["end"] - document["passage"]["start"]) * document["fps"]) for slot in final_slots]
        document["direction_plan"] = {"contract": LONG_GENERATION_CONTRACT, "track_id": document["track"]["id"],
            "passage": deepcopy(document["passage"]), "parts": plans, "timing_plan": timing_plan}
    else:
        from pipeline.lab.long_audio import analysis_for_passage

        ensure_timeline(document)
        document["music_timeline"]["provisional_timing"] = None
        _, targets = validate_generate_request(document, job.get("snapshot", {}).get("generate"), job.get("snapshot", {}).get("slot_ids"))
        if mode == "fill" and getattr(config.lab, "harness", "v1") == "v2":
            from pipeline.lab.generation import harness_fill
            return harness_fill(original, document, config, db, progress, job, [slot["id"] for slot in targets], stages)
        windows = _fixed_windows(document, targets)
        for index, (left, right, target_ids) in enumerate(windows):
            view_slots = deepcopy(document["music_timeline"]["slots"][left:right + 1])
            passage = {"start": view_slots[0]["start"], "end": view_slots[-1]["end"]}
            progress(f"Filling part {index + 1} of {len(windows)} · {len(target_ids)} requested shots")
            context = _context(document, passage, index, len(windows), db)
            part = _part_document(document, passage, analysis_for_passage(document["analysis"], passage))
            for slot in view_slots:
                slot["section_index"] = section_for(slot["start"], slot["end"], part["analysis"]["segments"])
            placed_ids = {slot["clip_id"] for slot in view_slots if slot.get("clip_id")}
            part["clips"] = [deepcopy(clip) for clip in document["clips"] if clip["id"] in placed_ids]
            part["music_timeline"] = {"track_id": part["track"]["id"], "passage": passage, "slots": view_slots, "provisional_timing": None}
            part_job = _part_job(job, part, index, context, slot_ids=target_ids)
            plan_ids = [slot["id"] for slot in view_slots if slot["id"] in target_ids and (mode == "improve"
                        or _needs_plan(slot, refresh_ai=document.get("editor_direction") is not None))]
            if plan_ids:
                plan_job = deepcopy(part_job)
                plan_job["snapshot"]["slot_ids"] = plan_ids
                part = run_direction_job(plan_job, config, db, progress)
                if "plan" not in stages:
                    stages.append("plan")
            selected = fill_timeline(part, config, db, progress, part_job["id"], target_ids,
                                     previous_sources=_placed(document), sequence_context=context, **inspection_options)
            if "draft" not in stages:
                stages.append("draft")
            _merge_fixed(document, selected, left, right)
            draft = selected["analysis"].get("draft", {})
            diagnostics.append({"part": index + 1, "passage": passage, **draft})
        if review_contexts is not None:
            document, inspection = _inspect_generation(document, config, db, progress, job["id"], review_contexts,
                                                        original=original, mode=mode)
            stages.append("inspect")
        _guard_edit(original, document, mode, {slot["id"] for slot in targets})
    if len(document["clips"]) > MAX_SAVED_CLIPS:
        raise ValueError(f"The completed edit would exceed {MAX_SAVED_CLIPS} saved clips. Clear unused saved clips and try again; the saved edit is unchanged")
    progress("Checking the complete edit before saving one revision")
    document = ProjectDocument.model_validate(document).model_dump(mode="json")
    remaining = [slot for slot in document["music_timeline"]["slots"] if not slot.get("clip_id")]
    requested_ids = {slot["id"] for slot in document["music_timeline"]["slots"]} if whole else {slot["id"] for slot in targets}
    failed = [{"slot_id": slot["id"], "error": slot.get("search_error") or "No fitting scene found"}
              for slot in document["music_timeline"]["slots"] if slot["id"] in requested_ids and (slot.get("search_error") or not slot.get("clip_id"))]
    selected_count = sum(bool(slot.get("clip_id")) and not slot.get("search_error")
                         for slot in document["music_timeline"]["slots"] if slot["id"] in requested_ids)
    candidate_count = sum(item.get("candidate_count", 0) for item in diagnostics)
    document["analysis"]["draft"] = {"contract": LONG_GENERATION_CONTRACT, "parts": diagnostics,
        "candidate_count": candidate_count, "selected_count": selected_count,
        **({"footage_inspection": inspection} if inspection is not None else {}),
        "unfilled_slot_ids": [slot["id"] for slot in remaining]}
    message = f"Edit ready with {len(remaining)} unfilled shots." if remaining else "Your edit is ready to play."
    if mode == "improve" and failed:
        message = "No fitting replacement found; your original shot is kept." if targets[0].get("clip_id") else "No fitting scene found; the selected shot remains empty."
    public_parts = [{key: value for key, value in part.items() if key not in {"candidate_ledger", "reasons"}} for part in diagnostics]
    return document, {"message": message, "contract": LONG_GENERATION_CONTRACT, "generation_mode": mode,
                      "stages": stages, "timing_mode": "source-aware" if whole else "fixed",
                      "timing_adjustments": [item for part in diagnostics for item in part.get("timing_adjustments", [])],
                      "parts": public_parts, "part_count": len(diagnostics),
                      "requested_slot_ids": [slot["id"] for slot in document["music_timeline"]["slots"] if slot["id"] in requested_ids],
                      "timing_plan": (document.get("direction_plan") or {}).get("timing_plan"),
                      "selected_count": selected_count, "candidate_count": candidate_count,
                      **({"footage_inspection": inspection} if inspection is not None else {}),
                      "remaining_gaps": len(remaining), "failed_slots": failed}
