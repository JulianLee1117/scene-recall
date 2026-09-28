"""Real bridge seam assembly and integrity, plus offline provider contracts."""
from copy import deepcopy
import io
import json
from pathlib import Path
import shutil

import pytest
from pydantic import ValidationError

from pipeline.ingest.probe import _content_hash
from pipeline.lab.media import JobCancelled, run_process
from pipeline.lab.store import LabStore
from pipeline.transitions import bridges, jobs
from pipeline.transitions.contracts import RenderRequest


@pytest.fixture
def bridge_pair(config, monkeypatch):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("FFmpeg is required")
    config.paths.films_dir.mkdir(parents=True)
    films = {}
    for name, color in (("a", "red"), ("b", "blue")):
        path = config.paths.films_dir / f"{name}.mp4"
        run_process(["ffmpeg", "-v", "error", "-nostdin", "-f", "lavfi", "-i",
                     f"color={color}:s=192x108:r=30", "-t", "1", "-c:v", "libx264", "-threads", "1", str(path)])
        identity = _content_hash(path)
        films[identity] = {"film_id": identity, "path": str(path), "duration": 1., "title": name}
    monkeypatch.setattr(jobs, "resolve_film", lambda _db, identity: films[identity])
    monkeypatch.setattr(jobs, "dimensions", lambda _output: (192, 108))
    source_ids = list(films)
    request = RenderRequest(outgoing={"film_id": source_ids[0], "source_start": 0, "source_end": .8},
                            incoming={"film_id": source_ids[1], "source_start": 0, "source_end": .8},
                            recipe={"id": "hard-cut"}).model_dump(mode="json")
    store = LabStore(config.paths.state_dir, config.paths.assets_dir)
    store.initialize()
    queued = store.enqueue_transition_render(jobs.freeze(request, None))
    result = jobs.run(store.claim(role="editor"), config, None, store, lambda _: None, lambda: False)
    store.finish(queued["id"], result=result)
    path = config.paths.films_dir / "bridge.mp4"
    run_process(["ffmpeg", "-v", "error", "-nostdin", "-f", "lavfi", "-i",
                 "color=lime:s=192x108:r=24", "-t", "1", "-c:v", "libx264", "-threads", "1", str(path)])
    return store, queued["id"], path, films


def prepare(config, bridge_pair, **kwargs):
    store, parent_id, path, _films = bridge_pair
    with path.open("rb") as stream:
        return bridges.prepare_import({"parent_render_id": parent_id, **kwargs}, stream,
                                      "external-bridge.mp4", config, None, store)


def complete(config, bridge_pair, proposal):
    store = bridge_pair[0]
    job = {"id": proposal["job_id"], "kind": "transition-bridge", "status": "running",
           "cancel_requested": False, "snapshot": {"transition_bridge": proposal}}
    job["result"] = bridges.run(job, config, None, store, lambda _: None, lambda: False)
    job["status"] = "completed"
    return job


def _pixel(path, timestamp):
    raw = run_process(["ffmpeg", "-v", "error", "-ss", str(timestamp), "-i", str(path),
                       "-vf", "scale=1:1", "-frames:v", "1", "-pix_fmt", "rgb24", "-f", "rawvideo", "-"])
    return tuple(raw[:3])


def test_import_assembles_a_bridge_b_with_exact_timing_and_original_receipt(config, bridge_pair):
    proposal = prepare(config, bridge_pair, trim_start=.125, trim_end=.875, playback_duration=.4,
                       provenance={"provider": "Example", "model": "external-v1", "seed": 42})
    job = complete(config, bridge_pair, proposal)
    result = job["result"]
    assert result["duration"] == pytest.approx(2.)
    assert result["transition_start"] == pytest.approx(.8)
    assert result["transition_end"] == pytest.approx(1.2)
    path = bridges.artifact(config, job, "video")
    for timestamp, channel in ((.3, 0), (1., 1), (1.5, 2)):
        pixel = _pixel(path, timestamp)
        assert pixel[channel] > 200 and all(value < 30 for index, value in enumerate(pixel) if index != channel)
    manifest = json.loads(bridges.artifact(config, job, "manifest").read_text())
    assert manifest["segment_frame_counts"] == [24, 12, 24]
    assert manifest["retime_factor"] == pytest.approx(.4 / .75)
    assert manifest["input"]["provenance_status"] == "user-supplied-unverified"
    assert str(config.paths.films_dir) not in json.dumps(manifest)
    assert bridges.artifact(config, job, "original").read_bytes() == bridge_pair[2].read_bytes()
    assert not list(path.parent.glob("clip-*.mp4"))
    assert not list(path.parent.glob("*.partial*"))


def test_owned_endpoints_survive_parent_cancellation(config, bridge_pair):
    proposal = prepare(config, bridge_pair)
    bridge_pair[0].cancel(bridge_pair[1])
    job = complete(config, bridge_pair, proposal)
    assert bridges.artifact(config, job, "a").exists()


