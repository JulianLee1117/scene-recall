"""Compact selector inputs retain evidence and bind aliases to their own footage."""
from copy import deepcopy
import json

import pytest

from pipeline.lab.music_evidence import PLANNING_GUIDANCE, music_evidence
from pipeline.lab.scene_selection import SELECTION_CONTRACT, TIMED_SELECTION_CONTRACT, selection_schema
from pipeline.lab.selection_prompt import build_selection_payload, build_selection_prompt, source_aliases


def _inputs():
    passage = {"start": 10., "end": 19.}
    provenance = {"track": "track", "passage": passage}
    meaning = {"vocal_status": "partly_understood", "summary": "A connection may be ending.",
               "themes": ["uncertain separation"], "uncertainty": "The final phrase is obscured.",
               "cues": [{"start": 11., "end": 13., "paraphrase": "The speaker asks the other person to remain.",
                         "confidence": "medium"}]}
    document = {"brief": "", "track": {"id": "track"}, "passage": passage,
                "planner_settings": {"pacing": "kinetic", "lyric_treatment": "metaphorical"},
                "analysis": {"provenance": provenance, "summary": "Bright accompaniment with uncertain words.",
                             "song_meaning": meaning,
                             "song_meaning_provenance": {**provenance, "source": "ai-heard-paraphrase"}},
                "song_context": {"track_id": "track", "notes": "No cheerful ending.",
                                 "lyrics": [{"id": "words", "start": 12., "end": 14., "text": "user words"}]},
                "visual_plan": {"arc": "A distance that does not close."},
                "rhythm": {"provenance": provenance, "beats": [11., 12., 13.], "downbeats": [12.]}}
    sources = {
        "unit-b": {"unit_id": "unit-b", "film_id": "film-one", "film_title": "First film",
                   "t_start": 40., "t_end": 49., "caption": "A person waits beside a closed door.",
                   "parent_shot_id": "long-take", "metadata": {"dialogue": ["Please stay."], "on_screen_text": "EXIT",
                   "framing": "wide", "camera_motion": "static", "mood": ["uncertain"], "future_annotation": {"fact": "kept"}}},
        "unit-a": {"unit_id": "unit-a", "film_id": "film-one", "film_title": "First film",
                   "t_start": 80., "t_end": 88., "caption": "Two people face away from each other.",
                   "parent_shot_id": "another-shot", "metadata": {"subjects": ["two people"]}},
    }
    direction = {"query": "A separation that remains unresolved", "search_facet": "scene",
                 "purpose": "Answer the first image without resolving the tension", "music_cue": "The held vocal phrase",
                 "timing_note": "Hold across the light percussion"}
    plan = {"clauses": [{"kind": "text", "facet": "scene", "text": "Two figures separated by a doorway", "reference_id": None}],
            "references": [], "unverified_requirements": ["No verified action completion"],
            "capability_version": "internal-version", "min_duration": 3.}
    offers = []
    for slot, identities in [(0, ["unit-b", "unit-a"]), (2, ["unit-a", "unit-b"])]:
        evidence = {identity: {"rank": index + 1, "matched_text": sources[identity]["caption"],
                              "matched_text_view": "caption", "matched_frame_index": None,
                              "matched_frame_timestamp": None, "suggested_source_start": None,
                              "matches": [{"clause_id": "clause-1", "facet": "scene", "rank": index + 1,
                                           "evidence": {"type": "text", "view": "caption", "text": sources[identity]["caption"]}}],
                              "channels": {"txt": {"rank": index + 1, "score": .8, "distance": .2}}}
                    for index, identity in enumerate(identities)}
        offers.append({"slot": slot, "id": f"slot-{slot}", "start": 10. + 3 * slot, "duration": 3., "locked": None,
                       "section_index": 0, "feeling": "Unresolved", "candidate_ids": identities,
                       "candidate_evidence": evidence, "search_plan": deepcopy(plan),
                       "direction": {**deepcopy(direction), "search_plan": deepcopy(plan)}})
    clip = {"id": "existing", "unit_id": "old-unit", "film_id": "film-two", "source_start": 20., "source_end": 23.,
            "title": "A user title, not indexed evidence", "locked": True, "crop": {"x": .1, "y": 0., "width": .8, "height": 1.}}
    caption_evidence = {"text": "A person faces a closed window.", "source_start": 19., "source_end": 25.,
                        "scope": "Sparse indexed caption at the selected clip's beginning; not watched video"}
    timeline = [{"slot": i, "id": f"slot-{i}", "start": 10. + 3 * i, "end": 13. + 3 * i, "section_index": 0,
                 "requested": i != 1, "direction": deepcopy(direction), "reason": "A sustained response" if i == 1 else None,
                 "current_clip": deepcopy(clip) if i == 1 else None,
                 "selected_source": {**deepcopy(clip), "caption_evidence": caption_evidence} if i == 1 else None}
                for i in range(3)]
    capabilities = {"version": "internal-version", "facets": [
        {"facet": "scene", "label": "Scene", "text_available": True, "source_available": False,
         "evidence": "Sparse annotated image meaning, not verified action"}],
        "recipe": {"max_recipes_per_job": 32}, "availability_note": "Internal readiness detail",
        "source_scope": "Indexed reference frames", "unit_scope": "Shots or long-take subdivisions",
        "unsupported": ["verified movement", "exact dialogue timing"]}
    return document, timeline, offers, sources, capabilities, None, SELECTION_CONTRACT


