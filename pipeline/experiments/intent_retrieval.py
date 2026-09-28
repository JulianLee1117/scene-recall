"""Bounded intent-guided retrieval comparison with resumable, priced receipts.

Dry by default. Hosted decisions use query text only; retrieval runs through the
existing local API so this process never loads an encoder or mutates indexes.
Known windows diagnose access to known footage, not exhaustive relevance.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import time

from pipeline.experiments.jev_probe import _write
from pipeline.experiments.search_intent import (
    CALL_RESERVE_USD, MAX_RESPONSE_BYTES, _http_client, _number, _payload_bytes,
    digest, request_decisions,
)

CONTRACT = "intent-guided-retrieval-comparison-v1"
MAX_CALLS = 16
MAX_USD = 0.05
VARIANTS = ("normal", "fixed", "jev")
PREFIXES = (5, 12, 48, 200)
ROOT = Path(__file__).resolve().parents[2]
LIMITS = (
    "Known-anchor/window ranks and candidate novelty are diagnostics, not relevance grades "
    "or exhaustive recall. Several related queries share anchors selected using existing evidence. "
    "A retained-frame check is not full-shot playback. Fixed/Jev share an allocated supplemental "
    "row budget, not equal physical compute. The comparison reuses retrieval/embedding work: "
    "its capture duration is not production latency for any individual strategy. "
    "No hosted decision sees expected windows, source annotations, or another query's results."
)


def _intent_api():
    from pipeline.search import intent
    return intent


def _implementation():
    names = ("pipeline/experiments/intent_retrieval.py", "pipeline/search/intent.py",
             "pipeline/search/intent_retrieval.py", "pipeline/experiments/search_intent.py")
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
            for name in names if (ROOT / name).is_file()}


def prepare_run(fixture, *, result_limit=200, supplemental_budget=120):
    value = json.loads(json.dumps(fixture, allow_nan=False))
    if (not isinstance(value, dict) or value.get("version") != 1
            or not isinstance(value.get("cases"), list) or not 1 <= len(value["cases"]) <= MAX_CALLS):
        raise ValueError("Supply a version-1 fixture with 1–16 cases")
    if (type(result_limit) is not int or not 1 <= result_limit <= 200
            or type(supplemental_budget) is not int or not 5 <= supplemental_budget <= 300):
        raise ValueError("Require result limit 1–200 and supplemental budget 5–300")
    seen, requests = set(), {}
    for case in value["cases"]:
        identifier = case.get("id") if isinstance(case, dict) else None
        if (not isinstance(identifier, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,79}", identifier)
                or identifier in seen):
            raise ValueError("Case IDs must be unique safe lowercase filenames")
        seen.add(identifier)
        query, scope = case.get("query"), case.setdefault("film_ids", [])
        if (not isinstance(query, str) or not query.strip() or len(query) > 2000
                or not isinstance(scope, list) or any(not isinstance(s, str) or not s for s in scope)
                or len(scope) != len(set(scope))):
            raise ValueError("Require original query and unique explicit film IDs")
        for field in ("expected_windows", "scene_windows", "exclusions"):
            if not isinstance(case.get(field, []), list):
                raise ValueError("Reference windows must be lists")
            for window in case.get(field, []):
                if (not isinstance(window, dict) or not isinstance(window.get("film_id"), str)
                        or not window["film_id"] or not _number(window.get("start"))
                        or not _number(window.get("end")) or window["end"] <= window["start"]
                        or (scope and window["film_id"] not in scope)):
                    raise ValueError("Reference windows must have valid scoped source times")
        body = _intent_api().build_intent_request(query, film_ids=scope)
        requests[identifier] = {"body": body, "sha256": digest(body)}
    return {"contract": CONTRACT, "status": "prepared", "fixture": value,
            "fixture_sha256": digest(value), "requests": requests,
            "requests_sha256": digest(requests), "implementation_sha256": _implementation(),
            "parameters": {"result_limit": result_limit, "supplemental_budget": supplemental_budget},
            "limits": LIMITS}


def _window_rank(rows, window, *, exact=False):
    return next((rank for rank, row in enumerate(rows, 1)
                 if row["film_id"] == window["film_id"] and
                 (row["unit_id"] == window.get("unit_id") if exact else
                  min(row["t_end"], window["end"]) > max(row["t_start"], window["start"]))), None)


def _validate_capture(value, scope, limit):
    if not isinstance(value, dict) or not isinstance(value.get("variants"), dict):
        raise ValueError("Comparison response needs variants")
    if set(value["variants"]) != set(VARIANTS):
        raise ValueError("Comparison must contain normal, fixed and Jev strategies")
    for variant in value["variants"].values():
        rows = variant.get("results") if isinstance(variant, dict) else None
        if not isinstance(rows, list) or len(rows) > limit:
            raise ValueError("Comparison results exceed the requested bound")
        seen = set()
        for row in rows:
            if (not isinstance(row, dict) or not isinstance(row.get("unit_id"), str)
                    or not row["unit_id"] or row["unit_id"] in seen
                    or not isinstance(row.get("film_id"), str) or not row["film_id"]
                    or (scope and row["film_id"] not in scope)
                    or not _number(row.get("t_start")) or not _number(row.get("t_end"))
                    or row["t_end"] <= row["t_start"]):
                raise ValueError("Results require unique source identities, valid times and preserved scope")
            seen.add(row["unit_id"])
    json.dumps(value, allow_nan=False)


def diagnostics(case, capture):
    baseline = capture["variants"]["normal"]["results"]
    baseline_ids = {row["unit_id"] for row in baseline}
    output = {}
    for strategy, variant in capture["variants"].items():
        rows = variant["results"]
        output[strategy] = {
            "prefixes": {str(k): {
                "returned": len(rows[:k]),
                "distinct_films": len({row["film_id"] for row in rows[:k]}),
                "ordinary_prefix_overlap": len({row["unit_id"] for row in rows[:k]} &
                                               {row["unit_id"] for row in baseline[:k]}),
                "absent_from_entire_ordinary_returned_pool": sum(
                    row["unit_id"] not in baseline_ids for row in rows[:k]),
            } for k in PREFIXES},
            "references": [{"anchor_id": w.get("anchor_id"), "status": w.get("status", "unverified"),
                            "exact_unit_rank": _window_rank(rows, w, exact=True) if w.get("unit_id") else None,
                            "overlapping_window_rank": _window_rank(rows, w)}
                           for w in case.get("expected_windows", [])],
            "scene_windows": [{"anchor_id": w.get("anchor_id"), "rank": _window_rank(rows, w)}
                              for w in case.get("scene_windows", [])],
            "known_exclusions": [{"anchor_id": w.get("anchor_id"), "rank": _window_rank(rows, w, exact=True)}
                                 for w in case.get("exclusions", [])],
            "relevance": None, "exhaustive_recall": None,
        }
    return output


def _read_decision(output, record, request, case):
    path = output / record["response_file"]
    raw = json.loads(path.read_text(encoding="utf-8"))
    if (digest(raw) != record.get("response_sha256") or request["sha256"] != record.get("request_sha256")):
        raise ValueError("Saved decision receipt does not match its frozen input")
    return _intent_api().parse_intent_response(raw, query=case["query"], film_ids=case["film_ids"])


def summarize_run(run, output):
    """Small machine-readable report; no model scores are called quality grades."""
    rows = []
    for identifier, receipt in run["cases"].items():
        if receipt["status"] != "completed":
            continue
        report = json.loads((Path(output) / receipt["file"]).read_text(encoding="utf-8"))
        if digest(report) != receipt["sha256"]:
            raise ValueError("Saved comparison receipt changed")
        rows.append({"id": identifier, "group_id": report["group_id"], "query": report["query"],
                     "intent_status": report["intent_status"], "comparison_seconds": report["comparison_seconds"],
                     "variants": report["diagnostics"]})
    return {"contract": CONTRACT, "status": run["status"], "budget": deepcopy(run["budget"]),
            "completed_cases": len(rows), "groups": len({row["group_id"] for row in rows}),
            "human_relevance": "pending", "exhaustive_recall": None, "cases": rows, "limits": LIMITS}


def execute_run(prepared, output, *, capture, snapshot, allow_hosted=False,
                max_calls=0, max_usd=0.0, api_key=None, transport=None,
                timeout=10.0, resume=False, decisions_only=False):
    """Run or resume exact inputs; priced completed decisions are never repeated.

    ``capture(case, intent_dict, parameters)`` returns the compare API envelope.
    A persisted unknown-cost attempt forbids any further hosted work. Retrieval
    may be resumed separately with ``decisions_only=False`` after pricing ends.
    """
    if (type(max_calls) is not int or not 0 <= max_calls <= MAX_CALLS
            or not _number(max_usd, high=MAX_USD) or not _number(timeout, low=0.1, high=30)):
        raise ValueError("Require at most 16 calls, $0.05 and a 0.1–30s timeout")
    if allow_hosted and (not api_key or max_calls < 1 or max_usd < CALL_RESERVE_USD):
        raise ValueError("Hosted execution needs explicit permission, key and sufficient reserves")
    if not allow_hosted and (max_calls or max_usd):
        raise ValueError("Budgets require hosted permission")
    expected = prepare_run(prepared["fixture"], **prepared["parameters"])
    if any(prepared.get(k) != expected[k] for k in
           ("contract", "fixture_sha256", "requests", "requests_sha256", "implementation_sha256", "parameters")):
        raise ValueError("Prepared inputs or implementation changed; prepare a new run")
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    journal_path = output / "run.json"
    if journal_path.exists():
        if not resume:
            raise ValueError("Existing run requires explicit resume")
        run = json.loads(journal_path.read_text(encoding="utf-8"))
        if run["prepared"] != prepared:
            raise ValueError("Resume requires the exact frozen fixture and implementation")
        if allow_hosted and (max_calls != run["budget"]["max_calls"] or max_usd != run["budget"]["max_usd"]):
            raise ValueError("Resume cannot change the cumulative hosted budget")
        if run["budget"]["unpriced_attempts"]:
            raise ValueError("An earlier attempted request has unknown cost; reconcile it before any new run")
    else:
        if not allow_hosted:
            raise ValueError("A new execution needs hosted permission; use resume to replay saved decisions")
        run = {"prepared": prepared, "status": "running", "created_at": datetime.now(timezone.utc).isoformat(),
               "decisions": {}, "cases": {}, "budget": {"max_calls": max_calls, "max_usd": max_usd,
               "attempted_calls": 0, "reported_cost_usd": 0.0, "unpriced_attempts": 0, "halt_reason": None},
               "transport_policy": {"automatic_retries": 0, "timeout_seconds": timeout,
                                    "note": "Transport timeout is not an end-to-end production deadline"}}
        _write(output / "prepared.json", prepared, exclusive=True)
    client, budget = None, run["budget"]
    # Verify completed receipts before any paid work on a resumed run.
    summarize_run(run, output)
    lock = output / "RUNNING.lock"
    # Exclusive create stops two resumes from reserving against the same journal.
    lock_handle = lock.open("x", encoding="utf-8")
    try:
        run["status"] = "running"
        run.pop("pause_reason", None)
        _write(journal_path, run)
        for case in prepared["fixture"]["cases"]:
            identifier, intent = case["id"], None
            request = prepared["requests"][identifier]
            if (output / "STOP").exists():
                run["status"] = "stopped"
                break
            decision = run["decisions"].get(identifier)
            if decision and decision["status"] == "completed":
                intent = _read_decision(output, decision, request, case)
            elif not decision:
                reason = budget["halt_reason"]
                if not allow_hosted:
                    reason = reason or "Hosted execution not enabled"
                if budget["attempted_calls"] >= budget["max_calls"]:
                    reason = reason or "Call limit reached"
                if budget["reported_cost_usd"] + CALL_RESERVE_USD > budget["max_usd"]:
                    reason = reason or "Insufficient remaining cost reserve"
                if reason:
                    run["status"] = "paused"
                    run["pause_reason"] = reason
                    break
                decision = {"status": "in-flight", "request_sha256": request["sha256"],
                            "cost_state": "unknown", "reserved_usd": CALL_RESERVE_USD}
                run["decisions"][identifier] = decision
                budget["attempted_calls"] += 1
                budget["unpriced_attempts"] += 1
                _write(journal_path, run)
                started = time.perf_counter()
                try:
                    if transport is None and client is None:
                        client = _http_client(timeout)
                    transport_started = time.perf_counter()
                    try:
                        raw = (request_decisions(deepcopy(request["body"]), api_key=api_key, timeout=timeout, client=client)
                               if transport is None else transport(deepcopy(request["body"]), api_key=api_key, timeout=timeout))
                    finally:
                        decision["transport_seconds"] = time.perf_counter() - transport_started
                    usage = raw.get("usage") if isinstance(raw, dict) else None
                    if isinstance(usage, dict) and _number(usage.get("cost")):
                        budget["reported_cost_usd"] += usage["cost"]
                        budget["unpriced_attempts"] -= 1
                        decision.update(cost_state="reported", reported_cost_usd=usage["cost"])
                        if usage["cost"] > CALL_RESERVE_USD or budget["reported_cost_usd"] > budget["max_usd"]:
                            budget["halt_reason"] = "Reported cost exceeded conservative reserve"
                    if len(_payload_bytes(raw)) > MAX_RESPONSE_BYTES:
                        raise ValueError("Response exceeds bounded size")
                    filename = f"{identifier}-decision.json"
                    _write(output / filename, raw, exclusive=True)
                    decision.update(response_file=filename, response_sha256=digest(raw))
                    intent = _intent_api().parse_intent_response(raw, query=case["query"], film_ids=case["film_ids"])
                    decision.update(status="completed", actual_model=raw["model"], usage=raw["usage"])
                except BaseException as exc:
                    decision.update(status="failed" if isinstance(exc, Exception) else "interrupted", error_type=type(exc).__name__)
                    if not isinstance(exc, Exception):
                        raise
                finally:
                    decision["decision_seconds"] = time.perf_counter() - started
                    if decision["cost_state"] == "unknown":
                        budget["halt_reason"] = "An attempted request has unknown cost"
                    _write(journal_path, run)
            if budget["halt_reason"]:
                run["status"] = "halted"
                break
            if decisions_only or run["cases"].get(identifier, {}).get("status") == "completed":
                continue
            before, start = snapshot(), time.perf_counter()
            try:
                result = capture(case, asdict(intent) if intent is not None else None, prepared["parameters"])
                seconds, after = time.perf_counter() - start, snapshot()
                _validate_capture(result, case["film_ids"], prepared["parameters"]["result_limit"])
                if not before or before != after:
                    raise ValueError("Index versions changed during this comparison; case excluded")
                report = {"case_id": identifier, "query": case["query"], "film_ids": case["film_ids"],
                          "group_id": case.get("group_id", identifier), "snapshot_before": before, "snapshot_after": after,
                          "comparison_seconds": seconds, "intent_status": decision["status"],
                          "comparison": result, "diagnostics": diagnostics(case, result)}
                filename = f"{identifier}-comparison.json"
                _write(output / filename, report)
                run["cases"][identifier] = {"status": "completed", "file": filename, "sha256": digest(report)}
            except Exception as exc:
                run["cases"][identifier] = {"status": "failed", "error_type": type(exc).__name__}
                run["status"] = "capture-stopped"
                _write(journal_path, run)
                raise
            _write(journal_path, run)
        else:
            run["status"] = "decisions-complete" if decisions_only else "completed"
    except BaseException as exc:
        if run["status"] == "running":
            run["status"] = "interrupted" if not isinstance(exc, Exception) else "failed"
        raise
    finally:
        run["updated_at"] = datetime.now(timezone.utc).isoformat()
        try:
            if client is not None:
                client.close()
        finally:
            try:
                _write(journal_path, run)
                _write(output / "summary.json", summarize_run(run, output))
            finally:
                lock_handle.close()
                lock.unlink()
    return run


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queries", type=Path, default=Path("pipeline/eval/intent_retrieval_queries.yaml"))
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=Path("config.yaml"))
    parser.add_argument("--api-base", default="http://127.0.0.1:8000")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--allow-hosted", action="store_true")
    parser.add_argument("--max-calls", type=int, default=0)
    parser.add_argument("--max-usd", type=float, default=0.0)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--decisions-only", action="store_true")
    parser.add_argument("--result-limit", type=int, default=200)
    parser.add_argument("--supplemental-budget", type=int, default=120)
    args = parser.parse_args(argv)
    import yaml
    prepared = prepare_run(yaml.safe_load(args.queries.read_text(encoding="utf-8")),
                           result_limit=args.result_limit, supplemental_budget=args.supplemental_budget)
    config_bytes = args.config.read_bytes()
    prepared["capture_configuration"] = {
        "api_base": args.api_base, "config_file_sha256": hashlib.sha256(config_bytes).hexdigest(),
        "note": "On-disk config identity; inspect comparison diagnostics for loaded API capabilities/code identity.",
    }
    if not args.execute:
        args.out.mkdir(parents=True, exist_ok=True)
        _write(args.out / "dry-run.json", prepared, exclusive=True)
        print(json.dumps({"status": "dry-run", "cases": len(prepared["fixture"]["cases"]),
                          "maximum_reserved_usd": len(prepared["fixture"]["cases"]) * CALL_RESERVE_USD}))
        return
    from pipeline.experiments.capture_search_intent import versions
    from pipeline.experiments.search_intent_review import loopback_base
    from dotenv import load_dotenv
    import os
    import httpx
    load_dotenv()
    settings = yaml.safe_load(config_bytes)
    db_path = Path(settings["paths"]["assets_dir"]) / "db"
    base = loopback_base(args.api_base)
    with httpx.Client(timeout=120, follow_redirects=False, trust_env=False) as client:
        def capture(case, intent, parameters):
            response = client.post(base + "/search/intent/compare", json={
                "q": case["query"], "film_ids": case["film_ids"], "intent": intent,
                "limit": parameters["result_limit"], "supplemental_budget": parameters["supplemental_budget"]})
            response.raise_for_status()
            print(json.dumps({"case": case["id"], "capture": "received"}), flush=True)
            return response.json()
        run = execute_run(prepared, args.out, capture=capture, snapshot=lambda: versions(db_path),
                          allow_hosted=args.allow_hosted, max_calls=args.max_calls, max_usd=args.max_usd,
                          api_key=os.getenv("OPENROUTER_API_KEY"), resume=args.resume,
                          decisions_only=args.decisions_only)
    print(json.dumps({"status": run["status"], "budget": run["budget"], "captured": len(run["cases"])}))


if __name__ == "__main__":
    main()
