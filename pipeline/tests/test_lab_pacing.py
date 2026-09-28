"""Technical processing bounds must not choose musical cut density."""
from copy import deepcopy

import pytest

from pipeline.lab.pacing import batch_slots, uses_bounded_generation, validate_cuts


def _slots(durations, start=0.):
    slots = []
    for index, duration in enumerate(durations):
        slots.append({"id": str(index), "start": start, "end": start + duration})
        start += duration
    return slots


def test_batches_preserve_musical_cuts_across_old_processing_boundaries():
    slots = _slots([18, .5, .5, 20, .5, .5, 70, 2], start=53.99)
    before = deepcopy(slots)
    for size in (2, 8, 32):
        batches = batch_slots(slots, max_shots=size)
        assert [slot for batch in batches for slot in batch] == before
        assert all(len(batch) <= size for batch in batches)
        assert all(batch[-1]["end"] - batch[0]["start"] <= 90 for batch in batches)
    assert slots == before


def test_over_ninety_second_hold_is_one_position_in_its_own_batch():
    slots = _slots([12, 100, 5, 7])
    batches = batch_slots(slots)
    assert [len(batch) for batch in batches] == [1, 1, 2]
    assert batches[1][0] is slots[1]
    assert batches[1][0]["end"] - batches[1][0]["start"] == 100


def test_three_hundred_positions_include_partial_last_batch():
    slots = _slots([.5] * 300)
    batches = batch_slots(slots)
    assert [len(batch) for batch in batches] == [32] * 9 + [12]
    assert [slot for batch in batches for slot in batch] == slots


@pytest.mark.parametrize("pacing", ["patient", "balanced", "kinetic", "rapid"])
def test_fixed_dense_short_timeline_is_bounded_for_every_preference(pacing):
    document = {"passage": {"start": 0, "end": 20}, "planner_settings": {"pacing": pacing},
                "music_timeline": {"slots": _slots([.5] * 40)}}
    assert uses_bounded_generation(document)
    document["music_timeline"]["slots"] = _slots([1] * 20)
    assert not uses_bounded_generation(document)
    document["passage"]["end"] = 100
    assert uses_bounded_generation(document)


def test_technical_validation_allows_bursts_and_holds_without_a_density_quota():
    assert validate_cuts([2400], {"start": 0, "end": 100}) == [(0, 100)]
    assert validate_cuts([12, 24, 480, 2160], {"start": 0, "end": 90}) == [(0, .5), (.5, 1), (1, 20), (20, 90)]


@pytest.mark.parametrize("frames", [[True, 24], [1., 24], [0, 24], [12, 12, 24], [25], [23]])
def test_invalid_output_frames_are_not_repaired(frames):
    before = deepcopy(frames)
    with pytest.raises(ValueError):
        validate_cuts(frames, {"start": 5., "end": 6.})
    assert frames == before


def test_partial_final_frame_cannot_create_a_shorter_than_frame_shot():
    passage = {"start": 9.94, "end": 39.963}
    with pytest.raises(ValueError, match="source time"):
        validate_cuts([720, 721], passage)
    bounds = validate_cuts([719, 721], passage)
    assert bounds[-1][1] == passage["end"]


@pytest.mark.parametrize("slots", [[{"start": 0, "end": 1}, {"start": 2, "end": 3}], [{"start": 2, "end": 1}]])
def test_batching_rejects_nonconsecutive_ranges(slots):
    with pytest.raises(ValueError, match="consecutive"):
        batch_slots(slots)
