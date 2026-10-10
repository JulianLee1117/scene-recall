"""Fill or replace explicit music slots from a finite retrieved source catalog."""
from __future__ import annotations

import copy
import json
import math
import uuid

from pipeline.lab.models import ClipSelection, MusicMatchEvidence
from pipeline.lab.limits import MAX_SAVED_CLIPS
from pipeline.lab.search_plan import execute_search, offered_references, recipe_key, resolve_search
from pipeline.lab.scene_selection import response_contract, selection_schema, selection_choices, selection_manifest
from pipeline.lab.selection_prompt import build_selection_payload, build_selection_prompt
from pipeline.lab.source_timing import ASSEMBLY_CONTRACT, timing_offer, validate_scope, validate_result
from pipeline.lab.timeline import TIMELINE_CONTRACT, direction_for, draft_targets, ensure_timeline
from pipeline.search.capabilities import search_capabilities


MAX_DIRECTION_SEARCHES = 32


def _candidate(row):
    start, end = row.get("t_start"), row.get("t_end")
    if not all(not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value) for value in (start, end)) or start < 0 or end <= start:
        return None
    if not row.get("unit_id") or not row.get("film_id"):
        return None
    # camera_motion is the annotator's stills guess; the measured movement replaces it, or drops it, once packed.
    metadata = {key: str(row[key])[:300] for key in ("framing", "setting", "time_of_day", "energy", "camera_motion") if row.get(key)}
    if isinstance(row.get("people_count"), int) and not isinstance(row["people_count"], bool) and row["people_count"] >= 0:
        metadata["people_count"] = row["people_count"]
    for key in ("mood", "palette", "subjects"):
        values = row.get(key)
        if isinstance(values, str):
            try:
                values = json.loads(values)
            except ValueError:
                values = []
        if isinstance(values, list):
            metadata[key] = [value[:200] for value in values[:12] if isinstance(value, str)]
    dialogue = row.get("dialogue")
    if isinstance(dialogue, str):
        try:
            dialogue = json.loads(dialogue)
        except ValueError:
            dialogue = []
    if isinstance(dialogue, list):
        metadata["dialogue"] = [line[:300] for line in dialogue[:8] if isinstance(line, str)]
    if isinstance(row.get("on_screen_text"), str) and row["on_screen_text"]:
        metadata["on_screen_text"] = row["on_screen_text"][:1500]
    return {"film_id": str(row["film_id"]), "unit_id": str(row["unit_id"]),
            "t_start": start, "t_end": end, "caption": str(row.get("caption", ""))[:4000],
            "film_title": str(row.get("film_title") or row["film_id"])[:300],
            "parent_shot_id": row.get("parent_shot_id"), "metadata": metadata}


def _match_evidence(row, rank):
    """Query-specific evidence never becomes canonical source metadata."""
    def number(value):
        return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)
    index, timestamp = row.get("matched_frame_index"), row.get("matched_frame_timestamp")
    matches = []
    for match in (row.get("matches") or [])[:3]:
        if not isinstance(match, dict):
            continue
        kept = {key: match[key] for key in ("clause_id", "facet") if isinstance(match.get(key), str)}
        if number(match.get("rank")):
            kept["rank"] = match["rank"]
        evidence = match.get("evidence")
        if isinstance(evidence, dict):
            kept["evidence"] = {key: value[:1500] if isinstance(value, str) else value
                                for key, value in evidence.items() if key in {"type", "view", "text", "frame_index", "timestamp"}
                                and (isinstance(value, str) or number(value))}
        matches.append(kept)
    channels = {}
    for name, channel in ((row.get("debug") or {}).get("channels") or {}).items():
        if name in {"img", "txt", "lex", "spatial"} and isinstance(channel, dict):
            channels[name] = {key: value for key, value in channel.items() if key in {"rank", "score", "distance"} and number(value)}
    return MusicMatchEvidence(rank=rank, matched_text=str(row.get("matched_text") or "")[:1500],
                              matched_text_view=str(row["matched_text_view"])[:100] if row.get("matched_text_view") else None,
                              matched_frame_index=index if isinstance(index, int) and not isinstance(index, bool) and index >= 0 else None,
                              matched_frame_timestamp=timestamp if number(timestamp) and timestamp >= 0 else None,
                              matches=matches, channels=channels).model_dump(mode="json")


