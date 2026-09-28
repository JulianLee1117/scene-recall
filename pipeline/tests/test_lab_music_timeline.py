"""Authoritative musical timing, bounded replacement and real gap playback."""
from __future__ import annotations

from copy import deepcopy
from io import BytesIO
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient
from PIL import Image
import pytest

from pipeline.lab import music
from pipeline.lab.media import import_track, render_manifest, render_reel, run_process, probe_media
from pipeline.lab.models import ClipSelection, JobRequest, ProjectDocument
from pipeline.lab.timeline import draft_targets, ensure_timeline
from pipeline.tests.selection_helpers import selector_response
from pipeline.tests.test_lab import db, store, _fixture_media  # noqa: F401
from pipeline.tests.test_lab_music import _interpretation, _audio


def _clip(identity="first", start=20, duration=3, locked=False):
    return ClipSelection(id=identity, film_id="film", unit_id=identity, title=identity,
                         source_start=start, source_end=start + duration, locked=locked).model_dump()


def _document():
    return ProjectDocument(track={"id": "track", "name": "Music", "duration": 20},
                           passage={"start": 10, "end": 19}, analysis=_interpretation(10, 19),
                           rhythm={"beats": [10, 11, 12, 13, 14, 15, 16, 17, 18], "downbeats": [10, 14, 18],
                                   "intensity": [.2, .4, .8], "markers": [13, 16], "marker_source": "user"}).model_dump()


def _timeline_document():
    document = _document()
    ensure_timeline(document)
    return document


def test_old_documents_default_null_and_manual_timeline_needs_no_analysis():
    assert ProjectDocument().music_timeline is None
    document = _document()
    document["analysis"] = None
    ensure_timeline(document)
    assert ProjectDocument.model_validate(document).music_timeline.slots[0].section_index == 0


@pytest.mark.parametrize("mutation", [
    lambda d: d["music_timeline"].update(track_id="different"),
    lambda d: d["music_timeline"]["slots"][0].update(end=12.9),
    lambda d: d["music_timeline"]["slots"][1].update(id=d["music_timeline"]["slots"][0]["id"]),
    lambda d: d["music_timeline"]["slots"][0].update(clip_id="missing"),
    lambda d: d["music_timeline"]["slots"][0].update(section_index=2),
    lambda d: d["music_timeline"]["slots"][0].update(start=float("nan")),
])
def test_invalid_timeline_cannot_be_saved(mutation):
    document = _timeline_document()
    mutation(document)
    with pytest.raises(ValueError):
        ProjectDocument.model_validate(document)


def test_selected_clip_duration_must_fit_slot_and_clip_cannot_fill_two_slots():
    document = _timeline_document()
    document["clips"] = [_clip(duration=4)]
    document["music_timeline"]["slots"][0]["clip_id"] = "first"
    with pytest.raises(ValueError, match="duration"):
        ProjectDocument.model_validate(document)
    document["clips"][0]["source_end"] -= 1
    document["music_timeline"]["slots"][1]["clip_id"] = "first"
    with pytest.raises(ValueError, match="unique"):
        ProjectDocument.model_validate(document)


def test_migration_preserves_unlocked_clips_and_authoritative_markers():
    document = _document()
    document["clips"] = [_clip(duration=2)]
    document["rhythm"].update(marker_source="user", markers=[12.5, 14, 18])
    timeline = ensure_timeline(document)
    assert [(slot["start"], slot["end"]) for slot in timeline["slots"]] == [(10, 12), (12, 12.5), (12.5, 14), (14, 18), (18, 19)]
    assert timeline["slots"][0]["clip_id"] == "first"
    assert document["clips"] == [_clip(duration=2)]
    previous = deepcopy(timeline)
    document["rhythm"]["markers"] = [11, 14.8, 16.6]
    assert ensure_timeline(document) == previous


