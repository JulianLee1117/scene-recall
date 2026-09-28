"""A context treatment cannot change frozen offers or grant source authority."""
from copy import deepcopy
import json

import pytest

from pipeline.experiments import context_selection as evaluation
from pipeline.experiments.music_edit import HostedBudgetExceeded
from pipeline.lab.scene_selection import selection_schema
from pipeline.lab.selection_prompt import build_selection_payload
from pipeline.tests.test_lab_selection_prompt import _inputs


def frozen():
    inputs = _inputs()
    payload = build_selection_payload(*inputs)
    treatment = deepcopy(payload)
    treatment["source_context"] = {
        "contract": "hierarchical-source-context-v1", "profile_id": "fixture-profile",
        "records": {"r0": {"record_id": "sequence-1", "level": "sequence",
            "applicability": [{"start": 0., "end": 200.}], "parent_refs": [],
            "claims": [
                {"claim_id": "claim-1", "kind": "narrative", "text": "The departure may be unresolved.",
                 "status": "uncertain", "evidence_refs": ["e1"]},
                {"claim_id": "claim-2", "kind": "observed", "text": "A figure waits at the door.",
                 "status": "supported", "evidence_refs": ["e1"]}],
            "evidence": [{"evidence_id": "e1", "kind": "caption", "start": 40., "end": 49.,
                          "text": "A person waits beside a closed door."}]}},
        "sources": {"c0": {"status": "ready", "record_ids": ["r0"], "coverage": []},
                    "c1": {"status": "ready", "record_ids": ["r0"], "coverage": []}},
        "artifacts": ["fixture-artifact"]}
    return {"payload": payload, "context_on_payload": treatment,
            "schema": selection_schema(inputs[2], inputs[3]), "offers": inputs[2],
            "sources": inputs[3], "timing_scope": None, "document": inputs[0]}


def response(*, changed=False):
    return {"choices": {"shot_0": {"source": {"c1": 80.} if changed else {"c0": 40.},
                                       "reason": "Answer the unresolved musical phrase with a held image."},
                        "shot_2": {"source": None if changed else {"c1": 80.},
                                   "reason": "Keep an unresolved ending."}}}


def test_prepare_freezes_one_pair_and_preserves_uncertainty_and_pending_human_grades():
    value = frozen()
    original = deepcopy(value)
    run = evaluation.prepare_run(value)
    assert run["status"] == "dry-run" and run["frozen"] == original
    assert run["input_hashes"]["context-off"] != run["input_hashes"]["context-on"]
    assert run["schema_sha256"] == evaluation.digest(original["schema"])
    rows = run["factual_audit"]["rows"]
    assert len(rows) == 2 and rows[0]["source_aliases"] == ["c0", "c1"]
    assert rows[0]["claim"]["status"] == "uncertain"
    assert rows[0]["evidence"][0]["text"] == original["sources"]["unit-b"]["caption"]
    assert all(row["factual_grade"] is None and row["human_review_status"] == "pending" for row in rows)
    assert run["human_review"]["creative_preference"] is None
    value["sources"]["unit-b"]["caption"] = "caller mutation"
    assert run["frozen"] == original


def test_production_context_input_receipt_derives_baseline_without_live_lookup():
    value = frozen()
    receipt = deepcopy(value)
    receipt["payload"] = receipt.pop("context_on_payload")
    original = deepcopy(receipt)
    run = evaluation.prepare_run(receipt)
    assert run["frozen"] == value and receipt == original
    assert run["source_input_sha256"] == evaluation.digest(receipt)


def test_standalone_audit_retains_packing_omissions_and_limits_without_grading(tmp_path):
    value = frozen()
    packet = value["context_on_payload"]["source_context"]
    packet.update(truncated=True, limits={"max_claims_per_record": 4, "max_chars": 24000})
    packet["records"]["r0"]["claims_omitted"] = 7
    run = evaluation.prepare_run(value)
    evaluation.write_preparation(run, tmp_path)
    audit = json.loads((tmp_path / "factual-audit.json").read_text())
    assert audit["packet_truncated"] is True and audit["packet_limits"] == packet["limits"]
    assert {row["claims_omitted"] for row in audit["rows"]} == {7}
    assert all(row["human_review_status"] == "pending" and row["factual_grade"] is None for row in audit["rows"])
    assert run["contract"] == evaluation.CONTRACT and run["human_review"]["status"] == "pending"
    packet["limits"]["max_claims_per_record"] = 99
    assert run["factual_audit"]["packet_limits"]["max_claims_per_record"] == 4
    legacy = evaluation.prepare_run(frozen())["factual_audit"]
    assert legacy["packet_truncated"] is None and legacy["packet_limits"] is None
    assert all(row["claims_omitted"] is None for row in legacy["rows"])


@pytest.mark.parametrize("field", ["sources", "slots", "music_evidence", "timing_scope"])
def test_context_treatment_cannot_change_other_inputs(field):
    value = frozen()
    value["context_on_payload"][field] = {"changed": True}
    with pytest.raises(ValueError, match="differ only"):
        evaluation.prepare_run(value)


