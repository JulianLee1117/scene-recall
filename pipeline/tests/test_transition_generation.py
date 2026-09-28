"""Offline provider lifecycle, spend guards, and real generated-bridge assembly."""
from copy import deepcopy
import json
from pathlib import Path
import shutil
from types import SimpleNamespace
import uuid

import pytest
from pydantic import ValidationError

from pipeline.lab.media import JobCancelled, run_process
from pipeline.transitions import bridges, generation, jobs, providers
from pipeline.tests.test_transition_bridges import bridge_pair, provider_frames, _pixel


@pytest.fixture
def prepared(config, bridge_pair, monkeypatch):
    monkeypatch.setenv(providers.KEY_ENV, "offline-test-secret")
    store, parent, source, _ = bridge_pair
    quoted = providers.quote({"parent_render_id": parent, "prompt": "Move from red through green to blue"}, config, None, store)
    request = {**quoted["request"], "request_id": str(uuid.uuid4()), "seed": 42, "max_credits": 120,
               "price_version": quoted["price_version"], "parent_manifest_sha256": quoted["parent_manifest_sha256"],
               "confirm_spend": True}
    proposal = generation.prepare(request, config, None, store)
    generated = source.with_name("generated.mp4")
    run_process(["ffmpeg", "-v", "error", "-nostdin", "-f", "lavfi", "-i", "color=lime:s=192x108:r=24",
                 "-t", "4", "-c:v", "libx264", "-threads", "1", str(generated)])
    return proposal, generated


def job_for(proposal, status="running"):
    return {"id": proposal["job_id"], "kind": "transition-generate", "status": status,
            "cancel_requested": False, "snapshot": {"transition_generation": proposal}}


def provider(monkeypatch, config, proposal, generated, statuses=("RUNNING", "SUCCEEDED")):
    calls, statuses = [], iter(statuses)
    task = "12345678-1234-1234-1234-123456789abc"
    root = jobs.output_root(config, proposal["job_id"])
    def request(_self, method, path, body=None):
        calls.append((method, path, body))
        if method == "POST":
            receipt = json.loads((root / "provider-task.json").read_text())
            assert receipt["state"] == "submitting" and "task_id" not in receipt
            assert body["audio"] is False and body["duration"] == 4
            assert body["promptImage"][0]["position"] == "first"
            return {"id": task, "estimatedCost": {"credits": 120}}
        if method == "DELETE":
            return {}
        status = next(statuses, "RUNNING")
        return {"id": task, "status": status, "cost": {"credits": 118},
                "output": ["https://outputs.runwayml.com/bridge.mp4?private-signed-token"],
                "failure": "private-provider-detail"}
    monkeypatch.setattr(providers.RunwayClient, "_request", request)
    monkeypatch.setattr(providers.RunwayClient, "download", lambda _self, url, dest, **_kw: shutil.copyfile(generated, dest))
    monkeypatch.setattr(generation, "_wait", lambda cancelled, deadline: (_ for _ in ()).throw(JobCancelled("cancelled")) if cancelled() else None)
    # Speed cancellation preflight spacing without affecting provider timestamps.
    monkeypatch.setattr(generation, "time", SimpleNamespace(time=providers.time.time,
                        monotonic=providers.time.monotonic, sleep=lambda _seconds: None))
    return calls


