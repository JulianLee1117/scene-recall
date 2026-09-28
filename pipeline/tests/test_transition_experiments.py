"""Observable image properties of the optional flat-image lens experiments."""
import numpy as np
import pytest

from pipeline.transitions.compositor import Compositor, defocus
from pipeline.transitions.contracts import Recipe


def recipe(identity, **values):
    # Keep these image tests usable independently of catalog registration.
    result = Recipe().model_dump()
    result.update(id=identity, peak_phase=.5, attack=.42, decay=.42, cut_phase=.47, softness=.04, **values)
    return result


def chart(width=161, height=91):
    x, y = np.meshgrid(np.arange(width), np.arange(height))
    pattern = (((x // 7 + y // 7) % 2) * 155 + 40).astype(np.uint8)
    return np.stack([pattern, pattern, pattern], axis=-1)


@pytest.mark.parametrize("identity", ["focus-pull", "prism-push"])
@pytest.mark.parametrize("shape", [(161, 91), (91, 161)])
def test_experimental_lenses_preserve_endpoints_and_do_not_expose_black_edges(identity, shape):
    w, h = shape
    a, b = chart(w, h), np.flip(chart(w, h), axis=1).copy()
    compositor = Compositor(w, h, recipe(identity, intensity=1, zoom_amount=5, chromatic=1))
    assert np.array_equal(compositor.render(a, b, 0), a)
    assert np.array_equal(compositor.render(a, b, 1), b)
    for time in (.1, .3, .5, .7, .9):
        output = compositor.render(a, b, time)
        assert output.shape == a.shape and output.dtype == np.uint8
        assert output.min() >= 38, "A lens operation must not introduce black borders."


def test_defocus_softens_detail_without_raising_a_flat_background():
    original = chart()
    focused = Compositor(161, 91, recipe("focus-pull", intensity=0, spread=0)).render(original, original, .5)
    blurred = Compositor(161, 91, recipe("focus-pull", intensity=1, spread=0)).render(original, original, .5)
    assert np.array_equal(focused, original)
    assert blurred.astype(float).std() < focused.astype(float).std() * .6
    flat = np.full_like(original, 35)
    assert np.abs(Compositor(161, 91, recipe("focus-pull", intensity=1, spread=0)).render(flat, flat, .5).astype(int) - 35).max() <= 1


def test_defocus_radius_changes_continuously_at_integer_boundaries():
    linear = chart().astype(np.float32) / 255
    before, after = defocus(linear, 2 - 1e-4), defocus(linear, 2 + 1e-4)
    assert np.abs(before - after).max() < .0002


def test_prism_separation_is_optional_and_keeps_the_anchor_neutral():
    original = chart()
    neutral = Compositor(161, 91, recipe("prism-push", chromatic=0, zoom_amount=1.25)).render(original, original, .5)
    colored = Compositor(161, 91, recipe("prism-push", chromatic=1, zoom_amount=1.25)).render(original, original, .5)
    assert np.array_equal(neutral[..., 0], neutral[..., 2])
    assert np.abs(colored[..., 0].astype(int) - colored[..., 2]).max() > 10
    assert np.array_equal(colored[45, 80], original[45, 80])


def test_prism_bend_and_anchor_change_geometry_independently():
    original = chart()
    settings = recipe("prism-push", chromatic=0, intensity=0, zoom_amount=1.25)
    plain = Compositor(161, 91, settings).render(original, original, .5)
    bent = Compositor(161, 91, {**settings, "intensity": 1}).render(original, original, .5)
    moved = Compositor(161, 91, {**settings, "anchor_bx": .25, "anchor_by": .25}).render(original, original, .5)
    assert np.abs(plain.astype(float) - bent).mean() > 5
    assert np.abs(plain.astype(float) - moved).mean() > 5
