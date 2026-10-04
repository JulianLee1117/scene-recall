"""Shared musical evidence remains scoped, compact, explicit and editable."""
from copy import deepcopy
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from pipeline.lab import music
from pipeline.lab.direction_planner import DirectionPlan, run_direction_job
from pipeline.lab.models import ProjectDocument
from pipeline.lab.music_evidence import listening_evidence, music_evidence
from pipeline.lab.timeline import ensure_timeline
from pipeline.tests.selection_helpers import selector_response
from pipeline.tests.test_lab_direction_planner import _answer, _job
from pipeline.tests.test_lab_music import _audio_interpretation, _interpretation


def _document():
    passage = {"start": 10., "end": 19.}
    provenance = {"track": "track", "passage": passage}
    document = ProjectDocument(track={"id": "track", "name": "Music", "duration": 30}, passage=passage,
                               analysis={**_interpretation(10, 19), "provenance": provenance},
                               rhythm={"provenance": provenance, "waveform_start": 10, "waveform_end": 19,
                                       "track_id": "track", "passage": passage,
                                       "intensity": [0.] * 3 + [.5] * 3 + [1.] * 3,
                                       "beats": [9, 10, 11, 12, 19, 20], "downbeats": [10, 14, 18],
                                       "markers": [13, 16], "marker_source": "user"}).model_dump(mode="json")
    ensure_timeline(document)
    return document


def test_old_documents_gain_defaults_and_track_context_is_validated():
    document = ProjectDocument()
    assert document.planner_settings.model_dump() == {"pacing": "balanced", "lyric_treatment": "metaphorical", "footage": "balanced",
                                                      "match_cuts": "some", "auto": False}
    assert document.song_context is None and document.visual_plan is None
    original = _document()
    original["song_context"] = {"track_id": "track", "lyrics": [{"id": "line", "start": 20, "end": 25, "text": "Later in the song"}]}
    assert ProjectDocument.model_validate(original).song_context.lyrics[0].start == 20
    for context in [
        {"track_id": "other"},
        {"track_id": "track", "lyrics": [{"id": "line", "start": 29, "end": 31}]},
        {"track_id": "track", "lyrics": [{"id": "line", "start": 11, "end": 11}]},
        {"track_id": "track", "lyrics": [{"id": "line", "start": float("nan"), "end": 12}]},
        {"track_id": "track", "lyrics": [{"id": "same", "start": 11, "end": 12}] * 2},
    ]:
        with pytest.raises(ValueError):
            ProjectDocument.model_validate({**original, "song_context": context})
    with pytest.raises(ValueError):
        ProjectDocument.model_validate({**original, "planner_settings": {"pacing": "frantic"}})


def test_shared_rms_is_compact_and_weighted_in_source_time():
    document = _document()
    measured = music_evidence(document)["measured"]
    assert measured["beats"] == [10, 11, 12, 19]
    rms = measured["relative_rms"]
    assert rms["input_window_seconds"] == 1
    assert [row["mean"] for row in rms["slots"]] == [0, .5, 1]
    assert [(row["start"], row["end"]) for row in rms["slots"]] == [(10, 13), (13, 16), (16, 19)]
    document["music_timeline"]["slots"][0]["end"] = 14.5
    document["music_timeline"]["slots"][1]["start"] = 14.5
    assert music_evidence(document)["measured"]["relative_rms"]["slots"][0]["mean"] == .1667
    document["rhythm"]["intensity"] = [.5] * 720
    rms = music_evidence(document)["measured"]["relative_rms"]
    assert len(rms["windows"]) == 24 and len(rms["slots"]) == 3
    assert rms["input_window_seconds"] == .0125
    assert "intensity" not in measured