def test_real_generation_owns_normalized_inputs_and_assembles_full_original(config, bridge_pair, prepared, monkeypatch):
    proposal, generated = prepared
    root = jobs.output_root(config, proposal["job_id"])
    from PIL import Image
    for side in ("a", "b"):
        with Image.open(root / f"provider-{side}.jpg") as image:
            assert image.size == (1280, 720)
        with Image.open(root / f"frame-{side}.jpg") as image:
            assert image.size == (192, 108)
    calls = provider(monkeypatch, config, proposal, generated)
    bridge_pair[0].cancel(bridge_pair[1])
    job = job_for(proposal)
    job["result"] = generation.run(job, config, None, bridge_pair[0], lambda _: None, lambda: False)
    job["status"] = "completed"
    result = job["result"]
    assert result["duration"] == pytest.approx(5.6)
    assert result["transition_end"] - result["transition_start"] == pytest.approx(4.)
    assert result["estimated_credits"] == 120 and result["actual_credits"] == 118
    assert result["provenance_status"] == "provider-task-verified"
    assert f"/generations/{job['id']}/video" in result["preview_url"]
    manifest = json.loads(generation.artifact(config, job, "manifest").read_text())
    assert manifest["segment_frame_counts"] == [24, 120, 24]
    assert manifest["retime_factor"] == 1 and manifest["generation"]["request"] == proposal["request"]
    video = generation.artifact(config, job, "video")
    assert _pixel(video, .4)[0] > 200 and _pixel(video, 2)[1] > 200 and _pixel(video, 5.2)[2] > 200
    assert generation.artifact(config, job, "original").read_bytes() == generated.read_bytes()
    for kind in ("a", "b", "first", "last", "provider-a", "provider-b", "receipt"):
        assert generation.artifact(config, job, kind).exists()
    public = generation.receipt(config, job)
    assert public["actual_credits"] == 118
    text = json.dumps([result, manifest, public])
    for private in ("private-signed-token", "private-provider-detail", "offline-test-secret", str(config.paths.films_dir), "data:image"):
        assert private not in text
    assert [call[0] for call in calls] == ["POST", "GET", "GET"]
    assert not list(root.glob("clip-*.mp4"))
    with pytest.raises(ValueError, match="already started"):
        generation.run(job, config, None, bridge_pair[0], lambda _: None, lambda: False)
    assert len(calls) == 3
    # Historical artifacts retain their original provider identity when future
    # adapters change the current model constant.
    monkeypatch.setattr(providers, "MODEL", "future-model")
    assert generation.receipt(config, job)["model"] == "seedance2_5"
    assert generation.artifact(config, job, "video").exists()


def test_request_id_reuses_only_identical_preparation_without_new_side_effects(config, bridge_pair, prepared):
    proposal, _ = prepared
    assert generation.prepare(proposal["request"], config, None, bridge_pair[0]) == proposal
    changed = {**proposal["request"], "prompt": "different"}
    with pytest.raises(ValueError, match="different generation"):
        generation.prepare(changed, config, None, bridge_pair[0])
    assert generation.receipt(config, job_for(proposal, "queued"))["state"] == "not_submitted"


def test_retimed_parent_and_queued_snapshot_never_start_generation(config, bridge_pair, prepared, monkeypatch):
    proposal, generated = prepared
    store = bridge_pair[0]
    parent = store.get_job(bridge_pair[1], private=True)
    parent["snapshot"]["transition_render"]["request"]["retime"] = {"mode": "rush"}
    monkeypatch.setattr(store, "get_job", lambda *_a, **_k: parent)
    body = {**proposal["request"], "request_id": str(uuid.uuid4())}
    with pytest.raises(ValueError, match="Render a version with Speed off"):
        generation.prepare(body, config, None, store)
    assert not jobs.output_root(config, body["request_id"]).exists()
    calls = provider(monkeypatch, config, proposal, generated)
    queued = deepcopy(proposal)
    queued["source_snapshot"]["request"]["retime"] = {"mode": "rush"}
    with pytest.raises(ValueError, match="Render a version with Speed off"):
        generation.run(job_for(queued), config, None, store, lambda _: None, lambda: False)
    assert calls == []
    assert not (jobs.output_root(config, proposal["job_id"]) / "provider-task.json").exists()


@pytest.mark.parametrize("change", [
    {"confirm_spend": False}, {"confirm_spend": "true"}, {"price_version": "stale"},
    {"max_credits": 119}, {"max_credits": 301}, {"duration": 3}, {"duration": 4.0},
    {"request_id": "not-a-uuid"}, {"parent_manifest_sha256": "bad"}, {"prompt": "   "},
])
def test_spend_and_identity_authorization_is_strict(change):
    body = {"parent_render_id": str(uuid.uuid4()), "request_id": str(uuid.uuid4()), "prompt": "A bridge",
            "max_credits": 120, "price_version": providers.PRICE_VERSION, "parent_manifest_sha256": "a" * 64,
            "confirm_spend": True, **change}
    with pytest.raises(ValidationError):
        generation.GenerateRequest.model_validate(body)


def test_stale_quote_missing_key_or_version_change_never_submits(config, bridge_pair, prepared, monkeypatch):
    proposal, _ = prepared
    body = {**proposal["request"], "request_id": str(uuid.uuid4()), "parent_manifest_sha256": "a" * 64}
    with pytest.raises(ValueError, match="reviewed quote"):
        generation.prepare(body, config, None, bridge_pair[0])
    monkeypatch.delenv(providers.KEY_ENV)
    with pytest.raises(ValueError, match="Configure"):
        generation.prepare(body, config, None, bridge_pair[0])
    monkeypatch.setattr(bridges, "BRIDGE_VERSION", "new-version")
    with pytest.raises(ValueError, match="version changed"):
        generation.run(job_for(proposal), config, None, bridge_pair[0], lambda _: None, lambda: False)
    assert not (jobs.output_root(config, proposal["job_id"]) / "provider-task.json").exists()


