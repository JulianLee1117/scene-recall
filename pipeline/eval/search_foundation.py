"""Frozen comparisons and promotion checks for optional search routes.

Human judgments are explicit input. Measured speed and complete coverage alone
never certify a creative ranking change.
"""
import math
from statistics import median


def ndcg(relevances, k=10, *, ideal_relevances=None):
    values = [float(value) for value in relevances]
    ideal_values = values if ideal_relevances is None else [float(value) for value in ideal_relevances]
    if any(not math.isfinite(value) or value < 0 or value > 3 for value in [*values, *ideal_values]):
        raise ValueError("Relevance must be a finite grade from zero to three")
    dcg = sum((2**value - 1) / math.log2(index + 2) for index, value in enumerate(values[:k]))
    ideal = sum((2**value - 1) / math.log2(index + 2) for index, value in enumerate(sorted(ideal_values, reverse=True)[:k]))
    return dcg / ideal if ideal else 0.


def validate_promotion(receipt, identity):
    if not isinstance(receipt, dict) or receipt.get("profile_id") != identity:
        raise ValueError("A profile-specific review receipt is required")
    cases = receipt.get("cases", [])
    if (not isinstance(cases, list) or not all(isinstance(case, dict) and isinstance(case.get("reference_id"), str) for case in cases)
            or len(cases) != 12 or len({case.get("reference_id") for case in cases}) != 12):
        raise ValueError("Review twelve distinct frozen reference comparisons")
    wins, regressions, improvements = 0, 0, []
    for case in cases:
        if not case.get("reference_id") or case.get("preference") not in {"baseline", "challenger", "tie"}:
            raise ValueError("Every reference needs an explicit human preference")
        baseline, challenger = case.get("baseline_ndcg10"), case.get("challenger_ndcg10")
        if any(type(value) not in {int, float} or not math.isfinite(value) or not 0 <= value <= 1 for value in (baseline, challenger)):
            raise ValueError("Every reference needs measured nDCG@10")
        if case.get("known_positives_retained") is not True:
            raise ValueError("Known positive recall must pass for every reference")
        wins += case["preference"] == "challenger"
        regressions += challenger < baseline
        improvements.append((challenger - baseline) / baseline if baseline else (1. if challenger else 0.))
    if wins < 8 or regressions > 2 or median(improvements) < .20 - 1e-12:
        raise ValueError("Composition quality gate did not pass")
    for key, maximum in (("warm_p95_seconds", 5.), ("optional_physical_bytes", 64 * 1024**3)):
        value = receipt.get(key)
        if type(value) not in {int, float} or not math.isfinite(value) or not 0 <= value <= maximum:
            raise ValueError(f"Missing or failed measured gate: {key}")
    if (receipt.get("complete_coverage") is not True or not isinstance(receipt.get("reviewed_by"), str)
            or not receipt["reviewed_by"].strip()):
        raise ValueError("Complete coverage and an identified human review are required")
    return True


def validate_ann(cases):
    """Every route/scope passes independently; do not hide losses in averages."""
    if not cases:
        raise ValueError("ANN comparison cases are required")
    for case in cases:
        exact, approximate = set(case["exact_units"]), set(case["ann_units"])
        positives = set(case["known_positive_units"])
        if not exact or len(exact & approximate) / len(exact) < .99 or not positives <= approximate:
            raise ValueError("ANN candidate recall gate did not pass")
        before, after = case["exact_p95_ms"], case["ann_p95_ms"]
        if not (math.isfinite(before) and math.isfinite(after) and before > 0 and 0 <= after <= .70 * before):
            raise ValueError("ANN retrieval latency gate did not pass")
    return True
