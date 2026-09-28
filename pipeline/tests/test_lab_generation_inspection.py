"""Inspection is one optional, cancellable budget before the atomic edit save."""
from copy import deepcopy
import json
import sys
from types import ModuleType

import pytest

from pipeline.lab import music_planner
from pipeline.lab.media import JobCancelled
from pipeline.tests.test_lab import db, store  # noqa: F401
from pipeline.tests.test_lab_generation import _project as short_project, _stages as short_stages
from pipeline.tests.test_lab_long_generation import _project as long_project, _stages as long_stages, _run


def _fixture(kind, store, db, tmp_path, monkeypatch):
    if kind == "short-fixed":
        project = short_project(store, db, tmp_path, filled=(0,), locked=(0,))
        calls = short_stages(monkeypatch, store, project)
    else:
        project = long_project(store, db, tmp_path, duration=120, fixed=kind == "long-fixed")
        calls = long_stages(monkeypatch)
    return project, calls


def _collect(monkeypatch):
    original_fill = music_planner.fill_timeline
    collectors = []

    def fill(document, config, db, progress, job_id, slot_ids=None, **options):
        collector = options.pop("review_contexts")
        collectors.append(collector)
        result = original_fill(document, config, db, progress, job_id, slot_ids, **options)
        collector.append({"slot_ids": list(slot_ids), "timing_scope": deepcopy(options.get("timing_scope")), "job_id": job_id})
        return result

    monkeypatch.setattr(music_planner, "fill_timeline", fill)
    return collectors


def _review(monkeypatch, callback):
    module = ModuleType("pipeline.lab.footage_review")
    module.inspect_edit = callback
    monkeypatch.setitem(sys.modules, module.__name__, module)


@pytest.mark.parametrize("kind", ["short-fixed", "long-fixed", "whole"])
def test_inspection_runs_once_after_all_selections_and_reports_final_gaps(config, store, db, tmp_path, monkeypatch, kind):
    config.lab.footage_inspection = True
    project, calls = _fixture(kind, store, db, tmp_path, monkeypatch)
    collectors = _collect(monkeypatch)
    inspections = []
    diagnostic = {"contract": "targeted-footage-inspection-v1", "status": "completed", "changed_count": 1}

    def inspect(document, _config, _db, _progress, job_id, *, contexts, cancelled):
        assert len([call for call in calls if call["stage"] in {"draft", "select"}]) == (1 if kind == "short-fixed" else 2)
        assert all(collector is contexts for collector in collectors)
        assert len(contexts) == len(collectors)
        assert not cancelled()
        assert store.get_project(project["id"]) == project
        replay = json.loads((config.paths.assets_dir / "lab" / "requests" / f"{job_id}-inspection-input.json").read_text())
        assert replay == {"document": document, "contexts": contexts}
        assert all(slot.get("clip_id") for slot in replay["document"]["music_timeline"]["slots"])
        inspections.append(job_id)
        result = deepcopy(document)
        slots = result["music_timeline"]["slots"]
        target = next(slot for slot in slots if slot["id"] == contexts[-1]["slot_ids"][-1])
        target.update(clip_id=None, search_error="The sampled source does not support this action.")
        if kind == "whole":
            # The final receipt must describe the reviewed fit, not the earlier selector result.
            slots[0]["end"] += 1 / 24
            slots[1]["start"] = slots[0]["end"]
            clips = {clip["id"]: clip for clip in result["clips"]}
            for slot in slots[:2]:
                clips[slot["clip_id"]]["source_end"] = clips[slot["clip_id"]]["source_start"] + slot["end"] - slot["start"]
        return result, diagnostic

    _review(monkeypatch, inspect)
    result, job = _run(store, config, db, project, mode="regenerate" if kind == "whole" else "fill")
    assert result["status"] == "completed", result["error"]
    assert inspections == [job["id"]]
    saved = store.get_project(project["id"])
    assert saved["revision"] == project["revision"] + 1
    assert result["result"]["stages"].count("inspect") == 1
    assert result["result"]["stages"][-1] == "inspect"
    assert result["result"]["remaining_gaps"] == 1
    assert len(result["result"]["failed_slots"]) == 1
    requested = set(result["result"]["requested_slot_ids"])
    assert result["result"]["selected_count"] == len(requested) - 1
    assert result["result"]["footage_inspection"] == diagnostic
    assert saved["document"]["analysis"]["draft"]["footage_inspection"] == diagnostic
    assert saved["document"]["analysis"]["draft"]["selected_count"] == len(requested) - 1
    if kind == "whole":
        timing = result["result"]["timing_plan"]
        assert timing["final_end_frames"][0] == timing["end_frames"][0] + 1
        assert timing["final_timing"]["shortest_seconds"] < timing["nominal_timing"]["shortest_seconds"]


