"""Music-led timing is scoped, bounded, inspectable and independent of retrieval."""
from copy import deepcopy
from dataclasses import replace
import json

import pytest

from pipeline.lab import music, timing_planner
from pipeline.lab.models import ProjectDocument


def _document(*, start=53.99, duration=90.):
    passage = {"start": start, "end": start + duration}
    document = ProjectDocument(track={"id": "track", "name": "Piano", "duration": start + duration},
                               passage=passage, planner_settings={"pacing": "rapid"}).model_dump(mode="json")
    provenance = {"track": "track", "passage": deepcopy(passage), "model": "audio-model"}
    document["analysis"] = {
        "summary": "Quiet piano develops into quick articulation, then a sustained release.",
        "segments": [{**passage, "feeling": "Reflective", "energy": .3,
                      "imagery": "PRIOR_IMAGE", "query": "PRIOR_SEARCH"}],
        "edit_beats": [{**passage, "query": "PRIOR_EDIT"}], "events": [],
        "provenance": provenance,
        "events_provenance": {**deepcopy(provenance), "source": "ai-observed", "interpretation_id": music.digest(provenance)},
    }
    document["rhythm"] = {"provenance": deepcopy(provenance), "beats": [start + duration / 4],
                           "downbeats": [start], "markers": [start + duration / 2], "marker_source": "derived",
                           "intensity": [.1, .2, .8, .2], "waveform_start": start, "waveform_end": start + duration}
    return document


def _job(document):
    return {"id": "timing-test", "document": document}


def _output(end_frames, *, evidence_ids=None):
    return {"end_frames": end_frames,
            "notes": [{"start_frame": 0, "end_frame": end_frames[-1],
                       "reason": "Allow sustained images between short visual answers.",
                       "evidence_ids": evidence_ids or []}]}


def test_timing_input_excludes_previous_imagery_and_converts_landmarks(config, monkeypatch):
    document = _document()
    document["visual_plan"] = {"source": "ai", "arc": "PRIOR_ARC", "motifs": "PRIOR_MOTIF"}
    document["analysis"]["provenance"]["old_request"] = {"query": "PRIOR_CACHED_IMAGE"}
    document["analysis"]["events"] = [{"id": "phrase", "start": 63.99, "end": 65.99,
        "label": "Piano articulation quickens", "kind": "phrase", "confidence": "medium"}]
    calls = []
    def hosted(_config, prompt, schema, **kwargs):
        payload = json.loads(prompt.split("\n", 1)[1]); calls.append(payload)
        assert "PRIOR_" not in prompt
        assert kwargs["operation"] == "timing" and "audio_path" not in kwargs
        assert schema["properties"]["end_frames"]["maxItems"] == 300
        assert "target_shots" not in prompt and "min_shots" not in prompt
        return _output([360, 372, 384, 1200, 2160], evidence_ids=["event:phrase", "beats:0"])
    monkeypatch.setattr(music, "_hosted_json", hosted)
    original = deepcopy(document)
    result = timing_planner.run_timing_job(_job(document), config, lambda _: None)
    payload = calls[0]
    assert payload["music"]["events"][0]["start_frame"] == 240
    assert payload["music"]["events"][0]["end_frame"] == 288
    assert payload["music"]["measured"]["beats"]["frames"] == [540]
    assert payload["music"]["measured"]["relative_rms"]["windows"][-1]["end_frame"] == 2160
    assert payload["music"]["provenance"]["analysis"]["passage"] == document["passage"]
    assert document == original
    slots = result["music_timeline"]["slots"]
    assert [round((s["end"] - document["passage"]["start"]) * 24) for s in slots] == [360, 372, 384, 1200, 2160]
    assert all(s["needs_direction"] and s["direction"] is None and s["clip_id"] is None for s in slots)


def test_absent_events_allow_honest_editorial_notes_without_wording_failure(config, monkeypatch):
    monkeypatch.setattr(music, "_hosted_json", lambda *_a, **_k: _output([2160]))
    result = timing_planner.run_timing_job(_job(_document()), config, lambda _: None)
    receipt = result["direction_plan"]["timing_plan"]
    assert receipt["notes"][0]["reason"].startswith("Editorial choice: ")
    assert receipt["notes"][0]["evidence_ids"] == []
    assert receipt["nominal_timing"]["selected_shots"] == 1
    assert "final_timing" not in receipt


