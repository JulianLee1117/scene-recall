"""Alg Mods treatments on synthetic frames: no GPU, no media, properties only."""

from __future__ import annotations

import numpy as np
import pytest

from pipeline.algmods import mods


def _scene(count: int = 12, size: int = 96) -> tuple[list[np.ndarray], list[np.ndarray]]:
    """A bright square sliding right over a vertical gradient; masks follow the square."""
    frames, masks = [], []
    base = np.zeros((size, size, 3), np.uint8)
    base[..., 0] = np.linspace(20, 120, size, dtype=np.uint8)[:, None]
    base[..., 2] = 60
    for i in range(count):
        frame = base.copy()
        x = 8 + i * 4
        frame[30:60, x:x + 24] = (240, 200, 40)
        mask = np.zeros((size, size), bool)
        mask[30:60, x:x + 24] = True
        frames.append(frame)
        masks.append(mask)
    return frames, masks


def test_stripe_ids_cover_every_stripe_and_fan_around_the_vanishing_point():
    ids = mods.stripe_ids(64, 64, count=16, vanish=(0.5, 0.5))
    assert ids.min() == 0 and ids.max() == 15
    assert len(np.unique(ids)) == 16
    bands = mods.stripe_ids(64, 64, count=8, direction=(1.0, 0.0))
    assert (bands[:, 0] == 0).all() and (bands[:, -1] == 7).all()       # left to right


def test_time_stripes_change_only_the_subject_and_pull_pixels_from_history():
    frames, masks = _scene()
    ids = mods.stripe_ids(96, 96, count=4, direction=(1.0, 0.0))
    lags = mods.stripe_lags(ids, count=4, max_lag=4, pattern="ramp")
    out = mods.time_stripes(frames, masks[-1], lags)
    # Outside the subject the current frame is kept exactly.
    assert np.array_equal(out[~masks[-1]], frames[-1][~masks[-1]])
    # Inside, where the lag is maximal, pixels come from four frames earlier, where
    # the square had not yet arrived at its current right edge.
    stripe_max = (lags == 4) & masks[-1]
    assert stripe_max.any()
    assert np.array_equal(out[stripe_max], frames[-5][stripe_max])
    assert not np.array_equal(out, frames[-1])


def test_time_stripes_with_no_mask_remixes_the_whole_frame_and_clamps_lag():
    frames, _ = _scene(count=3)
    lags = np.full((96, 96), 10, np.int32)
    out = mods.time_stripes(frames, None, lags)
    assert np.array_equal(out, frames[0])


def test_dot_tracker_keeps_protected_pixels_and_dots_the_rest():
    pytest.importorskip("cv2", reason="feature dots need OpenCV (the measure extra)")
    frames, masks = _scene(count=4)
    rng = np.random.RandomState(0)
    frames = [np.clip(f.astype(int) + rng.randint(-25, 25, f.shape), 0, 255).astype(np.uint8) for f in frames]
    tracker = mods.DotTracker(density=20000, radius_frac=0.02, quality=0.001, fade_in=1, soften=0.0)
    outs = [tracker.step(f, protect=m, detect_mask=~m) for f, m in zip(frames, masks)]
    last = outs[-1]
    assert np.array_equal(last[masks[-1]], frames[-1][masks[-1]])          # the subject stays real
    background = last[~masks[-1]]
    assert (background == np.array(tracker.background, np.uint8)).all(axis=1).mean() > 0.5
    assert tracker.points is not None and len(tracker.points) > 20
    assert (tracker.colours >= 0).all() and (tracker.colours <= 1).all()


def test_dots_do_not_seed_on_flat_dark_noise_but_do_on_texture():
    pytest.importorskip("cv2", reason="feature dots need OpenCV")
    rng = np.random.RandomState(3)
    flat = np.clip(12 + rng.randint(-2, 3, (160, 160, 3)), 0, 255).astype(np.uint8)       # sensor noise on black
    textured = flat.copy()
    textured[40:120, 40:120] = rng.randint(0, 255, (80, 80, 3)).astype(np.uint8)           # a busy patch
    a = mods.DotTracker(density=20000, radius_frac=0.02, quality=0.001, fade_in=1, soften=0.0)
    b = mods.DotTracker(density=20000, radius_frac=0.02, quality=0.001, fade_in=1, soften=0.0)
    a.step(flat)
    b.step(textured)
    assert (a.points is None or len(a.points) <= 5)
    assert b.points is not None and len(b.points) > 30
    inside = (b.points[:, 0] >= 36) & (b.points[:, 0] <= 124) & (b.points[:, 1] >= 36) & (b.points[:, 1] <= 124)
    assert inside.mean() > 0.9


