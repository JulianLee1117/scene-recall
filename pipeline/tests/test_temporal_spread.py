"""Focused coverage for ordinary result-stream temporal spreading."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import numpy as np

from pipeline.config import Config


def _row(
    unit_id: str,
    film_id: str,
    t_start: float,
    vector_index: int,
) -> dict:
    vector = [0.0] * 8
    vector[vector_index] = 1.0
    return {
        "unit_id": unit_id,
        "film_id": film_id,
        "shot_id": unit_id,
        "t_start": t_start,
        "t_end": t_start + 2.0,
        "is_representative": True,
        "caption": f"Moment {unit_id}",
        "searchable_text": f"moment {unit_id}",
        "dialogue": "[]",
        "keyframe_paths": json.dumps([f"{unit_id}.webp"]),
        "img_vec": vector,
        "_distance": vector_index / 100.0,
    }


def _preference_db(rows: list[dict]) -> MagicMock:
    query = MagicMock()
    query.select.return_value = query
    query.where.return_value = query
    query.limit.return_value = query
    query.to_list.return_value = rows
    table = MagicMock()
    table.search.return_value = query
    db = MagicMock()
    db.open_table.return_value = table
    return db


def _result(row: dict) -> dict:
    return {
        "unit_id": row["unit_id"],
        "film_id": row["film_id"],
        "t_start": row["t_start"],
        "t_end": row["t_end"],
        "caption": row["caption"],
        "rank": 1,
    }


def _repeat_policy_rows() -> list[dict]:
    return [
        _row("a-0", "film-a", 0.0, 0),
        _row("a-1", "film-a", 100.0, 1),
        _row("a-2", "film-a", 200.0, 2),
        _row("a-3", "film-a", 300.0, 3),
        _row("b-0", "film-b", 0.0, 4),
        _row("c-0", "film-c", 0.0, 5),
    ]


def test_soft_temporal_spread_defers_but_never_deletes_neighbors() -> None:
    from pipeline.search.retrieve import _soft_temporal_spread

    rows = [
        _row("strong", "film-a", 0.0, 0),
        _row("near-strong", "film-a", 20.0, 1),
        _row("other-film", "film-b", 10.0, 2),
        _row("later", "film-a", 40.0, 3),
        _row("near-later", "film-a", 60.0, 4),
    ]

    spread = _soft_temporal_spread([{"row": row} for row in rows])

    assert [candidate["row"]["unit_id"] for candidate in spread] == [
        "strong",
        "other-film",
        "later",
        "near-strong",
        "near-later",
    ]
    assert {candidate["row"]["unit_id"] for candidate in spread} == {
        row["unit_id"] for row in rows
    }


def test_unscoped_search_spreads_stably_and_backfills(
    config: Config,
) -> None:
    from pipeline.search.retrieve import search

    config.retrieval.weights.img = 1.0
    config.retrieval.weights.txt = 0.0
    config.retrieval.weights.lex = 0.0
    config.retrieval.diversity.page_size = 100
    config.retrieval.diversity.film_results_per_page_target = 100
    rows = [
        _row("strong", "film-a", 0.0, 0),
        _row("near-strong", "film-a", 20.0, 1),
        _row("other-film", "film-b", 10.0, 2),
        _row("later", "film-a", 40.0, 3),
        _row("near-later", "film-a", 60.0, 4),
    ]
    db = MagicMock()
    db.open_table.return_value = MagicMock()

    with (
        patch("pipeline.search.retrieve.require_visual_encoder_profile"),
        patch(
            "pipeline.search.retrieve.embed_text",
            return_value=np.asarray([[1.0, 0.0]], dtype=np.float32),
        ),
        patch("pipeline.search.retrieve._frame_search_rows", return_value=rows),
    ):
        first = search("quiet moment", db, config, result_limit=2)
        deeper = search("quiet moment", db, config, result_limit=5)

    assert [result["unit_id"] for result in deeper] == [
        "strong",
        "other-film",
        "later",
        "near-strong",
        "near-later",
    ]
    assert deeper[:2] == first


def test_explicit_film_scope_preserves_strict_relevance(config: Config) -> None:
    from pipeline.search.retrieve import search

    config.retrieval.weights.img = 1.0
    config.retrieval.weights.txt = 0.0
    config.retrieval.weights.lex = 0.0
    rows = [
        _row("strong", "film-a", 0.0, 0),
        _row("near-strong", "film-a", 20.0, 1),
        _row("later", "film-a", 40.0, 2),
    ]
    db = MagicMock()
    db.open_table.return_value = MagicMock()

    with (
        patch("pipeline.search.retrieve.require_visual_encoder_profile"),
        patch(
            "pipeline.search.retrieve.embed_text",
            return_value=np.asarray([[1.0, 0.0]], dtype=np.float32),
        ),
        patch("pipeline.search.retrieve._frame_search_rows", return_value=rows),
    ):
        results = search(
            "quiet moment",
            db,
            config,
            film_ids=("film-a",),
            result_limit=3,
        )

    assert [result["unit_id"] for result in results] == [
        "strong",
        "near-strong",
        "later",
    ]


def test_ordinary_recipe_spreads_only_when_unscoped(config: Config) -> None:
    from pipeline.search.retrieve import apply_recipe_result_preferences

    config.retrieval.diversity.page_size = 100
    config.retrieval.diversity.film_results_per_page_target = 100
    rows = [
        _row("strong", "film-a", 0.0, 0),
        _row("near-strong", "film-a", 20.0, 1),
        _row("later", "film-a", 40.0, 2),
    ]
    results = [_result(row) for row in rows]

    unscoped = apply_recipe_result_preferences(
        [dict(result) for result in results],
        _preference_db(rows),
        config,
        result_limit=3,
    )
    scoped = apply_recipe_result_preferences(
        [dict(result) for result in results],
        _preference_db(rows),
        config,
        film_ids=("film-a",),
        result_limit=3,
    )

    assert [result["unit_id"] for result in unscoped] == [
        "strong",
        "later",
        "near-strong",
    ]
    assert [result["unit_id"] for result in scoped] == [
        "strong",
        "near-strong",
        "later",
    ]


def test_ordinary_unscoped_recipe_uses_bounded_repeat_rank_stably(
    config: Config,
) -> None:
    from pipeline.search.retrieve import apply_recipe_result_preferences

    config.retrieval.diversity.film_repeat_rank_strength = 32
    rows = _repeat_policy_rows()

    def ranked(limit: int) -> list[dict]:
        return apply_recipe_result_preferences(
            [_result(row) for row in rows],
            _preference_db(rows),
            config,
            result_limit=limit,
        )

    first = ranked(3)
    full = ranked(6)

    assert [result["unit_id"] for result in full] == [
        "a-0",
        "b-0",
        "c-0",
        "a-1",
        "a-2",
        "a-3",
    ]
    assert first == full[:3]
    assert {result["unit_id"] for result in full} == {
        row["unit_id"] for row in rows
    }


def test_recipe_reference_and_explicit_scope_keep_their_existing_policies(
    config: Config,
) -> None:
    from pipeline.search.retrieve import (
        _bounded_film_repeat_rerank,
        _progressive_film_diversity,
        apply_recipe_result_preferences,
    )

    config.retrieval.diversity.page_size = 12
    config.retrieval.diversity.film_results_per_page_target = 4
    rows = _repeat_policy_rows()
    strict_order = [row["unit_id"] for row in rows]

    with (
        patch(
            "pipeline.search.retrieve._bounded_film_repeat_rerank",
            wraps=_bounded_film_repeat_rerank,
        ) as bounded,
        patch(
            "pipeline.search.retrieve._progressive_film_diversity",
            wraps=_progressive_film_diversity,
        ) as progressive,
    ):
        reference = apply_recipe_result_preferences(
            [_result(row) for row in rows],
            _preference_db(rows),
            config,
            result_limit=6,
            apply_reference_temporal_spread=True,
        )

    bounded.assert_not_called()
    progressive.assert_called_once()
    assert [result["unit_id"] for result in reference] == strict_order

    with (
        patch(
            "pipeline.search.retrieve._bounded_film_repeat_rerank",
            wraps=_bounded_film_repeat_rerank,
        ) as bounded,
        patch(
            "pipeline.search.retrieve._progressive_film_diversity",
            wraps=_progressive_film_diversity,
        ) as progressive,
    ):
        scoped = apply_recipe_result_preferences(
            [_result(row) for row in rows],
            _preference_db(rows),
            config,
            film_ids=("film-a", "film-b", "film-c"),
            result_limit=6,
        )

    bounded.assert_not_called()
    progressive.assert_not_called()
    assert [result["unit_id"] for result in scoped] == strict_order

    scoped_reference_rows = [
        _row("reference-strong", "film-a", 0.0, 0),
        _row("reference-near", "film-a", 20.0, 1),
        _row("reference-far", "film-a", 200.0, 2),
    ]
    with (
        patch(
            "pipeline.search.retrieve._bounded_film_repeat_rerank",
            wraps=_bounded_film_repeat_rerank,
        ) as bounded,
        patch(
            "pipeline.search.retrieve._progressive_film_diversity",
            wraps=_progressive_film_diversity,
        ) as progressive,
    ):
        scoped_reference = apply_recipe_result_preferences(
            [_result(row) for row in scoped_reference_rows],
            _preference_db(scoped_reference_rows),
            config,
            film_ids=("film-a",),
            result_limit=3,
            apply_reference_temporal_spread=True,
        )

    bounded.assert_not_called()
    progressive.assert_not_called()
    assert [result["unit_id"] for result in scoped_reference] == [
        "reference-strong",
        "reference-far",
        "reference-near",
    ]
