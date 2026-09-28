"""Music jobs: grounding, preservation, cache lineage and actual audio decoding."""
from __future__ import annotations

import copy
import json
from pathlib import Path
from unittest.mock import MagicMock
import wave

import numpy as np
import pytest

from pipeline.lab import music
from pipeline.lab.models import ProjectDocument
from pipeline.lab.store import LabStore
from pipeline.tests.selection_helpers import selector_response


def _audio(path, seconds=6):
    path.parent.mkdir(parents=True, exist_ok=True)
    samples = (np.sin(np.arange(22050 * seconds) * 2 * np.pi * 220 / 22050) * 10000).astype("<i2")
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(22050)
        audio.writeframes(samples.tobytes())


def _interpretation(start=0, end=6):
    return {"summary": "quiet to hopeful", "segments": [{"start": start, "end": end, "feeling": "hopeful",
            "imagery": "sunlight through leaves", "query": "sunlight through leaves", "energy": 0.5}]}


def _audio_interpretation(start=0, end=6):
    return {**_interpretation(start, end), "events": [],
        "song_meaning": {"vocal_status": "no_vocals", "summary": "Instrumental tone; no vocal meaning is available.",
                         "themes": [], "cues": [], "uncertainty": ""},
        "edit_beats": [{"start": start, "end": end,
        "query": "sunlight through leaves", "search_facet": "all", "purpose": "Hold an image of gradual hope",
        "music_cue": "A sustained tone opens into a brighter texture", "timing_note": "Hold through the sustained phrase"}]}


def test_decode_real_selected_audio_and_waveform(config, tmp_path):
    source, target = tmp_path / "source.wav", tmp_path / "passage.wav"
    _audio(source)
    signal, rate = music._decode_audio(source, target, 1.25, 4.5)
    assert abs(len(signal) / rate - 3.25) < 1 / rate
    rhythm = music.local_rhythm(target, signal, rate, music.content_hash(source), {"start": 1.25, "end": 4.5}, config)
    assert len(rhythm["waveform"]) == 720
    assert all(0 <= value <= 1 for value in rhythm["waveform"])
    assert rhythm["markers"] == [] and rhythm["warning"]
    assert rhythm["waveform_start"] == 1.25


def test_interpretation_cached_by_audio_range_and_model_not_legacy_brief(config, monkeypatch):
    call = MagicMock(return_value=_audio_interpretation())
    monkeypatch.setattr(music, "_hosted_json", call)
    passage = {"start": 12.0, "end": 18.0}
    first = music.interpret_audio(config, "audio-hash", passage, Path("unused"), "hope", "j1")
    assert first["segments"][0]["start"] == 12
    assert first["segments"][0]["end"] == 18
    assert music.interpret_audio(config, "audio-hash", passage, Path("unused"), "hope", "j2") == first
    assert call.call_count == 1
    music.interpret_audio(config, "audio-hash", passage, Path("unused"), "sorrow", "j3")
    assert call.call_count == 1
    config.lab.music_model = "different-version"
    music.interpret_audio(config, "audio-hash", passage, Path("unused"), "sorrow", "j4")
    assert call.call_count == 2


def test_changed_pacing_reuses_independent_listening_cache(config, monkeypatch):
    from pipeline.lab.music_evidence import music_evidence
    calls = []
    def listen(_config, prompt, *_args, **_kwargs):
        evidence = json.loads(prompt.split('\n', 1)[1])["music_evidence"]
        calls.append(evidence)
        return _audio_interpretation()
    monkeypatch.setattr(music, "_hosted_json", listen)
    document = ProjectDocument(track={"id": "audio-hash", "name": "Music", "duration": 6}, passage={"start": 0, "end": 6}).model_dump(mode="json")
    def interpret(identity):
        return music.interpret_audio(config, "audio-hash", document["passage"], Path("unused"), "", identity,
                                     evidence=music_evidence(document, include_analysis=False))
    balanced = interpret("balanced")
    document["planner_settings"]["pacing"] = "kinetic"
    energetic = interpret("energetic")
    assert balanced == energetic
    assert interpret("same-settings") == energetic
    assert len(calls) == 1
    assert "planner_settings" not in calls[0]


