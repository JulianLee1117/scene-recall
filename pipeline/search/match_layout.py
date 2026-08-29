"""Model-independent layout evidence for shadow match-cut experiments.

``match-layout-v1`` is deliberately isolated from production retrieval.  It
defines the canonical, active-picture-normalized payload that an extractor may
produce, a deterministic coarse vector for candidate generation, and an exact
class-compatible layout scorer.  It does not load a model, persist a profile,
or activate a search path.

The exact scorer is reference-directed.  Optional evidence absent from the
reference is removed from the denominator; optional evidence present on the
reference but absent from a candidate earns zero credit.  This prevents a
candidate with missing pose or mask evidence from receiving a synthetic match.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import hashlib
import json
import math
from typing import Any, Mapping, Sequence

import numpy as np


MATCH_LAYOUT_PROFILE_ID = "match-layout-v1"
ACTIVE_PICTURE_COORDINATE_SPACE = "active-picture-normalized-v1"

# Canonical silhouette evidence is an 8 x 8 occupancy mask over the entity's
# tight box, flattened in row-major order.  Extractors may use any model so long
# as they publish this representation.
SILHOUETTE_GRID_SIZE = 8
SILHOUETTE_VECTOR_DIM = SILHOUETTE_GRID_SIZE**2
MIN_POSE_KEYPOINTS = 2
MAX_LAYOUT_ENTITIES = 8

# These weights are part of match-layout-v1.  Changing one requires a new
# profile version rather than silently changing the meaning of stored scores.
ENTITY_COMPONENT_WEIGHTS: Mapping[str, float] = {
    "center": 0.28,
    "scale": 0.18,
    "aspect": 0.08,
    "silhouette": 0.16,
    "pose": 0.22,
    "orientation": 0.08,
}
GLOBAL_COMPONENT_WEIGHTS: Mapping[str, float] = {
    "entities": 0.82,
    "relations": 0.18,
}
UNMATCHED_SALIENT_PENALTY_WEIGHT = 0.24

CENTER_DISTANCE_SIGMA = 0.22
SCALE_LOG_RATIO_SIGMA = 0.65
ASPECT_LOG_RATIO_SIGMA = 0.55
POSE_LOCAL_DISTANCE_SIGMA = 0.20
ORIENTATION_ANGLE_SIGMA = 0.25
RELATION_DISTANCE_SIGMA = 0.22
RELATION_SCALE_LOG_RATIO_SIGMA = 0.65
RELATION_POSITION_WEIGHT = 0.80
RELATION_SCALE_WEIGHT = 0.20
MIN_ENTITY_MATCH_SCORE = 0.05

# The coarse vector is a two-projection class sketch plus class-agnostic
# center/area grids.  Stable BLAKE2 hashing avoids Python's randomized hash.
LAYOUT_GRID_SIZE = 4
LAYOUT_HASH_PROJECTIONS = 2
LAYOUT_HASH_BUCKETS = 16
LAYOUT_SCALE_ANCHORS = (0.10, 0.35, 0.70)
LAYOUT_ASPECT_ANCHORS = (-1.0, 0.0, 1.0)
_LAYOUT_BUCKET_DIM = (
    LAYOUT_GRID_SIZE**2
    + len(LAYOUT_SCALE_ANCHORS)
    + len(LAYOUT_ASPECT_ANCHORS)
)
LAYOUT_VECTOR_DIM = (
    LAYOUT_HASH_PROJECTIONS * LAYOUT_HASH_BUCKETS * _LAYOUT_BUCKET_DIM
    + 2 * LAYOUT_GRID_SIZE**2
)


class LayoutPayloadError(ValueError):
    """A match-layout payload violates the versioned canonical contract."""


def _number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise LayoutPayloadError(f"{field} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise LayoutPayloadError(f"{field} must be a finite number")
    return result


def _unit_interval(value: Any, field: str) -> float:
    result = _number(value, field)
    if not 0.0 <= result <= 1.0:
        raise LayoutPayloadError(f"{field} must be between 0 and 1")
    return result


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise LayoutPayloadError(f"{field} must be an object")
    return value


def _sequence(value: Any, field: str) -> Sequence[Any]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise LayoutPayloadError(f"{field} must be an array")
    return value


def _known_fields(
    payload: Mapping[str, Any],
    allowed: set[str],
    field: str,
) -> None:
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise LayoutPayloadError(
            f"{field} contains unknown field(s): {', '.join(unknown)}"
        )


def _required_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise LayoutPayloadError(f"{field} must be a non-empty string")
    return value.strip()


def _canonical_label(value: Any, field: str) -> str:
    raw = _required_string(value, field)
    return "_".join(raw.casefold().split())


@dataclass(frozen=True, slots=True)
class NormalizedBox:
    """An entity box in active-picture coordinates, not decoded-frame pixels."""

    x_min: float
    y_min: float
    x_max: float
    y_max: float

    def __post_init__(self) -> None:
        for name in ("x_min", "y_min", "x_max", "y_max"):
            object.__setattr__(
                self,
                name,
                _unit_interval(getattr(self, name), f"box.{name}"),
            )
        if self.x_max <= self.x_min or self.y_max <= self.y_min:
            raise LayoutPayloadError("box must have positive width and height")

    @classmethod
    def from_payload(cls, payload: Any) -> NormalizedBox:
        values = _mapping(payload, "entity.box")
        _known_fields(
            values,
            {"x_min", "y_min", "x_max", "y_max"},
            "entity.box",
        )
        try:
            return cls(
                x_min=values["x_min"],
                y_min=values["y_min"],
                x_max=values["x_max"],
                y_max=values["y_max"],
            )
        except KeyError as exc:
            raise LayoutPayloadError(
                f"entity.box is missing {exc.args[0]!r}"
            ) from exc

    @property
    def width(self) -> float:
        return self.x_max - self.x_min

    @property
    def height(self) -> float:
        return self.y_max - self.y_min

    @property
    def area(self) -> float:
        return self.width * self.height

    @property
    def aspect_ratio(self) -> float:
        return self.width / self.height

    @property
    def center(self) -> tuple[float, float]:
        return (
            (self.x_min + self.x_max) / 2.0,
            (self.y_min + self.y_max) / 2.0,
        )

    def to_payload(self) -> dict[str, float]:
        return {
            "x_min": self.x_min,
            "y_min": self.y_min,
            "x_max": self.x_max,
            "y_max": self.y_max,
        }


@dataclass(frozen=True, slots=True)
class PoseKeypoint:
    name: str
    x: float
    y: float
    confidence: float = 1.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _canonical_label(self.name, "pose.name"))
        object.__setattr__(self, "x", _unit_interval(self.x, "pose.x"))
        object.__setattr__(self, "y", _unit_interval(self.y, "pose.y"))
        object.__setattr__(
            self,
            "confidence",
            _unit_interval(self.confidence, "pose.confidence"),
        )

    @classmethod
    def from_payload(cls, payload: Any) -> PoseKeypoint:
        values = _mapping(payload, "entity.pose[]")
        _known_fields(
            values,
            {"name", "x", "y", "confidence"},
            "entity.pose[]",
        )
        try:
            return cls(
                name=values["name"],
                x=values["x"],
                y=values["y"],
                confidence=values.get("confidence", 1.0),
            )
        except KeyError as exc:
            raise LayoutPayloadError(
                f"entity.pose[] is missing {exc.args[0]!r}"
            ) from exc

    def to_payload(self) -> dict[str, float | str]:
        return {
            "name": self.name,
            "x": self.x,
            "y": self.y,
            "confidence": self.confidence,
        }


@dataclass(frozen=True, slots=True)
class Orientation:
    """A confidence-weighted image-plane facing direction."""

    x: float
    y: float
    confidence: float = 1.0

    def __post_init__(self) -> None:
        x = _number(self.x, "orientation.x")
        y = _number(self.y, "orientation.y")
        magnitude = math.hypot(x, y)
        if magnitude <= 1e-8:
            raise LayoutPayloadError("orientation direction must be non-zero")
        object.__setattr__(self, "x", x / magnitude)
        object.__setattr__(self, "y", y / magnitude)
        object.__setattr__(
            self,
            "confidence",
            _unit_interval(self.confidence, "orientation.confidence"),
        )

    @classmethod
    def from_payload(cls, payload: Any) -> Orientation:
        values = _mapping(payload, "entity.orientation")
        _known_fields(
            values,
            {"x", "y", "confidence"},
            "entity.orientation",
        )
        try:
            return cls(
                x=values["x"],
                y=values["y"],
                confidence=values.get("confidence", 1.0),
            )
        except KeyError as exc:
            raise LayoutPayloadError(
                f"entity.orientation is missing {exc.args[0]!r}"
            ) from exc

    def to_payload(self) -> dict[str, float]:
        return {
            "x": self.x,
            "y": self.y,
            "confidence": self.confidence,
        }


@dataclass(frozen=True, slots=True)
class LayoutEntity:
    entity_id: str
    category: str
    box: NormalizedBox
    salience: float = 1.0
    class_family: str | None = None
    silhouette: tuple[float, ...] | None = None
    pose: tuple[PoseKeypoint, ...] = ()
    orientation: Orientation | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "entity_id",
            _required_string(self.entity_id, "entity.entity_id"),
        )
        object.__setattr__(
            self,
            "category",
            _canonical_label(self.category, "entity.category"),
        )
        if not isinstance(self.box, NormalizedBox):
            raise LayoutPayloadError("entity.box must be a NormalizedBox")
        object.__setattr__(
            self,
            "salience",
            _unit_interval(self.salience, "entity.salience"),
        )
        if self.class_family is not None:
            object.__setattr__(
                self,
                "class_family",
                _canonical_label(self.class_family, "entity.class_family"),
            )

        if self.silhouette is not None:
            silhouette = tuple(
                _unit_interval(value, f"entity.silhouette[{index}]")
                for index, value in enumerate(self.silhouette)
            )
            if len(silhouette) != SILHOUETTE_VECTOR_DIM:
                raise LayoutPayloadError(
                    "entity.silhouette must contain exactly "
                    f"{SILHOUETTE_VECTOR_DIM} values"
                )
            if not any(silhouette):
                raise LayoutPayloadError("entity.silhouette cannot be all zero")
            object.__setattr__(self, "silhouette", silhouette)

        pose = tuple(sorted(self.pose, key=lambda point: point.name))
        if not all(isinstance(point, PoseKeypoint) for point in pose):
            raise LayoutPayloadError("entity.pose must contain PoseKeypoint values")
        names = [point.name for point in pose]
        if len(set(names)) != len(names):
            raise LayoutPayloadError("entity.pose keypoint names must be unique")
        object.__setattr__(self, "pose", pose)
        if self.orientation is not None and not isinstance(
            self.orientation, Orientation
        ):
            raise LayoutPayloadError(
                "entity.orientation must be an Orientation"
            )

    @classmethod
    def from_payload(cls, payload: Any) -> LayoutEntity:
        values = _mapping(payload, "entities[]")
        _known_fields(
            values,
            {
                "entity_id",
                "category",
                "class_family",
                "box",
                "salience",
                "silhouette",
                "pose",
                "orientation",
            },
            "entities[]",
        )
        try:
            raw_silhouette = values.get("silhouette")
            silhouette = (
                None
                if raw_silhouette is None
                else tuple(
                    _number(value, f"entity.silhouette[{index}]")
                    for index, value in enumerate(
                        _sequence(raw_silhouette, "entity.silhouette")
                    )
                )
            )
            raw_pose = _sequence(values.get("pose", ()), "entity.pose")
            raw_orientation = values.get("orientation")
            return cls(
                entity_id=values["entity_id"],
                category=values["category"],
                class_family=values.get("class_family"),
                box=NormalizedBox.from_payload(values["box"]),
                salience=values.get("salience", 1.0),
                silhouette=silhouette,
                pose=tuple(PoseKeypoint.from_payload(point) for point in raw_pose),
                orientation=(
                    None
                    if raw_orientation is None
                    else Orientation.from_payload(raw_orientation)
                ),
            )
        except KeyError as exc:
            raise LayoutPayloadError(
                f"entities[] is missing {exc.args[0]!r}"
            ) from exc

    @property
    def match_class(self) -> str:
        return self.class_family or self.category

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "entity_id": self.entity_id,
            "category": self.category,
            "box": self.box.to_payload(),
            "salience": self.salience,
        }
        if self.class_family is not None:
            payload["class_family"] = self.class_family
        if self.silhouette is not None:
            payload["silhouette"] = list(self.silhouette)
        if self.pose:
            payload["pose"] = [point.to_payload() for point in self.pose]
        if self.orientation is not None:
            payload["orientation"] = self.orientation.to_payload()
        return payload


@dataclass(frozen=True, slots=True)
class FrameLayout:
    entities: tuple[LayoutEntity, ...]
    frame_id: str | None = None
    profile_id: str = MATCH_LAYOUT_PROFILE_ID
    coordinate_space: str = ACTIVE_PICTURE_COORDINATE_SPACE

    def __post_init__(self) -> None:
        if self.profile_id != MATCH_LAYOUT_PROFILE_ID:
            raise LayoutPayloadError(
                f"profile_id must be {MATCH_LAYOUT_PROFILE_ID!r}"
            )
        if self.coordinate_space != ACTIVE_PICTURE_COORDINATE_SPACE:
            raise LayoutPayloadError(
                "coordinate_space must be "
                f"{ACTIVE_PICTURE_COORDINATE_SPACE!r}"
            )
        if self.frame_id is not None:
            object.__setattr__(
                self,
                "frame_id",
                _required_string(self.frame_id, "frame_id"),
            )
        entities = tuple(self.entities)
        if not all(isinstance(entity, LayoutEntity) for entity in entities):
            raise LayoutPayloadError("entities must contain LayoutEntity values")
        if len(entities) > MAX_LAYOUT_ENTITIES:
            raise LayoutPayloadError(
                f"entities must contain at most {MAX_LAYOUT_ENTITIES} values"
            )
        entities = tuple(sorted(entities, key=lambda entity: entity.entity_id))
        entity_ids = [entity.entity_id for entity in entities]
        if len(set(entity_ids)) != len(entity_ids):
            raise LayoutPayloadError("entity_id values must be unique per frame")
        object.__setattr__(self, "entities", entities)

    @classmethod
    def from_payload(cls, payload: Any) -> FrameLayout:
        values = _mapping(payload, "layout")
        _known_fields(
            values,
            {"profile_id", "coordinate_space", "frame_id", "entities"},
            "layout",
        )
        try:
            entities = _sequence(values["entities"], "entities")
            return cls(
                profile_id=values["profile_id"],
                coordinate_space=values["coordinate_space"],
                frame_id=values.get("frame_id"),
                entities=tuple(
                    LayoutEntity.from_payload(entity) for entity in entities
                ),
            )
        except KeyError as exc:
            raise LayoutPayloadError(
                f"layout is missing {exc.args[0]!r}"
            ) from exc

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "profile_id": self.profile_id,
            "coordinate_space": self.coordinate_space,
            "entities": [entity.to_payload() for entity in self.entities],
        }
        if self.frame_id is not None:
            payload["frame_id"] = self.frame_id
        return payload


def parse_layout_payload(payload: Mapping[str, Any] | str | bytes) -> FrameLayout:
    """Parse JSON or a mapping into a strictly versioned canonical layout."""
    if isinstance(payload, (str, bytes)):
        try:
            payload = json.loads(payload)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise LayoutPayloadError("layout payload is not valid JSON") from exc
    return FrameLayout.from_payload(payload)


@dataclass(frozen=True, slots=True)
class ComponentEvidence:
    name: str
    score: float | None
    configured_weight: float
    normalized_weight: float
    reference_available: bool
    candidate_available: bool

    def to_debug_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "configured_weight": self.configured_weight,
            "normalized_weight": self.normalized_weight,
            "reference_available": self.reference_available,
            "candidate_available": self.candidate_available,
        }


@dataclass(frozen=True, slots=True)
class EntityMatchEvidence:
    reference_entity_id: str
    candidate_entity_id: str
    match_class: str
    score: float
    components: tuple[ComponentEvidence, ...]

    def to_debug_dict(self) -> dict[str, Any]:
        return {
            "reference_entity_id": self.reference_entity_id,
            "candidate_entity_id": self.candidate_entity_id,
            "match_class": self.match_class,
            "score": self.score,
            "components": {
                component.name: component.to_debug_dict()
                for component in self.components
            },
        }


@dataclass(frozen=True, slots=True)
class LayoutMatchScore:
    score: float
    base_score: float
    entity_score: float
    relation_score: float | None
    unmatched_penalty: float
    unmatched_reference_salience: float
    unmatched_candidate_salience: float
    matches: tuple[EntityMatchEvidence, ...]
    unmatched_reference_ids: tuple[str, ...]
    unmatched_candidate_ids: tuple[str, ...]

    def to_debug_dict(self) -> dict[str, Any]:
        return {
            "profile_id": MATCH_LAYOUT_PROFILE_ID,
            "score": self.score,
            "base_score": self.base_score,
            "entity_score": self.entity_score,
            "relation_score": self.relation_score,
            "unmatched_penalty": self.unmatched_penalty,
            "unmatched_reference_salience": self.unmatched_reference_salience,
            "unmatched_candidate_salience": self.unmatched_candidate_salience,
            "matches": [match.to_debug_dict() for match in self.matches],
            "unmatched_reference_ids": list(self.unmatched_reference_ids),
            "unmatched_candidate_ids": list(self.unmatched_candidate_ids),
        }


def _gaussian(delta: float, sigma: float) -> float:
    return math.exp(-0.5 * (delta / sigma) ** 2)


def _classes_compatible(
    reference: LayoutEntity,
    candidate: LayoutEntity,
) -> bool:
    if reference.category == candidate.category:
        return True
    return (
        reference.class_family is not None
        and candidate.class_family is not None
        and reference.class_family == candidate.class_family
    )


def _pose_score(
    reference: LayoutEntity,
    candidate: LayoutEntity,
) -> tuple[bool, bool, float | None]:
    reference_points = {
        point.name: point
        for point in reference.pose
        if point.confidence > 0.0
    }
    reference_available = len(reference_points) >= MIN_POSE_KEYPOINTS
    if not reference_available:
        return False, False, None
    candidate_points = {
        point.name: point
        for point in candidate.pose
        if point.confidence > 0.0
    }
    candidate_available = len(candidate_points) >= MIN_POSE_KEYPOINTS
    if not candidate_available:
        return True, False, 0.0

    weighted_score = 0.0
    reference_weight = 0.0
    for name, reference_point in reference_points.items():
        reference_weight += reference_point.confidence
        candidate_point = candidate_points.get(name)
        if candidate_point is None:
            continue
        reference_local_x = (
            reference_point.x - reference.box.x_min
        ) / reference.box.width
        reference_local_y = (
            reference_point.y - reference.box.y_min
        ) / reference.box.height
        candidate_local_x = (
            candidate_point.x - candidate.box.x_min
        ) / candidate.box.width
        candidate_local_y = (
            candidate_point.y - candidate.box.y_min
        ) / candidate.box.height
        distance = math.hypot(
            reference_local_x - candidate_local_x,
            reference_local_y - candidate_local_y,
        ) / math.sqrt(2.0)
        weighted_score += (
            reference_point.confidence
            * candidate_point.confidence
            * _gaussian(distance, POSE_LOCAL_DISTANCE_SIGMA)
        )
    return True, True, weighted_score / max(reference_weight, 1e-12)


def _orientation_score(
    reference: LayoutEntity,
    candidate: LayoutEntity,
) -> tuple[bool, bool, float | None]:
    if reference.orientation is None or reference.orientation.confidence <= 0.0:
        return False, False, None
    if candidate.orientation is None or candidate.orientation.confidence <= 0.0:
        return True, False, 0.0
    dot = max(
        -1.0,
        min(
            1.0,
            reference.orientation.x * candidate.orientation.x
            + reference.orientation.y * candidate.orientation.y,
        ),
    )
    normalized_angle = math.acos(dot) / math.pi
    score = (
        _gaussian(normalized_angle, ORIENTATION_ANGLE_SIGMA)
        * candidate.orientation.confidence
    )
    return True, True, score


def _silhouette_score(
    reference: LayoutEntity,
    candidate: LayoutEntity,
) -> tuple[bool, bool, float | None]:
    if reference.silhouette is None:
        return False, False, None
    if candidate.silhouette is None:
        return True, False, 0.0
    squared_error = sum(
        (reference_value - candidate_value) ** 2
        for reference_value, candidate_value in zip(
            reference.silhouette,
            candidate.silhouette,
            strict=True,
        )
    ) / SILHOUETTE_VECTOR_DIM
    return True, True, max(0.0, 1.0 - math.sqrt(squared_error))


def _entity_pair_score(
    reference: LayoutEntity,
    candidate: LayoutEntity,
) -> EntityMatchEvidence:
    reference_center = reference.box.center
    candidate_center = candidate.box.center
    center_distance = math.hypot(
        reference_center[0] - candidate_center[0],
        reference_center[1] - candidate_center[1],
    ) / math.sqrt(2.0)
    raw_components: list[
        tuple[str, bool, bool, float | None]
    ] = [
        (
            "center",
            True,
            True,
            _gaussian(center_distance, CENTER_DISTANCE_SIGMA),
        ),
        (
            "scale",
            True,
            True,
            _gaussian(
                abs(math.log(candidate.box.area / reference.box.area)),
                SCALE_LOG_RATIO_SIGMA,
            ),
        ),
        (
            "aspect",
            True,
            True,
            _gaussian(
                abs(
                    math.log(
                        candidate.box.aspect_ratio / reference.box.aspect_ratio
                    )
                ),
                ASPECT_LOG_RATIO_SIGMA,
            ),
        ),
    ]
    silhouette_available = _silhouette_score(reference, candidate)
    raw_components.append(("silhouette", *silhouette_available))
    pose_available = _pose_score(reference, candidate)
    raw_components.append(("pose", *pose_available))
    orientation_available = _orientation_score(reference, candidate)
    raw_components.append(("orientation", *orientation_available))

    applied_weight = sum(
        ENTITY_COMPONENT_WEIGHTS[name]
        for name, reference_available, _candidate_available, _score in raw_components
        if reference_available
    )
    components: list[ComponentEvidence] = []
    pair_score = 0.0
    for name, reference_available, candidate_available, score in raw_components:
        normalized_weight = (
            ENTITY_COMPONENT_WEIGHTS[name] / applied_weight
            if reference_available and applied_weight > 0.0
            else 0.0
        )
        components.append(
            ComponentEvidence(
                name=name,
                score=score,
                configured_weight=ENTITY_COMPONENT_WEIGHTS[name],
                normalized_weight=normalized_weight,
                reference_available=reference_available,
                candidate_available=candidate_available,
            )
        )
        if score is not None:
            pair_score += normalized_weight * score

    return EntityMatchEvidence(
        reference_entity_id=reference.entity_id,
        candidate_entity_id=candidate.entity_id,
        match_class=reference.match_class,
        score=max(0.0, min(1.0, pair_score)),
        components=tuple(components),
    )


def _assign_entities(
    reference: FrameLayout,
    candidate: FrameLayout,
) -> tuple[EntityMatchEvidence, ...]:
    reference_count = len(reference.entities)
    candidate_count = len(candidate.entities)
    if reference_count == 0 or candidate_count == 0:
        return ()
    pair_scores: dict[tuple[int, int], EntityMatchEvidence] = {}
    utilities: dict[tuple[int, int], float] = {}

    for reference_index, reference_entity in enumerate(reference.entities):
        for candidate_index, candidate_entity in enumerate(candidate.entities):
            if not _classes_compatible(reference_entity, candidate_entity):
                continue
            evidence = _entity_pair_score(reference_entity, candidate_entity)
            salience = math.sqrt(
                reference_entity.salience * candidate_entity.salience
            )
            utility = evidence.score * salience
            if evidence.score < MIN_ENTITY_MATCH_SCORE or utility <= 0.0:
                continue
            pair = (reference_index, candidate_index)
            pair_scores[pair] = evidence
            utilities[pair] = utility

    # The canonical profile permits at most eight entities. A small bitmask
    # assignment is exact at that bound and avoids a heavy runtime dependency.
    @lru_cache(maxsize=None)
    def best_assignment(
        reference_index: int,
        used_candidates: int,
    ) -> tuple[float, tuple[tuple[int, int], ...]]:
        if reference_index == reference_count:
            return 0.0, ()

        best_utility, best_pairs = best_assignment(
            reference_index + 1,
            used_candidates,
        )
        for candidate_index in range(candidate_count):
            candidate_bit = 1 << candidate_index
            pair = (reference_index, candidate_index)
            if used_candidates & candidate_bit or pair not in utilities:
                continue
            remaining_utility, remaining_pairs = best_assignment(
                reference_index + 1,
                used_candidates | candidate_bit,
            )
            total_utility = utilities[pair] + remaining_utility
            if total_utility > best_utility:
                best_utility = total_utility
                best_pairs = (pair, *remaining_pairs)
        return best_utility, best_pairs

    _utility, assignments = best_assignment(0, 0)
    return tuple(pair_scores[pair] for pair in assignments)


def _relation_score(
    reference: FrameLayout,
    candidate: FrameLayout,
    matches: Sequence[EntityMatchEvidence],
) -> float | None:
    salient_reference = [
        entity for entity in reference.entities if entity.salience > 0.0
    ]
    if len(salient_reference) < 2:
        return None

    reference_by_id = {entity.entity_id: entity for entity in reference.entities}
    candidate_by_id = {entity.entity_id: entity for entity in candidate.entities}
    assignment = {
        match.reference_entity_id: match.candidate_entity_id for match in matches
    }
    weighted_score = 0.0
    total_weight = 0.0
    for first_index, first_reference in enumerate(salient_reference[:-1]):
        for second_reference in salient_reference[first_index + 1 :]:
            relation_weight = math.sqrt(
                first_reference.salience * second_reference.salience
            )
            total_weight += relation_weight
            first_candidate_id = assignment.get(first_reference.entity_id)
            second_candidate_id = assignment.get(second_reference.entity_id)
            if first_candidate_id is None or second_candidate_id is None:
                continue
            first_candidate = candidate_by_id[first_candidate_id]
            second_candidate = candidate_by_id[second_candidate_id]

            first_reference_center = reference_by_id[
                first_reference.entity_id
            ].box.center
            second_reference_center = reference_by_id[
                second_reference.entity_id
            ].box.center
            first_candidate_center = first_candidate.box.center
            second_candidate_center = second_candidate.box.center
            reference_delta = (
                second_reference_center[0] - first_reference_center[0],
                second_reference_center[1] - first_reference_center[1],
            )
            candidate_delta = (
                second_candidate_center[0] - first_candidate_center[0],
                second_candidate_center[1] - first_candidate_center[1],
            )
            displacement_error = math.hypot(
                reference_delta[0] - candidate_delta[0],
                reference_delta[1] - candidate_delta[1],
            ) / math.sqrt(2.0)
            position_score = _gaussian(
                displacement_error,
                RELATION_DISTANCE_SIGMA,
            )

            reference_area_ratio = (
                second_reference.box.area / first_reference.box.area
            )
            candidate_area_ratio = second_candidate.box.area / first_candidate.box.area
            scale_score = _gaussian(
                abs(math.log(candidate_area_ratio / reference_area_ratio)),
                RELATION_SCALE_LOG_RATIO_SIGMA,
            )
            weighted_score += relation_weight * (
                RELATION_POSITION_WEIGHT * position_score
                + RELATION_SCALE_WEIGHT * scale_score
            )
    if total_weight <= 0.0:
        return None
    return weighted_score / total_weight


def score_layout_match(
    reference: FrameLayout,
    candidate: FrameLayout,
) -> LayoutMatchScore:
    """Compute exact class-compatible layout similarity with debug evidence."""
    if reference.profile_id != candidate.profile_id:
        raise ValueError("layout profiles must match")
    if reference.coordinate_space != candidate.coordinate_space:
        raise ValueError("layout coordinate spaces must match")

    matches = _assign_entities(reference, candidate)
    reference_by_id = {entity.entity_id: entity for entity in reference.entities}
    candidate_by_id = {entity.entity_id: entity for entity in candidate.entities}
    matched_reference_ids = {
        match.reference_entity_id for match in matches
    }
    matched_candidate_ids = {
        match.candidate_entity_id for match in matches
    }

    match_weights = [
        math.sqrt(
            reference_by_id[match.reference_entity_id].salience
            * candidate_by_id[match.candidate_entity_id].salience
        )
        for match in matches
    ]
    total_match_weight = sum(match_weights)
    entity_score = (
        sum(
            match.score * weight
            for match, weight in zip(matches, match_weights, strict=True)
        )
        / total_match_weight
        if total_match_weight > 0.0
        else 0.0
    )

    relation_score = _relation_score(reference, candidate, matches)
    global_weight = GLOBAL_COMPONENT_WEIGHTS["entities"]
    weighted_base = GLOBAL_COMPONENT_WEIGHTS["entities"] * entity_score
    if relation_score is not None:
        global_weight += GLOBAL_COMPONENT_WEIGHTS["relations"]
        weighted_base += GLOBAL_COMPONENT_WEIGHTS["relations"] * relation_score
    base_score = weighted_base / global_weight

    unmatched_reference = tuple(
        entity
        for entity in reference.entities
        if entity.entity_id not in matched_reference_ids
    )
    unmatched_candidate = tuple(
        entity
        for entity in candidate.entities
        if entity.entity_id not in matched_candidate_ids
    )
    total_reference_salience = sum(
        entity.salience for entity in reference.entities
    )
    total_candidate_salience = sum(
        entity.salience for entity in candidate.entities
    )
    unmatched_reference_salience = (
        sum(entity.salience for entity in unmatched_reference)
        / total_reference_salience
        if total_reference_salience > 0.0
        else 0.0
    )
    unmatched_candidate_salience = (
        sum(entity.salience for entity in unmatched_candidate)
        / total_candidate_salience
        if total_candidate_salience > 0.0
        else 0.0
    )
    unmatched_penalty = UNMATCHED_SALIENT_PENALTY_WEIGHT * (
        unmatched_reference_salience + unmatched_candidate_salience
    ) / 2.0
    score = max(0.0, min(1.0, base_score - unmatched_penalty))

    return LayoutMatchScore(
        score=score,
        base_score=base_score,
        entity_score=entity_score,
        relation_score=relation_score,
        unmatched_penalty=unmatched_penalty,
        unmatched_reference_salience=unmatched_reference_salience,
        unmatched_candidate_salience=unmatched_candidate_salience,
        matches=matches,
        unmatched_reference_ids=tuple(
            entity.entity_id for entity in unmatched_reference
        ),
        unmatched_candidate_ids=tuple(
            entity.entity_id for entity in unmatched_candidate
        ),
    )


def _bilinear_grid(x: float, y: float) -> np.ndarray:
    grid = np.zeros((LAYOUT_GRID_SIZE, LAYOUT_GRID_SIZE), dtype=np.float64)
    grid_x = x * (LAYOUT_GRID_SIZE - 1)
    grid_y = y * (LAYOUT_GRID_SIZE - 1)
    left = int(math.floor(grid_x))
    top = int(math.floor(grid_y))
    right = min(left + 1, LAYOUT_GRID_SIZE - 1)
    bottom = min(top + 1, LAYOUT_GRID_SIZE - 1)
    horizontal = grid_x - left
    vertical = grid_y - top
    grid[top, left] += (1.0 - horizontal) * (1.0 - vertical)
    grid[top, right] += horizontal * (1.0 - vertical)
    grid[bottom, left] += (1.0 - horizontal) * vertical
    grid[bottom, right] += horizontal * vertical
    return grid.reshape(-1)


def _hash_bucket(match_class: str, projection: int) -> int:
    digest = hashlib.blake2s(
        f"{MATCH_LAYOUT_PROFILE_ID}:{projection}:{match_class}".encode("utf-8"),
        digest_size=4,
    ).digest()
    return int.from_bytes(digest, "little") % LAYOUT_HASH_BUCKETS


def layout_vector(layout: FrameLayout) -> np.ndarray:
    """Return a deterministic fixed-width coarse vector for shadow ANN tests."""
    vector = np.zeros(LAYOUT_VECTOR_DIM, dtype=np.float64)
    hashed_length = (
        LAYOUT_HASH_PROJECTIONS * LAYOUT_HASH_BUCKETS * _LAYOUT_BUCKET_DIM
    )
    generic_center_offset = hashed_length
    generic_area_offset = generic_center_offset + LAYOUT_GRID_SIZE**2

    for entity in layout.entities:
        center_x, center_y = entity.box.center
        center_basis = _bilinear_grid(center_x, center_y)
        scale = math.sqrt(entity.box.area)
        scale_basis = np.asarray(
            [_gaussian(scale - anchor, 0.20) for anchor in LAYOUT_SCALE_ANCHORS]
        )
        log_aspect = math.log(entity.box.aspect_ratio)
        aspect_basis = np.asarray(
            [
                _gaussian(log_aspect - anchor, 0.75)
                for anchor in LAYOUT_ASPECT_ANCHORS
            ]
        )
        entity_basis = np.concatenate(
            (center_basis, scale_basis, aspect_basis)
        )
        for projection in range(LAYOUT_HASH_PROJECTIONS):
            bucket = _hash_bucket(entity.match_class, projection)
            offset = (
                projection * LAYOUT_HASH_BUCKETS + bucket
            ) * _LAYOUT_BUCKET_DIM
            vector[offset : offset + _LAYOUT_BUCKET_DIM] += (
                entity.salience * entity_basis
            )
        vector[
            generic_center_offset : generic_center_offset + LAYOUT_GRID_SIZE**2
        ] += entity.salience * center_basis
        vector[
            generic_area_offset : generic_area_offset + LAYOUT_GRID_SIZE**2
        ] += entity.salience * scale * center_basis

    norm = float(np.linalg.norm(vector))
    if norm > 0.0:
        vector /= norm
    return vector.astype(np.float32)
