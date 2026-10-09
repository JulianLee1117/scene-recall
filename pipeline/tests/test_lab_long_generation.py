"""Long songs remain bounded private work and one recoverable project revision."""
from copy import deepcopy

import pytest

from pipeline.lab import direction_planner, music, music_planner, timing_planner
from pipeline.lab.long_audio import compose_analysis
from pipeline.lab.long_form import partition_passage
from pipeline.lab.models import ClipSelection, MusicDirection, ProjectDocument
from pipeline.lab.pacing import batch_slots, timing_diagnostics
from pipeline.lab.worker import execute_job
from pipeline.tests.test_lab import db, store  # noqa: F401
from pipeline.tests.test_lab_music import _audio, _audio_interpretation


def _analysis(track_id, passage):
    parts = []
    for scope in partition_passage(passage):
        provenance = {"track": track_id, "passage": deepcopy(scope)}
        profile = {**provenance, "interpretation_id": music.digest(provenance)}
        analysis = {**_audio_interpretation(scope["start"], scope["end"]), "provenance": provenance,
                    "events_provenance": {**profile, "source": "ai-observed"},
                    "song_meaning_provenance": {**profile, "source": "ai-heard-paraphrase"}}
        parts.append({"passage": scope, "analysis": analysis})
    return compose_analysis(parts, passage)


def _project(store, db, tmp_path, *, start=0., duration=116.610612, fixed=False):
    audio = tmp_path / "audio.wav"
    _audio(audio, seconds=1)
    identity = music.content_hash(audio)
    track = store.add_track(identity, "Full song", start + duration, audio)
    film = tmp_path / "film.mp4"
    film.touch()
    db.open_table("films").add([{"film_id": "film", "title": "Film", "path": str(film), "duration": 10000., "fps": 24.}])
    document = ProjectDocument(track={key: track[key] for key in ("id", "name", "duration")},
        passage={"start": start, "end": start + duration},
        song_context={"track_id": identity, "notes": "Quick piano images first, longer steady images after the build; one coherent motif."}).model_dump(mode="json")
    document["analysis"] = _analysis(identity, document["passage"])
    if fixed:
        assert duration == 120
        slots = []
        for index in range(40):
            slot = {"id": f"s{index}", "start": start + index * 3, "end": start + (index + 1) * 3,
                    "section_index": 0 if index < 20 else 1,
                    "direction": MusicDirection(query=f"Custom image {index}").model_dump(mode="json"), "direction_source": "user"}
            if index not in (0, 35):
                clip = ClipSelection(id=f"old{index}", film_id="film", unit_id=f"unit{index}",
                                     source_start=index * 4., source_end=index * 4. + 3, locked=True).model_dump(mode="json")
                document["clips"].append(clip)
                slot["clip_id"] = clip["id"]
            slots.append(slot)
        document["music_timeline"] = {"track_id": identity, "passage": deepcopy(document["passage"]), "slots": slots}
    project = store.create_project("Long song", "music-sketch")
    return store.update_project(project["id"], 1, document)


