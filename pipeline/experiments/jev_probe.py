"""Run a frozen, bounded Jev Decisions probe; dry by default, without retries.

This isolated runner consumes supplied evidence only. It does not retrieve,
ingest, activate a model, or interpret model judgments as verified facts.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import time

from pipeline.experiments import music_edit, search_intent
from pipeline.experiments.search_intent import (
    CALL_RESERVE_USD, MAX_REQUEST_BYTES, MAX_RESPONSE_BYTES, MODEL,
    _http_client, _number, _payload_bytes, digest, request_decisions,
)

MAX_CALLS = 60
MAX_USD = 0.25


def _write(path, value, *, exclusive=False):
    # Reservations are flushed before transport. An interrupted call remains
    # explicitly unpriced in this journal, even after an abrupt process exit.
    encoded = json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False)
    path = Path(path)
    target = path if exclusive else path.with_suffix(path.suffix + ".tmp")
    with target.open("x" if exclusive else "w", encoding="utf-8") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())
    if not exclusive:
        os.replace(target, path)


def _implementation():
    return {Path(path).name: hashlib.sha256(Path(path).read_bytes()).hexdigest()
            for path in (__file__, search_intent.__file__, music_edit.__file__)}


def prepare_run(value):
    plan = json.loads(json.dumps(value, allow_nan=False))
    if (not isinstance(plan, dict) or type(plan.get("version")) is not int or plan["version"] != 1
            or not isinstance(plan.get("contract"), str) or not plan["contract"].strip()
            or len(plan["contract"]) > 160 or not isinstance(plan.get("provenance"), dict)
            or not plan["provenance"] or not isinstance(plan.get("requests"), list)
            or not 1 <= len(plan["requests"]) <= MAX_CALLS):
        raise ValueError("Require a version-1 plan, named contract, provenance and 1–60 requests")
    seen = set()
    for request in plan["requests"]:
        identifier = request.get("id") if isinstance(request, dict) else None
        if (not isinstance(identifier, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", identifier)
                or identifier.lower() in seen):
            raise ValueError("Request IDs must be unique, case-insensitive safe filenames")
        seen.add(identifier.lower())
        body = request.get("body")
        if (not isinstance(body, dict) or set(body) != {"model", "state", "questions"}
                or body["model"] != MODEL or not isinstance(body["state"], (dict, str))
                or not isinstance(body["questions"], dict) or not 1 <= len(body["questions"]) <= 64):
            raise ValueError("Require the fixed Jev model, supplied state and 1–64 questions")
        for key, question in body["questions"].items():
            if (not isinstance(key, str) or not key or len(key) > 100 or not isinstance(question, dict)
                    or question.get("type") not in ("noul", "choice")
                    or not isinstance(question.get("instructions"), str) or not question["instructions"].strip()):
                raise ValueError("Questions require names, instructions and Noul or Choice types")
            if question["type"] == "choice":
                criteria = question.get("criteria")
                if (not isinstance(criteria, dict) or not 2 <= len(criteria) <= 32
                        or any(not isinstance(k, str) or not k or not isinstance(v, str) or not v
                               for k, v in criteria.items())):
                    raise ValueError("Choice questions require 2–32 named criteria")
        if len(_payload_bytes(body)) > MAX_REQUEST_BYTES:
            raise ValueError("Request exceeds the fixed 64 KiB payload limit")
    return {"status": "dry-run", "created_at": datetime.now(timezone.utc).isoformat(),
            "plan": plan, "plan_sha256": digest(plan), "implementation_sha256": _implementation(),
            "request_sha256": {r["id"]: digest(r["body"]) for r in plan["requests"]},
            "budget_policy": {"reserve_usd_per_call": CALL_RESERVE_USD,
                              "maximum_planned_calls": len(plan["requests"]),
                              "note": "Conservative admission estimate, not a provider-enforced billing cap."},
            "timing_scope": "transport_seconds measures the HTTP invocation and complete JSON read only; "
                            "seconds includes reservation, validation and receipt writes, excluding the final "
                            "journal write. Neither isolates model computation or production search latency."}


def _validate_response(raw, body):
    if (not isinstance(raw, dict) or not isinstance(raw.get("model"), str)
            or not (raw["model"] == MODEL or raw["model"].startswith(MODEL + "-")
                    or raw["model"].startswith(MODEL + "."))):
        raise ValueError("Invalid actual model receipt")
    usage = raw.get("usage")
    if (not isinstance(usage, dict) or not _number(usage.get("cost"))
            or any(type(usage.get(key)) is not int or usage[key] < 0
                   for key in ("input_tokens", "output_tokens"))):
        raise ValueError("Invalid usage receipt")
    answers = raw.get("answers")
    if not isinstance(answers, dict) or set(answers) != set(body["questions"]):
        raise ValueError("Answer keys differ from frozen questions")
    for key, question in body["questions"].items():
        answer = answers[key]
        if not isinstance(answer, dict) or answer.get("type") != question["type"]:
            raise ValueError("Answer type differs from frozen question")
        if question["type"] == "noul":
            if not _number(answer.get("noul"), high=1):
                raise ValueError("Invalid Noul probability")
        else:
            probabilities = answer.get("probabilities")
            if (answer.get("choice") not in question["criteria"]
                    or not isinstance(probabilities, dict) or set(probabilities) != set(question["criteria"])
                    or not all(_number(n, high=1) for n in probabilities.values())
                    or abs(sum(probabilities.values()) - 1) > 0.021
                    or not _number(answer.get("confidence"), high=1)):
                raise ValueError("Invalid Choice probability distribution")
    return deepcopy(answers)


def execute_run(run, output, *, allow_hosted=False, max_calls=0, max_usd=0.0,
                timeout=10.0, api_key=None, transport=None, stop_file=None):
    if (type(max_calls) is not int or not 1 <= max_calls <= MAX_CALLS
            or not _number(max_usd, low=CALL_RESERVE_USD, high=MAX_USD)
            or not _number(timeout, low=0.1, high=30)):
        raise ValueError("Require 1–60 calls, a sufficient reserve up to $0.25, and a 0.1–30s timeout")
    if not allow_hosted or not isinstance(api_key, str) or not api_key.strip():
        raise ValueError("Execution requires explicit hosted permission and an API key")
    expected = prepare_run(run.get("plan"))
    if run.get("status") != "dry-run" or any(run.get(k) != expected[k] for k in
            ("plan_sha256", "implementation_sha256", "request_sha256", "budget_policy")):
        raise ValueError("Prepared plan or implementation changed; prepare a new run")
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    stop_file = Path(stop_file) if stop_file is not None else output / "STOP"
    _write(output / "frozen-plan.json", run["plan"], exclusive=True)
    run.update(status="running", measurements=[], budget={"max_calls": max_calls, "max_usd": max_usd,
               "attempted_calls": 0, "reported_cost_usd": 0.0, "unpriced_attempts": 0, "halt_reason": None},
               transport_policy={"client": "injected" if transport else "pooled-httpx", "automatic_retries": 0,
                                 "timeout_seconds": timeout, "stop_file": str(stop_file)})
    _write(output / "run.json", run)
    client, budget = None, run["budget"]
    try:
        for request in run["plan"]["requests"]:
            started = time.perf_counter()
            report = {"id": request["id"], "request_sha256": run["request_sha256"][request["id"]],
                      "status": "skipped", "transport_seconds": None, "cost_state": "not-attempted"}
            run["measurements"].append(report)
            try:
                if stop_file.exists():
                    budget["halt_reason"] = "Stop file present"
                if not budget["halt_reason"] and budget["attempted_calls"] >= max_calls:
                    budget["halt_reason"] = "Call limit reached"
                if not budget["halt_reason"] and budget["reported_cost_usd"] + CALL_RESERVE_USD > max_usd:
                    budget["halt_reason"] = "Insufficient remaining cost reserve"
                if budget["halt_reason"]:
                    report["reason"] = budget["halt_reason"]
                    continue
                if transport is None and client is None:
                    client = _http_client(timeout)
                budget["attempted_calls"] += 1
                budget["unpriced_attempts"] += 1
                report.update(status="in-flight", cost_state="unknown", reserved_usd=CALL_RESERVE_USD)
                _write(output / "run.json", run)
                body = deepcopy(request["body"])
                transport_started = time.perf_counter()
                try:
                    raw = (request_decisions(body, api_key=api_key, timeout=timeout, client=client)
                           if transport is None else transport(body, api_key=api_key, timeout=timeout))
                finally:
                    report["transport_seconds"] = time.perf_counter() - transport_started
                usage = raw.get("usage") if isinstance(raw, dict) else None
                if isinstance(usage, dict) and _number(usage.get("cost")):
                    budget["reported_cost_usd"] += usage["cost"]
                    budget["unpriced_attempts"] -= 1
                    report.update(cost_state="reported", reported_cost_usd=usage["cost"])
                    if usage["cost"] > CALL_RESERVE_USD or budget["reported_cost_usd"] > max_usd:
                        budget["halt_reason"] = "Reported cost exceeded conservative reserve"
                if len(_payload_bytes(raw)) > MAX_RESPONSE_BYTES:
                    raise ValueError("Response exceeds bounded size")
                _write(output / f"{request['id']}-response.json", raw, exclusive=True)
                report["response_sha256"] = digest(raw)
                report["answers"] = _validate_response(raw, request["body"])
                report.update(status="completed", model=raw["model"], usage=deepcopy(raw["usage"]))
            except BaseException as error:
                # Exceptions from injected transports and providers may contain
                # secrets or response bodies. Persist their class, never text.
                report.update(status="failed" if isinstance(error, Exception) else "interrupted",
                              error_type=type(error).__name__)
                if not isinstance(error, Exception):
                    raise
            finally:
                if report["cost_state"] == "unknown":
                    budget["halt_reason"] = "An attempted request has unknown cost"
                report["seconds"] = time.perf_counter() - started
                _write(output / "run.json", run)
        run["status"] = ("completed" if all(r["status"] == "completed" for r in run["measurements"])
                         else "completed-with-failures")
    except BaseException:
        run["status"] = "interrupted"
        raise
    finally:
        if client is not None:
            try:
                client.close()
            except Exception as error:
                run["transport_close_error_type"] = type(error).__name__
        run["completed_at"] = datetime.now(timezone.utc).isoformat()
        _write(output / "run.json", run)
    return run


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--allow-hosted", action="store_true")
    parser.add_argument("--max-calls", type=int, default=0)
    parser.add_argument("--max-usd", type=float, default=0.0)
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--stop-file", type=Path)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    args = parser.parse_args(argv)
    if not args.execute and (args.allow_hosted or args.max_calls or args.max_usd):
        parser.error("Hosted permission and budgets require --execute")
    if args.input.stat().st_size > 32 * 1024 * 1024:
        parser.error("Plan exceeds 32 MiB")
    run = prepare_run(json.loads(args.input.read_text(encoding="utf-8-sig")))
    if args.execute:
        if not args.allow_hosted:
            parser.error("Execution requires --allow-hosted")
        from dotenv import dotenv_values
        key = os.environ.get("OPENROUTER_API_KEY") or dotenv_values(args.env_file).get("OPENROUTER_API_KEY")
        execute_run(run, args.out, allow_hosted=True, max_calls=args.max_calls, max_usd=args.max_usd,
                    timeout=args.timeout, api_key=key, stop_file=args.stop_file)
    else:
        args.out.mkdir(parents=True, exist_ok=False)
        _write(args.out / "frozen-plan.json", run["plan"], exclusive=True)
        _write(args.out / "run.json", run, exclusive=True)
    print(json.dumps({"status": run["status"], "output": str(args.out),
                      "planned_calls": len(run["plan"]["requests"]), "budget": run.get("budget")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