def test_cache_reuses_timing_without_library_dependence_or_slot_id_reuse(config, monkeypatch):
    calls = []
    monkeypatch.setattr(music, "_hosted_json", lambda *_a, **_k: calls.append(True) or _output([720, 2160]))
    document = _document()
    first = timing_planner.run_timing_job(_job(document), config, lambda _: None)
    changed = deepcopy(document)
    changed.update(film_ids=["other-library-film"], visual_plan={"source": "ai", "arc": "Different scenes", "motifs": "Other props"})
    changed["analysis"]["segments"][0].update(imagery="Other image", query="Other search")
    changed["analysis"]["edit_beats"] = []
    second = timing_planner.run_timing_job(_job(changed), config, lambda _: None)
    assert len(calls) == 1
    assert second["direction_plan"]["timing_plan"]["cache_reused"] is True
    assert {s["id"] for s in first["music_timeline"]["slots"]}.isdisjoint(s["id"] for s in second["music_timeline"]["slots"])


@pytest.mark.parametrize("change", ["brief", "preference", "context", "user-plan", "music", "pulse", "model", "settings", "prompt"])
def test_cache_invalidates_for_actual_timing_dependencies(config, monkeypatch, change):
    calls = []
    monkeypatch.setattr(music, "_hosted_json", lambda *_a, **_k: calls.append(True) or _output([2160]))
    document = _document()
    timing_planner.run_timing_job(_job(document), config, lambda _: None)
    if change == "brief": document["brief"] = "Let the middle settle."
    elif change == "preference": document["planner_settings"]["pacing"] = "patient"
    elif change == "context": document["song_context"] = {"track_id": "track", "notes": "Follow the piano runs.", "lyrics": []}
    elif change == "user-plan": document["visual_plan"] = {"source": "user", "arc": "An unbroken ending", "motifs": "Movement"}
    elif change == "music": document["analysis"]["summary"] = "Sustained piano without a run."
    elif change == "pulse": document["rhythm"]["beats"].append(100.)
    elif change == "model": config = replace(config, lab=replace(config.lab, planner_model="other-model"))
    elif change == "settings": monkeypatch.setattr(music, "PLANNER_SETTINGS", {**music.PLANNER_SETTINGS, "reasoning_effort": "medium"})
    elif change == "prompt": config = replace(config, lab=replace(config.lab, planner_prompt_version="new-version"))
    timing_planner.run_timing_job(_job(document), config, lambda _: None)
    assert len(calls) == 2


@pytest.mark.parametrize("frames", [[0, 2160], [True, 2160], [1.5, 2160], [20, 20, 2160], [2161], [2159], []])
def test_invalid_cuts_are_not_repaired_retried_cached_or_applied(config, monkeypatch, frames):
    document = _document()
    original = deepcopy(document)
    calls = []
    output = _output([2160]); output["end_frames"] = frames
    monkeypatch.setattr(music, "_hosted_json", lambda *_a, **_k: calls.append(True) or output)
    with pytest.raises(ValueError):
        timing_planner.run_timing_job(_job(document), config, lambda _: None)
    assert document == original and len(calls) == 1
    assert not list((config.paths.assets_dir / "lab" / "timing-plans").glob("*.json"))


@pytest.mark.parametrize("issue", ["unknown-evidence", "outside", "empty-range", "too-many-notes"])
def test_notes_must_be_scoped_and_reference_offered_evidence(config, monkeypatch, issue):
    output = _output([2160])
    if issue == "unknown-evidence": output["notes"][0]["evidence_ids"] = ["event:invented"]
    elif issue == "outside": output["notes"][0]["end_frame"] = 2161
    elif issue == "empty-range": output["notes"][0]["start_frame"] = 2160
    elif issue == "too-many-notes": output["notes"] *= 33
    monkeypatch.setattr(music, "_hosted_json", lambda *_a, **_k: output)
    with pytest.raises(ValueError):
        timing_planner.run_timing_job(_job(_document()), config, lambda _: None)
    assert not list((config.paths.assets_dir / "lab" / "timing-plans").glob("*.json"))


