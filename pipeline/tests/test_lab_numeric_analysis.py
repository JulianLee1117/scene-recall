"""Only authentic cached observations repair browser number normalization."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from pipeline.lab import music
from pipeline.lab.generation import _analysis_is_current
from pipeline.lab.long_audio import (compose_analysis, recover_numeric_analysis,
                                     validate_long_interpretation)
from pipeline.lab.models import ProjectDocument
from pipeline.tests.test_lab_long_audio import _part


def _browser_json(value):
    # JSON.stringify emits integer-valued numbers without Python's .0 suffix.
    def numbers(item):
        if isinstance(item, dict): return {key: numbers(value) for key, value in item.items()}
        if isinstance(item, list): return [numbers(value) for value in item]
        if isinstance(item, float) and item.is_integer(): return int(item)
        return item
    return json.loads(json.dumps(numbers(value)))


def _saved(config):
    parts = [_part(0., 90.), _part(90., 180.)]
    for part in parts:
        analysis = part["analysis"]
        analysis["provenance"]["evidence"] = {"pulses": [0., 25., 49.], "has_voice": True}
        identity = music.digest(analysis["provenance"])
        for key in ("events_provenance", "song_meaning_provenance"):
            analysis[key]["interpretation_id"] = identity
        music.write_json(config.paths.assets_dir / "lab" / "interpretations" / f"{identity}.json", analysis)
    original = compose_analysis(parts, {"start": 0., "end": 180.})
    saved = _browser_json(original)
    assert saved == original
    assert music.digest(saved["parts"][0]["analysis"]["provenance"]) != saved["parts"][0]["analysis"]["events_provenance"]["interpretation_id"]
    return original, saved


def _recover(saved, config, **overrides):
    return recover_numeric_analysis(saved, config, **{"track_id": "song", "passage": {"start": 0., "end": 180.}, **overrides})


def test_browser_numeric_roundtrip_restores_original_provenance_privately(config):
    original, saved = _saved(config)
    saved["draft"] = {"diagnostic": "Preserve this unrelated selection receipt"}
    before = deepcopy(saved)
    with pytest.raises(ValueError, match="provenance"):
        validate_long_interpretation(saved, {"start": 0., "end": 180.})
    restored = _recover(saved, config)
    assert restored is not None and saved == before
    assert restored["draft"] == saved["draft"]
    assert restored["parts"] == original["parts"]
    assert isinstance(restored["parts"][0]["analysis"]["provenance"]["passage"]["start"], float)
    validate_long_interpretation(restored, {"start": 0., "end": 180.})
    document = ProjectDocument(track={"id": "song", "name": "Song", "duration": 180},
        passage={"start": 0, "end": 180}, analysis=restored, planner_settings={"pacing": "rapid"}).model_dump(mode="json")
    assert _analysis_is_current(document)


@pytest.mark.parametrize("mutation", [
    lambda a: a["parts"][0]["analysis"]["events"][0].update(label="Changed heard event"),
    lambda a: a["parts"][0]["analysis"]["provenance"]["evidence"]["pulses"].__setitem__(1, 25.1),
    lambda a: a["parts"][0]["analysis"]["provenance"]["evidence"]["pulses"].__setitem__(0, False),
    lambda a: a["parts"][0]["analysis"]["provenance"]["evidence"].update(has_voice=1),
    lambda a: a["events"][0].update(label="Changed aggregate event"),
    lambda a: a["events"][0].update(start=False),
])
def test_changed_evidence_and_boolean_numeric_substitutions_are_rejected(config, mutation):
    _, saved = _saved(config)
    mutation(saved)
    assert _recover(saved, config) is None


@pytest.mark.parametrize("identity", ["../outside", "a" * 63, "A" * 64, "a" * 64 + "/other", None, True])
def test_malformed_cache_ids_never_read_a_path(config, monkeypatch, identity):
    _, saved = _saved(config)
    for key in ("events_provenance", "song_meaning_provenance"):
        saved["parts"][0]["analysis"][key]["interpretation_id"] = identity
    monkeypatch.setattr(Path, "read_text", lambda *_a, **_k: pytest.fail("Malformed IDs must not touch disk"))
    assert _recover(saved, config) is None


@pytest.mark.parametrize("issue", ["missing", "invalid-json", "wrong-digest", "changed-output"])
def test_missing_or_modified_original_cache_is_not_trusted(config, issue):
    _, saved = _saved(config)
    identity = saved["parts"][0]["analysis"]["events_provenance"]["interpretation_id"]
    path = config.paths.assets_dir / "lab" / "interpretations" / f"{identity}.json"
    if issue == "missing": path.unlink()
    elif issue == "invalid-json": path.write_text("broken", encoding="utf-8")
    else:
        cached = json.loads(path.read_text(encoding="utf-8"))
        if issue == "wrong-digest": cached["provenance"]["model"] = "Different model"
        else: cached["events"][0]["label"] = "Different observation"
        path.write_text(json.dumps(cached), encoding="utf-8")
    assert _recover(saved, config) is None


def test_valid_evidence_and_wrong_requested_scope_do_not_trigger_cache_reads(config, monkeypatch):
    original, saved = _saved(config)
    monkeypatch.setattr(Path, "read_text", lambda *_a, **_k: pytest.fail("No recovery should be attempted"))
    assert _recover(original, config) is None
    assert _recover(saved, config, track_id="other") is None
    assert _recover(saved, config, passage={"start": 0, "end": 181}) is None


@pytest.mark.parametrize("authentic", [True, False])
def test_pace_regeneration_recovers_or_uses_normal_listening_fallback(config, monkeypatch, tmp_path, authentic):
    from pipeline.lab import direction_planner, long_generation, music_planner, timing_planner
    from pipeline.lab.store import LabStore

    original, saved = _saved(config)
    if not authentic:
        saved["parts"][0]["analysis"]["events"][0]["label"] = "Modified observation"
    document = ProjectDocument(track={"id": "song", "name": "Song", "duration": 180},
        passage={"start": 0, "end": 180}, analysis=saved, planner_settings={"pacing": "rapid"}).model_dump(mode="json")
    before = deepcopy(document)
    source = tmp_path / "original.wav"
    source.write_bytes(b"retained audio")
    monkeypatch.setattr(LabStore, "get_track", lambda *_: {"id": "song", "path": str(source)})
    monkeypatch.setattr(music, "content_hash", lambda *_: "song")
    listening_calls = []
    def listen(job, *_args):
        assert not authentic, "Pace changes must reuse authentic music evidence"
        listening_calls.append(True)
        return {**deepcopy(job["document"]), "analysis": deepcopy(original)}
    monkeypatch.setattr(music, "run_music_job", listen)
    def timing(job, *_args):
        result = deepcopy(job["document"])
        assert _analysis_is_current(result)
        result["music_timeline"] = {"track_id": "song", "passage": result["passage"],
            "slots": [{"id": "hold", "start": 0., "end": 180., "section_index": 0}]}
        result["direction_plan"] = {"timing_plan": {"end_frames": [4320]}}
        return ProjectDocument.model_validate(result).model_dump(mode="json")
    monkeypatch.setattr(timing_planner, "run_timing_job", timing)
    monkeypatch.setattr(direction_planner, "run_direction_job", lambda job, *_: job["document"])
    monkeypatch.setattr(music_planner, "fill_timeline", lambda document, *_a, **_k: document)
    messages = []
    result, receipt = long_generation.run_long_generate_job(
        {"id": "recovery", "document": document, "snapshot": {"generate": {"mode": "regenerate"}}}, config, object(), messages.append)
    assert document == before and _analysis_is_current(result)
    assert ("analyze" in receipt["stages"]) is not authentic
    assert bool(listening_calls) is not authentic
    assert any("Restored the original cached" in message for message in messages) is authentic