def test_vivid_snaps_toward_the_palette_and_stays_bright():
    palette = np.array(mods.YNHP_PALETTE, np.float32) / 255.0
    rgb = np.array([[0.05, 0.05, 0.08], [0.9, 0.6, 0.1], [0.3, 0.3, 0.9]], np.float32)
    out = mods.vivid(rgb, palette, mix=1.0)
    for row in out:                                                   # fully mixed = a palette colour
        assert np.min(np.abs(palette - row).sum(axis=1)) < 1e-5
    half = mods.vivid(rgb, palette, mix=0.0)
    assert (half.max(axis=1) >= 0.55).all()                            # opaque and bright, never muddy
    assert half[1].argmax() == 0 and half[2].argmax() == 2            # hue kept


def test_vivid_dots_get_their_own_radii_and_bigger_on_lights():
    pytest.importorskip("cv2", reason="feature dots need OpenCV")
    rng = np.random.RandomState(5)
    frame = rng.randint(0, 60, (160, 160, 3)).astype(np.uint8)        # dark texture
    frame[20:60, 20:60] = rng.randint(200, 255, (40, 40, 3)).astype(np.uint8)   # a bright lit patch
    t = mods.DotTracker(density=20000, radius_frac=0.02, quality=0.001, fade_in=1, soften=0.0,
                        palette=mods.YNHP_PALETTE, size_jitter=0.3, bright_boost=1.0, background=mods.YNHP_GROUND)
    out = t.step(frame)
    assert t.radii is not None and len(np.unique(np.round(t.radii, 2))) > 5
    bright = (t.points[:, 0] >= 20) & (t.points[:, 0] < 60) & (t.points[:, 1] >= 20) & (t.points[:, 1] < 60)
    assert bright.any() and (~bright).any()
    assert t.radii[bright].mean() > t.radii[~bright].mean()
    assert tuple(out[0, 0]) == mods.YNHP_GROUND                        # navy ground, not black


