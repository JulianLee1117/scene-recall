"""Finding and following TV screens for screen effects (ADR-0108)."""

from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw

from pipeline.lab import screens

TRUTH = np.array([[130.0, 100.0], [360.0, 112.0], [352.0, 262.0], [136.0, 250.0]])


def _frame(shift: float = 0.0, rounded: bool = False):
    """A dark room with a grey set and a lit screen; the TV mask covers set and screen."""
    image = Image.new("RGB", (640, 360), (20, 20, 24))
    draw = ImageDraw.Draw(image)
    draw.rectangle([100 + shift, 80, 400 + shift, 285], fill=(60, 58, 55))
    if rounded:
        draw.rounded_rectangle([140 + shift, 110, 350 + shift, 250], radius=24, fill=(210, 220, 230))
    else:
        draw.polygon([tuple(p + [shift, 0]) for p in TRUTH], fill=(210, 220, 230))
    tv = np.zeros((360, 640), np.uint8)
    tv[80:286, int(100 + shift):int(401 + shift)] = 255
    return np.asarray(image), tv


def test_a_lit_screens_corners_are_found_from_its_edges():
    frame, tv = _frame()
    found = screens.screen_corners(frame, tv)
    assert found is not None and np.max(np.abs(found - TRUTH)) < 3
    frame, tv = _frame(rounded=True)
    found = screens.screen_corners(frame, tv)
    sharp = np.array([[140, 110], [350, 110], [350, 250], [140, 250]], float)
    assert found is not None and np.max(np.abs(found - sharp)) < 3          # rounded corners don't pull them inward
    assert screens.screen_corners(frame, np.zeros_like(tv)) is None


def test_following_holds_through_misses_and_jumps():
    frames = [_frame(4.0 * k) for k in range(8)]
    masks = {id(f): tv for f, tv in frames}
    masks[id(frames[3][0])] = np.zeros((360, 640), np.uint8)                # a missed frame
    jumpy = frames[5][0].copy()
    masks[id(jumpy)] = np.roll(frames[5][1], 200, axis=1)                   # a far-off TV for one frame
    pictures = [f for f, _ in frames]
    pictures[5] = jumpy
    track = screens.follow_screen(pictures, lambda frame: masks[id(frame)], window=1)
    for k, corners in enumerate(track):
        assert np.max(np.abs(corners - (TRUTH + [4.0 * k, 0]))) < 6, k


def test_given_corners_follow_a_moving_zooming_set_while_its_picture_changes():
    rng = np.random.default_rng(3)
    texture = Image.fromarray((rng.random((90, 160)) * 255).astype(np.uint8)).resize((640, 360), Image.BICUBIC)
    corners = np.array([[220.0, 120.0], [420.0, 120.0], [420.0, 250.0], [220.0, 250.0]])
    frames, truth = [], []
    for k in range(10):
        scale, shift = 1 + 0.01 * k, np.array([2.0 * k, -1.0 * k])         # a slow zoom and drift
        centre = np.array([320.0, 180.0])
        inverse = (1 / scale, 0, centre[0] - (centre[0] + shift[0]) / scale, 0, 1 / scale, centre[1] - (centre[1] + shift[1]) / scale)
        frame = np.asarray(texture.transform((640, 360), Image.AFFINE, inverse, resample=Image.BICUBIC)).copy()
        moved = (corners - centre) * scale + centre + shift
        x0, y0 = moved.min(axis=0).astype(int) + 2
        x1, y1 = moved.max(axis=0).astype(int) - 2
        frame[y0:y1, x0:x1] = (rng.random((y1 - y0, x1 - x0)) * 255).astype(np.uint8)    # the screen's own picture changes
        frames.append(frame)
        truth.append(moved)
    track = screens.follow_corners(frames, corners)
    for k, (found, expected) in enumerate(zip(track, truth)):
        assert np.max(np.abs(found - expected)) < 1.5, k