@pytest.mark.parametrize("kind", ["short-fixed", "long-fixed"])
def test_inspection_cannot_rewrite_protected_fixed_footage(config, store, db, tmp_path, monkeypatch, kind):
    config.lab.footage_inspection = True
    project, _ = _fixture(kind, store, db, tmp_path, monkeypatch)
    _collect(monkeypatch)

    def inspect(document, *_args, **_kwargs):
        document["clips"][0]["title"] = "Unauthorized replacement of a protected scene"
        return document, {"status": "completed"}

    _review(monkeypatch, inspect)
    result, _ = _run(store, config, db, project)
    assert result["status"] == "failed"
    assert "existing scene outside the requested shot" in result["error"]
    assert store.get_project(project["id"]) == project


@pytest.mark.parametrize("failure", ["error", "cancel"])
def test_inspection_error_or_cancellation_leaves_saved_revision_untouched(config, store, db, tmp_path, monkeypatch, failure):
    config.lab.footage_inspection = True
    project, _ = _fixture("whole", store, db, tmp_path, monkeypatch)
    _collect(monkeypatch)

    def inspect(document, _config, _db, _progress, job_id, *, contexts, cancelled):
        if failure == "cancel":
            store.cancel(job_id)
            assert cancelled()
            raise JobCancelled("Inspection cancelled")
        raise ValueError("Inspection produced an invalid source window")

    _review(monkeypatch, inspect)
    result, job = _run(store, config, db, project, mode="regenerate")
    assert result["status"] == ("cancelled" if failure == "cancel" else "failed")
    assert store.get_project(project["id"]) == project
    assert (config.paths.assets_dir / "lab" / "requests" / f"{job['id']}-inspection-input.json").is_file()


@pytest.mark.parametrize("kind", ["short-fixed", "long-fixed", "whole"])
def test_disabled_inspection_preserves_fill_interface_and_has_no_stage_or_artifact(config, store, db, tmp_path, monkeypatch, kind):
    assert config.lab.footage_inspection is False
    project, _ = _fixture(kind, store, db, tmp_path, monkeypatch)
    original_fill = music_planner.fill_timeline

    def fill(document, config, db, progress, job_id, slot_ids=None, **options):
        assert "review_contexts" not in options
        return original_fill(document, config, db, progress, job_id, slot_ids, **options)

    monkeypatch.setattr(music_planner, "fill_timeline", fill)
    _review(monkeypatch, lambda *_args, **_kwargs: pytest.fail("Disabled inspection must not run"))
    result, job = _run(store, config, db, project, mode="regenerate" if kind == "whole" else "fill")
    assert result["status"] == "completed", result["error"]
    assert "inspect" not in result["result"]["stages"]
    assert "footage_inspection" not in result["result"]
    assert not (config.paths.assets_dir / "lab" / "requests" / f"{job['id']}-inspection-input.json").exists()


def test_rejected_inspected_replacement_restores_original_scene(config, store, db, tmp_path, monkeypatch):
    config.lab.footage_inspection = True
    project = short_project(store, db, tmp_path, filled=(0,))
    short_stages(monkeypatch, store, project)
    _collect(monkeypatch)
    original_slot = project["document"]["music_timeline"]["slots"][0]
    original_clip = next(clip for clip in project["document"]["clips"] if clip["id"] == original_slot["clip_id"])

    def inspect(document, *_args, **_kwargs):
        result = deepcopy(document)
        slot = result["music_timeline"]["slots"][0]
        result["clips"] = [clip for clip in result["clips"] if clip["id"] != slot["clip_id"]]
        slot.update(clip_id=None, search_error="The replacement does not show the required gesture")
        return result, {"status": "completed", "targets": [{"slot_id": slot["id"], "reason": slot["search_error"]}]}

    _review(monkeypatch, inspect)
    result, _ = _run(store, config, db, project, mode="improve", slot_ids=[original_slot["id"]])
    assert result["status"] == "completed", result["error"]
    saved = store.get_project(project["id"])["document"]
    assert saved["music_timeline"]["slots"][0]["clip_id"] == original_clip["id"]
    assert next(clip for clip in saved["clips"] if clip["id"] == original_clip["id"]) == original_clip
    assert result["result"]["selected_count"] == 0
    assert "original shot is kept" in result["result"]["message"]
    assert "was not inspected" in result["result"]["footage_inspection"]["targets"][0]["reason"]
