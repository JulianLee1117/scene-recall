"""Saved pacing must reach generation through the durable worker snapshot."""

from copy import deepcopy
import json

import pytest

from pipeline.lab import direction_planner, music, timing_planner
from pipeline.lab.store import LabStore
from pipeline.lab.worker import execute_job
from pipeline.tests.test_lab import db, store  # noqa: F401
from pipeline.tests.test_lab_generation import _project, _stages
from pipeline.tests.test_lab_search_plans import _caps


@pytest.mark.parametrize("pacing", [None, "patient", "balanced", "kinetic"])
def test_saved_pacing_survives_reopen_queue_and_worker_generation(
    config, store, db, tmp_path, monkeypatch, pacing,
):
    project = _project(store, db, tmp_path)
    document = deepcopy(project["document"])
    if pacing is None:
        # Simulate a genuinely old persisted document, before this field existed.
        # create_project/update_project would normalize it with today's defaults.
        document.pop("planner_settings")
        with store.connection() as connection:
            connection.execute(
                "UPDATE projects SET document=? WHERE id=?",
                (json.dumps(document), project["id"]),
            )
            connection.execute(
                "UPDATE revisions SET document=? WHERE project_id=? AND revision=?",
                (json.dumps(document), project["id"], project["revision"]),
            )
    else:
        document["planner_settings"]["pacing"] = pacing
        project = store.update_project(project["id"], project["revision"], document)

    reopened = LabStore(config.paths.state_dir)
    project = reopened.get_project(project["id"])
    before = deepcopy(project)
    assert project["document"] == document
    queued = reopened.enqueue("generate", project["id"], project["revision"],
                              generate={"mode": "regenerate"})
    worker_store = LabStore(config.paths.state_dir)
    job = worker_store.claim()
    assert job["id"] == queued["id"]
    assert job["base_revision"] == before["revision"]
    assert job["document"] == job["snapshot"]["document"] == before["document"]
    assert worker_store.get_project(project["id"]) == before

    calls = _stages(monkeypatch, worker_store, before)
    result = execute_job(job, config, db, worker_store)
    assert result["status"] == "completed", result["error"]
    assert result["result"]["applied"] is True
    assert [call["stage"] for call in calls] == ["timing", "plan", "draft"]
    assert all(call["document"]["planner_settings"]["pacing"] == (pacing or "balanced")
               for call in calls)
    saved = LabStore(config.paths.state_dir).get_project(project["id"])
    assert saved["revision"] == before["revision"] + 1
    assert saved["document"]["passage"] == before["document"]["passage"]
    assert saved["document"]["planner_settings"]["pacing"] == (pacing or "balanced")
    assert worker_store.get_job(job["id"], private=True)["document"] == before["document"]


@pytest.mark.parametrize("user_changes_settings_after_queue", [False, True])
def test_rapid_dispatch_runs_real_planner_and_preserves_saved_work_and_failure_history(
    config, store, db, tmp_path, monkeypatch, user_changes_settings_after_queue,
):
    project = _project(store, db, tmp_path, filled=(0, 1, 2))
    document = deepcopy(project["document"])
    document["planner_settings"]["pacing"] = "rapid"
    project = store.update_project(project["id"], project["revision"], document)

    # An explicit retry must retain the old failure, never revive or replace it.
    failed = store.enqueue("generate", project["id"], project["revision"],
                           generate={"mode": "regenerate"})
    assert store.claim()["id"] == failed["id"]
    store.finish(failed["id"], error="Old worker rejected Rapid pacing")
    failed_before = store.get_job(failed["id"], private=True)
    assert store.get_project(project["id"]) == project

    reopened = LabStore(config.paths.state_dir)
    assert reopened.get_project(project["id"]) == project
    queued = reopened.enqueue("generate", project["id"], project["revision"],
                              generate={"mode": "regenerate"})
    latest = project
    if user_changes_settings_after_queue:
        changed = deepcopy(document)
        changed["planner_settings"]["pacing"] = "patient"
        latest = reopened.update_project(project["id"], project["revision"], changed)

    worker_store = LabStore(config.paths.state_dir)
    job = worker_store.claim()
    assert job["id"] == queued["id"]
    assert job["base_revision"] == project["revision"]
    assert job["document"] == job["snapshot"]["document"] == project["document"]

    # Keep real worker dispatch, generation validation, the whole-edit planner,
    # timing checks and transactional finish. Stub only external model/source work.
    real_timing_planner = timing_planner.run_timing_job
    calls = _stages(monkeypatch, worker_store, latest)
    monkeypatch.setattr(timing_planner, "run_timing_job", real_timing_planner)
    monkeypatch.setattr(direction_planner, "search_capabilities", lambda *_: _caps(config))
    hosted_calls = []

    def hosted(_config, prompt, schema, **kwargs):
        payload = json.loads(prompt.split("\n", 1)[1])
        hosted_calls.append(payload)
        assert schema["properties"]["end_frames"]["minItems"] == 1
        assert schema["properties"]["end_frames"]["maxItems"] == 144
        assert kwargs["operation"] == "timing"
        assert worker_store.get_project(project["id"]) == latest
        return {"end_frames": [12, 30, 42, 66, 78, 96, 120, 144], "notes": [
            {"start_frame": 0, "end_frame": 144, "reason": "Editorial choice: vary the visual reading time", "evidence_ids": []}]}

    monkeypatch.setattr(music, "_hosted_json", hosted)
    result = execute_job(job, config, db, worker_store)

    assert result["status"] == "completed", result["error"]
    assert result["result"]["stages"] == ["timing", "plan", "draft"]
    assert len(hosted_calls) == 1
    assert [call["stage"] for call in calls] == ["plan", "draft"]
    assert all(call["document"]["planner_settings"]["pacing"] == "rapid" for call in calls)
    assert worker_store.get_job(failed["id"], private=True) == failed_before
    assert worker_store.get_job(job["id"], private=True)["document"] == project["document"]
    assert worker_store.claim() is None

    saved = worker_store.get_project(project["id"])
    if user_changes_settings_after_queue:
        assert result["result"]["applied"] is False
        assert saved == latest
        proposed = result["result"]["document"]
    else:
        assert result["result"]["applied"] is True
        assert saved["revision"] == project["revision"] + 1
        proposed = saved["document"]
    assert proposed["planner_settings"] == project["document"]["planner_settings"]
    assert proposed["passage"] == project["document"]["passage"]
    assert proposed["clips"][:3] == project["document"]["clips"]
    slots = proposed["music_timeline"]["slots"]
    assert len(slots) == 8 and all(slot["clip_id"] for slot in slots)
    assert slots[0]["start"] == 0 and slots[-1]["end"] == 6
