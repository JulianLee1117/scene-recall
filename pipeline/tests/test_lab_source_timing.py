"""Source length can influence first cuts without claiming observed action timing."""
from copy import deepcopy
import itertools
import json

import pytest

from pipeline.lab import music, music_planner
from pipeline.lab.models import ClipSelection, MusicDirection, ProjectDocument
from pipeline.lab.source_timing import (initial_timing_scope, timing_offer, frame_time,
                                      fit_cut_preferences, validate_result, validate_scope)
from pipeline.lab.scene_selection import TIMED_SELECTION_CONTRACT, selection_choices
from pipeline.tests.test_lab_music_evidence import _document


def _intent_document():
    document = _document()
    document["rhythm"].pop("marker_source")
    document["rhythm"].pop("markers")
    for index, slot in enumerate(document["music_timeline"]["slots"]):
        slot["direction"] = MusicDirection(query=f"Specific image {index}").model_dump(mode="json")
        slot["direction_source"] = "ai"
    return ProjectDocument.model_validate(document).model_dump(mode="json")


def _row(identity, duration, **evidence):
    return {"unit_id": identity, "film_id": "film", "t_start": 20., "t_end": 20. + duration,
            "caption": "A figure at a doorway", **evidence}


def test_every_offered_boundary_combination_is_ordered_on_original_passage_grid():
    document = _intent_document()
    scope = initial_timing_scope(document)
    for selected in itertools.product(*scope["boundary_frames"]):
        times = [frame_time(scope, value) for value in selected]
        assert times[0] == 10 and times[-1] == 19
        assert all(b - a >= 1 / 24 - 1e-6 for a, b in zip(times, times[1:]))
        assert all(abs(time - nominal[2]) <= 2 for time, nominal in zip(times[1:-1], scope["nominal"][:-1]))
    assert all(len(options) <= 7 for options in scope["boundary_frames"])
    assert 24 * 2 in scope["boundary_frames"][1]  # The measured guide at source time 12.


def test_non_grid_audio_end_still_leaves_a_real_frame_in_last_slot():
    document = _intent_document()
    origin = document["passage"]["start"] = 9.94
    end = document["passage"]["end"] = 10.063
    document["music_timeline"]["passage"] = deepcopy(document["passage"])
    document["music_timeline"]["slots"] = [
        {"id": "a", "start": origin, "end": 10.}, {"id": "b", "start": 10., "end": end}]
    scope = initial_timing_scope(document)
    for boundary in scope["boundary_frames"][1]:
        assert frame_time(scope, boundary) - origin >= 1 / 24 - 1e-6
        assert end - frame_time(scope, boundary) >= 1 / 24 - 1e-6
    assert frame_time(scope, scope["total_frames"]) == end


def test_short_relevant_source_survives_first_edit_retrieval_and_moves_cut(config, monkeypatch):
    document = _intent_document()
    before = deepcopy(document)
    scope = initial_timing_scope(document)
    # This source could never fill the nominal three-second first slot.
    monkeypatch.setattr(music, "retrieve_edit_candidates", lambda *_: [
        _row("short", 2., matched_frame_timestamp=21.5, dialogue='["Wait here"]', on_screen_text="EXIT"),
        _row("long", 8., film_id="second-film"), _row("answer", 8., film_id="third-film")])
    def choose(_config, prompt, schema, *_args, **kwargs):
        payload = json.loads(prompt.split("\n", 1)[1])
        assert schema["properties"]["choices"]["required"] == ["shot_0", "shot_1", "shot_2"]
        assert schema["properties"]["choices"]["properties"]["shot_0"]["properties"]["preferred_end_frame"]["enum"] == payload["slots"][0]["timing"]["end_frames"]
        assert kwargs["operation"] == TIMED_SELECTION_CONTRACT
        assert payload["sources"]["c0"]["metadata"]["dialogue"] == ["Wait here"]
        assert payload["sources"]["c0"]["metadata"]["on_screen_text"] == "EXIT"
        assert payload["slots"][0]["candidates"][0]["source"] == "c0"
        assert payload["slots"][0]["timing"]["min_duration"] < 2
        assert "not observed action" in prompt
        return {"choices": {
            "shot_0": {"source": {"c0": 0.}, "preferred_end_frame": 72, "reason": "Use the evidenced doorway before the next visual answer"},
            "shot_1": {"source": {"c1": 0.}, "preferred_end_frame": 144, "reason": "A sustained response"},
            "shot_2": {"source": {"c2": .8}, "preferred_end_frame": 216, "reason": "Answer the motif through different footage"}}}
    monkeypatch.setattr(music, "_hosted_json", choose)
    result = music_planner.fill_timeline(document, config, object(), lambda _: None, "first", timing_scope=scope)
    assert [(slot["start"], slot["end"]) for slot in result["music_timeline"]["slots"]] == [(10, 12), (12, 16), (16, 19)]
    assert result["clips"][0]["source_end"] == 22
    assert result["rhythm"] == before["rhythm"] and result["passage"] == before["passage"]
    assert [slot["direction"] for slot in result["music_timeline"]["slots"]] == [slot["direction"] for slot in before["music_timeline"]["slots"]]
    draft = result["analysis"]["draft"]
    assert draft["timing_mode"] == "source-aware" and draft["selected_count"] == 3
    assert draft["candidate_ledger"][0]["selected_unit_id"] == "short"
    assert draft["timing_adjustments"] == [{"slot_id": document["music_timeline"]["slots"][0]["id"],
                                           "preferred_end_frame": 72, "end_frame": 48}]
    assert draft["candidate_ledger"][0]["preferred_end_frame"] == 72
    assert draft["candidate_ledger"][0]["end_frame"] == 48
    assert result["music_timeline"]["slots"][0]["resolved_search"]["min_duration"] == 2
    assert all(alternative["clip"]["source_end"] <= 28 for slot in result["music_timeline"]["slots"] for alternative in slot["alternatives"])
    ProjectDocument.model_validate(result)


