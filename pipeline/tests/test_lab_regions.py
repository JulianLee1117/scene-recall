"""Regions resolve a small spec into per-frame masks any treatment can use."""
import numpy as np
import pytest
from pydantic import ValidationError

from pipeline.lab import regions


def _frames(n=4, size=40):
    return [np.zeros((size, size, 3), np.uint8) for _ in range(n)]


def test_all_none_and_box_need_no_detector():
    frames = _frames()
    assert regions.resolve(frames, {"kind": "all"}) == [None] * 4
    none = regions.resolve(frames, {"kind": "none"})
    assert all(not m.any() for m in none)
    box = regions.resolve(frames, {"kind": "box", "x": .25, "y": .5, "w": .5, "h": .25})
    assert box[0].sum() == 20 * 10 and box[0][20:30, 10:30].all() and not box[0][:20].any()
    with pytest.raises(ValidationError):
        regions.Region(kind="subject", unknown=1)


class _FakeSegmenter:
    """Two instances on every frame but the third, which the vote must bridge."""
    def instances(self, frame, classes):
        h, w = frame.shape[:2]
        if frame[0, 0, 0] == 7:
            return []
        big = np.zeros((h, w), bool); big[5:25, 5:25] = True
        small = np.zeros((h, w), bool); small[30:34, 30:34] = True
        return [big, small]


def test_subject_background_largest_dilate_and_temporal_vote(monkeypatch):
    monkeypatch.setattr(regions, "segmentation_available", lambda: True)
    frames = _frames(5)
    frames[2][0, 0, 0] = 7                                        # the detector misses this frame
    seg = _FakeSegmenter()
    subject = regions.resolve(frames, {"kind": "subject"}, segmenter=seg)
    assert subject[0][10, 10] and subject[0][31, 31] and not subject[0][0, 0]
    assert subject[2][10, 10]                                     # bridged by the neighbours
    largest = regions.resolve(frames, {"kind": "subject", "largest": True}, segmenter=seg)
    assert largest[0][10, 10] and not largest[0][31, 31]
    grown = regions.resolve(frames, {"kind": "subject", "largest": True, "dilate": 3}, segmenter=seg)
    assert grown[0][3, 3] and not largest[0][3, 3]
    background = regions.resolve(frames, {"kind": "background"}, segmenter=seg)
    assert background[0][0, 0] and not background[0][10, 10]
    assert regions.coverage(largest) == pytest.approx(400 / 1600)
    assert regions.soft(largest[0], 2).max() <= 1.0


def test_without_segmentation_subject_resolves_to_everything(monkeypatch):
    monkeypatch.setattr(regions, "segmentation_available", lambda: False)
    assert regions.resolve(_frames(2), {"kind": "subject"}) == [None, None]


class _FakeDepth:
    """Nearness rises left to right, the same on every frame."""
    def shot(self, frames, progress=None):
        h, w = frames[0].shape[:2]
        return [np.tile(np.linspace(0, 1, w, dtype=np.float32), (h, 1)) for _ in frames]


def test_near_far_by_depth_and_keep_live(monkeypatch):
    from pipeline.lab import depth
    monkeypatch.setattr(depth, "DepthEstimator", lambda: _FakeDepth())
    frames = _frames(3)
    near = regions.resolve(frames, {"kind": "near", "share": .25})
    assert near[0][:, 35:].all() and not near[0][:, :25].any()          # the rightmost quarter is nearest
    far = regions.resolve(frames, {"kind": "far", "share": .25})
    assert far[0][:, :25].all() and not far[0][:, 35:].any()
    treated = [np.full_like(f, 200) for f in frames]
    out = regions.keep_live(frames, treated, {"kind": "near", "share": .25}, edge=0)
    assert out[0][20, 2, 0] == 200 and out[0][20, 38, 0] == 0             # film shows through the near layer
    assert regions.keep_live(frames, treated, {"kind": "none"}) is treated
    assert regions.keep_live(frames, treated, None) is treated


class _FakeMatte:
    """A soft person alpha: a disc with a feathered edge, the same on every frame."""
    def alphas(self, frames, progress=None):
        h, w = frames[0].shape[:2]
        yy, xx = np.mgrid[0:h, 0:w]
        d = np.hypot(yy - h / 2, xx - w / 2)
        return [np.clip((12 - d) / 4, 0, 1).astype(np.float32) for _ in frames]


def test_person_regions_use_the_matte_and_the_live_layer_keeps_its_soft_edge(monkeypatch):
    from pipeline.lab import matte
    monkeypatch.setattr(matte, "available", lambda assets_dir=None: True)
    monkeypatch.setattr(matte, "PersonMatte", lambda: _FakeMatte())
    frames = _frames(3)
    subject = regions.resolve(frames, {"kind": "subject", "classes": ["person"]})
    assert subject[0][20, 20] and not subject[0][2, 2]
    background = regions.resolve(frames, {"kind": "background", "classes": ["person"]})
    assert not background[0][20, 20] and background[0][2, 2]
    soft = regions.resolve_soft(frames, {"kind": "subject", "classes": ["person"]})
    assert 0 < soft[0][20, 30] < 1                                           # the matte's own edge survives
    treated = [np.full_like(f, 200) for f in frames]
    out = regions.keep_live(frames, treated, {"kind": "subject", "classes": ["person"]})
    # the live edge is hardened and pulled inward (no halo of real background around the figure):
    # the matte's half-way pixel is now treated, a pixel inside still blends, the centre is film
    assert out[0][20, 20, 0] == 0 and out[0][2, 2, 0] == 200 and out[0][20, 30, 0] == 200 and 0 < out[0][20, 28, 0] < 200
    # a car region never asks the matte
    monkeypatch.setattr(regions, "segmentation_available", lambda: False)
    assert regions.resolve(frames, {"kind": "subject", "classes": ["car"]}) == [None] * 3
