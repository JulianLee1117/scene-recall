"""Per-shot priors: calibrated fame, craft, distinctiveness, iconic and hidden-gem flags.

Two separate axes (see docs/current-work.md, "Balancing famous and forgotten"):

* fame  — how widely known the moment is. Within-film fame comes from the
  understanding pass, iconic-moment picks and famous quotes aligned to the
  subtitles; library fame scales it by the film's popularity so a famous shot
  from an obscure film can still be a hidden gem library-wide.
* craft — visual/editorial strength regardless of fame: the model's rating
  blended with measured sharpness and exposure.

A hidden gem is relevant, high-craft and low-fame; low fame alone is not a gem.
"""

from __future__ import annotations

import math
import re
from typing import Any, Callable

import numpy as np

from pipeline.evidence import store
from pipeline.evidence.library import FilmRef, film_units


PRODUCER = store.Producer(
    kind="synthesis",
    name="priors",
    version=1,
    settings={"iconic_bonus": 0.2, "quote_bonus": 0.15, "craft_model_weight": 0.75,
              "iconic_threshold": 0.6, "gem_fame_max": 0.35, "gem_craft_min": 0.6, "neighbors": 10},
)


def _normalize(text: str) -> list[str]:
    text = text.casefold().replace("’", "'")
    text = re.sub(r"in'\b", "ing", text)            # talkin' -> talking
    text = re.sub(r"[^a-z0-9' ]+", " ", text)
    return [token for token in text.split() if token]