def test_uncertain_submit_retains_receipt_never_replays_and_is_read_only(config, bridge_pair, prepared, monkeypatch):
    proposal, generated = prepared
    calls = provider(monkeypatch, config, proposal, generated)
    def uncertain(_self, *args):
        calls.append(args)
        raise ValueError("Connection ended before a confirmed task ID")
    monkeypatch.setattr(providers.RunwayClient, "_request", uncertain)
    job = job_for(proposal)
    with pytest.raises(ValueError, match="Connection ended"):
        generation.run(job, config, None, bridge_pair[0], lambda _: None, lambda: False)
    job["status"] = "interrupted"
    assert generation.receipt(config, job)["state"] == "submission_uncertain"
    root = jobs.output_root(config, proposal["job_id"])
    # Simulate a writer's temporary receipt; reading cannot collide with it.
    (root / "provider-public.json.partial").write_text("writer owns this")
    assert generation.receipt(config, job)["state"] == "submission_uncertain"
    with pytest.raises(ValueError, match="already started"):
        generation.run(job, config, None, bridge_pair[0], lambda _: None, lambda: False)
    assert len(calls) == 1


def test_user_cancellation_attempts_remote_delete_and_preserves_reconciliation(config, bridge_pair, prepared, monkeypatch):
    proposal, generated = prepared
    calls = provider(monkeypatch, config, proposal, generated)
    job = job_for(proposal)
    with pytest.raises(JobCancelled):
        generation.run(job, config, None, bridge_pair[0], lambda _: None, lambda: bool(calls))
    job["status"] = "cancelled"
    public = generation.receipt(config, job)
    assert public["cancellation_requested"] and public["cancellation_confirmed"]
    assert [call[0] for call in calls] == ["POST", "GET", "DELETE"]
    assert not (jobs.output_root(config, proposal["job_id"]) / "original.media").exists()
    again = generation.cancel_remote(config, job)
    assert again["cancellation_confirmed"] and again["state"] == "cancelled"
    assert len(calls) == 3


@pytest.mark.parametrize("status,expected", [("FAILED", ValueError), ("CANCELLED", JobCancelled)])
def test_terminal_provider_failures_do_not_download_or_delete(config, bridge_pair, prepared, monkeypatch, status, expected):
    proposal, generated = prepared
    calls = provider(monkeypatch, config, proposal, generated, (status,))
    with pytest.raises(expected):
        generation.run(job_for(proposal), config, None, bridge_pair[0], lambda _: None, lambda: False)
    assert [call[0] for call in calls] == ["POST", "GET"]


def test_poll_wait_respects_five_seconds_deadline_and_cancellation(monkeypatch):
    class Clock:
        value = 0.
        def monotonic(self): return self.value
        def sleep(self, duration): self.value += duration
    clock = Clock()
    monkeypatch.setattr(generation, "time", clock)
    generation._wait(lambda: False, 100)
    assert clock.value == 5.
    generation._wait(lambda: False, 100)
    assert clock.value == 10.
    with pytest.raises(ValueError, match="twenty-minute"):
        generation._wait(lambda: False, 12)
    assert clock.value == 12.
    with pytest.raises(JobCancelled):
        generation._wait(lambda: True, 100)


def test_mutated_endpoint_or_submission_receipt_cannot_be_used(config, bridge_pair, prepared):
    proposal, _ = prepared
    root = jobs.output_root(config, proposal["job_id"])
    (root / "provider-a.jpg").write_bytes(b"altered")
    with pytest.raises(ValueError, match="endpoint"):
        generation.run(job_for(proposal), config, None, bridge_pair[0], lambda _: None, lambda: False)
    with pytest.raises(ValueError, match="endpoint"):
        generation.receipt(config, job_for(proposal, "failed"))