def test_fixed_search_still_filters_short_sources_and_selector_can_abstain(config, monkeypatch):
    document = _intent_document()
    before = deepcopy(document)
    monkeypatch.setattr(music, "retrieve_edit_candidates", lambda *_: [_row("short", 2.), _row("unrelated", 10.)])
    def choose(_config, prompt, schema, *_args, **_kwargs):
        payload = json.loads(prompt.split("\n", 1)[1])
        assert schema["properties"]["choices"]["required"] == ["shot_0", "shot_1", "shot_2"]
        assert len(payload["sources"]) == 1 and payload["timing_scope"] is None
        return {"choices": {f"shot_{index}": {"source": None,
                             "reason": "The available image does not support this intention"} for index in range(3)}}
    monkeypatch.setattr(music, "_hosted_json", choose)
    result = music_planner.fill_timeline(document, config, object(), lambda _: None, "fixed")
    assert result["clips"] == []
    assert [(slot["start"], slot["end"]) for slot in result["music_timeline"]["slots"]] == [(slot["start"], slot["end"]) for slot in before["music_timeline"]["slots"]]
    assert result["analysis"]["draft"]["selected_count"] == 0
    assert len(result["analysis"]["draft"]["abstained_slot_ids"]) == 3
    assert all(slot["search_error"] and slot["direction"] for slot in result["music_timeline"]["slots"])


def test_abstaining_from_replacement_keeps_original_footage(config, monkeypatch):
    document = _intent_document()
    old = ClipSelection(id="kept", film_id="film", source_start=50, source_end=53).model_dump(mode="json")
    document["clips"] = [deepcopy(old)]
    slot = document["music_timeline"]["slots"][1]
    slot["clip_id"], slot["reason"] = old["id"], "Previously accepted image"
    monkeypatch.setattr(music, "retrieve_edit_candidates", lambda *_: [_row("unrelated", 10.)])
    monkeypatch.setattr(music, "_hosted_json", lambda *_a, **_k: {"choices": {
        "shot_1": {"source": None, "reason": "No useful replacement"}}})
    result = music_planner.fill_timeline(document, config, object(), lambda _: None, "replace", [slot["id"]])
    assert result["clips"] == [old] and slot["clip_id"] == old["id"]
    assert slot["reason"] == "Previously accepted image" and slot["search_error"] == "No useful replacement"