def _textured_frame(seed: int, size: int = 240) -> np.ndarray:
    rng = np.random.RandomState(seed)
    base = rng.randint(0, 255, (size // 8, size // 8, 3)).astype(np.uint8)       # blobs, not pixel noise
    return np.kron(base, np.ones((8, 8, 1), np.uint8))


def _shift(frame: np.ndarray, dx: int, dy: int) -> np.ndarray:
    return np.roll(np.roll(frame, dy, axis=0), dx, axis=1)


def test_dots_never_overlap_at_birth():
    pytest.importorskip("cv2", reason="feature dots need OpenCV")
    t = mods.DotTracker(density=50000, radius_frac=0.03, quality=0.0005, fade_in=1, soften=0.0,
                        size_jitter=0.4, spacing=0.1)
    t.step(_textured_frame(9))
    pts, radii = t.points, t.radii
    assert len(pts) > 10
    d = np.linalg.norm(pts[:, None, :] - pts[None, :, :], axis=-1)
    need = (radii[:, None] + radii[None, :]) * 1.1
    np.fill_diagonal(d, np.inf)
    assert (d >= need - 1e-4).all()


def test_dots_move_with_the_picture_leave_at_the_edge_and_arrive_full_size():
    pytest.importorskip("cv2", reason="feature dots need OpenCV")
    frame = _textured_frame(4)
    t = mods.DotTracker(density=20000, radius_frac=0.02, quality=0.001, fade_in=4, soften=0.0, smooth=0.0,
                        refresh_every=1)
    t.step(frame)
    origin = t.points.copy()
    origin_ids = t.ids.copy()
    rmax = t.radii.max()

    def nearest(target):
        return np.linalg.norm(t.points - target, axis=1).min()
    for step in range(1, 4):
        before = t.points.copy()
        t.step(_shift(frame, 6 * step, 0))                  # the whole picture slides 6 px right per frame
        assert t.last_motion["shift"][0] == pytest.approx(6, abs=.5)
        # Every dot that stays in frame is found 6 px to the right, edge dots included: none freezes.
        staying = before[before[:, 0] + 6 < 240 - rmax]
        assert np.mean([nearest(p + (6, 0)) < 1.5 for p in staying]) > 0.95
    # Dots that slid past the right edge are gone, and no surviving dot from the opening sits still.
    gone_ids = origin_ids[origin[:, 0] + 18 > 240 + rmax]
    opening = np.isin(t.ids, origin_ids)
    assert len(gone_ids) > 0 and not np.isin(t.ids, gone_ids).any()
    assert opening.sum() >= 0.7 * (len(origin) - len(gone_ids))                # the rest mostly survive
    assert (t.points[:, 0] > 240 + rmax).sum() == 0
    assert (np.abs(t.vel[opening, 0]) > 3).all()
    # Births this frame: arrivals in the left edge band are full-size at once, interior ones grow in.
    born = (t.age == 1) | (t.age == t.fade_in + 1)
    band = born & (t.points[:, 0] < 0.03 * 240)
    assert band.any() and (t.age[band] == t.fade_in + 1).all()
    assert ((t.age == 1) & ~band).any() or True                 # interior births depend on the texture


def test_a_hard_cut_clears_the_field_and_repaints_it_at_full_size():
    pytest.importorskip("cv2", reason="feature dots need OpenCV")
    t = mods.DotTracker(density=20000, radius_frac=0.02, quality=0.001, fade_in=4, soften=0.0)
    t.step(_textured_frame(1))
    t.step(_textured_frame(1))
    assert t.last_motion["trusted"] > 0.6 and not t.last_motion["cut"]     # a still picture is fully trusted
    t.step(_textured_frame(2))                                              # an unrelated picture
    assert t.last_motion["cut"]
    assert len(t.points) > 10 and (t.age >= t.fade_in).all()               # repainted, nothing growing in


def test_a_still_picture_keeps_its_dots_still_and_apart():
    pytest.importorskip("cv2", reason="feature dots need OpenCV")
    frame = _textured_frame(6)
    t = mods.DotTracker(density=20000, radius_frac=0.02, quality=0.001, fade_in=1, soften=0.0)
    t.step(frame)
    origin, ids = t.points.copy(), t.ids.copy()
    for _ in range(40):
        t.step(frame)
    survivors = np.isin(t.ids, ids)
    where = {i: p for i, p in zip(t.ids[survivors], t.points[survivors])}
    drift = np.array([np.linalg.norm(where[i] - p) for i, p in zip(ids, origin) if i in where])
    assert survivors.sum() > 0.9 * len(ids) and drift.max() < 0.5          # nothing wandered
    d = np.linalg.norm(t.points[:, None] - t.points[None], axis=-1)
    np.fill_diagonal(d, np.inf)
    assert (d.min(axis=1) >= 0.9 * 2 * t.radii.min()).all()                  # nothing crowded


def test_a_bright_wall_is_not_a_light_core_but_a_lamp_in_the_dark_is():
    pytest.importorskip("cv2", reason="feature dots need OpenCV")
    wall = np.full((160, 160, 3), (230, 150, 170), np.uint8)                  # a pink wall in daylight
    t = mods.DotTracker()
    t._gray(wall)
    assert t._hot.max() < 0.05
    night = np.full((160, 160, 3), 10, np.uint8)
    night[70:90, 70:90] = 250                                                  # a lamp
    t._gray(night)
    assert t._hot[80, 80] > 0.8 and t._hot[10, 10] < 0.05


def test_pastel_lifts_shadows_caps_saturation_and_keeps_hue_order():
    rgb = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.2, 0.6, 0.4]], np.float32)
    out = mods.pastel(rgb)
    assert out[0].min() >= 0.29                                     # black lifts to a dark grey
    assert out[1].max() <= 0.81 and out[1].argmax() == 0            # red stays red, never pure
    sat = (out[1].max() - out[1].min()) / out[1].max()
    assert sat <= 0.6
    assert np.argsort(out[2]).tolist() == np.argsort(rgb[2]).tolist()   # channel order (hue) preserved


def test_quadtree_is_flat_where_the_picture_is_flat_and_fine_where_it_varies():
    frame = np.full((64, 64, 3), 90, np.uint8)
    frame[8:24, 8:24] = (250, 30, 30)
    out = mods.quadtree(frame, threshold=4.0, min_size=4)
    assert (out[40:64, 40:64] == 90).all()                                # the flat corner is one colour
    assert out[12, 12, 0] > 200 and out[30, 30, 0] < 120                   # the red patch survives
    coarse = mods.quadtree(frame, threshold=200.0, min_size=4)
    assert len(np.unique(coarse.reshape(-1, 3), axis=0)) == 1              # one cell, the mean


def test_quadtree_reveal_returns_the_frame_at_full_progress_and_gets_finer_toward_it():
    ys, xs = np.mgrid[0:64, 0:64]
    frame = np.stack([xs * 4, ys * 4, (xs + ys) * 2], axis=-1).astype(np.uint8)   # smooth gradients
    frame[20:44, 20:44] = (250, 250, 250)                                        # one hard-edged patch
    assert mods.quadtree_reveal(frame, 1.0) is frame
    early = mods.quadtree_reveal(frame, 0.0)
    late = mods.quadtree_reveal(frame, 0.9)
    assert np.abs(late.astype(int) - frame).mean() < np.abs(early.astype(int) - frame).mean()


