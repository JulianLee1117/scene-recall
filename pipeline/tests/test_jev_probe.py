from copy import deepcopy
import json

import pytest

from pipeline.experiments import jev_probe as probe


def plan(count=2):
    body = {"model": probe.MODEL, "state": {"query": "a quiet corridor"}, "questions": {
        "visual": {"type": "noul", "instructions": "Is visible content requested?"},
        "support": {"type": "choice", "instructions": "Judge only supplied evidence.",
                    "criteria": {"yes": "Supported", "no": "Contradicted", "unclear": "Unknown"}}}}
    return {"version": 1, "contract": "bounded-probe-test-v1", "provenance": {"source": "fixture"},
            "requests": [{"id": f"case-{i}", "body": deepcopy(body), "expected": {"visual": True}}
                         for i in range(count)]}


def response(cost=0.0001):
    return {"model": probe.MODEL + "-20260917", "usage": {"cost": cost, "input_tokens": 100,
            "output_tokens": 0}, "answers": {"visual": {"type": "noul", "noul": 0.8},
            "support": {"type": "choice", "choice": "unclear", "confidence": 0.8,
                        "probabilities": {"yes": 0.1, "no": 0.1, "unclear": 0.8}}}}


def execute(tmp_path, *, value=None, transport=None, **kwargs):
    return probe.execute_run(probe.prepare_run(value or plan()), tmp_path / "output", allow_hosted=True,
                             max_calls=kwargs.pop("max_calls", 60), max_usd=kwargs.pop("max_usd", 0.25),
                             api_key="private-test-key", transport=transport or (lambda *a, **kw: response()),
                             **kwargs)


def test_freeze_and_exact_request_only_with_reserved_journal(tmp_path):
    original = plan()
    frozen = deepcopy(original)
    seen = []

    def send(body, **kwargs):
        journal = json.loads((tmp_path / "output" / "run.json").read_text())
        assert journal["implementation_sha256"]
        assert journal["budget"]["attempted_calls"] == len(seen) + 1
        assert journal["budget"]["unpriced_attempts"] == 1
        assert journal["measurements"][-1]["cost_state"] == "unknown"
        assert json.loads((tmp_path / "output" / "frozen-plan.json").read_text()) == frozen
        assert set(body) == {"model", "state", "questions"}
        seen.append(deepcopy(body))
        body["state"]["query"] = "transport mutation"
        return response()

    result = execute(tmp_path, value=original, transport=send)
    assert original == frozen == result["plan"]
    assert result["status"] == "completed"
    assert result["budget"]["unpriced_attempts"] == 0
    assert result["budget"]["reported_cost_usd"] == pytest.approx(0.0002)
    assert all(row["transport_seconds"] >= 0 and row["seconds"] >= row["transport_seconds"]
               for row in result["measurements"])
    assert len(list((tmp_path / "output").glob("*-response.json"))) == 2
    assert "private-test-key" not in (tmp_path / "output" / "run.json").read_text()


@pytest.mark.parametrize("mutate", [
    lambda r: r.pop("usage"),
    lambda r: r["usage"].pop("cost"),
    lambda r: r["usage"].update(cost=float("nan")),
    lambda r: r["usage"].update(cost=True),
])
def test_unknown_cost_stops_following_calls(tmp_path, mutate):
    calls = []

    def send(*args, **kwargs):
        calls.append(1)
        raw = response()
        mutate(raw)
        return raw

    result = execute(tmp_path, transport=send)
    assert len(calls) == 1
    assert result["budget"]["unpriced_attempts"] == 1
    assert result["measurements"][1]["status"] == "skipped"
    assert "unknown cost" in result["budget"]["halt_reason"]


def test_transport_error_redacted_and_never_retried(tmp_path):
    calls = []

    def send(*args, **kwargs):
        calls.append(1)
        raise ValueError("private-test-key provider-body")

    result = execute(tmp_path, transport=send)
    assert len(calls) == 1
    text = (tmp_path / "output" / "run.json").read_text()
    assert "private-test-key" not in text and "provider-body" not in text
    assert result["measurements"][0]["error_type"] == "ValueError"


def test_interrupt_stays_unpriced_and_cannot_resume(tmp_path):
    def send(*args, **kwargs):
        raise KeyboardInterrupt()

    with pytest.raises(KeyboardInterrupt):
        execute(tmp_path, transport=send)
    journal = json.loads((tmp_path / "output" / "run.json").read_text())
    assert journal["status"] == "interrupted"
    assert journal["budget"]["unpriced_attempts"] == 1
    assert journal["measurements"][0]["status"] == "interrupted"
    with pytest.raises(FileExistsError):
        execute(tmp_path)


def test_known_cost_is_accounted_before_invalid_answer(tmp_path):
    raw = response()
    raw["answers"]["extra"] = {"type": "noul", "noul": 0.5}
    result = execute(tmp_path, transport=lambda *a, **kw: raw)
    assert result["budget"]["attempted_calls"] == 2
    assert result["budget"]["reported_cost_usd"] == pytest.approx(0.0002)
    assert result["budget"]["unpriced_attempts"] == 0
    assert all(row["status"] == "failed" for row in result["measurements"])


