"""Local timing preparation cannot invent listening evidence or overwrite edits."""
from copy import deepcopy
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from pipeline.lab import music
from pipeline.lab.media import import_track
from pipeline.lab.models import JobRequest, ProjectDocument
from pipeline.lab.rhythm_timing import prepare_timing, timing_suggestions
from pipeline.lab.timeline import (PROVISIONAL_TIMING_CONTRACT, ensure_timeline,
                                   provisional_timing_eligible, timing_fingerprint)
from pipeline.lab.store import LabStore
from pipeline.tests.test_lab import db  # noqa: F401
from pipeline.tests.test_lab_music import _audio


def _document():
    passage = {"start": 10., "end": 54.}
    return ProjectDocument(track={"id": "track", "name": "song", "duration": 90}, passage=passage,
                           rhythm={"provenance": {"track": "track", "passage": passage},
                                   "beats": [10. + i / 2 for i in range(88)],
                                   "downbeats": [10. + i * 2 for i in range(22)],
                                   "markers": [], "waveform_start": 10., "waveform_end": 54.,
                                   "intensity": [.2] * 11 + [.7] * 8 + [.3] * 25}).model_dump()


def test_measured_changes_and_bar_groups_make_bounded_placeholders_not_every_beat():
    document = _document()
    original_rhythm = deepcopy(document["rhythm"])
    timeline = prepare_timing(document)
    cuts = document["rhythm"]["timing_suggestions"]["cuts"]
    durations = [round(slot["end"] - slot["start"], 4) for slot in timeline["slots"]]
    assert 2 < len(timeline["slots"]) < len(original_rhythm["downbeats"])
    assert len(set(durations)) > 1
    assert {cut["basis"] for cut in cuts} == {"bar-group", "relative-amplitude-change"}
    assert all(cut["landmark_time"] in original_rhythm["downbeats"] for cut in cuts)
    assert document["rhythm"]["beats"] == original_rhythm["beats"]
    assert document["rhythm"]["markers"] == []
    assert document["analysis"] is None and document["direction_plan"] is None
    assert all(slot["needs_direction"] and slot["direction"] is None and slot["clip_id"] is None for slot in timeline["slots"])
    assert timeline["provisional_timing"] == {"contract": PROVISIONAL_TIMING_CONTRACT, "fingerprint": timing_fingerprint(timeline)}
    assert provisional_timing_eligible(document)
    ProjectDocument.model_validate(document)


def test_sustained_music_groups_real_bars_without_jitter_or_one_huge_slot():
    document = _document()
    document["rhythm"]["intensity"] = [.4] * 44
    timeline = prepare_timing(document)
    assert max(slot["end"] - slot["start"] for slot in timeline["slots"]) <= 12
    assert all(cut["basis"] == "bar-group" for cut in document["rhythm"]["timing_suggestions"]["cuts"])
    assert all(cut["landmark_time"] == cut["time"] for cut in document["rhythm"]["timing_suggestions"]["cuts"])


def test_pacing_changes_only_requested_scaffold_not_fixed_shots():
    patient, kinetic = _document(), _document()
    patient["planner_settings"]["pacing"] = "patient"
    kinetic["planner_settings"]["pacing"] = "kinetic"
    prepare_timing(patient); prepare_timing(kinetic)
    assert len(patient["music_timeline"]["slots"]) < len(kinetic["music_timeline"]["slots"])
    original = deepcopy(patient["music_timeline"])
    patient["planner_settings"]["pacing"] = "kinetic"
    prepare_timing(patient)
    assert patient["music_timeline"] == original


def test_rapid_starter_uses_real_pulses_across_the_selection_and_preserves_existing_cuts():
    rapid, kinetic = _document(), _document()
    rapid["planner_settings"]["pacing"] = "rapid"
    kinetic["planner_settings"]["pacing"] = "kinetic"
    timeline = prepare_timing(rapid)
    assert 32 < len(timeline["slots"]) <= 300
    assert len(timeline["slots"]) > len(prepare_timing(kinetic)["slots"])
    cuts = rapid["rhythm"]["timing_suggestions"]["cuts"]
    assert cuts[-1]["time"] > 52
    assert all(cut["landmark_time"] in rapid["rhythm"]["beats"] and cut["landmark_source"] == "detected-beat" for cut in cuts)
    original = deepcopy(timeline)
    rapid["planner_settings"]["pacing"] = "patient"
    assert prepare_timing(rapid) == original


@pytest.mark.parametrize("missing", ["provenance", "grid", "scope"])
def test_missing_or_stale_beat_data_does_not_fabricate_guides_or_cuts(missing):
    document = _document()
    if missing == "provenance":
        document["rhythm"].pop("provenance")
    elif missing == "scope":
        document["rhythm"]["provenance"]["track"] = "different-song"
    else:
        document["rhythm"].update(beats=[], downbeats=[])
    suggestions = timing_suggestions(document)
    assert suggestions["cuts"] == []
    assert "No usable beat grid" in suggestions["note"]
    assert len(prepare_timing(document)["slots"]) == 1


