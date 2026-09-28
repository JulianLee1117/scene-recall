"""The global timing schema and local checks share exact passage bounds."""
from copy import deepcopy

import pytest

from pipeline.lab import music, timing_planner
from pipeline.tests.test_lab_timing_planner import _document, _job, _output


@pytest.mark.parametrize("start,duration,frames", [(76.49, 22.5, 540), (53.99, 90., 2160), (9.94, 30.023, 721), (0., 600., 14400)])
def test_schema_bounds_all_cut_and_note_frames(config, monkeypatch, start, duration, frames):
    document = _document(start=start, duration=duration)
    def hosted(_config, _prompt, schema, **_kwargs):
        assert schema["properties"]["end_frames"]["items"]["maximum"] == frames
        assert schema["$defs"]["TimingNote"]["properties"]["end_frame"]["maximum"] == frames
        assert schema["$defs"]["TimingNote"]["properties"]["start_frame"]["maximum"] == frames
        return _output([frames])
    monkeypatch.setattr(music, "_hosted_json", hosted)
    result = timing_planner.run_timing_job(_job(document), config, lambda _: None)
    assert result["music_timeline"]["slots"][-1]["end"] == start + duration


def test_recorded_processing_scope_overshoot_rejected_without_repair_retry_or_cache(config, monkeypatch):
    document = _document(start=76.49, duration=22.5)
    before, calls = deepcopy(document), []
    def hosted(*_args, **_kwargs):
        calls.append(True)
        return _output([300, 541])
    monkeypatch.setattr(music, "_hosted_json", hosted)
    with pytest.raises(ValueError, match="at most 540"):
        timing_planner.run_timing_job(_job(document), config, lambda _: None)
    assert calls == [True] and document == before
    assert not list((config.paths.assets_dir / "lab" / "timing-plans").glob("*.json"))