def test_interpretation_rejects_outside_excerpt(config, monkeypatch):
    monkeypatch.setattr(music, "_hosted_json", lambda *a, **k: _audio_interpretation(0, 8))
    with pytest.raises(ValueError, match="out-of-range"):
        music.interpret_audio(config, "h", {"start": 0, "end": 6}, Path("unused"), "", "j")


def test_ground_choices_rejects_invented_sources_and_trim_overflow():
    slots = [{"slot": 0, "duration": 3, "locked": None, "candidate_ids": ["a"]}]
    candidates = {"a": {"film_id": "film", "unit_id": "a", "t_start": 20, "t_end": 24, "caption": "trees"}}
    for identity, start in [("invented", 20), ("a", 22), ("a", 19)]:
        with pytest.raises(ValueError):
            music.ground_choices({"choices": [{"slot": 0, "candidate_id": identity, "source_start": start, "reason": "trees"}]}, slots, candidates)
    clips, reasons = music.ground_choices({"choices": [{"slot": 0, "candidate_id": "a", "source_start": 20.5, "reason": "trees"}]}, slots, candidates)
    assert clips[0]["source_start"] == 20.5 and clips[0]["source_end"] == 23.5
    assert reasons[clips[0]["id"]] == "trees"


def test_slots_and_grounding_preserve_locked_clip_position_range_and_crop():
    document = ProjectDocument(passage={"start": 0, "end": 9}, clips=[
        {"id": "u", "film_id": "f", "source_start": 10, "source_end": 13},
        {"id": "lock", "film_id": "f", "source_start": 30.1, "source_end": 33.6, "locked": True,
         "crop": {"x": 0.1, "y": 0.1, "width": 0.8, "height": 0.8}}
    ]).model_dump()
    original = copy.deepcopy(document["clips"][1])
    slots = music._slots(document)
    assert slots[1]["start"] == 3 and slots[1]["duration"] == 3.5
    candidates = {"a": {"film_id": "f", "unit_id": "a", "t_start": 0, "t_end": 10}}
    choices = []
    for slot in slots:
        if not slot["locked"]:
            slot["candidate_ids"] = ["a"]
            choices.append({"slot": slot["slot"], "candidate_id": "a", "source_start": 0, "reason": "light"})
    clips, _ = music.ground_choices({"choices": choices}, slots, candidates)
    assert clips[1] == original
    assert sum(clip["source_end"] - clip["source_start"] for clip in clips) == pytest.approx(9)


def test_user_timing_markers_used_instead_of_every_beat():
    document = ProjectDocument(passage={"start": 10, "end": 19}, rhythm={"markers": [13.4, 16.6]}).model_dump()
    slots = music._slots(document)
    assert slots[0]["duration"] == pytest.approx(82 / 24)
    assert sum(slot["duration"] for slot in slots) == pytest.approx(9)


def test_candidate_retrieval_preserves_visual_alternatives_without_disabling_junk(config, monkeypatch):
    from pipeline.search import retrieve
    call = MagicMock(return_value=[])
    monkeypatch.setattr(retrieve, "search", call)
    music.retrieve_edit_candidates("hope", object(), config, [])
    assert call.call_args.kwargs["_preserve_visual_alternatives"] is True
    assert "_defer_result_preferences" not in call.call_args.kwargs


def test_edit_search_keeps_near_matches_but_filters_titles(config, monkeypatch):
    from pipeline.search import retrieve
    from pipeline.tests.test_retrieve import _basis_vec, _fake_vec, _make_hybrid_mock_db, _make_unit_row
    rows = [
        _make_unit_row("a", "one", caption="A red car on a road", searchable_text="red car road", img_vec=_basis_vec(0)),
        _make_unit_row("b", "two", caption="A very similar red car", searchable_text="car road", img_vec=_basis_vec(0)),
        _make_unit_row("credits", "three", caption="End credits scroll on a black background", searchable_text="end credits", img_vec=_basis_vec(1)),
    ]
    db = _make_hybrid_mock_db(image_rows=rows, text_rows=rows, lexical_rows=rows)
    monkeypatch.setattr(retrieve, "embed_text", lambda *a, **k: _fake_vec())
    default = retrieve.search("road", db, config)
    edit = music.retrieve_edit_candidates("road", db, config, [])
    assert {row["unit_id"] for row in default} == {"a"}
    assert {row["unit_id"] for row in edit} == {"a", "b"}


