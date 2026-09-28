from copy import deepcopy
import hashlib

import pytest

from pipeline.experiments import match_effectiveness as evaluation
from pipeline.matching import cohort


def subset():
    return {
        "id": "cohort-0000000000000000", "films": [], "frames": [],
        "motion_unit_ids": [f"shot-{i:03}" for i in range(12, 80)],
        "units": [{"unit_id": f"shot-{i:03}", "film_id": f"film-{i % 8}", "t_start": 10.0, "t_end": 14.0, "caption": "Unverified scene description"} for i in range(100)],
    }


def draft():
    return evaluation.draft_cases(subset(), {"development_unit_ids": [f"shot-{i:03}" for i in range(60, 80)]})


def run_document():
    cases = draft()
    case_id = next(case["id"] for case in cases["cases"] if case["category"] == "motion")
    run = {
        "schema_version": 1, "kind": "match_effectiveness_run", "cases": cases,
        "measurements": [{"case_id": case_id, "variant": variant, "status": "completed", "candidates": [{"id": "same-candidate", "pair_path": f"{variant}.mp4", "pair_sha256": "a" * 64, "preview_ready": True}]} for variant in evaluation.VARIANTS],
    }
    run["run_sha256"] = cohort.digest(run)
    return run


def graded_review(run):
    review = evaluation.review_template(run)
    review["reviewer"] = "Human reviewer"
    for case in review["cases"]:
        case["preferred_variant"] = "tie"
        for variant in case["variants"]:
            for candidate in variant["candidates"]:
                candidate.update(played=True, grade=2, timing_grade=2)
    return review


def test_draft_is_deterministic_36_disjoint_ungraded_references():
    result = draft()
    assert result == draft()
    assert len(result["cases"]) == 36
    assert sum(case["category"] == "motion" for case in result["cases"]) == 24
    assert not ({case["unit_id"] for case in result["cases"]} & set(result["development_unit_ids"]))
    assert all(case["scenario"] is None and case["region"] is None for case in result["cases"])
    assert result["status"] == "draft-unreviewed"


def test_freeze_requires_reviewed_scenarios_regions_and_detects_changes():
    cases = draft()
    with pytest.raises(ValueError, match="label"):
        evaluation.freeze_cases(cases, "Reviewer")
    for case in cases["cases"]:
        case["scenario"] = "Manually inspected fixture"
    with pytest.raises(ValueError, match="human selected region"):
        evaluation.freeze_cases(cases, "Reviewer")
    for case in cases["cases"]:
        if case["focus"] == "subject":
            case["region"] = {"x": 0.25, "y": 0.25, "width": 0.5, "height": 0.5}
    frozen = evaluation.freeze_cases(cases, "Reviewer")
    evaluation.validate_cases(frozen)
    frozen["cases"][0]["reference_time"] += 0.1
    with pytest.raises(ValueError, match="changed"):
        evaluation.validate_cases(frozen)


def test_rejects_leakage_and_duplicate_references():
    cases = draft()
    cases["development_unit_ids"].append(cases["cases"][0]["unit_id"])
    with pytest.raises(ValueError, match="disjoint"):
        evaluation.validate_cases(cases)
    cases = draft()
    cases["cases"].append(deepcopy(cases["cases"][0]))
    with pytest.raises(ValueError, match="unique"):
        evaluation.validate_cases(cases)


def test_ablations_keep_legacy_request_unchanged_and_fix_new_timing():
    case = next(case for case in draft()["cases"] if case["focus"] == "camera")
    legacy_doc, legacy = evaluation.request_for(case, "legacy-fixed", "cohort")
    fixed_doc, fixed = evaluation.request_for(case, "current-fixed", "cohort")
    nearby_doc, nearby = evaluation.request_for(case, "current-nearby", "cohort")
    assert legacy_doc == fixed_doc == nearby_doc
    assert "focus" not in legacy and "timing" not in legacy
    assert fixed["focus"] == nearby["focus"] == "camera"
    assert fixed["timing"] == "fixed" and nearby["timing"] == "nearby"
    assert not legacy["allow_reframing"]


