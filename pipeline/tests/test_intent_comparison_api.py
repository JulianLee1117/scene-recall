"""Comparison admission, provider accounting, and query/scope isolation."""
from dataclasses import asdict
import asyncio
import json
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from pipeline.api import search_intent as api
from pipeline.experiments.intent_service import interpret
from pipeline.search.intent import ASPECTS, MODEL, parse_intent_response


def receipt():
    return {"model": MODEL, "usage": {"input_tokens": 100, "output_tokens": 0, "cost": .0001},
            "answers": {aspect: {"type": "choice", "choice": "yes", "confidence": .9,
                                  "probabilities": {"yes": .9, "no": .05, "unclear": .05}}
                        for aspect in ASPECTS}}


def test_cached_receipt_is_bound_to_query_and_film_scope(tmp_path):
    calls = []
    def transport(body, **kwargs):
        calls.append(body)
        return receipt()
    first, _ = interpret("romantic", tmp_path, film_ids=["a"], transport=transport, api_key="test")
    cached, info = interpret("romantic", tmp_path, film_ids=["a"], transport=transport, api_key="test")
    scoped, _ = interpret("romantic", tmp_path, film_ids=["b"], transport=transport, api_key="test")
    assert len(calls) == 2
    assert first == cached and first.query_sha256 != scoped.query_sha256
    assert info["status"] == "cached" and info["cost_usd"] == 0
    assert calls[0]["state"]["explicit_film_ids"] == ["a"]


def test_interactive_queries_reuse_one_bounded_http_client(tmp_path, monkeypatch):
    from pipeline.experiments import intent_service
    created, used = [], []
    client = SimpleNamespace(close=lambda: None)
    monkeypatch.setattr(intent_service, "_CLIENT", None)
    monkeypatch.setattr(intent_service, "_http_client", lambda timeout: created.append(timeout) or client)
    monkeypatch.setattr(intent_service, "request_decisions", lambda *args, **kwargs: used.append(kwargs["client"]) or receipt())
    interpret("first", tmp_path, api_key="test")
    interpret("second", tmp_path, api_key="test")
    assert created == [1.5] and used == [client, client]


def test_unknown_cost_halts_new_attempts_and_preserves_receipt(tmp_path):
    calls = []
    def fail(body, **kwargs):
        calls.append(body)
        raise TimeoutError()
    assert interpret("first", tmp_path, transport=fail, api_key="test")[0] is None
    intent, info = interpret("second", tmp_path, transport=fail, api_key="test")
    assert intent is None and info["reason"] == "unknown_previous_cost"
    assert len(calls) == 1
    saved = json.loads(next((tmp_path / "intent-comparison").glob("*.json")).read_text())
    assert saved["status"] == "failed" and saved["cost_usd"] is None


def test_known_cost_invalid_response_never_retries_same_query(tmp_path):
    calls = []
    def invalid(body, **kwargs):
        calls.append(body)
        return {"usage": {"cost": .0002}}
    interpret("first", tmp_path, transport=invalid, api_key="test")
    _, info = interpret("first", tmp_path, transport=invalid, api_key="test")
    assert len(calls) == 1 and info["reason"] == "previous_attempt_failed"


def test_budget_is_durable_across_calls(tmp_path, monkeypatch):
    from pipeline.experiments import intent_service
    monkeypatch.setattr(intent_service, "MAX_CALLS", 1)
    interpret("first", tmp_path, transport=lambda *a, **k: receipt(), api_key="test")
    _, info = interpret("second", tmp_path, transport=lambda *a, **k: pytest.fail("paid call"), api_key="test")
    assert info["reason"] == "comparison_budget_reached"


@pytest.fixture
def client(tmp_path):
    app = FastAPI()
    app.include_router(api.router)
    app.state.encoder_ready = True
    app.state.config = SimpleNamespace(paths=SimpleNamespace(state_dir=tmp_path))
    app.state.db = object()
    return TestClient(app)


def test_frozen_route_rejects_intent_for_different_scope(client, monkeypatch):
    monkeypatch.setattr(api, "_execute", lambda *a: pytest.fail("retrieval should not run"))
    intent = parse_intent_response(receipt(), query="romantic", film_ids=["a"])
    response = client.post("/search/intent/compare", json={"q": "romantic", "film_ids": ["b"], "intent": asdict(intent)})
    assert response.status_code == 422


