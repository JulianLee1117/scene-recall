from copy import deepcopy
from dataclasses import dataclass
import json
from types import SimpleNamespace

import pytest

from pipeline.experiments import intent_retrieval as exp


@dataclass
class Intent:
    query: str
    film_ids: tuple[str, ...]


@pytest.fixture(autouse=True)
def isolated_interpreter(monkeypatch):
    def build(query, *, film_ids=()):
        return {"model": "typesafe/jev-1.13", "state": {"query": query, "film_ids": list(film_ids)},
                "questions": {"mood": {"type": "noul", "instructions": "Does the query request mood?"}}}

    def parse(raw, *, query, film_ids=()):
        if raw.get("answers") != {"mood": {"type": "noul", "noul": 0.8}} or "usage" not in raw:
            raise ValueError("Invalid receipt")
        return Intent(query, tuple(film_ids))

    monkeypatch.setattr(exp, "_intent_api", lambda: SimpleNamespace(build_intent_request=build, parse_intent_response=parse))


def fixture(count=2):
    return {"version": 1, "cases": [{"id": f"query-{i}", "group_id": "same-scene", "query": "romantic tension",
            "film_ids": ["film"], "expected_windows": [{"anchor_id": "scene", "film_id": "film", "start": 20,
            "end": 30, "unit_id": "anchor", "status": "frame-verified"}]} for i in range(count)]}


def response(cost=0.0001):
    return {"model": "typesafe/jev-1.13-20260917", "usage": {"cost": cost, "input_tokens": 100, "output_tokens": 0},
            "answers": {"mood": {"type": "noul", "noul": 0.8}}}


def row(unit="anchor", **kwargs):
    return {"unit_id": unit, "film_id": "film", "t_start": 20, "t_end": 30, **kwargs}


def capture(*args):
    return {"variants": {name: {"results": [row()], "plan": {}, "diagnostics": {}} for name in exp.VARIANTS},
            "diagnostics": {"shared_capture": True}}


def execute(tmp_path, *, value=None, **kwargs):
    return exp.execute_run(exp.prepare_run(value or fixture()), tmp_path,
                           capture=kwargs.pop("capture", capture), snapshot=kwargs.pop("snapshot", lambda: {"units": 1}),
                           allow_hosted=kwargs.pop("allow_hosted", True), max_calls=kwargs.pop("max_calls", 16),
                           max_usd=kwargs.pop("max_usd", 0.05), api_key="private-key",
                           transport=kwargs.pop("transport", lambda *args, **kw: response()), **kwargs)


def test_query_only_requests_and_frozen_inputs():
    value = fixture()
    prepared = exp.prepare_run(value)
    value["cases"][0]["query"] = "changed"
    body = prepared["requests"]["query-0"]["body"]
    assert body["state"] == {"query": "romantic tension", "film_ids": ["film"]}
    assert "anchor" not in json.dumps(body)
    assert prepared["fixture"]["cases"][0]["query"] == "romantic tension"


def test_cumulative_journal_reserved_before_transport_and_report(tmp_path):
    calls = []

    def transport(body, **kwargs):
        journal = json.loads((tmp_path / "run.json").read_text())
        assert journal["budget"]["attempted_calls"] == len(calls) + 1
        assert journal["budget"]["unpriced_attempts"] == 1
        calls.append(body)
        return response()

    result = execute(tmp_path, transport=transport)
    assert result["status"] == "completed"
    assert result["budget"]["reported_cost_usd"] == pytest.approx(0.0002)
    report = json.loads((tmp_path / "summary.json").read_text())
    assert report["groups"] == 1 and report["completed_cases"] == 2
    assert report["human_relevance"] == "pending" and report["exhaustive_recall"] is None
    assert "private-key" not in (tmp_path / "run.json").read_text()
    assert not (tmp_path / "RUNNING.lock").exists()


def test_decisions_then_unpaid_capture_resume_does_not_repeat_calls(tmp_path):
    result = execute(tmp_path, decisions_only=True, capture=lambda *a: pytest.fail("Capture called"))
    assert result["status"] == "decisions-complete"
    assert not result["cases"]
    result = execute(tmp_path, resume=True, allow_hosted=False, max_calls=0, max_usd=0,
                     transport=lambda *a, **kw: pytest.fail("Paid call repeated"))
    assert result["status"] == "completed"
    assert result["budget"]["attempted_calls"] == 2
    result = execute(tmp_path, resume=True, allow_hosted=False, max_calls=0, max_usd=0,
                     capture=lambda *a: pytest.fail("Completed capture repeated"))
    assert result["status"] == "completed"


@pytest.mark.parametrize("change", [lambda r: r.pop("usage"), lambda r: r["usage"].pop("cost"),
                                   lambda r: r["usage"].update(cost=float("nan"))])
def test_unknown_cost_stops_calls_and_resume(tmp_path, change):
    calls = []

    def transport(*args, **kwargs):
        calls.append(1)
        raw = response()
        change(raw)
        return raw

    result = execute(tmp_path, transport=transport)
    assert len(calls) == 1 and result["budget"]["unpriced_attempts"] == 1
    assert result["status"] == "halted"
    with pytest.raises(ValueError, match="unknown cost"):
        execute(tmp_path, resume=True)


def test_transport_exception_redacted_and_interrupt_reserved(tmp_path):
    def error(*args, **kwargs):
        raise RuntimeError("private-key in provider body")

    result = execute(tmp_path / "error", transport=error)
    assert result["status"] == "halted"
    assert "private-key" not in (tmp_path / "error" / "run.json").read_text()

    def interrupt(*args, **kwargs):
        raise KeyboardInterrupt()

    with pytest.raises(KeyboardInterrupt):
        execute(tmp_path / "interrupt", transport=interrupt)
    saved = json.loads((tmp_path / "interrupt" / "run.json").read_text())
    assert saved["budget"]["unpriced_attempts"] == 1
    assert not (tmp_path / "interrupt" / "RUNNING.lock").exists()


