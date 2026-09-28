"""Real-reference subject selection regressions without inference or source media."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from pipeline.matching.search_subject import select_reference_track


CASES = json.loads((Path(__file__).parent / "data/match_reference_selection.json").read_text(encoding="utf-8"))["cases"]


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_automatic_selection_uses_the_visible_subject_in_reported_reference(case):
    tracks = deepcopy(case["tracks"])
    selected = select_reference_track(tracks, case["anchor_time"])
    assert selected["id"] == case["expected_track_id"], case["reason"]
    assert select_reference_track(list(reversed(tracks)), case["anchor_time"])["id"] == case["expected_track_id"]
    # Selection is a query policy; the retained model evidence stays immutable.
    assert tracks == case["tracks"]


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_explicit_selection_preserves_the_requested_region_even_when_auto_prefers_another(case):
    for requested in case["tracks"]:
        ordered = [requested, *(track for track in case["tracks"] if track is not requested)]
        assert select_reference_track(ordered, case["anchor_time"], explicit=True) is requested


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_reference_selection_does_not_borrow_geometry_from_a_different_cut_frame(case):
    assert select_reference_track(case["tracks"], case["anchor_time"] + .01) is None
