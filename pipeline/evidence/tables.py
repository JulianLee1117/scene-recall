"""Compiled evidence tables: compact, rebuildable search views of artifacts.

Tables are derived entirely from evidence artifacts plus the canonical
``units``/``films`` tables, so they can be dropped and recompiled at any time.
Writes use the same publication and cross-process locks as film publication.
"""

from __future__ import annotations

from typing import Any, Sequence

import pyarrow as pa

from pipeline.index.writer import _PUBLICATION_LOCK, _database_write_lock, _merge_rows, table_names


FILM_META = "film_meta"
TABLE_SCHEMA_VERSION = 1


def film_meta_schema() -> pa.Schema:
    return pa.schema([
        pa.field("schema_version", pa.int16()),
        pa.field("film_id", pa.string()),
        pa.field("title", pa.string()),
        pa.field("name", pa.string()),
        pa.field("year", pa.int32()),
        pa.field("wikidata_id", pa.string()),
        pa.field("imdb_id", pa.string()),
        pa.field("wikipedia_title", pa.string()),
        pa.field("directors", pa.string()),      # JSON list[str]
        pa.field("genres", pa.string()),         # JSON list[str]
        pa.field("forms", pa.string()),          # JSON list[str]: Wikidata film form (anime film, animated film, ...)
        pa.field("countries", pa.string()),      # JSON list[str]
        pa.field("languages", pa.string()),      # JSON list[str]
        pa.field("cast", pa.string()),           # JSON list[{actor, characters}]
        pa.field("plot", pa.string()),
        pa.field("quote_count", pa.int32()),
        pa.field("imdb_rating", pa.float32()),
        pa.field("imdb_votes", pa.int64()),
        pa.field("pageviews_12m", pa.int64()),
        pa.field("popularity", pa.float32()),    # 0..1 library percentile
        pa.field("metadata_profile", pa.string()),
    ])


def ensure_table(db: Any, name: str, schema: pa.Schema) -> None:
    """Create *name* when absent; add new nullable columns in place; rebuild other drift.

    A schema that only adds nullable columns migrates in place: existing rows
    read null until their film is compiled again, so ordinary per-film
    compiles keep working. Compiled tables hold no primary data, so any other
    drift (a removed or retyped column) recreates an empty table, and a
    non-empty one must be rebuilt explicitly.
    """
    with _PUBLICATION_LOCK, _database_write_lock(db):
        if name in table_names(db):
            table = db.open_table(name)
            existing = table.schema
            common = [n for n in schema.names if n in existing.names]
            if set(existing.names) <= set(schema.names) and all(existing.field(n).type == schema.field(n).type
                                                                  for n in common):
                missing = [schema.field(n) for n in schema.names if n not in existing.names]
                if not missing:
                    return
                if all(field.nullable for field in missing):
                    table.add_columns(pa.schema(missing))
                    return
            if table.count_rows() == 0:
                db.drop_table(name)
            else:
                raise RuntimeError(f"compiled table {name!r} has an outdated schema; run "
                                   f"`python -m pipeline.evidence compile --rebuild` to recreate it")
        db.create_table(name, schema=schema, exist_ok=True)


def replace_all(db: Any, name: str, schema: pa.Schema, key: str, rows: Sequence[dict[str, Any]]) -> None:
    """Replace a small table's complete content in one merge transaction."""
    ensure_table(db, name, schema)
    with _PUBLICATION_LOCK, _database_write_lock(db):
        if rows:
            _merge_rows(db, name, key, list(rows), delete_condition="true")
        else:
            db.open_table(name).delete("true")


def drop_table(db: Any, name: str) -> None:
    with _PUBLICATION_LOCK, _database_write_lock(db):
        if name in table_names(db):
            db.drop_table(name)


SHOT_EVIDENCE = "shot_evidence"
SCENES = "scenes"
DIALOGUE_LINES = "dialogue_lines"


