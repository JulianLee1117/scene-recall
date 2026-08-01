"""Tests for reproducible retrieval experiments and human-owned judgments."""

from __future__ import annotations

import copy
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import yaml

from pipeline.config import Config, RetrievalWeights
from pipeline.eval.experiment import (
    DEFAULT_QUERIES,
    DEFAULT_VARIANTS,
    Variant,
    _run_command,
    build_provenance,
    _evidence_sha256,
    capture_index_state,
    load_variants,
    preserve_judgments,
    run_experiment,
    score_experiment,
    snapshot_payload_sha256,
    write_snapshot,
)
from pipeline.eval.review import load_query_set


FILM_ID = "5" * 64


def _result(unit_id: str, *, t_start: float = 10.0) -> dict[str, Any]:
    return {
        "unit_id": unit_id,
        "film_id": FILM_ID,
        "t_start": t_start,
        "t_end": t_start + 2.0,
        "caption": f"caption for {unit_id}",
        "keyframe_url": f"/media/keyframe/{unit_id}/0",
        "preview_url": f"/media/preview/{unit_id}",
        "debug": {"final_score": 0.25, "channels": {"img": {"rank": 1}}},
    }


class _StepClock:
    def __init__(self, step: float = 0.01) -> None:
        self.value = 0.0
        self.step = step

    def __call__(self) -> float:
        value = self.value
        self.value += self.step
        return value


def _variants() -> list[Variant]:
    return [
        Variant("hybrid", RetrievalWeights(img=0.4, txt=0.4, lex=0.2)),
        Variant("image_only", RetrievalWeights(img=1.0, txt=0.0, lex=0.0)),
    ]


def _stable_index(_db: Any) -> dict[str, Any]:
    return {
        "tables": {"units": {"version": 7, "rows": 3}},
        "published_film_ids": [FILM_ID],
    }


def test_capture_index_state_records_index_coverage() -> None:
    table = MagicMock()
    table.schema = "unit schema"
    table.version = 7
    table.count_rows.return_value = 3
    table.list_indices.return_value = [
        SimpleNamespace(
            name="units_searchable_text_fts_v1",
            index_type="FTS",
            columns=["searchable_text"],
        )
    ]
    table.index_stats.return_value = SimpleNamespace(
        num_indexed_rows=3,
        num_unindexed_rows=0,
    )
    db = MagicMock()
    db.open_table.return_value = table

    with (
        patch("pipeline.eval.experiment.table_names", return_value={"units"}),
        patch(
            "pipeline.eval.experiment.published_film_ids",
            return_value=frozenset({FILM_ID}),
        ),
    ):
        state = capture_index_state(db)

    assert state["published_film_ids"] == [FILM_ID]
    assert state["tables"]["units"]["indices"] == [
        {
            "name": "units_searchable_text_fts_v1",
            "type": "FTS",
            "columns": ["searchable_text"],
            "indexed_rows": 3,
            "unindexed_rows": 0,
        }
    ]


def test_default_variants_and_query_scope_are_explicit() -> None:
    variants = load_variants(DEFAULT_VARIANTS)
    _queries, metadata = load_query_set(DEFAULT_QUERIES)

    assert [variant.id for variant in variants] == [
        "hybrid",
        "image_only",
        "text_only",
        "lexical_only",
    ]
    assert metadata["scope"]["film_ids"] == [
        "513831eef8eb598038f62bde3bddd7bff6b6e50d533edf5f1bc4e0f5fd4a6e03"
    ]


