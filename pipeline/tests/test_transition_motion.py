"""Motion continuity, frame-timed exposure, and captured-border ownership."""
import numpy as np
import pytest

from pipeline.transitions.compositor import Compositor, whip_motion
from pipeline.transitions.contracts import Recipe


@pytest.mark.parametrize("cut", [.15, .3, .5, .7, .85])
@pytest.mark.parametrize("easing", ["linear", "smooth", "snappy"])
def test_whip_motion_keeps_midpoint_and_continuous_velocity_and_flat_endpoints(cut, easing):
    position = lambda p: whip_motion(p, cut, easing, .15)[0]
    assert position(0) == 0 and position(1) == 1
    assert position(cut) == pytest.approx(.5, abs=1e-10)
    eps = 1e-5
    left = (position(cut) - position(cut - eps)) / eps
    right = (position(cut + eps) - position(cut)) / eps
    assert abs(left - right) < .01
    assert abs((position(eps) - position(0)) / eps) < 1e-4
    assert abs((position(1) - position(1 - eps)) / eps) < 1e-4
    assert abs((position(2 * eps) - 2 * position(eps) + position(0)) / eps ** 2) < .1


def test_whip_rebound_is_one_small_overshoot_and_overscan_can_cover_it():
    positions = np.array([whip_motion(p, .5, "snappy", 1)[0] for p in np.linspace(0, 1, 1001)])
    assert 1.025 < positions.max() <= 1.035001
    over = positions > 1 + 1e-5
    assert np.count_nonzero(np.diff(over.astype(int)) == 1) == 1
    assert positions.min() == 0 and positions[-1] == 1


def test_shutter_depends_on_actual_overlap_frames_and_closes_at_endpoints():
    recipe = Recipe(id="whip-pan", duration=.4, intensity=1, softness=.04).model_dump()
    short, long = [Compositor(160, 90, recipe, overlap_frames=n) for n in (8, 24)]
    assert short._exposure(.5)[1] - short._exposure(.5)[0] == pytest.approx(1 / 7)
    assert long._exposure(.5)[1] - long._exposure(.5)[0] == pytest.approx(1 / 23)
    assert short._exposure(0) == (0, 0) and short._exposure(1) == (1, 1)
    frame = np.zeros((90, 160, 3), dtype=np.uint8)
    frame[:, 80:] = 255
    blur_widths = []
    for compositor in (short, long):
        result = compositor.render(frame, frame, .45)
        blur_widths.append(((result[45, :, 0] > 15) & (result[45, :, 0] < 240)).sum())
    assert blur_widths[0] > blur_widths[1] * 1.8


@pytest.mark.parametrize("direction", ["left", "right", "up", "down"])
def test_camera_whip_carries_both_whole_shots_in_the_same_screen_direction(direction):
    horizontal = direction in {"left", "right"}
    frame = np.zeros((121, 161, 3), dtype=np.uint8)
    if horizontal:
        frame[:, 78:83] = 255
    else:
        frame[58:63] = 255
    c = Compositor(161, 121, Recipe(id="whip-pan", direction=direction, intensity=0,
                         softness=.04, overscan=0, rebound=0).model_dump())
    positions = []
    for progress in (.25, .42, .58, .75):
        pixels = c.render(frame, frame, progress)[..., 0].astype(float)
        profile = pixels.sum(axis=0 if horizontal else 1)
        positions.append(float(np.sum(np.arange(len(profile)) * profile) / profile.sum()))
    expected = -1 if direction in {"left", "up"} else 1
    assert (positions[1] - positions[0]) * expected > 1
    assert (positions[3] - positions[2]) * expected > 1


def test_camera_whip_uses_a_short_whole_frame_crossover_and_captured_pixel_crop():
    c = Compositor(161, 91, Recipe(id="whip-pan", softness=.04, rebound=1, overscan=0).model_dump())
    for progress in np.linspace(0, 1, 101):
        for outgoing in (True, False):
            offset, zoom = c._whip_pose(progress, outgoing)
            assert .5 + offset - .5 / zoom >= -1e-9
            assert .5 + offset + .5 / zoom <= 1 + 1e-9
    assert c._whip_pose(.5, True)[0] == pytest.approx(.12)
    assert c._whip_pose(.5, False)[0] == pytest.approx(-.12)


@pytest.mark.parametrize("direction", ["left", "right", "up", "down"])
def test_rebound_preserves_monotonic_captured_edges_without_mirror_fold(direction):
    horizontal = direction in {"left", "right"}
    width, height = 161, 91
    x, y = np.meshgrid(np.linspace(20, 230, width), np.linspace(20, 230, height))
    gradient = np.rint(x if horizontal else y).astype(np.uint8)
    frame = np.repeat(gradient[..., None], 3, axis=-1)
    compositor = Compositor(width, height, Recipe(id="whip-pan", direction=direction, intensity=0,
                              rebound=1, overscan=0).model_dump())
    for phase in (.88, .93, .97):
        output = compositor.render(frame, frame, phase)
        scan = output[height // 2, :, 0] if horizontal else output[:, width // 2, 0]
        assert np.diff(scan.astype(int)).min() >= 0, "A reflected outer strip must not fold back into the visible rebound"


@pytest.mark.parametrize("anchor", [0., .25, .75, 1.])
def test_zoom_tracking_extreme_anchors_never_exposes_a_fold(anchor):
    frame = np.repeat(np.rint(np.linspace(10, 240, 161))[None, :, None], 91, axis=0)
    frame = np.repeat(frame, 3, axis=2).astype(np.uint8)
    compositor = Compositor(161, 91, Recipe(id="crash-zoom", anchor_ax=anchor, anchor_bx=anchor,
                              intensity=0, rebound=1, overscan=0).model_dump())
    for progress in (.1, .3, .5, .7, .9, .98):
        output = compositor.render(frame, frame, progress)
        assert np.diff(output[45, :, 0].astype(int)).min() >= 0
    eps = 1e-5
    assert (compositor._zoom_pose(eps)[0] - 1) / eps < 1e-4
    assert (compositor._zoom_pose(1 - eps)[0] - 1) / eps < 1e-4


@pytest.mark.parametrize("cut", [.15, .5, .85])
def test_zoom_velocity_is_continuous_through_off_center_peak(cut):
    compositor = Compositor(161, 91, Recipe(id="crash-zoom", cut_phase=cut).model_dump())
    scale = lambda p: compositor._zoom_pose(p)[0]
    eps = 1e-6
    left = (scale(cut) - scale(cut - eps)) / eps
    right = (scale(cut + eps) - scale(cut)) / eps
    assert abs(left - right) < .005


def test_fast_radial_shutter_does_not_break_a_thin_highlight_into_ghost_taps():
    frame = np.zeros((90, 161, 3), dtype=np.uint8)
    frame[:, 110:112] = 255
    compositor = Compositor(161, 90, Recipe(id="crash-zoom", duration=.1, intensity=1,
                              zoom_amount=5, overscan=0, rebound=0).model_dump())
    scan = compositor.render(frame, frame, .2)[45, :, 0]
    visible = np.flatnonzero(scan > 3)
    assert len(visible) > 20, "The fast shutter must spread the highlight over its trajectory"
    assert np.all(np.diff(visible) == 1), "Finite shutter samples must not leave disconnected light ghosts"
    # Past the lead lobe, temporal sample spacing must not become periodic
    # brightness peaks along an otherwise continuous radial highlight trail.
    tail = scan[int(np.argmax(scan)):visible[-1] + 1].astype(int)
    assert np.max(np.diff(tail)) <= 3