def test_fill_uses_complete_context_preserves_neighbors_and_keeps_alternatives(config, monkeypatch):
    document = _timeline_document()
    document["rhythm"].update(provenance={"track": "track", "passage": document["passage"]}, waveform_start=10, waveform_end=19)
    document["clips"] = [_clip("before", locked=True), _clip("after", start=30)]
    slots = document["music_timeline"]["slots"]
    slots[0]["clip_id"], slots[2]["clip_id"] = "before", "after"
    original = deepcopy(document["clips"])
    rows = [{"unit_id": f"candidate{i}", "film_id": f"film{i}", "t_start": 40., "t_end": 46., "caption": f"Sunlight {i}"} for i in range(9)]
    monkeypatch.setattr(music, "retrieve_edit_candidates", lambda *a: rows)
    payloads = []
    def choose(_config, prompt, *_args, **_kwargs):
        payload = json.loads(prompt.split("\n", 1)[1]); payloads.append(payload)
        return selector_response([{"slot": 1, "candidate_id": "candidate7", "source_start": 41., "reason": "Connects the quiet opening to the bright ending"}], [row["unit_id"] for row in rows])
    monkeypatch.setattr(music, "_hosted_json", choose)
    result = music.make_draft(document, config, object(), lambda _: None, "job")
    assert result["clips"][:2] == original
    assert len(result["clips"]) == 3
    assert result["music_timeline"]["slots"][1]["end"] == 16
    alternatives = result["music_timeline"]["slots"][1]["alternatives"]
    assert len(alternatives) == 6 and alternatives[0]["clip"]["unit_id"] == "candidate7"
    assert all(40 <= alt["clip"]["source_start"] < alt["clip"]["source_end"] <= 46 for alt in alternatives)
    context = payloads[0]
    assert context["timeline"][0]["current_clip"]["id"] == "before"
    assert context["timeline"][2]["current_clip"]["id"] == "after"
    measured = context["music_evidence"]["measured"]
    assert measured["beats"] == document["rhythm"]["beats"]
    assert [row["mean"] for row in measured["relative_rms"]["slots"]] == [.2, .4, .8]
    assert len(context["slots"]) == 1
    ProjectDocument.model_validate(result)
    # Filling again does not silently regenerate successful choices.
    assert music.make_draft(result, config, object(), lambda _: None, "again") == result
    assert len(payloads) == 1


def test_explicit_replacement_keeps_original_when_search_has_no_fitting_source(config, monkeypatch):
    document = _timeline_document()
    document["clips"] = [_clip()]
    slot = document["music_timeline"]["slots"][0]
    slot["clip_id"] = "first"
    monkeypatch.setattr(music, "retrieve_edit_candidates", lambda *a: [])
    hosted = MagicMock(); monkeypatch.setattr(music, "_hosted_json", hosted)
    result = music.make_draft(document, config, object(), lambda _: None, "job", [slot["id"]])
    assert result["clips"] == [_clip()] and slot["clip_id"] == "first"
    assert slot["search_error"] and not slot["alternatives"]
    hosted.assert_not_called()


def test_explicit_replacement_does_not_fill_other_gaps_or_change_cut_positions(config, monkeypatch):
    document = _timeline_document()
    document["clips"] = [_clip()]
    slot = document["music_timeline"]["slots"][0]
    slot["clip_id"] = "first"
    boundaries = [(item["start"], item["end"]) for item in document["music_timeline"]["slots"]]
    monkeypatch.setattr(music, "retrieve_edit_candidates", lambda *a: [{"unit_id": "new", "film_id": "other", "t_start": 1., "t_end": 6., "caption": "Sea"}])
    monkeypatch.setattr(music, "_hosted_json", lambda *a, **k: selector_response([
        {"slot": 0, "candidate_id": "new", "source_start": 2., "reason": "A quieter beginning"}], ["new"]))
    result = music.make_draft(document, config, object(), lambda _: None, "job", [slot["id"]])
    assert len(result["clips"]) == 1 and result["clips"][0]["unit_id"] == "new"
    assert [item["clip_id"] for item in result["music_timeline"]["slots"]][1:] == [None, None]
    assert [(item["start"], item["end"]) for item in result["music_timeline"]["slots"]] == boundaries


def test_each_section_uses_its_supported_facet_and_reuses_identical_searches(config, monkeypatch):
    document = _timeline_document()
    document["analysis"]["segments"][0]["search_facet"] = "mood"
    retrieve = MagicMock(return_value=[]); monkeypatch.setattr(music, "retrieve_edit_candidates", retrieve)
    music.make_draft(document, config, object(), lambda _: None, "job")
    assert retrieve.call_count == 1 and retrieve.call_args.args[-1] == "mood"
    with pytest.raises(ValueError):
        music.EmotionalSegment.model_validate({**document["analysis"]["segments"][0], "search_facet": "composition"})


def test_typed_recipe_edit_policy_does_not_turn_off_junk_filter(config, monkeypatch):
    from pipeline.search import recipe
    called = MagicMock(return_value=[]); monkeypatch.setattr(recipe, "search_recipe", called)
    music.retrieve_edit_candidates("lonely", object(), config, [], "mood")
    assert called.call_args.args[0][0].facet == "mood"
    assert called.call_args.kwargs["_preserve_visual_alternatives"] is True


