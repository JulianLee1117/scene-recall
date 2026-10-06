"""Feature-locked effects (ADR-0106): geometry, feature lookup, document rules and a real composited render."""

from __future__ import annotations

from io import BytesIO
import math
from pathlib import Path

import numpy as np
import pytest
from pydantic import ValidationError

from pipeline.index.writer import create_tables, open_db
from pipeline.lab import effects
from pipeline.lab.media import import_track, probe_media, render_manifest, render_reel, run_process
from pipeline.lab.models import ProjectDocument
from pipeline.lab.store import LabStore
from pipeline.matching.moments import score as scoring


@pytest.fixture
def store(config):
    store = LabStore(config.paths.state_dir)
    store.initialize()
    return store


@pytest.fixture
def db(config):
    db = open_db(config)
    create_tables(db, vector_dim=4)
    return db


def test_points_follow_the_renderers_crop_and_fit():
    # A 2.39:1 picture letterboxed into 16:9: full width, centred bars.
    left, top, width, height = effects.fit_box(2.39, None, 1280, 720)
    assert (left, round(width)) == (0, 1280) and round(top + height / 2) == 360 and height < 720
    # A tall crop of a 16:9 source is pillarboxed; its centre stays at the output centre.
    crop = {"x": 0.25, "y": 0.0, "width": 0.5, "height": 1.0}
    assert np.allclose(effects.source_to_output((0.5, 0.5), 16 / 9, crop, 1280, 720), (640, 360))
    assert np.allclose(effects.source_to_output((0.25, 0.0), 16 / 9, crop, 1280, 720), (320, 0))
    assert np.allclose(effects.content_to_source((0.5, 0.5), [0, 0.1, 1, 0.9]), (0.5, 0.5))
    assert np.allclose(effects.content_to_source((0.5, 0.0), [0, 0.1, 1, 0.9]), (0.5, 0.1))


def test_similarity_lands_eye_on_eye_and_refuses_mirrored_turns():
    moving = effects.Feature(np.array([[100.0, 200.0], [140.0, 200.0]]), 40.0)
    fixed = effects.Feature(np.array([[600.0, 300.0], [680.0, 316.0]]), 81.6)            # an 11 degree tilt
    matrix = effects.similarity(moving, fixed, (640, 360))
    for source, target in zip(moving.points, fixed.points):
        assert np.allclose(matrix[:, :2] @ source + matrix[:, 2], target)
    # Opposite eye order means mirrored faces: keep scale, drop the half turn.
    flipped = effects.Feature(fixed.points[::-1].copy(), fixed.size)
    matrix = effects.similarity(moving, flipped, (640, 360))
    assert abs(math.atan2(matrix[1, 0], matrix[0, 0])) < 1e-9
    assert np.allclose(matrix[:, :2] @ moving.centre() + matrix[:, 2], flipped.centre())
    # Scale is limited, and a missing feature leaves the picture alone.
    tiny = effects.Feature(np.array([[0.0, 0.0]]), 1.0)
    assert math.isclose(math.hypot(*effects.similarity(tiny, effects.Feature(np.array([[5.0, 5.0]]), 50.0), (0, 0))[:, 0]),
                        effects.SCALE_LIMITS[1])
    assert np.allclose(effects.similarity(None, fixed, (0, 0)), np.eye(2, 3))