def test_frozen_route_never_uses_hosted_transport(client, monkeypatch):
    from pipeline.experiments import intent_service
    monkeypatch.setattr(intent_service, "interpret", lambda *a, **k: pytest.fail("hosted call"))
    calls = []
    monkeypatch.setattr(api, "_execute", lambda payload, request, intent: calls.append(intent) or {"ok": True})
    intent = parse_intent_response(receipt(), query="romantic", film_ids=["a"])
    response = client.post("/search/intent/compare", json={"q": "romantic", "film_ids": ["a"], "intent": asdict(intent)})
    assert response.status_code == 200 and len(calls) == 1


def test_hosted_unavailable_runs_ordinary_fallback(client, monkeypatch):
    from pipeline.experiments import intent_service
    monkeypatch.setattr(intent_service, "interpret", lambda *a, **k: (None, {"status": "fallback", "reason": "provider_not_configured"}))
    calls = []
    monkeypatch.setattr(api, "_execute", lambda payload, request, intent, **kwargs: calls.append(intent) or {"variants": {}})
    response = client.post("/search/intent/compare-hosted", json={"q": "romantic"})
    assert response.status_code == 200 and calls == [None]
    assert response.json()["provider"]["reason"] == "provider_not_configured"


def test_hosted_route_forbids_injected_intent(client):
    assert client.post("/search/intent/compare-hosted", json={"q": "romantic", "intent": {}}).status_code == 422


def test_busy_hosted_request_is_rejected_before_paid_interpretation(client, monkeypatch):
    from pipeline.experiments import intent_service
    monkeypatch.setattr(intent_service, "interpret", lambda *a, **k: pytest.fail("paid call while busy"))
    assert api._SLOT.acquire(blocking=False)
    try:
        response = client.post("/search/intent/compare-hosted", json={"q": "romantic"})
        assert response.status_code == 429
    finally:
        api._SLOT.release()


@pytest.mark.parametrize("field,value", [
    ("limit", 0), ("limit", 201), ("limit", True),
    ("supplemental_budget", 4), ("supplemental_budget", 301), ("supplemental_budget", True),
])
def test_prefix_and_candidate_budget_bounds_fail_before_retrieval(client, monkeypatch, field, value):
    monkeypatch.setattr(api, "_execute", lambda *a, **k: pytest.fail("retrieval for invalid bounds"))
    response = client.post("/search/intent/compare", json={"q": "romantic", field: value})
    assert response.status_code == 422


def test_provider_deadline_returns_baseline_and_does_not_retry(client, monkeypatch):
    from pipeline.experiments import intent_service
    released, finished = threading.Event(), threading.Event()
    calls, captures = [], []

    def slow(*args, **kwargs):
        calls.append(args)
        try:
            released.wait(timeout=2)
            return None, {"status": "completed"}
        finally:
            finished.set()

    monkeypatch.setattr(api, "_INTERPRETATION_TIMEOUT_SECONDS", .01)
    monkeypatch.setattr(intent_service, "interpret", slow)
    monkeypatch.setattr(api, "_execute", lambda payload, request, intent, **kwargs:
                        captures.append(intent) or released.set() or {"variants": {}})
    try:
        response = client.post("/search/intent/compare-hosted", json={"q": "romantic"})
        assert response.status_code == 200
        assert response.json()["provider"]["reason"] == "interpretation_deadline"
        assert captures == [None] and len(calls) == 1
        assert finished.wait(timeout=1)
    finally:
        released.set()


