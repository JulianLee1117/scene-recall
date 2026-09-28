"""API/ledger/worker integration and multipart streaming limits for bridge imports."""
import asyncio
import json
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from pipeline.lab.worker import execute_job
from pipeline.transitions import api, bridges, jobs, providers
from pipeline.tests.test_transition_bridges import bridge_pair as bridge_pair
from pipeline.tests.test_transition_bridges import provider_frames as provider_frames


@pytest.fixture
def bridge_api(config, bridge_pair):
    app = FastAPI()
    app.include_router(api.router)
    app.state.config, app.state.db, app.state.lab = config, None, bridge_pair[0]
    return TestClient(app)


def _upload(client, pair, **kwargs):
    return client.post("/lab/transitions/bridges", files={"file": (
        "C:\\private-upload-folder\\external.mp4", pair[2].read_bytes(), "video/mp4")},
        data={"metadata": json.dumps({"parent_render_id": pair[1], **kwargs})})


def test_upload_worker_download_and_restore_preserve_owned_receipts(config, bridge_pair, bridge_api):
    response = _upload(bridge_api, bridge_pair, trim_start=.1, trim_end=.8, playback_duration=.5,
                       provenance={"provider": "External", "model": "test-model", "prompt": "Move continuously"})
    assert response.status_code == 200, response.text
    queued = response.json()
    identity = queued["id"]
    assert queued["kind"] == "transition-bridge" and queued["project_id"] is None
    assert queued["input"]["name"] == "external.mp4"
    assert "private-upload-folder" not in response.text and "snapshot" not in queued
    assert str(config.paths.films_dir) not in response.text
    assert queued["source_titles"] == {"outgoing": "a", "incoming": "b"}
    store = bridge_pair[0]
    proposal = store.get_job(identity, private=True)["snapshot"]["transition_bridge"]
    assert store.enqueue_transition_bridge(proposal)["id"] == identity
    assert bridge_api.get(f"/lab/transitions/bridges/{identity}/video").status_code == 409
    claimed = store.claim(role="editor")
    assert claimed["id"] == identity
    complete = execute_job(claimed, config, None, store)
    assert complete["status"] == "completed", complete.get("error")
    restored = bridge_api.get(f"/lab/transitions/bridges/{identity}").json()
    assert restored["request"]["trim_start"] == .1
    assert restored["result"]["duration"] == pytest.approx(2.1)
    matching = bridge_api.get("/lab/transitions/bridges", params={"parent_render_id": bridge_pair[1]}).json()
    assert [row["id"] for row in matching["bridges"]] == [identity]
    assert bridge_api.get("/lab/transitions/bridges", params={"parent_render_id": identity}).json() == {"bridges": []}
    video = bridge_api.get(f"/lab/transitions/bridges/{identity}/video")
    assert video.status_code == 200 and "content-disposition" not in video.headers
    download = bridge_api.get(f"/lab/transitions/bridges/{identity}/video?download=true")
    assert download.content == video.content and download.headers["content-disposition"].startswith("attachment;")
    original = bridge_api.get(f"/lab/transitions/bridges/{identity}/original")
    assert original.content == bridge_pair[2].read_bytes()
    assert "external.mp4" in original.headers["content-disposition"]
    manifest = bridge_api.get(f"/lab/transitions/bridges/{identity}/manifest")
    assert manifest.status_code == 200 and "private-upload-folder" not in manifest.text
    assert str(config.paths.films_dir) not in manifest.text
    for frame in ("a", "b", "first", "last"):
        frame_response = bridge_api.get(f"/lab/transitions/bridges/{identity}/{frame}")
        assert frame_response.status_code == 200 and frame_response.headers["content-type"].startswith("image/jpeg")
    assert not store.list_projects()


