"""Create and score human review sets for real-film search.

This workflow intentionally separates candidate generation from judgment:

1. ``pool`` runs the current local search and freezes its top results.
2. A person fills in ``grade`` (and optional ``flags``/``note``) in YAML.
3. ``score`` reports ranked-retrieval metrics for fully reviewed queries.

No paid annotation API is called, and no relevance judgment is inferred from
captions or timestamps.

Examples::

    python -m pipeline.eval.review pool
    python -m pipeline.eval.review score pipeline/eval/fallen_angels_review.yaml
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from datetime import datetime, timezone
from pathlib import Path
from statistics import fmean
from typing import Any

import yaml

from pipeline.config import Config, load_config
from pipeline.index.writer import open_db
from pipeline.search.retrieve import search


DEFAULT_QUERIES = Path(__file__).parent / "fallen_angels_queries.yaml"
DEFAULT_REVIEW = Path(__file__).parent / "fallen_angels_review.yaml"
VALID_GRADES = frozenset({0, 1, 2, 3})
RELEVANT_GRADE = 2

SearchFunction = Callable[..., list[dict[str, Any]]]


def _read_yaml(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def _write_yaml(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        yaml.safe_dump(
            value,
            handle,
            allow_unicode=True,
            sort_keys=False,
            width=100,
        )


def load_query_set(path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Load either a query-list file or a versioned ``queries`` document."""
    document = _read_yaml(path) or {}
    if isinstance(document, list):
        queries = document
        metadata: dict[str, Any] = {}
    elif isinstance(document, dict):
        queries = document.get("queries", [])
        metadata = {
            key: value for key, value in document.items() if key != "queries"
        }
    else:
        raise ValueError("query file must contain a list or a mapping")

    if not isinstance(queries, list) or not queries:
        raise ValueError("query file contains no queries")

    seen: set[str] = set()
    for index, query in enumerate(queries, start=1):
        if not isinstance(query, dict):
            raise ValueError(f"query #{index} must be a mapping")
        missing = {"id", "category", "query"} - query.keys()
        if missing:
            raise ValueError(
                f"query #{index} is missing: {', '.join(sorted(missing))}"
            )
        query_id = str(query["id"])
        if query_id in seen:
            raise ValueError(f"duplicate query id: {query_id}")
        seen.add(query_id)
    return queries, metadata


def format_timecode(seconds: float) -> str:
    """Format seconds as a compact, sortable ``HH:MM:SS.s`` timecode."""
    total_tenths = max(0, round(float(seconds) * 10))
    whole_seconds, tenths = divmod(total_tenths, 10)
    minutes, second = divmod(whole_seconds, 60)
    hours, minute = divmod(minutes, 60)
    return f"{hours:02d}:{minute:02d}:{second:02d}.{tenths}"


def candidate_for_review(result: dict[str, Any], rank: int) -> dict[str, Any]:
    """Keep review evidence and add blank human-owned judgment fields."""
    t_start = float(result["t_start"])
    t_end = float(result["t_end"])
    return {
        "rank": rank,
        "unit_id": str(result["unit_id"]),
        "film_id": str(result["film_id"]),
        "t_start": t_start,
        "t_end": t_end,
        "timecode": f"{format_timecode(t_start)}–{format_timecode(t_end)}",
        "caption": str(result.get("caption") or ""),
        "keyframe_url": result.get("keyframe_url"),
        "preview_url": result.get("preview_url"),
        "matched_frame_index": result.get("matched_frame_index"),
        "matched_frame_timestamp": result.get("matched_frame_timestamp"),
        "grade": None,
        "flags": [],
        "note": "",
    }


