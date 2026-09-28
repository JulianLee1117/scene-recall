"""Arrangement regressions: every salient partner must actually match."""
from copy import deepcopy
import json
from math import nan
from pathlib import Path

import pytest

from pipeline.matching.person_layout import compare_people


def person(x, y=.5, height=.6, width=.13, *, aspect=16 / 9):
    return {"centroid": [x, y], "box": {"x": x - width / 2, "y": y - height / 2,
            "width": width, "height": height}, "area": width * height * .65,
            "picture_aspect": aspect, "silhouette": [1.] * 64, "visible": True}


def pair():
    return [person(.35), person(.65)]


def test_two_people_require_two_people_and_do_not_select_a_convenient_subset():
    assert compare_people(pair(), [person(.35)])["reason"] == "person_count_mismatch"
    assert compare_people(pair(), [*pair(), person(.85)])["reason"] == "person_count_mismatch"


def test_matching_centers_cannot_hide_both_people_being_too_small():
    smaller = [person(.35, height=.25, width=.055), person(.65, height=.25, width=.055)]
    result = compare_people(pair(), smaller)
    assert not result["reliable"]
    assert result["strength"] == 0.


def test_one_perfect_person_cannot_compensate_for_the_wrong_partner():
    result = compare_people(pair(), [person(.35), person(.85)])
    assert not result["reliable"]
    assert result["measurements"]["assignment"][0]["reliable"]
    assert not result["measurements"]["assignment"][1]["reliable"]


def test_valid_full_body_pair_has_per_person_proof_and_spacing():
    reference = pair()
    candidate = [person(.37, y=.51, height=.58), person(.66, y=.51, height=.59)]
    saved = deepcopy((reference, candidate))
    result = compare_people(reference, candidate)
    assert result["reliable"]
    assert .7 < result["strength"] < 1
    assert result["measurements"]["reference_people"] == 2
    assert result["measurements"]["candidate_people"] == 2
    assignment = result["measurements"]["assignment"]
    assert [row["candidate_index"] for row in assignment] == [0, 1]
    assert assignment[0]["horizontal_difference"] == pytest.approx(.02)
    assert assignment[0]["candidate_height_ratio"] == pytest.approx(.58 / .6)
    assert result["measurements"]["spacing"][0]["reference_vector"] == pytest.approx([.3, 0])
    assert (reference, candidate) == saved


def test_detector_order_is_irrelevant_and_each_candidate_is_assigned_once():
    reference = [person(.2), person(.5), person(.8)]
    result = compare_people(reference, [reference[2], reference[0], reference[1]])
    assert result["reliable"]
    assert result["strength"] == pytest.approx(1.)
    assert [row["candidate_index"] for row in result["measurements"]["assignment"]] == [1, 2, 0]
    assert len(result["measurements"]["spacing"]) == 3


def test_plausible_individual_centers_cannot_hide_wrong_group_spacing():
    result = compare_people(pair(), [person(.44), person(.56)])
    assert all(row["reliable"] for row in result["measurements"]["assignment"])
    assert not result["reliable"]
    assert not result["measurements"]["spacing"][0]["reliable"]


def test_weakest_partner_limits_strength_instead_of_averaging_it_away():
    result = compare_people(pair(), [person(.35), person(.72)])
    assert result["reliable"]
    assert result["strength"] <= result["measurements"]["assignment"][1]["strength"]
    assert result["strength"] < .55


def test_physical_aspect_survives_normalized_picture_coordinates():
    reference = [person(.5, aspect=16 / 9)]
    candidate = [person(.5, width=.13 * 4 / 3, aspect=4 / 3)]
    result = compare_people(reference, candidate)
    assert result["reliable"]
    assert result["measurements"]["assignment"][0]["physical_aspect_similarity"] == pytest.approx(1.)
    different_aspect = compare_people(reference, [person(.5, aspect=.5)])
    assert different_aspect["reliable"]
    assert different_aspect["measurements"]["assignment"][0]["physical_aspect_similarity"] < .6


def test_arm_width_and_outline_are_not_requirements_for_the_same_arrangement():
    candidate = [person(.35, width=.31), person(.65, width=.31)]
    for row in candidate:
        row["silhouette"] = [float(index % 2) for index in range(64)]
    result = compare_people(pair(), candidate)
    assert result["reliable"]
    assert result["strength"] == pytest.approx(1.)
    assert all(row["physical_aspect_similarity"] < .6 for row in result["measurements"]["assignment"])


def test_gross_mask_size_difference_still_requires_rejection():
    candidate = [person(.35, width=.55), person(.65, width=.55)]
    result = compare_people(pair(), candidate)
    assert not result["reliable"]
    assert all(row["area_similarity"] < .25 for row in result["measurements"]["assignment"])


def test_closer_central_pair_is_measured_without_being_erased():
    result = compare_people(pair(), [person(.42), person(.58)])
    assert result["reliable"]
    assert 0 < result["strength"] < .2
    assert result["measurements"]["spacing"][0]["distance_similarity"] == pytest.approx(.16 / .3)


def test_recorded_dancer_pairs_match_layout_and_still_require_both_people():
    fixture = json.loads((Path(__file__).parent / "data/match_people_layout.json").read_text())
    source = fixture["source"]["selected"]
    assert len(source) == 2
    for row in fixture["candidates"]:
        result = compare_people(source, row["selected"])
        assert result["reliable"], row["probe_index"]
        assert len(result["measurements"]["assignment"]) == 2
        assert compare_people(source, row["selected"][:1])["reason"] == "person_count_mismatch"
    assert compare_people(source, fixture["candidates"][0]["selected"])["strength"] < compare_people(source, fixture["candidates"][1]["selected"])["strength"]


def test_arrangement_does_not_reflect_the_picture_to_find_a_match():
    reference = [person(.25, y=.35, height=.4), person(.65, y=.6, height=.4)]
    reflected = [person(.75, y=.35, height=.4), person(.35, y=.6, height=.4)]
    assert not compare_people(reference, reflected)["reliable"]


@pytest.mark.parametrize("reference,candidate", [([], []), (pair() * 2, pair() * 2), (None, pair()), (pair(), {})])
def test_unsupported_groups_are_rejected(reference, candidate):
    assert compare_people(reference, candidate)["reason"] == "unsupported_person_count"


@pytest.mark.parametrize("field,value", [
    ("centroid", [nan, .5]), ("centroid", [1.1, .5]), ("centroid", [.5]),
    ("centroid", [.01, .01]), ("area", nan), ("area", -1), ("area", .8),
    ("picture_aspect", 0), ("picture_aspect", float("inf")), ("picture_aspect", True),
    ("box", {"x": .9, "y": .1, "width": .3, "height": .5}),
    ("box", {"x": .1, "y": .1, "width": -.3, "height": .5}),
    ("box", None), ("visible", False),
])
def test_invalid_normalized_geometry_is_rejected_without_a_score(field, value):
    invalid = person(.5)
    invalid[field] = value
    result = compare_people([person(.5)], [invalid])
    assert result["reason"] == "invalid_person_geometry"
    assert not result["reliable"]
    assert result["strength"] == 0.