_EVIDENCE_COLUMNS = ["unit_id", "scene_id", "characters", "action", "peak_time", "emotion", "audio_cue", "camera",
                     "camera_reliability", "camera_segments", "subject", "motion_energy", "fame_library", "craft",
                     "iconic", "gem", "famous_line"]
_CAMERA_WORDS = {"static": "static", "pan_left": "pan left", "pan_right": "pan right", "tilt_up": "tilt up",
                 "tilt_down": "tilt down", "push_in": "push in", "pull_out": "pull out", "roll": "roll",
                 "diagonal": "diagonal move", "handheld": "handheld"}


def _attach_evidence(sources, db):
    """Evidence v2 for offered sources: what happens and when it peaks, measured camera and subject, fame/craft.

    Stills-guessed camera labels are replaced by measured movement where it is
    reliable and dropped otherwise; sources without evidence keep annotations.
    """
    from pipeline.evidence.tables import SCENES, SHOT_EVIDENCE
    from pipeline.index.writer import table_names
    from pipeline.search.retrieve import _any_of
    try:
        names = table_names(db)
        if SHOT_EVIDENCE not in names:
            return
        identities = tuple(sources)
        rows = {row["unit_id"]: row for row in db.open_table(SHOT_EVIDENCE).search().where(_any_of("unit_id", identities))
                .select(_EVIDENCE_COLUMNS).limit(len(identities) + 1).to_list()}
        scene_ids = tuple({row["scene_id"] for row in rows.values() if row.get("scene_id")})
        scenes = ({row["scene_id"]: row for row in db.open_table(SCENES).search().where(_any_of("scene_id", scene_ids))
                   .select(["scene_id", "title", "summary"]).limit(len(scene_ids) + 1).to_list()}
                  if scene_ids and SCENES in names else {})
    except Exception:  # noqa: BLE001 - optional evidence never blocks selection
        return
    for identity, source in sources.items():
        row = rows.get(identity)
        if row is None:
            continue
        packed = {}
        if row.get("action"):
            packed["action"] = row["action"]
        if row.get("characters"):
            packed["characters"] = json.loads(row["characters"])[:8]
        for key in ("emotion", "audio_cue", "famous_line"):
            if row.get(key):
                packed[key] = row[key]
        if row.get("peak_time") is not None:
            packed["peak_time"] = round(float(row["peak_time"]), 2)
        scene = scenes.get(row.get("scene_id") or "")
        if scene:
            packed["scene"] = {"title": scene.get("title") or "", "summary": scene.get("summary") or ""}
        reliable = float(row.get("camera_reliability") or 0.0) >= 0.5
        if row.get("camera") and reliable:
            packed["camera"] = {"movement": _CAMERA_WORDS.get(row["camera"], row["camera"]),
                                "segments": json.loads(row.get("camera_segments") or "[]")[:6]}
        if row.get("subject"):
            subject = json.loads(row["subject"])
            packed["subject"] = {key: subject.get(key) for key in ("class", "center", "size", "direction")}
        if row.get("motion_energy") is not None:
            packed["subject_motion"] = round(float(row["motion_energy"]), 4)
        for key in ("fame_library", "craft"):
            if row.get(key) is not None:
                packed["fame" if key == "fame_library" else key] = round(float(row[key]), 2)
        packed["iconic"], packed["gem"] = bool(row.get("iconic")), bool(row.get("gem"))
        source["evidence"] = packed
        metadata = source.get("metadata")
        if isinstance(metadata, dict):
            if "camera" in packed:
                metadata["camera_motion"] = packed["camera"]["movement"]
            else:
                metadata.pop("camera_motion", None)