def test_draft_job_uses_current_editable_analysis_and_grounded_sources(config, tmp_path, monkeypatch):
    source = tmp_path / "original.wav"
    _audio(source)
    store = LabStore(config.paths.state_dir)
    store.initialize()
    track_id = music.content_hash(source)
    store.add_track(track_id, "music", 6, source)
    passage = {"start": 0.0, "end": 6.0}
    analysis = {**_interpretation(), "provenance": {"track": track_id, "passage": passage}}
    document = ProjectDocument(track={"id": track_id, "name": "music", "duration": 6}, passage=passage,
                               analysis=analysis, rhythm={"provenance": {"track": track_id, "passage": passage}, "markers": [3], "marker_source": "user"}).model_dump()
    monkeypatch.setattr(music, "retrieve_edit_candidates", lambda *a: [
        {"unit_id": f"a{n}", "film_id": "f", "t_start": 20.0 + n * 10, "t_end": 28.0 + n * 10, "caption": "warm light"}
        for n in range(2)])
    hosted = MagicMock(return_value=selector_response([
        {"slot": n, "candidate_id": f"a{n}", "source_start": 20.0 + n * 10, "reason": "warmth"} for n in range(2)], ["a0", "a1"]))
    monkeypatch.setattr(music, "_hosted_json", hosted)
    result = music.run_music_job({"id": "job", "kind": "draft", "document": document}, config, object(), lambda _: None)
    assert len(result["clips"]) == 2
    assert all(clip["film_id"] == "f" for clip in result["clips"])
    assert hosted.call_count == 1  # preserves editable analysis; does not pay to listen again


@pytest.mark.parametrize("markers", [[1.5, 4.5], []])
def test_local_analysis_preserves_explicit_user_markers_including_clear(config, tmp_path, monkeypatch, markers):
    source = tmp_path / "original.wav"
    _audio(source)
    store = LabStore(config.paths.state_dir)
    store.initialize()
    track_id = music.content_hash(source)
    store.add_track(track_id, "music", 6, source)
    passage = {"start": 0.0, "end": 6.0}
    document = ProjectDocument(track={"id": track_id, "name": "music", "duration": 6}, passage=passage,
                               rhythm={"markers": markers, "marker_source": "user", "track_id": track_id, "passage": passage}).model_dump()
    monkeypatch.setattr(music, "local_rhythm", lambda *a: {"markers": [0.5, 2.5, 4.5], "beats": [0.5, 2.5, 4.5]})
    monkeypatch.setattr(music, "interpret_audio", lambda *a: _interpretation())
    result = music.run_music_job({"id": "job", "kind": "analyze", "document": document}, config, object(), lambda _: None)
    assert result["rhythm"]["markers"] == markers
    assert result["rhythm"]["marker_source"] == "user"
    assert result["rhythm"]["beats"] == [0.5, 2.5, 4.5]


def test_hosted_request_sends_audio_without_storage_and_never_retries(config, tmp_path, monkeypatch):
    import httpx
    from google import genai
    config.lab.music_provider = "gemini"
    config.lab.music_model = "gemini-3.8-flash"
    attempts = []
    def handle(request):
        attempts.append(json.loads(request.content))
        return httpx.Response(503, json={"error": {"code": 503, "message": "busy", "status": "UNAVAILABLE"}})
    actual_client = genai.Client
    def client(**kwargs):
        kwargs["http_options"].client_args = {"transport": httpx.MockTransport(handle)}
        return actual_client(**kwargs)
    monkeypatch.setenv("GEMINI_API_KEY", "fake-test-key")
    monkeypatch.setattr(genai, "Client", client)
    source = tmp_path / "short.wav"
    _audio(source, 1)
    receipt = tmp_path / "receipt.json"
    with pytest.raises(Exception):
        music._hosted_json(config, "listen", music.Interpretation.model_json_schema(), audio_path=source, receipt_path=receipt)
    assert len(attempts) == 1
    assert attempts[0]["store"] is False
    assert attempts[0]["response_format"]["schema"]["properties"]["segments"]
    audio = attempts[0]["input"][0]["content"][1]
    assert audio["type"] == "audio" and audio["data"]
    assert json.loads(receipt.read_text())["status"] == "failed-or-uncertain"


