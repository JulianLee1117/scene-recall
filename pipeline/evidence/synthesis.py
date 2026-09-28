"""Per-shot priors: calibrated fame, craft, distinctiveness, iconic and hidden-gem flags.

Two separate axes (docs/current-work.md, "Balancing famous and forgotten"):

* fame — how widely known the moment is. Within-film fame comes from the
  understanding pass's 0-3 rating, its explicit iconic picks and famous quotes
  aligned to the subtitles; library fame scales it by the film's popularity, so
  a famous shot from an obscure film can still be a hidden gem library-wide.
* craft — visual and editorial strength regardless of fame: the model's 0-3
  rating (1 is ordinary coverage) refined by measured sharpness and exposure.

Flags are deliberately rare so they mean something as badges:

* iconic — the understanding pass's explicit iconic picks, or a shot carrying a
  famous quote (Wikiquote) that the model also rated as memorable;
* gem — per film, the top few percent by craft × (1 − library fame) ×
  distinctiveness among well-crafted, little-known shots. Low fame alone is not
  a gem: most little-known shots are ordinary coverage.
"""

from __future__ import annotations

import math
from typing import Any, Callable

import numpy as np

from pipeline.evidence import store
from pipeline.evidence.library import FilmRef, film_units
from pipeline.evidence.textnorm import ordered_match, tokens


FAME_LEVELS = (0.0, 0.2, 0.5, 0.8)       # model fame 0..3
CRAFT_LEVELS = (0.0, 0.3, 0.6, 0.9)      # model craft 0..3 (1 = ordinary)
PRODUCER = store.Producer(
    kind="synthesis",
    name="priors",
    version=2,
    settings={"fame_levels": FAME_LEVELS, "craft_levels": CRAFT_LEVELS, "iconic_pick_bonus": 0.2,
              "quote_bonus": 0.15, "quote_min_score": 0.72, "iconic_quote_score": 0.85, "craft_model_weight": 0.8,
              "gem_share": 0.03, "gem_min_craft_level": 2, "gem_max_fame_level": 1, "neighbors": 10,
              "library_fame": "fame*(0.35+0.65*popularity)", "highlights": 24},
)


def align_quotes(quotes: list[str], dialogue: list[dict[str, Any]], *, min_score: float) -> list[dict[str, Any]]:
    """Locate famous quotes in timed dialogue (windows of up to three cues)."""
    lines = [(float(row["start"]), float(row["end"]), tokens(str(row["text"]))) for row in dialogue]
    matches = []
    for quote in quotes:
        target = tokens(quote)
        if len(target) < 4:
            continue
        best: tuple[float, tuple[float, float] | None] = (0.0, None)
        for index in range(len(lines)):
            window: list[str] = []
            for width in range(3):
                if index + width >= len(lines):
                    break
                window = window + lines[index + width][2]
                score = ordered_match(target, window)
                if score > best[0]:
                    best = (score, (lines[index][0], lines[index + width][1]))
        if best[0] >= min_score and best[1] is not None:
            matches.append({"quote": quote, "start": best[1][0], "end": best[1][1], "score": round(best[0], 3)})
    return matches


def distinctiveness(vectors: np.ndarray, neighbors: int) -> np.ndarray:
    """Within-film percentile of dissimilarity to each shot's nearest visual neighbours (1 = most unusual)."""
    if len(vectors) < 3:
        return np.full(len(vectors), 0.5, dtype=np.float32)
    unit = vectors / np.maximum(np.linalg.norm(vectors, axis=1, keepdims=True), 1e-6)
    similarity = unit @ unit.T
    np.fill_diagonal(similarity, -1.0)
    k = min(neighbors, len(vectors) - 1)
    nearest = -np.sort(-similarity, axis=1)[:, :k].mean(axis=1)
    ranks = np.argsort(np.argsort(-nearest, kind="stable"), kind="stable")
    return (ranks / (len(vectors) - 1)).astype(np.float32)


def technical_quality(measured: dict[str, dict[str, Any]], unit_ids: list[str]) -> dict[str, float]:
    """Within-film sharpness percentile, zeroed for badly exposed shots (None when unmeasured)."""
    values = {unit_id: math.log1p(float(measured[unit_id].get("sharpness") or 0.0))
              for unit_id in unit_ids if unit_id in measured}
    if not values:
        return {}
    ordered = sorted(values, key=values.get)
    rank = {unit_id: position / max(1, len(ordered) - 1) for position, unit_id in enumerate(ordered)}
    quality = {}
    for unit_id in values:
        brightness = (measured[unit_id].get("look") or {}).get("brightness")
        exposed = brightness is None or 0.04 <= brightness <= 0.96
        quality[unit_id] = rank[unit_id] if exposed else 0.0
    return quality


