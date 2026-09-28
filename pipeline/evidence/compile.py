"""Compile evidence artifacts into search tables."""

from __future__ import annotations

import json
import math
from typing import Any, Callable

from pipeline.evidence import metadata, store, tables
from pipeline.evidence.library import FilmRef


def _percentiles(values: dict[str, float]) -> dict[str, float]:
    """Rank-based percentile in [0, 1]; ties share the average rank."""
    if not values:
        return {}
    ordered = sorted(values.items(), key=lambda item: item[1])
    result: dict[str, float] = {}
    index = 0
    while index < len(ordered):
        end = index
        while end + 1 < len(ordered) and ordered[end + 1][1] == ordered[index][1]:
            end += 1
        rank = (index + end) / 2
        for position in range(index, end + 1):
            result[ordered[position][0]] = rank / max(1, len(ordered) - 1)
        index = end + 1
    return result


def film_popularity(documents: dict[str, dict[str, Any]]) -> dict[str, float]:
    """Blend IMDb votes (long-run reach) with pageviews (current attention)."""
    votes = {film_id: math.log10(1 + (doc.get("popularity", {}).get("imdb_votes") or 0)) for film_id, doc in documents.items()}
    views = {film_id: math.log10(1 + (doc.get("popularity", {}).get("pageviews_12m") or 0)) for film_id, doc in documents.items()}
    vote_rank, view_rank = _percentiles(votes), _percentiles(views)
    return {film_id: round(0.75 * vote_rank.get(film_id, 0.0) + 0.25 * view_rank.get(film_id, 0.0), 4) for film_id in documents}


def compile_film_meta(config: Any, db: Any, films: list[FilmRef], progress: Callable[[str], None] = print) -> int:
    documents: dict[str, dict[str, Any]] = {}
    for film in films:
        artifact = store.read_artifact(config.paths.assets_dir, film.film_id, metadata.PRODUCER)
        if artifact is not None:
            documents[film.film_id] = artifact["data"]
    popularity = film_popularity(documents)
    rows = []
    for film in films:
        doc = documents.get(film.film_id, {})
        wiki = doc.get("wikidata", {})
        pop = doc.get("popularity", {})
        rows.append({
            "schema_version": tables.TABLE_SCHEMA_VERSION,
            "film_id": film.film_id,
            "title": film.title,
            "name": wiki.get("label") or film.name,
            "year": film.year,
            "wikidata_id": wiki.get("id"),
            "imdb_id": wiki.get("imdb_id"),
            "wikipedia_title": doc.get("wikipedia", {}).get("title"),
            "directors": json.dumps(wiki.get("directors", []), ensure_ascii=False),
            "genres": json.dumps(wiki.get("genres", []), ensure_ascii=False),
            "countries": json.dumps(wiki.get("countries", []), ensure_ascii=False),
            "languages": json.dumps(wiki.get("languages", []), ensure_ascii=False),
            "cast": json.dumps([{"actor": row["actor"], "characters": row["characters"]} for row in wiki.get("cast", [])], ensure_ascii=False),
            "plot": doc.get("wikipedia", {}).get("plot", ""),
            "quote_count": len(doc.get("wikiquote", {}).get("quotes", [])),
            "imdb_rating": pop.get("imdb_rating"),
            "imdb_votes": pop.get("imdb_votes"),
            "pageviews_12m": pop.get("pageviews_12m"),
            "popularity": popularity.get(film.film_id),
            "metadata_profile": metadata.PRODUCER.profile_id if film.film_id in documents else None,
        })
    tables.replace_all(db, tables.FILM_META, tables.film_meta_schema(), "film_id", rows)
    progress(f"[compile] film_meta: {len(rows)} films ({len(documents)} with metadata)")
    return len(rows)