@pytest.mark.parametrize("mutation", ["schema", "candidate-order", "source-range", "extra-alias", "timing", "contract"])
def test_frozen_payload_and_schema_must_match_authoritative_offers(mutation):
    value = frozen()
    if mutation == "schema":
        value["schema"]["additionalProperties"] = True
    elif mutation == "candidate-order":
        value["offers"][0]["candidate_ids"].reverse()
        value["schema"] = selection_schema(value["offers"], value["sources"])
    elif mutation == "source-range":
        value["sources"]["unit-b"]["t_end"] += 1
        value["schema"] = selection_schema(value["offers"], value["sources"])
    elif mutation == "extra-alias":
        value["context_on_payload"]["source_context"]["sources"]["c99"] = {}
    elif mutation == "timing":
        value["offers"][0]["start"] += 1
    else:
        value["payload"]["response_contract"] = "changed"
        value["context_on_payload"]["response_contract"] = "changed"
    with pytest.raises(ValueError):
        evaluation.prepare_run(value)


def test_audit_retains_uncited_claims_and_reports_unresolved_references_without_grading():
    value = frozen()
    claims = value["context_on_payload"]["source_context"]["records"]["r0"]["claims"]
    claims[0]["evidence_refs"] = []
    claims[1]["evidence_refs"] = ["missing"]
    rows = evaluation.prepare_run(value)["factual_audit"]["rows"]
    assert rows[0]["evidence"] == [] and rows[0]["claim"]["status"] == "uncertain"
    assert rows[1]["unresolved_evidence_refs"] == ["missing"]
    assert rows[1]["factual_grade"] is None


def test_dry_cli_never_loads_runtime_config_or_sends_requests_and_preserves_inputs(monkeypatch, tmp_path):
    from pipeline import config
    from pipeline.lab import music
    monkeypatch.setattr(config, "load_config", lambda *_: pytest.fail("No runtime config in dry preparation"))
    monkeypatch.setattr(music, "_hosted_json", lambda *_a, **_k: pytest.fail("No hosted requests in dry preparation"))
    source, out = tmp_path / "input.json", tmp_path / "comparison"
    source.write_text(json.dumps(frozen()), encoding="utf-8")
    original = source.read_bytes()
    assert evaluation.main(["--input", str(source), "--out", str(out)]) == 0
    run = json.loads((out / "run.json").read_text())
    assert run["status"] == "dry-run" and run["hosted_limit"] == 0
    assert (out / "context-on-prompt.txt").is_file() and (out / "selection-schema.json").is_file()
    assert source.read_bytes() == original
    with pytest.raises(FileExistsError):
        evaluation.main(["--input", str(source), "--out", str(out)])


def test_frozen_output_replay_needs_no_config_or_model_and_builds_source_clip_material(monkeypatch, tmp_path):
    from pipeline.lab import music
    from pipeline.lab.models import ClipSelection
    monkeypatch.setattr(music, "_hosted_json", lambda *_a, **_k: pytest.fail("Frozen replay cannot call models"))
    value, outputs = frozen(), {"context-off": response(), "context-on": response(changed=True)}
    original = deepcopy((value, outputs))
    run = evaluation.execute_run(evaluation.prepare_run(value), None, tmp_path, frozen_outputs=outputs)
    assert run["status"] == "completed" and run["hosted_calls"] == [] and run["frozen_input_unchanged"]
    assert [row["slot"] for row in run["changed_slots"]] == [0, 2]
    assert run["measurements"][1]["gap_slots"] == [2]
    assert run["human_review"]["status"] == "pending"
    material = json.loads((tmp_path / "context-on-played.json").read_text())
    clip = ClipSelection.model_validate(material["clips"][0])
    assert clip.unit_id == "unit-a" and clip.source_start == 80. and clip.source_end == 83.
    assert material["slots"][1]["clip_id"] is None and material["rendered_file"] is None
    assert (value, outputs) == original


def test_execution_freezes_same_schema_and_offers_and_records_actual_model_receipts(config, monkeypatch, tmp_path):
    from pipeline.lab import music
    calls = []
    def hosted(_config, prompt, schema, *, receipt_path, operation):
        payload = json.loads(prompt.split("\n", 1)[1])
        calls.append((payload, deepcopy(schema)))
        receipt_path.write_text(json.dumps({"provider": "fixture", "model": "fixture-model", "request_id": f"req-{len(calls)}",
                                           "usage": {"total_tokens": 7}}), encoding="utf-8")
        return response(changed="source_context" in payload)
    monkeypatch.setattr(music, "_hosted_json", hosted)
    run = evaluation.execute_run(evaluation.prepare_run(frozen()), config, tmp_path, allow_hosted=True, maximum=2)
    assert len(calls) == len(run["hosted_calls"]) == 2
    assert calls[0][1] == calls[1][1]
    assert {key: item for key, item in calls[1][0].items() if key != "source_context"} == calls[0][0]
    for report in run["measurements"]:
        assert report["status"] == "completed" and report["seconds"] >= 0
        assert report["output_sha256"] and report["receipt_file_sha256"]
        assert report["hosted_calls"][0]["model"] == "fixture-model"
        assert report["hosted_calls"][0]["usage"]["total_tokens"] == 7
    for prepared in (run, evaluation.prepare_run(frozen())):
        with pytest.raises(ValueError, match="already executed"):
            evaluation.execute_run(prepared, config, tmp_path, allow_hosted=True, maximum=2)
    assert len(calls) == 2


