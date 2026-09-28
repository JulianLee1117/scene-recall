"""Frozen ordering experiments cannot expand scope or disguise model labels as truth."""
from copy import deepcopy
import json

import pytest

from pipeline.experiments import search_intent as experiment


def capture(count=60):
    rows = [{"unit_id": f"unit-{i}", "film_id": f"film-{i % 3}", "film_title": f"Film {i % 3}",
             "t_start": float(i * 10), "t_end": float(i * 10 + 5), "caption": f"A person waits beside door {i}.",
             "rank": i + 1, "matched_frame_timestamp": float(i * 10 + 2),
             "matched_frame_url": f"/media/keyframe/unit-{i}/0", "preview_url": f"/media/preview/unit-{i}",
             "debug": {"channels": {"img": {"rank": count - i}, "txt": {"rank": i + 1}}}}
            for i in range(count)]
    return {"version": 1, "provenance": {"fixture_sha256": "fixture", "query_policy": "v2"}, "cases": [{
        "id": "waiting", "query": "a lonely person waiting at a door", "film_ids": [],
        "snapshot_before": {"units": 12, "frames": 34}, "snapshot_after": {"units": 12, "frames": 34},
        "capture_seconds": 0.75, "candidates": rows, "review_status": "pending",
        "expected_windows": ([{"film_id": "film-2", "start": 20., "end": 25., "unit_id": "unit-2", "status": "frame-verified"},
                              {"film_id": "film-1", "start": 10., "end": 15., "unit_id": "unit-1", "status": "timed-text-provisional"}]
                             if count >= 3 else [])}]}


def response(body, *, cost=0.00001, support="supported"):
    answers = {}
    for key, question in body["questions"].items():
        answers[key] = ({"type": "noul", "noul": 1. if key == "img" else 0.}
                        if question["type"] == "noul" else
                        {"type": "choice", "choice": support,
                         "probabilities": {label: 1. if label == support else 0. for label in experiment.SUPPORT_BONUS},
                         "confidence": 1.})
    return {"id": "mock-request", "model": "typesafe/jev-1.13-20260917", "provider": "TypeSafe",
            "answers": answers, "usage": {"input_tokens": 100, "output_tokens": 30, "cost": cost}}


def execute(tmp_path, value=None, **kwargs):
    run = experiment.prepare_run(value or capture())
    return experiment.execute_run(run, tmp_path, allow_hosted=True, max_calls=2, max_usd=0.1,
                                  api_key="fake-test-key", **kwargs)


def test_prepare_is_private_dry_frozen_and_preserves_original_authority():
    value = capture()
    original = deepcopy(value)
    run = experiment.prepare_run(value)
    assert run["status"] == "dry-run" and value == original and run["frozen"] == original
    assert run["variants"] == list(experiment.VARIANTS)
    assert run["budget_policy"]["maximum_planned_calls"] == 2
    intent = run["requests"]["waiting"]["intent"]["body"]
    evidence = run["requests"]["waiting"]["evidence"]["body"]
    assert intent["state"]["query"] == original["cases"][0]["query"]
    assert intent["state"]["explicit_film_ids"] == []
    assert set(intent["questions"]) == {"img", "txt", "lex"}
    assert len(evidence["questions"]) == 48 and len(evidence["state"]["candidates"]) == 48
    assert all(question["type"] == "choice" for question in evidence["questions"].values())
    assert "matched_frame_url" not in json.dumps(evidence)
    assert run["human_review"]["relevance"] is None
    value["cases"][0]["query"] = "mutation"
    assert run["frozen"] == original


@pytest.mark.parametrize("mutation", ["scope", "snapshot", "duplicate", "time", "rank", "query", "identity"])
def test_prepare_rejects_broken_capture_authority(mutation):
    value = capture()
    case = value["cases"][0]
    if mutation == "scope":
        case["film_ids"] = ["film-0"]
    elif mutation == "snapshot":
        case["snapshot_after"]["frames"] += 1
    elif mutation == "duplicate":
        case["candidates"][1]["unit_id"] = case["candidates"][0]["unit_id"]
    elif mutation == "time":
        case["candidates"][0]["t_end"] = -1
    elif mutation == "rank":
        case["candidates"][0]["debug"]["channels"]["img"]["rank"] = True
    elif mutation == "query":
        case["query"] = " "
    else:
        case["id"] = "../escape"
    with pytest.raises(ValueError):
        experiment.prepare_run(value)


