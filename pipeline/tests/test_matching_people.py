"""Person evidence contracts; these do not grade editorial cut usefulness."""
from copy import deepcopy
import json
from types import SimpleNamespace

import numpy as np
import pytest

from pipeline.matching import people


def prediction(rectangles, *, labels=None, scores=None, size=(100, 100)):
    width, height = size
    masks = np.zeros((len(rectangles), 1, height, width), dtype=np.float32)
    for index, (x0, y0, x1, y1) in enumerate(rectangles):
        masks[index, 0, y0:y1, x0:x1] = 1
    return {"boxes": np.asarray(rectangles, dtype=float).reshape(-1, 4),
            "labels": np.asarray(labels if labels is not None else [1] * len(rectangles)),
            "scores": np.asarray(scores if scores is not None else [.95] * len(rectangles)),
            "masks": masks}


def test_person_labels_and_confidence_are_required():
    value = prediction([(10, 10, 30, 80)] * 4, labels=[1, 2, 1, 1], scores=[.7, .99, .699, np.nan])
    result = people.describe_prediction(value, (100, 100))
    assert [item["id"] for item in result["detections"]] == ["0"]


def test_source_pixels_are_preserved_in_normalized_geometry():
    result = people.describe_prediction(prediction([(20, 10, 60, 70)], size=(200, 100)), (200, 100))
    person = result["selected"][0]
    assert person["box"] == {"x": .1, "y": .1, "width": .2, "height": .6}
    assert person["centroid"] == pytest.approx([.2, .4])
    assert person["area"] == pytest.approx(.12)
    assert person["picture_aspect"] == 2
    assert person["silhouette"] == [1.] * 64
    json.dumps(result, allow_nan=False)


def test_foreground_pair_survives_twenty_smaller_spectators():
    rectangles = [(35, 10, 45, 80), (55, 15, 70, 85)]
    rectangles.extend([(i * 4, 80, i * 4 + 4, 84) for i in range(20)])
    result = people.describe_prediction(prediction(rectangles), (100, 100))
    assert len(result["detections"]) == 22
    assert [item["id"] for item in result["selected"]] == ["1", "0"]
    assert result["selected_complete_count"] == 2


def test_salient_group_preserves_every_prominent_person_in_stable_order():
    value = prediction([(x, 10, x + 10, 70) for x in (0, 20, 40, 60, 80)])
    result = people.describe_prediction(value, (100, 100))
    assert len(result["detections"]) == 5
    assert [item["id"] for item in result["selected"]] == ["0", "1", "2", "3", "4"]
    assert result["selected_complete_count"] == 5


def test_extra_prominent_people_cannot_disappear_into_a_three_person_match():
    from pipeline.matching.person_layout import compare_people

    reference = people.describe_prediction(prediction([(x, 10, x + 10, 70) for x in (0, 20, 40)]), (100, 100))
    candidate = people.describe_prediction(prediction([(x, 10, x + 10, 70) for x in (0, 20, 40, 60, 80)]), (100, 100))
    result = compare_people(reference["selected"], candidate["selected"])
    assert not result["reliable"] and result["reason"] == "unsupported_person_count"
    assert result["measurements"]["candidate_people"] == 5


def test_detector_output_bound_is_explicit_instead_of_silently_losing_instances():
    value = prediction([(10, 10, 30, 80)] * 101)
    with pytest.raises(ValueError, match="bounded output"):
        people.describe_prediction(value, (100, 100))


@pytest.mark.parametrize("box", [[-1, 0, 20, 30], [0, 0, 101, 30], [20, 0, 10, 30], [0, 0, np.nan, 30]])
def test_malformed_box_cannot_generate_evidence(box):
    value = prediction([(10, 10, 30, 80)])
    value["boxes"][0] = box
    assert people.describe_prediction(value, (100, 100))["detections"] == []


@pytest.mark.parametrize("corrupt", [np.nan, -1., 1.1])
def test_invalid_mask_probabilities_are_rejected(corrupt):
    value = prediction([(10, 10, 30, 80)])
    value["masks"][0, 0, 0, 0] = corrupt
    assert people.describe_prediction(value, (100, 100))["selected"] == []


