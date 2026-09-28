"""Observed H3 timing variance stays explicit, bounded, and unretimed."""
import json
import shutil
import uuid

import pytest

from pipeline.lab.media import run_process
from pipeline.transitions import bridges, generation, jobs, providers
from pipeline.tests.test_transition_bridges import bridge_pair as bridge_pair
from pipeline.tests.test_transition_generation import job_for


@pytest.mark.parametrize("model,actual", [("h3_max", 5.251), ("h3_max", 4.849),
                                          ("h3_max", 6.), ("wan3", 2.184),
                                          ("seedance2_5", 4.184)])
def test_duration_compatibility_does_not_admit_larger_or_other_model_mismatches(tmp_path, monkeypatch, model, actual):
    requested = {"h3_max": 5, "wan3": 2, "seedance2_5": 4}[model]
    monkeypatch.setattr(bridges, "_media_info", lambda _path: {"duration": actual})
    with pytest.raises(ValueError, match="duration differs"):
        generation._assembly({"request": {"duration": requested, "model": model}}, tmp_path)
    assert not list(tmp_path.iterdir()), "rejection leaves the downloaded original and prior receipts untouched"


def test_real_h3_overrun_and_audio_tail_are_preserved_in_full_assembly(config, bridge_pair, monkeypatch):
    monkeypatch.setenv(providers.KEY_ENV, "offline-timing-test")
    store, parent, source, _films = bridge_pair
    quoted = providers.quote({"parent_render_id": parent, "model": "h3_max", "prompt": "Pass through the arch"}, config, None, store)
    body = {**quoted["request"], "request_id": str(uuid.uuid4()), "max_credits": 40,
            "price_version": quoted["price_version"], "parent_manifest_sha256": quoted["parent_manifest_sha256"],
            "confirm_spend": True}
    proposal = generation.prepare(body, config, None, store)
    picture = source.with_name("h3-picture.mp4")
    generated = source.with_name("h3-with-audio.mp4")
    run_process(["ffmpeg", "-v", "error", "-nostdin", "-f", "lavfi", "-i", "color=lime:s=192x108:r=24",
                 "-frames:v", "124", "-c:v", "libx264", "-threads", "1", str(picture)])
    run_process(["ffmpeg", "-v", "error", "-nostdin", "-i", str(picture), "-f", "lavfi", "-i", "anullsrc=r=32000:cl=stereo",
                 "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", "-t", "5.184", str(generated)])
    probe = bridges._probe(generated)
    video = next(row for row in probe["streams"] if row["codec_type"] == "video")
    actual = float(probe["format"]["duration"])
    assert int(video["nb_frames"]) == 124 and video["avg_frame_rate"] == "24/1"
    assert float(video["duration"]) == pytest.approx(124 / 24, abs=1e-6)
    assert actual == pytest.approx(5.184, abs=.001)
    calls, task_id = [], str(uuid.uuid4())

    def request(_self, method, _path, _body=None):
        calls.append(method)
        if method == "POST":
            return {"id": task_id, "estimatedCost": {"credits": 40}}
        assert method == "GET"
        return {"id": task_id, "status": "SUCCEEDED", "cost": {"credits": 40},
                "output": ["https://example.cloudfront.net/output"]}

    monkeypatch.setattr(providers.RunwayClient, "_request", request)
    monkeypatch.setattr(providers.RunwayClient, "download", lambda _self, _url, destination, **_kwargs: shutil.copyfile(generated, destination))
    monkeypatch.setattr(generation, "_wait", lambda *_args: None)
    job = job_for(proposal)
    job["result"] = generation.run(job, config, None, store, lambda _: None, lambda: False)
    job["status"] = "completed"
    manifest = json.loads(generation.artifact(config, job, "manifest").read_text())
    assert manifest["request"]["trim_end"] == actual
    assert manifest["request"]["playback_duration"] == actual
    assert manifest["retime_factor"] == 1
    assert manifest["segment_frame_counts"] == [24, 156, 24], "full measured duration uses the existing 30fps assembly grid"
    validation = manifest["input"]["duration_validation"]
    assert validation["requested_seconds"] == 5 and validation["actual_seconds"] == actual
    assert validation["maximum_overrun_seconds"] == .25 and validation["maximum_underrun_seconds"] == .15
    assert validation["preserved_at_original_speed"] is True
    assert generation.artifact(config, job, "original").read_bytes() == generated.read_bytes()
    assert calls == ["POST", "GET"], "accepting the valid original never submits a replacement generation"
