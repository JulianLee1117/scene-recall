"""The experiment must stay bounded and preserve evidence on partial failures."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from pipeline.experiments import assembly_comparison as comparison, music_edit
from pipeline.lab.models import ProjectDocument


def frozen():
    from pipeline.tests.test_lab_timing_planner import _document
    document = _document(start=10, duration=30)
    document["analysis"]["edit_beats"] = []
    document["music_timeline"] = {"track_id": "track", "passage": document["passage"], "slots": [
        {"id": "s0", "start": 10, "end": 25, "section_index": 0},
        {"id": "s1", "start": 25, "end": 40, "section_index": 0}]}
    return {"id": "frozen-project", "revision": 4, "document": ProjectDocument.model_validate(document).model_dump(mode="json")}


def test_prepare_is_dry_preserves_source_identity_and_requires_current_evidence():
    value = frozen()
    original = deepcopy(value)
    run = music_edit.prepare_run(value, "assembly")
    assert run["variants"] == ["fixed-slots", "joint-assembly", "expanded-discovery"]
    assert run["source_revision"] == 4 and run["limits"]["hosted_calls"] == 4
    assert run["status"] == "dry-run" and run["human_preference"] is None
    run["document"]["analysis"]["summary"] = "scratch"
    assert value == original
    value["document"]["analysis"]["provenance"]["track"] = "stale"
    with pytest.raises(ValueError, match="never listens"):
        comparison.prepare_run(value)


def test_placed_locks_are_rejected():
    value = frozen()
    value["document"]["clips"] = [{"id": "locked", "film_id": "f", "source_start": 0, "source_end": 15, "locked": True}]
    value["document"]["music_timeline"]["slots"][0]["clip_id"] = "locked"
    with pytest.raises(ValueError, match="placed lock"):
        comparison.prepare_run(value)


def test_query_summary_exposes_recipes_and_coverage_without_unoffered_sources():
    state = {"catalog": {"a": {}}, "queries": [{"id": "q0", "recipe": {"reason": "contrast",
        "search_plan": {"clauses": []}, "intention_ids": ["r0"]}, "rows": [
            {"unit_id": "a", "eligible": True}, {"unit_id": "a", "eligible": True},
            {"unit_id": "hidden-source", "eligible": True},
            {"unit_id": "missing", "eligible": False, "exclusion": "source-media-unavailable"}]}]}
    summary = comparison.query_summary(state)[0]
    assert summary["reason"] == "contrast" and summary["search_plan"] == {"clauses": []}
    assert summary["returned_count"] == 4 and summary["eligible_unique_count"] == 2
    assert summary["catalog_count"] == 1 and summary["retained_outside_catalog_count"] == 1
    assert summary["exclusions"] == {"source-media-unavailable": 1}
    assert "hidden-source" not in json.dumps(summary)


def test_registered_music_is_read_only_and_byte_identity_is_checked(config, tmp_path):
    music = tmp_path / "music.wav"
    music.write_bytes(b"original music")
    identity = hashlib.sha256(music.read_bytes()).hexdigest()
    path = config.paths.state_dir / "lab" / "lab.sqlite3"
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE tracks(id TEXT, path TEXT, duration REAL)")
        db.execute("INSERT INTO tracks VALUES(?,?,?)", (identity, str(music), 60))
    document = {"track": {"id": identity, "duration": 60}}
    store = comparison.FrozenTracks(config, document)
    assert store.get_track(identity)["path"] == str(music)
    with pytest.raises(ValueError, match="outside"):
        store.get_track("another")
    music.write_bytes(b"different music")
    with pytest.raises(ValueError, match="bytes changed"):
        comparison.FrozenTracks(config, document)


def harness(monkeypatch, config, tmp_path, *, needs=True, fail_stage=None, expansion_adds=True):
    from pipeline.experiments import assembly_discovery as discovery, assembly_sequence as sequence
    from pipeline.lab import music
    from pipeline.search import capabilities
    value = frozen()
    document = value["document"]
    catalog = {"u0": {"unit_id": "u0"}, "u1": {"unit_id": "u1"}}
    need = {"reason": "missing contrast", "search_plan": {"clauses": []}}
    state = {"catalog": catalog, "queries": [], "intent": {"intentions": []}}
    calls, renders, payloads = [], [], []
    monkeypatch.setattr(comparison, "FrozenTracks", lambda *_: object())
    monkeypatch.setattr(capabilities, "search_capabilities", lambda *_: {"version": "fixture"})
    monkeypatch.setattr(discovery, "discovery_payload", lambda *_: {"music": "frozen"})
    monkeypatch.setattr(discovery, "discovery_prompt", lambda *_: "discovery")
    monkeypatch.setattr(discovery, "discovery_schema", lambda: {})
    monkeypatch.setattr(discovery, "validate_discovery_intent", lambda *_: {"intentions": []})
    monkeypatch.setattr(discovery, "discover_candidates", lambda *_a, **_k: deepcopy(state))
    def expand(*_a, **_k):
        result = deepcopy(state)
        if expansion_adds:
            result["catalog"]["u2"] = {"unit_id": "u2"}
        return result
    monkeypatch.setattr(discovery, "expand_candidates", expand)
    monkeypatch.setattr(sequence, "fixed_baseline_payload", lambda *_a, **_k: {"payload": {"pool": catalog}, "prompt": "baseline", "schema": {}, "manifest": {}})
    monkeypatch.setattr(sequence, "fixed_baseline_document", lambda *_: (deepcopy(document), {"baseline": True}))
    def payload(_doc, pool, _caps, **kwargs):
        payloads.append({"pool": deepcopy(pool), **kwargs})
        return {"pool": deepcopy(pool), **kwargs}
    monkeypatch.setattr(sequence, "build_assembly_payload", payload)
    monkeypatch.setattr(sequence, "build_assembly_prompt", lambda p: json.dumps(p))
    monkeypatch.setattr(sequence, "assembly_schema", lambda *_: {})
    monkeypatch.setattr(sequence, "assemble_document", lambda _d, _r, _c, **kw: (deepcopy(document),
        {"discovery_needs": [need] if needs and kw["discovery_enabled"] else []}))
    def hosted(_config, _prompt, _schema, **kwargs):
        stage = kwargs["receipt_path"].name.removesuffix("-receipt.json")
        calls.append(stage)
        if stage == fail_stage:
            raise ValueError("fixture failure")
        kwargs["receipt_path"].write_text(json.dumps({"usage": {"total_tokens": 10}}), encoding="utf-8")
        return {"result": stage}
    monkeypatch.setattr(music, "_hosted_json", hosted)
    def render(_doc, _cfg, _db, _tracks, _out, variant, _progress):
        renders.append(variant)
        return str(tmp_path / f"{variant}.mp4")
    monkeypatch.setattr(comparison, "render_variant", render)
    return value, calls, renders, payloads


def test_four_call_comparison_retains_shared_catalog_and_previous_draft(config, monkeypatch, tmp_path):
    value, calls, renders, payloads = harness(monkeypatch, config, tmp_path)
    original = deepcopy(value)
    run = comparison.prepare_run(value)
    comparison.execute_run(run, value, config, None, tmp_path, allow_hosted=True, maximum=4)
    assert calls == ["discovery", "fixed-slots", "joint-assembly", "expanded-discovery"]
    assert renders == comparison.VARIANTS
    assert list(payloads[1]["pool"]) == ["u0", "u1", "u2"]
    assert payloads[1]["previous_draft"] == {"result": "joint-assembly"}
    assert payloads[0]["discovery_enabled"] and not payloads[1]["discovery_enabled"]
    assert run["status"] == "completed" and run["frozen_input_unchanged"] and value == original
    assert run["human_preference"] is None and len(run["hosted_calls"]) == 4


@pytest.mark.parametrize("needs,adds", [(False, True), (True, False)])
def test_no_request_or_no_new_candidates_skips_extra_model_call(config, monkeypatch, tmp_path, needs, adds):
    value, calls, _, _ = harness(monkeypatch, config, tmp_path, needs=needs, expansion_adds=adds)
    run = comparison.prepare_run(value)
    comparison.execute_run(run, value, config, None, tmp_path, allow_hosted=True, maximum=4)
    assert len(calls) == 3 and run["measurements"][-1]["status"] == "skipped"


def test_failed_expansion_preserves_completed_arms_and_counts_failure(config, monkeypatch, tmp_path):
    value, calls, renders, _ = harness(monkeypatch, config, tmp_path, fail_stage="expanded-discovery")
    run = comparison.prepare_run(value)
    comparison.execute_run(run, value, config, None, tmp_path, allow_hosted=True, maximum=4)
    assert len(calls) == 4 and renders == ["fixed-slots", "joint-assembly"]
    assert run["status"] == "partial" and run["hosted_calls"][-1]["status"] == "failed"
    assert (tmp_path / "joint-assembly-document.json").is_file()
    assert not (tmp_path / "expanded-discovery-document.json").exists()


def test_default_denies_paid_requests_and_budget_is_checked_before_send(config, monkeypatch, tmp_path):
    value, calls, _, _ = harness(monkeypatch, config, tmp_path)
    run = comparison.prepare_run(value)
    with pytest.raises(music_edit.HostedBudgetExceeded):
        comparison.execute_run(run, value, config, None, tmp_path)
    assert calls == [] and run["status"] == "failed"
    with pytest.raises(ValueError, match="four"):
        comparison.execute_run(run, value, config, None, tmp_path, allow_hosted=True, maximum=5)


def test_interrupt_records_cancelled_arm_and_call_without_retry(config, monkeypatch, tmp_path):
    from pipeline.lab import music
    value, calls, _, _ = harness(monkeypatch, config, tmp_path)
    original = music._hosted_json
    def interrupt(*args, **kwargs):
        if kwargs["receipt_path"].name == "fixed-slots-receipt.json":
            raise KeyboardInterrupt()
        return original(*args, **kwargs)
    monkeypatch.setattr(music, "_hosted_json", interrupt)
    run = comparison.prepare_run(value)
    with pytest.raises(KeyboardInterrupt):
        comparison.execute_run(run, value, config, None, tmp_path, allow_hosted=True, maximum=4)
    saved = json.loads((tmp_path / "run.json").read_text(encoding="utf-8"))
    assert calls == ["discovery"]
    assert saved["status"] == "cancelled"
    assert saved["measurements"][0]["status"] == "cancelled"
    assert saved["hosted_calls"][-1]["status"] == "cancelled"


def test_cli_dry_run_does_not_load_runtime_or_create_live_state(monkeypatch, tmp_path):
    from pipeline import config
    monkeypatch.setattr(config, "load_config", lambda *_: pytest.fail("Dry run loaded services"))
    source, output = tmp_path / "frozen.json", tmp_path / "run"
    source.write_text(json.dumps(frozen()), encoding="utf-8")
    assert music_edit.main(["assembly", "--input", str(source), "--out", str(output)]) == 0
    assert json.loads((output / "run.json").read_text())["status"] == "dry-run"
    with pytest.raises(FileExistsError):
        music_edit.main(["assembly", "--input", str(source), "--out", str(output)])


@pytest.mark.parametrize("error,status", [(ValueError("startup failed"), "failed"), (KeyboardInterrupt(), "cancelled")])
def test_cli_startup_failure_records_terminal_status_before_any_model_call(monkeypatch, tmp_path, error, status):
    from pipeline.lab import resources
    source, output = tmp_path / "frozen.json", tmp_path / "run"
    source.write_text(json.dumps(frozen()), encoding="utf-8")
    def fail_startup():
        raise error
    monkeypatch.setattr(resources, "configure_editor_process", fail_startup)
    with pytest.raises(type(error)):
        music_edit.main(["assembly", "--input", str(source), "--out", str(output), "--execute"])
    saved = json.loads((output / "run.json").read_text(encoding="utf-8"))
    assert saved["status"] == status and saved["active_stage"] == "preflight"
    assert saved["hosted_limit"] == 0
    assert (output / "frozen-input.json").is_file()