def synthesize_film(db: Any, film: FilmRef, *, understanding: dict[str, Any] | None, measure: dict[str, Any] | None,
                    metadata: dict[str, Any] | None, film_popularity: float | None,
                    dialogue: list[dict[str, Any]]) -> dict[str, Any]:
    settings = PRODUCER.settings
    units = film_units(db, film.film_id, columns=["unit_id", "t_start", "t_end", "img_vec"])
    unit_ids = [unit["unit_id"] for unit in units]
    story = (understanding or {}).get("shots") or {}
    measured = (measure or {}).get("shots") or {}
    picks = {row["unit_id"] for row in (understanding or {}).get("iconic") or []}
    quotes = ((metadata or {}).get("wikiquote") or {}).get("quotes") or []
    quote_hits = align_quotes(quotes, dialogue, min_score=settings["quote_min_score"]) if quotes and dialogue else []
    vectors = np.array([unit["img_vec"] for unit in units], dtype=np.float32) if units else np.zeros((0, 1))
    distinct = distinctiveness(vectors, settings["neighbors"]) if len(vectors) else np.zeros(0)
    technical = technical_quality(measured, unit_ids)
    popularity = 0.5 if film_popularity is None else float(film_popularity)
    records: dict[str, dict[str, Any]] = {}
    for position, unit in enumerate(units):
        unit_id = unit["unit_id"]
        shot = story.get(unit_id) or {}
        start, end = float(unit["t_start"]), float(unit["t_end"])
        quote = max((hit for hit in quote_hits if hit["start"] < end and hit["end"] > start),
                    key=lambda hit: hit["score"], default=None)
        fame = None
        if shot:
            fame = FAME_LEVELS[shot["fame"]] + (settings["iconic_pick_bonus"] if unit_id in picks else 0.0)
            fame = min(1.0, fame + (settings["quote_bonus"] * quote["score"] if quote else 0.0))
        elif quote:
            fame = min(1.0, 0.5 + settings["quote_bonus"] * quote["score"])
        craft = None
        if shot:
            model = CRAFT_LEVELS[shot["craft"]]
            craft = model if unit_id not in technical else (
                settings["craft_model_weight"] * model + (1 - settings["craft_model_weight"]) * technical[unit_id])
        elif unit_id in technical:
            craft = 0.5 * technical[unit_id]
        library_fame = None if fame is None else fame * (0.35 + 0.65 * popularity)
        iconic = unit_id in picks or bool(quote and quote["score"] >= settings["iconic_quote_score"]
                                          and shot.get("fame", 0) >= 2)
        records[unit_id] = {
            "fame": None if fame is None else round(fame, 4),
            "fame_library": None if library_fame is None else round(library_fame, 4),
            "craft": None if craft is None else round(craft, 4),
            "technical": None if unit_id not in technical else round(technical[unit_id], 4),
            "distinctiveness": round(float(distinct[position]), 4) if len(distinct) else None,
            "iconic": iconic,
            "gem": False,
            "famous_line": quote["quote"] if quote else None,
        }

    # Hidden gems: per film, the best well-crafted little-known shots.
    candidates = []
    for unit_id, record in records.items():
        shot = story.get(unit_id) or {}
        if (not shot or record["iconic"] or shot["craft"] < settings["gem_min_craft_level"]
                or shot["fame"] > settings["gem_max_fame_level"] or (record["technical"] or 0.0) < 0.4):
            continue
        score = record["craft"] * (1.0 - record["fame_library"]) * (0.5 + 0.5 * (record["distinctiveness"] or 0.0))
        candidates.append((score, unit_id))
    candidates.sort(reverse=True)
    for _score, unit_id in candidates[:max(1, round(settings["gem_share"] * len(records)))]:
        records[unit_id]["gem"] = True

    def highlight_score(unit_id: str) -> float:
        record = records[unit_id]
        return (record["fame"] or 0.0) + 0.3 * (record["craft"] or 0.0) + (0.3 if record["iconic"] else 0.0)

    highlights = sorted(records, key=highlight_score, reverse=True)[:settings["highlights"]]
    gems = [unit_id for _score, unit_id in candidates if records[unit_id]["gem"]][:settings["highlights"]]
    return {"shots": records, "highlights": highlights, "gems": gems, "quotes_matched": len(quote_hits),
            "film_popularity": film_popularity}


def run(config: Any, db: Any, films: list[FilmRef], *, progress: Callable[[str], None] = print) -> dict[str, int]:
    from pipeline.evidence import measure, metadata as metadata_module, understanding
    from pipeline.evidence.compile import film_popularity
    from pipeline.evidence.library import list_films
    from pipeline.evidence.subtitles import current_dialogue

    assets = config.paths.assets_dir
    # Popularity is a library percentile: compute it over every film with metadata.
    documents = {}
    for film in list_films(db):
        artifact = store.read_artifact(assets, film.film_id, metadata_module.PRODUCER)
        if artifact:
            documents[film.film_id] = artifact["data"]
    popularity = film_popularity(documents)
    story_producer = understanding.producer()
    counts = {"done": 0, "skipped": 0}
    for film in films:
        story = store.read_artifact(assets, film.film_id, story_producer)
        measured = store.read_artifact(assets, film.film_id, measure.PRODUCER)
        if story is None and measured is None:
            counts["skipped"] += 1
            continue
        data = synthesize_film(db, film, understanding=(story or {}).get("data"), measure=(measured or {}).get("data"),
                               metadata=documents.get(film.film_id), film_popularity=popularity.get(film.film_id),
                               dialogue=current_dialogue(config, film.film_id))
        inputs = {"understanding": f"{story['profile_id']}@{story['created_at']}" if story else "",
                  "measure": f"{measured['profile_id']}@{measured['created_at']}" if measured else "",
                  "popularity": str(popularity.get(film.film_id))}
        store.write_artifact(assets, film.film_id, PRODUCER, data, inputs=inputs)
        counts["done"] += 1
        iconic = sum(record["iconic"] for record in data["shots"].values())
        gems = sum(record["gem"] for record in data["shots"].values())
        progress(f"[synthesis] {film.title}: {iconic} iconic, {gems} gems, {data['quotes_matched']} quotes aligned")
    return counts
