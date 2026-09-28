"""Replay a measured timing failure without user media or live project writes.

The retained interpretation is model output, not a creative-quality reference.
These tests establish that its musical evidence can inform a paced first edit
while measured guides and deliberately saved timing keep separate authority.
"""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from pipeline.lab import direction_planner, music, music_planner
from pipeline.lab.models import ClipSelection, MusicDirection, ProjectDocument
from pipeline.lab.rhythm_timing import prepare_timing
from pipeline.lab.timeline import provisional_timing_eligible
from pipeline.lab.worker import execute_job
from pipeline.tests.test_lab import db, store  # noqa: F401


@pytest.fixture
def record():
    path = Path(__file__).with_name("data") / "steve_lacy_first_edit_timing.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _durations(document):
    return [slot["end"] - slot["start"] for slot in document["music_timeline"]["slots"]]


def _starter(record):
    document = deepcopy(record["document"])
    document["music_timeline"] = None
    prepare_timing(document)
    return ProjectDocument.model_validate(document).model_dump(mode="json")


def _project(document, store, db, tmp_path, monkeypatch):
    # Do not copy a user's original song into the test suite. The measured
    # evidence keeps its actual identity; only retained-file I/O is stubbed.
    track = document["track"]
    audio = tmp_path / "retained-audio-io-stub.mp3"
    audio.write_bytes(b"Original audio is intentionally absent from this fixture")
    store.add_track(track["id"], track["name"], track["duration"], audio)
    original_hash = music.content_hash
    monkeypatch.setattr(music, "content_hash", lambda path: track["id"] if Path(path) == audio else original_hash(path))
    film = tmp_path / "scene-selection-io-stub.mp4"
    film.touch()
    db.open_table("films").add([{"film_id": "film", "title": "Selection stub", "path": str(film), "duration": 100., "fps": 24.}])
    project = store.create_project("Recorded first-edit timing regression", "music-sketch")
    return store.update_project(project["id"], project["revision"], document)


def _local_stages(record, monkeypatch, store, project, *, cancel_after_plan=False):
    """Keep Generate and Analyze real; replace decoder/model/search boundaries."""
    calls = []

    def planned_sequence(_config, prompt, schema, **kwargs):
        assert kwargs["operation"] == "timing" and "end_frames" in schema["properties"]
        payload = json.loads(prompt.split("\n", 1)[1])
        assert "edit_beats" not in payload["music"]
        assert payload["max_positions"] == 300
        calls.append("timing")
        return {"end_frames": [96, 240, 360, 552, 720], "notes": [
            {"start_frame": 0, "end_frame": 720, "reason": "Editorial choice: varied visual reading times",
             "evidence_ids": []}]}

    def decode(_source, _output, start, end):
        assert {"start": start, "end": end} == record["document"]["passage"]
        calls.append("decode")
        return [], 22050

    def measured_rhythm(_audio, _signal, _rate, track_id, passage, _config):
        assert track_id == record["document"]["track"]["id"]
        assert passage == record["document"]["passage"]
        return deepcopy(record["document"]["rhythm"])

    def retained_interpretation(_config, track_id, passage, _audio, _brief, _job_id, _progress, _evidence):
        assert track_id == record["record"]["track_sha256"]
        music.validate_interpretation(record["interpretation"], passage)
        calls.append("interpretation")
        return deepcopy(record["interpretation"])

    def plan(job, _config, _db, _progress):
        document = deepcopy(job["document"])
        assert store.get_project(project["id"]) == project
        for slot in document["music_timeline"]["slots"]:
            if slot["id"] in job["snapshot"]["slot_ids"]:
                slot["direction"] = slot.get("direction") or MusicDirection(query="An expressive visible detail").model_dump(mode="json")
                slot["direction_source"] = "ai"
                slot["needs_direction"] = False
        calls.append("plan")
        if cancel_after_plan:
            store.cancel(job["id"].split("-part-")[0])
        return document

    def fill(document, _config, _db, _progress, _job_id, slot_ids=None, *, timing_scope=None, **_options):
        assert store.get_project(project["id"]) == project
        if "timing" in calls:
            assert timing_scope["contract"] == "bounded-source-aware-first-edit-v1"
        else:
            assert timing_scope is None
        document = deepcopy(document)
        for slot in document["music_timeline"]["slots"]:
            if slot["id"] not in slot_ids:
                continue
            clip = ClipSelection(id=f"selection-{slot['id']}", film_id="film", unit_id="stub-unit",
                                 source_start=20, source_end=20 + slot["end"] - slot["start"]).model_dump(mode="json")
            document["clips"].append(clip)
            slot["clip_id"] = clip["id"]
        calls.append("fill")
        return document

    monkeypatch.setattr(music, "_hosted_json", planned_sequence)
    monkeypatch.setattr(music, "_decode_audio", decode)
    monkeypatch.setattr(music, "local_rhythm", measured_rhythm)
    monkeypatch.setattr(music, "interpret_audio", retained_interpretation)
    from pipeline.search.capabilities import search_capabilities
    monkeypatch.setattr(direction_planner, "search_capabilities", lambda config, _db: search_capabilities(config, object()))
    monkeypatch.setattr(direction_planner, "run_direction_job", plan)
    monkeypatch.setattr(music_planner, "fill_timeline", fill)
    return calls