def _hydrate_metadata(sources, db):
    """Public results omit annotations; hydrate only the bounded offered IDs."""
    from pipeline.index.writer import table_names
    from pipeline.search.retrieve import _any_of
    try:
        if "units" not in table_names(db):
            return
        table = db.open_table("units")
        columns = {"unit_id", "film_id", "t_start", "t_end", "caption", "parent_shot_id", "framing", "setting",
                   "time_of_day", "energy", "camera_motion", "people_count", "mood", "palette", "subjects", "dialogue", "on_screen_text"}
        columns &= set(table.schema.names)
    except (AttributeError, TypeError):
        return  # Optional metadata cannot be claimed when no database is readable.
    identities = list(sources)
    for offset in range(0, len(identities), 128):
        batch = identities[offset:offset + 128]
        rows = table.search().where(_any_of("unit_id", tuple(batch))).select(sorted(columns)).limit(len(batch) + 1).to_list()
        matched = {}
        for row in rows:
            identity = row.get("unit_id")
            if identity not in batch or identity in matched:
                raise ValueError("Indexed metadata identities changed during retrieval; retry from the current index")
            current, expected = _candidate(row), sources[identity]
            if current is None or any(current[key] != expected[key] for key in ("film_id", "t_start", "t_end")):
                raise ValueError("Indexed source ranges changed during metadata lookup; retry from the current index")
            expected.update(metadata=current["metadata"], parent_shot_id=current["parent_shot_id"], caption=current["caption"])
            matched[identity] = True
        if set(matched) != set(batch):
            raise ValueError("A retrieved source no longer exists in the index; refresh before selecting scenes")


def _slot_evidence(candidate, duration, evidence):
    result = copy.deepcopy(evidence)
    moment = result.get("matched_frame_timestamp")
    if moment is None:
        moment = next((match["evidence"].get("timestamp") for match in result["matches"]
                       if isinstance(match.get("evidence"), dict) and match["evidence"].get("type") == "frame"
                       and isinstance(match["evidence"].get("timestamp"), (int, float))), None)
    if isinstance(moment, (int, float)) and math.isfinite(moment) and candidate["t_start"] <= moment <= candidate["t_end"]:
        result["suggested_source_start"] = max(candidate["t_start"], min(candidate["t_end"] - duration, moment - duration / 2))
    return result


def _alternative(candidate, duration, evidence=None):
    moment = (evidence or {}).get("matched_frame_timestamp")
    if not isinstance(moment, (int, float)) or not math.isfinite(moment):
        moment = (candidate["t_start"] + candidate["t_end"]) / 2
    proposed = (evidence or {}).get("suggested_source_start")
    if not isinstance(proposed, (int, float)) or not math.isfinite(proposed):
        proposed = moment - duration / 2
    start = max(candidate["t_start"], min(candidate["t_end"] - duration, proposed))
    clip = ClipSelection(id=str(uuid.uuid4()), film_id=candidate["film_id"], unit_id=candidate["unit_id"],
                         title=candidate["caption"][:200], source_start=start, source_end=start + duration)
    return {"clip": clip.model_dump(mode="json"), "film_title": candidate["film_title"], "reason": None, "search_evidence": evidence}


def _overlapping_source(candidate, start, end, clip):
    return (candidate["film_id"] == clip.get("film_id") and
            min(end, clip["source_end"]) - max(start, clip["source_start"]) > 1e-6)


def _previous_source(candidate, previous_sources):
    return any((clip.get("unit_id") and clip["unit_id"] == candidate["unit_id"]) or
               _overlapping_source(candidate, candidate["t_start"], candidate["t_end"], clip)
               for clip in previous_sources)


def _repetition_reason(candidate, choice, occupied):
    for clip, label in occupied:
        same_unit = clip.get("unit_id") and clip["unit_id"] == candidate["unit_id"]
        if same_unit or _overlapping_source(candidate, choice["source_start"],
                                            choice["source_start"] + choice["duration"], clip):
            return f"The proposed footage repeats {label}. Choose a different scene; recurring motifs should use different footage."
    return None