def _stages(monkeypatch, *, fail_part=None, cancel=None):
    calls = []

    def timing(job, _config, _progress):
        document = deepcopy(job["document"])
        start, end = document["passage"]["start"], document["passage"]["end"]
        # Fixed fixture density; it is not a production preset requirement.
        count = min(300, max(1, round((end - start) / (.9 if document["planner_settings"]["pacing"] == "rapid" else 3))))
        frames = round((end - start) * 24)
        bounds = [start, *[start + round(frames * index / count) / 24 for index in range(1, count)], end]
        document["music_timeline"] = {"track_id": document["track"]["id"], "passage": deepcopy(document["passage"]),
            "slots": [{"id": f"{job['id']}-s{index}", "start": left, "end": right, "section_index": 0,
                       "needs_direction": True} for index, (left, right) in enumerate(zip(bounds, bounds[1:]))]}
        document["direction_plan"] = {"timing_plan": {"contract": "fixture", "passage": deepcopy(document["passage"]),
            "fps": 24, "end_frames": [round((value - start) * 24) for value in bounds[1:]], "notes": [],
            "nominal_timing": timing_diagnostics(document["music_timeline"]["slots"]), "cache_reused": False}}
        calls.append({"stage": "timing", "job": deepcopy(job)})
        return ProjectDocument.model_validate(document).model_dump(mode="json")

    def plan(job, _config, _db, _progress):
        document = deepcopy(job["document"])
        for slot in document["music_timeline"]["slots"]:
            if slot["id"] in job["snapshot"]["slot_ids"]:
                slot.update(direction=MusicDirection(query=f"Intent {slot['id']}").model_dump(mode="json"),
                            direction_source="ai", needs_direction=False)
        document["visual_plan"] = document.get("visual_plan") or {"arc": "One evolving image across the complete song", "motifs": "Light and water", "source": "ai"}
        calls.append({"stage": "plan", "job": deepcopy(job)})
        return ProjectDocument.model_validate(document).model_dump(mode="json")

    def fill(document, _config, _db, _progress, job_id, slot_ids=None, **options):
        if fail_part and f"part-{fail_part:02}" in job_id:
            raise ValueError("Invalid choice in later part")
        document = deepcopy(document)
        calls.append({"stage": "select", "job_id": job_id, "document": deepcopy(document), "options": deepcopy(options)})
        selected = 0
        for slot in document["music_timeline"]["slots"]:
            if slot["id"] not in slot_ids:
                continue
            clip = ClipSelection(id=f"new-{slot['id']}", film_id="film", unit_id=f"new-unit-{slot['id']}",
                                 source_start=1000. + slot["start"], source_end=1000. + slot["end"]).model_dump(mode="json")
            document["clips"].append(clip)
            slot["clip_id"] = clip["id"]
            selected += 1
        document["analysis"]["draft"] = {"selected_count": selected, "candidate_count": selected * 2}
        if cancel:
            cancel()
        return ProjectDocument.model_validate(document).model_dump(mode="json")

    monkeypatch.setattr(timing_planner, "run_timing_job", timing)
    monkeypatch.setattr(direction_planner, "run_direction_job", plan)
    monkeypatch.setattr(music_planner, "fill_timeline", fill)
    monkeypatch.setattr(music, "run_music_job", lambda job, *_: {**deepcopy(job["document"]), "analysis": _analysis(job["document"]["track"]["id"], job["document"]["passage"])})
    return calls


def _run(store, config, db, project, *, mode="fill", slot_ids=None):
    store.enqueue("generate", project["id"], project["revision"], generate={"mode": mode}, slot_ids=slot_ids)
    job = store.claim()
    return execute_job(job, config, db, store), job


@pytest.mark.parametrize("start,duration", [(0., 116.610612), (53.99, 116.610612), (0., 600.)])
def test_full_song_uses_bounded_parts_shared_context_and_one_revision(config, store, db, tmp_path, monkeypatch, start, duration):
    project = _project(store, db, tmp_path, start=start, duration=duration)
    calls = _stages(monkeypatch)
    result, _ = _run(store, config, db, project, mode="regenerate")
    assert result["status"] == "completed", result["error"]
    saved = store.get_project(project["id"])
    assert saved["revision"] == project["revision"] + 1
    document = saved["document"]
    plans = [call["job"] for call in calls if call["stage"] == "plan"]
    selections = [call for call in calls if call["stage"] == "select"]
    assert len(plans) == len(batch_slots(document["music_timeline"]["slots"]))
    assert all(job["document"]["passage"]["end"] - job["document"]["passage"]["start"] <= 90 for job in plans)
    assert all(len(call["document"]["music_timeline"]["slots"]) <= 32 for call in selections)
    assert all(job["document"]["song_context"] == project["document"]["song_context"] for job in plans)
    assert all(len(job["snapshot"]["sequence_context"]["song_outline"]) == len(partition_passage(document["passage"])) for job in plans)
    assert plans[1]["snapshot"]["sequence_context"]["visual_plan"] == document["visual_plan"]
    assert plans[1]["snapshot"]["sequence_context"]["neighboring_shots"]
    assert {clip["id"] for clip in selections[1]["options"]["previous_sources"]} == {
        f"new-{slot['id']}" for slot in selections[0]["document"]["music_timeline"]["slots"]}
    slots = document["music_timeline"]["slots"]
    assert slots[0]["start"] == start and slots[-1]["end"] == start + duration
    assert all(abs((slot["end"] - start) * 24 - round((slot["end"] - start) * 24)) < 1e-6 for slot in slots[:-1])
    assert result["result"]["selected_count"] == len(document["clips"])
    assert result["result"]["candidate_count"] == 2 * len(document["clips"])
    budget = result["result"]["timing_plan"]
    assert budget["nominal_timing"]["selected_shots"] == budget["final_timing"]["selected_shots"] == len(slots)
    assert budget["final_timing"]["average_seconds_selected"] == pytest.approx(duration / len(slots))
    assert len(store.revisions(project["id"])) == 3


