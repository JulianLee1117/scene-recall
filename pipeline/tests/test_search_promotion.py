import pytest
from pipeline.eval.search_foundation import validate_promotion, validate_ann


def review():
    return {"profile_id": "test", "cases": [
        {"reference_id": str(i), "preference": "challenger", "baseline_ndcg10": .5,
         "challenger_ndcg10": .65, "known_positives_retained": True} for i in range(12)],
        "warm_p95_seconds": 4., "optional_physical_bytes": 10 * 1024**3,
        "complete_coverage": True, "reviewed_by": "human"}


@pytest.mark.parametrize("change", [
    lambda r: r.update(reviewed_by=None),
    lambda r: r.update(warm_p95_seconds=5.01),
    lambda r: r.update(optional_physical_bytes=65 * 1024**3),
    lambda r: r["cases"][0].update(preference=None),
    lambda r: r["cases"][0].update(known_positives_retained=False),
    lambda r: r["cases"][0].update(challenger_ndcg10=float("nan")),
])
def test_promotion_is_fail_closed(change):
    receipt = review()
    assert validate_promotion(receipt, "test")
    change(receipt)
    with pytest.raises(ValueError):
        validate_promotion(receipt, "test")


def test_ann_does_not_average_away_a_scope_failure():
    case = {"exact_units": list(range(100)), "ann_units": list(range(99)),
            "known_positive_units": [1], "exact_p95_ms": 100., "ann_p95_ms": 65.}
    assert validate_ann([case])
    failing = {**case, "ann_units": list(range(98))}
    with pytest.raises(ValueError):
        validate_ann([case] * 20 + [failing])
