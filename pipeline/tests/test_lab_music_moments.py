"""Song-specific timing and per-moment intentions remain bounded and editable."""
from copy import deepcopy
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from pipeline.lab import music
from pipeline.lab.limits import MAX_SAVED_CLIPS
from pipeline.lab.models import ClipSelection, JobRequest, MusicDirection, ProjectDocument
from pipeline.lab.store import LabStore
from pipeline.lab.timeline import direction_for, ensure_timeline, refresh_directions, replan_timeline
from pipeline.tests.test_lab import db  # noqa: F401
from pipeline.tests.test_lab_music import _audio, _audio_interpretation, _interpretation


def _moments(start=10, end=19):
    result = _audio_interpretation(start, end)
    prototype = result["edit_beats"][0]
    result["edit_beats"] = [
        {**prototype, "start": start, "end": start + 1.25, "query": "a doorway opening", "search_facet": "scene", "music_cue": "A brief accented entrance"},
        {**prototype, "start": start + 1.25, "end": start + 6.5, "query": "soft blue light", "search_facet": "look", "music_cue": "A suspended tone continues across several pulses", "timing_note": "Hold through the phrase instead of cutting on each pulse"},
        {**prototype, "start": start + 6.5, "end": end, "query": "relief", "search_facet": "mood", "music_cue": "The tension settles"},
    ]
    return result


def _doc():
    return ProjectDocument(track={"id": "track", "name": "Music", "duration": 30}, passage={"start": 10, "end": 19},
                           analysis=_moments(), rhythm={"markers": [11, 12, 13, 14, 15, 16, 17, 18]}).model_dump()


def test_initial_timing_and_directions_use_exact_song_moments_not_regular_cadence():
    document = _doc()
    slots = ensure_timeline(document)["slots"]
    assert [(slot["start"], slot["end"]) for slot in slots] == [(10, 11.25), (11.25, 16.5), (16.5, 19)]
    assert [slot["direction"]["search_facet"] for slot in slots] == ["scene", "look", "mood"]
    assert len({slot["direction"]["query"] for slot in slots}) == 3
    assert all(slot["direction_source"] == "ai" for slot in slots)
    ProjectDocument.model_validate(document)


def test_legacy_slots_keep_section_fallback_and_legacy_initial_timing_is_honest():
    document = _doc(); document["analysis"] = _interpretation(10, 19)
    timeline = ensure_timeline(document)
    assert [(slot["start"], slot["end"]) for slot in timeline["slots"]] == [(10, 19)]
    slot = timeline["slots"][0]
    assert slot["direction"] is None
    document["analysis"]["segments"][0].update(query="a changed section", search_facet="scene")
    assert direction_for(slot, document["analysis"])["query"] == "a changed section"
    assert music.validate_interpretation(document["analysis"], document["passage"]).edit_beats == []


def test_manual_cut_markers_override_initial_moments_but_explicit_replan_uses_music():
    document = _doc(); document["rhythm"].update(marker_source="user", markers=[12, 17])
    assert [(slot["start"], slot["end"]) for slot in ensure_timeline(document)["slots"]] == [(10, 12), (12, 17), (17, 19)]
    assert [(slot["start"], slot["end"]) for slot in replan_timeline(document)["slots"]] == [(10, 11.25), (11.25, 16.5), (16.5, 19)]
    assert document["rhythm"]["markers"] == [12, 17]


def test_hosted_edit_moments_are_offset_and_contract_scoped(config, monkeypatch):
    offered = _moments(0, 9)
    captured = []
    def hosted(_config, prompt, schema, **kwargs):
        captured.append((prompt, schema)); return deepcopy(offered)
    monkeypatch.setattr(music, "_hosted_json", hosted)
    result = music.interpret_audio(config, "track", {"start": 10, "end": 19}, Path("unused"), "Hold when it earns the hold", "j")
    assert [(item["start"], item["end"]) for item in result["edit_beats"]] == [(10, 11.25), (11.25, 16.5), (16.5, 19)]
    assert result["provenance"]["interpretation_contract"] == music.INTERPRETATION_CONTRACT
    prompt, schema = captured[0]
    assert "not every pulse" in prompt and "random timing" in prompt
    assert schema["properties"]["edit_beats"]["maxItems"] == 32
    assert "edit_beats" in schema["required"]
    assert music.interpret_audio(config, "track", {"start": 10, "end": 19}, Path("unused"), "Hold when it earns the hold", "again") == result
    assert len(captured) == 1


