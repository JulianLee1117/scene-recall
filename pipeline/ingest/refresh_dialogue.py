"""Adopt a film's preferred dialogue source without repeating media, image or annotation work.

When a better dialogue source appears for an indexed film (an accepted synced
subtitle download), only dialogue-derived unit fields change: each shot's
dialogue lines, its lexical ``searchable_text`` and the legacy PE text vector,
plus the semantic dialogue view. Shots whose lines are unchanged are left
alone, so a rerun after an interruption repairs exactly what is stale.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pipeline.config import Config


def refresh_dialogue(config: Config, film_path: Path) -> dict[str, Any]:
    """Re-derive dialogue for one film and publish changed units; returns counts."""
    from pipeline.index.backfill_text import backfill_text_features_during_ingest
    from pipeline.index.writer import _film_condition, open_db, publish_dialogue_updates
    from pipeline.ingest.dialogue import extract_dialogue
    from pipeline.ingest.embed import embed_text
    from pipeline.ingest.locks import film_operation_lock, global_ingest_lock
    from pipeline.ingest.probe import probe_film

    film = probe_film(film_path, config)
    lock = global_ingest_lock(config.paths.assets_dir)
    lock.acquire()
    try:
        with film_operation_lock(film.asset_dir):
            lines = extract_dialogue(film, config)
            db = open_db(config)
            units = (db.open_table("units").search().where(_film_condition(film.film_id))
                     .select(["unit_id", "t_start", "t_end", "caption", "dialogue"]).limit(None).to_list())
            updates: dict[str, dict[str, Any]] = {}
            for unit in units:
                start, end = float(unit["t_start"]), float(unit["t_end"])
                new = [line.text for line in lines if line.start < end and line.end > start]
                if new != json.loads(unit.get("dialogue") or "[]"):
                    searchable = " ".join(part for part in [unit.get("caption") or "", *new] if part).strip()
                    updates[unit["unit_id"]] = {"dialogue": new, "searchable_text": searchable}
            if updates:
                vectors = embed_text([update["searchable_text"] for update in updates.values()], config)
                for update, vector in zip(updates.values(), vectors, strict=True):
                    update["txt_vec"] = vector
                publish_dialogue_updates(db, film, updates)
            text = backfill_text_features_during_ingest(config, film_id=film.film_id)
            return {"film_id": film.film_id, "lines": len(lines), "units_updated": len(updates),
                    "text_views_embedded": text.embedded, "text_profile_active": text.activated}
    finally:
        lock.release()
