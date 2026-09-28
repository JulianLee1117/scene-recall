"""Small, provider-free single-strategy benchmark using the running API's models."""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from statistics import median
from time import perf_counter

STRATEGIES = ("normal", "fixed", "jev")
DEFAULT_CASES = ("booth_remembered_scoped", "medium_two_shot_noir", "lonely_neon", "matrix_screen_literal")


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def benchmark(query, db, config, *, intent, film_ids=(), result_limit=48, rounds=2,
              rotation=0, supplemental_budget=120):
    """Pin once, then measure independent requests without shared query memos.

    Models and OS/database caches remain warm. This intentionally has no outer
    search_execution decorator: each single-strategy call owns its own memo.
    """
    from pipeline.index.snapshot import acquire_search_snapshot
    from pipeline.search.intent_retrieval import execute_intent_search

    if type(rounds) is not int or not 1 <= rounds <= 2 or type(rotation) is not int or not 0 <= rotation <= 2:
        raise ValueError("Use one or two rounds and rotation 0, 1, or 2")
    started = perf_counter()
    snapshot = acquire_search_snapshot(config, db)
    snapshot_seconds = perf_counter() - started
    samples = []
    for round_index in range(rounds):
        offset = (rotation + round_index) % len(STRATEGIES)
        for strategy in STRATEGIES[offset:] + STRATEGIES[:offset]:
            # Bound subsequent stages; a native call already in flight finishes.
            if perf_counter() - started > 120:
                raise TimeoutError("Single-strategy benchmark exceeded its stage deadline")
            tick = perf_counter()
            result = execute_intent_search(query, snapshot, config, strategy=strategy,
                intent=intent, film_ids=film_ids, result_limit=result_limit,
                supplemental_budget=supplemental_budget)
            seconds = perf_counter() - tick
            ranked_sources = [{key: row.get(key) for key in
                ("unit_id", "film_id", "t_start", "t_end", "keyframe_index")} for row in result.results]
            samples.append({"round": round_index + 1, "strategy": strategy, "seconds": seconds,
                "result_count": len(result.results), "ranked_sources_sha256": digest(ranked_sources),
                "plan": asdict(result.plan), "diagnostics": result.diagnostics})
    return {"query": query, "film_ids": list(film_ids), "result_limit": result_limit,
        "snapshot_versions": dict(snapshot.versions), "snapshot_seconds": snapshot_seconds,
        "elapsed_seconds": perf_counter() - started, "samples": samples,
        "limits": "Warm loaded models; fresh per-strategy query memo; shared pinned library and OS/database caches. "
                  "Provider interpretation, response serialization and HTTP transfer excluded from strategy timings. "
                  "Two rounds are a diagnostic, not a latency distribution or relevance measurement."}


def load_cases(source: Path, identifiers):
    if not 1 <= len(identifiers) <= 4 or len(set(identifiers)) != len(identifiers):
        raise ValueError("Choose one to four unique saved cases")
    run = json.loads((source / "run.json").read_text(encoding="utf-8"))
    cases = []
    for identifier in identifiers:
        record = run["cases"][identifier]
        path = (source / record["file"]).resolve()
        if path.parent != source.resolve() or record["status"] != "completed":
            raise ValueError("Expected a completed in-directory comparison receipt")
        saved = json.loads(path.read_text(encoding="utf-8"))
        if digest(saved) != record["sha256"]:
            raise ValueError("Saved comparison receipt changed")
        from pipeline.search.intent import intent_from_dict
        frozen = saved["comparison"]["diagnostics"]["intent"]
        intent = intent_from_dict(frozen, query=saved["query"], film_ids=saved["film_ids"])
        cases.append({"id": identifier, "q": saved["query"], "film_ids": saved["film_ids"],
                      "intent": asdict(intent), "source_sha256": record["sha256"]})
    return cases


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path("pipeline/eval/runs/intent-retrieval-20260921"))
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--case", action="append", dest="cases")
    parser.add_argument("--api-base", default="http://127.0.0.1:8000")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    from pipeline.experiments.search_intent_review import loopback_base
    api = loopback_base(args.api_base)
    cases = load_cases(args.source, args.cases or DEFAULT_CASES)
    root = Path(__file__).resolve().parents[2]
    expected_code = {name: hashlib.sha256((root / "pipeline" / name).read_bytes()).hexdigest()
                     for name in ("search/intent.py", "search/intent_retrieval.py", "search/retrieve.py",
                                  "api/search_intent.py", "experiments/intent_latency.py")}
    document = {"version": 1, "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "prepared", "hosted_calls": 0, "provider_cost_usd": 0,
        "parameters": {"rounds": 2, "result_limit": 48, "supplemental_budget": 120},
        "cases": [], "prepared": cases, "expected_loaded_code_sha256": expected_code,
        "limits": "Four selected diagnostic queries; warm models/caches; no quality or p95 claims. "
                  "One pinned library per case, independent retrieval and query encoding for each strategy."}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("x", encoding="utf-8") as handle:
        def save():
            handle.seek(0)
            handle.write(json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False))
            handle.truncate()
            handle.flush()
        save()
        if not args.execute:
            print(json.dumps({"status": "prepared", "cases": len(cases), "hosted_calls": 0}))
            return
        import httpx
        started = perf_counter()
        document["status"] = "running"
        try:
            with httpx.Client(timeout=150, trust_env=False, follow_redirects=False) as client:
                for index, case in enumerate(cases):
                    if perf_counter() - started > 300:
                        raise TimeoutError("Five-minute benchmark admission budget exhausted")
                    payload = {key: case[key] for key in ("q", "film_ids", "intent")}
                    payload.update(limit=48, rounds=2, rotation=index % 3, supplemental_budget=120)
                    tick = perf_counter()
                    response = client.post(api + "/search/intent/benchmark", json=payload)
                    response.raise_for_status()
                    value = response.json()
                    samples = value.get("samples", [])
                    if (len(samples) != 6 or value.get("query") != case["q"]
                            or value.get("film_ids") != case["film_ids"]
                            or {(s.get("round"), s.get("strategy")) for s in samples}
                               != {(r, s) for r in (1, 2) for s in STRATEGIES}
                            or any(value.get("loaded_code_sha256", {}).get(name) != sha
                                   for name, sha in expected_code.items())):
                        raise ValueError("Incomplete or mismatched benchmark response")
                    document["cases"].append({"id": case["id"], "http_seconds": perf_counter() - tick,
                                             "benchmark": value})
                    save()
                    print(json.dumps({"case": case["id"], "seconds": {
                        strategy: round(median(s["seconds"] for s in value["samples"] if s["strategy"] == strategy), 3)
                        for strategy in STRATEGIES}}), flush=True)
            document["status"] = "completed"
        except Exception as exc:
            document.update(status="stopped", failure_type=type(exc).__name__)
            raise
        finally:
            document["elapsed_seconds"] = perf_counter() - started
            save()


if __name__ == "__main__":
    main()