def test_excerpts_disclose_clipping_without_changing_raw_evidence_or_sending_images():
    value = capture(1)
    row = value["cases"][0]["candidates"][0]
    row.update(caption="c" * 500, matched_text="w" * 300, matched_text_view="dialogue")
    run = experiment.prepare_run(value)
    excerpts = run["requests"]["waiting"]["evidence"]["body"]["state"]["candidates"]["c0"]["excerpts"]
    assert [len(item["text"]) for item in excerpts] == [384, 256]
    assert all(item["truncated"] for item in excerpts)
    assert excerpts[0]["full_text_sha256"] == experiment.digest(row["caption"])
    assert run["frozen"]["cases"][0]["candidates"][0] == row


def test_four_variants_change_only_head_order_and_neutral_signals_are_exact_baseline():
    case = capture()["cases"][0]
    for row in case["candidates"]:
        row["debug"]["channels"]["img"]["rank"] = 1000
    case["candidates"][5]["debug"]["channels"]["img"]["rank"] = 1
    original = deepcopy(case)
    neutral = experiment.rerank(case, {"img": 0, "txt": 0, "lex": 0}, {"c2": "unknown", "c4": "contradicted"})
    assert all(rows == case["candidates"] for rows in neutral.values())
    variants = experiment.rerank(case, {"img": 1}, {"c20": "supported"})
    assert variants["baseline"] == case["candidates"]
    assert variants["intent-only"] != variants["baseline"]
    assert variants["intent-only"][0]["unit_id"] == "unit-5"
    assert variants["evidence-judgment-only"][0]["unit_id"] == "unit-20"
    for rows in variants.values():
        assert rows[48:] == case["candidates"][48:]
        assert {row["unit_id"] for row in rows} == {row["unit_id"] for row in case["candidates"]}
        assert {row["unit_id"]: row for row in rows} == {row["unit_id"]: row for row in case["candidates"]}
    assert case == original


def test_missing_evidence_cannot_gain_model_bonus_and_absent_lex_channel_is_not_invented():
    case = capture(3)["cases"][0]
    case["candidates"][2]["caption"] = ""
    variants = experiment.rerank(case, {"lex": 1}, {"c2": "supported"})
    assert all(rows == case["candidates"] for rows in variants.values())


def test_paid_execution_reuses_two_decisions_for_four_variants_and_keeps_human_grades_pending(tmp_path):
    calls = []
    def transport(body, **kwargs):
        calls.append((deepcopy(body), kwargs))
        return response(body)
    run = execute(tmp_path, transport=transport)
    assert run["status"] == "completed" and len(calls) == 2
    assert run["budget"]["attempted_calls"] == 2
    assert run["budget"]["reported_cost_usd"] == pytest.approx(0.00002)
    assert all(options["timeout"] == 10. for _, options in calls)
    result = run["results"][0]
    assert set(result["variants"]) == set(experiment.VARIANTS) and result["fallback_components"] == []
    assert result["diagnostics"]["baseline"]["reference_ranks"][0]["ranks"] == [3]
    assert result["diagnostics"]["baseline"]["reference_ranks"][0]["verification_group"] == "frame-inspected-reference"
    assert result["diagnostics"]["baseline"]["prefixes"]["5"]["returned"] == 5
    assert result["diagnostics"]["baseline"]["reference_ranks"][1]["verification_group"] == "provisional-reference"
    assert all(row["relevance"] is None and row["candidate_recall"] is None for row in result["diagnostics"].values())
    blind = json.loads((tmp_path / "blind-review.json").read_text(encoding="utf-8"))
    assert set(blind["cases"][0]["lists"]) == set("ABCD")
    for rows in blind["cases"][0]["lists"].values():
        assert len(rows) == 12 and [row["rank"] for row in rows] == list(range(1, 13))
        assert all(row["human_grade"] is None and row["film_title"] for row in rows)
        assert all("debug" not in row for row in rows)
    assert "intent-only" not in json.dumps(blind)
    assert "fake-test-key" not in (tmp_path / "run.json").read_text()
    assert (tmp_path / "blind-key.json").is_file()