@pytest.mark.parametrize("mutation", [
    lambda r: r.update(provenance={"track": "other", "passage": {"start": 10, "end": 19}}),
    lambda r: r.update(provenance={"track": "track", "passage": {"start": 0, "end": 9}}),
    lambda r: r.update(waveform_start=0),
    lambda r: r.update(intensity=[float("nan")]),
    lambda r: r.update(intensity=[True]),
    lambda r: r.update(intensity=[2]),
])
def test_stale_or_invalid_measured_amplitude_is_not_supplied(mutation):
    document = _document(); mutation(document["rhythm"])
    assert "relative_rms" not in music_evidence(document)["measured"]


def test_current_derived_rhythm_does_not_validate_stale_user_markers():
    document = _document()
    document["rhythm"].update(track_id="other", passage={"start": 0, "end": 30})
    measured = music_evidence(document)["measured"]
    assert measured["beats"] == [10, 11, 12, 19]
    assert "relative_rms" in measured
    assert "markers" not in measured and "marker_source" not in measured
    document["rhythm"].pop("track_id"); document["rhythm"].pop("passage")
    assert "markers" not in music_evidence(document)["measured"]
    document["rhythm"]["marker_source"] = "derived"
    assert music_evidence(document)["measured"]["markers"] == [13, 16]


def test_lyrics_filter_overlaps_and_observations_need_their_own_provenance():
    document = _document()
    document["song_context"] = {"track_id": "track", "notes": "This is the second verse, reconsidering an earlier promise",
                                "lyrics": [{"id": str(i), "start": start, "end": end, "text": "supplied words", "meaning": "remembering"}
                                           for i, (start, end) in enumerate([(5, 10), (9, 11), (12, 14), (19, 24)])]}
    event = {"id": "e", "start": 12, "end": 13, "label": "A possible phrase ending", "kind": "phrase", "confidence": "low"}
    document["analysis"]["events"] = [event]
    packet = music_evidence(document)
    assert [line["id"] for line in packet["song_context"]["lyrics"]] == ["1", "2"]
    assert packet["audio_observations"] is None
    document["analysis"]["events_provenance"] = {"source": "ai-observed", "track": "track", "passage": document["passage"]}
    packet = music_evidence(document)
    assert packet["audio_observations"]["events"] == [event]
    assert "self-reported" in packet["audio_observations"]["note"]
    assert music_evidence(document, include_analysis=False)["audio_observations"] is None
    assert music_evidence(document, include_analysis=False)["audio_interpretation"] is None
    document["analysis"]["events_provenance"]["track"] = "other"
    assert music_evidence(document)["audio_observations"] is None
    document["song_context"]["track_id"] = "other"
    assert music_evidence(document)["song_context"] is None


@pytest.mark.parametrize("bad", [{"unexpected": "container"}, "text", ["text"], 1, None])
def test_opaque_legacy_event_containers_cannot_break_planning(bad):
    document = _document()
    document["analysis"].update(events=bad, events_provenance={"source": "ai-observed", "track": "track", "passage": document["passage"]})
    document = ProjectDocument.model_validate(document).model_dump(mode="json")
    assert music_evidence(document)["audio_observations"]["events"] == []
    document["analysis"]["events_provenance"] = bad
    assert music_evidence(document)["audio_observations"] is None
    document["analysis"]["provenance"] = bad
    assert music_evidence(document)["audio_interpretation"] is None


def test_analyze_receives_scoped_measurements_and_offsets_sparse_observations(config, monkeypatch):
    document = _document(); calls = []
    output = _audio_interpretation(0, 9)
    output["events"] = [{"id": "e", "start": 1.25, "end": 1.75, "label": "Possible soft accent", "kind": "accent", "confidence": "low"}]
    def hosted(_config, prompt, schema, **kwargs):
        calls.append((json.loads(prompt.split("\n", 1)[1]), schema))
        assert "Beat/downbeat timestamps estimate pulse, not measured accents" in prompt
        assert "relative to excerpt start 0" in prompt
        return deepcopy(output)
    monkeypatch.setattr(music, "_hosted_json", hosted)
    packet = music_evidence(document, include_analysis=False)
    args = (config, "track", document["passage"], Path("unused"), "brief", "job", lambda _: None)
    result = music.interpret_audio(*args, packet)
    assert result["events"][0]["start"] == 11.25 and result["events"][0]["end"] == 11.75
    assert result["events_provenance"]["source"] == "ai-observed"
    assert calls[0][0]["music_evidence"] == listening_evidence(packet)
    assert "slots" not in calls[0][0]["music_evidence"]["measured"]["relative_rms"]
    assert "events" in calls[0][1]["required"]
    music.interpret_audio(*args, packet)
    assert len(calls) == 1
    packet["planner_settings"]["pacing"] = "patient"
    music.interpret_audio(*args, packet)
    assert len(calls) == 1