@pytest.mark.parametrize("mutation", [
    lambda a: a.update(edit_beats=[]),
    lambda a: a["edit_beats"][0].update(start=10.5),
    lambda a: a["edit_beats"][1].update(start=11.5),
    lambda a: a["edit_beats"][-1].update(end=20),
    lambda a: a["edit_beats"][0].update(end=10.01),
    lambda a: a["edit_beats"][0].update(query="   "),
    lambda a: a["edit_beats"][0].update(search_facet="composition"),
])
def test_new_hosted_moments_reject_invalid_bounds_and_directions(mutation):
    analysis = _moments(); mutation(analysis)
    with pytest.raises(ValueError):
        music.validate_interpretation(analysis, {"start": 10, "end": 19}, require_edit_beats=True)


def test_reanalysis_preserves_timing_placements_and_user_directions():
    document = _doc(); timeline = ensure_timeline(document)
    clip = ClipSelection(id="kept", film_id="film", source_start=2, source_end=3.25).model_dump()
    document["clips"] = [clip]
    timeline["slots"][0]["clip_id"] = clip["id"]
    custom = MusicDirection(query="my specific image", music_cue="The moment I want to emphasize").model_dump()
    timeline["slots"][0].update(direction=custom, direction_source="user")
    before = deepcopy(document)
    newer = _moments(); newer["edit_beats"][1]["query"] = "another texture"
    newer["edit_beats"][0]["end"] = 12; newer["edit_beats"][1]["start"] = 12
    refresh_directions(timeline, newer)
    assert [(slot["id"], slot["start"], slot["end"], slot["clip_id"]) for slot in timeline["slots"]] == [
        (slot["id"], slot["start"], slot["end"], slot["clip_id"]) for slot in before["music_timeline"]["slots"]]
    assert timeline["slots"][0]["direction"] == custom
    assert timeline["slots"][1]["direction"]["query"] == "another texture"
    assert document["clips"] == before["clips"]


def test_explicit_replan_keeps_entire_bin_and_rejects_placed_locks():
    document = _doc(); timeline = ensure_timeline(document)
    placed = ClipSelection(id="kept", film_id="film", source_start=2, source_end=3.25).model_dump()
    unplaced = ClipSelection(id="unplaced", film_id="film", source_start=5, source_end=8, locked=True).model_dump()
    document["clips"] = [placed, unplaced]; timeline["slots"][0]["clip_id"] = "kept"
    originals = deepcopy(document["clips"])
    timeline["slots"][0].update(direction={"query": "personal image"}, direction_source="user")
    document["analysis"]["edit_beats"][0]["end"] = 12
    document["analysis"]["edit_beats"][1]["start"] = 12
    document["clips"][0]["locked"] = True
    with pytest.raises(ValueError, match="Unlock placed"):
        replan_timeline(document)
    document["clips"][0]["locked"] = False
    replacement = replan_timeline(document)
    assert all(slot["clip_id"] is None for slot in replacement["slots"])
    assert document["clips"] == originals
    assert replacement["slots"][0]["direction"]["query"] == "personal image"
    ProjectDocument.model_validate(document)


def test_slot_directions_drive_distinct_facet_queries_and_planner_context(config, monkeypatch):
    document = _doc(); ensure_timeline(document)
    calls, payloads = [], []
    def retrieve(query, _db, _config, _films, facet="all"):
        calls.append((facet, query))
        return [{"film_id": f"film-{query}", "unit_id": query, "t_start": 1., "t_end": 12., "caption": query}]
    def hosted(_config, prompt, *args, **kwargs):
        payload = json.loads(prompt.split("\n", 1)[1]); payloads.append(payload)
        return {"choices": {slot["key"]: {"source": {slot["candidates"][0]["source"]: 1.}, "reason": slot["direction"]["music_cue"]}
                            for slot in payload["slots"]}}
    monkeypatch.setattr(music, "retrieve_edit_candidates", retrieve)
    monkeypatch.setattr(music, "_hosted_json", hosted)
    result = music.make_draft(document, config, object(), lambda _: None, "j")
    assert calls == [("scene", "a doorway opening"), ("look", "soft blue light"), ("mood", "relief")]
    assert payloads[0]["slots"][1]["direction"]["timing_note"].startswith("Hold through")
    assert len(payloads[0]["timeline"]) == 3
    assert result["music_timeline"]["slots"][1]["reason"] == "A suspended tone continues across several pulses"


