"""Private evaluation measurements must not become source or quality authority."""
from copy import deepcopy
import json

import pytest

from pipeline.experiments import music_edit as evaluation
from pipeline.lab.models import ClipSelection, ProjectDocument


def frozen():
    document = ProjectDocument(track={"id": "track", "name": "Song", "duration": 60},
        passage={"start": 10, "end": 12}, analysis={"summary": "Frozen interpretation"},
        clips=[ClipSelection(id="clip-a", film_id="film", unit_id="unit", source_start=20, source_end=21)],
        music_timeline={"track_id": "track", "passage": {"start": 10, "end": 12},
            "slots": [{"id": "one", "start": 10, "end": 11, "section_index": 0, "clip_id": "clip-a"},
                      {"id": "two", "start": 11, "end": 12, "section_index": 0}]}).model_dump(mode="json")
    return {"id": "project", "revision": 4, "document": document}


def test_prepare_is_private_and_never_loads_config_or_runs_models(monkeypatch):
    from pipeline import config
    monkeypatch.setattr(config, "load_config", lambda *_: pytest.fail("Dry preparation must not load runtime services"))
    before = frozen()
    original = deepcopy(before)
    result = evaluation.prepare_run(before, "inspection", [{"slot_id": "one", "reason": "Known visible mismatch"}])
    assert result["status"] == "dry-run" and result["human_preference"] is None
    assert result["source_project_id"] == "project" and result["source_revision"] == 4
    assert result["variants"] == ["inspection-off", "inspection-on"]
    assert result["metrics_before"]["gaps"] == ["two"]
    result["document"]["analysis"]["summary"] = "Private scratch state"
    assert before == original
    assert result["source_input_sha256"] == evaluation.digest(original)


def test_report_ignores_clip_uuid_churn_but_records_real_source_and_timing_changes():
    original = frozen()["document"]
    changed = deepcopy(original)
    changed["clips"][0]["id"] = "new-uuid"
    changed["music_timeline"]["slots"][0]["clip_id"] = "new-uuid"
    assert evaluation.document_changes(original, changed)["changed_positions"] == []
    changed["clips"][0]["source_start"] += .1
    changed["clips"][0]["source_end"] += .1
    report = evaluation.document_changes(original, changed)
    assert report["slot_ids_preserved"] and report["position_count_change"] == 0
    assert [row["position"] for row in report["changed_positions"]] == [1]


def test_coverage_does_not_treat_unlabelled_shots_as_false_positives():
    document = frozen()["document"]
    report = evaluation.flag_coverage(document, [{"slot_id": "one", "reason": "Incomplete observed gesture"}],
                                      ["two"], ["two"])
    assert report["unflagged_known_failures"] == ["one"]
    assert report["flagged_unlabelled_slots"] == ["two"]
    assert report["precision"] is None


@pytest.mark.parametrize("known,flagged", [
    ([{"slot_id": "missing", "reason": "Invalid scope"}], []),
    ([{"slot_id": "one", "reason": ""}], []),
    ([{"slot_id": "one", "reason": "a"}, {"slot_id": "one", "reason": "b"}], []),
    ([], ["outside"]),
])
def test_coverage_rejects_ambiguous_or_out_of_scope_labels(known, flagged):
    with pytest.raises(ValueError):
        evaluation.flag_coverage(frozen()["document"], known, flagged, [])


def test_missing_listening_does_not_trigger_an_audio_stage():
    document = frozen()["document"]
    document["analysis"] = None
    with pytest.raises(ValueError, match="never listens"):
        evaluation.prepare_run(document, "timing")


def test_hosted_budget_denies_by_default_and_stops_before_over_budget_call(monkeypatch, tmp_path):
    from pipeline.lab import music
    sent = []
    def hosted(*args, **kwargs):
        sent.append(kwargs)
        kwargs["receipt_path"].write_text(json.dumps({"model": "fixture", "usage": {"total_tokens": 7}}), encoding="utf-8")
        return {"fixture": True}
    monkeypatch.setattr(music, "_hosted_json", hosted)
    with evaluation.hosted_budget(allow_hosted=False, maximum=5) as records:
        with pytest.raises(evaluation.HostedBudgetExceeded):
            music._hosted_json(receipt_path=tmp_path / "never.json")
    assert not sent and records == []
    with evaluation.hosted_budget(allow_hosted=True, maximum=1) as records:
        assert music._hosted_json(receipt_path=tmp_path / "one.json", operation="inspect") == {"fixture": True}
        with pytest.raises(evaluation.HostedBudgetExceeded):
            music._hosted_json(receipt_path=tmp_path / "two.json")
    assert len(sent) == len(records) == 1 and records[0]["usage"]["total_tokens"] == 7
    assert records[0]["operation"] == "inspect" and records[0]["status"] == "completed"
    assert music._hosted_json is hosted