def test_recipe_edit_preferences_keep_close_visual_alternatives_but_reject_credits(config):
    from pipeline.search.retrieve import apply_recipe_result_preferences
    from pipeline.tests.test_retrieve import _basis_vec, _make_hybrid_mock_db, _make_unit_row
    rows = [
        _make_unit_row("a", "one", caption="A red car on a road", searchable_text="red car road", img_vec=_basis_vec(0)),
        _make_unit_row("b", "two", caption="A very similar red car", searchable_text="car road", img_vec=_basis_vec(0)),
        _make_unit_row("credits", "three", caption="End credits scroll on a black background", searchable_text="end credits", img_vec=_basis_vec(1)),
    ]
    database = _make_hybrid_mock_db(image_rows=rows, text_rows=rows, lexical_rows=rows)
    assert {row["unit_id"] for row in apply_recipe_result_preferences(rows, database, config, requested_text="road")} == {"a"}
    edited = apply_recipe_result_preferences(rows, database, config, requested_text="road", _preserve_visual_alternatives=True)
    assert {row["unit_id"] for row in edited} == {"a", "b"}


@pytest.mark.parametrize("kind", ["analyze", "render", "match"])
def test_only_draft_accepts_targeted_slots(kind):
    with pytest.raises(ValueError):
        JobRequest(kind=kind, base_revision=1, slot_ids=["slot"])


def test_snapshot_retains_targets_and_locked_timeline_position_is_guarded(store):
    document = _timeline_document()
    document["clips"] = [_clip(locked=True)]
    slots = document["music_timeline"]["slots"]
    slots[0]["clip_id"] = "first"
    with pytest.raises(ValueError, match="Unlock"):
        draft_targets(document, [slots[0]["id"]])
    project = store.create_project("Music", "music-sketch")
    project = store.update_project(project["id"], 1, document)
    store.enqueue("draft", project["id"], 2, slot_ids=[slots[1]["id"]])
    job = store.claim()
    assert job["snapshot"]["slot_ids"] == [slots[1]["id"]]
    proposed = deepcopy(job["document"])
    proposed["music_timeline"]["slots"][0]["clip_id"] = None
    proposed["music_timeline"]["slots"][1]["clip_id"] = "first"
    with pytest.raises(ValueError, match="moved a locked"):
        store.finish(job["id"], document=proposed)
    assert store.get_project(project["id"]) == project


def test_reanalyze_uses_current_brief_and_replaces_section_overrides_without_moving_clips(config, tmp_path, monkeypatch):
    source = tmp_path / "track.wav"; _audio(source)
    from pipeline.lab.store import LabStore
    lab = LabStore(config.paths.state_dir); lab.initialize()
    identity = music.content_hash(source); lab.add_track(identity, "Music", 6, source)
    passage = {"start": 0., "end": 6.}
    analysis = {**_interpretation(), "provenance": {"track": identity, "passage": passage}}
    analysis["segments"][0].update(query="my handwritten image", search_facet="look")
    document = ProjectDocument(track={"id": identity, "name": "Music", "duration": 6}, passage=passage,
                               analysis=analysis, brief="A darker new direction", clips=[_clip("kept", duration=3)]).model_dump()
    ensure_timeline(document); previous = deepcopy(document["music_timeline"])
    replacement = _interpretation()
    replacement["segments"][0].update(query="storm over an empty city", search_facet="scene")
    interpreter = MagicMock(return_value=replacement); monkeypatch.setattr(music, "interpret_audio", interpreter)
    monkeypatch.setattr(music, "local_rhythm", lambda *a: {"markers": [1, 4], "beats": [1, 2, 3, 4, 5]})
    result = music.run_music_job({"id": "j", "kind": "analyze", "document": document}, config, object(), lambda _: None)
    interpreter.assert_called_once()
    assert interpreter.call_args.args[4] == "A darker new direction"
    assert result["analysis"]["segments"][0]["query"] == "storm over an empty city"
    assert result["analysis"]["segments"][0]["search_facet"] == "scene"
    assert result["music_timeline"] == previous
    assert result["clips"] == document["clips"]


def test_draft_without_current_interpretation_fails_before_implicit_listening(config, tmp_path, monkeypatch):
    from pipeline.lab.store import LabStore
    source = tmp_path / "track.wav"; _audio(source)
    lab = LabStore(config.paths.state_dir); lab.initialize()
    identity = music.content_hash(source); lab.add_track(identity, "Music", 6, source)
    document = ProjectDocument(track={"id": identity, "name": "Music", "duration": 6}, passage={"start": 0, "end": 6}).model_dump()
    interpreter = MagicMock(); monkeypatch.setattr(music, "interpret_audio", interpreter)
    decoder = MagicMock(); monkeypatch.setattr(music, "_decode_audio", decoder)
    with pytest.raises(ValueError, match="Analyze this music passage"):
        music.run_music_job({"id": "j", "kind": "draft", "document": document}, config, object(), lambda _: None)
    interpreter.assert_not_called()
    decoder.assert_not_called()