def test_cancelled_import_hides_result_and_refuses_all_artifacts(bridge_pair, bridge_api):
    queued = _upload(bridge_api, bridge_pair).json()
    identity = queued["id"]
    cancelled = bridge_api.post(f"/lab/transitions/bridges/{identity}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled" and cancelled.json()["result"] is None
    assert bridge_pair[0].claim(role="editor") is None
    for artifact in ("video", "manifest", "original", "a", "b", "first", "last"):
        assert bridge_api.get(f"/lab/transitions/bridges/{identity}/{artifact}").status_code == 409


def test_bridge_routes_reject_wrong_kind_and_do_not_cancel_parent(bridge_pair, bridge_api):
    parent = bridge_pair[1]
    assert bridge_api.get(f"/lab/transitions/bridges/{parent}").status_code == 404
    assert bridge_api.post(f"/lab/transitions/bridges/{parent}/cancel").status_code == 404
    assert bridge_api.get(f"/lab/transitions/bridges/{parent}/video").status_code == 404
    assert not bridge_pair[0].get_job(parent)["cancel_requested"]
    assert bridge_api.get("/lab/transitions/bridges", params={"parent_render_id": "../../private"}).status_code == 422


@pytest.mark.parametrize("metadata", [
    '{"parent_render_id":"invalid","secret":"super-private-value"}',
    '{"parent_render_id":"12345678-1234-1234-1234-123456789abc","trim_start":NaN,"secret":"super-private-value"}',
    '{"parent_render_id":"12345678-1234-1234-1234-123456789abc","provenance":{"secret":"super-private-value"}}',
    '{"parent_render_id":"super-private-value"',
])
def test_metadata_validation_redacts_input_values(config, bridge_api, metadata):
    root = config.paths.assets_dir / "lab" / "renders"
    existing = set(root.iterdir())
    response = bridge_api.post("/lab/transitions/bridges", files={"file": ("unused.mp4", b"tiny", "video/mp4")},
                               data={"metadata": metadata})
    assert response.status_code == 422, response.text
    assert "super-private-value" not in response.text
    assert set(root.iterdir()) == existing


def test_imported_bytes_remain_guarded_after_worker_completion(config, bridge_pair, bridge_api):
    identity = _upload(bridge_api, bridge_pair).json()["id"]
    store = bridge_pair[0]
    assert execute_job(store.claim(role="editor"), config, None, store)["status"] == "completed"
    original = jobs.output_root(config, identity) / "original.media"
    original.write_bytes(original.read_bytes() + b"mutation")
    assert bridge_api.get(f"/lab/transitions/bridges/{identity}/video").status_code == 409
    assert bridge_api.get(f"/lab/transitions/bridges/{identity}/original").status_code == 409


def test_bridge_intermediates_follow_existing_terminal_cleanup_allowlist(config, bridge_pair, bridge_api):
    from pipeline.lab.cleanup import collect_garbage
    identity = _upload(bridge_api, bridge_pair).json()["id"]
    root = jobs.output_root(config, identity)
    intermediates = [root / "clip-000.mp4", root / "clip-001.mp4", root / "clip-002.mp4",
                     root / "output.partial.mp4"]
    for path in intermediates:
        path.write_bytes(b"interrupted encode")
    store = bridge_pair[0]
    assert not collect_garbage(store, apply=True)["errors"]
    assert all(path.exists() for path in intermediates)  # Queued/running work is protected.
    store.cancel(identity)
    report = collect_garbage(store, apply=True)
    assert not report["errors"] and len(report["files"]) == 4
    assert not any(path.exists() for path in intermediates)
    for name in ("original.media", "input.json", "parent-manifest.json", "frame-a.jpg", "frame-b.jpg"):
        assert (root / name).exists()


def _asgi_post(app, chunks, extra_headers=()):
    received, messages = 0, []
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST",
             "scheme": "http", "path": "/lab/transitions/bridges", "raw_path": b"/lab/transitions/bridges",
             "query_string": b"", "root_path": "", "server": ("testserver", 80), "client": ("testclient", 1),
             "headers": [(b"content-type", b"multipart/form-data; boundary=bridge-boundary"), *extra_headers]}
    async def receive():
        nonlocal received
        if received >= len(chunks):
            return {"type": "http.disconnect"}
        part = chunks[received]
        received += 1
        return {"type": "http.request", "body": part, "more_body": received < len(chunks)}
    async def send(message):
        messages.append(message)
    asyncio.run(app(scope, receive, send))
    return next(row["status"] for row in messages if row["type"] == "http.response.start"), received


@pytest.mark.parametrize("extra_headers", [(), ((b"content-length", b"32"),)])
def test_actual_chunked_upload_is_bounded_before_spooling_or_import(monkeypatch, extra_headers):
    monkeypatch.setattr(api, "MAX_UPLOAD_BODY_BYTES", 1024)
    def cannot_import(*_args):
        pytest.fail("Oversized multipart streams must be rejected before import")
    monkeypatch.setattr(bridges, "prepare_import", cannot_import)
    app = FastAPI()
    app.include_router(api.router)
    chunks = [b'--bridge-boundary\r\nContent-Disposition: form-data; name="file"; filename="test.mp4"\r\nContent-Type: video/mp4\r\n\r\n',
              b"a" * 700, b"b" * 700, b"\r\n--bridge-boundary--\r\n"]
    status, received = _asgi_post(app, chunks, extra_headers)
    assert status == 413
    assert received == 3  # Stop when cumulative bytes cross the cap; never consume the tail.


