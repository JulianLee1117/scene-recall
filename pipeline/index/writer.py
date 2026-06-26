"""writer.py — LanceDB persistence layer for the cinema-search pipeline.

Public API
----------
- ``open_db(config)``          — open (or create) the LanceDB at assets_dir/db
- ``create_tables(db)``        — idempotent table creation
- ``write_unit(...)``          — upsert one indexable shot unit
- ``write_film(db, film)``     — upsert one film record

All write operations are idempotent: calling them a second time with the same
primary key (``unit_id`` / ``film_id``) silently updates the existing row.

Vector dimension
----------------
Vectors are fixed at **1024 dimensions** (PE core L/14) for Phase 1.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import numpy as np

import lancedb

from pipeline.config import Config
from pipeline.ingest.probe import FilmRecord
from pipeline.ingest.shots import Shot
from pipeline.index.schema import UNITS_SCHEMA, FILMS_SCHEMA


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def open_db(config: Config) -> lancedb.DBConnection:
    """Open (or create) the LanceDB at ``config.paths.assets_dir / "db"``.

    Parameters
    ----------
    config:
        Pipeline configuration.  ``config.paths.assets_dir`` determines
        the parent directory; the actual database lives in a ``db/``
        sub-directory beneath it.

    Returns
    -------
    lancedb.DBConnection
        A live connection to the database.
    """
    db_path = config.paths.assets_dir / "db"
    db_path.mkdir(parents=True, exist_ok=True)
    return lancedb.connect(str(db_path))


def create_tables(db: lancedb.DBConnection) -> None:
    """Create the ``units`` and ``films`` tables if they do not already exist.

    Safe to call multiple times; existing tables are left untouched.

    Parameters
    ----------
    db:
        Open LanceDB connection (from :func:`open_db`).
    """
    db.create_table("units", schema=UNITS_SCHEMA, exist_ok=True)
    db.create_table("films", schema=FILMS_SCHEMA, exist_ok=True)


def write_unit(
    db: lancedb.DBConnection,
    film: FilmRecord,
    shot: Shot,
    annotation: dict,
    img_vec: np.ndarray,
    txt_vec: np.ndarray,
    *,
    dialogue: Optional[list[str]] = None,
) -> None:
    """Upsert one indexable shot unit into the ``units`` table.

    Parameters
    ----------
    db:
        Open LanceDB connection.
    film:
        Film record for the film that contains this shot.
    shot:
        Shot (or sub-segment) to index.
    annotation:
        Dict with keys ``caption``, ``mood`` (list[str]), and
        ``searchable_text``.
    img_vec:
        L2-normalised float32 image embedding, shape ``(1024,)``.
    txt_vec:
        L2-normalised float32 text embedding, shape ``(1024,)``.
    dialogue:
        Dialogue lines that overlap this shot's time range.  Defaults to
        an empty list if not provided.
    """
    if dialogue is None:
        dialogue = []

    # Derive keyframe paths from the shot's keyframe times.
    # Keyframes are stored in film.asset_dir/keyframes/ with the naming
    # convention ``{shot_id}_{i}.webp``.
    keyframe_dir = film.asset_dir / "keyframes"
    keyframe_paths: list[str] = [
        str(keyframe_dir / f"{shot.shot_id}_{i}.webp")
        for i in range(len(shot.keyframe_times))
    ]

    row = [
        {
            "unit_id": shot.shot_id,
            "film_id": film.film_id,
            "shot_id": shot.shot_id,
            "t_start": float(shot.t_start),
            "t_end": float(shot.t_end),
            "is_representative": True,
            "img_vec": img_vec.astype(np.float32).tolist(),
            "txt_vec": txt_vec.astype(np.float32).tolist(),
            "caption": annotation["caption"],
            "searchable_text": annotation["searchable_text"],
            "mood": json.dumps(annotation["mood"]),
            "dialogue": json.dumps(dialogue),
            "keyframe_paths": json.dumps(keyframe_paths),
        }
    ]

    tbl = db.open_table("units")
    (
        tbl.merge_insert("unit_id")
        .when_matched_update_all()
        .when_not_matched_insert_all()
        .execute(row)
    )


def write_film(db: lancedb.DBConnection, film: FilmRecord) -> None:
    """Upsert one film record into the ``films`` table.

    Parameters
    ----------
    db:
        Open LanceDB connection.
    film:
        Film record to persist.
    """
    row = [
        {
            "film_id": film.film_id,
            "title": film.title,
            "path": str(film.path),
            "duration": float(film.duration),
        }
    ]

    tbl = db.open_table("films")
    (
        tbl.merge_insert("film_id")
        .when_matched_update_all()
        .when_not_matched_insert_all()
        .execute(row)
    )