@pytest.mark.parametrize("changed", ["wrong-cut", "too-short", "invented", "nan", "duplicate", "missing", "bad-null"])
def test_invalid_joint_selection_fails_without_repair(changed):
    document = _intent_document()
    scope = initial_timing_scope(document)
    offers = [{"slot": i, "start": slot["start"], "duration": slot["end"] - slot["start"],
               "candidate_ids": ["a"], "timing": timing_offer(scope, i)} for i, slot in enumerate(document["music_timeline"]["slots"])]
    choices = {f"shot_{i}": {"source": {"c0": 0.}, "preferred_end_frame": (i + 1) * 72, "reason": "Specific intent"} for i in range(3)}
    assert len(selection_choices({"choices": choices}, offers, {"a": _row("a", 10.)}, scope)) == 3
    if changed == "wrong-cut": choices["shot_0"]["preferred_end_frame"] = 71
    elif changed == "too-short": choices["shot_0"]["source"] = {"c0": 1.01}
    elif changed == "invented": choices["shot_0"]["source"] = {"c99": 20.}
    elif changed == "nan": choices["shot_0"]["source"] = {"c0": float("nan")}
    elif changed == "duplicate": choices["shot_9"] = deepcopy(choices["shot_0"])
    elif changed == "missing": choices.pop("shot_1")
    elif changed == "bad-null": choices["shot_0"]["source"] = {"c0": None}
    with pytest.raises(ValueError):
        selection_choices({"choices": choices}, offers, {"a": _row("a", 10.)}, scope)


def test_no_candidate_skips_selector_and_preserves_intent_and_timing(config, monkeypatch):
    document = _intent_document()
    before = deepcopy(document["music_timeline"])
    scope = initial_timing_scope(document)
    monkeypatch.setattr(music, "retrieve_edit_candidates", lambda *_: [])
    monkeypatch.setattr(music, "_hosted_json", lambda *_a, **_k: pytest.fail("No candidates must not spend a selector call"))
    result = music_planner.fill_timeline(document, config, object(), lambda _: None, "none", timing_scope=scope)
    assert [slot["direction"] for slot in result["music_timeline"]["slots"]] == [slot["direction"] for slot in before["slots"]]
    assert [(slot["start"], slot["end"]) for slot in result["music_timeline"]["slots"]] == [(slot["start"], slot["end"]) for slot in before["slots"]]
    assert all(row["decision"] == "no-fitting-candidates" for row in result["analysis"]["draft"]["candidate_ledger"])


def test_scope_rejects_protected_changes_and_unoffered_final_timing():
    document = _intent_document()
    scope = initial_timing_scope(document)
    document["music_timeline"]["slots"][0]["direction_source"] = "user"
    with pytest.raises(ValueError, match="scope"):
        validate_scope(document, scope)
    document["music_timeline"]["slots"][0]["direction_source"] = "ai"
    document["music_timeline"]["slots"][0]["end"] = 12.333
    document["music_timeline"]["slots"][1]["start"] = 12.333
    with pytest.raises(ValueError, match="unoffered"):
        validate_result(scope, document)


def test_joint_solver_matches_exhaustive_feasible_paths_and_has_deterministic_ties():
    scope = {"passage": {"start": 0., "end": 4.5}, "fps": 24, "total_frames": 108}
    options = [[24, 36, 48], [60, 72, 84], [108]]
    offers = [{"slot": index, "timing": {"end_frames": values}} for index, values in enumerate(options)]
    preferred = [24, 84, 108]
    durations = [2., 2., 2.]
    feasible = []
    for path in itertools.product(*options):
        starts = [0, *path[:-1]]
        if all(1 <= end - start <= duration * 24 for start, end, duration in zip(starts, path, durations)):
            feasible.append((sum(abs(end - wish) for end, wish in zip(path, preferred)),
                             sum(end != wish for end, wish in zip(path, preferred)), path))
    expected = list(min(feasible)[2])
    assert expected == [24, 72, 108]
    assert fit_cut_preferences(scope, offers, preferred, durations) == expected
    # Reversing the offer enumeration cannot change the winning path.
    reversed_offers = [{"slot": offer["slot"], "timing": {"end_frames": list(reversed(offer["timing"]["end_frames"]))}}
                       for offer in offers]
    assert fit_cut_preferences(scope, reversed_offers, preferred, durations) == expected


def test_joint_solver_looks_ahead_instead_of_committing_the_first_preference():
    scope = {"passage": {"start": 0., "end": 3.}, "fps": 24, "total_frames": 72}
    offers = [{"slot": 0, "timing": {"end_frames": [24, 48]}},
              {"slot": 1, "timing": {"end_frames": [72]}}]
    assert fit_cut_preferences(scope, offers, [24, 72], [2., 1.]) == [48, 72]


def test_explicit_abstention_keeps_its_preference_without_inventing_source_constraints():
    scope = {"passage": {"start": 0., "end": 3.}, "fps": 24, "total_frames": 72}
    offers = [{"slot": 0, "timing": {"end_frames": [24, 48]}},
              {"slot": 1, "timing": {"end_frames": [72]}}]
    assert fit_cut_preferences(scope, offers, [24, 72], [2., None]) == [24, 72]