def test_first_listen_assigns_section_context_to_an_existing_manual_timeline(config, tmp_path, monkeypatch):
    from pipeline.lab.store import LabStore
    source = tmp_path / "track.wav"; _audio(source)
    lab = LabStore(config.paths.state_dir); lab.initialize()
    identity = music.content_hash(source); lab.add_track(identity, "Music", 6, source)
    document = ProjectDocument(track={"id": identity, "name": "Music", "duration": 6}, passage={"start": 0, "end": 6},
                               rhythm={"markers": [3], "marker_source": "user"}).model_dump()
    ensure_timeline(document)
    timings = [(slot["start"], slot["end"]) for slot in document["music_timeline"]["slots"]]
    analysis = _interpretation(); analysis["segments"] = [_interpretation(0, 3)["segments"][0], _interpretation(3, 6)["segments"][0]]
    monkeypatch.setattr(music, "interpret_audio", lambda *a: analysis)
    monkeypatch.setattr(music, "local_rhythm", lambda *a: {"markers": [1, 2, 4, 5]})
    result = music.run_music_job({"id": "j", "kind": "analyze", "document": document}, config, object(), lambda _: None)
    assert [slot["section_index"] for slot in result["music_timeline"]["slots"]] == [0, 1]
    assert [(slot["start"], slot["end"]) for slot in result["music_timeline"]["slots"]] == timings


def test_real_preview_preserves_middle_gap_and_slot_order_export_requires_fill(config, store, db, tmp_path):
    video, audio = _fixture_media(tmp_path)
    track = import_track(store, audio, "melody.wav")
    db.open_table("films").add([{"film_id": "film", "title": "Film", "path": str(video), "duration": 3., "fps": 30.}])
    document = ProjectDocument(track={key: track[key] for key in ("id", "name", "duration")},
                               passage={"start": .5, "end": 2}, clips=[_clip("green", 1.25, .5), _clip("red", .25, .5),
                               {**_clip("unplaced"), "film_id": "unavailable-saved-film"}]).model_dump()
    document["music_timeline"] = {"track_id": track["id"], "passage": document["passage"], "slots": [
        {"id": "a", "start": .5, "end": 1., "section_index": 0, "clip_id": "red"},
        {"id": "gap", "start": 1., "end": 1.5, "section_index": 0},
        {"id": "b", "start": 1.5, "end": 2., "section_index": 0, "clip_id": "green"}]}
    manifest = render_manifest(document, db, store)
    assert manifest["profile"] == "decoded-reel-shared-voice-mix-v6"
    assert [item["id"] for item in manifest["clips"]] == ["red", "gap", "green"]
    assert [item["frame_count"] for item in manifest["clips"]] == [12, 12, 12]
    with pytest.raises(ValueError, match="Fill every"):
        render_manifest(document, db, store, mode="export")
    project = store.create_project("Gaps", "music-sketch")
    project = store.update_project(project["id"], 1, document)
    store.enqueue("render", project["id"], 2)
    job = store.claim(); render_reel(job, config, db, store, lambda _: None)
    output = config.paths.assets_dir / "lab" / "renders" / job["id"] / "output.mp4"
    stream = next(item for item in probe_media(output)["streams"] if item["codec_type"] == "video")
    assert int(stream["nb_frames"]) == 36
    def pixel(frame):
        raw = run_process(["ffmpeg", "-v", "error", "-i", str(output), "-vf", f"select=eq(n\\,{frame})", "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "pipe:1"])
        return Image.open(BytesIO(raw)).convert("RGB").getpixel((320, 360))
    assert pixel(0)[0] > 220
    assert max(pixel(12)) < 10
    assert pixel(24)[1] > 100 and pixel(24)[0] < 20
    assert music.content_hash(Path(track["path"])) == track["id"]


def test_api_save_validates_only_changed_sources_without_breaking_slot_references(config, db, tmp_path):
    from pipeline.api.main import app
    audio = tmp_path / "audio.wav"; _audio(audio)
    db.open_table("films").add([{"film_id": "film", "title": "Film", "path": "missing.mkv", "duration": 100., "fps": 24.}])
    with patch("pipeline.api.main.load_config", return_value=config), patch("pipeline.api.main.open_db", return_value=db), patch("pipeline.api.main.ensure_search_indexes"), TestClient(app) as client:
        project = client.post("/lab/projects", json={"name": "Timeline"}).json(); url = f"/lab/projects/{project['id']}"
        project = client.post(url + "/track", data={"base_revision": 1}, files={"file": ("audio.wav", audio.read_bytes())}).json()
        document = project["document"]; document["clips"] = [_clip()]
        ensure_timeline(document)
        saved = client.put(url, json={"base_revision": 2, "document": document})
        assert saved.status_code == 200, saved.text
        db.open_table("films").delete("film_id = 'film'")
        document["brief"] = "Keep the selected source while its index is offline"
        saved = client.put(url, json={"base_revision": 3, "document": document})
        assert saved.status_code == 200, saved.text
        assert saved.json()["document"]["music_timeline"]["slots"][0]["clip_id"] == "first"
