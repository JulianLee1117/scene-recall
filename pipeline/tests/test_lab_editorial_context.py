"""Creative instructions are scoped user intent, not music perception or cuts."""
from copy import deepcopy
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from pipeline.lab import direction_planner, music, timing_planner
from pipeline.lab.editorial_context import editorial_context, reset_track_ranges
from pipeline.lab.models import ProjectDocument
from pipeline.lab.music_evidence import LISTENING_INPUT_CONTRACT, music_evidence
from pipeline.lab.store import LabStore
from pipeline.tests.test_lab_direction_planner import _answer, _job
from pipeline.tests.test_lab_music import _audio_interpretation
from pipeline.tests.test_lab_music_evidence import _document


def _direction():
    return {"instruction": "Surreal blue imagery; keep the outcome unresolved.", "ranges": [
        {"id": "opening", "start": 8., "end": 12., "instruction": "Hold across the accent."},
        {"id": "draft", "start": 12., "end": 13., "instruction": " "},
        {"id": "change", "start": 17., "end": 22., "instruction": "Short red flashes, including off-beat cuts."},
        {"id": "later", "start": 24., "end": 29., "instruction": "End in darkness."},
    ]}


def test_explicit_empty_direction_clears_legacy_but_preserves_original_document():
    document = _document()
    document.update(brief="LEGACY_BRIEF", visual_plan={"source": "user", "arc": "LEGACY_ARC", "motifs": "LEGACY_MOTIFS"})
    assert editorial_context(document)["instruction"] == "LEGACY_BRIEF\n\nVisual arc: LEGACY_ARC\n\nMotifs: LEGACY_MOTIFS"
    document["visual_plan"]["source"] = "ai"
    assert editorial_context(document)["instruction"] == "LEGACY_BRIEF"
    document["visual_plan"]["source"] = "user"
    document["editor_direction"] = {"instruction": "", "ranges": []}
    before = deepcopy(document)
    packet = music_evidence(document)
    assert packet["editor_direction"]["instruction"] == "" and packet["visual_plan"] is None
    assert "LEGACY" not in json.dumps(packet)
    assert document == before


def test_ranges_use_track_seconds_clip_to_passage_and_ignore_empty_text():
    document = _document()
    document["editor_direction"] = _direction()
    document["planner_settings"]["lyric_treatment"] = "ignore"
    before = deepcopy(document)
    packet = music_evidence(document)
    assert packet["editor_direction"]["ranges"] == [
        {"id": "opening", "start": 10., "end": 12., "instruction": "Hold across the accent."},
        {"id": "change", "start": 17., "end": 19., "instruction": "Short red flashes, including off-beat cuts."},
    ]
    assert packet["editor_direction"]["time_base"] == "source-track-seconds"
    assert document == before
    assert ProjectDocument.model_validate(document).editor_direction.ranges[1].instruction == " "
    assert editorial_context(document, {"start": 13., "end": 17.})["ranges"] == []


@pytest.mark.parametrize("ranges", [
    [{"id": "bad", "start": 12, "end": 12}],
    [{"id": "bad", "start": -1, "end": 12}],
    [{"id": "bad", "start": 20, "end": 31}],
    [{"id": "bad", "start": float("nan"), "end": 12}],
    [{"id": "bad", "start": True, "end": 12}],
    [{"id": "bad", "start": 11, "end": float("inf")}],
    [{"id": "same", "start": 10, "end": 11}, {"id": "same", "start": 12, "end": 13}],
    [{"id": "left", "start": 10, "end": 12}, {"id": "right", "start": 11, "end": 13}],
    [{"id": "bad", "start": 10, "end": 12, "instruction": "x" * 2001}],
])
def test_invalid_ranges_are_rejected(ranges):
    with pytest.raises(ValueError):
        ProjectDocument.model_validate({**_document(), "editor_direction": {"instruction": "", "ranges": ranges}})