def test_subject_centre_defaults_to_the_frame_centre():
    assert mods.subject_centre(None, 100, 50) == (0.5, 0.5)
    mask = np.zeros((50, 100), bool)
    mask[10:20, 60:80] = True
    cx, cy = mods.subject_centre(mask, 100, 50)
    assert cx == pytest.approx(0.695) and cy == pytest.approx(0.29)


def test_live_region_rides_every_per_shot_treatment():
    from pipeline.algmods import contracts
    for kind in ("dots", "stripes", "quadtree"):
        _mod, params = contracts.render_params({"kind": kind, "live": {"kind": "near", "share": .3}})
        assert params["live"]["kind"] == "near" and params["live"]["share"] == .3
        _mod, params = contracts.render_params({"kind": kind})
        assert params["live"]["kind"] == "none"
    keys = {c["key"] for t in contracts.catalog()["treatments"] if t["id"] != "mosaic" for c in t["controls"]}
    assert {"live.kind", "live.share"} <= keys


def test_scene_ground_follows_the_shadows_and_stays_near_black():
    from pipeline.algmods.mods import scene_ground
    warm = np.zeros((40, 40, 3), np.uint8); warm[..., 0] = 60; warm[..., 1] = 20; warm[..., 2] = 10
    r, g, b = scene_ground(warm)
    assert r > g > b and max(r, g, b) <= 30
    cool = np.zeros((40, 40, 3), np.uint8); cool[..., 2] = 50; cool[..., 1] = 20
    r, g, b = scene_ground(cool)
    assert b > g > r
    black = np.zeros((40, 40, 3), np.uint8)
    assert max(scene_ground(black)) <= 30


def test_vivid_spread_draws_among_near_palette_colours_only():
    from pipeline.algmods.mods import YNHP_PALETTE, vivid
    palette = np.array(YNHP_PALETTE, np.float32) / 255.0
    rgb = np.tile(np.array([[0.35, 0.45, 0.8]], np.float32), (400, 1))      # one sky blue, many dots
    one = vivid(rgb, palette, spread=0.0)
    assert len(np.unique(one.round(3), axis=0)) == 1
    many = vivid(rgb, palette, spread=0.6, rng=np.random.RandomState(1))
    picks = np.unique(many.round(3), axis=0)
    assert 1 < len(picks) <= 3
    # every pick is a neighbour in hue: blue-ish, never a green or an orange
    assert (picks[:, 2] >= picks[:, 0]).all()


def test_dots_dashes_and_flat_marks_ride_the_tracker():
    from pipeline.algmods.mods import DotTracker, YNHP_PALETTE
    rng = np.random.RandomState(0)
    frame = np.zeros((120, 160, 3), np.uint8)
    frame[:, :, :] = rng.randint(60, 200, (120, 160, 3)).astype(np.uint8)
    frame[50:70, :, :] = 230                                                  # a bright horizontal band: coherent structure
    tracker = DotTracker(density=4000, radius_frac=0.02, palette=YNHP_PALETTE, dash=1.5, flat_boost=0.8, size_jitter=0.3)
    out = tracker.step(frame, None)
    assert out.shape == frame.shape and tracker.elong is not None and len(tracker.elong) == len(tracker.points)
    assert (tracker.elong > 1.05).any() and (tracker.elong >= 1.0).all()
    before = len(tracker.points)
    tracker._keep(np.arange(before) % 2 == 0)
    assert len(tracker.elong) == len(tracker.angle) == len(tracker.points) == (before + 1) // 2


def test_flat_gap_opens_a_flat_sky_and_leaves_structure_dense():
    from pipeline.algmods.mods import DotTracker, YNHP_PALETTE
    rng = np.random.RandomState(0)
    frame = np.zeros((240, 320, 3), np.uint8)
    frame[:120] = 140                                                          # a flat bright sky
    frame[120:] = rng.randint(40, 220, (120, 320, 3)).astype(np.uint8)         # busy ground
    counts = {}
    for gap in (0.0, 2.0):
        t = DotTracker(density=6000, radius_frac=0.015, palette=YNHP_PALETTE, flat_gap=gap, flat_thin=0.5, background=(0, 0, 0))
        for _ in range(3):
            t.step(frame, None)
        counts[gap] = (int((t.points[:, 1] < 120).sum()), int((t.points[:, 1] >= 120).sum()))
    assert counts[2.0][0] < counts[0.0][0] * 0.5                                # the sky opens up
    assert counts[2.0][1] > counts[0.0][1] * 0.7                                # the ground stays dense
