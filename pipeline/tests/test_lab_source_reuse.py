"""Automatic montage selection does not silently recycle footage."""
from copy import deepcopy
import json

import pytest

from pipeline.lab import music, music_planner
from pipeline.lab.models import ClipSelection, MusicDirection, ProjectDocument
from pipeline.tests.selection_helpers import selector_response
from pipeline.tests.test_lab_music_evidence import _document


def _row(identity, start=20., end=30., film="film"):
    return {"unit_id": identity, "film_id": film, "t_start": start, "t_end": end,
            "caption": f"A person looking toward a doorway: {identity}"}


def _source(identity, unit_id, start=20., end=23., film="film"):
    return ClipSelection(id=identity, unit_id=unit_id, film_id=film,
                         source_start=start, source_end=end).model_dump(mode="json")


def _targets():
    document = _document()
    for index, slot in enumerate(document["music_timeline"]["slots"]):
        slot["direction"] = MusicDirection(query=f"Image {index}").model_dump(mode="json")
    return document


def test_previous_units_are_excluded_before_the_offer_cap_with_rank_and_counts_preserved(config, monkeypatch):
    document = _targets()
    rows = [_row(f"old-{index}") for index in range(24)] + [_row("fresh", film="fresh-film")]
    # Unit identity excludes the entire previous shot, including other trims.
    previous = [_source(f"previous-{index}", f"old-{index}", start=40, end=43) for index in range(24)]
    monkeypatch.setattr(music, "retrieve_edit_candidates", lambda *_: rows)
    def choose(_config, prompt, *_args, **_kwargs):
        payload = json.loads(prompt.split("\n", 1)[1])
        assert set(payload["sources"]) == {"c0"}
        assert payload["sources"]["c0"]["caption"] == _row("fresh")["caption"]
        assert all([candidate["source"] for candidate in offer["candidates"]] == ["c0"]
                   for offer in payload["slots"])
        return selector_response([{"slot": 0, "candidate_id": "fresh", "source_start": 20., "reason": "A new opening image"},
                            *[{"slot": i, "candidate_id": None, "source_start": None, "reason": "No different fitting source"} for i in [1, 2]]], ["fresh"])
    monkeypatch.setattr(music, "_hosted_json", choose)
    result = music_planner.fill_timeline(document, config, object(), lambda _: None, "fresh", previous_sources=previous)
    diagnostic = result["analysis"]["draft"]
    assert diagnostic["previous_source_count"] == diagnostic["previous_excluded_count"] == 24
    assert diagnostic["candidate_count"] == diagnostic["selected_count"] == 1
    assert all(row["previous_excluded_count"] == 24 and len(row["previous_excluded_unit_ids"]) == 24
               and row["candidates"][0]["rank"] == 25 for row in diagnostic["candidate_ledger"])
    assert all(option["clip"]["unit_id"] == "fresh" for slot in result["music_timeline"]["slots"] for option in slot["alternatives"])


def test_legacy_previous_windows_exclude_overlap_without_claiming_absence_from_the_library(config, monkeypatch):
    document = _targets()
    previous = [_source("legacy", None, start=22, end=24)]
    monkeypatch.setattr(music, "retrieve_edit_candidates", lambda *_: [_row("overlapping", start=23, end=30)])
    monkeypatch.setattr(music, "_hosted_json", lambda *_a, **_k: pytest.fail("No fresh candidates should skip hosted selection"))
    result = music_planner.fill_timeline(document, config, object(), lambda _: None, "excluded", previous_sources=previous)
    assert not result["clips"]
    assert result["analysis"]["draft"]["previous_excluded_count"] == 1
    assert all("Only footage from the previous edit fits this search" in slot["search_error"] for slot in result["music_timeline"]["slots"])


@pytest.mark.parametrize("third_unit,third_start,expected_repeats", [("a", 24., 1), ("overlap", 22., 1), ("adjacent", 23., 0)])
def test_repeated_units_and_overlapping_windows_abstain_but_adjacent_different_units_remain_legal(
        config, monkeypatch, third_unit, third_start, expected_repeats):
    document = _targets()
    rows = [_row("a"), _row("b", film="other-film")]
    if third_unit != "a": rows.append(_row(third_unit))
    monkeypatch.setattr(music, "retrieve_edit_candidates", lambda *_: rows)
    def choose(_config, prompt, *_args, **_kwargs):
        assert "recurring motifs through DIFFERENT footage" in prompt
        return selector_response([{"slot": 0, "candidate_id": "a", "source_start": 20., "reason": "Opening image"},
                            {"slot": 1, "candidate_id": "b", "source_start": 20., "reason": "Answer from another film"},
                            {"slot": 2, "candidate_id": third_unit, "source_start": third_start, "reason": "Return to the motif"}], [row["unit_id"] for row in rows])
    monkeypatch.setattr(music, "_hosted_json", choose)
    result = music_planner.fill_timeline(document, config, object(), lambda _: None, "repeat")
    diagnostic = result["analysis"]["draft"]
    assert diagnostic["repetition_count"] == expected_repeats
    assert diagnostic["selected_count"] == 3 - expected_repeats
    last = result["music_timeline"]["slots"][2]
    if expected_repeats:
        assert last["clip_id"] is None and "repeats clip 1" in last["search_error"]
        decision = diagnostic["candidate_ledger"][2]
        assert decision["decision"] == "abstained" and decision["selected_unit_id"] is None
        assert decision["proposed_unit_id"] == third_unit and decision["repetition_reason"] == last["search_error"]
        assert any(option["clip"]["unit_id"] == third_unit for option in last["alternatives"]), "Manual override remains available"
    else:
        assert last["clip_id"] is not None
    ProjectDocument.model_validate(result)