def test_call_budget_blocks_second_request_before_dispatch_and_records_partial_failure(config, monkeypatch, tmp_path):
    from pipeline.lab import music
    calls = []
    monkeypatch.setattr(music, "_hosted_json", lambda *_a, **_k: calls.append(1) or response())
    run = evaluation.prepare_run(frozen())
    with pytest.raises(HostedBudgetExceeded):
        evaluation.execute_run(run, config, tmp_path, allow_hosted=True, maximum=1)
    saved = json.loads((tmp_path / "run.json").read_text())
    assert len(calls) == len(saved["hosted_calls"]) == 1 and saved["status"] == "failed"
    assert saved["measurements"][0]["status"] == "completed"
    assert saved["measurements"][1]["status"] == "failed"


@pytest.mark.parametrize("failure", ["provider", "unoffered", "outside-range"])
def test_failure_is_not_retried_and_invalid_choices_cannot_gain_context_range_authority(failure, config, monkeypatch, tmp_path):
    from pipeline.lab import music
    calls = []
    def hosted(*_a, **_k):
        calls.append(1)
        if failure == "provider":
            raise RuntimeError("Fixture provider failure")
        raw = response()
        raw["choices"]["shot_0"]["source"] = {"c99": 40.} if failure == "unoffered" else {"c0": 199.}
        return raw
    monkeypatch.setattr(music, "_hosted_json", hosted)
    with pytest.raises((ValueError, RuntimeError)):
        evaluation.execute_run(evaluation.prepare_run(frozen()), config, tmp_path, allow_hosted=True, maximum=2)
    saved = json.loads((tmp_path / "run.json").read_text())
    assert len(calls) == len(saved["hosted_calls"]) == 1 and saved["status"] == "failed"
    assert not (tmp_path / "context-off-played.json").exists()


@pytest.mark.parametrize("maximum", [-1, 3, True, 1.5])
def test_pair_never_accepts_more_than_two_or_ambiguous_hosted_budget(maximum, tmp_path):
    with pytest.raises(ValueError, match="zero to two"):
        evaluation.execute_run(evaluation.prepare_run(frozen()), None, tmp_path, allow_hosted=True, maximum=maximum)


def test_execution_without_permission_cannot_send_even_first_request(monkeypatch, tmp_path):
    from pipeline.lab import music
    monkeypatch.setattr(music, "_hosted_json", lambda *_a, **_k: pytest.fail("Permission is absent"))
    with pytest.raises(HostedBudgetExceeded):
        evaluation.execute_run(evaluation.prepare_run(frozen()), None, tmp_path)
    assert json.loads((tmp_path / "run.json").read_text())["hosted_calls"] == []


@pytest.mark.parametrize("target", ["frozen", "prompt"])
def test_prepared_input_mutation_is_detected_before_execution(target, tmp_path):
    run = evaluation.prepare_run(frozen())
    if target == "frozen":
        run["frozen"]["document"]["brief"] = "Later edit"
    else:
        run["prompts"]["context-on"] += "Later instruction"
    with pytest.raises(ValueError, match="changed before execution"):
        evaluation.execute_run(run, None, tmp_path, frozen_outputs={name: response() for name in evaluation.VARIANTS})
    assert not (tmp_path / "run.json").exists()


def test_flexible_timing_replay_uses_current_joint_cut_solver(tmp_path):
    from pipeline.tests.test_lab_scene_selection import _timed, _sources, _choice
    from pipeline.lab.scene_selection import TIMED_SELECTION_CONTRACT
    offers, scope = _timed()
    document, _, _, _, capabilities, _, _ = _inputs()
    document["passage"] = deepcopy(scope["passage"])
    sources = _sources()
    payload = build_selection_payload(document, [], offers, sources, capabilities, scope, TIMED_SELECTION_CONTRACT)
    treatment = {**deepcopy(payload), "source_context": {"records": {}, "sources": {}, "artifacts": []}}
    value = {"payload": payload, "context_on_payload": treatment, "schema": selection_schema(offers, sources, scope),
             "sources": sources, "offers": offers, "timing_scope": scope}
    raw = {"choices": {"shot_0": _choice("c0", frame=48, position=.5), "shot_1": _choice("c2", frame=144, position=1.)}}
    run = evaluation.execute_run(evaluation.prepare_run(value), None, tmp_path,
                                 frozen_outputs={name: raw for name in evaluation.VARIANTS})
    choices = json.loads((tmp_path / "context-on-choices.json").read_text())
    assert choices[0]["duration"] == 2. and choices[0]["source_start"] == 14.
    assert choices[-1]["start"] + choices[-1]["duration"] == scope["passage"]["end"]
    assert run["changed_slots"] == []
