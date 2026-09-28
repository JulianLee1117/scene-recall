"""Explicit generation authorization, durable identity, and worker routing."""
import json
import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from pipeline.lab.worker import execute_job
from pipeline.transitions import api, generation, providers
from pipeline.tests.test_transition_bridges import bridge_pair as bridge_pair


@pytest.fixture
def generation_api(config, bridge_pair):
    app = FastAPI()
    app.include_router(api.router)
    app.state.config, app.state.db, app.state.lab = config, None, bridge_pair[0]
    return TestClient(app)


def request_body(pair):
    return {"request_id": str(uuid.uuid4()), "parent_render_id": pair[1],
            "parent_manifest_sha256": pair[0].get_job(pair[1])["result"]["manifest_sha256"],
            "prompt": "A controlled camera whip", "duration": 4, "resolution": "720p",
            "seed": 42, "max_credits": 120, "price_version": providers.PRICE_VERSION, "confirm_spend": True}


def stub_prepare(body, _config, _db, _store):
    request = generation.GenerateRequest.model_validate(body).model_dump(mode="json")
    return {"job_id": request["request_id"], "generation_version": generation.GENERATION_VERSION,
            "request": request, "parent_manifest_sha256": request["parent_manifest_sha256"],
            "source_snapshot": {"sources": [{"title": "A", "path": "private-film-a"}, {"title": "B", "path": "private-film-b"}]},
            "preparation": {"private": "never public"}, "preparation_sha256": "0" * 64}


def test_generation_http_retry_returns_one_job_without_requeue(config, bridge_pair, generation_api, monkeypatch):
    calls = []
    def prepare(*args):
        calls.append("prepare")
        return stub_prepare(*args)
    monkeypatch.setattr(generation, "prepare", prepare)
    body = request_body(bridge_pair)
    response = generation_api.post("/lab/transitions/generations", json=body)
    assert response.status_code == 200, response.text
    value = response.json()
    assert value["id"] == body["request_id"] and value["kind"] == "transition-generate"
    assert value["project_id"] is None and value["request"] == generation.GenerateRequest.model_validate(body).model_dump(mode="json")
    assert "private-film" not in response.text and "preparation" not in response.text
    assert value["receipt_url"].endswith("/receipt")
    store = bridge_pair[0]
    assert store.claim(role="ingest") is None
    claimed = store.claim(role="editor")
    assert claimed["id"] == value["id"]
    monkeypatch.setattr(generation, "run", lambda *_args: {"preview_url": "local-video", "duration": 5.6})
    assert execute_job(claimed, config, None, store)["status"] == "completed"
    retried = generation_api.post("/lab/transitions/generations", json=body)
    assert retried.status_code == 200 and retried.json()["status"] == "completed"
    assert calls == ["prepare"] and store.claim(role="editor") is None
    assert generation_api.post("/lab/transitions/generations", json={**body, "seed": 99}).status_code == 409
    listed = generation_api.get("/lab/transitions/generations", params={"parent_render_id": bridge_pair[1]}).json()
    assert [row["id"] for row in listed["generations"]] == [body["request_id"]]


def test_generation_rejects_missing_confirmation_stale_quote_and_missing_key(bridge_pair, generation_api, monkeypatch):
    monkeypatch.delenv(providers.KEY_ENV, raising=False)
    body = request_body(bridge_pair)
    for patch in ({"confirm_spend": False}, {"confirm_spend": "true"}, {"max_credits": 119},
                  {"price_version": "stale"}, {"request_id": "../escape"}, {"duration": 4.5}):
        response = generation_api.post("/lab/transitions/generations", json={**body, **patch})
        assert response.status_code == 422, response.text
    response = generation_api.post("/lab/transitions/generations", json=body)
    assert response.status_code == 422 and providers.KEY_ENV in response.text
    assert bridge_pair[0].transition_generations() == []


def test_queued_generation_cancel_never_calls_provider(bridge_pair, generation_api, monkeypatch):
    monkeypatch.setattr(generation, "prepare", stub_prepare)
    body = request_body(bridge_pair)
    generation_api.post("/lab/transitions/generations", json=body)
    response = generation_api.post(f"/lab/transitions/generations/{body['request_id']}/cancel")
    assert response.status_code == 200 and response.json()["status"] == "cancelled"
    assert bridge_pair[0].claim(role="editor") is None
    for route in ("", "/cancel", "/video"):
        method = generation_api.post if route == "/cancel" else generation_api.get
        assert method(f"/lab/transitions/generations/{bridge_pair[1]}{route}").status_code == 404
    assert not bridge_pair[0].get_job(bridge_pair[1])["cancel_requested"]


def test_failed_generation_exposes_receipt_original_and_explicit_reconciliation(bridge_pair, generation_api, monkeypatch):
    monkeypatch.setattr(generation, "prepare", stub_prepare)
    body = request_body(bridge_pair)
    generation_api.post("/lab/transitions/generations", json=body)
    bridge_pair[0].claim(role="editor")
    bridge_pair[0].finish(body["request_id"], error="Local assembly failed")
    monkeypatch.setattr(generation, "receipt", lambda _config, job: {"job_id": job["id"], "state": "succeeded", "actual_credits": 118})
    monkeypatch.setattr(generation, "artifact", lambda _config, _job, kind: bridge_pair[2] if kind == "original" else (_ for _ in ()).throw(ValueError("not ready")))
    base = "/lab/transitions/generations/" + body["request_id"]
    receipt = generation_api.get(base + "/receipt")
    assert receipt.status_code == 200 and receipt.json()["actual_credits"] == 118
    assert "attachment" in receipt.headers["content-disposition"]
    original = generation_api.get(base + "/original")
    assert original.status_code == 200 and original.content == bridge_pair[2].read_bytes()
    assert generation_api.get(base + "/video").status_code == 409
    cancelled = []
    monkeypatch.setattr(generation, "cancel_remote", lambda _config, job: cancelled.append(job["id"]))
    assert generation_api.post(base + "/cancel").status_code == 200
    assert cancelled == [body["request_id"]]
    # Repeating the original confirmed HTTP request still cannot buy another run.
    assert generation_api.post("/lab/transitions/generations", json=body).json()["status"] == "failed"
    assert bridge_pair[0].claim(role="editor") is None
