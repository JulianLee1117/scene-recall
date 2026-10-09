"""Text-only planning: exact scope, evidence honesty, preserved edits and no replay."""
from copy import deepcopy
import json
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from pipeline.lab import music
from pipeline.lab.direction_planner import DirectionPlan, run_direction_job
from pipeline.lab.models import ClipSelection, JobRequest, ProjectDocument
from pipeline.lab.timeline import PROVISIONAL_TIMING_CONTRACT, ensure_timeline, plan_targets, timing_fingerprint
from pipeline.lab.worker import execute_job
from pipeline.tests.test_lab import db, store  # noqa: F401
from pipeline.tests.test_lab_music_moments import _doc


def _document():
    document = _doc(); ensure_timeline(document)
    return ProjectDocument.model_validate(document).model_dump(mode="json")


def _answer(ids):
    return {"visual_plan": {"arc": "Distance gives way to an intimate human detail", "motifs": "Open space and window light"},
            "directions": [{"slot_id": identity, "direction": {
        "query": f"wide quiet landscape {index}", "search_facet": "all", "search_plan": None,
        "purpose": "Establish distance before the intimate next image", "music_cue": "No current audio evidence; this is a visual proposal",
        "timing_note": "Hold a readable image for the fixed slot, without promising a complete action",
    }} for index, identity in enumerate(ids)]}


def _job(document, ids=None):
    return {"id": "test-plan", "kind": "plan", "document": document,
            "snapshot": {"slot_ids": ids} if ids is not None else {}}


def test_plan_default_and_explicit_targets_honor_ownership_locks_and_cap():
    document = _document(); slots = document["music_timeline"]["slots"]
    slots[0]["direction_source"] = "user"
    clip = ClipSelection(id="kept", film_id="f", source_start=0, source_end=5.25, locked=True).model_dump()
    document["clips"] = [clip]; slots[1]["clip_id"] = "kept"
    assert [row["id"] for row in plan_targets(document)] == [slots[2]["id"]]
    assert plan_targets(document, [slots[0]["id"]])[0] == slots[0]
    with pytest.raises(ValueError, match="Unlock"):
        plan_targets(document, [slots[1]["id"]])
    with pytest.raises(ValueError, match="missing or repeated"):
        plan_targets(document, [slots[0]["id"], slots[0]["id"]])
    slots[2]["direction_source"] = None
    with pytest.raises(ValueError, match="No eligible"):
        plan_targets(document)
    document["music_timeline"]["slots"] = [{"id": str(index)} for index in range(33)]
    with pytest.raises(ValueError, match="at most 32"):
        plan_targets(document)


def test_manual_plan_never_listens_or_retrieves_and_changes_only_targets(config, monkeypatch):
    document = _document(); document["analysis"] = None
    slots = document["music_timeline"]["slots"]
    slots[0]["direction_source"] = "user"
    slots[1]["needs_direction"] = True
    target = slots[1]["id"]
    original = deepcopy(document)
    calls = []
    def hosted(_config, prompt, schema, **kwargs):
        payload = json.loads(prompt.split("\n", 1)[1]); calls.append((payload, kwargs))
        assert payload["music_evidence"]["audio_interpretation"] is None
        assert "No current audio interpretation" in payload["audio_evidence_note"]
        assert "not selecting or generating footage" in prompt
        assert "cannot promise that an action completes" in prompt
        return _answer([target])
    monkeypatch.setattr(music, "_hosted_json", hosted)
    no_audio = MagicMock(side_effect=AssertionError("Audio path was entered"))
    no_search = MagicMock(side_effect=AssertionError("Retrieval was entered"))
    monkeypatch.setattr(music, "_decode_audio", no_audio)
    monkeypatch.setattr(music, "retrieve_edit_candidates", no_search)
    result = run_direction_job(_job(document, [target]), config, object(), lambda _: None)
    assert result["analysis"] is None and result["clips"] == original["clips"]
    assert result["music_timeline"]["slots"][0] == original["music_timeline"]["slots"][0]
    assert result["music_timeline"]["slots"][2] == original["music_timeline"]["slots"][2]
    assert result["music_timeline"]["slots"][1]["needs_direction"] is False
    assert result["music_timeline"]["slots"][1]["direction_source"] == "ai"
    for before, after in zip(original["music_timeline"]["slots"], result["music_timeline"]["slots"]):
        assert (before["id"], before["start"], before["end"], before["clip_id"]) == (after["id"], after["start"], after["end"], after["clip_id"])
    assert calls[0][1]["operation"] == "plan" and "audio_path" not in calls[0][1]
    no_audio.assert_not_called(); no_search.assert_not_called()


