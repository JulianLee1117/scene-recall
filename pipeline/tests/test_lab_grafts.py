"""Grafts: a plan is plain data; windows follow landmarks; entrances differ; schedules follow beats."""
from pathlib import Path

import numpy as np
import pytest
from pydantic import ValidationError

from pipeline.algmods.composite import DonorFace, FacePoints
from pipeline.lab import grafts


def _host(w=320, h=240):
    return FacePoints(eyes=np.array([[120.0, 100.0], [200.0, 100.0]]), nose=np.array([160.0, 150.0]))


def _donor(tmp_path):
    pytest.importorskip("cv2")
    from PIL import Image
    rng = np.random.RandomState(3)
    img = rng.randint(40, 220, (240, 320, 3)).astype(np.uint8)
    path = tmp_path / "d.webp"
    Image.fromarray(img).save(path)
    return DonorFace("f" * 64, 1, Path(path), FacePoints(eyes=np.array([[100.0, 90.0], [180.0, 90.0]]), nose=np.array([140.0, 140.0])))


def test_plan_is_plain_data_and_bounded():
    plan = grafts.GraftPlan(grafts=[{"window": "mouth", "start": 10, "hold": 6, "enter": "grow", "ramp": 4}])
    assert plan.grafts[0].leave == "cut" and plan.model_dump()["grafts"][0]["window"] == "mouth"
    with pytest.raises(ValidationError):
        grafts.Graft(window="ear", start=0)
    with pytest.raises(ValidationError):
        grafts.Graft(window="eye-l", start=0, hold=0)


def test_beats_schedule_cycles_a_phrase_and_rests():
    beats = grafts.beats_from_bpm(300, 30, 120)                      # every 15 frames
    plan = grafts.schedule_on_beats(beats, pattern=[("eye-l", .5, "cut"), ("mouth", 1.5, "grow")], rest_every=4, frames=300)
    assert [g.window for g in plan[:4]] == ["eye-l", "mouth", "eye-l", "mouth"]
    assert plan[0].hold == 8 and plan[1].hold == 22                   # holds in beats, not a fixed length
    starts = [g.start for g in plan]
    assert 45 not in starts and 105 not in starts                      # every fourth beat rests
    assert plan[1].enter == "grow" and plan[1].leave == "fade"


def test_windows_follow_landmarks_and_cutins_are_face_scoped(tmp_path):
    donor = _donor(tmp_path)
    r = grafts.GraftRenderer([donor])
    host = _host()
    eye = r.window_mask("eye-l", host, (240, 320))
    face = r.window_mask("face", host, (240, 320))
    frame_m = r.window_mask("frame", host, (240, 320))
    assert eye[100, 120] and not eye[100, 200]                        # the left-eye window is on the left eye only
    assert face.sum() > eye.sum() and face[100, 160] and not face[5, 5]  # the face window is an ellipse, not the screen
    assert frame_m.all()
    frame = np.full((240, 320, 3), 90, np.uint8)
    g = grafts.Graft(window="face", start=0, hold=4, enter="cut", light=.5)
    out = r.apply(frame, host, g, 0, 0.5)
    assert (out[5, 5] == 90).all() and not (out[100, 160] == 90).all()     # outside the face untouched


def test_entrances_differ(tmp_path):
    donor = _donor(tmp_path)
    r = grafts.GraftRenderer([donor])
    host = _host()
    frame = np.full((240, 320, 3), 90, np.uint8)
    grow = grafts.Graft(window="mouth", start=0, hold=4, enter="grow", ramp=4)
    fade = grafts.Graft(window="mouth", start=0, hold=4, enter="fade", ramp=4)
    early_grow = r.apply(frame, host, grow, 0, 0.1)
    early_fade = r.apply(frame, host, fade, 0, 0.1)
    full = r.apply(frame, host, grow, 0, 0.5)
    changed = lambda a: int((a != 90).any(axis=2).sum())
    assert changed(early_grow) < changed(full)                        # grow: a smaller window early
    assert changed(early_fade) >= changed(full) * 0.9                 # fade: full window, lower opacity
    assert np.abs(early_fade.astype(int) - 90).mean() < np.abs(full.astype(int) - 90).mean()


def test_a_graft_is_a_sticker_content_rides_with_the_window(tmp_path):
    donor = _donor(tmp_path)
    r = grafts.GraftRenderer([donor])
    frame = np.full((240, 320, 3), 90, np.uint8)
    g = grafts.Graft(window="eye-l", start=0, hold=10, enter="cut", light=0)
    host = _host()
    a = r.apply(frame, host, g, 0, 0.5)
    # the same landmarks again: identical pixels, nothing swims
    b = r.apply(frame, host, g, 0, 0.6)
    assert np.array_equal(a, b)
    # the head moves 20 px right: the whole patch moves 20 px right, content and edge together
    moved = grafts.FacePoints(host.eyes + [20, 0], host.nose + [20, 0])
    c = r.apply(frame, moved, g, 0, 0.7)
    changed_a = (a != 90).any(axis=2); changed_c = (c != 90).any(axis=2)
    assert np.array_equal(np.roll(changed_a, 20, axis=1), changed_c)
    assert np.array_equal(np.roll(a, 20, axis=1)[changed_c], c[changed_c])