@pytest.mark.parametrize("cost", [None, [], {"credits": -1}, {"credits": True}, {"credits": float("nan")}, {"credits": 301}])
def test_invalid_accepted_cost_keeps_task_identity_and_confirmed_cancellation(tmp_path, monkeypatch, provider_frames, cost):
    client = providers.RunwayClient(key="offline-key")
    task_id = "12345678-1234-1234-1234-123456789abc"
    calls = []
    def request(method, path, body=None):
        calls.append(method)
        if method == "POST":
            return {"id": task_id, "estimatedCost": cost}
        return {"id": task_id, "status": "RUNNING"} if method == "GET" else {}
    monkeypatch.setattr(client, "_request", request)
    path = tmp_path / "submission.json"
    with pytest.raises(ValueError, match="unavailable or excessive"):
        client.submit({"prompt": "Bridge", "max_credits": 120}, *provider_frames, path)
    receipt = json.loads(path.read_text())
    assert receipt["task_id"] == task_id and receipt["cancellation_confirmed"]
    assert receipt["state"] == "cancelled" and calls == ["POST", "GET", "DELETE"]
    with pytest.raises(ValueError, match="already has"):
        client.submit({"prompt": "Bridge", "max_credits": 120}, *provider_frames, path)


def test_successful_original_remains_downloadable_if_assembly_fails(config, bridge_pair, prepared, monkeypatch):
    proposal, generated = prepared
    calls = provider(monkeypatch, config, proposal, generated, ("SUCCEEDED",))
    monkeypatch.setattr(bridges, "run", lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("Assembly failed")))
    job = job_for(proposal)
    with pytest.raises(ValueError, match="Assembly failed"):
        generation.run(job, config, None, bridge_pair[0], lambda _: None, lambda: False)
    for status in ("failed", "interrupted"):
        job["status"] = status
        assert generation.artifact(config, job, "original").read_bytes() == generated.read_bytes()
        with pytest.raises(ValueError, match="not ready"):
            generation.artifact(config, job, "video")
    assert generation.receipt(config, job)["state"] == "succeeded"
    assert [call[0] for call in calls] == ["POST", "GET"]
    original = generation.artifact(config, job, "original")
    original.write_bytes(original.read_bytes() + b"mutated")
    with pytest.raises(ValueError, match="changed"):
        generation.artifact(config, job, "original")


def test_provider_endpoints_match_assembled_fill_crop(config, bridge_pair, prepared, monkeypatch):
    proposal, generated = prepared
    store = bridge_pair[0]
    # Asymmetric source makes a framing regression visible; compare provider
    # conditioning and assembly endpoints after one deliberate off-center crop.
    path = generated.with_name("pattern.mp4")
    run_process(["ffmpeg", "-v", "error", "-nostdin", "-f", "lavfi", "-i",
                 "color=red:s=192x108:r=30,drawbox=x=96:y=0:w=96:h=108:color=blue:t=fill", "-t", "1",
                 "-c:v", "libx264", "-threads", "1", str(path)])
    from pipeline.ingest.probe import _content_hash
    identity = _content_hash(path)
    bridge_pair[3][identity] = {"film_id": identity, "path": str(path), "duration": 1., "title": "Pattern"}
    source = deepcopy(proposal["source_snapshot"]["request"])
    source["outgoing"].update(film_id=identity, framing={"fit": "fill", "anchor_x": .8, "anchor_y": .5, "zoom": 1.2})
    source["incoming"].update(film_id=identity, framing={"fit": "fill", "anchor_x": .2, "anchor_y": .5, "zoom": 1.2})
    queued = store.enqueue_transition_render(jobs.freeze(source, None))
    result = jobs.run(store.claim(role="editor"), config, None, store, lambda _: None, lambda: False)
    store.finish(queued["id"], result=result)
    request = {**proposal["request"], "request_id": str(uuid.uuid4()), "parent_render_id": queued["id"],
               "parent_manifest_sha256": result["manifest_sha256"]}
    proposal = generation.prepare(request, config, None, store)
    provider(monkeypatch, config, proposal, generated, ("SUCCEEDED",))
    job = job_for(proposal)
    generation.run(job, config, None, store, lambda _: None, lambda: False)
    root = jobs.output_root(config, proposal["job_id"])
    from PIL import Image
    import numpy as np
    arrays = []
    for side in ("a", "b"):
        with Image.open(root / f"provider-{side}.jpg") as image:
            provider_pixels = np.asarray(image.resize((192, 108)), dtype=float)
        with Image.open(root / f"normalized-{side}.jpg") as image:
            assembled_pixels = np.asarray(image, dtype=float)
        assert np.abs(provider_pixels - assembled_pixels).mean() < 5
        arrays.append(provider_pixels)
    assert np.abs(arrays[0] - arrays[1]).mean() > 5
