"""A single bounded generation job owns all stages and one final revision."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
import pytest

from pipeline.lab import direction_planner, generation, music, music_planner, timing_planner
from pipeline.lab.limits import MAX_SAVED_CLIPS
from pipeline.lab.models import ClipSelection, JobRequest, MusicDirection, ProjectDocument
from pipeline.lab.worker import execute_job
from pipeline.tests.selection_helpers import selector_response
from pipeline.tests.test_lab import db, store  # noqa: F401
from pipeline.tests.test_lab_music import _audio, _interpretation


def _timeline(document, count=3):
    start, end = document["passage"]["start"], document["passage"]["end"]
    document["music_timeline"] = {"track_id": document["track"]["id"], "passage": dict(document["passage"]),
        "slots": [{"id": f"slot-{index}", "start": start + (end-start)*index/count,
                   "end": start + (end-start)*(index+1)/count, "section_index": 0}
                  for index in range(count)]}
    return ProjectDocument.model_validate(document).model_dump(mode="json")


def _current_analysis(document):
    return {**_interpretation(document["passage"]["start"], document["passage"]["end"]),
            "provenance": {"track": document["track"]["id"], "passage": dict(document["passage"])}}


def _project(store, db, tmp_path, *, current=True, timeline=True, filled=(), user=(), locked=(), count=3):
    audio = tmp_path / "music.wav"; _audio(audio)
    identity = music.content_hash(audio)
    store.add_track(identity, "Music", 6, audio)
    video = tmp_path / "film.mp4"; video.touch()
    db.open_table("films").add([{"film_id": "film", "title": "Film", "path": str(video), "duration": 100., "fps": 24.}])
    document = ProjectDocument(track={"id": identity, "name": "Music", "duration": 6},
                               passage={"start": 0, "end": 6}, brief="An uncertain opening becomes hopeful").model_dump(mode="json")
    if current:
        document["analysis"] = _current_analysis(document)
    if timeline:
        document = _timeline(document, count)
        for index, slot in enumerate(document["music_timeline"]["slots"]):
            if index in filled:
                clip = ClipSelection(id=f"original-{index}", film_id="film", unit_id=f"unit-{index}",
                                     title=f"Existing image {index}", source_start=10.,
                                     source_end=10. + slot["end"] - slot["start"], locked=index in locked).model_dump(mode="json")
                document["clips"].append(clip); slot["clip_id"] = clip["id"]
            if index in user:
                slot["direction"] = MusicDirection(query=f"User image {index}").model_dump(mode="json")
                slot["direction_source"] = "user"
    project = store.create_project("Generation", "music-sketch")
    return store.update_project(project["id"], 1, document)


def _stages(monkeypatch, store, project, *, callback=None, failed_stage=None):
    calls = []
    def called(stage, job, document):
        calls.append({"stage": stage, "document": deepcopy(document), "snapshot": deepcopy(job.get("snapshot", {}))})
        # Every model stage sees the frozen revision; no intermediate project save.
        if callback:
            callback(stage, {**job, "id": job["id"].split("-part-")[0]}, document)
        else:
            assert store.get_project(project["id"])["revision"] == project["revision"]
        if failed_stage == stage:
            raise RuntimeError(f"{stage} failed once")
    def analyze(job, _config, _db, _progress):
        document = deepcopy(job["document"])
        document["analysis"] = _current_analysis(document)
        if not document.get("music_timeline"):
            document = _timeline(document)
        called("analyze", job, document)
        return document
    def plan(job, _config, _db, _progress):
        document = deepcopy(job["document"])
        for slot in document["music_timeline"]["slots"]:
            if slot["id"] in job["snapshot"]["slot_ids"]:
                slot["direction"] = MusicDirection(query=f"Planned {slot.get('feedback') or slot['id']}").model_dump(mode="json")
                slot["direction_source"] = "ai"; slot["needs_direction"] = False
        called("plan", job, document)
        return document
    def fill(document, _config, _db, _progress, job_id, slot_ids=None, **options):
        document = deepcopy(document)
        for slot in document["music_timeline"]["slots"]:
            if slot["id"] not in slot_ids:
                continue
            clip = ClipSelection(id=f"generated-{slot['id']}", film_id="film", unit_id="new-unit", title="Chosen source",
                                 source_start=20., source_end=20. + slot["end"]-slot["start"]).model_dump(mode="json")
            old_index = next((i for i, old in enumerate(document["clips"]) if old["id"] == slot["clip_id"]), None)
            if old_index is None:
                document["clips"].append(clip)
            else:
                document["clips"][old_index] = clip
            slot["clip_id"] = clip["id"]; slot["search_error"] = None
        called("draft", {"id": job_id, "snapshot": {"slot_ids": slot_ids, **options}}, document)
        return document
    def timing(job, config, progress):
        document = deepcopy(job["document"])
        # Fixture timing is explicit; production timing is tested separately.
        moments = document["analysis"].get("edit_beats") or []
        document = _timeline(document, count=len(moments) or 3)
        if moments:
            for slot, moment in zip(document["music_timeline"]["slots"], moments):
                slot["start"], slot["end"] = moment["start"], moment["end"]
        called("timing", job, document)
        frames = [round((slot["end"] - document["passage"]["start"]) * document["fps"]) for slot in document["music_timeline"]["slots"]]
        document["direction_plan"] = {"timing_plan": {"contract": "fixture", "end_frames": frames,
            "notes": [], "passage": deepcopy(document["passage"]), "fps": document["fps"], "cache_reused": False}}
        return document
    monkeypatch.setattr(music, "run_music_job", analyze)
    monkeypatch.setattr(direction_planner, "run_direction_job", plan)
    monkeypatch.setattr(timing_planner, "run_timing_job", timing)
    monkeypatch.setattr(music_planner, "fill_timeline", fill)
    return calls


def _run(store, config, db, project, *, mode="fill", slot_ids=None):
    store.enqueue("generate", project["id"], project["revision"], generate={"mode": mode}, slot_ids=slot_ids)
    job = store.claim()
    return execute_job(job, config, db, store), job


def test_fresh_generate_runs_all_stages_in_one_atomic_revision(config, store, db, tmp_path, monkeypatch):
    project = _project(store, db, tmp_path, current=False, timeline=False)
    calls = _stages(monkeypatch, store, project)
    result, job = _run(store, config, db, project, mode="regenerate")
    assert result["status"] == "completed", result["error"]
    assert result["result"]["applied"] is True
    assert result["result"]["stages"] == ["analyze", "timing", "plan", "draft"]
    assert [item["stage"] for item in calls] == ["analyze", "timing", "plan", "draft"]
    assert store.get_project(project["id"])["revision"] == project["revision"] + 1
    assert len(store.revisions(project["id"])) == 3  # initial, imported fixture, one completed edit
    assert job["snapshot"]["generate"] == {"mode": "regenerate"}
    assert all("generate" not in item["snapshot"] for item in calls)
    assert all(slot["clip_id"] for slot in store.get_project(project["id"])["document"]["music_timeline"]["slots"])


@pytest.mark.parametrize("protected", ["manual-markers", "legacy-markers", "saved-directions", "legacy-clips", "explicit-empty-timeline"])
def test_fill_preserves_manual_or_legacy_work(config, store, db, tmp_path, monkeypatch, protected):
    project = _project(store, db, tmp_path, timeline=False)
    document = deepcopy(project["document"])
    if protected == "manual-markers":
        document["rhythm"] = {"markers": [], "marker_source": "user"}
    elif protected == "legacy-markers":
        document["rhythm"] = {"markers": [3.]}
    elif protected == "saved-directions":
        document["direction_plan"] = {"saved": "prior shot planning"}
    elif protected == "legacy-clips":
        document["clips"] = [ClipSelection(id="legacy", film_id="film", source_start=10, source_end=12).model_dump(mode="json")]
    elif protected == "explicit-empty-timeline":
        document = _timeline(document)
    project = store.update_project(project["id"], project["revision"], document)
    _stages(monkeypatch, store, project)
    monkeypatch.setattr(timing_planner, "run_timing_job", lambda *_: pytest.fail("Protected work entered whole-edit planning"))
    result, _ = _run(store, config, db, project)
    assert result["status"] == "completed", result["error"]
    assert "timing" not in result["result"]["stages"]
    if protected == "legacy-clips":
        assert store.get_project(project["id"])["document"]["clips"][0] == document["clips"][0]


def test_fill_preserves_user_direction_when_audio_establishes_first_timeline(config, store, db, tmp_path, monkeypatch):
    project = _project(store, db, tmp_path, current=False, timeline=False)
    def intervene(stage, _job, document):
        if stage == "analyze":
            document["music_timeline"]["slots"][0]["direction_source"] = "user"
            document["music_timeline"]["slots"][0]["direction"] = MusicDirection(query="A new user direction").model_dump(mode="json")
    calls = _stages(monkeypatch, store, project, callback=intervene)
    result, _ = _run(store, config, db, project)
    assert result["status"] == "completed", result["error"]
    assert [call["stage"] for call in calls] == ["analyze", "plan", "draft"]
    saved = store.get_project(project["id"])["document"]
    assert saved["music_timeline"]["slots"][0]["direction"]["query"] == "A new user direction"
    assert saved["music_timeline"]["slots"][0]["direction_source"] == "user"


def test_real_timing_planner_rejects_out_of_bounds_output_before_search(config, store, db, tmp_path, monkeypatch):
    project = _project(store, db, tmp_path, filled=(0, 1, 2))
    actual_timing = timing_planner.run_timing_job
    calls = _stages(monkeypatch, store, project)
    monkeypatch.setattr(timing_planner, "run_timing_job", actual_timing)
    monkeypatch.setattr(music, "_hosted_json", lambda *_a, **_k: {"end_frames": [145], "notes": []})
    result, _ = _run(store, config, db, project, mode="regenerate")
    assert result["status"] == "failed"
    assert not calls  # no listening, directions or retrieval before timing validates
    assert store.get_project(project["id"]) == project


def test_generate_reuses_analysis_and_preserves_filled_and_user_directed_slots(config, store, db, tmp_path, monkeypatch):
    project = _project(store, db, tmp_path, filled=(0,), user=(0, 1), locked=(0,))
    before = deepcopy(project["document"])
    calls = _stages(monkeypatch, store, project)
    result, _ = _run(store, config, db, project)
    assert result["status"] == "completed", result["error"]
    assert [item["stage"] for item in calls] == ["plan", "draft"]
    assert calls[0]["snapshot"]["slot_ids"] == ["slot-2"]
    assert calls[1]["snapshot"]["slot_ids"] == ["slot-1", "slot-2"]
    after = store.get_project(project["id"])["document"]
    assert after["clips"][0] == before["clips"][0]
    assert after["music_timeline"]["slots"][0] == before["music_timeline"]["slots"][0]
    assert after["music_timeline"]["slots"][1]["direction"] == before["music_timeline"]["slots"][1]["direction"]
    assert [(slot["start"], slot["end"]) for slot in after["music_timeline"]["slots"]] == [(slot["start"], slot["end"]) for slot in before["music_timeline"]["slots"]]


def test_all_user_directions_skip_planning_but_fill_gaps(config, store, db, tmp_path, monkeypatch):
    project = _project(store, db, tmp_path, user=(0, 1, 2))
    calls = _stages(monkeypatch, store, project)
    result, _ = _run(store, config, db, project)
    assert result["status"] == "completed", result["error"]
    assert [item["stage"] for item in calls] == ["draft"]


@pytest.mark.parametrize("stale", [False, "needs_direction", "capability", "duration", "editor-instruction", "empty-editor"])
def test_current_saved_recipe_skips_duplicate_planning_but_stale_plan_regenerates(config, store, db, tmp_path, monkeypatch, stale):
    from pipeline.search.capabilities import CAPABILITY_VERSION
    project = _project(store, db, tmp_path, filled=(0, 2))
    document = deepcopy(project["document"])
    slot = document["music_timeline"]["slots"][1]
    slot["direction"] = MusicDirection(query="A planned road image").model_dump(mode="json")
    slot["direction_source"] = "ai"
    slot["resolved_search"] = {"clauses": [{"kind": "text", "facet": "all", "text": "A planned road image", "reference_id": None}],
                               "references": [], "unverified_requirements": [], "capability_version": CAPABILITY_VERSION,
                               "min_duration": slot["end"]-slot["start"]}
    if stale == "needs_direction":
        slot["needs_direction"] = True
    elif stale == "capability":
        slot["resolved_search"]["capability_version"] = "old-contract"
    elif stale == "duration":
        slot["resolved_search"]["min_duration"] = 1
    elif stale in {"editor-instruction", "empty-editor"}:
        document["editor_direction"] = {"instruction": "" if stale == "empty-editor" else "Surreal blue images", "ranges": []}
    before_slots = deepcopy(document["music_timeline"]["slots"])
    project = store.update_project(project["id"], project["revision"], document)
    assert project["document"]["music_timeline"]["slots"] == before_slots
    calls = _stages(monkeypatch, store, project)
    result, _ = _run(store, config, db, project)
    assert result["status"] == "completed", result["error"]
    assert [item["stage"] for item in calls] == (["plan", "draft"] if stale else ["draft"])


def test_filled_generate_is_noop_without_audio_work_or_extra_revision(config, store, db, tmp_path, monkeypatch):
    project = _project(store, db, tmp_path, filled=(0, 1, 2), current=False)
    calls = _stages(monkeypatch, store, project)
    Path(store.get_track(project["document"]["track"]["id"])["path"]).unlink()
    result, _ = _run(store, config, db, project)
    assert result["status"] == "completed", result["error"]
    assert result["result"]["unchanged"] is True and "already filled" in result["result"]["message"]
    assert not calls
    assert store.get_project(project["id"]) == project


def test_improve_targets_one_user_direction_and_consumes_saved_feedback(config, store, db, tmp_path, monkeypatch):
    project = _project(store, db, tmp_path, filled=(0, 1, 2), user=(1,))
    document = deepcopy(project["document"])
    document["music_timeline"]["slots"][1]["feedback"] = "too_similar"
    project = store.update_project(project["id"], project["revision"], document)
    calls = _stages(monkeypatch, store, project)
    result, job = _run(store, config, db, project, mode="improve", slot_ids=["slot-1"])
    assert result["status"] == "completed", result["error"]
    assert job["snapshot"]["slot_ids"] == ["slot-1"]
    assert [item["snapshot"]["slot_ids"] for item in calls] == [["slot-1"], ["slot-1"]]
    assert "too_similar" in calls[0]["document"]["music_timeline"]["slots"][1]["direction"]["query"]
    after = store.get_project(project["id"])["document"]
    assert after["clips"][0] == project["document"]["clips"][0]
    assert after["clips"][2] == project["document"]["clips"][2]
    assert after["clips"][1]["id"] != project["document"]["clips"][1]["id"]


def test_improve_locked_clip_rejected_before_enqueue(config, store, db, tmp_path):
    project = _project(store, db, tmp_path, filled=(1,), locked=(1,))
    with pytest.raises(ValueError, match="Unlock"):
        store.enqueue("generate", project["id"], project["revision"], generate={"mode": "improve"}, slot_ids=["slot-1"])
    assert not store.project_jobs(project["id"])


def _regeneration_moments(stage, _job, document):
    if stage == "timing":
        boundaries = [0., 1.25, 3.5, 6.]
        for slot, start, end in zip(document["music_timeline"]["slots"], boundaries, boundaries[1:]):
            slot["start"], slot["end"] = start, end


def test_regenerate_rebuilds_filled_manual_edit_from_current_settings_in_one_revision(config, store, db, tmp_path, monkeypatch):
    project = _project(store, db, tmp_path, filled=(0, 1, 2), user=(0, 1, 2))
    document = deepcopy(project["document"])
    document["planner_settings"]["pacing"] = "kinetic"
    document["visual_plan"] = {"arc": "Old slow arc", "motifs": "A previous motif", "source": "ai"}
    document["direction_plan"] = {"old": "recipe"}
    # Locked clips retained in the bin must not block an unrelated new timeline.
    document["clips"].append(ClipSelection(id="saved-locked", film_id="film", source_start=40, source_end=42, locked=True).model_dump(mode="json"))
    project = store.update_project(project["id"], project["revision"], document)
    def observe(stage, job, value):
        assert store.get_project(project["id"]) == project
        assert value["planner_settings"]["pacing"] == "kinetic"
        assert value["visual_plan"] is None
        if stage == "timing":
            assert value["direction_plan"] is None
        if stage == "timing":
            assert value["clips"] == []
            assert all(slot.get("direction_source") != "user" for slot in value["music_timeline"]["slots"])
        _regeneration_moments(stage, job, value)
    calls = _stages(monkeypatch, store, project, callback=observe)
    result, job = _run(store, config, db, project, mode="regenerate")
    assert result["status"] == "completed", result["error"]
    assert result["result"]["stages"] == ["timing", "plan", "draft"]
    assert result["result"]["generation_mode"] == "regenerate"
    assert job["snapshot"]["generate"] == {"mode": "regenerate"}
    assert [item["stage"] for item in calls] == ["timing", "plan", "draft"]
    assert calls[-1]["snapshot"]["timing_scope"]["contract"] == "bounded-source-aware-first-edit-v1"
    saved = store.get_project(project["id"])
    assert saved["revision"] == project["revision"] + 1
    assert saved["document"]["clips"][:4] == project["document"]["clips"]
    slots = saved["document"]["music_timeline"]["slots"]
    assert [(slot["start"], slot["end"]) for slot in slots] == [(0, 1.25), (1.25, 3.5), (3.5, 6)]
    assert all(slot["clip_id"].startswith("generated-") and slot["direction_source"] == "ai" for slot in slots)
    assert store.restore(project["id"], saved["revision"], project["revision"])["document"] == project["document"]


@pytest.mark.parametrize("stage", ["timing", "plan", "draft"])
@pytest.mark.parametrize("outcome", ["cancel", "fail"])
def test_regenerate_failure_or_cancel_keeps_complete_original_edit(config, store, db, tmp_path, monkeypatch, stage, outcome):
    project = _project(store, db, tmp_path, filled=(0, 1, 2), user=(1,), current=stage != "analyze")
    def observe(name, job, document):
        _regeneration_moments(name, job, document)
        if name == stage and outcome == "cancel":
            store.cancel(job["id"])
    calls = _stages(monkeypatch, store, project, callback=observe, failed_stage=stage if outcome == "fail" else None)
    result, _ = _run(store, config, db, project, mode="regenerate")
    assert result["status"] == ("cancelled" if outcome == "cancel" else "failed"), result["error"]
    assert calls[-1]["stage"] == stage
    assert store.get_project(project["id"]) == project
    assert len(store.revisions(project["id"])) == 2


def test_regenerate_rejects_placed_locks_before_enqueue(config, store, db, tmp_path):
    project = _project(store, db, tmp_path, filled=(0, 1, 2), locked=(1,))
    with pytest.raises(ValueError, match="Unlock placed clips"):
        store.enqueue("generate", project["id"], project["revision"], generate={"mode": "regenerate"})
    assert not store.project_jobs(project["id"])


def test_regenerate_keeps_user_visual_plan_and_stale_proposal_does_not_overwrite(config, store, db, tmp_path, monkeypatch):
    project = _project(store, db, tmp_path, filled=(0, 1, 2))
    document = deepcopy(project["document"])
    document["visual_plan"] = {"arc": "A chosen arc", "motifs": "Windows", "source": "user"}
    project = store.update_project(project["id"], project["revision"], document)
    def observe(stage, job, value):
        assert value["visual_plan"] == document["visual_plan"]
        _regeneration_moments(stage, job, value)
        if stage == "plan":
            store.update_project(project["id"], project["revision"], {**document, "brief": "New user work"})
    _stages(monkeypatch, store, project, callback=observe)
    result, _ = _run(store, config, db, project, mode="regenerate")
    assert result["status"] == "completed" and result["result"]["applied"] is False
    assert len(result["result"]["document"]["music_timeline"]["slots"]) == 3
    saved = store.get_project(project["id"])["document"]
    assert saved["brief"] == "New user work" and saved["music_timeline"] == document["music_timeline"]


def test_regenerate_can_plan_visual_cuts_without_audio_editorial_moments(config, store, db, tmp_path, monkeypatch):
    project = _project(store, db, tmp_path, filled=(0, 1, 2))
    calls = _stages(monkeypatch, store, project)
    result, _ = _run(store, config, db, project, mode="regenerate")
    assert result["status"] == "completed", result["error"]
    assert [call["stage"] for call in calls] == ["timing", "plan", "draft"]
    assert store.get_project(project["id"])["document"]["clips"][:3] == project["document"]["clips"]


def test_regenerate_saved_clip_capacity_checked_before_planning_or_search(config, store, db, tmp_path, monkeypatch):
    project = _project(store, db, tmp_path, filled=(0, 1, 2))
    document = deepcopy(project["document"])
    document["clips"].extend(ClipSelection(id=f"saved-{index}", film_id="film", source_start=40, source_end=42).model_dump(mode="json")
                             for index in range(MAX_SAVED_CLIPS - len(document["clips"])))
    project = store.update_project(project["id"], project["revision"], document)
    calls = _stages(monkeypatch, store, project, callback=_regeneration_moments)
    result, _ = _run(store, config, db, project, mode="regenerate")
    assert result["status"] == "failed" and "Clear unused Saved clips" in result["error"]
    assert not calls
    assert store.get_project(project["id"]) == project


@pytest.mark.parametrize("stage", ["timing", "plan", "draft"])
def test_stage_failure_never_saves_partial_project_or_retries(config, store, db, tmp_path, monkeypatch, stage):
    project = _project(store, db, tmp_path, current=False, timeline=False)
    calls = _stages(monkeypatch, store, project, failed_stage=stage)
    result, _ = _run(store, config, db, project, mode="regenerate")
    assert result["status"] == "failed" and result["error"] == f"{stage} failed once"
    assert [item["stage"] for item in calls].count(stage) == 1
    assert store.get_project(project["id"]) == project
    assert len(store.revisions(project["id"])) == 2


@pytest.mark.parametrize("stage", ["analyze", "timing", "plan", "draft"])
def test_cancel_between_stages_prevents_later_work_and_revision(config, store, db, tmp_path, monkeypatch, stage):
    project = _project(store, db, tmp_path, current=False, timeline=False)
    def cancel(name, job, _document):
        if name == stage:
            store.cancel(job["id"])
    calls = _stages(monkeypatch, store, project, callback=cancel)
    result, _ = _run(store, config, db, project, mode="regenerate")
    assert result["status"] == "cancelled"
    assert calls[-1]["stage"] == stage
    assert store.get_project(project["id"]) == project


@pytest.mark.parametrize("message", ["Using the current music interpretation", "Using your saved shot directions"])
def test_cancel_at_reused_stage_boundary_stops_before_model_or_search(config, store, db, tmp_path, monkeypatch, message):
    project = _project(store, db, tmp_path, user=(0, 1, 2))
    calls = _stages(monkeypatch, store, project)
    original_progress = store.progress
    def cancel_at_boundary(identity, text):
        original_progress(identity, text)
        if text == message:
            store.cancel(identity)
    monkeypatch.setattr(store, "progress", cancel_at_boundary)
    result, _ = _run(store, config, db, project)
    assert result["status"] == "cancelled"
    assert not calls
    assert store.get_project(project["id"]) == project


def test_stale_result_retains_whole_proposal_without_overwriting_user(config, store, db, tmp_path, monkeypatch):
    project = _project(store, db, tmp_path)
    def edit_during_plan(stage, _job, _document):
        if stage == "plan":
            store.update_project(project["id"], project["revision"], {**project["document"], "brief": "New user work"})
    _stages(monkeypatch, store, project, callback=edit_during_plan)
    result, _ = _run(store, config, db, project)
    assert result["status"] == "completed" and result["result"]["applied"] is False
    assert all(slot["clip_id"] for slot in result["result"]["document"]["music_timeline"]["slots"])
    assert store.get_project(project["id"])["document"]["brief"] == "New user work"
    assert store.get_project(project["id"])["revision"] == project["revision"] + 1


def test_audio_refresh_restores_protected_search_evidence_and_existing_reason(config, store, db, tmp_path, monkeypatch):
    project = _project(store, db, tmp_path, current=False, filled=(0,), user=(0, 1))
    document = deepcopy(project["document"])
    slot = document["music_timeline"]["slots"][0]
    slot["reason"] = "Keep the reviewed source choice"
    slot["alternatives"] = [{"clip": document["clips"][0], "film_title": "Film", "reason": "Saved alternate"}]
    slot["search_evidence"] = {"rank": 1, "matched_text": "Existing visual evidence"}
    project = store.update_project(project["id"], project["revision"], document)
    def clear_derived(stage, _job, value):
        if stage == "analyze":
            for item in value["music_timeline"]["slots"]:
                item.update(search_evidence=None, resolved_search=None, alternatives=[], reason=None, search_error=None)
    _stages(monkeypatch, store, project, callback=clear_derived)
    result, _ = _run(store, config, db, project)
    assert result["status"] == "completed", result["error"]
    assert store.get_project(project["id"])["document"]["music_timeline"]["slots"][0] == project["document"]["music_timeline"]["slots"][0]


def test_no_fitting_improvement_reports_original_kept(config, store, db, tmp_path, monkeypatch):
    project = _project(store, db, tmp_path, filled=(0, 1, 2))
    _stages(monkeypatch, store, project)
    def no_match(document, _config, _db, _progress, _identity, slot_ids):
        document["music_timeline"]["slots"][1]["search_error"] = "No scene fits this duration"
        return document
    monkeypatch.setattr(music_planner, "fill_timeline", no_match)
    result, _ = _run(store, config, db, project, mode="improve", slot_ids=["slot-1"])
    assert result["status"] == "completed", result["error"]
    assert "original shot is kept" in result["result"]["message"]
    assert result["result"]["failed_slots"] == [{"slot_id": "slot-1", "error": "No scene fits this duration"}]
    assert store.get_project(project["id"])["document"]["clips"] == project["document"]["clips"]


def test_stage_cannot_retime_an_existing_edit(config, store, db, tmp_path, monkeypatch):
    project = _project(store, db, tmp_path)
    def move(stage, _job, document):
        if stage == "plan":
            document["music_timeline"]["slots"][0]["end"] += .25
            document["music_timeline"]["slots"][1]["start"] += .25
    calls = _stages(monkeypatch, store, project, callback=move)
    result, _ = _run(store, config, db, project)
    assert result["status"] == "failed" and "cut positions" in result["error"]
    assert [item["stage"] for item in calls] == ["plan"]
    assert store.get_project(project["id"]) == project


def _starter_project(store, db, tmp_path):
    from pipeline.lab.rhythm_timing import prepare_timing
    project = _project(store, db, tmp_path, current=False, timeline=False)
    document = deepcopy(project["document"])
    document["rhythm"] = {"provenance": {"track": document["track"]["id"], "passage": document["passage"]},
                           "beats": list(range(6)), "downbeats": [0., 2., 4.]}
    prepare_timing(document)
    return store.update_project(project["id"], project["revision"], document)


def test_missing_editorial_moments_still_allows_whole_text_planner_to_choose_first_cuts(config, store, db, tmp_path, monkeypatch):
    project = _starter_project(store, db, tmp_path)
    assert project["document"]["music_timeline"]["provisional_timing"]
    _stages(monkeypatch, store, project)
    progress = []
    prior = store.progress
    def record(identity, message):
        progress.append(message); prior(identity, message)
    monkeypatch.setattr(store, "progress", record)
    result, _ = _run(store, config, db, project, mode="regenerate")
    assert result["status"] == "completed", result["error"]
    assert "timing" in result["result"]["stages"]
    assert "Planning musical pacing across the complete passage" in progress
    after = store.get_project(project["id"])["document"]["music_timeline"]
    assert after["provisional_timing"] is None
    assert [(slot["start"], slot["end"]) for slot in after["slots"]] == [(0, 2), (2, 4), (4, 6)]


def test_whole_first_timing_does_not_permit_unoffered_cuts_during_selection(config, store, db, tmp_path, monkeypatch):
    project = _starter_project(store, db, tmp_path)
    def intervene(stage, _job, document):
        if stage == "analyze":
            boundaries = [0., .75, 2.25, 6.]
            document["analysis"]["edit_beats"] = [{"start": a, "end": b, "query": f"image-{index}", "search_facet": "all",
                "purpose": "Develop a visual idea", "music_cue": "Supplied musical context", "timing_note": "Let this idea read"}
                for index, (a, b) in enumerate(zip(boundaries, boundaries[1:]))]
        elif stage == "draft":
            slots = document["music_timeline"]["slots"]
            slots[0]["end"] += .123; slots[1]["start"] += .123
            for slot in slots[:2]:
                clip = next(clip for clip in document["clips"] if clip["id"] == slot["clip_id"])
                clip["source_end"] = clip["source_start"] + slot["end"] - slot["start"]
    calls = _stages(monkeypatch, store, project, callback=intervene)
    result, _ = _run(store, config, db, project, mode="regenerate")
    assert result["status"] == "failed" and "unoffered cut" in result["error"]
    assert [item["stage"] for item in calls] == ["analyze", "timing", "plan", "draft"]
    assert store.get_project(project["id"]) == project


@pytest.mark.parametrize("outcome", ["apply", "cancel", "illegal-cut", "unoffered-source", "wrong-slot-source", "wrong-source-time", "impossible-sources"])
@pytest.mark.parametrize("existing_edit", [False, True])
def test_source_aware_whole_generation_is_one_grounded_transaction(config, store, db, tmp_path, monkeypatch, outcome, existing_edit):
    """Real retrieval/selection assembly changes a cut only after seeing sources."""
    import json
    project = (_starter_project(store, db, tmp_path) if not existing_edit else
               _project(store, db, tmp_path, filled=(0,), count=1))
    if existing_edit:
        document = deepcopy(project["document"])
        document["rhythm"] = {"provenance": {"track": document["track"]["id"], "passage": document["passage"]},
                              "beats": list(range(6)), "downbeats": [0., 2., 4.]}
        project = store.update_project(project["id"], project["revision"], document)
    actual_fill = music_planner.fill_timeline
    def moments(stage, _job, document):
        if stage == "timing":
            document["music_timeline"]["slots"] = [
                {"id": f"slot-{index}", "start": a, "end": b, "section_index": 0,
                 "clip_id": None, "direction": None, "needs_direction": True}
                for index, (a, b) in enumerate([(0., 3.), (3., 6.)])]
    _stages(monkeypatch, store, project, callback=moments)
    monkeypatch.setattr(music_planner, "fill_timeline", actual_fill)
    # Source and index I/O are bounded stubs; the worker still validates the
    # retained film and source ranges before its actual atomic revision write.
    monkeypatch.setattr(music_planner, "_hydrate_metadata", lambda *_: None)
    rows = [
        {"unit_id": "short", "film_id": "film", "t_start": 20., "t_end": 22., "caption": "A concise visible image"},
        {"unit_id": "long", "film_id": "film", "t_start": 30., "t_end": 32. if outcome == "impossible-sources" else 38., "caption": "A sustained response"}]
    monkeypatch.setattr(music, "retrieve_edit_candidates", lambda query, *_:
                        rows[:1] if outcome == "wrong-slot-source" and query.endswith("slot-0") else rows)
    calls = []
    def select(_config, prompt, *_args, **_kwargs):
        payload = json.loads(prompt.split("\n", 1)[1])
        assert store.get_project(project["id"]) == project
        assert payload["sources"]["c0"]["t_end"] - payload["sources"]["c0"]["t_start"] == 2
        assert any(candidate["source"] == "c0" for candidate in payload["slots"][0]["candidates"])
        calls.append(payload)
        if outcome == "cancel":
            store.cancel(store.project_jobs(project["id"])[0]["id"])
        response = selector_response([
            {"slot": 0, "candidate_id": "short", "source_position": 0., "preferred_end_frame": 47 if outcome == "illegal-cut" else 48, "reason": "Use the concise image before a sustained response"},
            {"slot": 1, "candidate_id": "long", "source_position": 0., "preferred_end_frame": 144, "reason": "Let the available sustained image close the passage"}], ["short", "long"])
        if outcome == "unoffered-source":
            response["choices"]["shot_0"]["source"] = {"c999": 0.}
        elif outcome == "wrong-slot-source":
            assert [candidate["source"] for candidate in payload["slots"][0]["candidates"]] == ["c0"]
            assert "c1" in payload["sources"]
            response["choices"]["shot_0"]["source"] = {"c1": 0.}
        elif outcome == "wrong-source-time":
            response["choices"]["shot_0"]["source"] = {"c0": 30.}
        return response
    monkeypatch.setattr(music, "_hosted_json", select)
    result, job = _run(store, config, db, project, mode="regenerate")
    assert len(calls) == 1
    if outcome != "apply":
        assert result["status"] == ("cancelled" if outcome == "cancel" else "failed"), result["error"]
        assert store.get_project(project["id"]) == project
        if outcome == "illegal-cut":
            assert "unavailable cut for shot 1" in result["error"]
            assert "saved edit is unchanged" in result["error"]
        if outcome == "impossible-sources":
            assert "cannot fit the offered cuts" in result["error"]
        if outcome in {"unoffered-source", "wrong-slot-source", "wrong-source-time"}:
            expected = "returned an invalid scene choice" if outcome == "wrong-source-time" else "chose an unavailable scene"
            assert f"{expected} for shot 1" in result["error"]
            assert "saved edit is unchanged" in result["error"]
            manifest_path = config.paths.assets_dir / "lab" / "requests" / f"{job['id']}-part-01-offers.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            assert manifest["contract"] == "scoped-source-timing-preferences-v1"
            assert manifest["alias_to_source"] == {"c0": "short", "c1": "long"}
            assert manifest["shots"]["shot_0"]["candidate_ids"] == (["short"] if outcome == "wrong-slot-source" else ["short", "long"])
            assert manifest["sources"]["short"] == {"film_id": "film", "t_start": 20., "t_end": 22.}
            assert manifest["sources"]["long"] == {"film_id": "film", "t_start": 30., "t_end": 38.}
        return
    assert result["status"] == "completed", result["error"]
    saved = store.get_project(project["id"])
    assert saved["revision"] == project["revision"] + 1
    assert [(slot["start"], slot["end"]) for slot in saved["document"]["music_timeline"]["slots"]] == [(0, 2), (2, 6)]
    assert result["result"]["timing_mode"] == "source-aware" and result["result"]["selected_count"] == 2
    if existing_edit:
        assert saved["document"]["clips"][:1] == project["document"]["clips"]
    assert store.restore(project["id"], saved["revision"], project["revision"])["document"] == project["document"]


def test_short_fixed_layout_over_32_uses_bounded_groups(config, store, db, tmp_path, monkeypatch):
    project = _project(store, db, tmp_path, count=33)
    calls = _stages(monkeypatch, store, project)
    result, _ = _run(store, config, db, project)
    assert result["status"] == "completed", result["error"]
    assert "timing" not in result["result"]["stages"]
    assert [len(call["snapshot"]["slot_ids"]) for call in calls if call["stage"] == "draft"] == [32, 1]
    after = store.get_project(project["id"])["document"]
    assert [(s["id"], s["start"], s["end"]) for s in after["music_timeline"]["slots"]] == [
        (s["id"], s["start"], s["end"]) for s in project["document"]["music_timeline"]["slots"]]


@pytest.mark.parametrize("payload", [
    {"kind": "generate", "generate": {"mode": "improve"}},
    {"kind": "generate", "generate": {"mode": "improve"}, "slot_ids": ["a", "b"]},
    {"kind": "generate", "slot_ids": ["a"]},
    {"kind": "generate", "generate": {"mode": "regenerate"}, "slot_ids": ["a"]},
    {"kind": "draft", "generate": {"mode": "fill"}},
    {"kind": "generate", "generate": {"mode": "fill", "feedback": "unsupported request field"}},
    {"kind": "generate", "replan_timing": True},
])
def test_request_has_one_target_authority_and_no_implicit_retiming(payload):
    with pytest.raises(ValueError):
        JobRequest(base_revision=1, **payload)


def test_generate_api_freezes_default_mode_and_rejects_wrong_experiment(config, store, db, tmp_path):
    from pipeline.api.main import app
    project = _project(store, db, tmp_path, current=False, timeline=False)
    with patch("pipeline.api.main.load_config", return_value=config), patch("pipeline.api.main.open_db", return_value=db), patch("pipeline.api.main.ensure_search_indexes"), TestClient(app) as client:
        response = client.post(f"/lab/projects/{project['id']}/jobs", json={"kind": "generate", "base_revision": project["revision"]})
        assert response.status_code == 200, response.text
        assert store.get_job(response.json()["id"], private=True)["snapshot"]["generate"] == {"mode": "fill"}
        store.cancel(response.json()["id"])
        response = client.post(f"/lab/projects/{project['id']}/jobs", json={"kind": "generate", "base_revision": project["revision"], "generate": {"mode": "regenerate"}})
        assert response.status_code == 200, response.text
        assert store.get_job(response.json()["id"], private=True)["snapshot"]["generate"] == {"mode": "regenerate"}
        rhymes = store.create_project("Rhymes", "visual-rhymes")
        response = client.post(f"/lab/projects/{rhymes['id']}/jobs", json={"kind": "generate", "base_revision": 1})
        assert response.status_code == 422 and "AI Music Video" in response.text