@pytest.mark.parametrize("count", [300, 301])
def test_entire_ten_minute_passage_uses_all_300_positions(config, monkeypatch, count):
    document = _document(start=0, duration=600)
    output = _output([round(14400 * (index + 1) / count) for index in range(count)])
    monkeypatch.setattr(music, "_hosted_json", lambda *_a, **_k: output)
    if count == 301:
        with pytest.raises(ValueError): timing_planner.run_timing_job(_job(document), config, lambda _: None)
    else:
        result = timing_planner.run_timing_job(_job(document), config, lambda _: None)
        assert len(result["music_timeline"]["slots"]) == 300
        assert result["music_timeline"]["slots"][-1]["end"] == 600


def test_exact_subframe_endpoint_and_long_hold_survive_without_invented_cuts(config, monkeypatch):
    document = _document(start=9.94, duration=116.610612)
    monkeypatch.setattr(music, "_hosted_json", lambda *_a, **_k: _output([2400, 2799]))
    result = timing_planner.run_timing_job(_job(document), config, lambda _: None)
    slots = result["music_timeline"]["slots"]
    assert len(slots) == 2 and slots[0]["end"] == 109.94
    assert slots[-1]["end"] == document["passage"]["end"]


def test_rejects_existing_arrangement_and_stale_analysis_before_models(config, monkeypatch):
    document = _document()
    monkeypatch.setattr(music, "_hosted_json", lambda *_a, **_k: pytest.fail("Must validate before hosted work"))
    document["music_timeline"] = {"track_id": "track", "passage": document["passage"],
                                   "slots": [{"id": "manual", **document["passage"], "section_index": 0}]}
    with pytest.raises(ValueError, match="private empty"):
        timing_planner.run_timing_job(_job(document), config, lambda _: None)
    document["music_timeline"] = None
    document["analysis"]["provenance"]["track"] = "other"
    with pytest.raises(ValueError, match="Current scoped"):
        timing_planner.run_timing_job(_job(document), config, lambda _: None)


def test_meaning_and_user_lyric_ranges_are_scoped_and_citable(config, monkeypatch):
    document = _document()
    document["song_context"] = {"track_id": "track", "notes": "Hold across this line.",
        "lyrics": [{"id": "line", "start": 50., "end": 64., "text": "Supplied words", "meaning": "Requested meaning"}]}
    document["analysis"]["song_meaning"] = {
        "vocal_status": "partly_understood", "summary": "A remembered relationship.", "themes": ["memory"],
        "cues": [{"start": 63.99, "end": 65.99, "paraphrase": "Remembering someone", "confidence": "low"}],
        "uncertainty": "Some words are unclear.",
    }
    document["analysis"]["song_meaning_provenance"] = {
        **deepcopy(document["analysis"]["provenance"]), "source": "ai-heard-paraphrase"}
    payload = timing_planner.timing_payload(document)
    lyric = payload["song_context"]["lyrics"][0]
    cue = payload["music"]["song_meaning"]["cues"][0]
    assert (lyric["start_frame"], lyric["end_frame"]) == (0, 240)
    assert (cue["start_frame"], cue["end_frame"]) == (240, 288)
    assert "start" not in lyric and "end" not in cue
    monkeypatch.setattr(music, "_hosted_json", lambda *_a, **_k: _output([2160], evidence_ids=["supplied:line", "meaning:0"]))
    result = timing_planner.run_timing_job(_job(document), config, lambda _: None)
    assert result["direction_plan"]["timing_plan"]["notes"][0]["evidence_ids"] == ["supplied:line", "meaning:0"]


def test_cached_output_is_revalidated_before_it_can_create_slots(config, monkeypatch):
    document = _document()
    monkeypatch.setattr(music, "_hosted_json", lambda *_a, **_k: _output([2160]))
    timing_planner.run_timing_job(_job(document), config, lambda _: None)
    cache, = (config.paths.assets_dir / "lab" / "timing-plans").glob("*.json")
    artifact = json.loads(cache.read_text(encoding="utf-8"))
    artifact["output"]["end_frames"] = [2161]
    cache.write_text(json.dumps(artifact), encoding="utf-8")
    monkeypatch.setattr(music, "_hosted_json", lambda *_a, **_k: pytest.fail("An invalid cache must not trigger silent regeneration"))
    before = deepcopy(document)
    with pytest.raises(ValueError, match="at most 2160"):
        timing_planner.run_timing_job(_job(document), config, lambda _: None)
    assert document == before
