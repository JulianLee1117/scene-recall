"""Region proposals preserve source geometry and expose real crop costs."""

from __future__ import annotations

import math

import pytest

from pipeline.experiments.region_geometry import Box, NoFeasibleCrop, Picture, propose_crop


def test_identity_match_preserves_full_frame_without_crop_cost():
    picture, region = Picture(1920, 1080), Box(.3, .3, .4, .4)
    result = propose_crop(picture, region, picture, region)
    assert result.candidate_crop == Box(0, 0, 1, 1)
    assert result.geometry_similarity == pytest.approx(1)
    assert result.score == pytest.approx(1)
    assert result.crop_loss == 0
    assert result.resolution_headroom == 1


def test_small_region_in_4k_can_match_larger_region_with_uniform_whole_image_crop():
    result = propose_crop(
        Picture(1920, 1080), Box(.3, .3, .4, .4),
        Picture(3840, 2160), Box(.4, .4, .2, .2),
    )
    assert result.candidate_crop == Box(.25, .25, .5, .5)
    assert result.candidate_output_region.width == pytest.approx(.4)
    assert result.candidate_output_region.height == pytest.approx(.4)
    assert result.geometry_similarity == pytest.approx(1)
    assert result.crop_loss == pytest.approx(.75)
    assert result.resolution_headroom == pytest.approx(1)
    assert result.reference_region == Box(.3, .3, .4, .4)
    assert result.candidate_region == Box(.4, .4, .2, .2)
    assert result.score < result.geometry_similarity  # Lost context remains a cost.


def test_edge_subject_is_retained_and_position_mismatch_is_exposed():
    region = Box(0, 0, .2, .2)
    result = propose_crop(Picture(1920, 1080), Box(.3, .3, .4, .4), Picture(3840, 2160), region)
    assert result.candidate_crop.contains(region)
    assert result.candidate_crop.x == 0
    assert result.candidate_crop.y == 0
    assert result.geometry_similarity < 1


def test_uniform_pixel_scale_handles_different_source_aspects():
    result = propose_crop(
        Picture(1920, 1080), Box(.4, .3, .2, .4),
        Picture(2160, 3840), Box(.4, .4, .15, .15),
        output=Picture(1080, 1920), max_upscale=2,
    )
    crop = result.candidate_crop
    sx = 1080 / (crop.width * 2160)
    sy = 1920 / (crop.height * 3840)
    assert sx == pytest.approx(sy)
    assert sx == pytest.approx(result.candidate_uniform_scale)
    assert crop.contains(result.candidate_region)


def test_protected_context_cost_is_measured_separately():
    result = propose_crop(
        Picture(1920, 1080), Box(.3, .3, .4, .4),
        Picture(3840, 2160), Box(.4, .4, .2, .2),
        protected_context=Box(0, 0, .1, .1),
    )
    assert result.context_loss == pytest.approx(1)
    assert result.candidate_crop.contains(result.candidate_region)


def test_enlargement_limit_prevents_arbitrarily_good_tiny_region_match():
    result = propose_crop(
        Picture(1920, 1080), Box(.25, .25, .5, .5),
        Picture(1920, 1080), Box(.475, .475, .05, .05), max_upscale=2,
    )
    assert result.candidate_uniform_scale <= 2
    assert result.resolution_headroom >= .5
    assert result.geometry_similarity < .1


def test_reference_crop_cannot_distort_or_cut_out_selected_region():
    picture, region = Picture(1920, 1080), Box(.2, .2, .3, .3)
    with pytest.raises(ValueError, match="pixel aspect"):
        propose_crop(picture, region, picture, region, reference_crop=Box(0, 0, .5, .25))
    with pytest.raises(NoFeasibleCrop, match="remove part"):
        propose_crop(picture, region, picture, region, reference_crop=Box(.5, .5, .5, .5))


def test_impossible_portrait_region_and_insufficient_resolution_fail_explicitly():
    with pytest.raises(NoFeasibleCrop, match="without padding"):
        propose_crop(Picture(1920, 1080), Box(0, 0, 1, 1), Picture(1920, 1080), Box(.4, .4, .2, .2), output=Picture(1080, 1920))
    with pytest.raises(NoFeasibleCrop, match="enlargement"):
        propose_crop(Picture(3840, 2160), Box(.4, .4, .2, .2), Picture(640, 360), Box(.4, .4, .2, .2), output=Picture(3840, 2160), max_upscale=2)


@pytest.mark.parametrize("args", [(math.nan, 0, .1, .1), (0, 0, math.inf, .1), (True, 0, .1, .1), (0, 0, 0, .1), (.9, 0, .2, .1), (-.1, 0, .1, .1)])
def test_invalid_region_coordinates_are_rejected(args):
    with pytest.raises(ValueError):
        Box(*args)


def test_narrow_boundary_crops_stay_in_source_without_nan():
    result = propose_crop(Picture(1920, 1080), Box(.9, .9, .1, .1), Picture(1920, 1080), Box(.99, .99, .01, .01))
    crop = result.candidate_crop
    assert crop.x + crop.width <= 1 + 1e-9
    assert crop.y + crop.height <= 1 + 1e-9
    assert crop.contains(result.candidate_region)
    assert math.isfinite(result.score)