def build_review_document(
    queries: Sequence[dict[str, Any]],
    db: Any,
    config: Config,
    *,
    source_metadata: dict[str, Any] | None = None,
    film_ids: Sequence[str] | None = None,
    limit: int = 12,
    search_fn: SearchFunction = search,
    created_at: str | None = None,
) -> dict[str, Any]:
    """Pool current results and return an unjudged human-review document."""
    if limit <= 0:
        raise ValueError("limit must be positive")

    review_queries: list[dict[str, Any]] = []
    for query in queries:
        if film_ids is None:
            results = search_fn(str(query["query"]), db, config)[:limit]
        else:
            results = search_fn(
                str(query["query"]),
                db,
                config,
                film_ids=film_ids,
            )[:limit]
        review_queries.append(
            {
                "id": str(query["id"]),
                "category": str(query["category"]),
                "query": str(query["query"]),
                "candidates": [
                    candidate_for_review(result, rank)
                    for rank, result in enumerate(results, start=1)
                ],
            }
        )

    return {
        "schema_version": 1,
        "created_at": created_at
        or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "generator": "pipeline.eval.review",
        "source": source_metadata or {},
        "judging": {
            "grade_scale": {
                0: "irrelevant",
                1: "weak or partial match",
                2: "relevant",
                3: "ideal match",
            },
            "relevant_grade": RELEVANT_GRADE,
            "allowed_flags": ["junk", "duplicate", "wrong_moment"],
            "instructions": [
                "Judge the visible scene, not whether the caption contains query words.",
                "Fill every grade for a query before that query is included in metrics.",
                "Use flags for diagnostics; flags do not replace the relevance grade.",
                "Do not reorder, add, or remove candidates after judging begins.",
            ],
        },
        "queries": review_queries,
    }


def _validated_grade(candidate: dict[str, Any]) -> int | None:
    grade = candidate.get("grade")
    if grade is None or grade == "":
        return None
    if isinstance(grade, bool) or not isinstance(grade, int):
        raise ValueError(
            f"grade for {candidate.get('unit_id', '?')} must be an integer 0–3"
        )
    if grade not in VALID_GRADES:
        raise ValueError(
            f"grade for {candidate.get('unit_id', '?')} must be 0, 1, 2, or 3"
        )
    return grade


def _dcg(grades: Sequence[int], k: int) -> float:
    return sum(
        (2**grade - 1) / math.log2(rank + 1)
        for rank, grade in enumerate(grades[:k], start=1)
    )


def metrics_for_grades(grades: Sequence[int], k: int = 10) -> dict[str, float]:
    """Compute top-k ranking metrics for one fully judged result list."""
    if k <= 0:
        raise ValueError("k must be positive")
    top = list(grades[:k])
    if not top:
        return {
            f"precision@{k}": 0.0,
            f"success@{k}": 0.0,
            f"ndcg@{k}": 0.0,
            "mrr": 0.0,
        }

    relevant = [grade >= RELEVANT_GRADE for grade in top]
    ideal = sorted(grades, reverse=True)
    ideal_dcg = _dcg(ideal, k)
    first_relevant = next(
        (rank for rank, is_relevant in enumerate(relevant, start=1) if is_relevant),
        None,
    )
    return {
        f"precision@{k}": sum(relevant) / len(top),
        f"success@{k}": float(any(relevant)),
        f"ndcg@{k}": _dcg(grades, k) / ideal_dcg if ideal_dcg else 0.0,
        "mrr": 1.0 / first_relevant if first_relevant else 0.0,
    }


def _flag_rate(
    candidates: Sequence[dict[str, Any]],
    flag: str,
    k: int,
) -> float:
    top = candidates[:k]
    if not top:
        return 0.0
    return sum(flag in (candidate.get("flags") or []) for candidate in top) / len(top)