@pytest.mark.parametrize("cost", [probe.CALL_RESERVE_USD * 2, 0.3])
def test_reported_cost_above_reserve_stops(tmp_path, cost):
    result = execute(tmp_path, transport=lambda *a, **kw: response(cost))
    assert result["budget"]["attempted_calls"] == 1
    assert result["budget"]["reported_cost_usd"] == cost
    assert result["measurements"][1]["status"] == "skipped"


def test_call_limit_cost_reserve_and_stop(tmp_path):
    result = execute(tmp_path / "calls", max_calls=1)
    assert result["budget"]["attempted_calls"] == 1
    result = execute(tmp_path / "cost", max_usd=probe.CALL_RESERVE_USD)
    assert result["budget"]["attempted_calls"] == 1
    stop = tmp_path / "STOP"
    stop.touch()
    result = execute(tmp_path / "stopped-run", stop_file=stop)
    assert result["budget"]["attempted_calls"] == 0


@pytest.mark.parametrize("changes", [{"max_calls": 61}, {"max_calls": True}, {"max_calls": 0},
                                    {"max_usd": 0.251}, {"max_usd": float("nan")},
                                    {"timeout": 31}, {"timeout": 0}])
def test_limits_rejected_without_transport_or_output(tmp_path, changes):
    with pytest.raises(ValueError):
        execute(tmp_path, transport=lambda *a, **kw: pytest.fail("Transport called"), **changes)
    assert not (tmp_path / "output").exists()


@pytest.mark.parametrize("mutate", [
    lambda r: r["plan"]["requests"][0]["body"]["state"].update(query="changed"),
    lambda r: r["implementation_sha256"].update(fake="changed"),
    lambda r: r["request_sha256"].update(fake="changed"),
])
def test_modified_prepared_run_rejected(tmp_path, mutate):
    run = probe.prepare_run(plan())
    mutate(run)
    with pytest.raises(ValueError):
        probe.execute_run(run, tmp_path / "output", allow_hosted=True, max_calls=2,
                          max_usd=0.1, api_key="x", transport=lambda *a, **kw: pytest.fail("Called"))
    assert not (tmp_path / "output").exists()


@pytest.mark.parametrize("identifier", ["../escape", "a/b", "a\\b", "a:b", "", "a.", "_a", 1])
def test_safe_ids(identifier):
    value = plan()
    value["requests"][0]["id"] = identifier
    with pytest.raises(ValueError):
        probe.prepare_run(value)


def test_duplicate_ids_payload_and_model_bounds():
    value = plan()
    value["requests"][1]["id"] = "CASE-0"
    with pytest.raises(ValueError):
        probe.prepare_run(value)
    value = plan()
    value["requests"][0]["body"]["state"] = "a" * probe.MAX_REQUEST_BYTES
    with pytest.raises(ValueError):
        probe.prepare_run(value)
    value = plan()
    value["requests"][0]["body"]["model"] = "other-model"
    with pytest.raises(ValueError):
        probe.prepare_run(value)


@pytest.mark.parametrize("mutate", [
    lambda r: r.update(model="unrelated"),
    lambda r: r["usage"].update(input_tokens=True),
    lambda r: r["answers"].pop("visual"),
    lambda r: r["answers"]["visual"].update(type="choice"),
    lambda r: r["answers"]["visual"].update(noul=float("inf")),
    lambda r: r["answers"]["visual"].update(noul=True),
    lambda r: r["answers"]["support"].update(choice="invented"),
    lambda r: r["answers"]["support"].update(confidence=float("nan")),
    lambda r: r["answers"]["support"]["probabilities"].update(extra=0),
    lambda r: r["answers"]["support"]["probabilities"].update(yes=0.9),
])
def test_response_contract_mismatch(mutate):
    raw = response()
    mutate(raw)
    with pytest.raises(ValueError):
        probe._validate_response(raw, plan()["requests"][0]["body"])


def test_cli_dry_does_not_read_key_and_never_overwrites(tmp_path, monkeypatch):
    import dotenv
    monkeypatch.setattr(dotenv, "dotenv_values", lambda *a, **kw: pytest.fail("Read key"))
    monkeypatch.setattr(probe, "_http_client", lambda *a, **kw: pytest.fail("Created client"))
    source = tmp_path / "plan.json"
    source.write_text(json.dumps(plan()), encoding="utf-8")
    args = ["--input", str(source), "--out", str(tmp_path / "dry")]
    assert probe.main(args) == 0
    journal = json.loads((tmp_path / "dry" / "run.json").read_text())
    assert journal["status"] == "dry-run"
    with pytest.raises(FileExistsError):
        probe.main(args)
    with pytest.raises(SystemExit):
        probe.main(args + ["--allow-hosted"])


def test_pooled_client_reused_and_closed(tmp_path, monkeypatch):
    class Client:
        closed = False

        def close(self):
            self.closed = True

    client, clients, calls = Client(), [], []
    monkeypatch.setattr(probe, "_http_client", lambda t: clients.append(client) or client)

    def send(body, **kwargs):
        calls.append(kwargs["client"])
        return response()

    monkeypatch.setattr(probe, "request_decisions", send)
    run = probe.execute_run(probe.prepare_run(plan()), tmp_path / "output", allow_hosted=True,
                            max_calls=2, max_usd=0.1, api_key="private-test-key")
    assert run["status"] == "completed"
    assert clients == [client] and calls == [client, client] and client.closed
