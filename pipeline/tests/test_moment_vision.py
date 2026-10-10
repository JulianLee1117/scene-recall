"""The match lab's Vision layers: what the index saw at one instant, described for an overlay."""

import math

import numpy as np
import pytest

from pipeline.evidence import moments as producer
from pipeline.matching.moments import score as scoring
from pipeline.matching.moments import vision


def _moment(*, pose: bool = True) -> scoring.Moments:
    mask = np.zeros((1, 16, 16), bool)
    mask[0, 2:, 5:11] = True                                    # a standing figure, head near the top
    field = np.zeros((1, 2, 9, 16), np.float32)
    field[0, 0, 4, 8] = 0.9                                     # one strong cell: a horizontal gradient, a vertical edge
    keypoints = np.full((1, 17, 2), 0.5, np.float32)
    keypoints[0, 1], keypoints[0, 2] = (0.46, 0.2), (0.54, 0.2)  # the eyes
    return scoring.Moments(
        gray=np.full((1, 18, 32), 0.25, np.float32), field=field, color=np.full((1, 5, 8, 3), 0.5, np.float32),
        brightness=np.array([0.25], np.float32), camera=np.array([[0.1, 0.0, -0.4, 0.0]], np.float32),
        velocity=np.array([[0.05, 0.0]], np.float32), aspect=np.array([2.39], np.float32),
        inst_ptr=np.array([0, 1]), boxes=np.array([[0.4, 0.1, 0.6, 0.95]], np.float32), masks=mask,
        classes=np.array([1]), scores=np.array([0.95], np.float32),
        **({"pose_ptr": np.array([0, 1]), "pose_xy": keypoints, "pose_conf": np.ones((1, 17), np.float32)} if pose else {}))


@pytest.fixture(autouse=True)
def _names(monkeypatch):
    monkeypatch.setattr(producer, "class_names", lambda: {1: "person"})


def test_layers_describe_every_kind_in_content_fractions():
    layers = {layer["key"]: layer for layer in vision.layers(_moment())}
    assert list(layers) == ["objects", "pose", "eyes", "lines", "light", "colour", "motion"]
    region = layers["objects"]["items"][0]
    assert region["label"] == "person" and region["score"] == 0.95 and region["box"] == [0.4, 0.1, 0.6, 0.95]
    assert len(region["mask"]) == 16 and region["mask"][2] == "0000011111100000"
    pose = layers["pose"]
    assert len(pose["joints"]) == 17 and [0, 1] in pose["edges"] and len(pose["items"][0]["points"]) == 17
    assert layers["eyes"]["items"] == [{"label": "eyes", "at": [0.5, 0.2]}], "the scorer's own eye point"
    [cell] = layers["lines"]["cells"]
    assert cell[:2] == [8, 4] and math.isclose(cell[2], math.pi / 2, abs_tol=1e-3), "the edge runs across the gradient"
    assert len(layers["light"]["values"]) == 18 * 32 and layers["colour"]["colors"][0] == "#808080"
    motion = layers["motion"]
    assert motion["zoom"] == -0.4 and motion["seconds"] == vision.TRAVEL_SECONDS
    camera, subject = motion["items"]
    assert camera["to"] == [0.5 + 0.1 * vision.TRAVEL_SECONDS, 0.5]
    assert subject["from"] == [0.5, 0.525] and subject["to"] == [0.5 + 0.05 * vision.TRAVEL_SECONDS, 0.525]


def test_each_reason_names_the_layers_that_show_it():
    keys = {layer["key"] for layer in vision.layers(_moment())}
    assert set(scoring.REWARDS) <= set(scoring.REASON_LAYERS), "every scored cue can be drawn"
    assert all(set(layers) <= keys for layers in scoring.REASON_LAYERS.values())
    found = scoring.reasons({"eyes": 0.8, "light": 0.6, "pose": 0.2}, {}, _moment(), _moment(), 1.3)
    assert [(reason["code"], reason["layers"]) for reason in found] == [("eyes", ["eyes"]), ("light", ["light"]), ("reframe", [])]


def test_a_moment_without_poses_still_describes_itself():
    layers = {layer["key"]: layer for layer in vision.layers(_moment(pose=False))}
    assert layers["pose"]["items"] == []
    assert len(layers["objects"]["items"]) == 1


class _Index:
    id = "index-1"

    def __init__(self, profile: str):
        self.columns = {"time": np.array([10.0, 10.25, 10.5]), "film": np.array([0, 0, 0]), "ok": np.array([True, True, False])}
        self.manifest = {"films": [{"moments_profile": profile}]}
        self.film_ids = ["film-a"]
        self.film_aspect = np.array([2.39])
        self.asked: list[int] = []

    def unit_index(self, unit_id: str) -> int:
        if unit_id != "unit-a":
            raise KeyError(unit_id)
        return 0

    def unit_rows(self, unit: int) -> np.ndarray:
        return np.arange(3)

    def moments(self, rows: np.ndarray) -> scoring.Moments:
        self.asked.append(int(rows[0]))
        return _moment()


def test_describe_picks_the_nearest_instant_and_says_which_producer_made_it():
    index = _Index(producer.PRODUCER.profile_id)
    out = vision.describe(index, "unit-a", 10.3)
    assert index.asked == [1] and out["time"] == 10.25 and out["usable"] is True
    assert out["source"]["profile"] == producer.PRODUCER.profile_id
    assert out["source"]["models"] == producer.model_names()
    older = vision.describe(_Index("match-v0-old"), "unit-a", 10.5)
    assert older["usable"] is False and older["source"]["models"] is None, "an older version's models are not guessed"
    with pytest.raises(KeyError):
        vision.describe(index, "unknown", 10.0)
