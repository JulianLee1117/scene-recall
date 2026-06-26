"""Tests for pipeline/eval/run_eval.py — TDD.

Tests:
  - is_hit: exact match is a hit
  - is_hit: match within 5s tolerance is a hit
  - is_hit: wrong film_id is not a hit
  - is_hit: t_end exceeds acceptable range is not a hit
  - hit_at_k: returns True when a hit is in top-k
  - hit_at_k: returns False when no hit is in top-k
  - hit_at_k: uses only first k results
  - run_eval_queries: skips placeholder queries (all film_ids == REPLACE_WITH_FILM_ID)
  - run_eval_queries: counts hits correctly for mix of hits and misses
  - run_eval_queries: returns aggregate hit@5 and hit@10 metrics
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# is_hit
# ---------------------------------------------------------------------------


def test_is_hit_exact_match() -> None:
    """A result matching film_id, t_start, t_end exactly is a hit."""
    from pipeline.eval.run_eval import is_hit

    result = {"film_id": "film_abc", "t_start": 100.0, "t_end": 120.0}
    acceptable = {"film_id": "film_abc", "t_start": 100.0, "t_end": 120.0}

    assert is_hit(result, acceptable) is True


def test_is_hit_within_5s_tolerance() -> None:
    """A result within 5s tolerance on both ends is a hit."""
    from pipeline.eval.run_eval import is_hit

    result = {"film_id": "film_abc", "t_start": 97.0, "t_end": 124.0}
    acceptable = {"film_id": "film_abc", "t_start": 100.0, "t_end": 120.0}

    # t_start=97.0 >= 100.0 - 5.0 = 95.0 ✓
    # t_end=124.0 <= 120.0 + 5.0 = 125.0 ✓
    assert is_hit(result, acceptable) is True


def test_is_hit_wrong_film_id() -> None:
    """A result with wrong film_id is not a hit."""
    from pipeline.eval.run_eval import is_hit

    result = {"film_id": "film_xyz", "t_start": 100.0, "t_end": 120.0}
    acceptable = {"film_id": "film_abc", "t_start": 100.0, "t_end": 120.0}

    assert is_hit(result, acceptable) is False


def test_is_hit_t_start_before_tolerance() -> None:
    """A result with t_start too early (outside 5s tolerance) is not a hit."""
    from pipeline.eval.run_eval import is_hit

    result = {"film_id": "film_abc", "t_start": 90.0, "t_end": 120.0}
    acceptable = {"film_id": "film_abc", "t_start": 100.0, "t_end": 120.0}

    # t_start=90.0 < 100.0 - 5.0 = 95.0 ✗
    assert is_hit(result, acceptable) is False


def test_is_hit_t_end_after_tolerance() -> None:
    """A result with t_end too late (outside 5s tolerance) is not a hit."""
    from pipeline.eval.run_eval import is_hit

    result = {"film_id": "film_abc", "t_start": 100.0, "t_end": 130.0}
    acceptable = {"film_id": "film_abc", "t_start": 100.0, "t_end": 120.0}

    # t_end=130.0 > 120.0 + 5.0 = 125.0 ✗
    assert is_hit(result, acceptable) is False


# ---------------------------------------------------------------------------
# hit_at_k
# ---------------------------------------------------------------------------


def test_hit_at_k_hit_in_top5() -> None:
    """hit_at_k returns True when an acceptable answer is in the first k results."""
    from pipeline.eval.run_eval import hit_at_k

    results = [
        {"film_id": "film_abc", "t_start": 10.0, "t_end": 20.0},
        {"film_id": "film_abc", "t_start": 100.0, "t_end": 120.0},  # match
    ]
    acceptable = [{"film_id": "film_abc", "t_start": 100.0, "t_end": 120.0}]

    assert hit_at_k(results, acceptable, k=5) is True


def test_hit_at_k_no_hit_in_top5() -> None:
    """hit_at_k returns False when no acceptable answer is in the first k results."""
    from pipeline.eval.run_eval import hit_at_k

    results = [
        {"film_id": "film_abc", "t_start": 10.0, "t_end": 20.0},
        {"film_id": "film_abc", "t_start": 50.0, "t_end": 70.0},
    ]
    acceptable = [{"film_id": "film_xyz", "t_start": 100.0, "t_end": 120.0}]

    assert hit_at_k(results, acceptable, k=5) is False


def test_hit_at_k_uses_only_k_results() -> None:
    """hit_at_k only considers the first k results (hit at position k+1 is False)."""
    from pipeline.eval.run_eval import hit_at_k

    # Hit is at index 5 (position 6), so should not appear in top-5
    results = [
        {"film_id": "film_abc", "t_start": 10.0, "t_end": 20.0},
        {"film_id": "film_abc", "t_start": 20.0, "t_end": 30.0},
        {"film_id": "film_abc", "t_start": 30.0, "t_end": 40.0},
        {"film_id": "film_abc", "t_start": 40.0, "t_end": 50.0},
        {"film_id": "film_abc", "t_start": 50.0, "t_end": 60.0},
        {"film_id": "film_abc", "t_start": 100.0, "t_end": 120.0},  # at index 5 = position 6
    ]
    acceptable = [{"film_id": "film_abc", "t_start": 100.0, "t_end": 120.0}]

    assert hit_at_k(results, acceptable, k=5) is False
    assert hit_at_k(results, acceptable, k=6) is True


def test_hit_at_k_multiple_acceptable_any_matches() -> None:
    """hit_at_k returns True if any of the acceptable answers is in top-k."""
    from pipeline.eval.run_eval import hit_at_k

    results = [
        {"film_id": "film_abc", "t_start": 200.0, "t_end": 210.0},  # matches second acceptable
    ]
    acceptable = [
        {"film_id": "film_abc", "t_start": 100.0, "t_end": 120.0},
        {"film_id": "film_abc", "t_start": 200.0, "t_end": 210.0},
    ]

    assert hit_at_k(results, acceptable, k=5) is True


# ---------------------------------------------------------------------------
# is_placeholder_query
# ---------------------------------------------------------------------------


def test_is_placeholder_all_placeholders() -> None:
    """Query with all film_ids == REPLACE_WITH_FILM_ID is a placeholder."""
    from pipeline.eval.run_eval import is_placeholder_query

    query = {
        "id": "q001",
        "query": "some query",
        "intent": "vibe",
        "acceptable": [
            {"film_id": "REPLACE_WITH_FILM_ID", "t_start": 0.0, "t_end": 30.0},
            {"film_id": "REPLACE_WITH_FILM_ID", "t_start": 60.0, "t_end": 90.0},
        ],
    }
    assert is_placeholder_query(query) is True


def test_is_placeholder_some_real() -> None:
    """Query with at least one real film_id is NOT a placeholder."""
    from pipeline.eval.run_eval import is_placeholder_query

    query = {
        "id": "q001",
        "query": "some query",
        "intent": "vibe",
        "acceptable": [
            {"film_id": "REPLACE_WITH_FILM_ID", "t_start": 0.0, "t_end": 30.0},
            {"film_id": "real_film_abc", "t_start": 60.0, "t_end": 90.0},
        ],
    }
    assert is_placeholder_query(query) is False


# ---------------------------------------------------------------------------
# run_eval_queries (integration-level, mocked search)
# ---------------------------------------------------------------------------


def _make_gold_queries() -> list[dict]:
    """Two real queries + one placeholder."""
    return [
        {
            "id": "q001",
            "query": "rainy city street",
            "intent": "vibe",
            "acceptable": [
                {"film_id": "film_yi_yi", "t_start": 100.0, "t_end": 120.0}
            ],
        },
        {
            "id": "q002",
            "query": "astronaut floating in space",
            "intent": "visual",
            "acceptable": [
                {"film_id": "film_2001", "t_start": 200.0, "t_end": 220.0}
            ],
        },
        {
            "id": "q003",
            "query": "placeholder query",
            "intent": "vibe",
            "acceptable": [
                {"film_id": "REPLACE_WITH_FILM_ID", "t_start": 0.0, "t_end": 30.0}
            ],
        },
    ]


def test_run_eval_queries_skips_placeholder(capsys: pytest.CaptureFixture) -> None:
    """run_eval_queries skips queries where all film_ids are placeholders."""
    from pipeline.eval.run_eval import run_eval_queries

    queries = _make_gold_queries()
    mock_db = MagicMock()

    # search returns empty — no hits
    with patch("pipeline.eval.run_eval.search", return_value=[]) as mock_search:
        run_eval_queries(queries, mock_db, MagicMock())

    # Only 2 real queries should trigger search calls, not the placeholder
    assert mock_search.call_count == 2


def test_run_eval_queries_returns_aggregate_metrics() -> None:
    """run_eval_queries returns dict with hit@5 and hit@10 rates."""
    from pipeline.eval.run_eval import run_eval_queries

    queries = _make_gold_queries()
    mock_db = MagicMock()

    # search returns a hit for q001 in position 1, miss for q002
    def fake_search(query: str, db, config):
        if "rainy" in query:
            return [{"film_id": "film_yi_yi", "t_start": 100.0, "t_end": 120.0}]
        return []

    with patch("pipeline.eval.run_eval.search", side_effect=fake_search):
        metrics = run_eval_queries(queries, mock_db, MagicMock())

    # q001 = hit, q002 = miss, q003 = skipped
    # 1 hit out of 2 evaluated = 0.5
    assert metrics["evaluated"] == 2
    assert metrics["skipped"] == 1
    assert metrics["hit@5"] == pytest.approx(0.5)
    assert metrics["hit@10"] == pytest.approx(0.5)


def test_run_eval_queries_all_misses() -> None:
    """run_eval_queries returns 0.0 hit rates when all results miss."""
    from pipeline.eval.run_eval import run_eval_queries

    queries = [
        {
            "id": "q001",
            "query": "some query",
            "intent": "vibe",
            "acceptable": [{"film_id": "film_abc", "t_start": 100.0, "t_end": 120.0}],
        }
    ]
    mock_db = MagicMock()

    with patch("pipeline.eval.run_eval.search", return_value=[]):
        metrics = run_eval_queries(queries, mock_db, MagicMock())

    assert metrics["hit@5"] == pytest.approx(0.0)
    assert metrics["hit@10"] == pytest.approx(0.0)
    assert metrics["evaluated"] == 1


def test_run_eval_queries_all_placeholder() -> None:
    """run_eval_queries with all placeholders returns 0 evaluated and no hits."""
    from pipeline.eval.run_eval import run_eval_queries

    queries = [
        {
            "id": "q001",
            "query": "placeholder",
            "intent": "vibe",
            "acceptable": [{"film_id": "REPLACE_WITH_FILM_ID", "t_start": 0.0, "t_end": 30.0}],
        }
    ]
    mock_db = MagicMock()

    with patch("pipeline.eval.run_eval.search", return_value=[]) as mock_search:
        metrics = run_eval_queries(queries, mock_db, MagicMock())

    mock_search.assert_not_called()
    assert metrics["evaluated"] == 0
    assert metrics["skipped"] == 1
    # hit rates undefined when evaluated == 0; accept None or 0.0
    assert metrics["hit@5"] in (None, 0.0)
    assert metrics["hit@10"] in (None, 0.0)