def test_exact_cache_replays_without_permission_or_key_and_modified_receipt_never_retries(tmp_path):
    original = execute(tmp_path / "original", transport=lambda body, **_: response(body))
    replay = experiment.execute_run(experiment.prepare_run(capture()), tmp_path / "replay",
                                    cache_dir=tmp_path / "original" / "cache")
    assert replay["status"] == "completed" and replay["budget"]["attempted_calls"] == 0
    assert all(item["transport_seconds"] is None for item in replay["measurements"])
    assert replay["budget"]["reported_cost_usd"] == 0
    assert replay["results"][0]["variants"] == original["results"][0]["variants"]
    cache = next((tmp_path / "original" / "cache").glob("*.json"))
    damaged = json.loads(cache.read_text())
    damaged["response"]["model"] = "other/model"
    cache.write_text(json.dumps(damaged))
    calls = []
    broken = execute(tmp_path / "damaged", cache_dir=tmp_path / "original" / "cache",
                     transport=lambda *a, **kw: calls.append(a))
    assert broken["status"] == "completed-with-fallbacks" and not calls
    assert any(item["status"] == "failed" for item in broken["measurements"])


def test_query_or_generation_change_cannot_reuse_old_cache(tmp_path):
    execute(tmp_path / "original", transport=lambda body, **_: response(body))
    changed = capture()
    changed["cases"][0]["query"] += " in daylight"
    result = experiment.execute_run(experiment.prepare_run(changed), tmp_path / "changed",
                                     cache_dir=tmp_path / "original" / "cache")
    assert result["status"] == "completed-with-fallbacks"
    assert all(item["status"] == "skipped" for item in result["measurements"])
    assert all(item["transport_seconds"] is None for item in result["measurements"])


@pytest.mark.parametrize("mutation", ["request", "source", "formula"])
def test_prepared_run_tampering_refuses_before_any_attempt(tmp_path, mutation):
    run = experiment.prepare_run(capture())
    if mutation == "request":
        run["requests"]["waiting"]["intent"]["body"]["state"]["query"] = "new query"
    elif mutation == "source":
        run["frozen"]["cases"][0]["candidates"][0]["caption"] = "different evidence"
    else:
        run["parameters"]["intent_strength"] = 9
    with pytest.raises(ValueError, match="changed"):
        experiment.execute_run(run, tmp_path)
    assert not (tmp_path / "execution-started.json").exists()


@pytest.mark.parametrize("failure", ["timeout", "missing-cost", "nan-cost", "infinite-cost", "cost-over-reserve"])
def test_unknown_or_excessive_cost_stops_remaining_calls_with_explicit_fallbacks(tmp_path, failure):
    calls = []
    def transport(body, **_):
        calls.append(body)
        if failure == "timeout":
            raise TimeoutError("bounded request timeout")
        raw = response(body)
        if failure == "missing-cost":
            raw["usage"].pop("cost")
        elif failure == "nan-cost":
            raw["usage"]["cost"] = float("nan")
        elif failure == "infinite-cost":
            raw["usage"]["cost"] = float("inf")
        else:
            raw["usage"]["cost"] = 0.02
        return raw
    run = execute(tmp_path, transport=transport)
    assert len(calls) == 1 and run["status"] == "completed-with-fallbacks"
    assert run["budget"]["halt_reason"]
    if failure != "cost-over-reserve":
        assert run["budget"]["unpriced_attempts"] == 1
    assert run["measurements"][1]["status"] == "skipped"
    assert "evidence" in run["results"][0]["fallback_components"]
    assert run["results"][0]["variants"]["evidence-judgment-only"] == run["frozen"]["cases"][0]["candidates"]