@pytest.mark.parametrize("events", [
    [{"id": "e", "start": 9, "end": 11, "label": "pulse", "kind": "accent", "confidence": "high"}],
    [{"id": "e", "start": 12, "end": 13, "label": "pulse", "kind": "accent", "confidence": "high"}] * 2,
    [{"id": "e", "start": 12, "end": 12, "label": "pulse", "kind": "accent", "confidence": "high"}],
    [{"id": "e", "start": 12, "end": 13, "label": "pulse", "kind": "verified_motion", "confidence": "high"}],
])
def test_audio_events_reject_invalid_scope_ids_and_claim_types(events):
    analysis = _audio_interpretation(10, 19); analysis["events"] = events
    with pytest.raises(ValueError):
        music.validate_interpretation(analysis, {"start": 10, "end": 19}, require_edit_beats=True)
    assert music.validate_interpretation(_interpretation(10, 19), {"start": 10, "end": 19}).events == []


@pytest.mark.parametrize("user_plan", [False, True])
def test_plan_uses_settings_feedback_and_keeps_user_visual_plan(config, monkeypatch, user_plan):
    document = _document(); slots = document["music_timeline"]["slots"]
    target = slots[1]["id"]; slots[1]["feedback"] = "unfinished_action"
    document["planner_settings"] = {"pacing": "patient", "lyric_treatment": "counterpoint"}
    document["visual_plan"] = {"arc": "A handwritten plan", "motifs": "Empty rooms", "source": "user" if user_plan else "ai"}
    original = deepcopy(document); captured = []
    def hosted(_config, prompt, schema, **kwargs):
        packet = json.loads(prompt.split("\n", 1)[1])["music_evidence"]; captured.append(packet)
        assert "counterpoint deliberately contrasts" in prompt and "unfinished_action asks" in prompt
        return _answer([target])  # Even a non-null model plan may never overwrite user ownership.
    monkeypatch.setattr(music, "_hosted_json", hosted)
    result = run_direction_job(_job(document, [target]), config, object(), lambda _: None)
    assert captured[0]["planner_settings"] == {"footage": "balanced", **document["planner_settings"]}
    assert captured[0]["feedback"][0]["feedback"] == "unfinished_action"
    assert captured[0]["measured"]["relative_rms"]["slots"]
    assert result["analysis"] == document["analysis"] and result["clips"] == document["clips"]
    assert result["music_timeline"]["slots"][0] == original["music_timeline"]["slots"][0]
    assert result["music_timeline"]["slots"][1]["feedback"] == "unfinished_action"
    if user_plan:
        assert result["visual_plan"] == original["visual_plan"]
    else:
        assert result["visual_plan"]["arc"] == _answer([target])["visual_plan"]["arc"]
        assert result["visual_plan"]["source"] == "ai"


