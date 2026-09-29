"""Moment-level match cuts: evidence helpers, pair scoring and retrieval rules (no GPU, video or database)."""

from __future__ import annotations

import numpy as np
import pytest

from pipeline.evidence import moments as producer
from pipeline.matching.moments import find, index as moment_index, score as scoring


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _moments(instances: list[list[tuple[int, list[float], np.ndarray | None]]], *, poses=None, camera=None,
             velocity=None, gray=None, aspect: float = 16 / 9, brightness=None) -> scoring.Moments:
    """Moments from per-moment instance lists ``(class, box, silhouette or None for a full box)``."""
    n = len(instances)
    boxes, masks, classes, ptr = [], [], [], [0]
    for rows in instances:
        for code, box, mask in rows:
            boxes.append(box)
            masks.append(np.ones((16, 16), bool) if mask is None else mask)
            classes.append(code)
        ptr.append(len(boxes))
    pose_ptr, pose_xy, pose_conf = [0], [], []
    for rows in (poses or [[] for _ in range(n)]):
        for xy, conf in rows:
            pose_xy.append(xy)
            pose_conf.append(conf)
        pose_ptr.append(len(pose_xy))
    return scoring.Moments(
        gray=np.array(gray) if gray is not None else np.full((n, 18, 32), 0.5, np.float32),
        field=np.zeros((n, 2, 9, 16), np.float32), color=np.full((n, 5, 8, 3), 0.5, np.float32),
        brightness=np.array(brightness if brightness is not None else [0.5] * n, np.float32),
        camera=np.array(camera if camera is not None else [[np.nan] * 4] * n, np.float32),
        velocity=np.array(velocity if velocity is not None else [[np.nan] * 2] * n, np.float32),
        aspect=np.full(n, aspect, np.float32), inst_ptr=np.array(ptr, np.int64),
        boxes=np.array(boxes, np.float32).reshape(-1, 4), masks=np.array(masks, bool).reshape(-1, 16, 16),
        classes=np.array(classes, np.int64), scores=np.full(len(classes), 0.9, np.float32),
        pose_ptr=np.array(pose_ptr, np.int64), pose_xy=np.array(pose_xy, np.float32).reshape(-1, 17, 2),
        pose_conf=np.array(pose_conf, np.float32).reshape(-1, 17))


def _figure(x: float, top: float, height: float, shown=range(17)) -> tuple[np.ndarray, np.ndarray]:
    """A standing person's COCO keypoints centred at ``x`` (content fractions)."""
    offsets = np.array([[0, 0.06], [-0.01, 0.05], [0.01, 0.05], [-0.02, 0.055], [0.02, 0.055], [-0.05, 0.18],
                        [0.05, 0.18], [-0.07, 0.33], [0.07, 0.33], [-0.08, 0.46], [0.08, 0.46], [-0.03, 0.52],
                        [0.03, 0.52], [-0.03, 0.75], [0.03, 0.75], [-0.03, 0.97], [0.03, 0.97]])
    xy = np.stack([x + offsets[:, 0] * height, top + offsets[:, 1] * height], axis=1)
    conf = np.zeros(17)
    conf[list(shown)] = 0.9
    return xy.astype(np.float32), conf.astype(np.float32)


# ---------------------------------------------------------------------------
# Evidence helpers
# ---------------------------------------------------------------------------


def test_sample_times_follow_the_film_grid_inside_each_shot():
    times, owners = producer.sample_times([{"t_start": 0.0, "t_end": 1.0}, {"t_start": 1.0, "t_end": 1.1},
                                           {"t_start": 1.1, "t_end": 2.6}])
    assert list(times[owners == 0]) == [0.25, 0.5, 0.75]                   # 0.0 and 1.0 sit on the cuts
    assert list(owners).count(1) == 1                                       # a sliver keeps its nearest step
    assert list(times[owners == 2]) == [1.25, 1.5, 1.75, 2.0, 2.25, 2.5]


def test_select_instances_keeps_confident_large_distinct_detections():
    scores = np.array([0.9, 0.3, 0.8, 0.85, 0.9])
    labels = np.array([1, 1, 1, 1, 3])
    boxes = np.array([[0.1, 0.1, 0.5, 0.9], [0.0, 0.0, 1.0, 1.0], [0.11, 0.1, 0.5, 0.9],
                      [0.6, 0.2, 0.9, 0.8], [0.0, 0.0, 0.01, 0.01]])
    kept = producer.select_instances(scores, labels, boxes)
    assert list(kept) == [0, 3]              # low score, duplicate and tiny detections dropped; largest first


def test_silhouettes_round_trip():
    mask = np.random.default_rng(1).random((16, 16)) > 0.5
    assert np.array_equal(producer.unpack_silhouettes(producer.pack_silhouette(mask)[None])[0], mask)