def fill_timeline(document, config, db, progress, job_id, slot_ids=None, *, suggest_only=False, timing_scope=None, previous_sources=None, sequence_context=None, review_contexts=None):
    from pipeline.lab import music
    from pipeline.search.recipe import SemanticTextProfileUnavailable

    inspection = bool(config.lab.footage_inspection and review_contexts is not None and not suggest_only)

    if suggest_only and (not isinstance(slot_ids, list) or len(slot_ids) != 1):
        raise ValueError("Finding alternatives requires exactly one selected music slot")
    # Regeneration exclusions are private job input. Human searches can still
    # deliberately find and place footage already used in any version.
    previous_sources = [] if suggest_only else list(previous_sources or [])
    timeline = ensure_timeline(document)
    targets = draft_targets(document, slot_ids)
    if timing_scope:
        validate_scope(document, timing_scope)
        if suggest_only or [slot["id"] for slot in targets] != [slot["id"] for slot in timeline["slots"]]:
            raise ValueError("Source-aware first timing must cover all untouched musical intentions")
    if not targets:
        progress("Every music slot is already filled")
        return document
    retained = {clip["id"] for clip in document["clips"]}
    added_count = sum(slot.get("clip_id") not in retained for slot in targets)
    if not suggest_only and len(retained) + added_count > MAX_SAVED_CLIPS:
        raise ValueError(f"These slots would exceed the project's {MAX_SAVED_CLIPS} saved clips, including its bin. Choose a smaller selection or remove unused saved clips before finding more scenes")
    interpretation = document.get("analysis") or {}
    if not suggest_only and not interpretation:
        raise ValueError("Analyze this music passage before filling shots automatically")
    segments = interpretation.get("segments", [])
    sources, searches, errors, film_titles, evidence_by_search = {}, {}, {}, {}, {}
    directions = {slot["id"]: direction_for(slot, interpretation) for slot in targets}
    capabilities = search_capabilities(config, db)
    needs_references = any(clause["kind"] == "source" for direction in directions.values()
                           for clause in (direction.get("search_plan") or {}).get("clauses", []))
    # Plain descriptions and text-only recipes cannot consume indexed anchors.
    # Source recipes still rebuild their offers to reject changed source authority.
    references = (offered_references(document, db, capabilities, exclude_slot_ids=[slot["id"] for slot in targets])
                  if needs_references else [])
    timing = {slot["id"]: timing_offer(timing_scope, index) for index, slot in enumerate(timeline["slots"])} if timing_scope else {}
    resolved = {slot["id"]: resolve_search(directions[slot["id"]], references, capabilities,
                                          timing[slot["id"]]["min_duration"] if timing_scope else slot["end"] - slot["start"]) for slot in targets}
    recipes = {recipe_key(value): value for value in resolved.values()}
    keys = list(recipes)
    if len(keys) > MAX_DIRECTION_SEARCHES:
        raise ValueError("This request needs more than 32 distinct scene searches. Choose a smaller selection of slots to fill or replace")
    if sum(len(recipe["clauses"]) for recipe in recipes.values()) > 96:
        raise ValueError("This request exceeds 96 recipe clauses; choose a smaller selection")
    for index, key in enumerate(keys):
        progress(f"Searching musical moments · {index + 1} of {len(keys)}")
        try:
            rows = execute_search(recipes[key], document, config, db)
        except SemanticTextProfileUnavailable:
            rows = []
            errors[key] = "This search type needs the active semantic index. Choose a broad description or repair that index."
        ids, query_evidence = [], {}
        for rank, row in enumerate(rows[:48], start=1):
            candidate = _candidate(row)
            if candidate:
                film_id = candidate["film_id"]
                if film_id not in film_titles:
                    # Human labels are presentation context, not source authority.
                    # Range/source availability is independently checked by the worker.
                    try:
                        from pipeline.lab.media import resolve_film
                        film_titles[film_id] = str(resolve_film(db, film_id).get("title") or film_id)[:300]
                    except (AttributeError, ValueError, KeyError):
                        film_titles[film_id] = candidate["film_title"]
                candidate["film_title"] = film_titles[film_id]
                identity = candidate["unit_id"]
                if identity in sources and any(sources[identity][field] != candidate[field] for field in ("film_id", "t_start", "t_end")):
                    raise ValueError("Indexed source ranges changed during retrieval; retry from the current index")
                sources.setdefault(identity, candidate)
                query_evidence.setdefault(identity, _match_evidence(row, rank))
                if identity not in ids:
                    ids.append(identity)
        searches[key], evidence_by_search[key] = ids, query_evidence

    active, offered, ledger, previous_excluded = [], {}, [], set()
    slot_index = {slot["id"]: index for index, slot in enumerate(timeline["slots"])}
    for slot in targets:
        segment = segments[slot["section_index"]] if not suggest_only else None
        direction = directions[slot["id"]]
        key = recipe_key(resolved[slot["id"]])
        slot["resolved_search"] = resolved[slot["id"]]
        duration = slot["end"] - slot["start"]
        minimum = timing[slot["id"]]["min_duration"] if timing_scope else duration
        fitting = [identity for identity in searches[key]
                   if sources[identity]["t_end"] - sources[identity]["t_start"] >= minimum - 0.000001]
        excluded = {identity for identity in fitting if _previous_source(sources[identity], previous_sources)}
        previous_excluded.update(excluded)
        ids = [identity for identity in fitting if identity not in excluded][:24]
        ledger.append({"slot_id": slot["id"], "query": direction["query"], "nominal_duration": duration,
                       "minimum_duration": minimum, "retrieved_count": len(searches[key]), "offered_count": len(ids),
                       "previous_excluded_count": len(excluded), "previous_excluded_unit_ids": sorted(excluded),
                       "too_short_count": sum(sources[identity]["t_end"] - sources[identity]["t_start"] < minimum - 1e-6 for identity in searches[key]),
                       "candidates": [{"unit_id": identity, "rank": evidence_by_search[key][identity]["rank"],
                                       "duration": sources[identity]["t_end"] - sources[identity]["t_start"]} for identity in ids],
                       "decision": "offered" if ids else "no-fitting-candidates", "selected_unit_id": None})
        if not ids:
            slot["search_error"] = errors.get(key) or (
                "Only footage from the previous edit fits this search. Change the search, or search manually to reuse it."
                if excluded else
                f"No scene fits this {'flexible intention' if timing_scope else f'{duration:.2f}s slot'}. Edit its search or choose a different scene.")
            slot["alternatives"] = []
            if not timing_scope:
                continue
        else:
            slot["search_error"] = None
        active.append({"slot": slot_index[slot["id"]], "id": slot["id"], "start": slot["start"],
                       "duration": duration, "locked": None, "candidate_ids": ids,
                       "search_plan": resolved[slot["id"]], "candidate_evidence": {identity: _slot_evidence(sources[identity], min(duration, sources[identity]["t_end"] - sources[identity]["t_start"]), evidence_by_search[key][identity]) for identity in ids},
                       "section_index": slot["section_index"], "feeling": segment["feeling"] if segment else None, "direction": direction})
        if timing_scope:
            active[-1]["timing"] = timing[slot["id"]]
        offered.update({identity: sources[identity] for identity in ids})
    diagnostic = {"selection_contract": ASSEMBLY_CONTRACT if timing_scope else "grounded-slot-selection-with-abstention-v1",
                  "response_contract": response_contract(timing_scope, inspection=inspection),
                  "timing_mode": "source-aware" if timing_scope else "fixed", "candidate_ledger": ledger,
                  "previous_source_count": len(previous_sources), "previous_excluded_count": len(previous_excluded),
                  "repetition_count": 0,
                  "candidate_count": len(offered), "requested_slot_ids": [slot["id"] for slot in targets]}
    if previous_excluded:
        progress(f"Excluded {len(previous_excluded)} previously used source shots from regeneration")
    if not offered:
        if not suggest_only:
            document["analysis"] = {**interpretation, "draft": {**diagnostic, "selected_count": 0,
                "unfilled_slot_ids": [slot["id"] for slot in targets], "abstained_slot_ids": []}}
        progress("No fitting scenes found; your selections and gap positions are preserved")
        return document
    if suggest_only:
        # Human-directed search returns ranked source windows without asking a
        # model to rewrite the request or choose footage on the user's behalf.
        for offer in active:
            slot = timeline["slots"][offer["slot"]]
            slot["alternatives"] = [_alternative(sources[identity], offer["duration"], offer["candidate_evidence"][identity])
                                    for identity in offer["candidate_ids"][:6]]
        progress("Scene options ready; choose a scene to place it")
        return document
    _hydrate_metadata(offered, db)
    _attach_evidence(offered, db)

    clips_by_id = {clip["id"]: clip for clip in document["clips"]}
    from pipeline.lab.direction_planner import _source_context
    source_context = {identity: _source_context(clips_by_id[identity], db)
                      for identity in {slot["clip_id"] for slot in timeline["slots"] if slot.get("clip_id")}}
    requested_ids = {offer["id"] for offer in active}
    context = [{"slot": index, "id": slot["id"], "start": slot["start"], "end": slot["end"],
                "section_index": slot["section_index"], "current_clip": clips_by_id.get(slot.get("clip_id")),
                "selected_source": source_context.get(slot.get("clip_id")),
                "direction": direction_for(slot, interpretation),
                "reason": slot.get("reason"), "requested": slot["id"] in requested_ids}
               for index, slot in enumerate(timeline["slots"])]
    payload = build_selection_payload(document, context, active, offered, capabilities,
                                     timing_scope, diagnostic["selection_contract"])
    if inspection:
        payload.update(footage_inspection=True, response_contract=response_contract(timing_scope, inspection=True))
    if sequence_context:
        payload["sequence_context"] = copy.deepcopy(sequence_context)
    if getattr(config.lab, "context_profile", None):
        from pipeline.context.editor import attach_context
        progress("Reading prepared source context")
        payload = attach_context(payload, config, db, sources=offered)
    prompt = build_selection_prompt(payload)
    schema = selection_schema(active, offered, timing_scope, inspection=inspection)
    manifest = selection_manifest(active, offered, timing_scope, inspection=inspection)
    manifest.update(job_id=job_id, input_hash=music.digest(payload),
                    prompt_hash=music.digest(prompt), schema_hash=music.digest(schema))
    if "source_context" in payload:
        # Freeze the exact derivations supplied to this selection. Later profile
        # publication cannot change the provenance of an existing saved edit.
        frozen_context = {"payload": payload, "schema": schema, "offers": active,
                          "sources": offered, "timing_scope": timing_scope,
                          "document": document}
        context_path = config.paths.assets_dir / "lab" / "requests" / f"{job_id}-source-context-input.json"
        music.write_json(context_path, frozen_context)
        manifest["source_context"] = {"contract": payload["source_context"]["contract"],
            "profile_id": payload["source_context"]["profile_id"],
            "artifact_ids": payload["source_context"]["artifacts"],
            "input_receipt": str(context_path), "input_receipt_hash": music.digest(frozen_context)}
    music.write_json(config.paths.assets_dir / "lab" / "requests" / f"{job_id}-offers.json", manifest)
    progress(f"Reviewing {len(offered)} source candidates across {len(active)} musical intentions")
    progress("Choosing footage and nearby cuts together" if timing_scope else "Choosing scenes with the whole sequence in view")
    choices = music._hosted_json(config, prompt, schema,
                                receipt_path=config.paths.assets_dir / "lab" / "requests" / f"{job_id}-draft.json",
                                progress=progress, operation=response_contract(timing_scope, inspection=inspection))
    progress("Checking selected scenes and alternatives")
    selected = selection_choices(choices, active, sources, timing_scope, inspection=inspection)
    if timing_scope:
        adjustments = [{"slot_id": offer["id"], "preferred_end_frame": choice["preferred_end_frame"],
                        "end_frame": choice["end_frame"]}
                       for offer, choice in zip(active, selected)
                       if choice["preferred_end_frame"] != choice["end_frame"]]
        diagnostic["timing_adjustments"] = adjustments
        if adjustments:
            progress(f"Fitted {len(adjustments)} nearby cuts to the selected source lengths; source choices are unchanged")
    # Reserve requested originals too: any replacement can abstain and retain
    # its old clip, including a target processed after the current choice.
    occupied = [(clips_by_id[slot["clip_id"]], f"current clip {index + 1}")
                for index, slot in enumerate(timeline["slots"]) if slot.get("clip_id")]
    reasons, abstained = {}, []
    previous_reasons = (interpretation.get("draft") or {}).get("reasons", {})
    ledger_by_id = {row["slot_id"]: row for row in ledger}
    for offer, choice in zip(active, selected):
        slot = timeline["slots"][offer["slot"]]
        decision = ledger_by_id[slot["id"]]
        if inspection:
            decision["inspection_hint"] = choice["inspection_hint"]
        if choice["candidate_id"]:
            repetition = _repetition_reason(sources[choice["candidate_id"]], choice, occupied)
            if repetition:
                diagnostic["repetition_count"] += 1
                decision.update(repetition_reason=repetition, proposed_unit_id=choice["candidate_id"])
                choice = {**choice, "candidate_id": None, "source_start": None, "reason": repetition}
        if timing_scope:
            slot["start"], slot["end"] = choice["start"], choice["start"] + choice["duration"]
            slot["resolved_search"]["min_duration"] = choice["duration"]
            decision.update(preferred_end_frame=choice["preferred_end_frame"],
                            end_frame=choice["end_frame"], source_position=choice["source_position"])
        duration = choice["duration"]
        evidence = {identity: _slot_evidence(sources[identity], duration, evidence_by_search[recipe_key(resolved[slot["id"]])][identity])
                    for identity in offer["candidate_ids"] if sources[identity]["t_end"] - sources[identity]["t_start"] >= duration - 1e-6}
        decision.update(decision="selected" if choice["candidate_id"] else "abstained", reason=choice["reason"],
                        selected_unit_id=choice["candidate_id"], duration=duration)
        if choice["candidate_id"] is None:
            abstained.append(slot["id"])
            slot["search_error"] = choice["reason"]
            # A requested replacement preserves the existing source and proof.
            if not slot.get("clip_id"):
                slot["reason"], slot["search_evidence"] = None, None
            slot["alternatives"] = [_alternative(sources[identity], duration, evidence[identity]) for identity in list(evidence)[:6]]
            continue
        candidate = sources[choice["candidate_id"]]
        clip = ClipSelection(id=str(uuid.uuid4()), film_id=candidate["film_id"], unit_id=candidate["unit_id"],
                             title=candidate["caption"][:200], source_start=choice["source_start"],
                             source_end=choice["source_start"] + duration).model_dump(mode="json")
        reasons[clip["id"]] = choice["reason"]
        old_id = slot.get("clip_id")
        old_index = next((index for index, value in enumerate(document["clips"]) if value["id"] == old_id), None)
        clip = ClipSelection.model_validate(clip).model_dump(mode="json")
        occupied.append((clip, f"clip {offer['slot'] + 1}"))
        if old_index is None:
            document["clips"].append(clip)
        else:
            document["clips"][old_index] = clip
        slot["clip_id"], slot["reason"] = clip["id"], reasons[clip["id"]]
        slot["search_evidence"] = evidence[clip["unit_id"]]
        slot["alternatives"] = [{"clip": copy.deepcopy(clip), "film_title": sources[clip["unit_id"]]["film_title"],
                                  "reason": reasons[clip["id"]], "search_evidence": slot["search_evidence"]}]
        for identity in offer["candidate_ids"]:
            if identity != clip["unit_id"] and identity in evidence:
                slot["alternatives"].append(_alternative(sources[identity], duration, evidence[identity]))
            if len(slot["alternatives"]) == 6:
                break
    if timing_scope:
        validate_result(timing_scope, document)
    if inspection:
        review_contexts.append({"offers": copy.deepcopy(active), "sources": copy.deepcopy(offered),
                                "timing_scope": copy.deepcopy(timing_scope), "ledger": copy.deepcopy(ledger),
                                "previous_sources": copy.deepcopy(previous_sources)})
    if diagnostic["repetition_count"]:
        progress(f"Left {diagnostic['repetition_count']} repeated footage choices for review; alternatives are available")
    document["analysis"] = {**interpretation, "draft": {**diagnostic,
        "prompt_version": config.lab.planner_prompt_version, "timeline_contract": TIMELINE_CONTRACT,
        "model": config.lab.planner_model, "provider": config.lab.music_provider,
        "hosted_contract": music.PLANNER_CONTRACT,
        "settings": music.PLANNER_SETTINGS if config.lab.music_provider == "openai" else music.SETTINGS,
        "input_hash": music.digest(payload), "reasons": {identity: reason for identity, reason in {**previous_reasons, **reasons}.items()
                                                        if identity in {clip["id"] for clip in document["clips"]}},
        "queries": [{"facet": clause["facet"], "query": clause["text"]} for recipe in recipes.values() for clause in recipe["clauses"]],
        "selected_count": len(reasons), "abstained_slot_ids": abstained,
        "timing_scope": timing_scope,
        "unfilled_slot_ids": [slot["id"] for slot in timeline["slots"] if not slot.get("clip_id")],
    }}
    progress(f"Selected {len(reasons)} scenes; {len(abstained)} intentions left for review" if abstained else f"Selected {len(reasons)} scenes")
    return document
