"""Manual shot searches collect grounded alternatives without choosing footage."""
from copy import deepcopy
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient
import pytest

from pipeline.lab import music, music_planner
from pipeline.lab.limits import MAX_SAVED_CLIPS
from pipeline.lab.models import ClipSelection, JobRequest, MusicDirection, MusicMatchEvidence, ProjectDocument
from pipeline.lab.worker import execute_job
from pipeline.tests.test_lab import db, store  # noqa: F401
from pipeline.tests.test_lab_generation import _project
from pipeline.tests.test_lab_music_evidence import _document


def _rows(count=8):
    return [{"unit_id": f"candidate-{index}", "film_id": "film", "t_start": 20., "t_end": 30.,
             "caption": f"Sunlight through doorway {index}", "matched_text": f"Matched doorway {index}",
             "matched_text_view": "caption", "matched_frame_timestamp": 21. + index}
            for index in range(count)]


def _selected_document():
    document = _document()
    clip = ClipSelection(id="chosen", film_id="film", unit_id="chosen-unit", title="Current scene",
                         source_start=2, source_end=5).model_dump(mode="json")
    document["clips"] = [clip]
    slot = document["music_timeline"]["slots"][1]
    slot.update(clip_id=clip["id"], direction=MusicDirection(query="a warmer doorway").model_dump(mode="json"),
                direction_source="user", feedback="wrong_energy", needs_direction=True,
                reason="The existing scene's editorial reason", search_evidence=MusicMatchEvidence(rank=8).model_dump(mode="json"))
    return ProjectDocument.model_validate(document).model_dump(mode="json")


@pytest.mark.parametrize("kind,slot_ids", [
    ("draft", None), ("draft", []), ("draft", ["a", "b"]),
    ("plan", ["a"]), ("analyze", ["a"]), ("generate", ["a"]), ("render", ["a"]),
])
def test_suggestions_require_one_explicit_draft_target(kind, slot_ids):
    with pytest.raises(ValueError):
        JobRequest(kind=kind, base_revision=1, slot_ids=slot_ids, suggest_only=True)


def test_suggestions_option_is_strict_and_legacy_draft_default_is_unchanged():
    assert JobRequest(kind="draft", base_revision=1).suggest_only is False
    assert JobRequest(kind="draft", base_revision=1, slot_ids=["a"], suggest_only=True).suggest_only is True
    with pytest.raises(ValueError):
        JobRequest(kind="draft", base_revision=1, slot_ids=["a"], suggest_only="true")


def test_suggestions_keep_the_edit_and_return_ranked_evidence_without_hosted_selection(config, monkeypatch):
    document = _selected_document(); before = deepcopy(document)
    slot_id = document["music_timeline"]["slots"][1]["id"]
    rows = [{"unit_id": "too-short", "film_id": "film", "t_start": 0., "t_end": 1.}] + _rows()
    retrieve = MagicMock(return_value=rows)
    hosted = MagicMock(side_effect=AssertionError("Manual search must not call a hosted model"))
    monkeypatch.setattr(music, "retrieve_edit_candidates", retrieve)
    monkeypatch.setattr(music, "_hosted_json", hosted)
    result = music_planner.fill_timeline(document, config, object(), lambda _: None, "suggestions", [slot_id], suggest_only=True)
    slot = result["music_timeline"]["slots"][1]
    assert [item["clip"]["unit_id"] for item in slot["alternatives"]] == [f"candidate-{i}" for i in range(6)]
    for index, alternative in enumerate(slot["alternatives"]):
        clip, evidence = alternative["clip"], alternative["search_evidence"]
        assert 20 <= clip["source_start"] < clip["source_end"] <= 30
        assert clip["source_end"] - clip["source_start"] == 3
        assert clip["source_start"] <= 21 + index <= clip["source_end"]
        assert evidence["rank"] == index + 2 and evidence["matched_text"] == f"Matched doorway {index}"
        assert alternative["reason"] is None
    assert slot["resolved_search"]["min_duration"] == 3
    assert slot["resolved_search"]["clauses"][0]["text"] == "a warmer doorway"
    # Only this slot's shortlist and last executed search may change.
    restored = deepcopy(result)
    for key in ("alternatives", "resolved_search", "search_error"):
        restored["music_timeline"]["slots"][1][key] = before["music_timeline"]["slots"][1][key]
    assert restored == before
    assert retrieve.call_count == 1
    hosted.assert_not_called()
    ProjectDocument.model_validate(result)


