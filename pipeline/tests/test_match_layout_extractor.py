from __future__ import annotations

import numpy as np
import pytest

from pipeline.search.match_layout import (
    FrameLayout,
    layout_vector,
    parse_layout_payload,
    score_layout_match,
)
from pipeline.search import match_layout_extractor as extractor_module
from pipeline.search.match_layout_extractor import (
    DETECTOR_ARCHITECTURE,
    DETECTOR_WEIGHTS_ID,
    MAX_LAYOUT_ENTITIES,
    POSE_MODEL_ID,
    POSE_MODEL_REVISION,
    ActivePictureRect,
    InstanceDetection,
    KeypointDetection,
    MatchLayoutExtractor,
    PoseDetection,
    detect_active_picture,
    frame_layout_from_detections,
)


def _mask(
    height: int,
    width: int,
    box: tuple[int, int, int, int],
) -> np.ndarray:
    result = np.zeros((height, width), dtype=np.float32)
    result[box[1] : box[3], box[0] : box[2]] = 1.0
    return result


def _detection(
    detection_id: str,
    category: str,
    score: float,
    box: tuple[int, int, int, int],
    *,
    height: int = 100,
    width: int = 100,
    mask_box: tuple[int, int, int, int] | None = None,
) -> InstanceDetection:
    return InstanceDetection(
        detection_id=detection_id,
        category=category,
        score=score,
        box_xyxy=tuple(float(value) for value in box),
        mask=_mask(height, width, mask_box or box),
    )


def test_pinned_lineage_constants_are_explicit() -> None:
    assert DETECTOR_ARCHITECTURE == "torchvision.maskrcnn_resnet50_fpn_v2"
    assert DETECTOR_WEIGHTS_ID == (
        "MaskRCNN_ResNet50_FPN_V2_Weights.COCO_V1"
    )
    assert POSE_MODEL_ID == "usyd-community/vitpose-plus-small"
    assert POSE_MODEL_REVISION == (
        "0c30b6534bb621af0162b481176742577264e36e"
    )


def test_active_picture_removes_supported_bars_but_not_a_dark_scene() -> None:
    letterboxed = np.zeros((80, 120, 3), dtype=np.uint8)
    interior = np.linspace(35, 180, 60, dtype=np.uint8)[:, None]
    letterboxed[10:70, :, :] = interior[:, :, None]

    assert detect_active_picture(letterboxed) == ActivePictureRect(
        left=0,
        top=10,
        right=120,
        bottom=70,
    )

    dark_scene = np.full((80, 120, 3), 8, dtype=np.uint8)
    dark_scene[8:72, 8:112, :] = 14
    assert detect_active_picture(dark_scene) == ActivePictureRect(0, 0, 120, 80)


def test_duplicate_instances_collapse_but_overlapping_people_remain() -> None:
    image = np.full((100, 100, 3), 110, dtype=np.uint8)
    first_mask = _mask(100, 100, (10, 10, 58, 90))
    second_mask = _mask(100, 100, (43, 10, 90, 90))
    detections = (
        InstanceDetection("first", "person", 0.90, (10, 10, 60, 90), first_mask),
        InstanceDetection("first-duplicate", "person", 0.72, (11, 10, 60, 90), first_mask),
        InstanceDetection("second", "person", 0.82, (40, 10, 90, 90), second_mask),
    )

    layout = frame_layout_from_detections(image, detections)

    assert len(layout.entities) == 2
    assert all(entity.category == "person" for entity in layout.entities)
    assert all(entity.silhouette is not None for entity in layout.entities)
    assert all(len(entity.silhouette or ()) == 64 for entity in layout.entities)


def test_entities_are_active_picture_normalized_and_capped_by_salience() -> None:
    image = np.full((100, 100, 3), 90, dtype=np.uint8)
    categories = (
        "person",
        "car",
        "dog",
        "chair",
        "bottle",
        "book",
        "clock",
        "vase",
        "umbrella",
    )
    detections = [
        _detection(
            f"det-{index}",
            category,
            0.95 - index * 0.02,
            (5 + index * 9, 20, 13 + index * 9, 80),
        )
        for index, category in enumerate(categories)
    ]
    active = ActivePictureRect(0, 10, 100, 90)

    layout = frame_layout_from_detections(
        image,
        detections,
        active_picture=active,
    )

    assert len(layout.entities) == MAX_LAYOUT_ENTITIES
    person = next(entity for entity in layout.entities if entity.category == "person")
    assert person.box.x_min == pytest.approx(0.05)
    assert person.box.y_min == pytest.approx(0.125)
    assert person.box.y_max == pytest.approx(0.875)