def test_failed_hosted_request_consumes_its_slot_and_is_never_retried(monkeypatch):
    from pipeline.lab import music
    def failed(*args, **kwargs):
        raise ValueError("Hosted fixture failed")
    monkeypatch.setattr(music, "_hosted_json", failed)
    with evaluation.hosted_budget(allow_hosted=True, maximum=1) as calls:
        with pytest.raises(ValueError, match="Hosted fixture"):
            music._hosted_json()
        with pytest.raises(evaluation.HostedBudgetExceeded):
            music._hosted_json()
    assert len(calls) == 1 and calls[0]["status"] == "failed"


@pytest.mark.parametrize("maximum", [-1, 6, True, 2.5])
def test_hosted_budget_is_explicit_and_bounded(maximum):
    with pytest.raises(ValueError, match="zero to five"):
        with evaluation.hosted_budget(allow_hosted=True, maximum=maximum):
            pass


def listening_document():
    # Share the validated timing fixture rather than invent another audio schema.
    from pipeline.tests.test_lab_timing_planner import _document
    document = _document(start=10, duration=30)
    document["analysis"]["edit_beats"] = []
    return document


def test_beat_ablation_withholds_derived_guides_and_nested_receipts_without_changing_evidence():
    from pipeline.lab.timing_planner import timing_payload
    document = listening_document()
    nested = {"original_request": {"rhythm": {"beats": [13.123], "downbeats": [14.123]}}, "bpm": 197}
    document["analysis"]["provenance"]["prior_request"] = deepcopy(nested)
    document["rhythm"]["provenance"]["analysis"] = deepcopy(nested)
    document["song_context"] = {"track_id": "track", "notes": "Follow these words",
        "lyrics": [{"id": "line", "start": 10, "end": 14, "text": "User supplied line"}]}
    original = deepcopy(document)
    baseline = timing_payload(document)
    assert baseline == timing_payload(document, beat_guides=True)
    without = timing_payload(document, beat_guides=False)
    assert {"beats", "downbeats", "markers"}.isdisjoint(without["music"]["measured"])
    assert "relative_rms" in without["music"]["measured"]
    assert without["song_context"] == baseline["song_context"]
    assert "prior_request" not in json.dumps(without) and "13.123" not in json.dumps(without)
    assert '"bpm"' not in json.dumps(without)
    assert without["evaluation"]["beat_guides"] is False
    assert evaluation.digest(without) != evaluation.digest(baseline)
    assert original == document


def test_beat_ablation_keeps_explicit_user_markers():
    from pipeline.lab.timing_planner import timing_payload
    document = listening_document()
    document["rhythm"].update(marker_source="user", track_id="track", passage=deepcopy(document["passage"]))
    measured = timing_payload(document, beat_guides=False)["music"]["measured"]
    assert measured["marker_source"] == "user" and measured["markers"]["frames"] == [360]
    assert "beats" not in measured and "downbeats" not in measured


def test_selection_ablation_removes_only_additional_pulse_cut_choices():
    from pipeline.lab.source_timing import initial_timing_scope
    document = listening_document()
    document["music_timeline"] = {"track_id": "track", "passage": deepcopy(document["passage"]), "slots": [
        {"id": "one", "start": 10, "end": 20, "section_index": 0},
        {"id": "two", "start": 20, "end": 40, "section_index": 0}]}
    document["rhythm"]["beats"] = [19, 19.5, 20.5, 21]
    original = deepcopy(document)
    baseline = initial_timing_scope(document)
    assert baseline == initial_timing_scope(document, beat_guides=True)
    without = initial_timing_scope(document, beat_guides=False)
    assert baseline["boundary_frames"][1] == [192, 216, 228, 240, 252, 264, 288]
    assert without["boundary_frames"][1] == [192, 240, 288]
    assert without["nominal"] == baseline["nominal"] and without["passage"] == baseline["passage"]
    assert document == original


def test_dry_cli_writes_reviewable_payloads_without_loading_services(monkeypatch, tmp_path):
    from pipeline import config
    from pipeline.lab import music
    monkeypatch.setattr(config, "load_config", lambda *_: pytest.fail("Dry CLI must not load runtime config"))
    monkeypatch.setattr(music, "_hosted_json", lambda *_a, **_k: pytest.fail("Dry CLI must not send requests"))
    source, output = tmp_path / "source.json", tmp_path / "comparison"
    source.write_text(json.dumps(listening_document()), encoding="utf-8")
    original = source.read_bytes()
    assert evaluation.main(["timing", "--input", str(source), "--out", str(output)]) == 0
    run = json.loads((output / "run.json").read_text(encoding="utf-8"))
    assert run["status"] == "dry-run" and not run["hosted_allowed"]
    assert (output / "beats-off-timing-payload.json").is_file()
    assert source.read_bytes() == original
    with pytest.raises(FileExistsError):
        evaluation.main(["timing", "--input", str(source), "--out", str(output)])