def test_distinct_search_budget_rejects_before_any_retrieval_or_hosted_call(config, monkeypatch):
    document = _doc()
    slots = [{"id": f"s{i}", "start": 10 + 9 * i / 33, "end": 10 + 9 * (i + 1) / 33,
              "section_index": 0, "direction": {"query": f"image {i}"}} for i in range(33)]
    document["music_timeline"] = {"track_id": "track", "passage": document["passage"], "slots": slots}
    document = ProjectDocument.model_validate(document).model_dump()
    retrieval = MagicMock(return_value=[]); hosted = MagicMock()
    monkeypatch.setattr(music, "retrieve_edit_candidates", retrieval); monkeypatch.setattr(music, "_hosted_json", hosted)
    with pytest.raises(ValueError, match="32 distinct"):
        music.make_draft(document, config, object(), lambda _: None, "j")
    retrieval.assert_not_called(); hosted.assert_not_called()
    music.make_draft(document, config, object(), lambda _: None, "subset", [slot["id"] for slot in slots[:32]])
    assert retrieval.call_count == 32


def test_retained_bin_capacity_fails_before_search_or_payment(config, monkeypatch):
    document = _doc(); timeline = ensure_timeline(document)
    document["clips"] = [ClipSelection(id=f"c{i}", film_id="f", source_start=0, source_end=1).model_dump() for i in range(MAX_SAVED_CLIPS - 1)]
    retrieval = MagicMock(return_value=[]); hosted = MagicMock()
    monkeypatch.setattr(music, "retrieve_edit_candidates", retrieval); monkeypatch.setattr(music, "_hosted_json", hosted)
    before = deepcopy(document["clips"])
    with pytest.raises(ValueError, match=f"{MAX_SAVED_CLIPS} saved clips"):
        music.make_draft(document, config, object(), lambda _: None, "j")
    retrieval.assert_not_called(); hosted.assert_not_called()
    music.make_draft(document, config, object(), lambda _: None, "one", [timeline["slots"][0]["id"]])
    assert retrieval.call_count == 1
    assert document["clips"] == before


def test_replan_option_is_analyze_only_snapshot_and_rejected_before_decode(config, tmp_path, monkeypatch):
    for kind in ("draft", "render"):
        with pytest.raises(ValueError):
            JobRequest(kind=kind, base_revision=1, replan_timing=True)
    document = _doc(); timeline = ensure_timeline(document)
    document["clips"] = [ClipSelection(id="locked", film_id="film", source_start=0, source_end=1.25, locked=True).model_dump()]
    timeline["slots"][0]["clip_id"] = "locked"
    decode = MagicMock(); hosted = MagicMock()
    monkeypatch.setattr(music, "_decode_audio", decode); monkeypatch.setattr(music, "_hosted_json", hosted)
    with pytest.raises(ValueError, match="Unlock placed"):
        music.run_music_job({"id": "j", "kind": "analyze", "document": document, "snapshot": {"replan_timing": True}}, config, object(), lambda _: None)
    decode.assert_not_called(); hosted.assert_not_called()
    store = LabStore(config.paths.state_dir); store.initialize()
    project = store.create_project("Music", "music-sketch")
    store.enqueue("analyze", project["id"], 1, replan_timing=True)
    assert store.claim()["snapshot"]["replan_timing"] is True


def test_api_replan_freezes_option_and_preserves_sources_in_bin(config, db, tmp_path):
    from pipeline.api.main import app
    source = tmp_path / "track.wav"; _audio(source)
    with patch("pipeline.api.main.load_config", return_value=config), patch("pipeline.api.main.open_db", return_value=db), patch("pipeline.api.main.ensure_search_indexes"), TestClient(app) as client:
        project = client.post("/lab/projects", json={"name": "Timing"}).json()
        url = f"/lab/projects/{project['id']}"
        project = client.post(url + "/track", data={"base_revision": 1}, files={"file": ("track.wav", source.read_bytes())}).json()
        result = client.post(url + "/jobs", json={"kind": "analyze", "base_revision": 2, "replan_timing": True})
        assert result.status_code == 200, result.text
        store = LabStore(config.paths.state_dir)
        assert store.get_job(result.json()["id"], private=True)["snapshot"]["replan_timing"] is True