@pytest.mark.parametrize("failure", ["size", "deadline"])
def test_rejected_receive_closes_every_partial_multipart_spool(monkeypatch, failure):
    import starlette.formparsers as parsers
    original_factory = parsers.SpooledTemporaryFile
    spools = []
    def capture_spool(*args, **kwargs):
        spool = original_factory(*args, **kwargs)
        spools.append(spool)  # Hold references so garbage collection cannot hide a leaked handle.
        return spool
    monkeypatch.setattr(parsers, "SpooledTemporaryFile", capture_spool)
    if failure == "size":
        monkeypatch.setattr(api, "MAX_UPLOAD_BODY_BYTES", 150)
        expected = 413
    else:
        ticks = iter((0., 0., 121.))
        monkeypatch.setattr(api, "time", SimpleNamespace(monotonic=lambda: next(ticks)))
        expected = 408
    app = FastAPI()
    app.include_router(api.router)
    chunks = [b'--bridge-boundary\r\nContent-Disposition: form-data; name="file"; filename="test.mp4"\r\n\r\n',
              b"a" * 200, b"\r\n--bridge-boundary--\r\n"]
    status, _received = _asgi_post(app, chunks)
    assert status == expected
    assert spools and all(spool.closed for spool in spools)


def test_declared_oversized_upload_never_reads_body(monkeypatch):
    monkeypatch.setattr(api, "MAX_UPLOAD_BODY_BYTES", 1024)
    app = FastAPI()
    app.include_router(api.router)
    status, received = _asgi_post(app, [b"should not be read"], ((b"content-length", b"1025"),))
    assert status == 413 and received == 0


def test_stalled_upload_receives_408_and_cancels_pending_receive(monkeypatch):
    monkeypatch.setattr(api, "MAX_UPLOAD_RECEIVE_SECONDS", .01)
    app = FastAPI()
    app.include_router(api.router)
    messages, stopped = [], []
    async def receive():
        try:
            await asyncio.Event().wait()
        finally:
            stopped.append(True)
    async def send(message):
        messages.append(message)
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST",
             "scheme": "http", "path": "/lab/transitions/bridges", "raw_path": b"/lab/transitions/bridges",
             "query_string": b"", "root_path": "", "server": ("testserver", 80), "client": ("testclient", 1),
             "headers": [(b"content-type", b"multipart/form-data; boundary=bridge-boundary")]}
    asyncio.run(app(scope, receive, send))
    assert next(row["status"] for row in messages if row["type"] == "http.response.start") == 408
    assert stopped == [True]
    assert b"two-minute reception budget" in b"".join(row.get("body", b"") for row in messages)


def test_upload_deadline_is_total_not_reset_for_each_chunk(monkeypatch):
    clock = iter((0., 30., 70., 121.))
    monkeypatch.setattr(api, "time", SimpleNamespace(monotonic=lambda: next(clock)))
    waits = []
    async def tracked_wait(awaitable, *, timeout):
        waits.append(timeout)
        return await awaitable
    monkeypatch.setattr(api, "asyncio", SimpleNamespace(wait_for=tracked_wait))
    app = FastAPI()
    app.include_router(api.router)
    chunks = [b'--bridge-boundary\r\nContent-Disposition: form-data; name="file"; filename="test.mp4"\r\n\r\n',
              b"body", b"more", b"\r\n--bridge-boundary--\r\n"]
    status, received = _asgi_post(app, chunks)
    assert status == 408 and received == 2
    assert waits == [90., 50.]


@pytest.mark.parametrize("aspect,expected", [("landscape", "854:480"), ("portrait", "480:854"), ("square", "640:640")])
def test_480p_contract_uses_current_seedance25_openapi_values(tmp_path, provider_frames, monkeypatch, aspect, expected):
    # Verified against docs.dev.runwayml.com/openapi.json on 2026-09-16.
    # The Aug 7 changelog's 864:496 sentinels are absent from the current enum.
    payloads = []
    client = providers.RunwayClient(key="test")
    def submit(_method, _path, body):
        payloads.append(body)
        return {"id": "12345678-1234-1234-1234-123456789abc", "estimatedCost": {"credits": 80}}
    monkeypatch.setattr(client, "_request", submit)
    client.submit({"prompt": "Bridge", "resolution": "480p", "aspect": aspect, "max_credits": 80},
                  *provider_frames, tmp_path / "receipt.json")
    assert payloads[0]["ratio"] == expected


def test_inline_image_limit_is_checked_before_receipt_or_network(tmp_path, provider_frames, monkeypatch):
    client = providers.RunwayClient(key="test")
    monkeypatch.setattr(providers, "MAX_IMAGE_URI_LENGTH", 100)
    def cannot_submit(*_args):
        pytest.fail("An oversized inline image must not reach Runway")
    monkeypatch.setattr(client, "_request", cannot_submit)
    receipt = tmp_path / "receipt.json"
    with pytest.raises(ValueError, match="encoded-image limit"):
        client.submit({"prompt": "Bridge", "max_credits": 120}, *provider_frames, receipt)
    assert not receipt.exists()