@pytest.mark.parametrize("spans", [
    [(1, 6)], [(0, 5)], [(0, 2), (3, 6)], [(0, 4), (3, 6)],
    [(0, 6), (6, 6.01)],
])
def test_interpretation_requires_full_ordered_passage_coverage(spans):
    analysis = _interpretation()
    analysis["segments"] = [{**analysis["segments"][0], "start": start, "end": end} for start, end in spans]
    with pytest.raises(ValueError, match="overlapping|uncovered|out-of-range"):
        music.validate_interpretation(analysis, {"start": 0, "end": 6})


def test_provider_rounding_is_snapped_without_gaps(config, monkeypatch):
    analysis = _audio_interpretation()
    first = analysis["segments"][0]
    analysis["segments"] = [{**first, "start": .02, "end": 2.999}, {**first, "start": 3.02, "end": 5.98}]
    monkeypatch.setattr(music, "_hosted_json", lambda *a, **k: analysis)
    result = music.interpret_audio(config, "h", {"start": 10, "end": 16}, Path("unused"), "", "job")
    assert result["segments"][0]["start"] == 10
    assert result["segments"][0]["end"] == result["segments"][1]["start"]
    assert result["segments"][1]["end"] == 16


def test_invalid_reused_editable_analysis_fails_before_paid_draft(config, tmp_path, monkeypatch):
    source = tmp_path / "music.wav"
    _audio(source)
    store = LabStore(config.paths.state_dir)
    store.initialize()
    identity = music.content_hash(source)
    store.add_track(identity, "Music", 6, source)
    passage = {"start": 0, "end": 6}
    analysis = {**_interpretation(2, 6), "provenance": {"track": identity, "passage": passage}}
    doc = ProjectDocument(track={"id": identity, "name": "Music", "duration": 6}, passage=passage,
                          analysis=analysis, rhythm={"provenance": {"track": identity, "passage": passage}}).model_dump()
    provider = MagicMock()
    monkeypatch.setattr(music, "_hosted_json", provider)
    with pytest.raises(ValueError, match="uncovered"):
        music.run_music_job({"id": "j", "kind": "draft", "document": doc}, config, object(), lambda _: None)
    provider.assert_not_called()


def test_draft_uses_overlapping_emotion_instead_of_nearest_segment_center(config, monkeypatch):
    analysis = _interpretation(0, 80)
    first = analysis["segments"][0]
    analysis["segments"] = [{**first, "query": "slow imagery"}, {**first, "start": 80, "end": 90, "query": "bright imagery"}]
    document = ProjectDocument(passage={"start": 0, "end": 90}, analysis=analysis).model_dump()
    monkeypatch.setattr(music, "_slots", lambda _: [{"slot": 0, "start": 74, "duration": 3, "locked": None}])
    def retrieve(query, *_):
        return [{"unit_id": query, "film_id": "f", "t_start": 0, "t_end": 5, "caption": query}]
    monkeypatch.setattr(music, "retrieve_edit_candidates", retrieve)
    monkeypatch.setattr(music, "_hosted_json", lambda *a, **k: {"choices": [{"slot": 0, "candidate_id": "slow imagery", "source_start": 0, "reason": "Still inside the long section"}]})
    result = music.make_draft(document, config, object(), lambda _: None, "job")
    assert result["clips"][0]["unit_id"] == "slow imagery"


def test_prepare_uses_explicit_checkpoint_without_downloading(config, monkeypatch, tmp_path):
    from pipeline.lab import prepare_music
    config.lab.beat_checkpoint = tmp_path / "custom.ckpt"
    profile = {"sha256": "verified", "checkpoint": str(config.lab.beat_checkpoint)}
    monkeypatch.setattr(music, "_beat_profile", lambda _: profile)
    download = MagicMock()
    monkeypatch.setattr(prepare_music.urllib.request, "urlopen", download)
    assert prepare_music.prepare(config) == profile
    download.assert_not_called()


def test_failed_checkpoint_download_does_not_leave_large_partial_or_activate(config, monkeypatch):
    from pipeline.lab import prepare_music
    from io import BytesIO
    monkeypatch.setattr(prepare_music.urllib.request, "urlopen", lambda *a, **k: BytesIO(b"not a checkpoint"))
    with pytest.raises(ValueError, match="unexpectedly small"):
        prepare_music.prepare(config)
    root = config.paths.assets_dir / "lab" / "beat-this"
    assert not list(root.glob("*.download"))
    assert not (root / "profile.json").exists()