def test_invalid_answer_is_not_cached_and_does_not_retry_but_independent_stage_can_finish(tmp_path):
    calls = []
    def transport(body, **_):
        calls.append(body)
        raw = response(body)
        if len(calls) == 1:
            raw["answers"]["img"]["noul"] = 1.2
        return raw
    run = execute(tmp_path, transport=transport)
    assert len(calls) == 2 and run["results"][0]["fallback_components"] == ["intent"]
    assert len(list((tmp_path / "cache").glob("*.json"))) == 1
    assert run["results"][0]["variants"]["both"] == run["results"][0]["variants"]["evidence-judgment-only"]


def test_one_call_limit_and_restart_guard_are_durable(tmp_path):
    calls = []
    run = experiment.prepare_run(capture())
    result = experiment.execute_run(run, tmp_path, allow_hosted=True, max_calls=1, max_usd=0.1,
        api_key="fake", transport=lambda body, **_: (calls.append(body) or response(body)))
    assert len(calls) == 1 and result["measurements"][1]["status"] == "skipped"
    assert json.loads((tmp_path / "run.json").read_text())["budget"]["attempted_calls"] == 1
    with pytest.raises(ValueError, match="already executed"):
        experiment.execute_run(experiment.prepare_run(capture()), tmp_path)


@pytest.mark.parametrize("options", [{"max_calls": 1}, {"allow_hosted": True}, {"max_usd": 2},
                                      {"timeout": 31}, {"max_calls": True}, {"max_usd": float("nan")}])
def test_invalid_permission_or_budget_never_creates_execution(tmp_path, options):
    with pytest.raises(ValueError):
        experiment.execute_run(experiment.prepare_run(capture()), tmp_path, **options)
    assert not (tmp_path / "execution-started.json").exists()


def test_empty_pool_needs_no_calls_and_dry_cli_does_not_read_environment(tmp_path, monkeypatch):
    import dotenv
    monkeypatch.setattr(dotenv, "dotenv_values", lambda *a, **k: pytest.fail("dry run read credentials"))
    monkeypatch.setattr(experiment, "request_decisions", lambda *a, **k: pytest.fail("dry run used network"))
    value = capture(0)
    source, output = tmp_path / "input.json", tmp_path / "dry"
    source.write_text(json.dumps(value))
    assert experiment.main(["--input", str(source), "--out", str(output)]) == 0
    saved = json.loads((output / "run.json").read_text())
    assert saved["status"] == "dry-run" and saved["budget_policy"]["maximum_planned_calls"] == 0
    run = experiment.execute_run(experiment.prepare_run(value), tmp_path / "empty")
    assert run["status"] == "completed" and run["budget"]["attempted_calls"] == 0
    assert all(rows == [] for rows in run["results"][0]["variants"].values())


def test_http_adapter_uses_official_endpoint_one_attempt_and_size_bound():
    import httpx
    attempts = []
    def handler(request):
        attempts.append(request)
        return httpx.Response(200, json={})
    body = {"model": experiment.MODEL, "state": {"query": "face"}, "questions": {}}
    with httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False) as client:
        assert experiment.request_decisions(body, api_key="fake", timeout=3, client=client) == {}
    assert len(attempts) == 1 and attempts[0].method == "POST"
    assert str(attempts[0].url) == "https://openrouter.ai/api/alpha/decisions"
    assert json.loads(attempts[0].content) == body
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(302, headers={"location": "https://example.com"}))) as client:
        with pytest.raises(ValueError, match="HTTP 302"):
            experiment.request_decisions(body, api_key="fake", timeout=3, client=client)
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b"x" * (experiment.MAX_RESPONSE_BYTES + 1)))) as client:
        with pytest.raises(ValueError, match="bounded size"):
            experiment.request_decisions(body, api_key="fake", timeout=3, client=client)


