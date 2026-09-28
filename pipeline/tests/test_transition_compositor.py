"""Pixel-level checks for the light/motion compositor's actual invariants."""
import numpy as np
import pytest

from pipeline.transitions.compositor import Compositor, DECODE_709, encode_rgb, pulse
from pipeline.transitions.contracts import Recipe, catalog


def recipe(identity, **kwargs):
    return Recipe(id=identity, **kwargs).model_dump()


def pair(width=160, height=90):
    x, y = np.meshgrid(np.linspace(0, 1, width), np.linspace(0, 1, height))
    a = np.stack([x, y * .5, .15 + y * .5], axis=-1)
    b = np.stack([y * .4, .3 + x * .4, 1 - x], axis=-1)
    a[height // 3:height // 3 + 9, width // 4:width // 4 + 9] = 1
    return np.rint(a * 255).astype(np.uint8), np.rint(b * 255).astype(np.uint8)


@pytest.mark.parametrize("spec", [row for row in catalog()["recipes"] if row["id"] != "hard-cut"], ids=lambda row: row["id"])
def test_every_recipe_has_exact_source_endpoints_and_bounded_pixels(spec):
    a, b = pair()
    compositor = Compositor(160, 90, spec["defaults"])
    assert np.array_equal(compositor.render(a, b, 0), a)
    assert np.array_equal(compositor.render(a, b, 1), b)
    for position in (.01, .3, .5, .72, .99):
        output = compositor.render(a, b, position)
        assert output.dtype == np.uint8 and output.shape == a.shape
        assert np.isfinite(output).all()


@pytest.mark.parametrize("direction", ["left", "right", "up", "down"])
def test_camera_whip_changes_whole_frames_without_a_sliding_picture_divider(direction):
    a, b = np.zeros((90, 160, 3), dtype=np.uint8), np.zeros((90, 160, 3), dtype=np.uint8)
    a[..., 0], b[..., 2] = 255, 255
    sharp = Compositor(160, 90, recipe("whip-pan", direction=direction, intensity=0)).render(a, b, .5)
    blurred = Compositor(160, 90, recipe("whip-pan", direction=direction, intensity=1, spread=1)).render(a, b, .5)
    # The crossover weight applies to every pixel of each complete picture.
    assert np.ptp(sharp[..., 0]) == 0 and np.ptp(sharp[..., 2]) == 0
    assert np.all((sharp[..., 0] > 100) & (sharp[..., 2] > 100))
    assert np.array_equal(sharp, blurred)
    compositor = Compositor(160, 90, recipe("whip-pan", direction=direction, softness=.04))
    assert np.array_equal(compositor.render(a, b, .35), a)
    assert np.array_equal(compositor.render(a, b, .65), b)


def test_blends_preserve_light_and_transfer_roundtrips_all_codes():
    ramp = np.arange(256, dtype=np.uint8)
    assert np.array_equal(encode_rgb(DECODE_709[ramp]), ramp)
    a, b = np.zeros((10, 10, 3), dtype=np.uint8), np.full((10, 10, 3), 255, dtype=np.uint8)
    middle = Compositor(10, 10, recipe("cross-dissolve", easing="linear")).render(a, b, .5)
    assert 179 <= middle.mean() <= 181  # Rec.709 OETF of 0.5, not a dark code-space blend of128.


def test_luma_order_and_matte_source_are_independent():
    a = np.zeros((20, 40, 3), dtype=np.uint8)
    a[:, :20] = 255
    b = np.zeros_like(a)
    b[..., 0] = 255
    bright = Compositor(40, 20, recipe("luma-reveal", easing="linear")).render(a, b, .5)
    dark = Compositor(40, 20, recipe("luma-reveal", easing="linear", luma_order="dark")).render(a, b, .5)
    other = Compositor(40, 20, recipe("luma-reveal", easing="linear", luma_source="incoming")).render(a, b, .5)
    assert bright[10, 5, 1] == 0 and dark[10, 5, 1] == 255
    assert bright[10, 30, 0] == 0 and dark[10, 30, 0] == 255
    assert not np.array_equal(bright, other)


def test_zoom_aligns_separate_anchors_at_crossover_and_honors_peak_scale():
    a, b = np.zeros((101, 101, 3), dtype=np.uint8), np.zeros((101, 101, 3), dtype=np.uint8)
    a[48:53, 23:28] = (255, 0, 0)
    b[48:53, 73:78] = (0, 0, 255)
    output = Compositor(101, 101, recipe("crash-zoom", anchor_ax=.25, anchor_bx=.75, zoom_amount=3, intensity=0)).render(a, b, .5)
    assert output[50, 50, 0] > 170 and output[50, 50, 2] > 170
    assert np.count_nonzero(output[50, :, 0] > 100) >= 10


def test_bloom_responds_to_highlights_and_keeps_distant_shadows():
    a = np.full((90, 160, 3), 12, dtype=np.uint8)
    a[40:50, 75:85] = 225
    c = Compositor(160, 90, recipe("highlight-bloom", intensity=.7, spread=.5))
    output = c.render(a, a, .5)
    assert output[45, 80].mean() > a[45, 80].mean()
    assert output[45, 70].mean() > a[45, 70].mean()
    assert output[0, 0].mean() <= 13


def test_burn_seed_is_deterministic_and_changes_the_spatial_recipe():
    a, b = pair()
    left = Compositor(160, 90, recipe("film-burn", texture=1, seed=7)).render(a, b, .5)
    repeat = Compositor(160, 90, recipe("film-burn", texture=1, seed=7)).render(a, b, .5)
    other = Compositor(160, 90, recipe("film-burn", texture=1, seed=32)).render(a, b, .5)
    assert np.array_equal(left, repeat)
    assert np.abs(left.astype(float) - other).mean() > 2


def test_flash_families_are_spatially_distinct_and_second_pulse_is_real():
    a, b = pair()
    names = ["highlight-bloom", "film-burn", "lens-sweep", "shutter-flash", "afterimage-flash", "light-flash"]
    outputs = [Compositor(160, 90, recipe(name)).render(a, b, .55).astype(float) for name in names]
    for index, output in enumerate(outputs):
        for previous in outputs[:index]:
            assert np.abs(output - previous).mean() > 1
    one = Compositor(160, 90, recipe("shutter-flash", decay=.08, second_pulse=0)).render(a, b, .7)
    two = Compositor(160, 90, recipe("shutter-flash", decay=.08, second_pulse=.8, pulse_gap=.2)).render(a, b, .7)
    assert two.mean() > one.mean() + 5


def test_afterimage_freezes_one_documented_outgoing_frame_then_decays():
    a, b = pair()
    c = Compositor(160, 90, recipe("afterimage-flash"))
    c.render(a, b, .4, frame_index=4)
    c.render(a, b, .49, frame_index=5)
    frozen = c.frozen_afterimage.copy()
    c.render(np.zeros_like(a), b, .65, frame_index=6)
    assert c.afterimage_frame == 5
    assert np.array_equal(frozen, c.frozen_afterimage)


def test_short_early_afterimage_cut_retains_the_outgoing_endpoint_frame():
    a, b = pair()
    c = Compositor(160, 90, recipe("afterimage-flash", duration=.1, cut_phase=.15))
    assert np.array_equal(c.render(a, b, 0, frame_index=0), a)
    c.render(np.zeros_like(a), b, .5, frame_index=1)
    assert c.afterimage_frame == 0
    assert np.array_equal(c.frozen_afterimage, DECODE_709[a])
    assert np.array_equal(c.render(a, b, 1, frame_index=2), b)


def test_light_envelope_has_exact_peak_independent_attack_decay_and_zero_endpoints():
    assert pulse(0, .6, .1, .3) == pulse(1, .6, .1, .3) == 0
    assert pulse(.6, .6, .1, .3) == 1
    assert pulse(.55, .6, .1, .3) == pytest.approx(.5)
    assert pulse(.75, .6, .1, .3) == pytest.approx(.5)


def test_catalog_exposes_valid_controls_and_source_framing_is_bounded():
    from pipeline.transitions.contracts import SourceClip
    from pydantic import ValidationError
    for spec in catalog()["recipes"]:
        assert [row["key"] for row in spec["control_specs"]] == spec["controls"]
        for control in spec["control_specs"]:
            assert control["key"] in spec["defaults"]
    with pytest.raises(ValidationError):
        SourceClip(film_id="a", source_start=0, source_end=1, framing={"zoom": 3})
    with pytest.raises(ValidationError):
        SourceClip(film_id="a", source_start=0, source_end=1, framing={"anchor_x": float("nan")})


def test_light_peak_is_independent_of_picture_cut_phase():
    a, b = np.full((20, 40, 3), 30, dtype=np.uint8), np.full((20, 40, 3), 100, dtype=np.uint8)
    early = Compositor(40, 20, recipe("light-flash", cut_phase=.75, peak_phase=.3, attack=.1, decay=.1)).render(a, b, .3)
    late = Compositor(40, 20, recipe("light-flash", cut_phase=.75, peak_phase=.6, attack=.1, decay=.1)).render(a, b, .3)
    assert early.mean() > late.mean() + 15
    assert late.mean() == 30, "Moving the light peak must not move the picture cut"


def test_non_cut_recipes_require_an_interior_effect_frame():
    from pydantic import ValidationError
    with pytest.raises(ValidationError, match="at least 3 frames"):
        Recipe(id="highlight-bloom", duration=.08)
    assert Recipe(id="highlight-bloom", duration=.1).duration == .1
    assert Recipe(id="hard-cut", duration=.08).duration == 0


@pytest.mark.parametrize("identity", ["film-burn", "lens-sweep"])
@pytest.mark.parametrize("direction", ["left", "right", "up", "down"])
def test_moving_light_travels_in_the_named_direction(identity, direction):
    black = np.zeros((90, 160, 3), dtype=np.uint8)
    c = Compositor(160, 90, recipe(identity, direction=direction, texture=0, chromatic=0,
                                  easing="linear", attack=.45, decay=.6))
    coordinate = c.x if direction in {"left", "right"} else c.y
    centroids = []
    for phase in (.35, .65):
        energy = c.render(black, black, phase).astype(float).mean(axis=-1)
        centroids.append(float((coordinate * energy).sum() / energy.sum()))
    shift = centroids[1] - centroids[0]
    assert shift < -.1 if direction in {"left", "up"} else shift > .1


def test_default_burn_is_localized_instead_of_lifting_every_shadow():
    defaults = next(row["defaults"] for row in catalog()["recipes"] if row["id"] == "film-burn")
    dark = np.full((90, 160, 3), 12, dtype=np.uint8)
    c = Compositor(160, 90, {**defaults, "texture": 0})
    result = c.render(dark, dark, .5)
    assert result[:, :8].mean() < 15, "Pixels well ahead of the burn should retain their black level"
    assert result[:, 65:95].mean() > 100, "The localized bright front must still be expressive"
    assert np.mean(result.mean(axis=-1) > 80) < .6


@pytest.mark.parametrize("direction", ["left", "right", "up", "down"])
def test_afterimage_prismatic_color_changes_both_horizontal_and_vertical_motion(direction):
    a = np.zeros((90, 160, 3), dtype=np.uint8)
    a[30:60, 55:105] = 255
    b = np.zeros_like(a)
    images = []
    for chromatic in (0, 1):
        c = Compositor(160, 90, recipe("afterimage-flash", direction=direction, intensity=0,
                                      ghost=1, spread=1, chromatic=chromatic))
        c.render(a, b, .49, frame_index=5)
        images.append(c.render(a, b, .6, frame_index=6))
    assert np.abs(images[0].astype(float) - images[1]).mean() > .5
    assert np.abs(images[1][..., 0].astype(float) - images[1][..., 2]).mean() > 1


def test_lens_geometry_rotates_with_the_frame_without_squashing_its_ghost_ring():
    black = np.zeros((90, 160, 3), dtype=np.uint8)
    horizontal = Compositor(160, 90, recipe("lens-sweep", direction="left", chromatic=1))
    vertical = Compositor(90, 160, recipe("lens-sweep", direction="up", chromatic=1))
    left = horizontal.render(black, black, .4)
    rotated = vertical.render(black.transpose(1, 0, 2), black.transpose(1, 0, 2), .4)
    assert np.max(np.abs(left.astype(float) - rotated.transpose(1, 0, 2))) <= 1


@pytest.mark.parametrize("spec,control", [(spec, control) for spec in catalog()["recipes"]
                         for control in spec["control_specs"] if control["key"] != "duration"],
                         ids=lambda value: value.get("id", value.get("key")))
def test_every_advertised_pixel_control_changes_its_effect(spec, control):
    a, b = pair(80, 46)
    key = control["key"]
    values = [control["min"], control["max"]] if control["type"] == "range" else [
        control["options"][0]["value"], control["options"][-1]["value"]]
    comps = [Compositor(80, 46, {**spec["defaults"], key: value}) for value in values]
    differences = []
    for index, position in enumerate((.15, .3, .45, .55, .7, .85)):
        outputs = [c.render(a, b, position, frame_index=index).astype(float) for c in comps]
        differences.append(np.abs(outputs[0] - outputs[1]).mean())
    assert max(differences) > .01, f'{spec["id"]}: advertised {key} has no observable effect'