def test_execution_reuses_baseline_and_spends_one_call_only_for_ablation(config, monkeypatch, tmp_path):
    from pipeline.lab import music, timing_planner
    from pipeline.tests.test_lab_timing_planner import _output
    document = listening_document()
    monkeypatch.setattr(music, "_hosted_json", lambda *_a, **_k: _output([240, 720]))
    timing_planner.run_timing_job({"id": "cached", "document": document}, config, lambda _: None)
    calls = []
    def hosted(_config, prompt, *_a, **_k):
        payload = json.loads(prompt.split("\n", 1)[1]); calls.append(payload)
        assert payload["evaluation"]["beat_guides"] is False
        return _output([480, 720])
    monkeypatch.setattr(music, "_hosted_json", hosted)
    output = tmp_path / "execution"; output.mkdir()
    run = evaluation.prepare_run(document, "timing")
    original = deepcopy(document)
    result = evaluation.execute_run(run, document, config, None, output, allow_hosted=True, maximum=1, cached_baseline=True)
    assert result["status"] == "completed" and result["frozen_input_unchanged"]
    assert len(calls) == len(result["hosted_calls"]) == 1
    assert result["measurements"][0]["diagnostics"]["cache_reused"] is True
    assert len(result["between_variants"]["changed_positions"]) == 2
    scopes = json.loads((output / "beats-off-cut-offers.json").read_text(encoding="utf-8"))
    assert scopes[0]["evaluation"]["beat_guides"] is False
    assert document == original


def test_uncached_baseline_cannot_spend_the_ablation_budget(config, monkeypatch, tmp_path):
    from pipeline.lab import music
    monkeypatch.setattr(music, "_hosted_json", lambda *_a, **_k: pytest.fail("Baseline must be cached"))
    document = listening_document()
    run = evaluation.prepare_run(document, "timing")
    with pytest.raises(evaluation.HostedBudgetExceeded):
        evaluation.execute_run(run, document, config, None, tmp_path, allow_hosted=True, maximum=1, cached_baseline=True)
    saved = json.loads((tmp_path / "run.json").read_text(encoding="utf-8"))
    assert saved["status"] == "failed" and saved["hosted_calls"] == []


def test_inspection_replays_one_frozen_selection_and_labels_injected_hints(config, monkeypatch, tmp_path):
    from pipeline.lab import footage_review
    value = frozen()
    value["document"]["analysis"] = listening_document()["analysis"]
    # Keep the fixture arrangement while giving it a validated matching listening scope.
    value["document"]["analysis"]["provenance"]["passage"] = value["document"]["passage"]
    value["document"]["analysis"]["segments"][0].update(start=10, end=12)
    value["contexts"] = [{"offers": [], "sources": {}, "timing_scope": None,
        "ledger": [{"slot_id": "one", "inspection_hint": "none"}]}]
    original = deepcopy(value)
    calls = []
    def inspect(document, _config, _db, _progress, _job, *, contexts):
        calls.append(deepcopy(contexts))
        assert _config.lab.footage_inspection
        assert contexts[0]["ledger"][0]["inspection_hint"] == "action_timing"
        document["music_timeline"]["slots"][0]["clip_id"] = None
        contexts[0]["sources"]["scratch"] = "must remain private"
        return document, {"status": "completed", "flagged_slot_ids": ["one"], "inspected_slot_ids": ["one"], "reviewed_slot_ids": ["one"]}
    monkeypatch.setattr(footage_review, "inspect_edit", inspect)
    run = evaluation.prepare_run(value, "inspection", [{"slot_id": "one", "reason": "Supplied mismatch"}])
    result = evaluation.execute_run(run, value, config, None, tmp_path, injected_hints={"one": "action_timing"})
    assert len(calls) == 1 and result["hosted_calls"] == []
    assert result["injected_hint_slot_ids"] == ["one"] and not result["automatic_hints_unchanged"]
    assert result["measurements"][0]["metrics"]["gaps"] == ["two"]
    assert result["measurements"][1]["metrics"]["gaps"] == ["one", "two"]
    assert result["measurements"][1]["coverage"]["inspected_known_failures"] == ["one"]
    assert value == original


@pytest.mark.parametrize("hint", ["invented", {}, None])
def test_hint_injection_refuses_invalid_values(hint):
    with pytest.raises(ValueError, match="action_timing"):
        evaluation.inject_inspection_hints([{"ledger": [{"slot_id": "one"}]}], {"one": hint})


def test_injection_rejects_non_ledger_slots_and_missing_diagnostics_are_not_zero_coverage():
    with pytest.raises(ValueError, match="frozen selector ledger"):
        evaluation.inject_inspection_hints([{"ledger": [{"slot_id": "one"}]}], {"outside": "visual_fit"})
    with pytest.raises(ValueError, match="flagged_slot_ids"):
        evaluation._inspection_ids({}, "flagged_slot_ids")
