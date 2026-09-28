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
    """Create *name* when absent; recreate an empty table whose schema drifted.

    Compiled tables hold no primary data, so an incompatible table is rebuilt
    rather than migrated. Non-empty drifted tables must be dropped explicitly.
    """
    with _PUBLICATION_LOCK, _database_write_lock(db):
        if name in table_names(db):
            existing = db.open_table(name).schema
            if existing.names == schema.names and all(existing.field(n).type == schema.field(n).type for n in schema.names):
                return
            if db.open_table(name).count_rows() == 0:
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