def _generate(project, store, config, db, *, mode="fill"):
    store.enqueue("generate", project["id"], project["revision"], generate={"mode": mode})
    job = store.claim()
    return execute_job(job, config, db, store)


def test_actual_beat_record_reproduces_two_long_starter_holds(record):
    document = _starter(record)
    rhythm = document["rhythm"]
    assert len(rhythm["beats"]) == 36
    assert len(rhythm["downbeats"]) == 9
    assert _durations(document) == pytest.approx([14, 16])
    assert _durations(document) == pytest.approx(_durations(record["document"]))
    assert rhythm["timing_suggestions"] == record["document"]["rhythm"]["timing_suggestions"]
    assert provisional_timing_eligible(document)


@pytest.mark.parametrize("reuse_analysis", [False, True])
def test_first_generate_plans_visual_shots_from_recorded_music_in_one_revision(record, config, store, db, tmp_path, monkeypatch, reuse_analysis):
    document = _starter(record)
    if reuse_analysis:
        document["analysis"] = deepcopy(record["interpretation"])
    project = _project(document, store, db, tmp_path, monkeypatch)
    calls = _local_stages(record, monkeypatch, store, project)

    result = _generate(project, store, config, db, mode="regenerate")

    assert result["status"] == "completed", result["error"]
    assert result["result"]["stages"] == ([] if reuse_analysis else ["analyze"]) + ["timing", "plan", "draft"]
    assert calls == ([] if reuse_analysis else ["decode", "interpretation"]) + ["timing", "plan", "fill"]
    after = store.get_project(project["id"])
    assert _durations(after["document"]) == pytest.approx([4, 6, 5, 8, 7])
    assert after["document"]["music_timeline"]["provisional_timing"] is None
    assert all(slot["clip_id"] for slot in after["document"]["music_timeline"]["slots"])
    for key in ("beats", "downbeats", "intensity"):
        assert after["document"]["rhythm"][key] == record["document"]["rhythm"][key]
    assert after["revision"] == project["revision"] + 1
    assert len(store.revisions(project["id"])) == 3
    restored = store.restore(project["id"], after["revision"], project["revision"])
    assert restored["document"] == project["document"]
    assert _durations(restored["document"]) == pytest.approx([14, 16])


@pytest.mark.parametrize("protected", ["starter", "legacy", "moved-cut", "manual-markers", "written-direction"])
def test_fill_keeps_every_cut_including_untouched_starter_timing(record, config, store, db, tmp_path, monkeypatch, protected):
    document = deepcopy(record["document"]) if protected == "legacy" else _starter(record)
    if protected == "moved-cut":
        slots = document["music_timeline"]["slots"]
        slots[0]["end"] += 1
        slots[1]["start"] += 1
    elif protected == "manual-markers":
        document["rhythm"].update(marker_source="user", markers=[22.94])
    elif protected == "written-direction":
        slot = document["music_timeline"]["slots"][0]
        slot["direction"] = MusicDirection(query="Keep this deliberate long opening").model_dump(mode="json")
        slot["direction_source"] = "user"
    assert provisional_timing_eligible(document) is (protected == "starter")
    project = _project(document, store, db, tmp_path, monkeypatch)
    if protected == "legacy":
        assert project["document"]["music_timeline"]["provisional_timing"] is None
    _local_stages(record, monkeypatch, store, project)

    result = _generate(project, store, config, db)

    assert result["status"] == "completed", result["error"]
    assert "timing" not in result["result"]["stages"]
    after = store.get_project(project["id"])["document"]
    assert [(s["id"], s["start"], s["end"]) for s in after["music_timeline"]["slots"]] == [
        (s["id"], s["start"], s["end"]) for s in project["document"]["music_timeline"]["slots"]]
    if protected == "manual-markers":
        assert after["rhythm"]["markers"] == document["rhythm"]["markers"]
    elif protected == "written-direction":
        assert after["music_timeline"]["slots"][0]["direction"] == document["music_timeline"]["slots"][0]["direction"]


def test_cancelling_after_first_cut_adoption_keeps_the_saved_starter(record, config, store, db, tmp_path, monkeypatch):
    project = _project(_starter(record), store, db, tmp_path, monkeypatch)
    calls = _local_stages(record, monkeypatch, store, project, cancel_after_plan=True)

    result = _generate(project, store, config, db, mode="regenerate")

    assert result["status"] == "cancelled", result["error"]
    assert calls == ["decode", "interpretation", "timing", "plan"]
    assert store.get_project(project["id"]) == project
    assert len(store.revisions(project["id"])) == 2
    assert provisional_timing_eligible(store.get_project(project["id"])["document"])