def test_suggestions_use_nested_reference_frame_evidence_for_the_preview_window(config, monkeypatch):
    document = _selected_document(); slot_id = document["music_timeline"]["slots"][1]["id"]
    row = _rows(1)[0]
    row.pop("matched_frame_timestamp")
    row["matches"] = [{"clause_id": "look", "facet": "look", "rank": 1,
                       "evidence": {"type": "frame", "timestamp": 29., "frame_index": 2}}]
    monkeypatch.setattr(music, "retrieve_edit_candidates", lambda *_: [row])
    result = music_planner.fill_timeline(document, config, object(), lambda _: None, "suggestions", [slot_id], suggest_only=True)
    option = result["music_timeline"]["slots"][1]["alternatives"][0]
    assert option["clip"]["source_start"] == 27 and option["clip"]["source_end"] == 30
    assert option["search_evidence"]["matches"][0]["evidence"]["timestamp"] == 29


def test_no_matches_clears_only_the_shortlist_and_keeps_selected_source_evidence(config, monkeypatch):
    document = _selected_document(); slot = document["music_timeline"]["slots"][1]
    slot["alternatives"] = [{"clip": deepcopy(document["clips"][0]), "film_title": "Film", "reason": None,
                             "search_evidence": None}]
    before = deepcopy(document)
    monkeypatch.setattr(music, "retrieve_edit_candidates", lambda *_: [])
    result = music_planner.fill_timeline(document, config, object(), lambda _: None, "empty", [slot["id"]], suggest_only=True)
    assert slot["alternatives"] == [] and "No scene fits" in slot["search_error"]
    assert result["clips"] == before["clips"]
    assert slot["clip_id"] == "chosen" and slot["search_evidence"] == before["music_timeline"]["slots"][1]["search_evidence"]
    assert result["analysis"] == before["analysis"]


def test_suggestion_for_gap_is_allowed_at_full_saved_clip_capacity(config, monkeypatch):
    document = _document()
    document["clips"] = [ClipSelection(id=f"bin-{i}", film_id="film", source_start=i, source_end=i+1).model_dump(mode="json")
                         for i in range(MAX_SAVED_CLIPS)]
    slot_id = document["music_timeline"]["slots"][0]["id"]
    monkeypatch.setattr(music, "retrieve_edit_candidates", lambda *_: _rows(1))
    result = music_planner.fill_timeline(document, config, object(), lambda _: None, "bin", [slot_id], suggest_only=True)
    assert len(result["clips"]) == MAX_SAVED_CLIPS
    assert result["music_timeline"]["slots"][0]["clip_id"] is None
    assert len(result["music_timeline"]["slots"][0]["alternatives"]) == 1


@pytest.mark.parametrize("kind,slot_ids", [("plan", ["slot-1"]), ("draft", None), ("draft", ["slot-0", "slot-1"])])
def test_store_rejects_invalid_suggestion_requests_without_api(config, store, db, tmp_path, kind, slot_ids):
    project = _project(store, db, tmp_path)
    with pytest.raises(ValueError, match="exactly one"):
        store.enqueue(kind, project["id"], project["revision"], slot_ids=slot_ids, suggest_only=True)
    assert store.project_jobs(project["id"]) == []


def test_store_rejects_locked_and_unknown_suggestion_targets(config, store, db, tmp_path):
    project = _project(store, db, tmp_path, filled=(1,), locked=(1,))
    for selected, match in [("slot-1", "Unlock"), ("missing", "missing")]:
        with pytest.raises(ValueError, match=match):
            store.enqueue("draft", project["id"], project["revision"], slot_ids=[selected], suggest_only=True)
    assert store.project_jobs(project["id"]) == []


@pytest.mark.parametrize("with_matches", [True, False])
def test_worker_suggestions_save_one_revision_without_audio_or_llm_and_expose_result(config, store, db, tmp_path, monkeypatch, with_matches):
    project = _project(store, db, tmp_path, filled=(0, 1, 2), user=(1,))
    forbidden = MagicMock(side_effect=AssertionError("Suggestion search must not decode, analyze or select"))
    monkeypatch.setattr(music, "_decode_audio", forbidden)
    monkeypatch.setattr(music, "_hosted_json", forbidden)
    monkeypatch.setattr(music, "retrieve_edit_candidates", lambda *_: _rows(2) if with_matches else [])
    store.enqueue("draft", project["id"], project["revision"], slot_ids=["slot-1"], suggest_only=True)
    job = store.claim()
    assert job["snapshot"]["suggest_only"] is True
    result = execute_job(job, config, db, store)
    assert result["status"] == "completed", result["error"]
    assert result["result"]["suggest_only"] is True and result["result"]["applied"] is True
    assert result["result"]["candidate_count"] == (2 if with_matches else 0)
    assert ("Use scene" if with_matches else "No fitting scenes") in result["result"]["message"]
    saved = store.get_project(project["id"])
    assert saved["revision"] == project["revision"] + 1
    before, after = project["document"], saved["document"]
    assert after["clips"] == before["clips"] and after["analysis"] == before["analysis"] and after["rhythm"] == before["rhythm"]
    assert after["music_timeline"]["slots"][0] == before["music_timeline"]["slots"][0]
    assert after["music_timeline"]["slots"][2] == before["music_timeline"]["slots"][2]
    forbidden.assert_not_called()


