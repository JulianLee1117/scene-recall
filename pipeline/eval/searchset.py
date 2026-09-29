"""Personal search eval: known-item ranks plus a reviewable top list for every query.

    python -m pipeline.eval.searchset [--preset balanced] [--film "The Matrix" ...] [--out report.json]

Queries live in ``searchset.yaml``. A known-item query names a film and either
a time (``at``, seconds or ``H:MM:SS``) or a subtitle line (``line``) that is
resolved against the film's current dialogue. A result hits when its shot is
within ``tolerance`` seconds (default 15) of the target. Discovery queries have
no target; the report keeps their top results, badges and scene titles for a
side-by-side read. Reports go to ``pipeline/eval/runs/`` (git-ignored).
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

SET_PATH = Path(__file__).with_name("searchset.yaml")
RUNS = Path(__file__).with_name("runs")


def _seconds(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    total = 0.0
    for part in str(value).split(":"):
        total = total * 60 + float(part)
    return total


def _resolve_line(config: Any, film_id: str, line: str) -> float | None:
    from pipeline.evidence.subtitles import current_dialogue
    from pipeline.evidence.textnorm import tokens
    from pipeline.search.quotes import score_window

    target = tokens(line)
    best = (0.0, None)
    for row in current_dialogue(config, film_id):
        score = score_window(target, tokens(row["text"]))
        if score > best[0]:
            best = (score, (float(row["start"]) + float(row["end"])) / 2)
    return best[1] if best[0] >= 0.6 else None


def _hit(result: dict[str, Any], film_id: str, at: float, tolerance: float) -> bool:
    if result["film_id"] != film_id:
        return False
    return float(result["t_start"]) - tolerance <= at <= float(result["t_end"]) + tolerance


def _watch_degradation() -> dict[str, int]:
    """Count queries that silently lost the judge or the semantic text channel.

    Both fall back quietly when the GPU is busy (another process decoding or
    encoding), which is right for serving and wrong for measuring: such a run
    does not compare with others, so the report says how many were affected.
    """
    import logging

    from pipeline.search import rerank as reranker

    counts = {"judge_skipped": 0, "text_fallback": 0}
    score = reranker.score

    def counted(query: str, documents: list[str], **kwargs: Any) -> list[float] | None:
        verdicts = score(query, documents, **kwargs)
        if verdicts is None and documents:
            counts["judge_skipped"] += 1
        return verdicts

    class Fallbacks(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            if "Semantic text profile" in record.getMessage():
                counts["text_fallback"] += 1

    reranker.score = counted
    logging.getLogger("pipeline.search.retrieve").addHandler(Fallbacks(level=logging.WARNING))
    return counts


def run(preset: str, films: list[str] | None, out: Path | None, rerank: int | None = None) -> dict[str, Any]:
    from pipeline.config import load_config
    from pipeline.evidence.library import resolve_films
    from pipeline.index.writer import open_db
    from pipeline.search.retrieve import search

    config = load_config()
    if rerank is not None:
        config.retrieval.rerank_shortlist = rerank
    # This measures ranking quality: the reranker's serving time budget (which yields to a busy
    # GPU or CPU) would make results depend on whatever else the machine is doing.
    from pipeline.search import rerank as reranker
    reranker.set_budget(None)
    degraded = _watch_degradation()
    db = open_db(config)
    spec = yaml.safe_load(SET_PATH.read_text(encoding="utf-8"))
    titles = {film.film_id: film.title for film in resolve_films(db, None)}
    report: dict[str, Any] = {"preset": preset, "rerank": config.retrieval.rerank_shortlist, "created": datetime.now().isoformat(timespec="seconds"), "queries": []}
    ranks: list[int | None] = []
    for item in spec["queries"]:
        target_film = item.get("film")
        if films and target_film and not any(name.casefold() in target_film.casefold() for name in films):
            continue
        entry: dict[str, Any] = {"query": item["query"], "kind": item.get("kind", "known" if target_film else "discovery")}
        film_id = at = None
        if target_film:
            matches = resolve_films(db, [target_film])
            if not matches:
                entry["error"] = f"film not found: {target_film}"
                report["queries"].append(entry)
                continue
            film_id = matches[0].film_id
            if "at" in item:
                targets = [_seconds(value) for value in (item["at"] if isinstance(item["at"], list) else [item["at"]])]
            else:
                resolved = _resolve_line(config, film_id, item["line"])
                targets = [resolved] if resolved is not None else []
            at = targets or None
            entry.update(film=matches[0].title, target=targets)
        started = time.perf_counter()
        results = search(item["query"], db, config, result_limit=48, preset=preset)
        entry["seconds"] = round(time.perf_counter() - started, 2)
        if film_id is not None and at is not None:
            tolerance = float(item.get("tolerance", 15))
            rank = next((index for index, result in enumerate(results, start=1)
                         if any(_hit(result, film_id, target, tolerance)
                                or any(_hit({**alt, "film_id": result["film_id"]}, film_id, target, tolerance)
                                       for alt in result.get("scene_alternatives") or [])
                                for target in at)), None)
            entry["rank"] = rank
            ranks.append(rank)
        entry["top"] = [{
            "film": titles.get(result["film_id"], result["film_id"])[:40],
            "t": round(float(result["t_start"]), 1),
            "badges": result.get("badges", []),
            "scene": (result.get("scene") or {}).get("title", ""),
            "text": (result.get("action") or result.get("caption") or "")[:120],
            "line": (result.get("matched_line") or {}).get("text", "")[:80],
            "hero": "hero_url" in result,
            "alternatives": len(result.get("scene_alternatives") or []),
        } for result in results[:12]]
        report["queries"].append(entry)
    known = ranks
    report["summary"] = {
        "known_items": len(known),
        "mrr": round(statistics.fmean([1 / rank if rank else 0.0 for rank in known]), 3) if known else None,
        "hit@1": sum(1 for rank in known if rank and rank <= 1),
        "hit@5": sum(1 for rank in known if rank and rank <= 5),
        "hit@12": sum(1 for rank in known if rank and rank <= 12),
        "missed": sum(1 for rank in known if not rank),
        "median_seconds": round(statistics.median(q["seconds"] for q in report["queries"] if "seconds" in q), 2),
        **degraded,
    }
    destination = out or RUNS / f"searchset-{preset}-{datetime.now():%Y%m%d-%H%M%S}.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")
    return report


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--preset", default="balanced", choices=["balanced", "famous", "gems"])
    parser.add_argument("--film", action="append", help="only known-item queries for these films (discovery always runs)")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--rerank", type=int, default=None, help="override retrieval.rerank_shortlist (0 disables)")
    args = parser.parse_args()
    report = run(args.preset, args.film, args.out, args.rerank)
    for entry in report["queries"]:
        rank = entry.get("rank", "-") if entry["kind"] != "discovery" else " "
        print(f"{str(rank or 'MISS'):>5}  {entry['query'][:60]:60s} {entry.get('seconds', '')}s")
    print(json.dumps(report["summary"]))
    if report["summary"]["judge_skipped"] or report["summary"]["text_fallback"]:
        print(f"WARNING: {report['summary']['judge_skipped']} queries ran without the judge and "
              f"{report['summary']['text_fallback']} without semantic text (a busy GPU?); "
              "this run does not compare with others. Rerun on an idle GPU.")


if __name__ == "__main__":
    main()
