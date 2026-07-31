"""Tests for the human-reviewed real-film evaluation workflow."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

from pipeline.eval.review import (
    build_review_document,
    format_timecode,
    load_query_set,
    metrics_for_grades,
    score_review_document,
)


def _result(unit_id: str, t_start: float = 10.25) -> dict:
    return {
        "unit_id": unit_id,
        "film_id": "film-a",
        "t_start": t_start,
        "t_end": t_start + 2.5,
        "caption": f"caption for {unit_id}",
        "keyframe_url": f"/keyframe/{unit_id}",
        "preview_url": f"/preview/{unit_id}",
        "matched_frame_index": 2,
        "matched_frame_timestamp": t_start + 2.0,
    }


def test_seed_file_has_42_unique_categorized_queries() -> None:
    path = Path("pipeline/eval/fallen_angels_queries.yaml")
    queries, metadata = load_query_set(path)

    assert len(queries) == 42
    assert len({query["id"] for query in queries}) == 42
    assert len({query["category"] for query in queries}) == 7
    assert metadata["film"]["title"] == "Fallen Angels"


def test_load_query_set_rejects_duplicate_ids(tmp_path: Path) -> None:
    path = tmp_path / "queries.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "queries": [
                    {"id": "same", "category": "vibe", "query": "one"},
                    {"id": "same", "category": "vibe", "query": "two"},
                ]
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="duplicate query id"):
        load_query_set(path)


def test_format_timecode_rounds_to_tenth() -> None:
    assert format_timecode(3661.26) == "01:01:01.3"
    assert format_timecode(-2) == "00:00:00.0"


def test_build_review_document_preserves_evidence_and_blank_labels() -> None:
    queries = [{"id": "q1", "category": "action", "query": "smoking"}]
    search_fn = MagicMock(return_value=[_result("u1"), _result("u2")])

    document = build_review_document(
        queries,
        MagicMock(),
        MagicMock(),
        limit=1,
        search_fn=search_fn,
        created_at="2026-07-25T00:00:00+00:00",
    )

    search_fn.assert_called_once()
    candidates = document["queries"][0]["candidates"]
    assert len(candidates) == 1
    assert candidates[0]["unit_id"] == "u1"
    assert candidates[0]["rank"] == 1
    assert candidates[0]["timecode"] == "00:00:10.2–00:00:12.8"
    assert candidates[0]["matched_frame_index"] == 2
    assert candidates[0]["matched_frame_timestamp"] == pytest.approx(12.25)
    assert candidates[0]["grade"] is None
    assert candidates[0]["flags"] == []
    assert candidates[0]["note"] == ""


def test_build_review_document_forwards_explicit_film_scope() -> None:
    queries = [{"id": "q1", "category": "action", "query": "smoking"}]
    search_fn = MagicMock(return_value=[])
    db = MagicMock()
    config = MagicMock()

    build_review_document(
        queries,
        db,
        config,
        film_ids=["film-a"],
        search_fn=search_fn,
    )

    search_fn.assert_called_once_with(
        "smoking",
        db,
        config,
        film_ids=["film-a"],
    )


def test_metrics_for_grades_uses_graded_ndcg_and_binary_relevance() -> None:
    metrics = metrics_for_grades([3, 0, 2, 1], k=3)

    assert metrics["precision@3"] == pytest.approx(2 / 3)
    assert metrics["success@3"] == 1.0
    assert metrics["mrr"] == 1.0
    assert 0.0 < metrics["ndcg@3"] <= 1.0


def test_score_skips_partial_queries_and_aggregates_categories() -> None:
    complete = [
        {**_result("u1"), "grade": 3, "flags": [], "note": ""},
        {**_result("u2"), "grade": 0, "flags": ["junk"], "note": ""},
    ]
    partial = [
        {**_result("u3"), "grade": 2, "flags": [], "note": ""},
        {**_result("u4"), "grade": None, "flags": [], "note": ""},
    ]
    document = {
        "queries": [
            {
                "id": "complete",
                "category": "action",
                "query": "smoking",
                "candidates": complete,
            },
            {
                "id": "partial",
                "category": "vibe",
                "query": "lonely",
                "candidates": partial,
            },
        ]
    }

    report = score_review_document(document, k=2)

    assert report["queries_total"] == 2
    assert report["queries_evaluated"] == 1
    assert report["queries_incomplete"] == 1
    assert report["incomplete_ids"] == ["partial"]
    assert report["overall"]["precision@2"] == pytest.approx(0.5)
    assert report["overall"]["junk@2"] == pytest.approx(0.5)
    assert report["by_category"]["action"]["evaluated"] == 1
    assert "vibe" not in report["by_category"]


def test_score_does_not_report_synthetic_zero_quality_without_judgments() -> None:
    document = {
        "queries": [
            {
                "id": "unjudged",
                "category": "vibe",
                "query": "lonely at night",
                "candidates": [
                    {**_result("u1"), "grade": None, "flags": [], "note": ""}
                ],
            }
        ]
    }

    report = score_review_document(document)

    assert report["queries_evaluated"] == 0
    assert report["overall"] is None
    assert "unavailable" in report["metric_note"].lower()


@pytest.mark.parametrize("grade", [-1, 4, 1.5, True])
def test_score_rejects_invalid_grades(grade: object) -> None:
    document = {
        "queries": [
            {
                "id": "q",
                "category": "vibe",
                "query": "night",
                "candidates": [{**_result("u"), "grade": grade}],
            }
        ]
    }

    with pytest.raises(ValueError, match="grade"):
        score_review_document(document)