@pytest.mark.parametrize("duration", [30.023, 116.610612, 600.])
def test_rapid_edit_reuses_music_evidence_and_commits_dense_sequence_once(config, store, db, tmp_path, monkeypatch, duration):
    project = _project(store, db, tmp_path, start=9.94, duration=duration)
    document = deepcopy(project["document"])
    document["planner_settings"]["pacing"] = "rapid"
    if duration < 90:
        document["analysis"] = document["analysis"]["parts"][0]["analysis"]
    project = store.update_project(project["id"], project["revision"], document)
    calls = _stages(monkeypatch)
    monkeypatch.setattr(music, "run_music_job", lambda *_: pytest.fail("Smaller text scopes must reuse current music evidence"))
    result, _ = _run(store, config, db, project, mode="regenerate")
    assert result["status"] == "completed", result["error"]
    saved = store.get_project(project["id"])
    assert saved["revision"] == project["revision"] + 1
    plans = [call["job"] for call in calls if call["stage"] == "plan"]
    assert len(plans) == len(batch_slots(saved["document"]["music_timeline"]["slots"]))
    assert all(job["snapshot"]["sequence_context"]["song_outline"] for job in plans)
    selections = [call for call in calls if call["stage"] == "select"]
    assert all(len(call["document"]["music_timeline"]["slots"]) <= 32 for call in selections)
    slots = saved["document"]["music_timeline"]["slots"]
    assert slots[0]["start"] == 9.94 and slots[-1]["end"] == 9.94 + duration
    assert all(a["end"] == b["start"] for a, b in zip(slots, slots[1:]))
    assert len(slots) == min(300, round(duration / .9))
    assert len([call for call in calls if call["stage"] == "timing"]) == 1
    assert result["result"]["timing_plan"]["end_frames"] == result["result"]["timing_plan"]["final_end_frames"]


def test_rapid_short_fixed_timeline_fills_all_gaps_without_retiming(config, store, db, tmp_path, monkeypatch):
    project = _project(store, db, tmp_path, duration=20)
    document = deepcopy(project["document"])
    document["planner_settings"]["pacing"] = "rapid"
    document["music_timeline"] = {"track_id": document["track"]["id"], "passage": document["passage"],
        "slots": [{"id": f"manual-{index}", "start": index / 2, "end": (index + 1) / 2, "section_index": 0,
                   "direction": MusicDirection(query=f"My image {index}").model_dump(mode="json"), "direction_source": "user"}
                  for index in range(40)]}
    project = store.update_project(project["id"], project["revision"], document)
    calls = _stages(monkeypatch)
    result, _ = _run(store, config, db, project)
    assert result["status"] == "completed", result["error"]
    saved = store.get_project(project["id"])["document"]
    assert [(slot["id"], slot["start"], slot["end"], slot["direction"]) for slot in saved["music_timeline"]["slots"]] == [
        (slot["id"], slot["start"], slot["end"], slot["direction"]) for slot in project["document"]["music_timeline"]["slots"]]
    assert all(slot["clip_id"] for slot in saved["music_timeline"]["slots"])
    assert len([call for call in calls if call["stage"] == "select"]) == 2


@pytest.mark.parametrize("failure", ["later-part", "cancel"])
def test_failure_or_cancel_during_long_generation_never_applies_a_partial_edit(config, store, db, tmp_path, monkeypatch, failure):
    project = _project(store, db, tmp_path)
    store.enqueue("generate", project["id"], project["revision"], generate={"mode": "regenerate"})
    job = store.claim()
    _stages(monkeypatch, fail_part=2 if failure == "later-part" else None,
            cancel=(lambda: store.cancel(job["id"])) if failure == "cancel" else None)
    result = execute_job(job, config, db, store)
    assert result["status"] == ("failed" if failure == "later-part" else "cancelled")
    assert store.get_project(project["id"]) == project
    assert len(store.revisions(project["id"])) == 2