def test_empty_or_tiny_person_masks_do_not_invent_subjects():
    for rectangles in ([], [(0, 0, 3, 3)]):
        result = people.describe_prediction(prediction(rectangles), (100, 100))
        assert result["selected"] == [] and result["selected_complete_count"] == 0


def test_malformed_output_axes_and_picture_size_fail_clearly():
    value = prediction([(10, 10, 30, 80)])
    for key in ("scores", "masks", "boxes"):
        bad = deepcopy(value)
        bad[key] = np.asarray(1)
        with pytest.raises(ValueError, match="Malformed"):
            people.describe_prediction(bad, (100, 100))
    with pytest.raises(ValueError, match="dimensions"):
        people.describe_prediction(value, (0, 100))


@pytest.fixture
def prepared(tmp_path):
    config = SimpleNamespace(paths=SimpleNamespace(assets_dir=tmp_path))
    directory = people.model_directory(config)
    directory.mkdir(parents=True)
    (directory / people.CHECKPOINT).write_bytes(b"test-local-checkpoint")
    payload = people._payload("cpu")
    profile = {**payload, "id": people.digest(payload)}
    (directory / "profile.json").write_text(json.dumps(profile), encoding="utf-8")
    return config, directory, profile


def test_profile_identity_rejects_changed_contract_and_missing_weights(prepared):
    config, directory, profile = prepared
    assert people.load_profile(config) == profile
    changed = {**profile, "min_score": .1}
    (directory / "profile.json").write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(ValueError, match="incompatible"):
        people.load_profile(config)
    (directory / "profile.json").write_text(json.dumps(profile), encoding="utf-8")
    (directory / people.CHECKPOINT).unlink()
    with pytest.raises(ValueError, match="unavailable"):
        people.load_profile(config)


def test_complete_group_selection_has_separate_profile_lineage(prepared):
    config, directory, profile = prepared
    assert directory.name == "people-maskrcnn-v2"
    assert profile["contract"] == "coco-person-mask-full-source-v2"
    assert profile["max_supported_people"] == 3 and profile["detections_per_image"] == 100
    assert profile["selection"] == "all-salient-detected-people-without-truncation-v2"
    assert "max_people" not in profile
    changed = {**profile, "contract": "coco-person-mask-full-source-v1"}
    changed["id"] = people.digest({key: value for key, value in changed.items() if key != "id"})
    (directory / "profile.json").write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(ValueError, match="incompatible"):
        people.load_profile(config)


def test_runtime_constructs_only_local_weights_without_backbone_download(prepared, monkeypatch):
    import torch
    import torchvision.models.detection

    config, directory, profile = prepared
    calls = {}
    class Model:
        def load_state_dict(self, state): calls["state"] = state
        def to(self, device): calls["device"] = device; return self
        def eval(self): return self
    def factory(**kwargs): calls["factory"] = kwargs; return Model()
    def load(path, **kwargs): calls["load"] = (path, kwargs); return {"test": 1}
    def forbidden(*args, **kwargs): pytest.fail("Runtime attempted a model download")
    monkeypatch.setattr(people, "_hash", lambda path: people.SHA256)
    monkeypatch.setattr(torchvision.models.detection, "maskrcnn_resnet50_fpn_v2", factory)
    monkeypatch.setattr(torch, "load", load)
    monkeypatch.setattr(torch.hub, "download_url_to_file", forbidden)
    detector = people.Detector(config)
    assert detector.profile == profile
    assert calls["factory"]["weights"] is None
    assert calls["factory"]["weights_backbone"] is None
    assert calls["load"] == (directory / people.CHECKPOINT, {"map_location": "cpu", "weights_only": True})
    assert calls["device"] == "cpu"


def test_runtime_rejects_unverified_weights_before_model_construction(prepared, monkeypatch):
    import torchvision.models.detection
    config, _, _ = prepared
    monkeypatch.setattr(torchvision.models.detection, "maskrcnn_resnet50_fpn_v2",
                        lambda **kwargs: pytest.fail("Unverified weights reached model construction"))
    with pytest.raises(ValueError, match="pinned official weights"):
        people.Detector(config)


def test_runtime_device_must_match_frozen_profile(prepared):
    with pytest.raises(ValueError, match="device differs"):
        people.Detector(prepared[0], device="cuda")