def test_capture_failure_resumes_without_repeating_paid_request(tmp_path):
    with pytest.raises(RuntimeError):
        execute(tmp_path, capture=lambda *a: (_ for _ in ()).throw(RuntimeError("local capture failed")))
    assert json.loads((tmp_path / "run.json").read_text())["budget"]["attempted_calls"] == 1
    calls = []
    result = execute(tmp_path, resume=True, transport=lambda *a, **kw: calls.append(1) or response())
    assert len(calls) == 1 and result["status"] == "completed"


@pytest.mark.parametrize("scope_or_time", [{"film_id": "other-film"}, {"t_end": 10}])
def test_invalid_capture_cannot_enter_report(tmp_path, scope_or_time):
    def bad(*args):
        result = capture()
        result["variants"]["jev"]["results"] = [row(**scope_or_time)]
        return result

    with pytest.raises(ValueError):
        execute(tmp_path, capture=bad)
    summary = json.loads((tmp_path / "summary.json").read_text())
    assert summary["completed_cases"] == 0


def test_changing_snapshot_excludes_entire_case(tmp_path):
    versions = iter(({"units": 1}, {"units": 2}))
    with pytest.raises(ValueError, match="versions changed"):
        execute(tmp_path, snapshot=lambda: next(versions))
    assert not list(tmp_path.glob("*-comparison.json"))


def test_cost_reserve_and_stop_prevent_extra_calls(tmp_path):
    result = execute(tmp_path / "cost", max_usd=exp.CALL_RESERVE_USD)
    assert result["budget"]["attempted_calls"] == 1
    result = execute(tmp_path / "calls", max_calls=1)
    assert result["budget"]["attempted_calls"] == 1
    output = tmp_path / "stop"
    output.mkdir()
    (output / "STOP").touch()
    result = execute(output)
    assert result["budget"]["attempted_calls"] == 0 and result["status"] == "stopped"


def test_excessive_reported_cost_blocks_remaining_work(tmp_path):
    result = execute(tmp_path, transport=lambda *a, **kw: response(exp.CALL_RESERVE_USD * 2))
    assert result["budget"]["attempted_calls"] == 1
    assert not result["cases"]


def test_decision_tampering_and_capture_tampering_rejected_on_resume(tmp_path):
    execute(tmp_path / "decision", decisions_only=True)
    path = tmp_path / "decision" / "query-0-decision.json"
    raw = json.loads(path.read_text())
    raw["model"] = "changed"
    path.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="receipt"):
        execute(tmp_path / "decision", resume=True)
    execute(tmp_path / "capture")
    path = tmp_path / "capture" / "query-0-comparison.json"
    path.write_text("{}")
    with pytest.raises(ValueError, match="receipt"):
        execute(tmp_path / "capture", resume=True)


def test_changed_fixture_budget_and_inflight_lock_rejected(tmp_path):
    execute(tmp_path, decisions_only=True)
    changed = fixture()
    changed["cases"][0]["query"] = "different"
    with pytest.raises(ValueError, match="exact frozen"):
        execute(tmp_path, resume=True, value=changed)
    with pytest.raises(ValueError, match="cumulative"):
        execute(tmp_path, resume=True, max_calls=3)
    (tmp_path / "RUNNING.lock").touch()
    with pytest.raises(FileExistsError):
        execute(tmp_path, resume=True)


def test_novelty_is_against_full_returned_pool_and_windows_are_not_quality():
    value = capture()
    value["variants"]["normal"]["results"] = [row("other", t_start=0, t_end=10), row()]
    value["variants"]["jev"]["results"] = [row(), row("new", t_start=25, t_end=27)]
    report = exp.diagnostics(fixture()["cases"][0], value)["jev"]
    assert report["prefixes"]["5"]["absent_from_entire_ordinary_returned_pool"] == 1
    assert report["references"][0]["exact_unit_rank"] == 1
    assert report["relevance"] is None and report["exhaustive_recall"] is None


@pytest.mark.parametrize("change", [lambda v: v["cases"].append(deepcopy(v["cases"][0])),
                                   lambda v: v["cases"][0].update(id="../unsafe"),
                                   lambda v: v["cases"][0].update(film_ids=["film", "film"]),
                                   lambda v: v["cases"][0]["expected_windows"][0].update(end=1)])
def test_invalid_fixture(change):
    value = fixture()
    change(value)
    with pytest.raises(ValueError):
        exp.prepare_run(value)


@pytest.mark.parametrize("args", [{"max_calls": 17}, {"max_usd": 0.051}, {"max_calls": True},
                                 {"timeout": 31}, {"allow_hosted": False}])
def test_invalid_budgets_do_not_touch_output(tmp_path, args):
    with pytest.raises(ValueError):
        execute(tmp_path / "out", **args)
    assert not (tmp_path / "out").exists()


def test_builtin_fixture_uses_real_interpreter_contract(monkeypatch):
    import yaml
    from pipeline.search.intent import ASPECTS
    monkeypatch.undo()
    value = yaml.safe_load((exp.ROOT / "pipeline/eval/intent_retrieval_queries.yaml").read_text(encoding="utf-8"))
    prepared = exp.prepare_run(value)
    assert len(prepared["fixture"]["cases"]) == 16
    for request in prepared["requests"].values():
        assert set(request["body"]["questions"]) == set(ASPECTS)
        assert "expected_windows" not in json.dumps(request["body"])
    title = next(case for case in prepared["fixture"]["cases"] if case["id"] == "unconfirmed_title")
    assert title["film_ids"] == []
