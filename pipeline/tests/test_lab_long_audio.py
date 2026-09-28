"""Whole-song listening composes bounded, independently reusable evidence."""
from copy import deepcopy
import json
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest

from pipeline.lab import music
from pipeline.lab.long_audio import (PARTS_CONTRACT, analysis_for_passage, compose_analysis,
                                     interpret_long_audio, rhythm_for_passage)
from pipeline.lab.long_form import partition_passage
from pipeline.lab.music_evidence import music_evidence
from pipeline.lab.models import ProjectDocument
from pipeline.lab.store import LabStore
from pipeline.lab.song_meaning import SongMeaning
from pipeline.tests.test_lab_music import _audio_interpretation


def _part(start, end, *, count=1, status="understood"):
    scope = {"start": start, "end": end}
    value = _audio_interpretation(start, end)
    value["summary"] = f"An audible progression from {start} to {end}."
    value["segments"] = [{**value["segments"][0], "start": start + i * (end - start) / count,
                           "end": start + (i + 1) * (end - start) / count} for i in range(count)]
    value["events"] = [{"id": f"voice-{i}", "start": start + i * (end - start) / 32,
                        "end": start + (i + 1) * (end - start) / 32,
                        "label": f"Audible entrance {i}", "kind": "voice", "confidence": "medium"} for i in range(32)]
    value["song_meaning"] = {"vocal_status": status, "summary": f"Meaning from {start} to {end}.",
                             "themes": [f"theme {start} {i}" for i in range(6)] if status == "understood" else [],
                             "cues": [{"start": start + i * (end - start) / 8,
                                       "end": start + (i + 1) * (end - start) / 8,
                                       "paraphrase": f"Paraphrase {start} {i}", "confidence": "medium"}
                                      for i in range(8)] if status == "understood" else [], "uncertainty": "Approximate timing."}
    value = music.AudioInterpretation.model_validate(value).model_dump(mode="json")
    provenance = {"track": "song", "passage": deepcopy(scope), "model": "fixture"}
    profile = {**deepcopy(provenance), "interpretation_id": music.digest(provenance)}
    value.update(provenance=provenance, events_provenance={**deepcopy(profile), "source": "ai-observed"},
                 song_meaning_provenance={**deepcopy(profile), "source": "ai-heard-paraphrase"},
                 time_base="source-track-seconds")
    return {"passage": scope, "analysis": value}


def test_full_song_retains_all_bounded_evidence_and_original_parts():
    scope = {"start": 10., "end": 610.}
    parts = [_part(**part, count=8) for part in partition_passage(scope)]
    original = deepcopy(parts)
    result = compose_analysis(parts, scope)
    assert result["parts"] == original == parts
    assert len(result["segments"]) == 56
    assert len(result["events"]) == len({event["id"] for event in result["events"]}) == 224
    assert len(result["song_meaning"]["cues"]) == 56
    assert len(result["song_meaning"]["themes"]) == 42
    assert result["provenance"]["contract"] == PARTS_CONTRACT
    assert music.validate_interpretation(result, scope).model_dump(mode="json")["events"] == result["events"]
    packet = music_evidence({"track": {"id": "song"}, "passage": scope, "analysis": result})
    assert len(packet["audio_observations"]["events"]) == 224
    assert len(packet["song_meaning"]["cues"]) == 56
    assert packet["song_meaning"]["provenance"]["parts"][-1]["passage"]["end"] == 610
    assert music_evidence({"track": {"id": "song"}, "passage": scope, "analysis": result},
                          include_analysis=False)["audio_interpretation"] is None


@pytest.mark.parametrize("mutation", [
    lambda value: value["events"][0].update(label="Invented event"),
    lambda value: value["provenance"].update(track="another"),
    lambda value: value["parts"][1]["analysis"]["provenance"].update(track="another"),
    lambda value: value["parts"][0]["analysis"]["events_provenance"].update(interpretation_id="stale"),
    lambda value: value["parts"].reverse(),
    lambda value: value["song_meaning"]["cues"][0].update(end=601),
])
def test_stale_or_inconsistent_aggregate_is_not_shared_as_audio_evidence(mutation):
    scope = {"start": 0, "end": 180}
    result = compose_analysis([_part(0, 90), _part(90, 180)], scope)
    mutation(result)
    with pytest.raises(ValueError):
        music.validate_interpretation(result, scope)
    packet = music_evidence({"track": {"id": "song"}, "passage": scope, "analysis": result})
    assert packet["audio_interpretation"] is None
    assert packet["audio_observations"] is None
    assert packet["song_meaning"] is None