def test_manual_search_before_listening_keeps_analysis_absent_and_never_decodes(config, store, db, tmp_path, monkeypatch):
    project = _project(store, db, tmp_path, user=(1,))
    document = {**project["document"], "analysis": None}
    project = store.update_project(project["id"], project["revision"], document)
    forbidden = MagicMock(side_effect=AssertionError("Written scene search does not listen or select"))
    monkeypatch.setattr(music, "_decode_audio", forbidden)
    monkeypatch.setattr(music, "_hosted_json", forbidden)
    monkeypatch.setattr(music, "retrieve_edit_candidates", lambda *_: _rows(2))
    store.enqueue("draft", project["id"], project["revision"], slot_ids=["slot-1"], suggest_only=True)
    result = execute_job(store.claim(), config, db, store)
    assert result["status"] == "completed", result["error"]
    saved = store.get_project(project["id"])
    assert saved["document"]["analysis"] is None
    assert saved["document"]["clips"] == project["document"]["clips"]
    assert len(saved["document"]["music_timeline"]["slots"][1]["alternatives"]) == 2
    forbidden.assert_not_called()


def test_empty_placeholder_requires_a_written_query_before_manual_search(config, monkeypatch):
    document = _selected_document(); document["analysis"] = None
    slot = document["music_timeline"]["slots"][1]; slot["direction"] = None
    retrieve = MagicMock(side_effect=AssertionError("No invented query"))
    monkeypatch.setattr(music, "retrieve_edit_candidates", retrieve)
    with pytest.raises(ValueError, match="its own search direction"):
        music_planner.fill_timeline(document, config, object(), lambda _: None, "empty", [slot["id"]], suggest_only=True)
    retrieve.assert_not_called()


def test_automatic_fill_still_requires_interpretation(config, monkeypatch):
    document = _selected_document(); document["analysis"] = None
    retrieve = MagicMock(side_effect=AssertionError("Automatic filling still requires listening"))
    monkeypatch.setattr(music, "retrieve_edit_candidates", retrieve)
    with pytest.raises(ValueError, match="Analyze this music passage"):
        music_planner.fill_timeline(document, config, object(), lambda _: None, "auto", [document["music_timeline"]["slots"][1]["id"]])
    retrieve.assert_not_called()


def test_api_freezes_candidate_only_option(config, store, db, tmp_path):
    from pipeline.api.main import app
    project = _project(store, db, tmp_path, filled=(1,))
    with patch("pipeline.api.main.load_config", return_value=config), patch("pipeline.api.main.open_db", return_value=db), patch("pipeline.api.main.ensure_search_indexes"), TestClient(app) as client:
        response = client.post(f"/lab/projects/{project['id']}/jobs", json={"kind": "draft", "base_revision": project["revision"],
                                                                        "slot_ids": ["slot-1"], "suggest_only": True})
        assert response.status_code == 200, response.text
        assert store.get_job(response.json()["id"], private=True)["snapshot"]["suggest_only"] is True


@pytest.mark.parametrize("during_search", ["cancel", "new_revision"])
def test_candidate_search_cannot_overwrite_cancellation_or_newer_edits(config, store, db, tmp_path, monkeypatch, during_search):
    project = _project(store, db, tmp_path, filled=(1,))
    store.enqueue("draft", project["id"], project["revision"], slot_ids=["slot-1"], suggest_only=True)
    job = store.claim()
    def retrieve(*_):
        if during_search == "cancel":
            store.cancel(job["id"])
        else:
            store.update_project(project["id"], project["revision"], {**project["document"], "brief": "My newer edit"})
        return _rows(1)
    monkeypatch.setattr(music, "retrieve_edit_candidates", retrieve)
    result = execute_job(job, config, db, store)
    saved = store.get_project(project["id"])
    assert saved["document"]["clips"] == project["document"]["clips"]
    assert saved["document"]["music_timeline"] == project["document"]["music_timeline"]
    if during_search == "cancel":
        assert result["status"] == "cancelled" and saved == project
    else:
        assert result["status"] == "completed" and result["result"]["applied"] is False
        assert saved["document"]["brief"] == "My newer edit"
        assert len(result["result"]["document"]["music_timeline"]["slots"][1]["alternatives"]) == 1