def test_cancelled_http_request_keeps_slot_until_retrieval_thread_finishes(tmp_path, monkeypatch):
    from pipeline.api import main
    from pipeline.experiments import intent_service
    entered, released, slot_free = threading.Event(), threading.Event(), threading.Event()

    class RecordedSlot:
        def __init__(self):
            self.semaphore = threading.BoundedSemaphore(1)

        def acquire(self, **kwargs):
            return self.semaphore.acquire(**kwargs)

        def release(self):
            self.semaphore.release()
            slot_free.set()

    slot = RecordedSlot()
    monkeypatch.setattr(api, "_SLOT", slot)
    monkeypatch.setattr(main, "_require_search_ready", lambda *args: None)
    monkeypatch.setattr(intent_service, "interpret", lambda *args, **kwargs: (None, {"status": "fallback"}))

    def blocked_capture(*args, **kwargs):
        entered.set()
        released.wait(timeout=3)
        return {"variants": {}}

    monkeypatch.setattr(api, "_execute", blocked_capture)
    request = SimpleNamespace(is_disconnected=AsyncMock(return_value=False), app=SimpleNamespace(state=SimpleNamespace(
        config=SimpleNamespace(paths=SimpleNamespace(state_dir=tmp_path)))))

    async def scenario():
        task = asyncio.create_task(api.compare_hosted(api.ComparisonRequest(q="romantic"), request))
        assert await asyncio.to_thread(entered.wait, 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not slot_free.is_set()
        assert not slot.acquire(blocking=False)
        released.set()
        assert await asyncio.to_thread(slot_free.wait, 1)
        assert slot.acquire(blocking=False)
        slot.release()

    try:
        asyncio.run(scenario())
    finally:
        released.set()


@pytest.mark.parametrize("disconnect", [False, True])
def test_comparison_deadline_or_disconnect_stops_next_stage_but_holds_running_worker(tmp_path, monkeypatch, disconnect):
    from fastapi import HTTPException
    from pipeline.api import main
    from pipeline.experiments import intent_service
    entered, released, finished, stopped = (threading.Event() for _ in range(4))
    slot = threading.BoundedSemaphore(1)
    monkeypatch.setattr(api, "_SLOT", slot)
    monkeypatch.setattr(api, "_COMPARISON_TIMEOUT_SECONDS", .05)
    monkeypatch.setattr(main, "_require_search_ready", lambda *args: None)
    monkeypatch.setattr(intent_service, "interpret", lambda *a, **k: (None, {"status": "fallback"}))

    def blocked_capture(*args, check_cancelled, **kwargs):
        entered.set()
        released.wait(timeout=3)
        try:
            check_cancelled()
            pytest.fail("expired request must not start its next retrieval stage")
        except TimeoutError:
            stopped.set()
            raise
        finally:
            finished.set()

    monkeypatch.setattr(api, "_execute", blocked_capture)
    request = SimpleNamespace(is_disconnected=AsyncMock(return_value=disconnect),
        app=SimpleNamespace(state=SimpleNamespace(config=SimpleNamespace(paths=SimpleNamespace(state_dir=tmp_path)))))

    async def scenario():
        task = asyncio.create_task(api.compare_hosted(api.ComparisonRequest(q="romantic"), request))
        assert await asyncio.to_thread(entered.wait, 1)
        with pytest.raises(HTTPException) as error:
            await asyncio.wait_for(task, timeout=1)
        assert error.value.status_code == (499 if disconnect else 504)
        assert not slot.acquire(blocking=False), "native work retains admission after HTTP response ends"
        released.set()
        assert await asyncio.to_thread(finished.wait, 1)
        assert stopped.is_set()
        assert await asyncio.to_thread(lambda: slot.acquire(timeout=1))
        slot.release()

    try:
        asyncio.run(scenario())
    finally:
        released.set()


def test_invalid_cached_receipt_falls_back_without_new_provider_attempt(tmp_path):
    interpret("romantic", tmp_path, transport=lambda *args, **kwargs: receipt(), api_key="test")
    path = next((tmp_path / "intent-comparison").glob("*.json"))
    saved = json.loads(path.read_text())
    saved["response"]["answers"].pop("mood")
    path.write_text(json.dumps(saved), encoding="utf-8")
    intent, info = interpret("romantic", tmp_path, transport=lambda *a, **k: pytest.fail("paid retry"), api_key="test")
    assert intent is None and info["reason"] == "invalid_cached_receipt"


def test_reported_cost_above_reserve_halts_new_queries_but_keeps_paid_cache(tmp_path):
    from pipeline.experiments import intent_service
    raw = receipt()
    raw["usage"]["cost"] = intent_service.CALL_RESERVE_USD + .001
    intent, info = interpret("first", tmp_path, transport=lambda *args, **kwargs: raw, api_key="test")
    assert intent is not None and info["cost_usd"] == raw["usage"]["cost"]
    _, stopped = interpret("second", tmp_path, transport=lambda *a, **k: pytest.fail("paid after overrun"), api_key="test")
    assert stopped["reason"] == "cost_exceeded_reserve"
    reused, cached = interpret("first", tmp_path, transport=lambda *a, **k: pytest.fail("paid cache"), api_key="test")
    assert reused == intent and cached["status"] == "cached"
