"""A latency diagnostic must measure separate requests, not reused comparison work."""
from dataclasses import asdict
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from pipeline.experiments import intent_latency as latency
from pipeline.search.intent import ASPECTS, INTENT_VERSION, IntentSignal, QueryIntent, request_identity


def intent(query="quiet", scope=()):
    return QueryIntent(INTENT_VERSION, request_identity(query, scope),
        tuple(IntentSignal(name, "yes" if name == "mood" else "no", .9) for name in ASPECTS), "frozen", None)


def test_benchmark_pins_once_but_does_not_share_request_memos(monkeypatch):
    from pipeline.index import snapshot
    from pipeline.search import intent_retrieval
    from pipeline.search.request import search_execution, reuse_vector
    pin_calls, executions, encodings = [], [], []
    pinned = SimpleNamespace(versions={"units": 3})
    monkeypatch.setattr(snapshot, "acquire_search_snapshot", lambda config, db: pin_calls.append(db) or pinned)

    @search_execution
    def execute(query, db, config, **kwargs):
        executions.append((db, kwargs["strategy"]))
        def encode():
            encodings.append(query)
            return np.zeros(2)
        reuse_vector(("query", query), encode)
        reuse_vector(("query", query), encode)
        return intent_retrieval.SearchExecution(
            [{"unit_id": "unit", "film_id": "film", "t_start": 1, "t_end": 2}],
            intent_retrieval.build_plan(kwargs["strategy"], available_views=()), {})

    monkeypatch.setattr(intent_retrieval, "execute_intent_search", execute)
    result = latency.benchmark("quiet", object(), object(), intent=intent(), rotation=1)
    assert len(pin_calls) == 1
    assert len(encodings) == 6
    assert all(db is pinned for db, _ in executions)
    assert [strategy for _, strategy in executions] == ["fixed", "jev", "normal", "jev", "normal", "fixed"]
    assert result["snapshot_versions"] == {"units": 3}
    assert len({s["ranked_sources_sha256"] for s in result["samples"]}) == 1


@pytest.fixture
def client(monkeypatch):
    from pipeline.api import search_intent, main
    app = FastAPI()
    app.include_router(search_intent.router)
    app.state.db = object()
    app.state.config = object()
    monkeypatch.setattr(main, "_require_search_ready", lambda request: None)
    from pipeline.experiments import intent_service
    monkeypatch.setattr(intent_service, "interpret", lambda *a, **k: pytest.fail("Hosted call"))
    return TestClient(app)


def test_frozen_api_requires_matching_intent_and_strict_bounds(client, monkeypatch):
    monkeypatch.setattr(latency, "benchmark", lambda *a, **k: pytest.fail("Invalid request reached execution"))
    for body in ({"q": "quiet"}, {"q": "other", "intent": asdict(intent())},
                 {"q": "quiet", "intent": asdict(intent()), "rounds": 3},
                 {"q": "quiet", "intent": asdict(intent()), "limit": 200},
                 {"q": "quiet", "intent": asdict(intent()), "rounds": True}):
        assert client.post("/search/intent/benchmark", json=body).status_code == 422


def test_frozen_api_uses_shared_admission_and_releases_after_error(client, monkeypatch):
    from pipeline.api import search_intent as api
    body = {"q": "quiet", "intent": asdict(intent())}
    assert api._SLOT.acquire(blocking=False)
    try:
        assert client.post("/search/intent/benchmark", json=body).status_code == 429
    finally:
        api._SLOT.release()
    def fail(*args, **kwargs):
        assert not api._SLOT.acquire(blocking=False)
        raise TimeoutError()
    monkeypatch.setattr(latency, "benchmark", fail)
    assert client.post("/search/intent/benchmark", json=body).status_code == 504
    assert api._SLOT.acquire(blocking=False)
    api._SLOT.release()
    monkeypatch.setattr(latency, "benchmark", lambda *a, **k: {"samples": []})
    assert client.post("/search/intent/benchmark", json=body).status_code == 200


def test_saved_receipt_hash_and_query_scope_are_required(tmp_path):
    saved = {"query": "quiet", "film_ids": [], "comparison": {"diagnostics": {"intent": asdict(intent())}}}
    (tmp_path / "a.json").write_text(json.dumps(saved), encoding="utf-8")
    (tmp_path / "run.json").write_text(json.dumps({"cases": {"a": {
        "status": "completed", "file": "a.json", "sha256": latency.digest(saved)}}}), encoding="utf-8")
    assert latency.load_cases(tmp_path, ["a"])[0]["q"] == "quiet"
    saved["query"] = "changed"
    (tmp_path / "a.json").write_text(json.dumps(saved), encoding="utf-8")
    with pytest.raises(ValueError, match="changed"):
        latency.load_cases(tmp_path, ["a"])


def test_dry_cli_never_sends_an_http_request(tmp_path, monkeypatch):
    monkeypatch.setattr(latency, "load_cases", lambda *a: [{"id": "case"}])
    import httpx
    monkeypatch.setattr(httpx, "Client", lambda *a, **k: pytest.fail("Dry run contacted API"))
    out = tmp_path / "dry.json"
    latency.main(["--out", str(out)])
    assert json.loads(out.read_text())["status"] == "prepared"
