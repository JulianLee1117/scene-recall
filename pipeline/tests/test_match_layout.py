from __future__ import annotations

import json

import numpy as np
import pytest

from pipeline.search.match_layout import (
    ACTIVE_PICTURE_COORDINATE_SPACE,
    LAYOUT_VECTOR_DIM,
    MAX_LAYOUT_ENTITIES,
    MATCH_LAYOUT_PROFILE_ID,
    FrameLayout,
    LayoutEntity,
    LayoutPayloadError,
    NormalizedBox,
    Orientation,
    PoseKeypoint,
    layout_vector,
    parse_layout_payload,
    score_layout_match,
)


def _pose(box: NormalizedBox, *, frontal: bool = False) -> tuple[PoseKeypoint, ...]:
    if frontal:
        local_points = (
            ("left_shoulder", 0.35, 0.28),
            ("right_shoulder", 0.65, 0.28),
            ("left_hip", 0.40, 0.62),
            ("right_hip", 0.60, 0.62),
        )
    else:
        local_points = (
            ("left_shoulder", 0.24, 0.25),
            ("right_shoulder", 0.42, 0.29),
            ("left_hip", 0.34, 0.65),
            ("right_hip", 0.47, 0.66),
        )
    return tuple(
        PoseKeypoint(
            name,
            box.x_min + local_x * box.width,
            box.y_min + local_y * box.height,
        )
        for name, local_x, local_y in local_points
    )


def _person(
    entity_id: str,
    box: NormalizedBox,
    *,
    pose: bool = True,
    frontal_pose: bool = False,
    orientation: tuple[float, float] | None = (1.0, 0.0),
    salience: float = 1.0,
) -> LayoutEntity:
    return LayoutEntity(
        entity_id=entity_id,
        category="person",
        class_family="person",
        box=box,
        salience=salience,
        pose=_pose(box, frontal=frontal_pose) if pose else (),
        orientation=(
            Orientation(*orientation) if orientation is not None else None
        ),
    )


def _layout(*entities: LayoutEntity, frame_id: str | None = None) -> FrameLayout:
    return FrameLayout(entities=entities, frame_id=frame_id)


def test_same_profile_outranks_position_pose_orientation_and_scale_mismatches() -> None:
    reference_box = NormalizedBox(0.10, 0.18, 0.42, 0.86)
    reference = _layout(_person("reference", reference_box))

    same = _layout(_person("same", reference_box))
    opposite_position_box = NormalizedBox(0.58, 0.18, 0.90, 0.86)
    opposite_position = _layout(
        _person("opposite-position", opposite_position_box)
    )
    frontal = _layout(
        _person(
            "frontal",
            reference_box,
            frontal_pose=True,
            orientation=(0.0, -1.0),
        )
    )
    scale_mismatch_box = NormalizedBox(0.22, 0.39, 0.30, 0.65)
    scale_mismatch = _layout(_person("tiny", scale_mismatch_box))

    same_score = score_layout_match(reference, same).score
    mismatch_scores = (
        score_layout_match(reference, opposite_position).score,
        score_layout_match(reference, frontal).score,
        score_layout_match(reference, scale_mismatch).score,
    )

    assert same_score == pytest.approx(1.0)
    assert all(same_score > mismatch for mismatch in mismatch_scores)


def test_candidate_missing_reference_pose_earns_zero_pose_credit() -> None:
    box = NormalizedBox(0.12, 0.15, 0.44, 0.88)
    reference = _layout(
        _person("reference", box, orientation=None)
    )
    with_pose = score_layout_match(
        reference,
        _layout(_person("with-pose", box, orientation=None)),
    )
    without_pose = score_layout_match(
        reference,
        _layout(_person("without-pose", box, pose=False, orientation=None)),
    )

    pose_evidence = next(
        component
        for component in without_pose.matches[0].components
        if component.name == "pose"
    )
    assert with_pose.score == pytest.approx(1.0)
    assert without_pose.score < with_pose.score
    assert pose_evidence.reference_available is True
    assert pose_evidence.candidate_available is False
    assert pose_evidence.score == 0.0
    assert pose_evidence.normalized_weight > 0.0


def test_unmatched_salient_candidate_entity_is_penalized() -> None:
    box = NormalizedBox(0.08, 0.20, 0.40, 0.86)
    reference = _layout(_person("reference", box))
    exact = score_layout_match(reference, _layout(_person("match", box)))
    extra_box = NormalizedBox(0.67, 0.18, 0.94, 0.88)
    with_extra = score_layout_match(
        reference,
        _layout(
            _person("match", box),
            _person("extra", extra_box, salience=1.0),
        ),
    )

    assert exact.score == pytest.approx(1.0)
    assert with_extra.score < exact.score
    assert with_extra.unmatched_penalty > 0.0
    assert with_extra.unmatched_candidate_ids == ("extra",)


def test_entity_assignment_finds_the_global_crossed_match() -> None:
    left_box = NormalizedBox(0.08, 0.20, 0.35, 0.86)
    right_box = NormalizedBox(0.65, 0.20, 0.92, 0.86)
    reference = _layout(
        _person("reference-left", left_box),
        _person("reference-right", right_box),
    )
    candidate = _layout(
        _person("candidate-a-right", right_box),
        _person("candidate-b-left", left_box),
    )

    result = score_layout_match(reference, candidate)

    assert result.score == pytest.approx(1.0)
    assert {
        (match.reference_entity_id, match.candidate_entity_id)
        for match in result.matches
    } == {
        ("reference-left", "candidate-b-left"),
        ("reference-right", "candidate-a-right"),
    }


def test_payload_round_trip_and_layout_vector_are_order_independent() -> None:
    person_box = NormalizedBox(0.08, 0.20, 0.40, 0.86)
    table = LayoutEntity(
        entity_id="table",
        category="table",
        class_family="furniture",
        box=NormalizedBox(0.50, 0.62, 0.94, 0.91),
        salience=0.65,
    )
    person = _person("person", person_box)
    first = _layout(table, person, frame_id="film::unit::frame::0")
    second = _layout(person, table, frame_id="film::unit::frame::0")

    parsed = parse_layout_payload(json.dumps(first.to_payload()))
    first_vector = layout_vector(parsed)
    second_vector = layout_vector(second)

    assert parsed == first
    assert first_vector.shape == (LAYOUT_VECTOR_DIM,)
    assert first_vector.dtype == np.float32
    assert np.linalg.norm(first_vector) == pytest.approx(1.0)
    np.testing.assert_array_equal(first_vector, second_vector)


def test_payload_rejects_incompatible_coordinate_space() -> None:
    payload = {
        "profile_id": MATCH_LAYOUT_PROFILE_ID,
        "coordinate_space": "decoded-frame-normalized-v1",
        "entities": [
            {
                "entity_id": "person",
                "category": "person",
                "box": {
                    "x_min": 0.1,
                    "y_min": 0.1,
                    "x_max": 0.4,
                    "y_max": 0.9,
                },
            }
        ],
    }

    with pytest.raises(LayoutPayloadError, match="coordinate_space"):
        parse_layout_payload(payload)

    assert ACTIVE_PICTURE_COORDINATE_SPACE != payload["coordinate_space"]


def test_layout_rejects_more_than_the_profile_entity_bound() -> None:
    entities = tuple(
        LayoutEntity(
            entity_id=f"entity-{index}",
            category="person",
            box=NormalizedBox(0.1, 0.1, 0.2, 0.2),
        )
        for index in range(MAX_LAYOUT_ENTITIES + 1)
    )

    with pytest.raises(LayoutPayloadError, match="at most"):
        FrameLayout(entities=entities)