def test_aliases_preserve_offer_order_across_shared_sources():
    inputs = _inputs()
    payload = build_selection_payload(*inputs)
    assert source_aliases(inputs[3]) == {"unit-b": "c0", "unit-a": "c1"}
    assert list(payload["sources"]) == ["c0", "c1"]
    assert [(row["slot"], row["key"]) for row in payload["slots"]] == [(0, "shot_0"), (2, "shot_2")]
    assert [[c["source"] for c in row["candidates"]] for row in payload["slots"]] == [["c0", "c1"], ["c1", "c0"]]
    assert all("index" not in candidate for slot in payload["slots"] for candidate in slot["candidates"])
    assert payload["response_contract"] == "scoped-scene-choices-v3"
    for slot in payload["slots"]:
        assert "candidate_ids" not in slot and "candidate_evidence" not in slot


def test_all_source_annotations_and_legal_ranges_survive_without_internal_ids():
    inputs = _inputs()
    payload = build_selection_payload(*inputs)
    for identity, alias in source_aliases(inputs[3]).items():
        original, projected = inputs[3][identity], payload["sources"][alias]
        for key in ("t_start", "t_end", "caption", "parent_shot_id", "metadata"):
            assert projected[key] == original[key]
        assert "unit_id" not in projected and "film_id" not in projected and "film_title" not in projected
    assert payload["sources"]["c0"]["film"] == payload["sources"]["c1"]["film"] == "f0"
    assert payload["films"]["f0"] == {"title": "First film"}


def test_only_exact_text_duplicates_are_references_and_distinct_match_evidence_survives():
    inputs = _inputs()
    evidence = inputs[2][1]["candidate_evidence"]["unit-a"]
    evidence.update(matched_text="Do not leave yet.", matched_text_view="dialogue", matched_frame_index=2,
                    matched_frame_timestamp=84.5, suggested_source_start=83.)
    evidence["matches"] = [
        {"clause_id": "clause-1", "facet": "words", "rank": 4,
         "evidence": {"type": "text", "view": "dialogue", "text": "Do not leave yet."}},
        {"clause_id": "clause-2", "facet": "look", "rank": 2,
         "evidence": {"type": "frame", "frame_index": 2, "timestamp": 84.5}},
        {"clause_id": "clause-3", "facet": "scene", "rank": 7,
         "evidence": {"type": "text", "view": "other", "text": "A separate caption variant."}},
    ]
    payload = build_selection_payload(*inputs)
    exact = payload["slots"][0]["candidates"][0]["evidence"]
    assert "matched_text" not in exact and exact["matched_text_ref"] == "source.caption"
    assert exact["matches"][0]["evidence"] == {"type": "text", "view": "caption", "text_ref": "source.caption"}
    assert "channels" not in exact and "matched_frame_timestamp" not in exact
    distinct = payload["slots"][1]["candidates"][0]["evidence"]
    for key in ("rank", "matched_text", "matched_text_view", "matched_frame_index", "matched_frame_timestamp", "suggested_source_start"):
        assert distinct[key] == evidence[key]
    assert distinct["matches"][0]["evidence"]["text_ref"] == "candidate.evidence.matched_text"
    assert distinct["matches"][1:] == evidence["matches"][1:]