def test_long_fill_preserves_fixed_cuts_custom_directions_and_locked_neighbors(config, store, db, tmp_path, monkeypatch):
    project = _project(store, db, tmp_path, duration=120, fixed=True)
    calls = _stages(monkeypatch)
    monkeypatch.setattr(direction_planner, "run_direction_job", lambda *_: pytest.fail("Custom directions must not be replanned"))
    result, _ = _run(store, config, db, project)
    assert result["status"] == "completed", result["error"]
    saved = store.get_project(project["id"])["document"]
    old_slots, new_slots = project["document"]["music_timeline"]["slots"], saved["music_timeline"]["slots"]
    assert [(slot["id"], slot["start"], slot["end"], slot["direction"]) for slot in new_slots] == [(slot["id"], slot["start"], slot["end"], slot["direction"]) for slot in old_slots]
    assert saved["clips"][:len(project["document"]["clips"])] == project["document"]["clips"]
    assert all(slot["clip_id"] for slot in new_slots)
    selections = [call for call in calls if call["stage"] == "select"]
    assert len(selections) == 2 and all(len(call["document"]["music_timeline"]["slots"]) == 1 for call in selections)


def test_long_starter_fill_keeps_exact_cuts_and_has_no_source_timing_scope(config, store, db, tmp_path, monkeypatch):
    from pipeline.lab.rhythm_timing import prepare_timing
    from pipeline.lab.timeline import provisional_timing_eligible

    project = _project(store, db, tmp_path, start=53.99, duration=120)
    document = deepcopy(project["document"])
    document["rhythm"] = {"provenance": {"track": document["track"]["id"], "passage": document["passage"]},
                          "beats": [53.99 + index for index in range(120)],
                          "downbeats": [53.99 + index for index in range(0, 120, 4)]}
    prepare_timing(document)
    assert provisional_timing_eligible(document)
    project = store.update_project(project["id"], project["revision"], document)
    calls = _stages(monkeypatch)
    monkeypatch.setattr(timing_planner, "run_timing_job", lambda *_: pytest.fail("Fill must keep starter cuts"))

    result, _ = _run(store, config, db, project)

    assert result["status"] == "completed", result["error"]
    saved = store.get_project(project["id"])["document"]
    assert [(slot["id"], slot["start"], slot["end"]) for slot in saved["music_timeline"]["slots"]] == [
        (slot["id"], slot["start"], slot["end"]) for slot in project["document"]["music_timeline"]["slots"]]
    assert all(slot["clip_id"] for slot in saved["music_timeline"]["slots"])
    assert saved["music_timeline"]["provisional_timing"] is None
    assert "timing" not in result["result"]["stages"]
    assert all(call["options"].get("timing_scope") is None for call in calls if call["stage"] == "select")


def test_long_regeneration_retains_saved_clips_and_excludes_previous_placed_sources(config, store, db, tmp_path, monkeypatch):
    project = _project(store, db, tmp_path, duration=120, fixed=True)
    document = deepcopy(project["document"])
    for clip in document["clips"]:
        clip["locked"] = False
    document["clips"].insert(4, ClipSelection(id="locked-bin", film_id="film", unit_id="saved-only",
        source_start=800., source_end=805., locked=True).model_dump(mode="json"))
    document["visual_plan"] = {"arc": "A chosen continuous journey", "motifs": "Windows", "source": "user"}
    project = store.update_project(project["id"], project["revision"], document)
    calls = _stages(monkeypatch)
    result, _ = _run(store, config, db, project, mode="regenerate")
    assert result["status"] == "completed", result["error"]
    saved = store.get_project(project["id"])["document"]
    assert saved["clips"][:len(document["clips"])] == document["clips"]
    assert saved["visual_plan"] == document["visual_plan"]
    assert not ({slot["id"] for slot in saved["music_timeline"]["slots"]} & {slot["id"] for slot in document["music_timeline"]["slots"]})
    old_placed = {slot["clip_id"] for slot in document["music_timeline"]["slots"] if slot["clip_id"]}
    selections = [call for call in calls if call["stage"] == "select"]
    assert {clip["id"] for clip in selections[0]["options"]["previous_sources"]} == old_placed
    assert all(old_placed <= {clip["id"] for clip in call["options"]["previous_sources"]} for call in selections)
    assert all("locked-bin" not in {clip["id"] for clip in call["options"]["previous_sources"]} for call in selections)


