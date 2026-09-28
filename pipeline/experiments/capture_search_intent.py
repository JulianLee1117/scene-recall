"""Freeze sequential local search responses without loading models or writing indexes."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import time

import httpx
import lancedb
import yaml

from pipeline.experiments.search_intent_review import loopback_base

ROOT = Path(__file__).resolve().parents[2]


def versions(db_path: Path) -> dict:
    # Do not use the application database constructor: it initializes schemas.
    if not db_path.is_dir():
        raise ValueError("Configured existing database is unavailable")
    db = lancedb.connect(str(db_path))
    result = {name: db.open_table(name).version for name in sorted(db.list_tables().tables)}
    if not result:
        raise ValueError("Configured database contains no tables")
    return result


def _cases(fixture: dict) -> list[dict]:
    cases = fixture.get("queries", fixture.get("cases", [])) if isinstance(fixture, dict) else []
    if not isinstance(cases, list) or not 1 <= len(cases) <= 30:
        raise ValueError("Expected one to thirty frozen queries")
    seen = set()
    for case in cases:
        if (not isinstance(case, dict) or not isinstance(case.get("id"), str)
                or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", case["id"]) or case["id"] in seen):
            raise ValueError("Case IDs must be unique bounded identifiers")
        seen.add(case["id"])
        scope, query = case.get("film_ids") or [], case.get("query")
        if (not isinstance(query, str) or not query.strip() or len(query) > 500
                or not isinstance(scope, list) or any(not isinstance(s, str) or not s for s in scope)
                or len(set(scope)) != len(scope)):
            raise ValueError("Each case needs a 1–500 character query and unique explicit film IDs")
    return cases


def capture(queries: Path, out: Path, config: Path, api_base="http://127.0.0.1:8000") -> dict:
    api = loopback_base(api_base)
    fixture_bytes, config_bytes = queries.read_bytes(), config.read_bytes()
    fixture, settings = yaml.safe_load(fixture_bytes), yaml.safe_load(config_bytes)
    cases = _cases(fixture)
    db_path = Path(settings["paths"]["assets_dir"]) / "db"
    if not db_path.is_dir():
        raise ValueError("Configured existing database is unavailable")
    document = {
        "version": 1, "capture_status": "partial", "expected_case_count": len(cases),
        "captured_case_count": 0, "cases": [], "partial_cases": [],
        "provenance": {
            "created_at": datetime.now(timezone.utc).isoformat(), "capture_endpoint": api + "/search",
            "fixture_sha256": hashlib.sha256(fixture_bytes).hexdigest(),
            "config_file_sha256": hashlib.sha256(config_bytes).hexdigest(),
            "on_disk_models": settings["models"], "on_disk_retrieval": settings["retrieval"],
            "on_disk_search_code_sha256": {
                name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in (
                    "pipeline/search/retrieve.py", "pipeline/search/recipe.py", "pipeline/ingest/text_embed.py")},
            "limits": "The running API may have loaded older code/config. On-disk hashes are contextual, not certification of the loaded process. Exact response evidence is frozen for all four replays. External table versions bracket each response but do not expose the API internal snapshot.",
            "capture_policy": "One sequential existing-API request per query, limit200, no retries. No GPU model duplication or index mutation. Unequal before/after versions stop capture.",
        },
        "fixture_metadata": {k: v for k, v in fixture.items() if k not in ("queries", "cases")},
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("x", encoding="utf-8") as handle:
        def save():
            encoded = json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False)
            handle.seek(0)
            handle.write(encoded)
            handle.truncate()
            handle.flush()
        save()
        active_case = None
        try:
            with httpx.Client(timeout=httpx.Timeout(60.0), follow_redirects=False, trust_env=False) as client:
                for case in cases:
                    active_case = case["id"]
                    scope = list(case.get("film_ids") or [])
                    params = [("q", case["query"]), ("limit", "200")] + [("film_id", s) for s in scope]
                    before, start = versions(db_path), time.perf_counter()
                    response = client.get(api + "/search", params=params)
                    seconds = time.perf_counter() - start
                    response.raise_for_status()
                    data, after = response.json(), versions(db_path)
                    if before != after:
                        raise ValueError("Index versions changed during capture; unstable case excluded")
                    rows = data.get("results") if isinstance(data, dict) else None
                    if (not isinstance(rows, list) or len(rows) > 200
                            or any(not isinstance(row, dict) or not isinstance(row.get("film_id"), str)
                                   or not row["film_id"] for row in rows)):
                        raise ValueError("Search response needs at most 200 source-backed results")
                    if scope and any(row["film_id"] not in scope for row in rows):
                        raise ValueError("A scoped response escaped explicit film scope")
                    json.dumps(data, allow_nan=False)
                    document["partial_cases"].append({**case, "film_ids": scope, "snapshot_before": before,
                        "snapshot_after": after, "capture_seconds": seconds, "candidates": rows,
                        "response_envelope": {k: v for k, v in data.items() if k != "results"}})
                    document["captured_case_count"] = len(document["partial_cases"])
                    save()
                    print(json.dumps({"case": case["id"], "count": len(rows), "captured": document["captured_case_count"]}), flush=True)
        except BaseException as exc:
            document["failure"] = {"case_id": active_case,
                                   "type": type(exc).__name__, "message": str(exc)}
            save()
            raise
        # Partial records cannot accidentally pass the existing runner's nonempty-cases gate.
        document["cases"] = document.pop("partial_cases")
        document["capture_status"] = "complete"
        save()
    return document


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queries", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True, help="New JSON path; never overwrite or resume")
    parser.add_argument("--config", type=Path, default=Path("config.yaml"))
    parser.add_argument("--api-base", default="http://127.0.0.1:8000")
    args = parser.parse_args(argv)
    result = capture(args.queries, args.out, args.config, args.api_base)
    print(json.dumps({"output": str(args.out), "sha256": hashlib.sha256(args.out.read_bytes()).hexdigest(),
                      "cases": len(result["cases"]), "capture_status": result["capture_status"]}), flush=True)


if __name__ == "__main__":
    main()
