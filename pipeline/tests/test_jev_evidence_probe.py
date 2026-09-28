from copy import deepcopy

from pipeline.experiments import jev_evidence_probe as probe
from pipeline.experiments import search_intent as original
from pipeline.tests.test_search_intent_experiment import capture


def test_new_prompt_keeps_identical_query_scope_excerpts_and_hides_references():
    value = capture()
    base = value["cases"][0]
    value["cases"] = [{**deepcopy(base), "id": case_id} for case_id in probe.CASE_IDS]
    before = deepcopy(value)
    plan = probe.build_plan(value)
    for case, request in zip(value["cases"], plan["requests"]):
        old = original._requests(case)["evidence"]
        assert request["body"]["state"]["candidates"] == old["state"]["candidates"]
        assert request["body"]["state"]["query"] == old["state"]["query"]
        assert request["body"]["state"]["explicit_film_ids"] == old["state"]["explicit_film_ids"]
        assert set(request["body"]["questions"]) == set(old["questions"])
        assert "expected_windows" not in request["body"]["state"]
    assert value == before


def test_old_score_is_exact_existing_policy_and_penalty_preserves_membership_tail():
    case = capture()["cases"][0]
    labels = {f"c{i}": ("contradicted" if i < 4 else "supported" if i % 3 == 0 else "partial") for i in range(48)}
    expected = original.rerank(case, evidence=labels)["evidence-judgment-only"]
    assert probe.rank_ids(case, labels) == [row["unit_id"] for row in expected]
    penalty = probe.rank_ids(case, labels, penalize=True)
    assert penalty.index("unit-0") > 0
    assert len(penalty) == len(set(penalty)) == 60
    assert set(penalty) == {row["unit_id"] for row in case["candidates"]}
    assert penalty[48:] == [row["unit_id"] for row in case["candidates"][48:]]


def test_unknown_and_missing_evidence_do_not_receive_contradiction_penalty():
    case = capture()["cases"][0]
    for row in case["candidates"]:
        row["caption"] = ""
    baseline = [row["unit_id"] for row in case["candidates"]]
    assert probe.rank_ids(case, {"c0": "contradicted"}, penalize=True) == baseline
    assert probe.rank_ids(case, {"c0": "unknown"}, penalize=True) == baseline


def test_original_float_operation_order_preserves_near_tie_at_positions_six_and_seventeen():
    case = capture()["cases"][0]
    labels = {"c16": "partial"}
    expected = [row["unit_id"] for row in original.rerank(case, evidence=labels)["evidence-judgment-only"]]
    actual = probe.rank_ids(case, labels)
    assert actual == expected
    assert actual.index("unit-5") < actual.index("unit-16")