def test_beat_only_output_is_never_presented_as_downbeats():
    document = _document(); document["rhythm"]["downbeats"] = []
    assert all(cut["landmark_source"] == "detected-beat" for cut in timing_suggestions(document)["cuts"])


def test_rapid_timing_distributes_its_capacity_across_the_complete_ten_minute_song():
    document = _document()
    passage = {"start": .123, "end": 600.123}
    beats = [passage["start"] + index / 2 for index in range(1200)]
    document.update(track={"id": "track", "name": "Long song", "duration": 601}, passage=passage,
                    planner_settings={"pacing": "rapid"},
                    rhythm={"provenance": {"track": "track", "passage": passage}, "beats": beats})

    timeline = prepare_timing(document)
    suggestions = document["rhythm"]["timing_suggestions"]

    assert len(timeline["slots"]) == 300
    assert suggestions["capacity_limited"] and suggestions["candidate_cut_count"] > 299
    assert max(slot["end"] - slot["start"] for slot in timeline["slots"]) < 3
    assert timeline["slots"][0]["start"] == passage["start"]
    assert timeline["slots"][-1]["end"] == passage["end"]
    assert all(cut["landmark_time"] in beats for cut in suggestions["cuts"])
    assert all(abs((cut["time"] - passage["start"]) * 24 - round((cut["time"] - passage["start"]) * 24)) < 1e-6
               for cut in suggestions["cuts"])
    ProjectDocument.model_validate(document)


@pytest.mark.parametrize("pacing", ["rapid", "kinetic"])
def test_timing_resumes_on_real_landmarks_after_a_gap_in_the_beat_guides(pacing):
    document = _document()
    passage = {"start": 0., "end": 30.017}
    beats = [index / 2 for index in range(20)] + [20 + index / 2 for index in range(20)]
    document.update(passage=passage, planner_settings={"pacing": pacing},
                    rhythm={"provenance": {"track": "track", "passage": passage}, "beats": beats})

    timeline = prepare_timing(document)
    cuts = document["rhythm"]["timing_suggestions"]["cuts"]

    assert any(cut["landmark_time"] == 20 and cut["basis"] == "pulse-after-gap" for cut in cuts)
    assert any(cut["landmark_time"] > 20 for cut in cuts)
    assert not any(9.5 < cut["landmark_time"] < 20 for cut in cuts)
    assert all(cut["landmark_time"] in beats for cut in cuts)
    assert timeline["slots"][-1]["end"] == passage["end"]
    assert all(slot["end"] - slot["start"] >= 1 / 24 - 1e-6 for slot in timeline["slots"])
    ProjectDocument.model_validate(document)


def test_rebuild_is_explicit_preserves_bin_and_protects_locked_placement():
    document = _document(); prepare_timing(document)
    slot = document["music_timeline"]["slots"][0]
    document["clips"] = [{"id": "clip", "film_id": "film", "source_start": 0.,
                          "source_end": slot["end"] - slot["start"], "locked": True}]
    slot.update(clip_id="clip", direction={"query": "my image"}, direction_source="user")
    original = deepcopy(document["music_timeline"])
    prepare_timing(document)
    assert document["music_timeline"] == original
    with pytest.raises(ValueError, match="Unlock placed"):
        prepare_timing(document, replan=True)
    document["clips"][0]["locked"] = False
    sources = deepcopy(document["clips"])
    prepare_timing(document, replan=True)
    assert document["clips"] == sources
    assert all(slot["clip_id"] is None for slot in document["music_timeline"]["slots"])
    assert document["music_timeline"]["slots"][0]["direction"]["query"] == "my image"
    assert document["music_timeline"]["provisional_timing"] is None
    ProjectDocument.model_validate(document)


def test_manual_markers_remain_exact_and_never_overwrite_measured_beats():
    document = _document()
    document["rhythm"].update(marker_source="user", track_id="track", passage=document["passage"], markers=[23.1, 48.2])
    timeline = prepare_timing(document)
    assert [slot["end"] for slot in timeline["slots"]] == [23.1, 48.2, 54.]
    assert len(document["rhythm"]["beats"]) == 88
    assert timeline["provisional_timing"] is None


def test_existing_empty_timing_never_acquires_provisional_ownership_by_inference():
    document = _document()
    original = deepcopy(ensure_timeline(document))
    assert original["provisional_timing"] is None
    assert prepare_timing(document) == original
    assert not provisional_timing_eligible(document)