def test_explicit_plan_claims_starter_timing_without_changing_cuts(config, monkeypatch):
    document = _document()
    timeline = document["music_timeline"]
    timeline["provisional_timing"] = {"contract": PROVISIONAL_TIMING_CONTRACT, "fingerprint": timing_fingerprint(timeline)}
    ids = [slot["id"] for slot in timeline["slots"]]
    monkeypatch.setattr(music, "_hosted_json", lambda *a, **k: _answer(ids))
    result = run_direction_job(_job(document), config, object(), lambda _: None)
    assert result["music_timeline"]["provisional_timing"] is None
    assert [(slot["id"], slot["start"], slot["end"]) for slot in result["music_timeline"]["slots"]] == [
        (slot["id"], slot["start"], slot["end"]) for slot in timeline["slots"]]
    assert timeline["provisional_timing"] is not None  # The input snapshot is immutable.


def test_plan_caches_exact_context_and_invalidates_on_brief_or_model(config, monkeypatch):
    document = _document(); ids = [slot["id"] for slot in document["music_timeline"]["slots"]]
    provider = MagicMock(return_value=_answer(ids)); monkeypatch.setattr(music, "_hosted_json", provider)
    first = run_direction_job(_job(document), config, object(), lambda _: None)
    again = run_direction_job(_job(document), config, object(), lambda _: None)
    assert again["direction_plan"]["cache_reused"] is True
    assert again["music_timeline"] == first["music_timeline"]
    assert provider.call_count == 1
    document["brief"] = "A new visual story"
    run_direction_job(_job(document), config, object(), lambda _: None)
    config.lab.planner_model = "new-planner-version"
    run_direction_job(_job(document), config, object(), lambda _: None)
    assert provider.call_count == 3


@pytest.mark.parametrize("corruption", ["missing", "extra", "duplicate", "timing", "facet"])
def test_malformed_output_never_changes_project_or_populates_cache(config, monkeypatch, corruption):
    document = _document(); before = deepcopy(document)
    ids = [slot["id"] for slot in document["music_timeline"]["slots"]]
    output = _answer(ids)
    if corruption == "missing": output["directions"].pop()
    elif corruption == "extra": output["directions"].append(_answer(["invented"])["directions"][0])
    elif corruption == "duplicate": output["directions"][1] = deepcopy(output["directions"][0])
    elif corruption == "timing": output["directions"][0]["start"] = 1
    elif corruption == "facet": output["directions"][0]["direction"]["search_facet"] = "motion"
    monkeypatch.setattr(music, "_hosted_json", lambda *a, **k: output)
    with pytest.raises(ValueError):
        run_direction_job(_job(document), config, object(), lambda _: None)
    assert document == before
    assert not list((config.paths.assets_dir / "lab" / "direction-plans").glob("*.json"))


def test_planner_context_uses_only_current_audio_and_resolved_caption(config, db, monkeypatch):
    from pipeline.search.capabilities import search_capabilities
    monkeypatch.setattr("pipeline.lab.direction_planner.search_capabilities", lambda *_: search_capabilities(config, object()))
    document = _document(); slots = document["music_timeline"]["slots"]
    document["analysis"]["provenance"] = {"track": "track", "passage": document["passage"]}
    clip = ClipSelection(id="existing", film_id="f", title="A title is not a caption", source_start=1, source_end=2.25).model_dump()
    document["clips"] = [clip]; slots[0]["clip_id"] = "existing"
    # Populate a scalar caption row using the existing test row factory.
    from pipeline.tests.test_retrieve import _make_unit_row
    row = _make_unit_row("source", "f", caption="A solitary person at a window", t_start=0., t_end=5.)
    from pipeline.tests.test_search_recipe import _chain
    unit_table = MagicMock(); unit_table.search.return_value = _chain([row])
    database = MagicMock(); database.open_table.return_value = unit_table
    payloads = []
    def hosted(_config, prompt, schema, **kwargs):
        payloads.append(json.loads(prompt.split("\n", 1)[1])); return _answer([slots[1]["id"]])
    monkeypatch.setattr(music, "_hosted_json", hosted)
    with patch("pipeline.index.writer.table_names", return_value={"units"}):
        result = run_direction_job(_job(document, [slots[1]["id"]]), config, database, lambda _: None)
    assert payloads[0]["music_evidence"]["audio_interpretation"]["summary"] == document["analysis"]["summary"]
    assert payloads[0]["timeline"][0]["selected_source"]["caption_evidence"]["text"] == row["caption"]
    assert result["analysis"] == document["analysis"] and result["clips"] == document["clips"]


