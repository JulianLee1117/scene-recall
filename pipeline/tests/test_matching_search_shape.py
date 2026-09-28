"""Precision guards for search silhouettes; no editorial quality is inferred."""
from copy import deepcopy

import numpy as np
import pytest

from pipeline.matching.search_shape import shape_similarity


def frame(pattern=None, *, box=None, picture_aspect=1.):
    if pattern is None:
        pattern = np.zeros((8, 8))
        pattern[:2, 3:5] = 1
        pattern[2:5, 1:7] = 1
        pattern[5:, 2:4] = 1
        pattern[5:, 5:7] = 1
    box = box or {"x": .3, "y": .2, "width": .4, "height": .6}
    return {"visible": True, "centroid": [.5, .5], "box": box,
            "area": float(np.mean(pattern) * box["width"] * box["height"]),
            "picture_aspect": picture_aspect, "silhouette": np.asarray(pattern).ravel().tolist()}


def test_same_outline_is_supported_and_preserves_meaningful_measurements():
    result = shape_similarity(frame(), frame())
    assert result["reliable"] and result["score"] == pytest.approx(1.)
    assert result["components"]["foreground_overlap"] == 1
    assert result["components"]["negative_space_overlap"] == 1
    assert result["components"]["outline_mode"] == "foreground-and-negative-space"
    assert "position" not in result["components"]


def test_position_cannot_improve_or_weaken_shape_evidence():
    first, shifted = frame(), frame()
    shifted["centroid"] = [.8, .6]
    shifted["box"]["x"] = .6
    result = shape_similarity(first, shifted)
    assert result["reliable"]
    assert result["score"] == shape_similarity(first, first)["score"]


def test_centered_filled_band_does_not_match_a_subject_outline():
    subject = frame()
    band = frame(np.ones((8, 8)), box={"x": .3, "y": 0., "width": .4, "height": 1.})
    band["centroid"] = subject["centroid"]
    result = shape_similarity(subject, band)
    assert result["components"]["foreground_overlap"] > .5
    assert result["components"]["negative_space_overlap"] == 0
    assert not result["reliable"]


def test_nearly_filled_border_band_cannot_gain_outline_support_from_soft_edges():
    solid = np.ones((8, 8))
    solid[:, -1] = .7
    band = frame(solid, box={"x": .6, "y": 0., "width": .4, "height": 1.})
    assert not shape_similarity(band, band)["reliable"]


def test_solid_interior_shapes_are_explicitly_supported_without_inventing_negative_space():
    solid = frame(np.ones((8, 8)))
    result = shape_similarity(solid, solid)
    assert result["reliable"] and result["score"] == pytest.approx(1.)
    assert result["components"]["negative_space_overlap"] is None
    assert result["components"]["outline_mode"] == "interior-solid"


def test_shared_empty_cells_cannot_match_opposite_foregrounds():
    first = np.zeros((8, 8)); first[:, 0] = 1
    second = np.zeros((8, 8)); second[:, -1] = 1
    result = shape_similarity(frame(first), frame(second))
    assert result["components"]["negative_space_overlap"] > .7
    assert result["components"]["foreground_overlap"] == 0
    assert not result["reliable"]


def test_scale_change_reduces_support_but_preserves_an_actual_outline_match():
    first, smaller = frame(), frame(box={"x": .4, "y": .35, "width": .2, "height": .3})
    result = shape_similarity(first, smaller)
    assert result["reliable"]
    assert result["components"]["scale"] == pytest.approx(.25)
    assert result["components"]["silhouette"] == 1
    assert .7 < result["score"] < shape_similarity(first, first)["score"]


def test_picture_aspect_restores_geometry_lost_in_resized_masks():
    first, wide = frame(np.ones((8, 8))), frame(np.ones((8, 8)), picture_aspect=3.)
    result = shape_similarity(first, wide)
    assert result["components"]["aspect"] == pytest.approx(1 / 3)
    assert not result["reliable"]


def test_position_agreement_does_not_turn_a_different_outline_into_shape_evidence():
    first = frame()
    second = frame(1 - np.asarray(first["silhouette"]).reshape(8, 8))
    assert first["centroid"] == second["centroid"]
    assert not shape_similarity(first, second)["reliable"]


def test_invisible_frame_never_claims_shape_evidence():
    hidden = frame(); hidden["visible"] = False
    assert not shape_similarity(frame(), hidden)["reliable"]


@pytest.mark.parametrize("field,value", [("area", float("nan")), ("area", 0.), ("picture_aspect", float("inf"))])
def test_invalid_geometry_cannot_create_a_shape_claim(field, value):
    invalid = deepcopy(frame()); invalid[field] = value
    with pytest.raises(ValueError, match="geometry"):
        shape_similarity(frame(), invalid)


def test_invalid_silhouette_values_remain_rejected():
    invalid = frame(); invalid["silhouette"][0] = float("nan")
    with pytest.raises(ValueError, match="silhouette"):
        shape_similarity(frame(), invalid)