def test_exact_part_is_unchanged_and_cross_boundary_view_keeps_scoped_evidence():
    parts = [_part(0, 90, count=8), _part(90, 180, count=8)]
    result = compose_analysis(parts, {"start": 0, "end": 180})
    assert analysis_for_passage(result, parts[1]["passage"]) == parts[1]["analysis"]
    scope = {"start": 60, "end": 150}
    sliced = analysis_for_passage(result, scope)
    assert sliced["parts"] == parts
    assert sliced["provenance"]["passage"] == scope
    assert sliced["provenance"]["parts"][0]["passage"] == {"start": 0, "end": 90}
    for collection in (sliced["segments"], sliced["events"], sliced["song_meaning"]["cues"]):
        assert all(60 <= item["start"] < item["end"] <= 150 for item in collection)
    assert sliced["segments"][0]["start"] == 60 and sliced["segments"][-1]["end"] == 150
    packet = music_evidence({"track": {"id": "song"}, "passage": scope, "analysis": sliced})
    assert packet["song_meaning"]["cues"] == sliced["song_meaning"]["cues"]
    assert len(packet["audio_observations"]["events"]) > 32
    with pytest.raises(ValueError, match="inside"):
        analysis_for_passage(result, {"start": 150, "end": 181})


def test_projection_does_not_import_lyric_themes_from_outside_heard_cue():
    part = _part(0, 90)
    part["analysis"]["song_meaning"]["cues"] = [
        {"start": 0, "end": 3, "paraphrase": "A connection is ending.", "confidence": "medium"}]
    result = analysis_for_passage(part["analysis"], {"start": 40, "end": 80})
    meaning = result["song_meaning"]
    assert meaning["vocal_status"] == "unclear"
    assert meaning["cues"] == meaning["themes"] == []
    assert "No timestamped" in meaning["summary"]


def test_single_request_schema_and_duration_remain_bounded(config, monkeypatch):
    hosted = MagicMock()
    monkeypatch.setattr(music, "_hosted_json", hosted)
    with pytest.raises(ValueError, match="at most 90"):
        music.interpret_audio(config, "song", {"start": 0, "end": 600}, Path("unused"), "", "job")
    hosted.assert_not_called()
    schema = music.AudioInterpretation.model_json_schema()
    assert schema["properties"]["segments"]["maxItems"] == 8
    assert schema["properties"]["events"]["maxItems"] == 32
    assert SongMeaning.model_json_schema()["properties"]["cues"]["maxItems"] == 8


def test_seven_bounded_audio_calls_reuse_completed_part_cache_after_failure(config, monkeypatch):
    decoded, calls, messages = [], [], []
    monkeypatch.setattr(music, "_decode_audio", lambda source, target, start, end: decoded.append((start, end)))
    fail = [True]

    def hosted(_config, prompt, schema, **kwargs):
        evidence = json.loads(prompt.split("\n", 1)[1])["music_evidence"]
        scope = evidence["passage"]
        calls.append(deepcopy(scope))
        assert scope["end"] - scope["start"] <= 90
        assert "audio_interpretation" not in evidence
        assert schema["properties"]["segments"]["maxItems"] == 8
        assert "-part-" in str(kwargs["receipt_path"])
        if len(calls) == 3 and fail[0]:
            fail[0] = False
            raise music.MusicUnavailable("Fixture interruption")
        return _audio_interpretation(0, scope["end"] - scope["start"])

    monkeypatch.setattr(music, "_hosted_json", hosted)
    document = {"track": {"id": "song"}, "passage": {"start": 10., "end": 610.}, "brief": ""}
    original = deepcopy(document)
    with pytest.raises(music.MusicUnavailable):
        interpret_long_audio(config, Path("unused"), document, "first", messages.append)
    result = interpret_long_audio(config, Path("unused"), document, "second", messages.append)
    assert len(result["parts"]) == 7 and len(calls) == 8
    assert calls[2] == calls[3]
    assert document == original
    assert all(end - start <= 90 for start, end in decoded)
    assert any("section 7 of 7" in message for message in messages)
    assert any("saved interpretation" in message for message in messages)


def test_full_rhythm_has_local_resolution_and_keeps_late_beats(config, monkeypatch):
    monkeypatch.setattr(music, "_beat_profile", lambda _: {"model": "fixture"})
    beats = [index / 4 for index in range(2400)]
    monkeypatch.setattr(music, "_beat_times", lambda *_: (beats, beats[::4]))
    scope = {"start": 10, "end": 610}
    rhythm = music.local_rhythm(Path("unused"), np.ones(600 * 10, dtype=np.float32), 10, "song", scope, config)
    assert len(rhythm["intensity"]) == 4800
    packet = music_evidence({"track": {"id": "song"}, "passage": scope, "rhythm": rhythm})
    assert len(packet["measured"]["beats"]) == 2400
    assert packet["measured"]["beats"][-1] == 609.75
    local = rhythm_for_passage(rhythm, {"start": 520, "end": 610})
    assert len(local["intensity"]) == 720 and local["intensity"] == [1.] * 720
    assert local["beats"][0] == 520 and local["beats"][-1] == 609.75
    assert local["provenance"]["source_passage"] == scope
    evidence = music_evidence({"track": {"id": "song"}, "passage": {"start": 520, "end": 610}, "rhythm": local})
    assert "original measured passage" in evidence["measured"]["relative_rms"]["scope"]


