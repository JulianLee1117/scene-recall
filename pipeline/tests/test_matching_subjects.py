"""Contracts for tracked movement; quality acceptance still requires played cuts."""

from copy import deepcopy
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

from pipeline.matching import subjects


def evidence(*, dx=2.0, camera=0., opposing=False, empty=False):
    rows = [SimpleNamespace(time=i / 6, end=(i + 1) / 6,
                            image=Image.new("RGB", (128, 128), "gray")) for i in range(13)]
    masks = np.zeros((1, 13, 128, 128), dtype=bool)
    if not empty:
        masks[:, :, 32:96, 32:96] = True
    flow = np.zeros((128, 128, 2), dtype=float)
    flow[:, :, 0] = camera
    flow[32:96, 32:96, 0] += dx
    if opposing:
        flow[64:96, 32:96, 0] -= 2 * dx
    model = SimpleNamespace(pair=lambda first, second: flow)
    track = subjects.describe_masks(rows, masks, model)[0]
    track["profile_id"] = "fixture-profile"
    return track


def test_opposing_articulation_does_not_cancel_to_static():
    track = evidence(opposing=True)
    movement = track["motion"][0]["residual"]
    assert np.allclose(movement["velocity"], 0, atol=1e-8)
    assert movement["speed"] > .01
    assert np.linalg.norm(movement["vector"]) > .01
    result = subjects.similarity(track, track, 1., 0.)
    assert result["reliable"] and result["score"] == pytest.approx(1.)


def test_reversed_direction_is_rejected_even_with_identical_layout():
    forward, backward = evidence(dx=2), evidence(dx=-2)
    correct = subjects.similarity(forward, forward, 1., 0.)
    reversed_match = subjects.similarity(forward, backward, 1., 0.)
    assert correct["reliable"]
    assert not reversed_match["reliable"]
    assert correct["score"] > reversed_match["score"] + .4


def test_camera_estimate_excludes_subject_and_retains_both_channels():
    track = evidence(dx=2, camera=-2)
    motion = track["motion"][0]
    assert motion["camera_confidence"] > .99
    assert motion["screen"]["speed"] == pytest.approx(0)
    assert motion["residual"]["velocity"][0] == pytest.approx(12 / 128)
    assert subjects.similarity(track, track, 1., 0.)["reliable"]


def test_static_and_occluded_evidence_is_unknown():
    for track in (evidence(dx=0), evidence(empty=True)):
        assert subjects.similarity(track, track, 1., 0.) == {
            "score": -1., "components": {}, "reliable": False}


def test_profiles_cannot_mix():
    first, second = evidence(), evidence()
    second["profile_id"] = "different"
    with pytest.raises(ValueError, match="incompatible"):
        subjects.similarity(first, second, 1., 0.)


def test_order_and_boundary_movement_affect_score():
    first = evidence()
    second = deepcopy(first)
    # Same collection of velocities; reversed phase at the actual boundary.
    for item in second["motion"][:2]:
        for channel in ("screen", "residual"):
            item[channel]["vector"] = [-v for v in item[channel]["vector"]]
    assert subjects.similarity(first, first, 1., 0.)["score"] > subjects.similarity(first, second, 1., 0.)["score"]


def test_layout_uses_mask_position_and_scale():
    first = evidence()
    second = deepcopy(first)
    second["frames"][0]["centroid"] = [.85, .85]
    second["frames"][0]["area"] /= 4
    result = subjects.similarity(first, second, 1., 0.)
    assert result["components"]["position"] < .2
    assert result["components"]["scale"] == pytest.approx(.25)


def test_insufficient_background_stays_unknown():
    flow = np.ones((128, 128, 2))
    camera, confidence = subjects._camera(flow, np.ones((128, 128), dtype=bool))
    assert camera is None and confidence == 0


def test_finite_temporal_and_flow_evidence_required():
    with pytest.raises(ValueError, match="Non-finite"):
        subjects._vector_similarity([np.nan], [1])
    rows = [SimpleNamespace(time=0., end=.1, image=Image.new("RGB", (32, 32)))] * 2
    with pytest.raises(ValueError, match="timestamps"):
        subjects.describe_masks(rows, np.ones((1, 2, 32, 32), dtype=bool), None)


def test_cancellation_happens_before_flow_inference():
    from pipeline.lab.media import JobCancelled
    rows = [SimpleNamespace(time=i, end=i+.1, image=None) for i in range(2)]
    with pytest.raises(JobCancelled):
        subjects.describe_masks(rows, np.ones((1, 2, 32, 32)), None, cancelled=lambda: True)


def test_subject_evidence_preserves_picture_aspect():
    assert evidence()["frames"][0]["picture_aspect"] == 1.0


def test_unsupported_boundary_cannot_hide_in_a_supported_window():
    track = evidence()
    track["motion"][0]["reliable"] = False
    assert not subjects.similarity(track, track, 1., 0.)["reliable"]


def test_elapsed_time_phases_are_invariant_to_sampling_density():
    track = evidence()
    sequence = track["motion"][:6]
    dense = []
    for item in sequence:
        middle = (item["start"] + item["end"]) / 2
        dense.extend([{**item, "end": middle}, {**item, "start": middle}])
    assert np.allclose(subjects._phases(sequence, "screen"), subjects._phases(dense, "screen"))


def test_automatic_salience_prefers_interior_subject_over_border_pane():
    pane = np.zeros((100, 100), dtype=bool)
    pane[:, :30] = True
    person = np.zeros((100, 100), dtype=bool)
    person[25:, 35:65] = True
    assert subjects._salience(person, .95) > subjects._salience(pane, .99)