# ---------------------------------------------------------------------------
# Geometry and components
# ---------------------------------------------------------------------------


def test_salient_group_keeps_prominent_instances_and_orders_by_weight():
    moments = _moments([[(1, [0.0, 0.0, 0.1, 0.1], None), (1, [0.2, 0.2, 0.7, 0.9], None), (1, [0.6, 0.1, 0.9, 0.8], None)],
                        []])
    group, main = scoring.salient(moments)
    assert main[0] == 1 and list(group[0][:2]) == [1, 2] and group[0][2] == -1        # the speck is background
    assert main[1] == -1 and (group[1] == -1).all()


def test_sampling_through_a_crop_reads_the_cropped_region():
    gradient = np.tile(np.linspace(0, 1, 32, dtype=np.float32), (1, 18, 1))
    whole = scoring.sample(gradient, np.array([[0.0, 0.0, 1.0, 1.0]]), (18, 32))
    assert np.array_equal(whole, gradient)                                            # identity fast path
    right_half = scoring.sample(gradient, np.array([[0.5, 0.0, 0.5, 1.0]]), (18, 32))
    assert right_half[0, 0, 0] == pytest.approx(0.5, abs=0.03) and right_half[0, 0, -1] == pytest.approx(1.0, abs=0.03)


def test_rasterize_covers_the_box_through_the_crop():
    moments = _moments([[(1, [0.25, 0.25, 0.75, 0.75], None)]])
    coverage = scoring.rasterize(moments, np.array([0]), np.array([[0.0, 0.0, 1.0, 1.0]]), (8, 8))
    assert coverage[0].sum() / 64 == pytest.approx(0.25, abs=0.02)
    zoomed = scoring.rasterize(moments, np.array([0]), np.array([[0.25, 0.25, 0.5, 0.5]]), (8, 8))
    assert zoomed[0].mean() == pytest.approx(1.0)


def test_identical_subjects_overlap_fully_and_a_missing_partner_costs():
    pair = [(1, [0.1, 0.2, 0.4, 0.9], None), (1, [0.55, 0.2, 0.85, 0.9], None)]
    reference = _moments([pair])
    candidates = _moments([pair, pair[:1], [(1, [0.6, 0.0, 0.7, 0.1], None)]])
    group_a, _ = scoring.salient(reference)
    group, _ = scoring.salient(candidates)
    crops = np.tile([0.0, 0.0, 1.0, 1.0], (3, 1))
    iou = scoring.subject(reference, group_a[0], np.array([0.0, 0.0, 1.0, 1.0]), candidates, group, crops, (9, 16))
    assert iou[0] == pytest.approx(1.0, abs=1e-6)
    assert 0.35 < iou[1] < 0.65                      # one of the two people
    assert iou[2] == 0.0


def test_reframe_moves_and_zooms_the_incoming_subject_onto_the_reference():
    reference = _moments([[(3, [0.4, 0.3, 0.6, 0.7], None)]])
    small = _moments([[(3, [0.3, 0.3, 0.4, 0.5], None)]])
    options = scoring.Options(reframe=True, zoom_max=1.5)
    scored = scoring.score(reference, small, options)
    assert scored.zoom[0] == pytest.approx(1.5)                                # wanted 2x, capped
    x, y, w, h = scored.crop[0]
    assert 0 <= x <= 1 - w and 0 <= y <= 1 - h and w == pytest.approx(1 / 1.5)
    plain = scoring.score(reference, small, scoring.Options())
    assert np.allclose(plain.crop[0], [0, 0, 1, 1]) and scored.parts["subject"][0] > plain.parts["subject"][0]


def test_vertical_output_crops_a_window_of_its_own_shape_around_the_subject():
    reference = _moments([[(1, [0.1, 0.2, 0.2, 0.9], None)]], aspect=2.39)
    crop = scoring.reference_crop(reference, 0, scoring.Options(output=scoring.VERTICAL))
    assert crop[2] == pytest.approx((9 / 16) / 2.39) and crop[3] == pytest.approx(1.0)
    assert crop[0] <= 0.15 <= crop[0] + crop[2]                              # the subject is inside the window


def test_motion_continues_only_in_the_same_direction():
    reference = _moments([[]], camera=[[0.1, 0.0, 0.0, 0.0]])
    candidates = _moments([[], [], [], []], camera=[[0.1, 0.0, 0, 0], [-0.1, 0.0, 0, 0], [0.0, 0.0, 0, 0],
                                                   [np.nan] * 4])
    result = scoring.motion(reference, candidates, np.ones(4))
    same, opposite, still, unknown = result["pan"]
    assert same > 0.95 and opposite < 0.05 and still == pytest.approx(0.25) and unknown == pytest.approx(0.5)