def test_repeating_a_current_source_preserves_the_requested_replacement(config, monkeypatch):
    document = _targets()
    original = _source("current", "same")
    document["clips"] = [deepcopy(original)]
    slot = document["music_timeline"]["slots"][1]
    slot.update(clip_id=original["id"], reason="Accepted source")
    monkeypatch.setattr(music, "retrieve_edit_candidates", lambda *_: [_row("same")])
    monkeypatch.setattr(music, "_hosted_json", lambda *_a, **_k: selector_response([
        {"slot": 1, "candidate_id": "same", "source_start": 24., "reason": "An alternative trim"}], ["same"]))
    result = music_planner.fill_timeline(document, config, object(), lambda _: None, "replace", [slot["id"]])
    assert result["clips"] == [original]
    assert slot["clip_id"] == original["id"] and slot["reason"] == "Accepted source"
    assert "repeats current clip 2" in slot["search_error"]


def test_manual_search_can_still_offer_previously_used_footage(config, monkeypatch):
    document = _targets()
    old = _source("old", "same")
    monkeypatch.setattr(music, "retrieve_edit_candidates", lambda *_: [_row("same")])
    monkeypatch.setattr(music, "_hosted_json", lambda *_a, **_k: pytest.fail("Manual search must not call a model"))
    slot = document["music_timeline"]["slots"][0]
    result = music_planner.fill_timeline(document, config, object(), lambda _: None, "manual", [slot["id"]],
                                        previous_sources=[old], suggest_only=True)
    assert slot["alternatives"][0]["clip"]["unit_id"] == "same"
    assert not result["clips"]


@pytest.mark.parametrize("abstained_index", [0, 2])
def test_multi_target_replacement_reserves_originals_before_and_after_an_abstention(config, monkeypatch, abstained_index):
    document = _targets()
    originals = [_source("first", "original-first", start=20, end=23),
                 _source("last", "original-last", start=40, end=43)]
    document["clips"] = deepcopy(originals)
    slots = document["music_timeline"]["slots"]
    slots[0].update(clip_id="first", reason="Accepted first image")
    slots[2].update(clip_id="last", reason="Accepted last image")
    prior_slots = deepcopy(slots)
    target_indices = [0, 2]
    overlap_start = originals[target_indices.index(abstained_index)]["source_start"]
    # A different indexed unit still overlaps the original source retained by
    # the other targeted slot. Order must not affect the collision check.
    monkeypatch.setattr(music, "retrieve_edit_candidates", lambda *_: [_row("new-unit", start=overlap_start, end=overlap_start + 10)])
    monkeypatch.setattr(music, "_hosted_json", lambda *_a, **_k: selector_response([
        {"slot": index, "candidate_id": None if index == abstained_index else "new-unit",
         "source_start": None if index == abstained_index else overlap_start + 1,
         "reason": "No fitting alternative" if index == abstained_index else "Use this image"}
        for index in target_indices], ["new-unit"]))
    result = music_planner.fill_timeline(document, config, object(), lambda _: None, "multi-replace",
                                        [slots[index]["id"] for index in target_indices])
    assert result["clips"] == originals
    assert all(slots[index]["clip_id"] == prior_slots[index]["clip_id"] and
               slots[index]["reason"] == prior_slots[index]["reason"] for index in target_indices)
    repeated_index = 2 - abstained_index
    assert f"repeats current clip {abstained_index + 1}" in slots[repeated_index]["search_error"]
    assert slots[abstained_index]["search_error"] == "No fitting alternative"
    diagnostic = result["analysis"]["draft"]
    assert diagnostic["repetition_count"] == 1 and diagnostic["selected_count"] == 0
    assert set(diagnostic["abstained_slot_ids"]) == {slots[index]["id"] for index in target_indices}