@pytest.mark.parametrize("change", [
    "cut", "slot-id", "fingerprint", "track", "rhythm-scope", "manual-markers", "user-direction", "legacy-direction",
    "feedback", "alternative", "search-evidence", "resolved-search", "saved-clip", "placed-clip", "missing-marker",
])
def test_changed_or_user_owned_starters_cannot_be_automatically_retimed(change):
    document = _document(); timeline = prepare_timing(document)
    first = timeline["slots"][0]
    if change == "cut":
        first["end"] += .25; timeline["slots"][1]["start"] += .25
    elif change == "slot-id":
        first["id"] = "a-manually-replaced-slot"
    elif change == "fingerprint":
        timeline["provisional_timing"]["fingerprint"] = "0" * 64
    elif change == "track":
        document["track"]["id"] = "another-track"
    elif change == "rhythm-scope":
        document["rhythm"]["provenance"]["passage"] = {"start": 0., "end": 44.}
    elif change == "manual-markers":
        document["rhythm"]["marker_source"] = "user"
    elif change in {"user-direction", "legacy-direction"}:
        first.update(direction={"query": "my shot"}, direction_source="user" if change == "user-direction" else None)
    elif change == "feedback":
        first["feedback"] = "wrong_energy"
    elif change == "alternative":
        first["alternatives"] = [{"clip": {"id": "reviewed"}}]
    elif change == "search-evidence":
        first["search_evidence"] = {"rank": 1}
    elif change == "resolved-search":
        first["resolved_search"] = {"clauses": []}
    elif change == "saved-clip":
        document["clips"] = [{"id": "saved-source"}]
    elif change == "placed-clip":
        first["clip_id"] = "placed-source"
    else:
        timeline["provisional_timing"] = None
    assert not provisional_timing_eligible(document)


def test_unrelated_brief_and_pacing_settings_do_not_claim_starter_cuts():
    document = _document(); prepare_timing(document)
    document["brief"] = "A different visual idea"
    document["planner_settings"]["pacing"] = "kinetic"
    assert provisional_timing_eligible(document)


def test_rhythm_job_decodes_audio_without_hosted_interpretation_or_search(config, tmp_path, monkeypatch):
    store = LabStore(config.paths.state_dir); store.initialize()
    source = tmp_path / "song.wav"; _audio(source)
    track = import_track(store, source, "song.wav")
    document = ProjectDocument(track={key: track[key] for key in ("id", "name", "duration")},
                               passage={"start": 0., "end": 6.}).model_dump()
    hosted = MagicMock(side_effect=AssertionError("Local timing must not call a provider"))
    search = MagicMock(side_effect=AssertionError("Local timing must not retrieve scenes"))
    monkeypatch.setattr(music, "_hosted_json", hosted)
    monkeypatch.setattr(music, "retrieve_edit_candidates", search)
    progress = []
    result = music.run_music_job({"id": "local", "kind": "rhythm", "document": document, "snapshot": {}},
                                 config, object(), progress.append)
    assert result["analysis"] is None and result["clips"] == []
    assert result["rhythm"]["warning"] and result["rhythm"]["beats"] == []
    assert result["music_timeline"]["slots"][0]["needs_direction"]
    assert "Preparing beat guides and editable placeholders" in progress
    hosted.assert_not_called(); search.assert_not_called()


def test_timing_job_options_are_bounded_to_the_two_explicit_timing_stages():
    for kind in ("rhythm", "analyze"):
        assert JobRequest(kind=kind, base_revision=1, replan_timing=True).replan_timing
    for kind in ("plan", "draft", "generate", "render"):
        with pytest.raises(ValueError, match="Only rhythm or analyze"):
            JobRequest(kind=kind, base_revision=1, replan_timing=True)
    with pytest.raises(ValueError):
        JobRequest(kind="rhythm", base_revision=1, slot_ids=["one"])


@pytest.mark.parametrize("replan", [False, True])
def test_rhythm_api_freezes_request_and_worker_applies_one_local_revision(config, db, tmp_path, monkeypatch, replan):
    from pipeline.api.main import app
    from pipeline.lab.worker import execute_job

    source = tmp_path / "song.wav"; _audio(source)
    hosted = MagicMock(side_effect=AssertionError("Local timing must never call hosted models"))
    monkeypatch.setattr(music, "_hosted_json", hosted)
    with patch("pipeline.api.main.load_config", return_value=config), patch("pipeline.api.main.open_db", return_value=db), patch("pipeline.api.main.ensure_search_indexes"), TestClient(app) as client:
        project = client.post("/lab/projects", json={"name": "Local timing"}).json()
        url = f"/lab/projects/{project['id']}"
        project = client.post(url + "/track", data={"base_revision": 1}, files={"file": ("song.wav", source.read_bytes())}).json()
        response = client.post(url + "/jobs", json={"kind": "rhythm", "base_revision": 2, "replan_timing": replan})
        assert response.status_code == 200, response.text
        store = LabStore(config.paths.state_dir)
        job = store.claim()
        assert job["kind"] == "rhythm" and job["snapshot"]["replan_timing"] is replan
        execute_job(job, config, db, store)
        completed = store.get_job(job["id"])
        assert completed["status"] == "completed", completed.get("error")
        assert completed["result"]["beat_count"] == 0
        assert completed["result"]["placeholder_count"] == 1
        updated = client.get(url).json()
        assert updated["revision"] == 3 and updated["document"]["analysis"] is None
        assert updated["document"]["music_timeline"]["slots"][0]["needs_direction"]
        hosted.assert_not_called()