@pytest.mark.parametrize(
    "variant_document, message",
    [
        (
            {
                "version": 1,
                "variants": [
                    {"id": "same", "weights": {"img": 1, "txt": 0, "lex": 0}},
                    {"id": "same", "weights": {"img": 0, "txt": 1, "lex": 0}},
                ],
            },
            "duplicate variant id",
        ),
        (
            {
                "version": 1,
                "variants": [
                    {"id": "off", "weights": {"img": 0, "txt": 0, "lex": 0}}
                ],
            },
            "disable every channel",
        ),
        (
            {
                "version": 1,
                "variants": [
                    {"id": "bad", "weights": {"img": -1, "txt": 1, "lex": 1}}
                ],
            },
            "finite and non-negative",
        ),
    ],
)
def test_load_variants_rejects_ambiguous_recipes(
    tmp_path: Path,
    variant_document: dict[str, Any],
    message: str,
) -> None:
    path = tmp_path / "variants.yaml"
    path.write_text(yaml.safe_dump(variant_document), encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        load_variants(path)


def test_run_experiment_records_variants_pool_latency_and_provenance(
    config: Config,
) -> None:
    queries = [
        {"id": "q1", "category": "action", "query": "first query"},
        {"id": "q2", "category": "vibe", "query": "second query"},
    ]
    metadata = {"scope": {"film_ids": [FILM_ID]}, "version": 1}
    calls: list[tuple[str, tuple[float, float, float], tuple[str, ...] | None]] = []

    def fake_search(
        query: str,
        _db: Any,
        variant_config: Config,
        *,
        film_ids: tuple[str, ...] | None,
        result_limit: int,
    ) -> list[dict[str, Any]]:
        weights = variant_config.retrieval.weights
        recipe = (weights.img, weights.txt, weights.lex)
        calls.append((query, recipe, film_ids))
        assert result_limit == 2
        prefix = query.split()[0]
        shared = _result(f"{prefix}-shared", t_start=10.0)
        shared["matched_frame_index"] = 2 if weights.img == 1.0 else 1
        shared["matched_frame_timestamp"] = (
            11.5 if weights.img == 1.0 else 10.5
        )
        shared["keyframe_url"] = (
            f"/media/keyframe/{prefix}-shared/"
            f"{shared['matched_frame_index']}"
        )
        if weights.img == 1.0:
            return [
                shared,
                _result(f"{prefix}-image", t_start=30.0),
            ]
        return [
            shared,
            _result(f"{prefix}-hybrid", t_start=20.0),
        ]

    original_weights = copy.deepcopy(config.retrieval.weights)
    document = run_experiment(
        queries,
        metadata,
        _variants(),
        object(),
        config,
        limit=2,
        search_fn=fake_search,
        clock=_StepClock(),
        index_state_fn=_stable_index,
        provenance={"git": {"commit": "abc", "dirty": False}},
        warmup=False,
        created_at="2026-07-31T00:00:00+00:00",
    )

    assert config.retrieval.weights == original_weights
    assert document["schema_version"] == 1
    assert document["relevance"] is None
    assert document["provenance"]["index"] == _stable_index(None)
    assert document["provenance"]["git"]["commit"] == "abc"
    assert document["queries"][0]["film_ids"] == [FILM_ID]
    assert document["queries"][0]["execution_order"] == ["hybrid", "image_only"]
    assert document["queries"][1]["execution_order"] == ["image_only", "hybrid"]
    assert [
        row["unit_id"]
        for row in document["queries"][0]["runs"]["hybrid"]["ranking"]
    ] == ["first-shared", "first-hybrid"]
    assert {
        row["unit_id"] for row in document["queries"][0]["candidates"]
    } == {"first-shared", "first-hybrid", "first-image"}
    assert all(
        candidate["grade"] is None
        for query in document["queries"]
        for candidate in query["candidates"]
    )
    shared = next(
        candidate
        for candidate in document["queries"][0]["candidates"]
        if candidate["unit_id"] == "first-shared"
    )
    assert shared["presentations"]["hybrid"]["matched_frame_index"] == 1
    assert shared["presentations"]["image_only"]["matched_frame_index"] == 2
    assert document["diagnostics"]["variants"]["hybrid"]["latency_ms"][
        "p50"
    ] == pytest.approx(10.0)
    assert document["diagnostics"]["variants"]["image_only"][
        "new_candidates_vs_baseline"
    ] == 2
    assert len(calls) == 4
    assert all(call[2] == (FILM_ID,) for call in calls)


def test_run_experiment_discards_snapshot_when_index_moves(config: Config) -> None:
    states = iter(
        [
            {
                "tables": {"units": {"version": 1}},
                "published_film_ids": [FILM_ID],
            },
            {
                "tables": {"units": {"version": 2}},
                "published_film_ids": [FILM_ID],
            },
        ]
    )

    with pytest.raises(RuntimeError, match="index changed"):
        run_experiment(
            [{"id": "q", "category": "vibe", "query": "night"}],
            {"scope": {"film_ids": [FILM_ID]}},
            _variants()[:1],
            object(),
            config,
            search_fn=lambda *_args, **_kwargs: [_result("u")],
            clock=_StepClock(),
            index_state_fn=lambda _db: next(states),
            warmup=False,
        )


def test_run_experiment_warms_every_variant(config: Config) -> None:
    calls: list[tuple[float, float, float]] = []

    def fake_search(
        _query: str,
        _db: Any,
        variant_config: Config,
        **_kwargs: Any,
    ) -> list[dict[str, Any]]:
        weights = variant_config.retrieval.weights
        calls.append((weights.img, weights.txt, weights.lex))
        return [_result("u")]

    document = run_experiment(
        [{"id": "q", "category": "vibe", "query": "night"}],
        {"scope": {"film_ids": [FILM_ID]}},
        _variants(),
        object(),
        config,
        search_fn=fake_search,
        clock=_StepClock(),
        index_state_fn=_stable_index,
        warmup=True,
    )

    assert len(calls) == 4  # two warmups plus two measured runs
    assert set(document["diagnostics"]["warmup_ms"]) == {
        "hybrid",
        "image_only",
    }


@pytest.mark.parametrize(
    ("metadata", "message"),
    [
        ({}, "explicitly set"),
        ({"scope": {"film_ids": []}}, "cannot be empty"),
        ({"scope": {"film_ids": ["missing"]}}, "unpublished"),
    ],
)
def test_run_experiment_rejects_ambiguous_or_missing_scope(
    config: Config,
    metadata: dict[str, Any],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        run_experiment(
            [{"id": "q", "category": "vibe", "query": "night"}],
            metadata,
            _variants()[:1],
            object(),
            config,
            search_fn=lambda *_args, **_kwargs: [],
            index_state_fn=_stable_index,
            warmup=False,
        )


def test_run_experiment_rejects_limit_above_production_contract(
    config: Config,
) -> None:
    with pytest.raises(ValueError, match="max_result_limit"):
        run_experiment(
            [{"id": "q", "category": "vibe", "query": "night"}],
            {"scope": {"film_ids": [FILM_ID]}},
            _variants()[:1],
            object(),
            config,
            limit=101,
            search_fn=lambda *_args, **_kwargs: [],
            index_state_fn=_stable_index,
            warmup=False,
        )


def test_preserve_judgments_requires_same_query_and_candidate_evidence(
    config: Config,
) -> None:
    query = [{"id": "q", "category": "action", "query": "smoking"}]

    def search_at(t_start: float):
        return lambda *_args, **_kwargs: [_result("u", t_start=t_start)]

    previous = run_experiment(
        query,
        {"scope": {"film_ids": [FILM_ID]}},
        _variants()[:1],
        object(),
        config,
        search_fn=search_at(10.0),
        clock=_StepClock(),
        index_state_fn=_stable_index,
        warmup=False,
    )
    previous_candidate = previous["queries"][0]["candidates"][0]
    previous_candidate["grade"] = 3
    previous_candidate["flags"] = ["duplicate"]
    previous_candidate["note"] = "human note"

    matching = run_experiment(
        query,
        {"scope": {"film_ids": [FILM_ID]}},
        _variants()[:1],
        object(),
        config,
        search_fn=search_at(10.0),
        clock=_StepClock(),
        index_state_fn=_stable_index,
        previous_judgments=previous,
        warmup=False,
    )
    assert matching["judgments_inherited"] == 1
    assert matching["queries"][0]["candidates"][0]["grade"] == 3
    assert matching["queries"][0]["candidates"][0]["note"] == "human note"

    changed = run_experiment(
        query,
        {"scope": {"film_ids": [FILM_ID]}},
        _variants()[:1],
        object(),
        config,
        search_fn=search_at(30.0),
        clock=_StepClock(),
        index_state_fn=_stable_index,
        previous_judgments=previous,
        warmup=False,
    )
    assert changed["judgments_inherited"] == 0
    assert changed["queries"][0]["candidates"][0]["grade"] is None

    blank = copy.deepcopy(matching)
    blank["queries"][0]["query"] = "different meaning"
    blank["queries"][0]["candidates"][0]["grade"] = None
    assert preserve_judgments(blank, previous) == 0


def _scoring_document(*, judged: bool) -> dict[str, Any]:
    candidates = [
        {
            **_result("good", t_start=10.0),
            "grade": 3 if judged else None,
            "flags": [],
            "note": "",
        },
        {
            **_result("bad", t_start=20.0),
            "grade": 0 if judged else None,
            "flags": ["junk"] if judged else [],
            "note": "",
        },
    ]
    for candidate in candidates:
        candidate["evidence_sha256"] = _evidence_sha256(candidate)
    document = {
        "schema_version": 1,
        "kind": "scene_recall_retrieval_experiment",
        "diagnostics": {"note": "not relevance"},
        "variants": [{"id": "baseline"}, {"id": "challenger"}],
        "queries": [
            {
                "id": "q",
                "category": "action",
                "query": "find the good moment",
                "candidates": candidates,
                "runs": {
                    "baseline": {
                        "ranking": [
                            {"rank": 1, "unit_id": "bad"},
                            {"rank": 2, "unit_id": "good"},
                        ]
                    },
                    "challenger": {
                        "ranking": [{"rank": 1, "unit_id": "good"}]
                    },
                },
            }
        ],
    }
    document["machine_payload_sha256"] = snapshot_payload_sha256(document)
    return document


def test_score_experiment_reports_null_relevance_until_pool_is_judged() -> None:
    report = score_experiment(_scoring_document(judged=False), k=2)

    assert report["diagnostics"] == {"note": "not relevance"}
    assert report["judgments"]["queries_evaluated"] == 0
    assert report["judgments"]["candidate_coverage"] == 0.0
    assert report["relevance"] is None
    assert "unavailable" in report["metric_note"].lower()


def test_score_experiment_uses_pooled_ideal_and_penalizes_underfill() -> None:
    report = score_experiment(_scoring_document(judged=True), k=2)
    relevance = report["relevance"]
    assert relevance is not None
    baseline = relevance["variants"]["baseline"]["overall"]
    challenger = relevance["variants"]["challenger"]["overall"]

    assert report["judgments"]["queries_evaluated"] == 1
    assert challenger["ndcg@2"] == pytest.approx(1.0)
    assert baseline["ndcg@2"] < challenger["ndcg@2"]
    # One relevant result in a two-slot metric is 0.5 even when the system
    # returned only one item; underfilling cannot inflate precision.
    assert challenger["precision@2"] == pytest.approx(0.5)
    assert challenger["success@1"] == 1.0
    assert baseline["success@1"] == 0.0
    comparison = relevance["paired_vs_baseline"]["challenger"]
    assert comparison["wins"] == 1
    assert comparison["losses"] == 0


def test_score_rejects_machine_payload_edits_but_accepts_human_grades() -> None:
    document = _scoring_document(judged=False)
    document["queries"][0]["candidates"][0]["caption"] = "accidental edit"
    with pytest.raises(ValueError, match="machine payload changed"):
        score_experiment(document)

    document = _scoring_document(judged=False)
    for candidate in document["queries"][0]["candidates"]:
        candidate["grade"] = 0
        candidate["note"] = "human judgment"
    assert score_experiment(document)["relevance"] is not None


def test_success_at_five_is_independent_of_scoring_k() -> None:
    candidates = []
    ranking = []
    for index in range(1, 6):
        candidate = {
            **_result(f"u{index}", t_start=float(index * 10)),
            "grade": 3 if index == 4 else 0,
            "flags": [],
            "note": "",
        }
        candidate["evidence_sha256"] = _evidence_sha256(candidate)
        candidates.append(candidate)
        ranking.append({"rank": index, "unit_id": candidate["unit_id"]})
    document = {
        "schema_version": 1,
        "kind": "scene_recall_retrieval_experiment",
        "diagnostics": {},
        "variants": [{"id": "baseline"}],
        "queries": [
            {
                "id": "q",
                "category": "action",
                "query": "target",
                "candidates": candidates,
                "runs": {"baseline": {"ranking": ranking}},
            }
        ],
    }
    document["machine_payload_sha256"] = snapshot_payload_sha256(document)

    overall = score_experiment(document, k=2)["relevance"]["variants"][
        "baseline"
    ]["overall"]
    assert overall["precision@2"] == 0.0
    assert overall["success@5"] == 1.0


def test_provenance_marks_git_failure_unknown(
    config: Config,
    tmp_path: Path,
) -> None:
    queries = tmp_path / "queries.yaml"
    variants = tmp_path / "variants.yaml"
    queries.write_text("queries: []\n", encoding="utf-8")
    variants.write_text("variants: []\n", encoding="utf-8")

    with patch("pipeline.eval.experiment._git_output", return_value=None):
        provenance = build_provenance(
            queries,
            variants,
            config,
            repo_root=tmp_path,
        )

    assert provenance["git"]["available"] is False
    assert provenance["git"]["dirty"] is None
    assert provenance["git"]["reproducible"] is False
    assert provenance["models"]["semantic_text_encoder"] == (
        config.models.visual_encoder
    )


def test_run_cli_refuses_existing_output_before_database_work(
    tmp_path: Path,
) -> None:
    output = tmp_path / "existing.yaml"
    output.write_text("human work\n", encoding="utf-8")
    with patch("pipeline.eval.experiment.load_config") as load:
        with pytest.raises(FileExistsError, match="already exists"):
            _run_command(Namespace(output=output))
    load.assert_not_called()


def test_write_snapshot_refuses_to_overwrite_human_work(tmp_path: Path) -> None:
    path = tmp_path / "run.yaml"
    write_snapshot(path, {"kind": "scene_recall_retrieval_experiment"})

    with pytest.raises(FileExistsError, match="already exists"):
        write_snapshot(path, {"kind": "replacement"})

    assert yaml.safe_load(path.read_text(encoding="utf-8"))["kind"] == (
        "scene_recall_retrieval_experiment"
    )
