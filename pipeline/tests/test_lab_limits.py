"""Whole-edit storage limits remain separate from one hosted planning batch."""
from copy import deepcopy

import pytest

from pipeline.lab.models import MusicSlot, Passage, ProjectDocument
from pipeline.lab.scene_selection import selection_choices, selection_schema


def _long_document():
    passage = {"start": 12., "end": 612.}
    return {
        "track": {"id": "track", "name": "Full song", "duration": 700.},
        "passage": passage,
        "clips": [{"id": f"clip-{index}", "film_id": "film", "source_start": 0., "source_end": 2.}
                  for index in range(600)],
        "music_timeline": {
            "track_id": "track", "passage": passage,
            "slots": [{"id": f"slot-{index}", "start": 12. + index * 2, "end": 14. + index * 2,
                       "section_index": 0, "clip_id": f"clip-{index}"} for index in range(300)],
        },
    }


def test_ten_minute_passage_accepts_offset_and_rejects_over_limit():
    assert Passage(start=12., end=612.).end == 612.
    with pytest.raises(ValueError, match="at most 10 minutes"):
        Passage(start=12., end=612.001)


def test_three_hundred_placed_slots_and_six_hundred_retained_sources_fit_independent_limits():
    document = ProjectDocument.model_validate(_long_document())
    assert len(document.music_timeline.slots) == 300
    assert len(document.clips) == 600
    assert document.music_timeline.slots[-1].clip_id == "clip-299"


def test_one_extra_saved_source_is_rejected_without_changing_timeline_capacity():
    document = _long_document()
    document["clips"].append({"id": "overflow", "film_id": "film", "source_start": 0., "source_end": 1.})
    with pytest.raises(ValueError, match="at most 600 items"):
        ProjectDocument.model_validate(document)


def test_one_extra_slot_is_rejected_even_when_timing_and_sources_fit():
    document = _long_document()
    # Split an empty final slot, so the only violation is its 301st position.
    slots = document["music_timeline"]["slots"]
    slots[-1].update(end=611., clip_id=None)
    slots.append({"id": "overflow", "start": 611., "end": 612., "section_index": 0})
    with pytest.raises(ValueError, match="at most 300 items"):
        ProjectDocument.model_validate(document)


def test_later_audio_part_sections_are_valid_but_remain_bounded():
    slot = {"id": "late", "start": 590., "end": 600., "section_index": 55}
    assert MusicSlot.model_validate(slot).section_index == 55
    with pytest.raises(ValueError):
        MusicSlot.model_validate({**slot, "section_index": 56})


def test_selector_can_address_last_timeline_slot_without_expanding_its_batch():
    sources = {"offered": {"film_id": "film", "t_start": 10., "t_end": 20.}}
    offers = [{"slot": 299, "start": 598., "duration": 2., "candidate_ids": ["offered"]}]
    schema = selection_schema(offers, sources)
    assert schema["properties"]["choices"]["required"] == ["shot_299"]
    selected = selection_choices({"choices": {"shot_299": {"source": {"c0": 10.}, "reason": "A final image"}}}, offers, sources)
    assert selected[0]["slot"] == 299 and selected[0]["candidate_id"] == "offered"
    invalid = deepcopy(offers)
    invalid[0]["slot"] = 300
    with pytest.raises(ValueError, match="valid timeline slot indices"):
        selection_schema(invalid, sources)