def test_outgoing_boundary_excludes_frame_starting_at_cut():
    outgoing, incoming = evidence(), evidence()
    outgoing["frames"][6]["visible"] = False  # time == 1.0 belongs after the cut
    assert subjects.similarity(outgoing, incoming, 1., 0.)["reliable"]


def test_following_camera_does_not_hide_screen_velocity_mismatch():
    following, stationary_camera = evidence(dx=2, camera=-2), evidence(dx=2, camera=0)
    perfect = subjects.similarity(following, following, 1., 0.)
    mismatch = subjects.similarity(following, stationary_camera, 1., 0.)
    assert mismatch["components"]["screen_movement"] == 0
    assert mismatch["score"] < perfect["score"]


def test_request_boundary_cache_preserves_exact_results_and_evidence():
    first, second = evidence(opposing=True), evidence()
    original = deepcopy(first), deepcopy(second)
    cache = {}
    for outgoing_time in (1., 1.2, 1.6):
        for incoming_time in (0., .2, .8):
            assert subjects.similarity(first, second, outgoing_time, incoming_time, cache=cache) == subjects.similarity(first, second, outgoing_time, incoming_time)
    assert (first, second) == original
    assert len(cache) <= 6
    assert all(track is first or track is second for track, _ in cache.values())


def test_shape_only_description_skips_optical_flow_and_preserves_masks():
    rows = [SimpleNamespace(time=i / 6, end=(i + 1) / 6,
                            image=Image.new("RGB", (128, 128), "gray")) for i in range(3)]
    masks = np.zeros((1, 3, 128, 128), dtype=bool)
    masks[:, :, 32:96, 32:96] = True
    model = SimpleNamespace(pair=lambda first, second: np.zeros((128, 128, 2)))
    complete = subjects.describe_masks(rows, masks, model)
    shape_only = subjects.describe_masks(rows, masks, None, measure_motion=False)
    assert complete[0]["frames"] == shape_only[0]["frames"]
    assert shape_only[0]["motion"] == []


def test_empty_cells_cannot_make_disjoint_subject_silhouettes_match():
    from pipeline.matching.subject_service import shape_similarity
    first, second = np.zeros(64), np.zeros(64)
    first[[0, 63]], second[[7, 56]] = 1., 1.
    assert 1 - np.mean(np.abs(first - second)) == .9375  # Previous MAE false agreement.
    assert subjects.silhouette_overlap(first, second) == 0.
    base = evidence()["frames"][0]
    result = shape_similarity({**base, "silhouette": first.tolist()},
                              {**base, "silhouette": second.tolist()})
    assert result["score"] == 0 and not result["reliable"]


def test_foreground_overlap_rewards_matching_shape_and_ignores_empty_background():
    first = np.zeros(64)
    first[[0, 1, 62, 63]] = 1.
    second = first.copy()
    second[1] = 0.
    assert subjects.silhouette_overlap(first, first) == 1.
    assert subjects.silhouette_overlap(first, second) == .75


def test_unknown_reference_explains_subject_failure_but_keeps_shape_channel():
    from pipeline.matching import subject_service
    subset = {"motion_unit_ids": [], "units": []}
    prepared = {"id": "prepared", "windows": [], "expected_rows": 0}
    references = [SimpleNamespace(time=.9, end=1.)]
    context = {"prepared": (None, [], references)}
    options = {"_context": context, "channel": "subject"}
    call = lambda: subject_service.candidates(None, None, subset, prepared, {}, {}, 1., options,
                                              None, lambda _: None, lambda: False)
    with pytest.raises(ValueError, match="No visible subject"):
        call()
    context["prepared"] = (None, [evidence(dx=0)], references)
    with pytest.raises(ValueError, match="No clear subject movement"):
        call()
    options["channel"] = "shape"
    assert call() == []  # Static grounded masks remain usable by Automatic's shape channel.


@pytest.mark.parametrize("invalid_prefix,expected_refinements", [(0, 5), (4, 7), (10, 10)])
def test_refine_five_then_extend_only_when_fewer_than_three_reliable(monkeypatch, invalid_prefix, expected_refinements):
    from pipeline.matching import subject_service
    image = Image.new("RGB", (128, 128), "gray")
    sample = SimpleNamespace(time=1., end=1.1, image=image)
    units = [{"unit_id": str(i), "film_id": f"film-{i}", "t_start": 0., "t_end": 5.} for i in range(10)]
    windows = [{"unit_id": u["unit_id"], "tracks": [{"coarse": True}]} for u in units]
    refined = []

    def describe(*args, **kwargs):
        refined.append(True)
        return {"tracks": [{"coarse": False}]}

    tracker = SimpleNamespace(describe=describe)
    reference_track = evidence()
    context = {"prepared": (tracker, [reference_track], [sample])}
    options = {"_context": context, "channel": "shape", "candidate_budget": 10, "allow_reframing": False}

    def best(query, references, tracks, row, shape, score_cache=None):
        if not tracks[0]["coarse"] and int(row["unit_id"]) < invalid_prefix:
            return None
        return {"score": 1 - int(row["unit_id"]) / 100, "time": 1., "unit": row,
                "reference": sample, "incoming_region": reference_track["frames"][0]["box"]}

    monkeypatch.setattr(subject_service, "_best", best)
    monkeypatch.setattr(subject_service.cohort, "resolve_film", lambda db, identity: {"path": "fake.mp4"})
    monkeypatch.setattr(subject_service.media, "samples", lambda *args, **kwargs: [sample] * 3)
    subject_service.candidates(None, None, {"motion_unit_ids": [u["unit_id"] for u in units], "units": units},
                               {"id": "prepared", "windows": windows, "expected_rows": 10}, {},
                               {"film_id": "reference-film"}, 1., options, None, lambda _: None, lambda: False)
    assert len(refined) == expected_refinements