def align_quotes(quotes: list[str], dialogue: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Locate famous quotes in timed dialogue (windows of up to three lines)."""
    lines = [(float(row["start"]), float(row["end"]), _normalize(str(row["text"]))) for row in dialogue]
    matches = []
    for quote in quotes:
        target = _normalize(quote)
        if len(target) < 4:
            continue
        target_set = set(target)
        best = (0.0, None)
        for index in range(len(lines)):
            window_tokens: list[str] = []
            for width in range(3):
                if index + width >= len(lines):
                    break
                window_tokens = window_tokens + lines[index + width][2]
                if not window_tokens:
                    continue
                recall = len(target_set & set(window_tokens)) / len(target_set)
                precision = sum(token in target_set for token in window_tokens) / len(window_tokens)
                score = recall * (0.5 + 0.5 * precision)       # tightest window that contains the quote
                if score > best[0]:
                    best = (score, (lines[index][0], lines[index + width][1]))
        if best[0] >= 0.72 and best[1] is not None:
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


def synthesize_film(config: Any, db: Any, film: FilmRef, *, understanding: dict[str, Any] | None,
                    measure: dict[str, Any] | None, metadata: dict[str, Any] | None,
                    film_popularity: float | None, dialogue: list[dict[str, Any]]) -> dict[str, Any]:
    settings = PRODUCER.settings
    units = film_units(db, film.film_id, columns=["unit_id", "t_start", "t_end", "img_vec"])
    shots_u = (understanding or {}).get("shots") or {}
    shots_m = (measure or {}).get("shots") or {}
    iconic_ids = {row["unit_id"] for row in (understanding or {}).get("iconic") or []}
    quotes = ((metadata or {}).get("wikiquote") or {}).get("quotes") or []
    quote_hits = align_quotes(quotes, dialogue) if quotes and dialogue else []
    vectors = np.array([unit["img_vec"] for unit in units], dtype=np.float32) if units else np.zeros((0, 1))
    distinct = distinctiveness(vectors, settings["neighbors"]) if len(vectors) else np.zeros(0)
    sharp = np.array([math.log1p((shots_m.get(u["unit_id"]) or {}).get("sharpness") or 0.0) for u in units])
    sharp_z = (sharp - sharp.mean()) / (sharp.std() + 1e-6) if len(sharp) else sharp
    popularity = 0.5 if film_popularity is None else float(film_popularity)
    records: dict[str, dict[str, Any]] = {}
    for position, unit in enumerate(units):
        unit_id = unit["unit_id"]
        story = shots_u.get(unit_id) or {}
        measured = shots_m.get(unit_id) or {}
        start, end = float(unit["t_start"]), float(unit["t_end"])
        quote = next((hit for hit in quote_hits if hit["start"] < end and hit["end"] > start), None)
        fame = None
        if story:
            fame = story["fame"] / 3.0
            fame += settings["iconic_bonus"] if unit_id in iconic_ids else 0.0
            fame += settings["quote_bonus"] * quote["score"] if quote else 0.0
            fame = min(1.0, fame)
        elif quote:
            fame = min(1.0, 0.4 + settings["quote_bonus"] * quote["score"])
        brightness = (measured.get("look") or {}).get("brightness")
        exposure_ok = 0.0 if brightness is not None and (brightness < 0.04 or brightness > 0.96) else 1.0
        technical = (1.0 / (1.0 + math.exp(-float(sharp_z[position])))) * exposure_ok if measured else None
        craft = None
        if story and technical is not None:
            craft = settings["craft_model_weight"] * story["craft"] / 3.0 + (1 - settings["craft_model_weight"]) * technical
        elif story:
            craft = story["craft"] / 3.0
        elif technical is not None:
            craft = 0.5 * technical
        library_fame = None if fame is None else fame * (0.35 + 0.65 * popularity)
        records[unit_id] = {
            "fame": None if fame is None else round(fame, 4),
            "fame_library": None if library_fame is None else round(library_fame, 4),
            "craft": None if craft is None else round(craft, 4),
            "distinctiveness": round(float(distinct[position]), 4) if len(distinct) else None,
            "iconic": bool(fame is not None and (fame >= settings["iconic_threshold"] or unit_id in iconic_ids)),
            "gem": bool(craft is not None and library_fame is not None and craft >= settings["gem_craft_min"]
                        and library_fame <= settings["gem_fame_max"] and exposure_ok > 0),
            "famous_line": quote["quote"] if quote else None,
        }
    ranked = sorted(records.items(), key=lambda item: -((item[1]["fame"] or 0) + 0.2 * (item[1]["craft"] or 0)))
    gems = sorted(((uid, r) for uid, r in records.items() if r["gem"]),
                  key=lambda item: -((item[1]["craft"] or 0) + 0.3 * (item[1]["distinctiveness"] or 0)))
    return {"shots": records, "highlights": [uid for uid, _r in ranked[:24]], "gems": [uid for uid, _r in gems[:24]],
            "quotes_matched": len(quote_hits), "film_popularity": film_popularity}


def run(config: Any, db: Any, films: list[FilmRef], *, progress: Callable[[str], None] = print) -> dict[str, int]:
    from pipeline.evidence import measure, metadata as metadata_module, understanding
    from pipeline.evidence.compile import film_popularity
    from pipeline.evidence.subtitles import current_dialogue
    meta_docs = {}
    for film in films:
        artifact = store.read_artifact(config.paths.assets_dir, film.film_id, metadata_module.PRODUCER)
        if artifact:
            meta_docs[film.film_id] = artifact["data"]
    # Popularity is a library percentile: compute over every film with metadata.
    from pipeline.evidence.library import list_films
    all_meta = {}
    for film in list_films(db):
        artifact = store.read_artifact(config.paths.assets_dir, film.film_id, metadata_module.PRODUCER)
        if artifact:
            all_meta[film.film_id] = artifact["data"]
    popularity = film_popularity(all_meta)
    counts = {"done": 0, "skipped": 0}
    for film in films:
        und = store.read_artifact(config.paths.assets_dir, film.film_id, understanding.producer())
        mea = store.read_artifact(config.paths.assets_dir, film.film_id, measure.PRODUCER)
        if und is None and mea is None:
            counts["skipped"] += 1
            continue
        data = synthesize_film(config, db, film, understanding=(und or {}).get("data"), measure=(mea or {}).get("data"),
                               metadata=meta_docs.get(film.film_id), film_popularity=popularity.get(film.film_id),
                               dialogue=current_dialogue(config, film.film_id))
        inputs = {"understanding": (und or {}).get("profile_id") or "", "measure": (mea or {}).get("profile_id") or "",
                  "understanding_created": (und or {}).get("created_at") or "", "measure_created": (mea or {}).get("created_at") or ""}
        store.write_artifact(config.paths.assets_dir, film.film_id, PRODUCER, data, inputs=inputs)
        counts["done"] += 1
        iconic = sum(r["iconic"] for r in data["shots"].values())
        gems = sum(r["gem"] for r in data["shots"].values())
        progress(f"[synthesis] {film.title}: {iconic} iconic, {gems} gems, {data['quotes_matched']} quotes aligned")
    return counts