def test_complete_song_evidence_and_neighbor_context_remain_scoped_and_unchanged():
    inputs = _inputs()
    payload = build_selection_payload(*inputs)
    assert payload["music_evidence"] == music_evidence(inputs[0])
    assert payload["music_evidence"]["song_meaning"]["cues"][0]["confidence"] == "medium"
    assert payload["music_evidence"]["song_context"]["notes"] == "No cheerful ending."
    retained = payload["timeline"][1]
    assert retained["direction"] == inputs[1][1]["direction"]
    assert retained["reason"] == "A sustained response"
    original = inputs[1][1]["current_clip"]
    for key in ("id", "source_start", "source_end", "title", "locked", "crop"):
        assert retained["current_clip"][key] == original[key]
    assert retained["current_clip"]["film"] == "f1"
    assert retained["selected_source"] == {"caption_evidence": inputs[1][1]["selected_source"]["caption_evidence"]}
    assert "direction" not in payload["timeline"][0]
    assert payload["slots"][0]["direction"]["music_cue"] == inputs[2][0]["direction"]["music_cue"]


def test_resolved_search_keeps_clause_reference_authority_and_evidence_limits():
    inputs = _inputs()
    plan = inputs[2][0]["search_plan"]
    plan["clauses"] = [{"kind": "source", "facet": "composition", "reference_id": "ref-one", "text": None}]
    plan["references"] = [{"reference_id": "ref-one", "clip_id": "existing", "film_id": "film-two", "unit_id": "old-unit",
                           "frame_index": 1, "timestamp": 22., "source_start": 20., "source_end": 23.}]
    payload = build_selection_payload(*inputs)
    resolved = payload["slots"][0]["search_plan"]
    assert resolved["clauses"] == plan["clauses"]
    assert resolved["unverified_requirements"] == plan["unverified_requirements"]
    assert resolved["references"] == [{"reference_id": "ref-one", "clip_id": "existing", "film": "f1",
                                        "frame_index": 1, "timestamp": 22., "source_start": 20., "source_end": 23.}]
    assert "capability_version" not in resolved and "min_duration" not in resolved
    assert "search_plan" not in payload["slots"][0]["direction"]
    assert payload["search_evidence_limits"]["unsupported"] == inputs[4]["unsupported"]
    assert payload["search_evidence_limits"]["facets"] == [{"facet": "scene", "evidence": inputs[4]["facets"][0]["evidence"]}]
    assert "search_capabilities" not in payload


@pytest.mark.parametrize("timed", [False, True])
def test_prompt_uses_one_json_block_source_bound_choices_and_exact_timing_scope(timed):
    inputs = list(_inputs())
    if timed:
        inputs[5] = {"contract": "timing", "passage": inputs[0]["passage"], "fps": 24., "total_frames": 216,
                     "track_id": "track", "nominal": [["slot-0", 10, 13]], "boundary_frames": [[0], [72], [216]],
                     "note": "Legal source duration is not observed action timing."}
        inputs[2][0]["timing"] = {"start_frames": [0], "end_frames": [60, 72, 84], "min_duration": 2.5, "max_duration": 3.5}
        inputs[2][1]["timing"] = {"start_frames": [132, 144, 156], "end_frames": [216], "min_duration": 2.5, "max_duration": 3.5}
    payload = build_selection_payload(*inputs)
    prompt = build_selection_prompt(payload)
    prose, encoded = prompt.split("\n", 1)
    assert json.loads(encoded) == payload
    assert prompt.count("\n") == 1 and PLANNING_GUIDANCE in prose
    for fragment in ("shot_{slot}", "return source null",
                     "DIFFERENT footage", "not aligned utterance timing", "actual neighbors"):
        assert fragment in prose
    assert "candidate_index" not in prose and "local candidate index" not in prose
    if timed:
        assert payload["response_contract"] == TIMED_SELECTION_CONTRACT
        assert "THAT slot's timing.end_frames" in prose
        assert "preferred_end_frame" in prose and "normalized position from 0 to 1, NOT a timestamp" in prose
        assert "minimizing total frame distance" in prose and "never substitutes sources" in prose
        assert payload["timing_scope"] == {key: value for key, value in inputs[5].items()
                                           if key not in {"track_id", "nominal", "boundary_frames"}}
        assert [row["timing"] for row in payload["slots"]] == [row["timing"] for row in inputs[2]]
    else:
        assert "boundaries are fixed" in prose and "Do not return preferred_end_frame" in prose
        assert "Choose the source alias and its own timestamp together" in prose