def test_pose_matches_like_framings_and_penalises_parts_shown_in_one_frame():
    full = _figure(0.5, 0.1, 0.8)
    close = _figure(0.5, 0.1, 0.8, shown=range(7))                       # head and shoulders only
    reference = _moments([[(1, [0.4, 0.1, 0.6, 0.9], None)]], poses=[[full]])
    candidates = _moments([[(1, [0.4, 0.1, 0.6, 0.9], None)]] * 3,
                          poses=[[full], [close], [_figure(0.8, 0.1, 0.8)]])
    crops = np.tile([0.0, 0.0, 1.0, 1.0], (3, 1))
    similarity = scoring.pose(reference, np.array([0.0, 0.0, 1.0, 1.0]), candidates, crops)
    assert similarity[0] == pytest.approx(1.0, abs=1e-6)
    assert similarity[1] < 0.5 and similarity[2] < 0.1


def test_a_person_without_a_visible_face_has_no_eye_point():
    headless = _figure(0.5, -0.2, 1.0, shown=range(5, 17))
    moments = _moments([[(1, [0.3, 0.0, 0.7, 1.0], None)], [(1, [0.3, 0.2, 0.7, 1.0], None)]],
                       poses=[[headless], [_figure(0.5, 0.2, 0.8)]])
    points, exists = scoring.eye_points(moments, np.array([0, 1]))
    assert not exists[0] and exists[1] and points[1][1] == pytest.approx(0.2 + 0.05 * 0.8, abs=1e-6)


def test_eye_points_need_evidence_without_a_pose():
    moments = _moments([[(1, [0.3, 0.0, 0.7, 1.0], None)], [(1, [0.3, 0.2, 0.7, 1.0], None)],
                        [(62, [0.0, 0.0, 1.0, 0.95], None)], [(62, [0.4, 0.4, 0.6, 0.6], None)]])
    points, exists = scoring.eye_points(moments, np.array([0, 1, 2, 3]))
    assert list(exists) == [False, True, False, True]        # cut-off head, head, frame-filling blob, object
    assert points[3] == pytest.approx([0.5, 0.5])


def test_a_frame_filling_subject_hands_its_weight_to_other_cues():
    full = _moments([[(1, [0.0, 0.0, 1.0, 1.0], None)]])
    small = _moments([[(1, [0.3, 0.2, 0.7, 0.9], None)]])
    candidates = _moments([[(62, [0.0, 0.0, 1.0, 1.0], None)]])
    filled = scoring.score(full, candidates, scoring.Options())
    framed = scoring.score(small, candidates, scoring.Options())
    assert filled.parts["filled"][0] == pytest.approx(0.8) and framed.parts["filled"][0] == 0.0
    assert filled.weights["subject"] < framed.weights["subject"]


def test_calibration_maps_chance_to_zero_and_the_rare_top_to_one():
    quantiles = [0.1, 0.3, 0.5, 0.7]
    assert list(scoring.calibrate(np.array([0.0, 0.1, 0.3, 0.5, 0.7, 0.9]), quantiles)) == pytest.approx(
        [0.0, 0.0, 0.4, 0.8, 1.0, 1.0])
    flat = scoring.calibrate(np.array([0.0, 0.5]), [0.0, 0.0, 0.0, 0.0])
    assert flat[0] == 0.0 and flat[1] == 1.0                                  # degenerate quantiles stay ordered


# ---------------------------------------------------------------------------
# Index assembly
# ---------------------------------------------------------------------------


def test_camera_at_averages_reliable_pairs_around_each_time():
    flow = [[0.0, 0.1, 0.0, 0, 0, 0, 1], [1 / 6, 0.3, 0.0, 0, 0, 0, 1], [2 / 6, 9.9, 9.9, 0, 0, 0, 0]]
    camera = moment_index.camera_at(np.array([0.1, 5.0]), flow)
    assert camera[0][0] == pytest.approx(0.2) and np.isnan(camera[1]).all()


def test_usable_needs_something_lit_and_distance_from_hidden_cuts():
    gray = np.zeros((3, 18, 32), np.uint8)
    gray[1:, :2, :2] = 200                                  # a lit satellite in black space is fine
    ok = moment_index.usable(np.array([1.0, 2.0, 3.0]), np.array([0, 0, 0]), gray, np.ones(3, bool), {0: [2.1]})
    assert list(ok) == [False, False, True]


def test_usable_keeps_cut_points_inside_the_pictures_of_a_split_shot():
    gray = np.full((4, 18, 32), 120, np.uint8)
    ok = moment_index.usable(np.array([1.0, 2.0, 3.0, 4.0]), np.zeros(4, np.int64), gray, np.ones(4, bool), {},
                             {0: [[0.5, 1.5], [3.5, 5.0]]})
    assert list(ok) == [True, False, False, True]          # 2 s and 3 s fall in the dissolve between pictures


