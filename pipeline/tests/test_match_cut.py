"""Tests for the reference-frame Match Cut evaluation harness."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml

from pipeline.eval.match_cut import (
    CASE_KIND,
    CRITERIA,
    RANKINGS_KIND,
    load_case_document,
    load_cases,
    score_match_cut,
)


def _criterion_descriptions() -> dict[str, str]:
    return {criterion: f"Human grade for {criterion}." for criterion in CRITERIA}


def _grades(**values: int | None) -> dict[str, int | None]:
    grades = dict.fromkeys(CRITERIA)
    grades.update(values)
    return grades


def _case_document() -> dict:
    return {
        "schema_version": 1,
        "kind": CASE_KIND,
        "criteria": _criterion_descriptions(),
        "cases": [
            {
                "id": "profile",
                "description": "A tight side profile.",
                "reference": {"unit_id": "source", "frame_index": 0},
                "judgments": [
                    {
                        "unit_id": "positive-a",
                        "frame_index": 1,
                        "label": "positive",
                        "criteria": _grades(
                            normalized_position=3,
                            scale=3,
                            viewpoint_orientation=3,
                        ),
                        "note": "Strong geometric match.",
                    },
                    {
                        "unit_id": "positive-b",
                        "frame_index": 0,
                        "label": "positive",
                        "criteria": _grades(scale=0, pose=3),
                        "note": "Pose match, scale mismatch.",
                    },
                    {
                        "unit_id": "hard-negative",
                        "frame_index": 2,
                        "label": "hard_negative",
                        "criteria": _grades(viewpoint_orientation=0),
                        "note": "Frontal confounder.",
                    },
                ],
            }
        ],
    }


def _entry(rank: int, unit_id: str, frame_index: int) -> dict:
    return {"rank": rank, "unit_id": unit_id, "frame_index": frame_index}


def _rankings_document() -> dict:
    return {
        "schema_version": 1,
        "kind": RANKINGS_KIND,
        "matcher": {
            "id": "matcher-under-test",
            "revision": "experiment-1",
            "corpus_snapshot": "units-generation-abc",
        },
        "profiles": [
            {
                "id": "layout-v1",
                "model_id": "example/layout-encoder",
                "revision": "sha256:abc",
                "vector_space": "layout-v1-256d",
                "contract": "Normalized frame-layout descriptor.",
            }
        ],
        "gates": [
            {
                "id": "candidate_pool",
                "description": "Independent layout candidates before reranking.",
                "expected_depth": 4,
                "profile_ids": ["layout-v1"],
                "ranking_contract": "Cosine ANN within layout-v1 only.",
            },
            {
                "id": "results",
                "description": "Final bounded result window.",
                "expected_depth": 2,
                "profile_ids": ["layout-v1"],
                "ranking_contract": "Stable truncation of the candidate ranking.",
            },
        ],
        "cases": [
            {
                "id": "profile",
                "rankings": {
                    "candidate_pool": [
                        _entry(1, "unjudged", 0),
                        _entry(2, "hard-negative", 2),
                        _entry(3, "positive-a", 1),
                        _entry(4, "positive-b", 0),
                    ],
                    "results": [
                        _entry(1, "positive-a", 1),
                        _entry(2, "hard-negative", 2),
                    ],
                },
            }
        ],
    }


def test_seed_cases_are_real_frame_references_with_sparse_human_grades() -> None:
    cases = load_cases(Path("pipeline/eval/match_cut_cases.yaml"))

    assert [case.id for case in cases] == [
        "dune_tight_right_profile",
        "dune_tight_profile",
    ]
    assert all(case.reference.frame_index >= 0 for case in cases)
    assert all(
        {judgment.label for judgment in case.judgments}
        == {"positive", "hard_negative"}
        for case in cases
    )
    assert any(
        grade is None
        for case in cases
        for judgment in case.judgments
        for grade in judgment.criteria.values()
    )


def test_score_reports_candidate_loss_ranking_and_criterion_coverage() -> None:
    report = score_match_cut(_case_document(), _rankings_document())

    assert report["kind"] == "scene_recall_match_cut_evaluation"
    assert report["matcher"]["corpus_snapshot"] == "units-generation-abc"
    assert len(report["inputs"]["case_set_sha256"]) == 64
    assert len(report["inputs"]["rankings_sha256"]) == 64

    candidate_pool = report["gates"]["candidate_pool"]
    assert candidate_pool["known_positive_recall_micro"] == 1.0
    assert candidate_pool["known_positive_success_rate"] == 1.0
    assert candidate_pool["mean_first_known_positive_rank_on_success"] == 3.0
    assert candidate_pool["known_hard_negative_retrieval_rate_micro"] == 1.0
    assert candidate_pool["positive_before_hard_negative"]["accuracy"] == 0.0

    results = report["gates"]["results"]
    assert results["known_positive_recall_micro"] == 0.5
    assert results["positive_before_hard_negative"]["accuracy"] == 0.5
    assert results["criteria"]["scale"]["strong_match_recall_micro"] == 1.0
    assert results["criteria"]["scale"]["pairwise_ranking"]["accuracy"] == 1.0
    scale = report["cases"][0]["gates"]["results"]["criteria"]["scale"]
    assert [row["grade"] for row in scale["retrieved_grades"]] == [3]
    assert scale["mean_retrieved_grade"] == 3.0

    subject = results["criteria"]["subject_object"]
    assert subject["graded_judgments"] == 0
    assert subject["strong_match_recall_micro"] is None
    assert subject["pairwise_ranking"]["accuracy"] is None

    transition = report["cases"][0]["transitions"][0]
    assert transition["from"] == "candidate_pool"
    assert transition["to"] == "results"
    assert transition["known_positives_lost"] == [
        {"unit_id": "positive-b", "frame_index": 0}
    ]
    assert "not all relevant frames" in report["metric_note"]
    assert "never combines vector scores" in report["metric_note"]


def test_case_validation_requires_every_criterion_but_allows_null() -> None:
    document = _case_document()
    del document["cases"][0]["judgments"][0]["criteria"]["pose"]

    with pytest.raises(ValueError, match="every declared criterion"):
        load_case_document(document)

    document = _case_document()
    document["cases"][0]["judgments"][0]["criteria"]["pose"] = True
    with pytest.raises(ValueError, match="null or an integer 0-3"):
        load_case_document(document)


def test_rankings_require_explicit_profile_lineage_and_gate_contract() -> None:
    rankings = _rankings_document()
    del rankings["profiles"][0]["vector_space"]
    with pytest.raises(ValueError, match="vector_space"):
        score_match_cut(_case_document(), rankings)

    rankings = _rankings_document()
    rankings["gates"][0]["profile_ids"] = ["different-space"]
    with pytest.raises(ValueError, match="unknown profiles"):
        score_match_cut(_case_document(), rankings)

    rankings = _rankings_document()
    rankings["gates"][0]["ranking_contract"] = ""
    with pytest.raises(ValueError, match="ranking_contract"):
        score_match_cut(_case_document(), rankings)


def test_rankings_reject_partial_case_sets_and_invalid_rank_evidence() -> None:
    cases = _case_document()
    second = copy.deepcopy(cases["cases"][0])
    second["id"] = "second"
    second["reference"] = {"unit_id": "other-source", "frame_index": 1}
    cases["cases"].append(second)

    with pytest.raises(ValueError, match="exactly match case set"):
        score_match_cut(cases, _rankings_document())

    rankings = _rankings_document()
    rankings["cases"][0]["rankings"]["results"][1]["rank"] = 3
    with pytest.raises(ValueError, match="sequential ranks"):
        score_match_cut(_case_document(), rankings)

    rankings = _rankings_document()
    rankings["cases"][0]["rankings"]["results"][0] = _entry(
        1, "source", 0
    )
    with pytest.raises(ValueError, match="own reference frame"):
        score_match_cut(_case_document(), rankings)


def test_case_yaml_round_trip_remains_human_editable() -> None:
    document = yaml.safe_load(yaml.safe_dump(_case_document(), sort_keys=False))

    cases = load_case_document(document)

    assert cases[0].reference.unit_id == "source"
    assert cases[0].judgments[0].criteria["normalized_position"] == 3