def test_default_targets_frozen_and_stale_or_cancelled_results_preserve_edits(config, store, monkeypatch):
    document = _document(); original_user = document["music_timeline"]["slots"][0]
    original_user["direction_source"] = "user"
    project = store.create_project("Directions", "music-sketch")
    project = store.update_project(project["id"], 1, document)
    store.enqueue("plan", project["id"], 2)
    job = store.claim()
    assert job["snapshot"]["slot_ids"] == [slot["id"] for slot in document["music_timeline"]["slots"]][1:]
    provider = MagicMock(return_value=_answer(job["snapshot"]["slot_ids"]))
    monkeypatch.setattr(music, "_hosted_json", provider)
    edited = store.update_project(project["id"], 2, {**project["document"], "brief": "Edited while planning"})
    result = execute_job(job, config, object(), store)
    assert result["status"] == "completed" and result["result"]["applied"] is False
    assert store.get_project(project["id"]) == edited
    store.enqueue("plan", project["id"], 3)
    cancelled = store.claim(); store.cancel(cancelled["id"])
    result = execute_job(cancelled, config, object(), store)
    assert result["status"] == "cancelled"
    assert store.get_project(project["id"]) == edited
    assert provider.call_count == 1


def test_provenance_clears_on_passage_change_but_survives_brief_and_valid_restore(store):
    document = _document()
    document["direction_plan"] = {"contract": "test", "track_id": "track", "passage": document["passage"]}
    project = store.create_project("Plan", "music-sketch")
    project = store.update_project(project["id"], 1, document)
    assert project["document"]["direction_plan"] is not None
    changed = {**project["document"], "brief": "Only the brief changed"}
    project = store.update_project(project["id"], 2, changed)
    assert project["document"]["direction_plan"] is not None
    changed = {**project["document"], "music_timeline": None, "passage": {"start": 0, "end": 6}}
    project = store.update_project(project["id"], 3, changed)
    assert project["document"]["direction_plan"] is None
    restored = store.restore(project["id"], 4, 2)
    assert restored["document"]["direction_plan"] == document["direction_plan"]


def test_plan_api_works_without_playback_media_and_freezes_targets(config, db, store):
    from pipeline.api.main import app
    document = _document(); document["analysis"] = None
    project = store.create_project("Manual direction", "music-sketch")
    project = store.update_project(project["id"], 1, document)
    with patch("pipeline.api.main.load_config", return_value=config), patch("pipeline.api.main.open_db", return_value=db), patch("pipeline.api.main.ensure_search_indexes"), TestClient(app) as client:
        result = client.post(f"/lab/projects/{project['id']}/jobs", json={"kind": "plan", "base_revision": 2})
        assert result.status_code == 200, result.text
        snapshot = store.get_job(result.json()["id"], private=True)["snapshot"]
        assert len(snapshot["slot_ids"]) == 3
        assert client.post(f"/lab/projects/{project['id']}/jobs", json={"kind": "plan", "base_revision": 1}).status_code == 409
    assert JobRequest(kind="plan", base_revision=1, slot_ids=["a"]).slot_ids == ["a"]


def test_strict_output_schema_requires_all_direction_fields():
    schema = DirectionPlan.model_json_schema()
    direction = schema["$defs"]["GeneratedDirection"]
    assert set(direction["required"]) == set(direction["properties"])
    assert direction["additionalProperties"] is False


def test_timing_guides_require_current_provenance_or_explicit_manual_scope(config, monkeypatch):
    document = _document(); slot = document["music_timeline"]["slots"][0]
    payloads = []
    def hosted(_config, prompt, schema, **kwargs):
        payloads.append(json.loads(prompt.split("\n", 1)[1])); return _answer([slot["id"]])
    monkeypatch.setattr(music, "_hosted_json", hosted)
    document["rhythm"] = {"provenance": {"track": "other", "passage": {"start": 0, "end": 9}},
                          "beats": [1, 11], "markers": [2, 12]}
    run_direction_job(_job(document, [slot["id"]]), config, object(), lambda _: None)
    assert payloads[-1]["music_evidence"]["measured"] == {}
    document["rhythm"] = {"marker_source": "user", "track_id": "track", "passage": document["passage"],
                          "markers": [9, 10, 13, 20, "14", True], "beats": [1, 11]}
    run_direction_job(_job(document, [slot["id"]]), config, object(), lambda _: None)
    assert payloads[-1]["music_evidence"]["measured"] == {"markers": [10, 13], "marker_source": "user"}
    document["rhythm"]["provenance"] = {"track": "track", "passage": document["passage"]}
    run_direction_job(_job(document, [slot["id"]]), config, object(), lambda _: None)
    assert payloads[-1]["music_evidence"]["measured"]["beats"] == [11]
