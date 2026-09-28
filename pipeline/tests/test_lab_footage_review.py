"""Inspection is bounded evidence, never unchecked source/timeline authority."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from pipeline.lab import footage_observations as observations, footage_review as review
from pipeline.lab.media import JobCancelled
from pipeline.lab.models import ClipSelection, ProjectDocument


def fixture(count=4, *, flexible=False):
    doc = ProjectDocument(track={"id": "music", "name": "Music", "duration": count * 2},
                          passage={"start": 0, "end": count * 2}).model_dump(mode="json")
    slots, sources, offers, ledger = [], {}, [], []
    for index in range(count):
        key = f"unit-{index}"
        clip = ClipSelection(id=f"clip-{index}", film_id=f"film-{index}", unit_id=key,
                             source_start=10, source_end=12).model_dump(mode="json")
        doc["clips"].append(clip)
        slots.append({"id": f"slot-{index}", "start": index * 2, "end": index * 2 + 2,
                      "clip_id": clip["id"], "direction_source": "ai", "section_index": 0})
        for identity in (key, key + "-alt"):
            sources[identity] = {"unit_id": identity, "film_id": "film-" + identity,
                                 "t_start": 5., "t_end": 25., "caption": "Caption", "film_title": "Film"}
        sources[key]["film_id"] = clip["film_id"]
        offer = {"id": slots[-1]["id"], "slot": index, "start": index * 2., "duration": 2.,
                 "candidate_ids": [key, key + "-alt"], "candidate_evidence": {}}
        if flexible:
            offer["timing"] = {"min_duration": 1., "max_duration": 3.}
        offers.append(offer)
        ledger.append({"slot_id": slots[-1]["id"], "inspection_hint": "action_timing", "decision": "selected", "selected_unit_id": key})
    doc["music_timeline"] = {"track_id": "music", "passage": doc["passage"], "slots": slots}
    doc = ProjectDocument.model_validate(doc).model_dump(mode="json")
    scope = None
    if flexible:
        scope = {"passage": doc["passage"], "fps": 24, "total_frames": count * 48,
                 "nominal": [[row["id"], row["start"], row["end"]] for row in slots],
                 "boundary_frames": [[0], *[[i * 48 - 12, i * 48, i * 48 + 12] for i in range(1, count)], [count * 48]]}
    return doc, [{"offers": offers, "sources": sources, "ledger": ledger, "timing_scope": scope}]


def sample(window, *args, **kwargs):
    left = window["source_start"]
    return {"artifact_id": window["unit_id"], "window": deepcopy(window), "cache_reused": False,
            "summary": "A person lowers an object", "uncertainty": "Sparse samples",
            "frames": [{"id": f"sample-{index}", "timestamp": left + index * .5, "frame_end": left + index * .5 + 1 / 24,
                        "sha256": "a" * 64} for index in range(6)],
            "events": [{"id": "event-0", "start": left, "end": left + 2., "completion": "visible",
                        "before": "Object raised", "after": "Object lowered", "evidence_ids": ["sample-0", "sample-4"]}]}


@pytest.fixture
def setup(monkeypatch, tmp_path):
    calls = []
    def inspect(window, *args, **kwargs):
        calls.append(deepcopy(window))
        return sample(window)
    monkeypatch.setattr(observations, "inspect_window", inspect)
    monkeypatch.setattr(review, "_review", lambda prepared, *args: {
        row["slot"]["id"]: {"choice": "keep", "baseline_fit": "supported", "reason": "Image serves the musical pause"} for row in prepared})
    return SimpleNamespace(paths=SimpleNamespace(assets_dir=tmp_path)), calls


def run(doc, contexts, setup):
    config, calls = setup
    return review.inspect_edit(doc, config, None, lambda _: None, "job", contexts=contexts)


def test_global_budget_priority_and_timeline_ties(setup):
    doc, contexts = fixture(6)
    contexts[0]["ledger"][0]["inspection_hint"] = "visual_fit"
    # Several selection batches still share one entire-generation budget.
    second = deepcopy(contexts[0])
    for key in ("offers", "ledger"):
        second[key] = second[key][3:]
        contexts[0][key] = contexts[0][key][:3]
    result, report = run(doc, [*contexts, second], setup)
    assert result["clips"] == doc["clips"]
    assert [(row["start"], row["end"]) for row in result["music_timeline"]["slots"]] == [(row["start"], row["end"]) for row in doc["music_timeline"]["slots"]]
    assert [row["position"] for row in report["targets"]] == [2, 3]
    assert report["eligible_count"] == 6
    assert report["window_count"] == 4 and len(setup[1]) == 4
    assert all(window["source_end"] - window["source_start"] <= 8 for window in setup[1])


def test_absent_hint_locked_abstained_and_stale_choice_are_not_inspected(setup):
    doc, contexts = fixture()
    contexts[0]["ledger"][0]["inspection_hint"] = "none"
    doc["clips"][1]["locked"] = True
    contexts[0]["ledger"][2]["decision"] = "abstained"
    contexts[0]["ledger"][3]["selected_unit_id"] = "other"
    result, report = run(doc, contexts, setup)
    assert result == doc and report["status"] == "not-needed" and not setup[1]


def test_optional_sampling_failure_retains_uninspected_baseline(monkeypatch, setup):
    doc, contexts = fixture()
    monkeypatch.setattr(observations, "inspect_window", lambda *a, **k: (_ for _ in ()).throw(observations.InspectionUnavailable("Decoder unavailable")))
    result, report = run(doc, contexts, setup)
    assert result == doc and report["status"] == "unavailable"
    assert report["inspected_count"] == 0 and report["changed_count"] == 0


def test_missing_baseline_cannot_be_replaced_from_alternative_only(monkeypatch, setup):
    doc, contexts = fixture()
    def inspect(window, *a, **k):
        if not window["unit_id"].endswith("-alt"):
            raise observations.InspectionUnavailable("No samples")
        return sample(window)
    monkeypatch.setattr(observations, "inspect_window", inspect)
    result, report = run(doc, contexts, setup)
    assert result == doc and report["inspected_count"] == 0 and report["window_count"] == 2


@pytest.mark.parametrize("failure", [ValueError("Source changed"), JobCancelled("Cancelled")])
def test_authority_and_cancellation_abort_instead_of_optional_fallback(monkeypatch, setup, failure):
    doc, contexts = fixture()
    monkeypatch.setattr(observations, "inspect_window", lambda *a, **k: (_ for _ in ()).throw(failure))
    with pytest.raises(type(failure), match=str(failure)):
        run(doc, contexts, setup)


def test_cancel_before_inspection_makes_no_model_calls(setup):
    doc, contexts = fixture()
    with pytest.raises(JobCancelled):
        review.inspect_edit(doc, setup[0], None, lambda _: None, "job", contexts=contexts, cancelled=lambda: True)
    assert not setup[1]


def test_optional_review_failure_preserves_baseline(monkeypatch, setup):
    doc, contexts = fixture()
    monkeypatch.setattr(review, "_review", lambda *a: (_ for _ in ()).throw(ValueError("Unrecognized trim")))
    result, report = run(doc, contexts, setup)
    assert result == doc and report["status"] == "unavailable"
    assert report["inspected_count"] == 2


def test_action_span_moves_only_nearby_cut_and_revalidates_neighbor(monkeypatch, setup):
    doc, contexts = fixture(flexible=True)
    for row in contexts[0]["ledger"][1:]:
        row["inspection_hint"] = "none"
    def choose(prepared, *args):
        target = prepared[0]
        option = next(key for key, row in target["options"].items()
                      if row["kind"] == "observed_action" and row["unit_id"] == "unit-0")
        return {"slot-0": {"choice": option, "baseline_fit": "supported", "reason": "Let the lowering resolve before the pause"}}
    monkeypatch.setattr(review, "_review", choose)
    result, report = run(doc, contexts, setup)
    assert result["music_timeline"]["slots"][0]["end"] == 2.5
    assert result["music_timeline"]["slots"][1]["start"] == 2.5
    assert result["music_timeline"]["slots"][1]["end"] == 4
    assert result["clips"][0]["source_start"] == 9.5
    assert result["clips"][0]["source_end"] == 12
    assert result["clips"][1]["source_end"] == 11.5
    assert result["clips"][2:] == doc["clips"][2:]
    assert report["changed_count"] == 1


def test_fixed_timeline_never_offers_action_that_needs_longer_slot(setup):
    doc, contexts = fixture()
    target = review.inspection_targets(doc, contexts)[0][0]
    scope, _ = review._scope(doc, [target])
    candidate, window = review._candidate_windows(target, doc)[0]
    options = review._trim_options(target, [(candidate, sample(window))], scope)
    assert options and all(row["kind"] != "observed_action" for row in options.values())


def test_uncertain_target_freezes_its_shared_boundary():
    doc, contexts = fixture(flexible=True)
    targets, _ = review.inspection_targets(doc, contexts)
    scope, current = review._scope(doc, targets)
    targets[0]["options"] = {"trim_0": {"minimum_duration": 2.3, "maximum_duration": 4}}
    targets[1]["options"] = {}
    choices = {"slot-0": {"choice": "trim_0", "baseline_fit": "supported"},
               "slot-1": {"choice": "keep", "baseline_fit": "uncertain"}}
    with pytest.raises(ValueError, match="cannot fit"):
        review._fit(doc, targets, choices, scope, current)


def test_contradicted_match_becomes_honest_gap(monkeypatch, setup):
    doc, contexts = fixture()
    monkeypatch.setattr(review, "_review", lambda prepared, *a: {
        row["slot"]["id"]: {"choice": "gap", "baseline_fit": "contradicted", "reason": "The sample shows intimacy rather than fastening a coat"} for row in prepared})
    result, report = run(doc, contexts, setup)
    assert all(not slot["clip_id"] for slot in result["music_timeline"]["slots"][:2])
    assert len(result["clips"]) == 2 and report["changed_count"] == 2
    assert doc["music_timeline"]["slots"][0]["clip_id"]  # Private proposal, input untouched.


def test_sampled_visual_can_keep_action_intentionally_in_progress(setup):
    doc, contexts = fixture()
    target = review.inspection_targets(doc, contexts)[0][0]
    scope, _ = review._scope(doc, [target])
    candidate, window = review._candidate_windows(target, doc)[0]
    observed = sample(window)
    observed["events"][0]["completion"] = "uncertain"
    options = review._trim_options(target, [(candidate, observed)], scope)
    assert options and all(row["kind"] == "sampled_visual" for row in options.values())


def test_hint_schema_and_parser_are_version_scoped():
    from pipeline.lab.scene_selection import selection_choices, selection_schema, response_contract
    doc, contexts = fixture(1)
    context = contexts[0]
    output = {"choices": {"shot_0": {"source": {"c0": 10.}, "reason": "Lowering gesture", "inspection_hint": "action_timing"}}}
    choices = selection_choices(output, context["offers"], context["sources"], inspection=True)
    assert choices[0]["inspection_hint"] == "action_timing"
    assert "inspection_hint" not in selection_schema(context["offers"], context["sources"])["properties"]["choices"]["properties"]["shot_0"]["properties"]
    assert response_contract(inspection=True) != response_contract()
    output["choices"]["shot_0"]["inspection_hint"] = "watch_everything"
    with pytest.raises(ValueError, match="invalid scene choice"):
        selection_choices(output, context["offers"], context["sources"], inspection=True)


@pytest.mark.parametrize("value", ['"false"', '1', 'null'])
def test_inspection_flag_rejects_non_boolean_config(tmp_path, value):
    from pipeline.config import load_config
    from pipeline.tests.test_config import MINIMAL_CONFIG, _write_config
    path = _write_config(tmp_path, MINIMAL_CONFIG + f"\n    lab:\n      footage_inspection: {value}\n")
    with pytest.raises(ValueError, match="footage_inspection must be a boolean"):
        load_config(path)


@pytest.mark.parametrize("operation,expected", [("inspect", "Writing footage observations"), ("review", "Writing the footage review")])
def test_hosted_progress_names_the_actual_inspection_stage(config, monkeypatch, tmp_path, operation, expected):
    from pipeline.lab import music
    config.lab.music_provider = "openai"
    config.lab.planner_model = "gpt-5.6-terra"
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    def hosted(_config, _key, _prompt, _schema, _audio, progress, **kwargs):
        progress("Choosing the sequence")
        return {}, "response", {}
    monkeypatch.setattr(music, "_openai_json", hosted)
    progress = []
    music._hosted_json(config, "Prompt", {}, receipt_path=tmp_path / "receipt.json", progress=progress.append, operation=operation)
    assert expected in progress and "Choosing the sequence" not in progress


def test_inspected_keep_preserves_exact_off_grid_fixed_cuts(setup):
    doc, contexts = fixture()
    doc["music_timeline"]["slots"][0]["end"] = 2.01
    doc["music_timeline"]["slots"][1]["start"] = 2.01
    doc["clips"][0]["source_end"] = 12.01
    doc["clips"][1]["source_end"] = 11.99
    doc = ProjectDocument.model_validate(doc).model_dump(mode="json")
    result, report = run(doc, contexts, setup)
    assert [(slot["start"], slot["end"]) for slot in result["music_timeline"]["slots"]] == [
        (slot["start"], slot["end"]) for slot in doc["music_timeline"]["slots"]]
    assert result["clips"] == doc["clips"]
    assert report["changed_count"] == 0 and report["timing_adjustments"] == []


def test_inspected_keep_does_not_normalize_unrelated_valid_source_duration(setup):
    doc, contexts = fixture()
    # The saved-document contract permits a source-duration difference below
    # one output frame. Inspection of other positions must not rewrite it.
    doc["clips"][3]["source_end"] = 12.01
    doc = ProjectDocument.model_validate(doc).model_dump(mode="json")
    result, report = run(doc, contexts, setup)
    assert result["clips"] == doc["clips"]
    assert result["music_timeline"] == doc["music_timeline"]
    assert report["changed_count"] == 0