def test_range_limits_and_track_required_but_global_instruction_can_precede_music():
    assert ProjectDocument(editor_direction={"instruction": "x" * 24000}).editor_direction.instruction
    with pytest.raises(ValueError):
        ProjectDocument(editor_direction={"instruction": "x" * 24001})
    with pytest.raises(ValueError):
        ProjectDocument(editor_direction={"ranges": [{"id": "x", "start": 0, "end": 1}]})
    document = _document()
    ranges = [{"id": str(index), "start": index / 10, "end": (index + 1) / 10} for index in range(33)]
    with pytest.raises(ValueError):
        ProjectDocument.model_validate({**document, "editor_direction": {"ranges": ranges}})
    ranges.pop()
    assert len(ProjectDocument.model_validate({**document, "editor_direction": {"ranges": ranges}}).editor_direction.ranges) == 32


def test_long_scopes_only_receive_overlapping_instructions():
    document = ProjectDocument(track={"id": "long", "name": "Long", "duration": 640}, passage={"start": 10, "end": 610},
        editor_direction={"instruction": "One recurring blue motif", "ranges": [
            {"id": "across", "start": 85, "end": 110, "instruction": "Stay with this image"},
            {"id": "last", "start": 550, "end": 620, "instruction": "Let it dissolve into darkness"},
        ]}).model_dump(mode="json")
    for start, end, expected in [(10, 100, (85, 100)), (100, 190, (100, 110)), (550, 610, (550, 610))]:
        scoped = music_evidence({**document, "passage": {"start": start, "end": end}})["editor_direction"]
        assert [(row["start"], row["end"]) for row in scoped["ranges"]] == [expected]
    assert document["editor_direction"]["ranges"][0]["end"] == 110


def test_style_timing_and_legacy_changes_do_not_relisten_but_song_context_does(config, monkeypatch):
    document = _document()
    calls = []
    def hosted(_config, prompt, _schema, **_kwargs):
        calls.append(json.loads(prompt.split("\n", 1)[1])["music_evidence"])
        return _audio_interpretation(0, 9)
    monkeypatch.setattr(music, "_hosted_json", hosted)
    def listen():
        return music.interpret_audio(config, "track", document["passage"], Path("unused"), document["brief"], "listen",
                                     evidence=music_evidence(document, include_analysis=False))
    first = listen()
    document.update(brief="PRIVATE_BRIEF", visual_plan={"source": "user", "arc": "PRIVATE_ARC", "motifs": "PRIVATE_MOTIF"},
                    editor_direction=_direction())
    document["planner_settings"].update(pacing="rapid", lyric_treatment="ignore")
    document["rhythm"]["markers"] = [10.5, 16.25]
    document["music_timeline"]["slots"][0]["end"] = 12.5
    document["music_timeline"]["slots"][1]["start"] = 12.5
    assert listen() == first and len(calls) == 1
    assert first["provenance"]["listening_input_contract"] == LISTENING_INPUT_CONTRACT
    assert "PRIVATE" not in json.dumps(first["provenance"])
    assert set(calls[0]) == {"contract", "track_id", "passage", "time_base", "song_context", "measured"}
    assert "markers" not in calls[0]["measured"] and "slots" not in calls[0]["measured"]["relative_rms"]
    document["song_context"] = {"track_id": "track", "notes": "A partly obscured lyric", "lyrics": []}
    listen()
    assert len(calls) == 2