def test_mutated_source_or_import_cannot_render_or_download(config, bridge_pair):
    proposal = prepare(config, bridge_pair)
    job = complete(config, bridge_pair, proposal)
    original = bridges.artifact(config, job, "original")
    original.write_bytes(original.read_bytes() + b"mutated")
    with pytest.raises(ValueError, match="changed"):
        bridges.artifact(config, job, "video")
    source = Path(next(iter(bridge_pair[3].values()))["path"])
    proposal = prepare(config, bridge_pair)
    source.write_bytes(source.read_bytes() + b"changed")
    with pytest.raises(ValueError, match="source film.*changed"):
        complete(config, bridge_pair, proposal)


def test_cancel_cleans_encodes_but_retains_import(config, bridge_pair):
    proposal = prepare(config, bridge_pair)
    job = {"id": proposal["job_id"], "snapshot": {"transition_bridge": proposal}}
    with pytest.raises(JobCancelled):
        bridges.run(job, config, None, bridge_pair[0], lambda _: None, lambda: True)
    root = jobs.output_root(config, proposal["job_id"])
    assert (root / "original.media").exists()
    assert not list(root.glob("*.mp4"))


def test_invalid_container_and_out_of_range_trim_leave_no_import(config, bridge_pair):
    root = config.paths.assets_dir / "lab" / "renders"
    before = set(root.iterdir())
    with pytest.raises(ValueError, match="container"):
        bridges.prepare_import({"parent_render_id": bridge_pair[1]}, io.BytesIO(b"#EXTM3U\nhttp://127.0.0.1"),
                               "attack.mp4", config, None, bridge_pair[0])
    with pytest.raises(ValueError, match="outside"):
        prepare(config, bridge_pair, trim_end=2)
    assert set(root.iterdir()) == before


def test_upload_byte_cap_is_enforced_before_probe(config, bridge_pair, monkeypatch):
    monkeypatch.setattr(bridges, "MAX_UPLOAD_BYTES", 10)
    with pytest.raises(ValueError, match="128 MiB"):
        prepare(config, bridge_pair)


@pytest.mark.parametrize("extra", [
    {"trim_end": float("nan")}, {"playback_duration": 31}, {"trim_start": 1, "trim_end": .5},
    {"path": "C:/private.mov"}, {"provenance": {"seed": -1}},
])
def test_unbounded_import_contract_is_rejected(extra):
    with pytest.raises(ValidationError):
        bridges.BridgeImportRequest.model_validate({"parent_render_id": "12345678-1234-1234-1234-123456789abc", **extra})


def test_forged_job_cannot_claim_another_import(config, bridge_pair):
    proposal = prepare(config, bridge_pair)
    job = complete(config, bridge_pair, proposal)
    altered = deepcopy(job)
    altered["id"] = "12345678-1234-1234-1234-123456789abc"
    with pytest.raises(ValueError, match="different job"):
        bridges.artifact(config, altered, "video")


@pytest.fixture
def provider_frames(tmp_path):
    from PIL import Image
    paths = [tmp_path / "first.jpg", tmp_path / "last.jpg"]
    for path, color in zip(paths, ("red", "blue")):
        Image.new("RGB", (640, 360), color).save(path)
    return paths


def test_provider_readiness_and_quote_are_local_and_key_presence_only(config, bridge_pair, monkeypatch):
    from pipeline.transitions import providers
    monkeypatch.setenv(providers.KEY_ENV, "super-secret-test-key")
    status = providers.provider_status()
    assert status["configured"] and not status["live_verified"] and status["generation_enabled"]
    assert "super-secret" not in json.dumps(status)
    quoted = providers.quote({"parent_render_id": bridge_pair[1], "prompt": "Dolly through", "resolution": "1080p"},
                             config, None, bridge_pair[0])
    assert quoted["estimated_credits"] == 272 and quoted["ratio"] == "1920:1080"
    assert quoted["estimated_usd"] == 2.72 and quoted["within_request_ceiling"]


@pytest.mark.parametrize("retime", [None, {"mode": "off"}])
def test_old_and_explicit_off_parents_remain_bridge_compatible(config, bridge_pair, retime):
    parent = bridge_pair[0].get_job(bridge_pair[1], private=True)
    request = parent["snapshot"]["transition_render"]["request"]
    if retime is None:
        request.pop("retime", None)
    else:
        request["retime"] = retime
    # Simulate a complete historical receipt, not a tampered request paired with
    # a different manifest. Artifact ownership must remain enforced.
    manifest_path = jobs.output_root(config, parent["id"]) / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["request"] = request
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    parent["result"]["manifest_sha256"] = jobs.sha256(manifest_path)
    source, _ = bridges._check_parent(parent, config, None)
    assert source["sources"] == parent["snapshot"]["transition_render"]["sources"]