def test_local_sharpness_contributes_to_salience() -> None:
    image = np.full((80, 120, 3), 100, dtype=np.uint8)
    checker = (np.indices((60, 40)).sum(axis=0) % 2 * 255).astype(np.uint8)
    image[10:70, 70:110, :] = checker[..., None]
    detections = (
        _detection("smooth", "book", 0.8, (10, 10, 50, 70), height=80, width=120),
        _detection("sharp", "book", 0.8, (70, 10, 110, 70), height=80, width=120),
    )

    layout = frame_layout_from_detections(image, detections)

    left = next(entity for entity in layout.entities if entity.box.center[0] < 0.5)
    right = next(entity for entity in layout.entities if entity.box.center[0] > 0.5)
    assert right.salience > left.salience


def test_pose_keeps_confident_body_points_without_inventing_facing() -> None:
    image = np.full((100, 100, 3), 120, dtype=np.uint8)
    person = _detection("person", "person", 0.9, (10, 10, 90, 90))
    weak_face = PoseDetection(
        (
            KeypointDetection("nose", 65, 30, 0.40),
            KeypointDetection("left_eye", 42, 30, 0.41),
            KeypointDetection("left_shoulder", 35, 45, 0.91),
            KeypointDetection("right_shoulder", 60, 46, 0.89),
        )
    )

    layout = frame_layout_from_detections(
        image,
        (person,),
        poses={"person": weak_face},
    )
    entity = layout.entities[0]

    assert {point.name for point in entity.pose} == {
        "left_shoulder",
        "right_shoulder",
    }
    assert entity.orientation is None


def test_facing_requires_multiple_confident_asymmetric_face_landmarks() -> None:
    image = np.full((100, 100, 3), 120, dtype=np.uint8)
    person = _detection("person", "person", 0.9, (10, 10, 90, 90))
    side_facing = PoseDetection(
        (
            KeypointDetection("nose", 66, 30, 0.94),
            KeypointDetection("left_eye", 45, 30, 0.92),
            KeypointDetection("left_ear", 36, 31, 0.90),
            KeypointDetection("left_shoulder", 35, 48, 0.88),
            KeypointDetection("right_shoulder", 59, 48, 0.86),
        )
    )
    frontal = PoseDetection(
        (
            KeypointDetection("nose", 50, 30, 0.94),
            KeypointDetection("left_eye", 44, 30, 0.92),
            KeypointDetection("right_eye", 56, 30, 0.91),
            KeypointDetection("left_shoulder", 35, 48, 0.88),
            KeypointDetection("right_shoulder", 65, 48, 0.86),
        )
    )

    side_layout = frame_layout_from_detections(
        image,
        (person,),
        poses={"person": side_facing},
    )
    front_layout = frame_layout_from_detections(
        image,
        (person,),
        poses={"person": frontal},
    )

    assert side_layout.entities[0].orientation is not None
    assert side_layout.entities[0].orientation.x == pytest.approx(1.0)
    assert front_layout.entities[0].orientation is None


def test_models_load_lazily_and_zero_entity_layouts_are_valid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"detector": 0, "pose": 0}

    class EmptyDetector:
        def predict(self, rgb: np.ndarray) -> tuple[()]:
            return ()

    def detector_factory(device: str | None) -> EmptyDetector:
        calls["detector"] += 1
        assert device == "cpu"
        return EmptyDetector()

    def pose_factory(device: str | None) -> None:
        calls["pose"] += 1
        raise AssertionError("pose model must not load without a retained person")

    monkeypatch.setattr(extractor_module, "_new_detector_backend", detector_factory)
    monkeypatch.setattr(extractor_module, "_new_pose_backend", pose_factory)
    extractor = MatchLayoutExtractor(device="cpu")
    assert calls == {"detector": 0, "pose": 0}

    first = extractor.extract(np.full((40, 60, 3), 80, dtype=np.uint8), frame_id="empty")
    second = extractor.extract(np.full((40, 60, 3), 80, dtype=np.uint8))

    assert first == FrameLayout(entities=(), frame_id="empty")
    assert second.entities == ()
    assert calls == {"detector": 1, "pose": 0}
    assert not np.any(layout_vector(first))
    assert parse_layout_payload(first.to_payload()) == first
    assert score_layout_match(first, second).score == pytest.approx(0.0)