def test_long_regeneration_rejects_placed_locks_before_enqueue(store, db, tmp_path):
    project = _project(store, db, tmp_path, duration=120, fixed=True)
    with pytest.raises(ValueError, match="Unlock placed clips"):
        store.enqueue("generate", project["id"], project["revision"], generate={"mode": "regenerate"})
    assert not store.project_jobs(project["id"])


def test_long_complete_edit_at_saved_capacity_is_noop_before_audio_or_models(config, store, db, tmp_path, monkeypatch):
    project = _project(store, db, tmp_path, duration=120, fixed=True)
    document = deepcopy(project["document"])
    for slot in document["music_timeline"]["slots"]:
        if not slot["clip_id"]:
            clip = ClipSelection(id=f"filled-{slot['id']}", film_id="film", source_start=900., source_end=903.).model_dump(mode="json")
            document["clips"].append(clip)
            slot["clip_id"] = clip["id"]
    document["clips"].extend(ClipSelection(id=f"saved-{index}", film_id="film", source_start=950., source_end=951.).model_dump(mode="json")
                             for index in range(600 - len(document["clips"])))
    project = store.update_project(project["id"], project["revision"], document)
    monkeypatch.setattr(music, "content_hash", lambda *_: pytest.fail("A complete edit needs no source or model work"))
    calls = _stages(monkeypatch)
    result, _ = _run(store, config, db, project)
    assert result["status"] == "completed" and result["result"]["unchanged"] is True
    assert result["result"]["stages"] == [] and not calls
    assert store.get_project(project["id"]) == project


def test_edit_during_long_generation_retains_complete_stale_proposal(config, store, db, tmp_path, monkeypatch):
    project = _project(store, db, tmp_path)
    changed = []
    def edit_once():
        if not changed:
            changed.append(store.update_project(project["id"], project["revision"], {**project["document"], "brief": "New user work"}))
    _stages(monkeypatch, cancel=edit_once)
    result, _ = _run(store, config, db, project, mode="regenerate")
    assert result["status"] == "completed" and result["result"]["applied"] is False
    proposal = result["result"]["document"]
    assert all(slot["clip_id"] for slot in proposal["music_timeline"]["slots"])
    assert proposal["music_timeline"]["slots"][-1]["end"] == project["document"]["passage"]["end"]
    assert store.get_project(project["id"]) == changed[0]


def test_long_fill_accounts_for_entire_saved_bin_before_paid_work(config, store, db, tmp_path, monkeypatch):
    project = _project(store, db, tmp_path, duration=120, fixed=True)
    document = deepcopy(project["document"])
    document["clips"].extend(ClipSelection(id=f"saved-{index}", film_id="film", source_start=950., source_end=951.).model_dump(mode="json")
                             for index in range(600 - len(document["clips"])))
    project = store.update_project(project["id"], project["revision"], document)
    monkeypatch.setattr(music, "content_hash", lambda *_: pytest.fail("Capacity must be checked before audio or paid model work"))
    result, _ = _run(store, config, db, project)
    assert result["status"] == "failed" and "600 saved clips" in result["error"]
    assert store.get_project(project["id"]) == project


@pytest.mark.parametrize("duration", [0, -1, 600.001, float("nan"), float("inf")])
def test_partition_rejects_invalid_or_over_limit_duration_before_rounding(duration):
    with pytest.raises(ValueError):
        partition_passage({"start": 0., "end": duration})


def test_local_long_song_scaffold_keeps_finding_real_landmarks_near_the_end():
    from pipeline.lab.rhythm_timing import timing_suggestions

    passage = {"start": 0., "end": 600.}
    document = ProjectDocument(track={"id": "track", "name": "song", "duration": 600}, passage=passage,
                               planner_settings={"pacing": "kinetic"}).model_dump(mode="json")
    document["rhythm"] = {"provenance": {"track": "track", "passage": passage},
                          "downbeats": list(range(0, 601, 2)), "beats": list(range(601)),
                          "waveform_start": 0., "waveform_end": 600., "intensity": [.5] * 4800}
    result = timing_suggestions(document)
    assert 31 < len(result["cuts"]) < 300
    assert result["cuts"][-1]["time"] >= 594
    assert all(cut["landmark_time"] in document["rhythm"]["downbeats"] for cut in result["cuts"])
