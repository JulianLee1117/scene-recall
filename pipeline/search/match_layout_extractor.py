"""Pinned, shadow-only evidence extraction for ``match-layout-v1``.

The module intentionally stops at an in-memory :class:`FrameLayout`.  It has
no profile writer, CLI, database, or production retrieval integration.  Model
construction is lazy: Mask R-CNN is loaded on the first extraction and
ViTPose is loaded only if a retained person detection needs pose evidence.

Uncertain evidence is omitted.  In particular, a detector box never becomes a
synthetic rectangular silhouette, low-confidence keypoints are not published,
and facing direction is emitted only when multiple confident facial landmarks
support a non-frontal horizontal direction.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Callable, Mapping, Protocol, Sequence

import numpy as np
from PIL import Image

from pipeline.search.match_layout import (
    MIN_POSE_KEYPOINTS,
    MAX_LAYOUT_ENTITIES,
    FrameLayout,
    LayoutEntity,
    NormalizedBox,
    Orientation,
    PoseKeypoint,
    SILHOUETTE_GRID_SIZE,
)


# These identities are part of the shadow extractor lineage.  Do not replace
# an enum alias or move the Hugging Face revision without defining a new
# extractor/profile version.
MATCH_LAYOUT_EXTRACTOR_ID = "match-layout-extractor-v1"
DETECTOR_ARCHITECTURE = "torchvision.maskrcnn_resnet50_fpn_v2"
DETECTOR_WEIGHTS_ID = "MaskRCNN_ResNet50_FPN_V2_Weights.COCO_V1"
POSE_MODEL_ID = "usyd-community/vitpose-plus-small"
POSE_MODEL_REVISION = "0c30b6534bb621af0162b481176742577264e36e"

DETECTOR_SCORE_THRESHOLD = 0.50
MASK_THRESHOLD = 0.50
MIN_ACTIVE_ENTITY_AREA = 0.0008
MIN_ACTIVE_BOX_RETENTION = 0.50
POSE_KEYPOINT_CONFIDENCE = 0.55
FACING_KEYPOINT_CONFIDENCE = 0.72
MIN_FACING_CONFIDENCE = 0.62

_DUPLICATE_BOX_IOU = 0.88
_DUPLICATE_MASK_IOU = 0.78
_DUPLICATE_CONTAINMENT = 0.92
_DUPLICATE_MASK_CONTAINMENT = 0.88

_BAR_DARK_LUMA = 18.0
_BAR_DARK_FRACTION = 0.985
_MIN_BAR_FRACTION = 0.005
_MAX_BAR_FRACTION = 0.24
_MIN_INTERIOR_P90 = 24.0

_COCO_KEYPOINT_NAMES = (
    "nose",
    "left_eye",
    "right_eye",
    "left_ear",
    "right_ear",
    "left_shoulder",
    "right_shoulder",
    "left_elbow",
    "right_elbow",
    "left_wrist",
    "right_wrist",
    "left_hip",
    "right_hip",
    "left_knee",
    "right_knee",
    "left_ankle",
    "right_ankle",
)
_BODY_KEYPOINT_NAMES = frozenset(_COCO_KEYPOINT_NAMES[5:])

_CLASS_FAMILIES: Mapping[str, str] = {
    "person": "person",
    "bicycle": "vehicle",
    "car": "vehicle",
    "motorcycle": "vehicle",
    "airplane": "vehicle",
    "bus": "vehicle",
    "train": "vehicle",
    "truck": "vehicle",
    "boat": "vehicle",
    "bird": "animal",
    "cat": "animal",
    "dog": "animal",
    "horse": "animal",
    "sheep": "animal",
    "cow": "animal",
    "elephant": "animal",
    "bear": "animal",
    "zebra": "animal",
    "giraffe": "animal",
    "chair": "furniture",
    "couch": "furniture",
    "bed": "furniture",
    "dining_table": "furniture",
}


@dataclass(frozen=True, slots=True)
class ActivePictureRect:
    """Pixel-exclusive active-picture bounds in the decoded frame."""

    left: int
    top: int
    right: int
    bottom: int

    def __post_init__(self) -> None:
        if min(self.left, self.top) < 0:
            raise ValueError("active-picture coordinates cannot be negative")
        if self.right <= self.left or self.bottom <= self.top:
            raise ValueError("active-picture rectangle must have positive area")

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top


@dataclass(frozen=True, slots=True)
class InstanceDetection:
    """One raw detector result in decoded-frame pixel coordinates."""

    detection_id: str
    category: str
    score: float
    box_xyxy: tuple[float, float, float, float]
    mask: np.ndarray | None = None


@dataclass(frozen=True, slots=True)
class KeypointDetection:
    """One raw COCO keypoint in decoded-frame pixel coordinates."""

    name: str
    x: float
    y: float
    confidence: float


@dataclass(frozen=True, slots=True)
class PoseDetection:
    keypoints: tuple[KeypointDetection, ...]


class _DetectorBackend(Protocol):
    def predict(self, rgb: np.ndarray) -> Sequence[InstanceDetection]: ...


class _PoseBackend(Protocol):
    def predict(
        self,
        rgb: np.ndarray,
        people: Sequence[InstanceDetection],
    ) -> Mapping[str, PoseDetection]: ...


@dataclass(frozen=True, slots=True)
class _PreparedDetection:
    detection: InstanceDetection
    box_xyxy: tuple[float, float, float, float]
    mask: np.ndarray | None
    mask_area: int
    salience: float


def _coerce_rgb(image: Image.Image | np.ndarray) -> np.ndarray:
    if isinstance(image, Image.Image):
        return np.asarray(image.convert("RGB"), dtype=np.uint8)
    array = np.asarray(image)
    if array.ndim == 2:
        array = np.repeat(array[..., None], 3, axis=2)
    if array.ndim != 3 or array.shape[2] not in (3, 4):
        raise ValueError("image must be HxW RGB/RGBA or HxW grayscale")
    array = array[..., :3]
    if np.issubdtype(array.dtype, np.floating):
        if not np.all(np.isfinite(array)):
            raise ValueError("image contains non-finite pixels")
        scale = 255.0 if array.size and float(array.max()) <= 1.0 else 1.0
        array = array * scale
    return np.clip(array, 0, 255).astype(np.uint8)


def _dark_edge_run(dark_lines: np.ndarray, *, reverse: bool) -> int:
    sequence = dark_lines[::-1] if reverse else dark_lines
    count = 0
    for is_dark in sequence:
        if not bool(is_dark):
            break
        count += 1
    return count


def _paired_bar_width(luma: np.ndarray, *, axis: int) -> int:
    """Return a symmetric bar crop, or zero when the evidence is ambiguous."""
    dark_fraction = np.mean(luma <= _BAR_DARK_LUMA, axis=1 - axis)
    dark_lines = dark_fraction >= _BAR_DARK_FRACTION
    first = _dark_edge_run(dark_lines, reverse=False)
    second = _dark_edge_run(dark_lines, reverse=True)
    dimension = luma.shape[axis]
    minimum = max(2, int(math.ceil(dimension * _MIN_BAR_FRACTION)))
    maximum = int(math.floor(dimension * _MAX_BAR_FRACTION))
    if first < minimum or second < minimum:
        return 0
    crop = min(first, second)
    if crop > maximum or min(first, second) / max(first, second) < 0.75:
        return 0
    if crop * 2 >= dimension * 0.55:
        return 0

    if axis == 0:
        interior = luma[crop : dimension - crop, :]
        edges = np.concatenate((luma[:crop, :].ravel(), luma[-crop:, :].ravel()))
    else:
        interior = luma[:, crop : dimension - crop]
        edges = np.concatenate((luma[:, :crop].ravel(), luma[:, -crop:].ravel()))
    if interior.size == 0:
        return 0
    interior_p90 = float(np.percentile(interior, 90))
    edge_p95 = float(np.percentile(edges, 95))
    if interior_p90 < max(_MIN_INTERIOR_P90, edge_p95 + 10.0):
        return 0
    return crop


def detect_active_picture(image: Image.Image | np.ndarray) -> ActivePictureRect:
    """Conservatively remove only strongly supported paired black bars.

    A dark scene is not enough: both opposing edges must be nearly uniformly
    black, similarly sized, and separated from a visibly brighter interior.
    """
    rgb = _coerce_rgb(image)
    height, width = rgb.shape[:2]
    if height < 4 or width < 4:
        return ActivePictureRect(0, 0, width, height)
    luma = (
        0.2126 * rgb[..., 0].astype(np.float32)
        + 0.7152 * rgb[..., 1].astype(np.float32)
        + 0.0722 * rgb[..., 2].astype(np.float32)
    )
    horizontal = _paired_bar_width(luma, axis=0)
    horizontal_slice = luma[horizontal : height - horizontal, :]
    vertical = _paired_bar_width(horizontal_slice, axis=1)
    return ActivePictureRect(
        left=vertical,
        top=horizontal,
        right=width - vertical,
        bottom=height - horizontal,
    )


def _box_area(box: tuple[float, float, float, float]) -> float:
    return max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])


def _intersection_box(
    box: tuple[float, float, float, float],
    active: ActivePictureRect,
) -> tuple[float, float, float, float]:
    return (
        max(float(active.left), box[0]),
        max(float(active.top), box[1]),
        min(float(active.right), box[2]),
        min(float(active.bottom), box[3]),
    )


def _box_iou(
    first: tuple[float, float, float, float],
    second: tuple[float, float, float, float],
) -> tuple[float, float]:
    intersection = _box_area(
        (
            max(first[0], second[0]),
            max(first[1], second[1]),
            min(first[2], second[2]),
            min(first[3], second[3]),
        )
    )
    first_area = _box_area(first)
    second_area = _box_area(second)
    union = first_area + second_area - intersection
    iou = intersection / union if union > 0.0 else 0.0
    containment = intersection / min(first_area, second_area) if min(first_area, second_area) > 0.0 else 0.0
    return iou, containment


def _mask_overlap(
    first: _PreparedDetection,
    second: _PreparedDetection,
) -> tuple[float, float]:
    if first.mask is None or second.mask is None:
        return 0.0, 0.0
    left = max(0, int(math.floor(max(first.box_xyxy[0], second.box_xyxy[0]))))
    top = max(0, int(math.floor(max(first.box_xyxy[1], second.box_xyxy[1]))))
    right = min(first.mask.shape[1], int(math.ceil(min(first.box_xyxy[2], second.box_xyxy[2]))))
    bottom = min(first.mask.shape[0], int(math.ceil(min(first.box_xyxy[3], second.box_xyxy[3]))))
    if right <= left or bottom <= top:
        return 0.0, 0.0
    intersection = int(
        np.count_nonzero(
            first.mask[top:bottom, left:right]
            & second.mask[top:bottom, left:right]
        )
    )
    union = first.mask_area + second.mask_area - intersection
    iou = intersection / union if union > 0 else 0.0
    containment = intersection / min(first.mask_area, second.mask_area) if min(first.mask_area, second.mask_area) > 0 else 0.0
    return iou, containment


def _local_sharpness(
    rgb: np.ndarray,
    box: tuple[float, float, float, float],
) -> float:
    height, width = rgb.shape[:2]
    left = max(0, min(width - 1, int(math.floor(box[0]))))
    top = max(0, min(height - 1, int(math.floor(box[1]))))
    right = max(left + 1, min(width, int(math.ceil(box[2]))))
    bottom = max(top + 1, min(height, int(math.ceil(box[3]))))
    crop = rgb[top:bottom, left:right].astype(np.float32)
    if crop.shape[0] < 2 or crop.shape[1] < 2:
        return 0.0
    gray = 0.2126 * crop[..., 0] + 0.7152 * crop[..., 1] + 0.0722 * crop[..., 2]
    gradients = np.concatenate(
        (np.abs(np.diff(gray, axis=0)).ravel(), np.abs(np.diff(gray, axis=1)).ravel())
    )
    robust_gradient = float(np.percentile(gradients, 75))
    return max(0.0, min(1.0, 1.0 - math.exp(-robust_gradient / 18.0)))


def _salience(
    rgb: np.ndarray,
    box: tuple[float, float, float, float],
    active: ActivePictureRect,
    detector_score: float,
) -> float:
    area_fraction = _box_area(box) / float(active.width * active.height)
    area_score = math.sqrt(min(1.0, area_fraction / 0.20))
    sharpness = _local_sharpness(rgb, box)
    value = 0.45 * detector_score + 0.35 * area_score + 0.20 * sharpness
    return max(0.0, min(1.0, value))


def _prepare_detections(
    rgb: np.ndarray,
    detections: Sequence[InstanceDetection],
    active: ActivePictureRect,
) -> tuple[_PreparedDetection, ...]:
    height, width = rgb.shape[:2]
    candidates: list[_PreparedDetection] = []
    for detection in detections:
        score = float(detection.score)
        if not math.isfinite(score) or score < DETECTOR_SCORE_THRESHOLD:
            continue
        category = "_".join(str(detection.category).casefold().split())
        if not category or category in {"__background__", "n/a"}:
            continue
        try:
            raw_box = tuple(float(value) for value in detection.box_xyxy)
        except (TypeError, ValueError):
            continue
        if len(raw_box) != 4 or not all(math.isfinite(value) for value in raw_box):
            continue
        decoded_box = (
            max(0.0, min(float(width), raw_box[0])),
            max(0.0, min(float(height), raw_box[1])),
            max(0.0, min(float(width), raw_box[2])),
            max(0.0, min(float(height), raw_box[3])),
        )
        decoded_area = _box_area(decoded_box)
        box = _intersection_box(decoded_box, active)
        active_area = _box_area(box)
        if decoded_area <= 0.0 or active_area / decoded_area < MIN_ACTIVE_BOX_RETENTION:
            continue
        if active_area / float(active.width * active.height) < MIN_ACTIVE_ENTITY_AREA:
            continue

        mask: np.ndarray | None = None
        mask_area = 0
        if detection.mask is not None:
            raw_mask = np.asarray(detection.mask)
            if raw_mask.ndim == 3 and raw_mask.shape[0] == 1:
                raw_mask = raw_mask[0]
            if raw_mask.shape == (height, width):
                mask = raw_mask >= MASK_THRESHOLD
                active_mask = np.zeros_like(mask)
                active_mask[active.top : active.bottom, active.left : active.right] = True
                mask &= active_mask
                mask_area = int(np.count_nonzero(mask))
                if mask_area == 0:
                    mask = None

        candidates.append(
            _PreparedDetection(
                detection=InstanceDetection(
                    detection_id=str(detection.detection_id),
                    category=category,
                    score=score,
                    box_xyxy=decoded_box,
                    mask=detection.mask,
                ),
                box_xyxy=box,
                mask=mask,
                mask_area=mask_area,
                salience=_salience(rgb, box, active, score),
            )
        )

    # Keep the detector's strongest rendering of an instance.  The conservative
    # thresholds preserve overlapping people unless masks/boxes are nearly the
    # same object.
    kept: list[_PreparedDetection] = []
    for candidate in sorted(
        candidates,
        key=lambda value: (-value.detection.score, value.detection.detection_id),
    ):
        duplicate = False
        for prior in kept:
            if candidate.detection.category != prior.detection.category:
                continue
            box_iou, box_containment = _box_iou(candidate.box_xyxy, prior.box_xyxy)
            mask_iou, mask_containment = _mask_overlap(candidate, prior)
            if (
                box_iou >= _DUPLICATE_BOX_IOU
                or mask_iou >= _DUPLICATE_MASK_IOU
                or (
                    box_containment >= _DUPLICATE_CONTAINMENT
                    and mask_containment >= _DUPLICATE_MASK_CONTAINMENT
                )
            ):
                duplicate = True
                break
        if not duplicate:
            kept.append(candidate)

    kept.sort(
        key=lambda value: (-value.salience, -value.detection.score, value.detection.detection_id)
    )
    return tuple(kept[:MAX_LAYOUT_ENTITIES])


def _silhouette(
    mask: np.ndarray | None,
    box: tuple[float, float, float, float],
) -> tuple[float, ...] | None:
    if mask is None:
        return None
    height, width = mask.shape
    left = max(0, min(width - 1, int(math.floor(box[0]))))
    top = max(0, min(height - 1, int(math.floor(box[1]))))
    right = max(left + 1, min(width, int(math.ceil(box[2]))))
    bottom = max(top + 1, min(height, int(math.ceil(box[3]))))
    crop = mask[top:bottom, left:right]
    if not np.any(crop):
        return None
    reduced = np.asarray(
        Image.fromarray(crop.astype(np.uint8) * 255).resize(
            (SILHOUETTE_GRID_SIZE, SILHOUETTE_GRID_SIZE),
            resample=Image.Resampling.BOX,
        ),
        dtype=np.float32,
    ) / 255.0
    if not np.any(reduced > 0.0):
        return None
    return tuple(float(value) for value in reduced.ravel())


def _normalized_box(
    box: tuple[float, float, float, float],
    active: ActivePictureRect,
) -> NormalizedBox:
    return NormalizedBox(
        (box[0] - active.left) / active.width,
        (box[1] - active.top) / active.height,
        (box[2] - active.left) / active.width,
        (box[3] - active.top) / active.height,
    )


def _canonical_keypoint_name(name: str) -> str | None:
    canonical = "_".join(str(name).casefold().replace("-", "_").split())
    aliases = {
        "l_eye": "left_eye",
        "r_eye": "right_eye",
        "l_ear": "left_ear",
        "r_ear": "right_ear",
        "l_shoulder": "left_shoulder",
        "r_shoulder": "right_shoulder",
        "l_elbow": "left_elbow",
        "r_elbow": "right_elbow",
        "l_wrist": "left_wrist",
        "r_wrist": "right_wrist",
        "l_hip": "left_hip",
        "r_hip": "right_hip",
        "l_knee": "left_knee",
        "r_knee": "right_knee",
        "l_ankle": "left_ankle",
        "r_ankle": "right_ankle",
    }
    canonical = aliases.get(canonical, canonical)
    return canonical if canonical in _COCO_KEYPOINT_NAMES else None


def _valid_pose_points(
    pose: PoseDetection | None,
    box: tuple[float, float, float, float],
    active: ActivePictureRect,
) -> tuple[PoseKeypoint, ...]:
    if pose is None:
        return ()
    margin_x = (box[2] - box[0]) * 0.15
    margin_y = (box[3] - box[1]) * 0.15
    by_name: dict[str, KeypointDetection] = {}
    for point in pose.keypoints:
        name = _canonical_keypoint_name(point.name)
        confidence = float(point.confidence)
        if name is None or not math.isfinite(confidence) or confidence < POSE_KEYPOINT_CONFIDENCE:
            continue
        x = float(point.x)
        y = float(point.y)
        if not (math.isfinite(x) and math.isfinite(y)):
            continue
        if not (
            box[0] - margin_x <= x <= box[2] + margin_x
            and box[1] - margin_y <= y <= box[3] + margin_y
            and active.left <= x <= active.right
            and active.top <= y <= active.bottom
        ):
            continue
        prior = by_name.get(name)
        if prior is None or confidence > prior.confidence:
            by_name[name] = KeypointDetection(name, x, y, confidence)
    if len(by_name) < MIN_POSE_KEYPOINTS or not (_BODY_KEYPOINT_NAMES & by_name.keys()):
        return ()
    return tuple(
        PoseKeypoint(
            name=name,
            x=(point.x - active.left) / active.width,
            y=(point.y - active.top) / active.height,
            confidence=point.confidence,
        )
        for name, point in sorted(by_name.items())
    )


def _facing_orientation(
    pose: PoseDetection | None,
    box: tuple[float, float, float, float],
) -> Orientation | None:
    if pose is None:
        return None
    points: dict[str, KeypointDetection] = {}
    for raw in pose.keypoints:
        name = _canonical_keypoint_name(raw.name)
        confidence = float(raw.confidence)
        if name is None or not math.isfinite(confidence) or confidence < FACING_KEYPOINT_CONFIDENCE:
            continue
        if not (math.isfinite(float(raw.x)) and math.isfinite(float(raw.y))):
            continue
        prior = points.get(name)
        if prior is None or confidence > prior.confidence:
            points[name] = KeypointDetection(name, float(raw.x), float(raw.y), confidence)
    nose = points.get("nose")
    if nose is None:
        return None

    anchors: tuple[KeypointDetection, ...] = ()
    for pair in (("left_ear", "right_ear"), ("left_eye", "right_eye")):
        if pair[0] in points and pair[1] in points:
            anchors = (points[pair[0]], points[pair[1]])
            break
    if not anchors:
        for side in ("left", "right"):
            ear = points.get(f"{side}_ear")
            eye = points.get(f"{side}_eye")
            if ear is not None and eye is not None:
                anchors = (ear, eye)
                break
    if not anchors:
        return None

    anchor_x = sum(point.x for point in anchors) / len(anchors)
    anchor_y = sum(point.y for point in anchors) / len(anchors)
    width = max(box[2] - box[0], 1e-6)
    height = max(box[3] - box[1], 1e-6)
    horizontal_offset = (nose.x - anchor_x) / width
    vertical_offset = abs(nose.y - anchor_y) / height
    if not 0.045 <= abs(horizontal_offset) <= 0.45 or vertical_offset > 0.18:
        return None
    landmark_confidence = min(
        nose.confidence,
        *(point.confidence for point in anchors),
    )
    direction_margin = min(1.0, (abs(horizontal_offset) - 0.045) / 0.08)
    confidence = landmark_confidence * direction_margin
    if confidence < MIN_FACING_CONFIDENCE:
        return None
    return Orientation(
        x=1.0 if horizontal_offset > 0.0 else -1.0,
        y=0.0,
        confidence=confidence,
    )


def frame_layout_from_detections(
    image: Image.Image | np.ndarray,
    detections: Sequence[InstanceDetection],
    *,
    poses: Mapping[str, PoseDetection] | None = None,
    active_picture: ActivePictureRect | None = None,
    frame_id: str | None = None,
) -> FrameLayout:
    """Build canonical layout evidence from already-computed model outputs."""
    rgb = _coerce_rgb(image)
    active = active_picture or detect_active_picture(rgb)
    if active.right > rgb.shape[1] or active.bottom > rgb.shape[0]:
        raise ValueError("active-picture rectangle exceeds image bounds")
    selected = _prepare_detections(rgb, detections, active)
    pose_evidence = poses or {}
    entities: list[LayoutEntity] = []
    for index, prepared in enumerate(selected):
        category = prepared.detection.category
        pose = pose_evidence.get(prepared.detection.detection_id) if category == "person" else None
        entities.append(
            LayoutEntity(
                entity_id=f"entity-{index:02d}",
                category=category,
                class_family=_CLASS_FAMILIES.get(category),
                box=_normalized_box(prepared.box_xyxy, active),
                salience=prepared.salience,
                silhouette=_silhouette(prepared.mask, prepared.box_xyxy),
                pose=_valid_pose_points(pose, prepared.box_xyxy, active),
                orientation=_facing_orientation(pose, prepared.box_xyxy),
            )
        )
    return FrameLayout(entities=tuple(entities), frame_id=frame_id)


class _TorchvisionMaskRCNNBackend:
    def __init__(self, device: str | None) -> None:
        import torch
        from torchvision.models.detection import (
            MaskRCNN_ResNet50_FPN_V2_Weights,
            maskrcnn_resnet50_fpn_v2,
        )

        weights = MaskRCNN_ResNet50_FPN_V2_Weights.COCO_V1
        self._device = torch.device(device or ("cuda" if torch.cuda.is_available() else "cpu"))
        self._categories = tuple(weights.meta["categories"])
        self._transform = weights.transforms()
        self._model = maskrcnn_resnet50_fpn_v2(weights=weights).to(self._device).eval()

    def predict(self, rgb: np.ndarray) -> Sequence[InstanceDetection]:
        import torch

        tensor = self._transform(Image.fromarray(rgb)).to(self._device)
        with torch.inference_mode():
            output = self._model([tensor])[0]
        boxes = output["boxes"].detach().cpu().numpy()
        labels = output["labels"].detach().cpu().numpy()
        scores = output["scores"].detach().cpu().numpy()
        masks = output.get("masks")
        mask_values = masks.detach().cpu().numpy() if masks is not None else None
        results: list[InstanceDetection] = []
        for index, (box, label, score) in enumerate(zip(boxes, labels, scores, strict=True)):
            label_index = int(label)
            category = self._categories[label_index] if 0 <= label_index < len(self._categories) else "n/a"
            results.append(
                InstanceDetection(
                    detection_id=f"detector-{index:03d}",
                    category=category,
                    score=float(score),
                    box_xyxy=tuple(float(value) for value in box),
                    mask=None if mask_values is None else mask_values[index, 0],
                )
            )
        return tuple(results)


class _PinnedVitPoseBackend:
    def __init__(self, device: str | None) -> None:
        import torch
        from transformers import VitPoseForPoseEstimation, VitPoseImageProcessor

        self._torch = torch
        self._device = torch.device(device or ("cuda" if torch.cuda.is_available() else "cpu"))
        self._processor = VitPoseImageProcessor.from_pretrained(
            POSE_MODEL_ID,
            revision=POSE_MODEL_REVISION,
        )
        self._model = VitPoseForPoseEstimation.from_pretrained(
            POSE_MODEL_ID,
            revision=POSE_MODEL_REVISION,
        ).to(self._device).eval()

    def predict(
        self,
        rgb: np.ndarray,
        people: Sequence[InstanceDetection],
    ) -> Mapping[str, PoseDetection]:
        if not people:
            return {}
        boxes = [
            [box[0], box[1], box[2] - box[0], box[3] - box[1]]
            for box in (person.box_xyxy for person in people)
        ]
        inputs = self._processor(
            images=[Image.fromarray(rgb)],
            boxes=[boxes],
            return_tensors="pt",
        )
        inputs = {name: value.to(self._device) for name, value in inputs.items()}
        # This pinned ViTPose+ checkpoint has six dataset experts.  Its COCO
        # keypoint head is expert zero and Transformers requires the selector
        # explicitly when more than one expert is present.
        dataset_index = self._torch.zeros(
            inputs["pixel_values"].shape[0],
            dtype=self._torch.long,
            device=self._device,
        )
        with self._torch.inference_mode():
            outputs = self._model(**inputs, dataset_index=dataset_index)
        processed = self._processor.post_process_pose_estimation(outputs, boxes=[boxes])[0]
        results: dict[str, PoseDetection] = {}
        for person, result in zip(people, processed, strict=True):
            keypoints = result["keypoints"].detach().cpu().numpy()
            scores = result["scores"].detach().cpu().numpy()
            labels = result["labels"].detach().cpu().numpy()
            evidence: list[KeypointDetection] = []
            for point, score, label in zip(keypoints, scores, labels, strict=True):
                label_index = int(label)
                if 0 <= label_index < len(_COCO_KEYPOINT_NAMES):
                    evidence.append(
                        KeypointDetection(
                            name=_COCO_KEYPOINT_NAMES[label_index],
                            x=float(point[0]),
                            y=float(point[1]),
                            confidence=float(score),
                        )
                    )
            results[person.detection_id] = PoseDetection(tuple(evidence))
        return results


def _new_detector_backend(device: str | None) -> _DetectorBackend:
    return _TorchvisionMaskRCNNBackend(device)


def _new_pose_backend(device: str | None) -> _PoseBackend:
    return _PinnedVitPoseBackend(device)


class MatchLayoutExtractor:
    """Lazily run the two pinned models and emit a shadow layout payload."""

    def __init__(self, *, device: str | None = None) -> None:
        self._device = device
        self._detector: _DetectorBackend | None = None
        self._pose: _PoseBackend | None = None

    def _detector_backend(self) -> _DetectorBackend:
        if self._detector is None:
            self._detector = _new_detector_backend(self._device)
        return self._detector

    def _pose_backend(self) -> _PoseBackend:
        if self._pose is None:
            self._pose = _new_pose_backend(self._device)
        return self._pose

    def extract(
        self,
        image: Image.Image | np.ndarray,
        *,
        frame_id: str | None = None,
    ) -> FrameLayout:
        rgb = _coerce_rgb(image)
        active = detect_active_picture(rgb)
        detections = tuple(self._detector_backend().predict(rgb))
        selected = _prepare_detections(rgb, detections, active)
        people = tuple(
            prepared.detection
            for prepared in selected
            if prepared.detection.category == "person"
        )
        poses = self._pose_backend().predict(rgb, people) if people else {}
        return frame_layout_from_detections(
            rgb,
            detections,
            poses=poses,
            active_picture=active,
            frame_id=frame_id,
        )