def test_prompt_aliases_and_schema_ranges_ignore_extra_or_reordered_retrieval_rows():
    inputs = list(_inputs())
    offered = inputs[3]
    inputs[3] = {"not-offered": {**offered["unit-a"], "unit_id": "not-offered", "caption": "This must not reach the model."},
                 "unit-a": offered["unit-a"], "unit-b": offered["unit-b"]}
    payload = build_selection_payload(*inputs)
    assert list(payload["sources"]) == ["c0", "c1"]
    assert payload["sources"]["c0"]["caption"] == offered["unit-b"]["caption"]
    assert payload["sources"]["c1"]["caption"] == offered["unit-a"]["caption"]
    assert "This must not reach the model." not in json.dumps(payload)
    schema = selection_schema(inputs[2], inputs[3])
    for slot in payload["slots"]:
        branches = schema["properties"]["choices"]["properties"][slot["key"]]["properties"]["source"]["anyOf"]
        assert [next(iter(branch["properties"])) for branch in branches[:-1]] == [candidate["source"] for candidate in slot["candidates"]]
        for branch in branches[:-1]:
            alias, limits = next(iter(branch["properties"].items()))
            source = payload["sources"][alias]
            assert limits["minimum"] == source["t_start"]
            assert limits["maximum"] == source["t_end"] - slot["duration"]


def test_projection_does_not_mutate_evidence_and_removes_substantial_exact_duplication():
    inputs = _inputs()
    before = deepcopy(inputs)
    payload = build_selection_payload(*inputs)
    assert inputs == before
    # The same full caption appears three times in each old candidate record.
    # Lengthening that actual evidence must not duplicate it in the new offers.
    caption = inputs[3]["unit-b"]["caption"] = "Full indexed caption. " * 100
    for offer in inputs[2]:
        evidence = offer["candidate_evidence"]["unit-b"]
        evidence["matched_text"] = caption
        evidence["matches"][0]["evidence"]["text"] = caption
    payload = build_selection_payload(*inputs)
    old = json.dumps({"candidates": inputs[3], "slots": inputs[2]}, separators=(",", ":"))
    new = json.dumps({"sources": payload["sources"], "slots": payload["slots"]}, separators=(",", ":"))
    assert new.count(caption) == 1 and old.count(caption) == 5
    assert len(new) < len(old) * .55
    payload["sources"]["c0"]["metadata"]["dialogue"].append("Mutation after projection")
    assert inputs[3]["unit-b"]["metadata"]["dialogue"] == ["Please stay."]


@pytest.mark.parametrize("nested_frame", [False, True])
def test_long_source_trim_is_anchored_to_visual_evidence_instead_of_unit_start(nested_frame):
    inputs = _inputs()
    inputs[3]["unit-b"].update(t_start=2581.34, t_end=2599.62)
    offer = inputs[2][0]
    offer["duration"] = 10 / 3
    evidence = offer["candidate_evidence"]["unit-b"]
    evidence.update(suggested_source_start=2593.387, matched_frame_timestamp=None if nested_frame else 2595.054)
    if nested_frame:
        evidence["matches"] = [{"clause_id": "clause-1", "facet": "look", "rank": 1,
                                "evidence": {"type": "frame", "timestamp": 2595.054, "frame_index": 2}}]
    payload = build_selection_payload(*inputs)
    source = payload["sources"]["c0"]
    preserved = payload["slots"][0]["candidates"][0]["evidence"]
    frame = preserved["matches"][0]["evidence"]["timestamp"] if nested_frame else preserved["matched_frame_timestamp"]
    assert source["t_start"] + offer["duration"] < frame, "the nominal unit-start trim misses the supplied visual evidence"
    assert preserved["suggested_source_start"] <= frame < preserved["suggested_source_start"] + offer["duration"] <= source["t_end"]
    prose = build_selection_prompt(payload).split("\n", 1)[0]
    for instruction in ("the selected trim must include the visual instant", "express its relative position using the normalized control",
                        "Do not default to t_start", "not proof that its image appears throughout the unit",
                        "or verify every frame of the trim"):
        assert instruction in prose
