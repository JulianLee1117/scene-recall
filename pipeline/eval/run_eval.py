"""run_eval.py — Evaluation harness for cinema semantic search.

Reads a gold_queries.yaml file, calls ``search()`` for each query, and
measures hit@5 and hit@10 — whether any acceptable answer appears in the
top 5 or top 10 results (with a 5-second timestamp tolerance).

Queries where ALL acceptable answers have ``film_id == "REPLACE_WITH_FILM_ID"``
are skipped with a warning, since those placeholders have not been filled in
after film ingestion.

Usage (standalone)::

    python -m pipeline.eval.run_eval --queries pipeline/eval/gold_queries.yaml

Or via the CLI::

    python -m pipeline.cli eval [--queries pipeline/eval/gold_queries.yaml]
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

import yaml

import lancedb

from pipeline.config import Config, load_config
from pipeline.index.writer import open_db
from pipeline.search.retrieve import search

_PLACEHOLDER = "REPLACE_WITH_FILM_ID"
_DEFAULT_QUERIES = Path(__file__).parent / "gold_queries.yaml"


# ---------------------------------------------------------------------------
# Core evaluation helpers
# ---------------------------------------------------------------------------


def is_hit(result: dict, acceptable: dict, tolerance: float = 5.0) -> bool:
    """Return True if *result* matches *acceptable* within timestamp tolerance.

    Parameters
    ----------
    result:
        A search result dict with ``film_id``, ``t_start``, ``t_end``.
    acceptable:
        An acceptable answer dict with ``film_id``, ``t_start``, ``t_end``.
    tolerance:
        Number of seconds of leeway on each timestamp bound (default 5 s).
    """
    if result["film_id"] != acceptable["film_id"]:
        return False
    if result["t_start"] < acceptable["t_start"] - tolerance:
        return False
    if result["t_end"] > acceptable["t_end"] + tolerance:
        return False
    return True


def hit_at_k(
    results: list[dict],
    acceptable: list[dict],
    k: int,
    tolerance: float = 5.0,
) -> bool:
    """Return True if any of *acceptable* appears in the first *k* of *results*.

    Parameters
    ----------
    results:
        Ranked list of search result dicts (in descending relevance order).
    acceptable:
        List of acceptable answer dicts for this query.
    k:
        Rank cut-off to check.
    tolerance:
        Timestamp tolerance forwarded to :func:`is_hit`.
    """
    for result in results[:k]:
        for acc in acceptable:
            if is_hit(result, acc, tolerance=tolerance):
                return True
    return False


def is_placeholder_query(query: dict) -> bool:
    """Return True if ALL acceptable answers still use the placeholder film_id."""
    return all(a["film_id"] == _PLACEHOLDER for a in query.get("acceptable", []))


# ---------------------------------------------------------------------------
# Main evaluation loop
# ---------------------------------------------------------------------------


def run_eval_queries(
    queries: list[dict],
    db: lancedb.DBConnection,
    config: Config,
    k5: int = 5,
    k10: int = 10,
) -> dict:
    """Evaluate *queries* against the indexed DB and return aggregate metrics.

    Parameters
    ----------
    queries:
        List of query dicts loaded from gold_queries.yaml.
    db:
        Open LanceDB connection.
    config:
        Pipeline configuration.
    k5, k10:
        Rank cut-offs for hit@5 and hit@10 (defaults: 5 and 10).

    Returns
    -------
    dict
        Keys: ``evaluated``, ``skipped``, ``hit@5``, ``hit@10``.
        Hit rates are ``None`` when ``evaluated == 0``.
    """
    evaluated = 0
    skipped = 0
    hits5 = 0
    hits10 = 0

    for q in queries:
        qid = q.get("id", "?")
        query_text = q["query"]
        acceptable = q.get("acceptable", [])

        if is_placeholder_query(q):
            print(f"[SKIP] {qid}: all acceptable answers are placeholders", file=sys.stderr)
            skipped += 1
            continue

        results = search(query_text, db, config)
        h5 = hit_at_k(results, acceptable, k=k5)
        h10 = hit_at_k(results, acceptable, k=k10)

        hits5 += int(h5)
        hits10 += int(h10)
        evaluated += 1

        status5 = "HIT" if h5 else "MISS"
        status10 = "HIT" if h10 else "MISS"
        print(f"[{qid}] hit@{k5}={status5}  hit@{k10}={status10}  query={query_text!r}")

    if evaluated == 0:
        return {
            "evaluated": 0,
            "skipped": skipped,
            "hit@5": None,
            "hit@10": None,
        }

    return {
        "evaluated": evaluated,
        "skipped": skipped,
        "hit@5": hits5 / evaluated,
        "hit@10": hits10 / evaluated,
    }


# ---------------------------------------------------------------------------
# CLI entry-point
# ---------------------------------------------------------------------------


def main(queries_path: Optional[Path] = None) -> None:
    """Load queries and run evaluation, printing results to stdout."""
    path = queries_path or _DEFAULT_QUERIES

    if not path.exists():
        print(f"ERROR: queries file not found: {path}", file=sys.stderr)
        sys.exit(1)

    with path.open("r", encoding="utf-8") as fh:
        queries: list[dict] = yaml.safe_load(fh) or []

    config = load_config()
    db = open_db(config)

    metrics = run_eval_queries(queries, db, config)

    print()
    print("=" * 50)
    print(f"Evaluated : {metrics['evaluated']}")
    print(f"Skipped   : {metrics['skipped']}")
    if metrics["hit@5"] is not None:
        print(f"hit@5     : {metrics['hit@5']:.1%}")
        print(f"hit@10    : {metrics['hit@10']:.1%}")
    else:
        print("hit@5     : N/A (no evaluated queries)")
        print("hit@10    : N/A (no evaluated queries)")
    print("=" * 50)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Run eval harness")
    parser.add_argument(
        "--queries",
        type=Path,
        default=None,
        help="Path to gold_queries.yaml (default: pipeline/eval/gold_queries.yaml)",
    )
    args = parser.parse_args()
    main(args.queries)