def test_matrix_blend_envelope_and_blend_modes():
    a = effects.scale_about(np.array([100.0, 100.0]), 3.0, np.array([640.0, 360.0]))
    assert np.allclose(effects.blend_matrices(a, np.eye(2, 3), 0), a)
    assert np.allclose(effects.blend_matrices(a, np.eye(2, 3), 1), np.eye(2, 3))
    assert effects.envelope(0.5, 0, 1, 0, 0) == 1 and effects.envelope(1.0, 0, 1, 0, 0) == 0
    assert effects.envelope(0.05, 0, 1, 0.2, 0) < effects.envelope(0.15, 0, 1, 0.2, 0) < 1
    base = np.full((2, 2, 3), 0.5, np.float32)
    top = np.full((2, 2, 3), 0.5, np.float32)
    alpha = np.ones((2, 2, 1), np.float32)
    assert np.allclose(effects.blend(base, top, alpha, "screen"), 0.75)
    assert np.allclose(effects.blend(base, top, alpha, "multiply"), 0.25)
    assert np.allclose(effects.blend(base, top, alpha * 0, "normal"), base)
    dark, light = np.zeros((1, 1, 3), np.float32), np.ones((1, 1, 3), np.float32)
    assert np.allclose(effects.blend(dark, light, np.ones((1, 1, 1), np.float32), "luma"), 1)      # shows through shadows
    assert np.allclose(effects.blend(light, dark, np.ones((1, 1, 1), np.float32), "luma"), 1)      # not through highlights


def test_document_rules_for_effects():
    base = ProjectDocument().model_dump(mode="json")
    overlay = {"id": "o", "kind": "overlay", "start": 0.1, "end": 0.9, "source": {"film_id": "film", "source_start": 1.0}}
    ProjectDocument.model_validate(base | {"effects": [overlay]})
    with pytest.raises(ValidationError, match="source window"):
        ProjectDocument.model_validate(base | {"effects": [{"id": "o", "kind": "overlay", "start": 0, "end": 1}]})
    with pytest.raises(ValidationError, match="cut time"):
        ProjectDocument.model_validate(base | {"effects": [{"id": "c", "kind": "lock_cut", "start": 0, "end": 1}]})
    with pytest.raises(ValidationError, match="unique"):
        ProjectDocument.model_validate(base | {"effects": [overlay, overlay]})
    with pytest.raises(ValidationError, match="Only overlay"):
        ProjectDocument.model_validate(base | {"effects": [{**overlay, "kind": "flash"}]})
    # Effects outside the passage stay saveable; the render skips them.
    ProjectDocument.model_validate(base | {"effects": [{"id": "f", "kind": "flash", "start": 40, "end": 41}]})


class _FakeIndex:
    """Two instants of one shot with a person whose eyes move right."""

    def __init__(self):
        self.unit_ids, self.film_ids = ["film_0001"], ["film"]
        self.unit_film, self.unit_start, self.unit_end = np.array([0]), np.array([0.0]), np.array([3.0])
        self.manifest = {"films": [{"film_id": "film", "content_box": [0.0, 0.1, 1.0, 0.9]}]}
        self.columns = {"time": np.array([1.0, 1.25])}
        self.eyes = [np.array([[0.4, 0.5], [0.6, 0.5]]), np.array([[0.5, 0.5], [0.7, 0.5]])]

    def unit_index(self, unit_id):
        return self.unit_ids.index(unit_id)

    def unit_rows(self, unit):
        return np.arange(2)

    def moments(self, rows):
        n = len(rows)
        xy = np.zeros((n, 17, 2), np.float32)
        conf = np.zeros((n, 17), np.float32)
        for i, row in enumerate(rows):
            xy[i, [1, 2]] = self.eyes[int(row)]
            conf[i, [1, 2]] = 0.9
        zeros = lambda *shape: np.zeros(shape, np.float32)
        return scoring.Moments(zeros(n, 18, 32), zeros(n, 2, 9, 16), zeros(n, 5, 8, 3), zeros(n), zeros(n, 4), zeros(n, 2),
                               np.full(n, 16 / 9, np.float32), np.zeros(n + 1, np.int64), zeros(0, 4), np.zeros((0, 16, 16), bool),
                               np.zeros(0, np.int64), zeros(0), pose_ptr=np.arange(n + 1, dtype=np.int64), pose_xy=xy, pose_conf=conf)