def test_transport_factory_has_short_connect_budget_no_retries_and_one_connection(monkeypatch):
    import httpx
    options = {}
    monkeypatch.setattr(httpx, "HTTPTransport", lambda **kwargs: options.update(transport=kwargs) or "transport")
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: options.update(client=kwargs) or "client")
    assert experiment._http_client(10) == "client"
    assert options["transport"]["retries"] == 0
    assert options["transport"]["limits"].max_connections == 1
    assert options["client"]["follow_redirects"] is False
    assert options["client"]["timeout"].connect == 1 and options["client"]["timeout"].read == 10


def test_one_client_is_reused_and_closed_for_the_whole_run(tmp_path, monkeypatch):
    clients, requests = [], []
    class Client:
        closed = False
        def close(self):
            self.closed = True
    def factory(timeout):
        clients.append(Client())
        return clients[-1]
    def request(body, *, client, **_):
        requests.append(client)
        return response(body)
    monkeypatch.setattr(experiment, "_http_client", factory)
    monkeypatch.setattr(experiment, "request_decisions", request)
    run = execute(tmp_path)
    assert run["status"] == "completed" and len(clients) == 1
    assert requests == [clients[0], clients[0]] and clients[0].closed


def test_cooperative_stop_finishes_current_receipt_but_starts_no_next_paid_call(tmp_path):
    calls = []
    def transport(body, **_):
        calls.append(body)
        (tmp_path / "STOP").touch()
        return response(body)
    run = execute(tmp_path, transport=transport)
    assert len(calls) == 1 and run["measurements"][0]["status"] == "completed"
    assert run["measurements"][1]["status"] == "skipped"
    assert "STOP" in run["budget"]["halt_reason"]
    assert len(list((tmp_path / "cache").glob("*.json"))) == 1
    assert run["budget"]["unpriced_attempts"] == 0


def test_interrupt_during_attempt_persists_unknown_cost_and_never_retries(tmp_path):
    calls = []
    def transport(*args, **kwargs):
        calls.append(args)
        raise KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        execute(tmp_path, transport=transport)
    run = json.loads((tmp_path / "run.json").read_text())
    assert len(calls) == 1 and run["status"] == "interrupted"
    assert run["budget"]["attempted_calls"] == 1 and run["budget"]["unpriced_attempts"] == 1
    assert run["measurements"][0]["status"] == "interrupted"


def test_serialization_failure_after_response_still_accounts_for_valid_cost(tmp_path):
    calls = []
    def transport(body, **_):
        calls.append(body)
        raw = response(body)
        if len(calls) == 1:
            raw["answers"]["img"]["noul"] = float("nan")
        return raw
    run = execute(tmp_path, transport=transport)
    assert len(calls) == 2 and run["budget"]["reported_cost_usd"] == pytest.approx(0.00002)
    assert run["budget"]["unpriced_attempts"] == 0
    assert run["measurements"][0]["status"] == "failed" and run["measurements"][1]["status"] == "completed"


@pytest.mark.parametrize("failed_transport", [False, True])
def test_transport_timing_excludes_slow_bookkeeping_and_client_setup(tmp_path, monkeypatch, failed_transport):
    clock = [0.0]
    original_write = experiment._write
    def write(path, value):
        clock[0] += 0.2
        original_write(path, value)
    class Client:
        def close(self):
            pass
    def factory(timeout):
        clock[0] += 3.0
        return Client()
    def request(body, **_):
        clock[0] += 0.05
        if failed_transport:
            raise TimeoutError("timed fixture")
        return response(body)
    monkeypatch.setattr(experiment.time, "perf_counter", lambda: clock[0])
    monkeypatch.setattr(experiment, "_write", write)
    monkeypatch.setattr(experiment, "_http_client", factory)
    monkeypatch.setattr(experiment, "request_decisions", request)
    run = execute(tmp_path)
    first = run["measurements"][0]
    assert first["transport_seconds"] == pytest.approx(0.05)
    assert first["seconds"] >= 3.2
    if failed_transport:
        assert first["status"] == "failed" and run["measurements"][1]["transport_seconds"] is None
    else:
        assert run["measurements"][1]["transport_seconds"] == pytest.approx(0.05)
        assert run["measurements"][1]["seconds"] == pytest.approx(0.65)