def test_rhythm_projection_preserves_global_normalization_and_manual_scope():
    scope = {"start": 0, "end": 180}
    rhythm = {"provenance": {"track": "song", "passage": scope}, "intensity": [.1, .3, .7, 1.],
              "waveform": [.2, .4, .8, 1.], "waveform_start": 0, "waveform_end": 180,
              "beats": [0, 45, 90, 135], "markers": [10, 50, 100], "marker_source": "user",
              "track_id": "song", "passage": scope}
    view = rhythm_for_passage(rhythm, {"start": 0, "end": 90})
    assert view["intensity"] == [.1, .3] and view["waveform"] == [.2, .4]
    assert view["markers"] == [10, 50] and view["passage"] == {"start": 0, "end": 90}
    rhythm["track_id"] = "stale"
    assert rhythm_for_passage(rhythm, {"start": 0, "end": 90})["markers"] == []


def test_long_analyze_job_uses_bounded_audio_and_returns_valid_editable_project(config, tmp_path, monkeypatch):
    source = tmp_path / "original.wav"
    source.write_bytes(b"immutable audio fixture")
    track_id = music.content_hash(source)
    store = LabStore(config.paths.state_dir)
    store.initialize()
    store.add_track(track_id, "music", 116.610612, source)
    scope = {"start": 0., "end": 116.610612}
    document = ProjectDocument(track={"id": track_id, "name": "music", "duration": 116.610612}, passage=scope).model_dump(mode="json")
    decoded, calls = [], []
    monkeypatch.setattr(music, "_decode_audio", lambda source, target, start, end:
                        (decoded.append((start, end)) or np.ones(100, dtype=np.float32), 10))
    monkeypatch.setattr(music, "local_rhythm", lambda *args: {
        "provenance": {"track": track_id, "passage": scope}, "markers": [], "beats": [1, 50, 100]})

    def hosted(_config, prompt, *_args, **_kwargs):
        passage = json.loads(prompt.split("\n", 1)[1])["music_evidence"]["passage"]
        calls.append(passage)
        return _audio_interpretation(0, passage["end"] - passage["start"])

    monkeypatch.setattr(music, "_hosted_json", hosted)
    result = music.run_music_job({"id": "long", "kind": "analyze", "document": document}, config, object(), lambda _: None)
    assert len(calls) == 2 and all(part["end"] - part["start"] <= 90 for part in calls)
    assert len(decoded) == 3 and decoded[0] == (0., 116.610612)
    assert result["analysis"]["parts"][-1]["passage"]["end"] == 116.610612
    assert result["music_timeline"]["slots"][-1]["end"] == 116.610612
    assert music.validate_interpretation(result["analysis"], scope)


@pytest.mark.parametrize("overshoot", [0., .00001])
def test_hosted_observation_endpoint_rounding_uses_exact_real_track_scope(config, monkeypatch, overshoot):
    scope = {"start": 58.333333333333336, "end": 116.610612}
    answer = _audio_interpretation(0, 58.277279)
    answer["events"] = [{"id": "closing", "start": 57., "end": 58.277279 + overshoot,
                          "label": "Closing texture", "kind": "texture", "confidence": "medium"}]
    answer["song_meaning"] = {"vocal_status": "partly_understood", "summary": "A repeated request.",
                              "themes": ["desire"], "uncertainty": "Approximate final phrase.",
                              "cues": [{"start": 57., "end": 58.277279 + overshoot,
                                        "paraphrase": "The speaker repeats a request.", "confidence": "medium"}]}
    original = deepcopy(answer)
    monkeypatch.setattr(music, "_hosted_json", lambda *_args, **_kwargs: answer)
    if overshoot:
        with pytest.raises(ValueError, match="inside the selected passage"):
            music.interpret_audio(config, "song", scope, Path("unused"), "", "rounding")
    else:
        result = music.interpret_audio(config, "song", scope, Path("unused"), "", "rounding")
        assert result["events"][0]["end"] == result["song_meaning"]["cues"][0]["end"] == scope["end"]
        # Cached/manual evidence does not receive the hosted response repair.
        result["events"][0]["end"] += .0000003
        with pytest.raises(ValueError, match="inside the selected passage"):
            music.validate_interpretation(result, scope)
    assert answer == original