def test_direction_changes_invalidate_editorial_calls_and_clear_legacy_user_plan(config, monkeypatch):
    document = _document()
    document.update(brief="PRIVATE_OLD", visual_plan={"source": "user", "arc": "PRIVATE_ARC", "motifs": "PRIVATE_MOTIFS"},
                    editor_direction={"instruction": "", "ranges": []})
    target = document["music_timeline"]["slots"][0]["id"]
    calls = []
    def hosted(_config, prompt, _schema, **_kwargs):
        assert "PRIVATE" not in prompt
        calls.append(json.loads(prompt.split("\n", 1)[1]))
        return _answer([target])
    monkeypatch.setattr(music, "_hosted_json", hosted)
    first = direction_planner.run_direction_job(_job(document, [target]), config, object(), lambda _: None)
    assert first["visual_plan"]["source"] == "ai"
    assert first["editor_direction"] == document["editor_direction"]
    assert direction_planner.run_direction_job(_job(document, [target]), config, object(), lambda _: None)["direction_plan"]["cache_reused"]
    document["editor_direction"] = _direction()
    direction_planner.run_direction_job(_job(document, [target]), config, object(), lambda _: None)
    assert len(calls) == 2
    assert calls[-1]["music_evidence"]["editor_direction"]["ranges"][0]["start"] == 10


def test_editorial_context_reaches_selection_next_scene_and_inspection():
    from pipeline.lab.footage_review import _review_payload
    from pipeline.lab.next_scene import _context
    from pipeline.lab.selection_prompt import build_selection_payload
    from pipeline.tests.test_lab_selection_prompt import _inputs
    document = _document()
    document["editor_direction"] = _direction()
    expected = editorial_context(document)
    args = list(_inputs()); args[0] = document
    assert build_selection_payload(*args)["music_evidence"]["editor_direction"] == expected
    scope = {"anchor_slot_id": document["music_timeline"]["slots"][0]["id"], "options": {"intent": ""}}
    assert _context(document, scope, MagicMock(), {}, [])["music_evidence"]["editor_direction"] == expected
    assert _review_payload(document, [])["music_evidence"]["editor_direction"] == expected


def test_timing_receives_source_time_instructions_without_forcing_range_endpoint_cuts(config, monkeypatch):
    document = _document()
    document.update(music_timeline=None, editor_direction=_direction())
    calls = []
    def hosted(_config, prompt, _schema, **_kwargs):
        calls.append(json.loads(prompt.split("\n", 1)[1]))
        return {"end_frames": [216], "notes": [{"start_frame": 0, "end_frame": 216,
                "reason": "Hold across the accents", "evidence_ids": []}]}
    monkeypatch.setattr(music, "_hosted_json", hosted)
    result = timing_planner.run_timing_job({"id": "timing", "document": document}, config, lambda _: None)
    assert len(result["music_timeline"]["slots"]) == 1
    assert calls[0]["editor_direction"]["ranges"][0]["start"] == 10
    assert calls[0]["time_base"] == "output-frames-relative-to-passage-start"
    changed = deepcopy(document); changed["editor_direction"]["ranges"][0]["instruction"] = "Short impressions"
    timing_planner.run_timing_job({"id": "timing", "document": changed}, config, lambda _: None)
    assert len(calls) == 2


def test_track_import_clears_ranges_but_undo_save_and_history_preserve_them(tmp_path):
    store = LabStore(tmp_path); store.initialize()
    document = _document(); document["editor_direction"] = _direction()
    project = store.create_project("Direction", "music-sketch", document)
    replacement = reset_track_ranges(document)
    replacement.update(track={"id": "other", "name": "Other", "duration": 12}, passage={"start": 0, "end": 12},
                       analysis=None, rhythm=None, music_timeline=None)
    updated = store.update_project(project["id"], 1, replacement)
    assert updated["document"]["editor_direction"] == {"instruction": _direction()["instruction"], "ranges": []}
    # Local Undo restores an entire valid draft, then Save sends that snapshot.
    undone = store.update_project(project["id"], 2, deepcopy(document))
    assert undone["document"]["track"] == document["track"]
    assert undone["document"]["editor_direction"] == document["editor_direction"]
    store.update_project(project["id"], 3, replacement)
    restored = store.restore(project["id"], 4, 1)
    assert restored["document"]["editor_direction"] == document["editor_direction"]
    assert restored["document"]["track"] == document["track"]