def shot_evidence_schema() -> pa.Schema:
    """One row per unit of every film with v2 evidence; nulls mean "not measured"."""
    return pa.schema([
        pa.field("schema_version", pa.int16()),
        pa.field("unit_id", pa.string()),
        pa.field("film_id", pa.string()),
        pa.field("t_start", pa.float64()),
        pa.field("t_end", pa.float64()),
        pa.field("scene_id", pa.string()),
        # Understanding (hosted model; world knowledge allowed and labelled by source).
        pa.field("characters", pa.string()),      # JSON list[str]
        pa.field("action", pa.string()),
        pa.field("peak_time", pa.float64()),
        pa.field("emotion", pa.string()),
        pa.field("line", pa.string()),
        pa.field("speaker", pa.string()),
        pa.field("audio_cue", pa.string()),
        pa.field("camera_hint", pa.string()),
        pa.field("cut_hint", pa.bool_()),
        pa.field("iconic_note", pa.string()),
        # Priors (synthesis).
        pa.field("fame", pa.float32()),           # 0..1 within the film
        pa.field("fame_library", pa.float32()),   # fame scaled by film popularity
        pa.field("craft", pa.float32()),          # 0..1
        pa.field("distinctiveness", pa.float32()),  # 0..1 within-film percentile
        pa.field("iconic", pa.bool_()),
        pa.field("gem", pa.bool_()),
        pa.field("famous_line", pa.string()),
        # Measurements (pixels).
        pa.field("camera", pa.string()),          # dominant measured movement or "unknown"
        pa.field("camera_slow", pa.bool_()),      # sustained slow move found by accumulated drift
        pa.field("camera_moving", pa.float32()),  # share of the shot with camera movement
        pa.field("camera_reliability", pa.float32()),
        pa.field("camera_segments", pa.string()),  # JSON [[start, end, label], ...]
        pa.field("motion_energy", pa.float32()),  # residual subject motion
        pa.field("hidden_cuts", pa.string()),     # JSON list[float]
        pa.field("dark_spans", pa.string()),      # JSON list[[start, end]]: near-black stretches (fades)
        pa.field("people", pa.float32()),         # median people per sample
        pa.field("people_max", pa.int32()),
        pa.field("subject", pa.string()),         # JSON main-subject track (content-box coordinates)
        pa.field("subject_x", pa.float32()),
        pa.field("subject_y", pa.float32()),
        pa.field("subject_size", pa.float32()),
        pa.field("brightness", pa.float32()),
        pa.field("contrast", pa.float32()),
        pa.field("saturation", pa.float32()),
        pa.field("colorfulness", pa.float32()),
        pa.field("warmth", pa.float32()),
        pa.field("palette", pa.string()),         # JSON [[r, g, b, share], ...]
        pa.field("sharpness", pa.float32()),
        pa.field("hero_path", pa.string()),       # relative to assets_dir
        pa.field("hero_time", pa.float64()),
        pa.field("focus_start", pa.float64()),    # the stretch showing the same picture as the peak
        pa.field("focus_end", pa.float64()),
        pa.field("preview_path", pa.string()),    # hover preview kept on the focus span, else the ingest one
        # Search text derived from the above (semantic views).
        pa.field("story_text", pa.string()),
        pa.field("scene_text", pa.string()),
        pa.field("mood_text", pa.string()),
        pa.field("sources", pa.string()),         # JSON {kind: profile_id}
    ])


def scenes_schema() -> pa.Schema:
    return pa.schema([
        pa.field("schema_version", pa.int16()),
        pa.field("scene_id", pa.string()),
        pa.field("film_id", pa.string()),
        pa.field("index", pa.int32()),
        pa.field("t_start", pa.float64()),
        pa.field("t_end", pa.float64()),
        pa.field("first_unit_id", pa.string()),
        pa.field("last_unit_id", pa.string()),
        pa.field("shot_count", pa.int32()),
        pa.field("title", pa.string()),
        pa.field("summary", pa.string()),
        pa.field("setting", pa.string()),
        pa.field("characters", pa.string()),      # JSON list[str]
        pa.field("story_context", pa.string()),
        pa.field("tone", pa.string()),
        pa.field("fame", pa.float32()),           # max shot fame in the scene
        pa.field("iconic_count", pa.int32()),
        pa.field("profile", pa.string()),
    ])


def dialogue_lines_schema() -> pa.Schema:
    return pa.schema([
        pa.field("schema_version", pa.int16()),
        pa.field("line_id", pa.string()),
        pa.field("film_id", pa.string()),
        pa.field("unit_id", pa.string()),         # shot with the largest overlap
        pa.field("t_start", pa.float64()),
        pa.field("t_end", pa.float64()),
        pa.field("text", pa.string()),
        pa.field("norm", pa.string()),            # normalized for quote matching (FTS-indexed with positions)
        pa.field("source", pa.string()),          # downloaded_srt | sidecar | embedded_text | whisper | ...
    ])


def replace_film(db: Any, name: str, schema: pa.Schema, key: str, film_id: str, rows: Sequence[dict[str, Any]]) -> None:
    """Replace one film's rows in one transaction, typed by *schema*."""
    from pipeline.index.writer import _film_condition

    ensure_table(db, name, schema)
    with _PUBLICATION_LOCK, _database_write_lock(db):
        table = db.open_table(name)
        if not rows:
            table.delete(_film_condition(film_id))
            return
        data = pa.Table.from_pylist(list(rows), schema=schema)
        (table.merge_insert(key).when_matched_update_all().when_not_matched_insert_all()
         .when_not_matched_by_source_delete(_film_condition(film_id)).execute(data))