def score_review_document(
    document: dict[str, Any],
    *,
    k: int = 10,
) -> dict[str, Any]:
    """Score only queries whose candidate grades are all filled.

    This deliberately does not report corpus recall: the review file contains
    a candidate pool, not exhaustive judgments over every shot in the film.
    """
    query_rows: list[dict[str, Any]] = []
    incomplete: list[str] = []
    category_rows: dict[str, list[dict[str, float]]] = defaultdict(list)

    queries = document.get("queries", [])
    for query in queries:
        candidates = query.get("candidates", [])
        grades = [_validated_grade(candidate) for candidate in candidates]
        if not candidates or any(grade is None for grade in grades):
            incomplete.append(str(query.get("id", "?")))
            continue

        complete_grades = [int(grade) for grade in grades if grade is not None]
        metrics = metrics_for_grades(complete_grades, k=k)
        metrics[f"junk@{k}"] = _flag_rate(candidates, "junk", k)
        metrics[f"duplicate@{k}"] = _flag_rate(candidates, "duplicate", k)
        row = {
            "id": str(query["id"]),
            "category": str(query["category"]),
            "query": str(query["query"]),
            **metrics,
        }
        query_rows.append(row)
        category_rows[row["category"]].append(metrics)

    metric_names = (
        f"precision@{k}",
        f"success@{k}",
        f"ndcg@{k}",
        "mrr",
        f"junk@{k}",
        f"duplicate@{k}",
    )

    def aggregate(rows: Iterable[dict[str, float]]) -> dict[str, float]:
        materialized = list(rows)
        return {
            name: fmean(row[name] for row in materialized)
            for name in metric_names
        }

    by_category = {
        category: {
            "evaluated": len(rows),
            **aggregate(rows),
        }
        for category, rows in sorted(category_rows.items())
    }
    overall = aggregate(query_rows) if query_rows else None
    metric_note = (
        "Top-k quality within this frozen candidate pool; corpus recall is not "
        "measured."
        if query_rows
        else "Relevance unavailable: no query has a complete human judgment set."
    )

    return {
        "queries_total": len(queries),
        "queries_evaluated": len(query_rows),
        "queries_incomplete": len(incomplete),
        "incomplete_ids": incomplete,
        "metric_note": metric_note,
        "overall": overall,
        "by_category": by_category,
        "queries": query_rows,
    }


def pool_command(
    queries_path: Path,
    output_path: Path,
    *,
    limit: int,
    force: bool,
) -> None:
    if output_path.exists() and not force:
        raise FileExistsError(
            f"{output_path} already exists; use --force only if it has no judgments "
            "you need to preserve"
        )
    queries, metadata = load_query_set(queries_path)
    scope = metadata.get("scope") or {}
    if not isinstance(scope, dict):
        raise ValueError("query metadata scope must be a mapping")
    raw_film_ids = scope.get("film_ids")
    film_ids: list[str] | None = None
    if raw_film_ids is not None:
        if not isinstance(raw_film_ids, list) or not raw_film_ids or not all(
            isinstance(film_id, str) and film_id.strip()
            for film_id in raw_film_ids
        ):
            raise ValueError("scope.film_ids must contain non-empty film IDs")
        film_ids = [film_id.strip() for film_id in raw_film_ids]
    config = load_config()
    db = open_db(config)
    document = build_review_document(
        queries,
        db,
        config,
        source_metadata={
            "queries_file": str(queries_path),
            **metadata,
        },
        film_ids=film_ids,
        limit=limit,
    )
    _write_yaml(output_path, document)
    print(f"Wrote {len(queries)} unjudged queries to {output_path}")
    print("Next: fill each candidate grade with 0, 1, 2, or 3.")


def score_command(review_path: Path, output_path: Path | None, *, k: int) -> None:
    document = _read_yaml(review_path) or {}
    metrics = score_review_document(document, k=k)
    rendered = json.dumps(metrics, indent=2, ensure_ascii=False)
    if output_path is None:
        print(rendered)
        return
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(rendered + "\n", encoding="utf-8")
    print(f"Wrote metrics to {output_path}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Create and score a human review set without paid APIs."
    )
    commands = parser.add_subparsers(dest="command", required=True)

    pool = commands.add_parser("pool", help="freeze current search candidates")
    pool.add_argument("--queries", type=Path, default=DEFAULT_QUERIES)
    pool.add_argument("--output", type=Path, default=DEFAULT_REVIEW)
    pool.add_argument("--limit", type=int, default=12)
    pool.add_argument(
        "--force",
        action="store_true",
        help="overwrite an existing review file, including any judgments",
    )

    score = commands.add_parser("score", help="score completed human judgments")
    score.add_argument("review", type=Path, nargs="?", default=DEFAULT_REVIEW)
    score.add_argument("--output", type=Path, default=None)
    score.add_argument("--k", type=int, default=10)

    args = parser.parse_args(argv)
    try:
        if args.command == "pool":
            pool_command(
                args.queries,
                args.output,
                limit=args.limit,
                force=args.force,
            )
        else:
            score_command(args.review, args.output, k=args.k)
    except (FileExistsError, FileNotFoundError, ValueError, yaml.YAMLError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