@pytest.mark.parametrize("direction", ["canonical", "legacy", "cleared", "ai-plan"])
def test_api_track_import_resets_timed_instructions_and_retains_global_direction(tmp_path, monkeypatch, direction):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from pipeline.lab import api
    store = LabStore(tmp_path); store.initialize()
    document = _document(); document["editor_direction"] = _direction()
    document.update(brief="Keep an unresolved atmosphere", visual_plan={"source": "user", "arc": "Move from blue to red", "motifs": "Windows"})
    if direction in {"legacy", "ai-plan"}:
        document["editor_direction"] = None
    if direction == "ai-plan":
        document["visual_plan"]["source"] = "ai"
    if direction == "cleared":
        document["editor_direction"] = {"instruction": "", "ranges": []}
    expected = editorial_context(document)["instruction"]
    project = store.create_project("Direction", "music-sketch", document)
    monkeypatch.setattr(api, "_uploaded_track", lambda *_: {"id": "new-song", "name": "New song", "duration": 5})
    app = FastAPI(); app.state.lab = store; app.include_router(api.router)
    with TestClient(app) as client:
        response = client.post(f"/lab/projects/{project['id']}/track", data={"base_revision": 1},
                               files={"file": ("song.mp3", b"test upload", "audio/mpeg")})
    assert response.status_code == 200, response.text
    saved = response.json()["document"]
    assert saved["editor_direction"] == {"instruction": expected, "ranges": []}
    assert saved["passage"] == {"start": 0, "end": 5}
    assert saved["visual_plan"] is None


def test_legacy_undo_save_preserves_the_original_null_direction_and_user_plan(tmp_path):
    store = LabStore(tmp_path); store.initialize()
    document = _document()
    document.update(brief="Remain unresolved", visual_plan={"source": "user", "arc": "Blue to red", "motifs": "Windows"})
    project = store.create_project("Legacy direction", "music-sketch", document)
    expected = editorial_context(document)["instruction"]
    replacement = {**reset_track_ranges(document), "track": {"id": "other", "name": "Other", "duration": 5},
                   "passage": {"start": 0, "end": 5}, "analysis": None, "rhythm": None,
                   "music_timeline": None, "visual_plan": None}
    updated = store.update_project(project["id"], 1, replacement)
    assert updated["document"]["editor_direction"] == {"instruction": expected, "ranges": []}
    restored = store.update_project(project["id"], 2, deepcopy(document))
    assert restored["document"]["editor_direction"] is None
    assert restored["document"]["visual_plan"] == document["visual_plan"]


def test_valid_legacy_analysis_survives_direction_changes_without_relabeling():
    from pipeline.lab.generation import _analysis_is_current
    document = _document()
    document["analysis"]["provenance"].update(interpretation_contract="legacy-listening", brief="Legacy creative input")
    document["analysis"]["events"] = []
    document["analysis"]["events_provenance"] = {
        **deepcopy(document["analysis"]["provenance"]), "source": "ai-observed",
        "interpretation_id": music.digest(document["analysis"]["provenance"]),
        "evidence": {"visual_plan": {"source": "user", "arc": "Legacy visual instruction"}},
    }
    original = deepcopy(document["analysis"])
    document["editor_direction"] = _direction()
    assert _analysis_is_current(document)
    assert ProjectDocument.model_validate(document).model_dump(mode="json")["analysis"] == original
    assert "Legacy" not in json.dumps(music_evidence(document))
    assert document["analysis"] == original


def test_new_direction_refresh_never_overrides_written_shot_queries():
    from pipeline.lab.generation import _needs_plan
    slot = _document()["music_timeline"]["slots"][0]
    slot.update(direction={"query": "This exact user image"}, direction_source="user")
    assert not _needs_plan(slot, refresh_ai=True)
    slot["direction_source"] = None  # Legacy written direction has the same ownership.
    assert not _needs_plan(slot, refresh_ai=True)