def test_blinded_review_has_no_model_names_or_scores_and_nulls_stay_unknown():
    run = run_document()
    review = evaluation.review_template(run)
    for variant in review["cases"][0]["variants"]:
        assert "variant" not in variant and "score" not in variant
        assert variant["candidates"][0]["grade"] is None
    result = evaluation.score_review(run, review)
    assert result["quality_status"] == "ungraded"
    assert not result["eligible_for_acceptance_review"]
    assert all(row["useful_top3_rate_among_judged_or_empty"] is None for row in result["metrics"])


@pytest.mark.parametrize("field,value", [("rank", 4), ("candidate_token", "different"), ("pair_path", "other.mp4"), ("preview_verified", False)])
def test_human_grades_cannot_move_to_changed_media(field, value):
    run = run_document()
    review = graded_review(run)
    review["cases"][0]["variants"][0]["candidates"][0][field] = value
    with pytest.raises(ValueError, match="proposal"):
        evaluation.score_review(run, review)


def test_unplayed_unverified_or_unattributed_grades_are_rejected():
    run = run_document()
    review = graded_review(run)
    review["cases"][0]["variants"][0]["candidates"][0]["played"] = False
    with pytest.raises(ValueError, match="played"):
        evaluation.score_review(run, review)
    review = graded_review(run)
    review["reviewer"] = None
    with pytest.raises(ValueError, match="named reviewer"):
        evaluation.score_review(run, review)
    run["measurements"][0]["candidates"][0]["preview_ready"] = False
    run["run_sha256"] = cohort.digest({key: value for key, value in run.items() if key != "run_sha256"})
    with pytest.raises(ValueError, match="boundary-verified"):
        evaluation.score_review(run, graded_review(run))


def test_timing_and_every_variant_required_before_preference():
    run = run_document()
    review = graded_review(run)
    review["cases"][0]["variants"][0]["candidates"][0]["timing_grade"] = None
    with pytest.raises(ValueError, match="every variant"):
        evaluation.score_review(run, review)
    review["cases"][0]["preferred_variant"] = None
    result = evaluation.score_review(run, review)
    assert result["quality_status"] == "partial-human-review"
    assert not result["eligible_for_acceptance_review"]


def test_even_complete_draft_review_cannot_pass_acceptance():
    run = run_document()
    result = evaluation.score_review(run, graded_review(run))
    assert result["quality_status"] == "complete-human-review"
    assert not result["eligible_for_acceptance_review"]
    assert result["acceptance"] == "pending-editorial-decision"
    assert all(row["proposed_motion_thresholds_met"] is None for row in result["paired_baseline_comparisons"])


def test_run_digest_rejects_proposal_changes():
    run = run_document()
    review = graded_review(run)
    run["measurements"][0]["candidates"][0]["score"] = 0.99
    with pytest.raises(ValueError, match="Recorded run changed"):
        evaluation.score_review(run, review)


def test_import_checks_actual_rendered_bytes(tmp_path):
    run = run_document()
    for row in run["measurements"]:
        candidate = row["candidates"][0]
        content = b"recorded media bytes"
        (tmp_path / candidate["pair_path"]).write_bytes(content)
        candidate["pair_sha256"] = hashlib.sha256(content).hexdigest()
    run["run_sha256"] = cohort.digest({key: value for key, value in run.items() if key != "run_sha256"})
    evaluation.verify_run_media(run, tmp_path)
    (tmp_path / run["measurements"][0]["candidates"][0]["pair_path"]).write_bytes(b"changed media")
    with pytest.raises(ValueError, match="media changed"):
        evaluation.verify_run_media(run, tmp_path)


def test_no_results_count_as_known_miss_but_runtime_failure_is_unknown():
    run = run_document()
    run["measurements"][0]["candidates"] = []
    run["run_sha256"] = cohort.digest({key: value for key, value in run.items() if key != "run_sha256"})
    review = graded_review(run)
    result = evaluation.score_review(run, review)
    baseline = next(row for row in result["metrics"] if row["variant"] == "legacy-fixed")
    assert baseline["judged_top3"] == 0 and baseline["no_result_references"] == 1
    assert baseline["useful_top3_rate_among_judged_or_empty"] == 0
    run["measurements"][0]["status"] = "unavailable"
    run["run_sha256"] = cohort.digest({key: value for key, value in run.items() if key != "run_sha256"})
    review = evaluation.review_template(run)
    result = evaluation.score_review(run, review)
    assert all(row["useful_top3_rate_among_judged_or_empty"] is None for row in result["metrics"])