def test_retimed_parent_rejects_import_and_quote_before_copy(config, bridge_pair, monkeypatch):
    from pipeline.transitions import providers
    store = bridge_pair[0]
    parent = store.get_job(bridge_pair[1], private=True)
    parent["snapshot"]["transition_render"]["request"]["retime"] = {"mode": "rush"}
    monkeypatch.setattr(store, "get_job", lambda *_a, **_k: parent)
    with pytest.raises(ValueError, match="Render a version with Speed off"):
        providers.quote({"parent_render_id": parent["id"], "prompt": "Move through"}, config, None, store)
    stream = io.BytesIO(b"must not be copied")
    with pytest.raises(ValueError, match="Render a version with Speed off"):
        bridges.prepare_import({"parent_render_id": parent["id"]}, stream, "test.mp4", config, None, store)
    assert stream.tell() == 0


def test_import_worker_rechecks_frozen_source_timing(config):
    proposal = {"bridge_version": bridges.BRIDGE_VERSION,
                "source_snapshot": {"request": {"retime": {"mode": "rush"}}}}
    with pytest.raises(ValueError, match="Render a version with Speed off"):
        bridges._verify(proposal, config, None, None)


def test_provider_requires_explicit_bounded_spend():
    from pipeline.transitions.providers import RunwayRequest
    for changes in ({"max_credits": 119}, {"max_credits": 301}, {"duration": 9},
                    {"duration": 4.0}, {"seed": -1}, {"resolution": "1080p", "max_credits": 271}):
        with pytest.raises(ValidationError):
            RunwayRequest.model_validate({"prompt": "Bridge", "max_credits": 120, **changes})


def test_one_shot_submission_preserves_receipt_and_never_replays(tmp_path, provider_frames, monkeypatch):
    from pipeline.transitions import providers
    client = providers.RunwayClient(key="test-key")
    requests = []
    task_id = "12345678-1234-1234-1234-123456789abc"
    def request(method, path, body=None):
        requests.append((method, path, body))
        assert json.loads((tmp_path / "request.json").read_text())["state"] == "submitting"
        return {"id": task_id, "estimatedCost": {"credits": 120}}
    monkeypatch.setattr(client, "_request", request)
    receipt = client.submit({"prompt": "Bridge", "max_credits": 120}, *provider_frames, tmp_path / "request.json")
    assert receipt["task_id"] == task_id
    assert requests[0][2]["model"] == "seedance2_5" and requests[0][2]["audio"] is False
    assert [item["position"] for item in requests[0][2]["promptImage"]] == ["first", "last"]
    assert "test-key" not in (tmp_path / "request.json").read_text()
    assert "data:image" not in (tmp_path / "request.json").read_text()
    with pytest.raises(ValueError, match="already has"):
        client.submit({"prompt": "Bridge", "max_credits": 120}, *provider_frames, tmp_path / "request.json")
    assert len(requests) == 1


def test_uncertain_submission_stays_nonreplayable(tmp_path, provider_frames, monkeypatch):
    from pipeline.transitions.providers import RunwayClient
    client = RunwayClient(key="test-key")
    def failed(*_args):
        raise ValueError("Connection lost after submit")
    monkeypatch.setattr(client, "_request", failed)
    path = tmp_path / "request.json"
    with pytest.raises(ValueError, match="Connection lost"):
        client.submit({"prompt": "Bridge", "max_credits": 120}, *provider_frames, path)
    assert json.loads(path.read_text())["state"] == "submission_uncertain"
    with pytest.raises(ValueError, match="already has"):
        client.submit({"prompt": "Bridge", "max_credits": 120}, *provider_frames, path)


def test_completed_task_cancel_does_not_delete_and_poll_is_persisted(tmp_path, monkeypatch):
    from pipeline.transitions.providers import RunwayClient
    client = RunwayClient(key="test-key")
    task_id = "12345678-1234-1234-1234-123456789abc"
    calls = []
    def request(method, path, body=None):
        calls.append(method)
        return {"id": task_id, "status": "SUCCEEDED", "output": ["https://example.cloudfront.net/video.mp4"]}
    monkeypatch.setattr(client, "_request", request)
    assert client.cancel(task_id) == {"status": "SUCCEEDED", "cancelled": False}
    path = tmp_path / "request.json"
    path.write_text(json.dumps({"task_id": task_id}))
    assert client.poll_receipt(path)["state"] == "succeeded"
    assert calls == ["GET", "GET"]


def test_provider_download_rejects_private_urls_before_io(tmp_path):
    from pipeline.transitions.providers import RunwayClient
    client = RunwayClient()
    for url in ("http://example.cloudfront.net/video.mp4", "https://localhost/x", "file:///secret",
                "https://example.cloudfront.net.evil.test/x", "https://user:password@example.cloudfront.net/x"):
        with pytest.raises(ValueError, match="HTTPS"):
            client.download(url, tmp_path / "output.mp4")
    assert not (tmp_path / "output.mp4").exists()