def test_visual_plan_schema_and_context_changes_are_versioned(config, monkeypatch):
    schema = DirectionPlan.model_json_schema()
    assert set(schema["required"]) == set(schema["properties"])
    assert set(schema["$defs"]["GeneratedVisualPlan"]["required"]) == {"arc", "motifs"}
    document = _document(); target = document["music_timeline"]["slots"][0]["id"]
    hosted = MagicMock(return_value=_answer([target])); monkeypatch.setattr(music, "_hosted_json", hosted)
    run_direction_job(_job(document, [target]), config, object(), lambda _: None)
    run_direction_job(_job(document, [target]), config, object(), lambda _: None)
    assert hosted.call_count == 1
    document["music_timeline"]["slots"][0]["feedback"] = "too_literal"
    run_direction_job(_job(document, [target]), config, object(), lambda _: None)
    document["planner_settings"]["lyric_treatment"] = "literal"
    run_direction_job(_job(document, [target]), config, object(), lambda _: None)
    assert hosted.call_count == 3


@pytest.mark.parametrize("plan", [None, {"arc": "   ", "motifs": "light"}])
def test_missing_generated_visual_arc_is_rejected_before_cache(config, monkeypatch, plan):
    document = _document(); target = document["music_timeline"]["slots"][0]["id"]
    answer = _answer([target]); answer["visual_plan"] = plan
    monkeypatch.setattr(music, "_hosted_json", lambda *a, **k: answer)
    with pytest.raises(ValueError):
        run_direction_job(_job(document, [target]), config, object(), lambda _: None)
    assert not list((config.paths.assets_dir / "lab" / "direction-plans").glob("*.json"))


def test_find_scenes_gets_same_evidence_without_relistening_and_preserves_feedback(config, monkeypatch):
    from pipeline.lab import direction_planner
    from pipeline.lab.models import ClipSelection
    document = _document(); slots = document["music_timeline"]["slots"]
    target = slots[1]["id"]; slots[1]["feedback"] = "too_similar"
    document["planner_settings"] = {"pacing": "kinetic", "lyric_treatment": "literal"}
    document["song_context"] = {"track_id": "track", "notes": "A memory becomes less comfortable", "lyrics": [
        {"id": "line", "start": 13, "end": 15, "text": "a supplied line", "meaning": "regret"}]}
    document["visual_plan"] = {"arc": "Distance to confrontation", "motifs": "Closed doors", "source": "user"}
    document["clips"] = [ClipSelection(id="kept", film_id="film", source_start=1, source_end=4, title="Film title").model_dump()]
    slots[0]["clip_id"] = "kept"
    source = {"id": "kept", "caption_evidence": {"text": "A figure in open space", "scope": "Sparse caption, not watched video"}}
    caption = MagicMock(return_value=source); monkeypatch.setattr(direction_planner, "_source_context", caption)
    before = deepcopy(document)
    expected = music_evidence(document)
    row = {"unit_id": "candidate", "film_id": "film", "t_start": 20., "t_end": 26., "caption": "A solitary figure facing a closed door"}
    monkeypatch.setattr(music, "retrieve_edit_candidates", lambda *a: [row])
    def hosted(_config, prompt, schema, **kwargs):
        payload = json.loads(prompt.split("\n", 1)[1])
        assert payload["music_evidence"] == expected
        assert payload["timeline"][0]["current_clip"]["id"] == "kept"
        assert payload["timeline"][0]["selected_source"]["caption_evidence"] == source["caption_evidence"]
        assert "too_similar asks for meaningful contrast" in prompt
        assert "Fixed slots stay fixed" in prompt
        assert "audio_path" not in kwargs
        return selector_response([{"slot": 1, "candidate_id": "candidate", "source_start": 21., "reason": "A readable still impression contrasts with surrounding open space"}], ["candidate"])
    monkeypatch.setattr(music, "_hosted_json", hosted)
    result = music.make_draft(document, config, object(), lambda _: None, "job", slot_ids=[target])
    assert result["visual_plan"] == before["visual_plan"] and result["song_context"] == before["song_context"]
    assert result["planner_settings"] == before["planner_settings"]
    assert result["music_timeline"]["slots"][1]["feedback"] == "too_similar"
    caption.assert_called_once()
    assert [(row["start"], row["end"]) for row in result["music_timeline"]["slots"]] == [(row["start"], row["end"]) for row in before["music_timeline"]["slots"]]