def test_subject_velocity_follows_the_same_subject_within_a_shot():
    times = np.array([0.0, 0.25, 0.5, 0.75])
    units = np.array([0, 0, 0, 1])
    boxes = np.array([[0.1, 0.4, 0.2, 0.6], [0.2, 0.4, 0.3, 0.6], [0.3, 0.4, 0.4, 0.6], [0.4, 0.4, 0.5, 0.6]], np.float32)
    velocity = moment_index.subject_velocity(times, units, boxes, np.array([1, 1, 1, 1], np.uint8))
    assert velocity[1][0] == pytest.approx(0.4) and np.isnan(velocity[0]).all() and np.isnan(velocity[2]).all()


# ---------------------------------------------------------------------------
# Retrieval rules on a small in-memory index
# ---------------------------------------------------------------------------


def _index() -> moment_index.Index:
    """Four films; each unit holds eight grid instants with the same framed subject."""
    unit_rows = []
    films, scenes = [], []
    for film in range(4):
        for unit in range(3):
            films.append(film)
            scenes.append(f"f{film}:s{unit}")
    count = len(films) * 8
    unit = np.repeat(np.arange(len(films)), 8).astype(np.int32)
    time_values = np.tile(np.arange(8) * 0.25 + 10, len(films)) + np.repeat(np.arange(len(films)) * 20, 8)
    instances = [[(1, [0.3, 0.2, 0.7, 0.9], None)] for _ in range(count)]
    base = _moments(instances)
    from pipeline.evidence.moments import pack_silhouette
    columns = {
        "film": np.repeat(np.array(films, np.int16), 8), "unit": unit, "time": time_values,
        "ok": np.ones(count, bool), "gray": np.full((count, 18, 32), 128, np.uint8),
        "field": np.zeros((count, 2, 9, 16), np.int8), "color": np.full((count, 5, 8, 3), 128, np.uint8),
        "brightness": np.full(count, 0.5, np.float32), "sharpness": np.full(count, 100.0, np.float32),
        "camera": np.full((count, 4), np.nan, np.float32), "velocity": np.full((count, 2), np.nan, np.float32),
        "inst_ptr": base.inst_ptr, "inst_box": base.boxes, "inst_class": base.classes.astype(np.uint8),
        "inst_score": np.full(count, 230, np.uint8),
        "inst_mask": np.stack([pack_silhouette(mask) for mask in base.masks]),
        "pose_ptr": np.zeros(count + 1, np.int64), "pose_xy": np.zeros((0, 17, 2), np.float16),
        "pose_conf": np.zeros((0, 17), np.uint8),
    }
    coarse_rows = np.arange(count)
    rng = np.random.default_rng(0)
    coarse = np.ones((count, sum(moment_index.PARTS.values())), np.float32) + rng.random((count, 1)) * 0.01
    starts = np.array([20 * index + 10 for index in range(len(films))], float)
    return moment_index.Index(
        id="test", directory=None, manifest={"moments": count, "films": [{"content_box": None}] * 4},
        columns=columns, coarse=coarse, coarse_rows=coarse_rows, pca={}, unit_ids=[f"u{i}" for i in range(len(films))],
        unit_film=np.array(films, np.int32), unit_start=starts, unit_end=starts + 2.0, unit_scene=scenes,
        film_ids=[f"film{i}" for i in range(4)], film_titles=[f"Film {i}" for i in range(4)],
        film_aspect=np.full(4, 16 / 9), unit_first=np.arange(len(films) + 1) * 8, calibration=None)


def test_find_excludes_the_source_film_by_default_and_caps_each_film():
    result = find.find(_index(), find.Request(unit_id="u0", time=10.5, limit=20))
    films = [row["film_id"] for row in result["results"]]
    assert "film0" not in films and len(films) == 9                 # three other films, three shots each
    assert all(row["t_end"] - row["time"] >= 1.0 for row in result["results"])     # enough incoming footage


def test_find_allows_other_scenes_of_the_same_film_when_asked_and_skips_excluded_shots():
    result = find.find(_index(), find.Request(unit_id="u0", time=10.5, include_same_film=True,
                                              exclude_unit_ids=["u1"], limit=20))
    units = {row["unit_id"] for row in result["results"]}
    assert "u0" not in units and "u1" not in units and "u2" in units


def test_previous_direction_needs_footage_before_the_cut():
    result = find.find(_index(), find.Request(unit_id="u0", time=10.5, direction="previous", limit=20))
    assert all(row["time"] - row["t_start"] >= 1.0 for row in result["results"])


def test_find_rejects_an_unknown_focus():
    with pytest.raises(ValueError):
        find.find(_index(), find.Request(unit_id="u0", time=10.5, focus="vibes"))