def test_features_interpolate_between_grid_instants_and_map_to_output():
    source = effects.FeatureSource(_FakeIndex())
    points, size = source.at("film", "film_0001", 1.125)
    assert np.allclose(points, [[0.45, 0.5], [0.65, 0.5]]) and math.isclose(size, 0.2, rel_tol=1e-6)
    assert np.allclose(source.at("film", None, 1.0)[0], [[0.4, 0.5], [0.6, 0.5]])     # found by film and time
    assert source.at("other", None, 1.0) is None
    picture = effects.Picture("film", "film_0001", "unused", 16 / 9, None)
    feature = picture.feature(source, 1.0, "eyes", 1280, 720)
    # Content y 0.5 of the 0.1-0.9 band is source y 0.5: the output centre row.
    assert np.allclose(feature.points, [[512, 360], [768, 360]]) and math.isclose(feature.size, 256, rel_tol=1e-5)
    assert effects.FeatureSource(None).at("film", None, 1.0) is None


def _media(directory: Path):
    video, music = directory / "film.mp4", directory / "track.wav"
    picture = "color=c=red:s=160x90:r=30:d=3,drawbox=x=80:y=0:w=80:h=90:color=blue:t=fill,drawbox=x=0:y=0:w=160:h=90:color=green:t=fill:enable='gte(t,1)'"
    run_process(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", picture, "-c:v", "libx264", "-g", "90", "-pix_fmt", "yuv420p", str(video)])
    run_process(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=3", str(music)])
    return video, music


def test_real_render_composites_effects_and_keeps_effectless_manifests(config, store, db, tmp_path):
    video, audio = _media(tmp_path)
    track = import_track(store, audio, "Music.wav")
    db.open_table("films").add([{"film_id": "film", "title": "Film", "path": str(video), "duration": 3., "fps": 30.}])
    project = store.create_project("Reel", "music-sketch")
    clips = [{"id": "a", "film_id": "film", "source_start": .25, "source_end": .75, "crop": {"x": .25, "y": 0, "width": .5, "height": 1}},
             {"id": "b", "film_id": "film", "source_start": 1.25, "source_end": 1.75}]
    doc = {**project["document"], "track": {key: track[key] for key in ("id", "name", "duration")},
           "passage": {"start": .5, "end": 1.5}, "clips": clips}
    plain = render_manifest(doc, db, store)
    assert "effects" not in plain and "effects_profile" not in plain
    doc["effects"] = [
        {"id": "flash", "kind": "flash", "start": .5, "end": .6, "release": .1},
        {"id": "lock", "kind": "lock_cut", "start": .8, "end": 1.2, "at": 1.0, "align": "none", "opacity": .85},
        {"id": "late", "kind": "flash", "start": 2.0, "end": 2.2},
    ]
    project = store.update_project(project["id"], 1, doc)
    store.enqueue("render", project["id"], 2)
    job = store.claim()
    result = render_reel(job, config, db, store, lambda _: None)
    assert result["manifest"]["effects_profile"] == effects.EFFECTS_PROFILE
    assert result["effects"]["indexed"] is False
    assert any(item.startswith("flash late") for item in result["effects"]["skipped"])
    output = config.paths.assets_dir / "lab" / "renders" / job["id"] / "output.mp4"
    streams = {stream["codec_type"]: stream for stream in probe_media(output)["streams"]}
    assert int(streams["video"]["nb_frames"]) == 24 and "audio" in streams

    from PIL import Image

    def pixel(frame, x, y=360):
        raw = run_process(["ffmpeg", "-v", "error", "-i", str(output), "-vf", f"select=eq(n\\,{frame})", "-frames:v", "1",
                           "-f", "image2pipe", "-vcodec", "png", "pipe:1"])
        return Image.open(BytesIO(raw)).convert("RGB").getpixel((x, y))

    assert min(pixel(0, 100)) > 230                          # the flash lifts even the pillarbox to white
    early, late = pixel(8, 500), pixel(11, 500)              # the incoming green fades in over the red half
    assert late[1] > early[1] > 20 and late[0] < early[0]
    assert pixel(11, 100)[1] > 60                            # and over the pillarbox, where nothing else is
    after = pixel(12, 500)                                   # after the cut the outgoing red fades out on top
    assert 15 < after[0] < 90 and after[1] > 80
    assert pixel(12, 100)[0] < 15                            # its pillarbox is transparent
    assert pixel(20, 500)[0] < 15                            # and it is gone once the effect ends


def test_rolls_held_at_a_shot_boundary_never_read_unbounded(tmp_path):
    video, _ = _media(tmp_path)
    picture = effects.Picture("film", None, str(video), 16 / 9, None)
    # The shot starts after the whole requested window: hold one decoded frame, read nothing unbounded.
    frames = effects.read_frames(picture, 0.2, 6, 24, 64, 36, hold_before=1.2)
    assert len(frames) == 6 and all(frame is frames[0] for frame in frames)
    assert frames[0][18, 32, 1] > 80                        # the held frame is the shot's (green) picture
    # The shot ends before the window: hold its last frame.
    frames = effects.read_frames(picture, 1.5, 5, 24, 64, 36, hold_after=0.9)
    assert len(frames) == 5 and frames[0][18, 16, 0] > 200    # red, from before 0.9 s


def test_moved_pictures_keep_covering_their_frame_and_overlay_edges_fade():
    box = (0.0, 0.0, 1280.0, 720.0)
    turned = effects.similarity(effects.Feature(np.array([[600.0, 360.0], [680.0, 360.0]]), 80),
                                effects.Feature(np.array([[600.0, 360.0], [678.0, 376.0]]), 80), (640, 360))
    eyes = np.array([640.0, 360.0])
    anchor = turned[:, :2] @ eyes + turned[:, 2]                 # where the moved picture puts the eyes
    covered = effects.cover(turned, box, anchor)
    inverse = np.linalg.inv(np.vstack([covered, [0, 0, 1]]))[:2]
    corners = np.array([[0, 0], [1280, 0], [0, 720], [1280, 720]], float) @ inverse[:, :2].T + inverse[:, 2]
    assert corners.min() >= -0.5 and corners[:, 0].max() <= 1280.5 and corners[:, 1].max() <= 720.5
    assert np.allclose(covered[:, :2] @ eyes + covered[:, 2], anchor)          # the eyes stay put
    assert np.allclose(effects.cover(np.eye(2, 3), box, anchor), np.eye(2, 3))
    mask = effects.valid_mask(effects.Picture("f", None, "", 16 / 9, None), 1280, 720)
    assert mask[360, 640] == 255 and mask[360, 0] < 10 and 0 < mask[360, 40] < 255


def test_a_move_too_far_to_cover_is_weakened_instead_of_leaving_black():
    box = (0.0, 0.0, 1280.0, 720.0)
    far = effects.scale_about(np.array([100.0, 100.0]), 1.0, np.array([1100.0, 650.0]))       # eyes dragged across the frame
    covered = effects.cover(far, box, np.array([1100.0, 650.0]))
    inverse = np.linalg.inv(np.vstack([covered, [0, 0, 1]]))[:2]
    corners = np.array([[0, 0], [1280, 0], [0, 720], [1280, 720]], float) @ inverse[:, :2].T + inverse[:, 2]
    assert corners.min() >= -0.5 and corners[:, 0].max() <= 1280.5 and corners[:, 1].max() <= 720.5
    assert math.hypot(covered[0, 0], covered[1, 0]) <= effects.COVER_LIMIT + 1e-6


def test_feature_patches_sit_on_the_face_and_panels_fill_their_rectangle():
    eyes = effects.Feature(np.array([[600.0, 300.0], [680.0, 300.0]]), 80.0)
    strip = effects.feature_patch(eyes, "eyes", 1280, 720)
    assert strip[300, 640] == 255 and strip[300, 560] == 255 and strip[300, 500] == 0 and strip[380, 640] == 0
    mouth = effects.feature_patch(eyes, "mouth", 1280, 720)
    assert mouth[392, 640] == 255 and mouth[300, 640] == 0
    rect = {"x": 0.5, "y": 0.1, "width": 0.4, "height": 0.4}
    matrix = effects.panel_matrix(rect, 0.0, 1280, 720)
    assert np.allclose(matrix[:, :2] @ np.array([640.0, 360.0]) + matrix[:, 2], [(0.5 + 0.2) * 1280, (0.1 + 0.2) * 720])
    mask = effects.rect_mask(rect, 0.0, 1280, 720)
    assert mask[216, 896] == 255 and mask[216, 600] == 0
    turned = effects.rect_mask(rect, 20.0, 1280, 720)
    assert turned[216, 896] == 255 and (turned != mask).any()


def test_real_render_places_panels_strips_fills_and_cutouts(config, store, db, tmp_path, monkeypatch):
    video, audio = _media(tmp_path)
    track = import_track(store, audio, "Music.wav")
    db.open_table("films").add([{"film_id": "film", "title": "Film", "path": str(video), "duration": 3., "fps": 30.}])

    def left_half(self, rgb, classes):                     # a stand-in segmenter: the subject is the left half
        mask = np.zeros(rgb.shape[:2], np.uint8)
        mask[:, : rgb.shape[1] // 2] = 255
        return mask

    monkeypatch.setattr(effects.Segmenter, "mask", left_half)
    project = store.create_project("Reel", "music-sketch")
    doc = {**project["document"], "track": {key: track[key] for key in ("id", "name", "duration")},
           "passage": {"start": .5, "end": 1.5},
           "clips": [{"id": "a", "film_id": "film", "source_start": .25, "source_end": .75},
                     {"id": "b", "film_id": "film", "source_start": 1.25, "source_end": 1.75}]}
    doc["effects"] = [
        {"id": "panel", "kind": "panel", "start": .5, "end": .7, "source": {"film_id": "film", "source_start": 1.2},
         "rect": {"x": .6, "y": .1, "width": .3, "height": .3}},
        {"id": "strips", "kind": "strips", "start": .8, "end": .95,
         "sources": [{"film_id": "film", "source_start": .3}, {"film_id": "film", "source_start": 1.3}]},
        {"id": "fill", "kind": "fill", "start": 1.1, "end": 1.3, "source": {"film_id": "film", "source_start": .3}, "align": "none"},
        {"id": "cutout", "kind": "overlay", "start": 1.35, "end": 1.45, "source": {"film_id": "film", "source_start": .3},
         "align": "none", "region": "subject", "matte": "#ffffff"},
        {"id": "split", "kind": "overlay", "start": .65, "end": .75, "source": {"film_id": "film", "source_start": 1.3},
         "align": "none", "rect": {"x": .5, "y": 0, "width": .5, "height": 1}},
    ]
    project = store.update_project(project["id"], 1, doc)
    store.enqueue("render", project["id"], 2)
    job = store.claim()
    render_reel(job, config, db, store, lambda _: None)
    output = config.paths.assets_dir / "lab" / "renders" / job["id"] / "output.mp4"
    from PIL import Image

    def pixel(frame, x, y=360):
        raw = run_process(["ffmpeg", "-v", "error", "-i", str(output), "-vf", f"select=eq(n\\,{frame})", "-frames:v", "1",
                           "-f", "image2pipe", "-vcodec", "png", "pipe:1"])
        return Image.open(BytesIO(raw)).convert("RGB").getpixel((x, y))

    assert pixel(2, 960, 200)[1] > 100 and pixel(2, 960, 200)[0] < 40      # the green panel, inside its rectangle
    assert pixel(2, 300)[0] > 200                                        # the red picture around it
    assert pixel(9, 100)[0] > 200 and pixel(9, 500)[2] > 200             # left strip: the red/blue picture, centred on it
    assert pixel(9, 900)[1] > 100 and pixel(9, 900)[0] < 40              # right strip: green
    assert pixel(16, 100)[0] > 200 and pixel(16, 900)[1] > 100           # fill: red/blue inside the left-half subject, green outside
    assert min(pixel(21, 100)) > 230 and pixel(21, 900)[1] > 100         # white matte cutout on the left half only
    assert pixel(5, 300)[0] > 200 and pixel(5, 1000)[1] > 100 and pixel(5, 1000)[2] < 60  # split: red left, green window right
