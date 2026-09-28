"""Music boundaries survive execution scopes and older listening evidence."""
from copy import deepcopy

import pytest

from pipeline.lab import music, timing_planner
from pipeline.lab.long_audio import analysis_for_passage
from pipeline.lab.music_evidence import music_evidence
from pipeline.tests.test_lab import db, store  # noqa: F401
from pipeline.tests.test_lab_long_generation import _project, _run, _stages


def test_global_hold_crosses_audio_parts_without_an_execution_cut(config, store, db, tmp_path, monkeypatch):
    project = _project(store, db, tmp_path, duration=120)
    calls = _stages(monkeypatch)
    original_timing = timing_planner.run_timing_job

    def timing(job, config, progress):
        document = original_timing(job, config, progress)
        document["music_timeline"]["slots"] = [
            {"id": "hold", "start": 0., "end": 100., "section_index": 0, "needs_direction": True},
            {"id": "answer", "start": 100., "end": 120., "section_index": 1, "needs_direction": True},
        ]
        document["direction_plan"]["timing_plan"]["end_frames"] = [2400, 2880]
        return document

    monkeypatch.setattr(timing_planner, "run_timing_job", timing)
    monkeypatch.setattr(music, "run_music_job", lambda *_: pytest.fail("Existing listening must be reused"))
    result, _ = _run(store, config, db, project, mode="regenerate")
    assert result["status"] == "completed", result["error"]
    selections = [call for call in calls if call["stage"] == "select"]
    assert [call["document"]["passage"] for call in selections] == [
        {"start": 0., "end": 100.}, {"start": 100., "end": 120.}]
    assert len(selections[0]["document"]["analysis"]["parts"]) == 2
    assert result["result"]["timing_plan"]["final_end_frames"] == [2400, 2880]
    assert store.get_project(project["id"])["revision"] == project["revision"] + 1


def test_legacy_listening_can_be_clipped_without_inventing_events_or_mutating_source():
    from pipeline.tests.test_lab_music import _interpretation

    source = {**_interpretation(10., 70.), "provenance": {"track": "track", "passage": {"start": 10., "end": 70.}}}
    original = deepcopy(source)
    passage = {"start": 20., "end": 40.}
    view = analysis_for_passage(source, passage)
    assert source == original
    assert view["segments"][0]["start"] == 20.
    assert view["segments"][-1]["end"] == 40.
    assert view["provenance"]["source_passage"] == source["provenance"]["passage"]
    assert "events_provenance" not in view and "song_meaning_provenance" not in view
    evidence = music_evidence({"track": {"id": "track"}, "passage": passage, "analysis": view})
    assert evidence["audio_interpretation"] is not None
    assert evidence["audio_observations"] is None
    assert evidence["song_meaning"] is None
