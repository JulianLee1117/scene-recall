"""writer.py — LanceDB persistence layer for the cinema-search pipeline.

Public API
----------
- ``open_db(config)``          — open (or create) the LanceDB at assets_dir/db
- ``create_tables(db)``        — idempotent table creation
- ``ensure_search_indexes(db)`` — create/synchronize native search indexes
- ``write_unit(...)``          — upsert one indexable shot unit
- ``write_units(...)``         — upsert a film's buffered units in one merge
- ``publish_film_index(...)``  — scoped replacement with units as ready marker
- ``write_film(db, film)``     — upsert one film record

All write operations are idempotent: calling them a second time with the same
primary key (``unit_id`` / ``film_id``) silently updates the existing row.

Vector dimension
----------------
Vectors are fixed at **1024 dimensions** (PE core L/14) for Phase 1.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
import json
import os
from pathlib import Path
from threading import Lock
from typing import Any, Optional

from filelock import FileLock
import lancedb
import numpy as np

from pipeline.config import Config
from pipeline.ingest.probe import FilmRecord
from pipeline.ingest.shots import Shot
from pipeline.index.schema import (
    FILMS_SCHEMA,
    FRAMES_SCHEMA_VERSION,
    make_frames_schema,
    make_units_schema,
)


_INGEST_TIMESTAMP_SOURCE = "ingest_keyframe_seek_v1"
_PUBLICATION_LOCK = Lock()
_DATABASE_WRITE_LOCK = ".scene-recall-write.lock"
_DATABASE_WRITE_LOCK_TIMEOUT_SECONDS = 600
UNITS_FTS_FIELD = "searchable_text"
UNITS_FTS_INDEX = "units_searchable_text_fts_v1"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class UnitWrite:
    """One fully prepared unit waiting for a batched LanceDB upsert."""

    shot: Shot
    annotation: dict[str, Any]
    img_vec: np.ndarray
    txt_vec: np.ndarray
    dialogue: Sequence[str] = ()


@dataclass(frozen=True)
class FrameWrite:
    """One independently embedded keyframe waiting for publication."""

    unit_id: str
    shot_id: str
    frame_index: int
    timestamp: float
    path: Path
    visual_encoder: str
    visual_vec: np.ndarray
    is_representative: bool


def table_names(db: lancedb.DBConnection) -> set[str]:
    """Return the database's table names across LanceDB API generations."""
    try:
        return set(db.list_tables().tables)
    except (AttributeError, TypeError):
        return set(db.table_names(limit=1_000))


def published_film_ids(db: lancedb.DBConnection) -> frozenset[str]:
    """Return film IDs that are fully published and safe to surface.

    Publication is defined by :func:`publish_film_index`: a film row plus at
    least one representative unit.  Every consumer of "which films are done"
    (the /library endpoint, ingest-batch skipping) must share this query so
    their answers cannot drift.
    """
    from lancedb.expr import col, lit

    names = table_names(db)
    if "films" not in names or "units" not in names:
        return frozenset()

    film_rows = (
        db.open_table("films").search().select(["film_id"]).limit(None).to_list()
    )
    ready_rows = (
        db.open_table("units")
        .search()
        .select(["film_id"])
        .where(col("is_representative") == lit(True))
        .limit(None)
        .to_list()
    )
    film_ids = {str(row["film_id"]) for row in film_rows}
    ready_ids = {str(row["film_id"]) for row in ready_rows}
    return frozenset(film_ids & ready_ids)


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


def create_tables(db: lancedb.DBConnection, vector_dim: int = 1024) -> None:
    """Create the ``units``, ``frames``, and ``films`` tables if absent.

    Safe to call multiple times; existing tables are left untouched.

    Parameters
    ----------
    db:
        Open LanceDB connection (from :func:`open_db`).
    vector_dim:
        Embedding dimension for ``img_vec``, ``txt_vec``, and ``visual_vec``.
        Defaults to 1024 (PE core L/14).  Pass 1152 for SigLIP-2.
        Ignored when the table already exists.
    """
    # ``exist_ok`` table creation and FTS creation are separate Lance
    # operations.  Serialize them across API-launched ingest subprocesses so
    # two first-time ingests cannot race to create the same native index.
    with _PUBLICATION_LOCK, _database_write_lock(db):
        _create_or_check_table(db, "units", make_units_schema(vector_dim))
        _create_or_check_table(db, "frames", make_frames_schema(vector_dim))
        _create_or_check_table(db, "films", FILMS_SCHEMA)
        _ensure_search_indexes_locked(db)


def _database_write_lock(
    db: lancedb.DBConnection,
) -> AbstractContextManager[None]:
    """Return the cross-process lock guarding local DB publication.

    Unit tests use lightweight DB doubles and LanceDB also supports remote
    connection URIs, neither of which has a meaningful local lock-file path.
    The in-process publication lock still protects those callers.
    """
    uri = getattr(db, "uri", None)
    if not isinstance(uri, (str, os.PathLike)):
        return nullcontext()
    uri_text = os.fspath(uri)
    if "://" in uri_text:
        return nullcontext()

    root = Path(uri_text)
    root.mkdir(parents=True, exist_ok=True)
    return FileLock(
        root / _DATABASE_WRITE_LOCK,
        timeout=_DATABASE_WRITE_LOCK_TIMEOUT_SECONDS,
    )


def ensure_search_indexes(db: lancedb.DBConnection) -> None:
    """Create and fully synchronize the versioned native FTS index.

    LanceDB 0.33 can leave merge-inserted rows outside an existing FTS index.
    A normal query is not guaranteed to merge those rows correctly for terms
    already present in the index, so zero unindexed rows is a correctness
    invariant rather than a performance preference.
    """
    with _PUBLICATION_LOCK, _database_write_lock(db):
        _ensure_search_indexes_locked(db)


def _ensure_search_indexes_locked(db: lancedb.DBConnection) -> None:
    """Implement :func:`ensure_search_indexes` while the DB lock is held."""
    if "units" not in table_names(db):
        return

    table = db.open_table("units")
    indices = list(table.list_indices())
    named = [index for index in indices if index.name == UNITS_FTS_INDEX]
    same_field = [
        index
        for index in indices
        if str(index.index_type).upper() == "FTS"
        and list(index.columns) == [UNITS_FTS_FIELD]
    ]

    if named and not (
        len(named) == 1
        and str(named[0].index_type).upper() == "FTS"
        and list(named[0].columns) == [UNITS_FTS_FIELD]
    ):
        raise RuntimeError(
            f"search index {UNITS_FTS_INDEX!r} has an incompatible contract"
        )
    if len(same_field) > 1 or (same_field and not named):
        names = ", ".join(sorted(index.name for index in same_field))
        raise RuntimeError(
            "units.searchable_text already has an unmanaged FTS index "
            f"({names}); remove it before creating {UNITS_FTS_INDEX!r}"
        )

    if not named:
        table.create_fts_index(
            UNITS_FTS_FIELD,
            name=UNITS_FTS_INDEX,
            replace=False,
            base_tokenizer="simple",
            language="English",
            max_token_length=40,
            lower_case=True,
            stem=True,
            remove_stop_words=True,
            ascii_folding=True,
            with_position=True,
        )

    stats = table.index_stats(UNITS_FTS_INDEX)
    if stats is None:
        raise RuntimeError(f"search index {UNITS_FTS_INDEX!r} was not created")
    if int(stats.num_unindexed_rows) > 0:
        table.optimize()
        stats = table.index_stats(UNITS_FTS_INDEX)
    if stats is None or int(stats.num_unindexed_rows) > 0:
        remaining = None if stats is None else int(stats.num_unindexed_rows)
        raise RuntimeError(
            f"search index {UNITS_FTS_INDEX!r} is incomplete; "
            f"unindexed_rows={remaining}"
        )


def _create_or_check_table(
    db: lancedb.DBConnection,
    name: str,
    schema: Any,
) -> None:
    """Create *name* if absent; fail with a recovery hint when it is stale."""
    try:
        db.create_table(name, schema=schema, exist_ok=True)
    except ValueError as exc:
        raise RuntimeError(_stale_table_message(name)) from exc

    existing = set(db.open_table(name).schema.names)
    missing = [field for field in schema.names if field not in existing]
    if missing:
        raise RuntimeError(_stale_table_message(name, missing))


def _stale_table_message(name: str, missing: Sequence[str] = ()) -> str:
    details = f" (missing columns: {', '.join(missing)})" if missing else ""
    return (
        f"LanceDB table {name!r} predates the current index schema{details}. "
        "Delete the database directory (<assets_dir>/db) and re-ingest your "
        "films; dialogue, media, and matching annotations are reused from "
        "each film's asset cache, so only stale annotations are re-requested."
    )


def upsert_frames(
    db: lancedb.DBConnection,
    rows: Sequence[dict[str, Any]],
) -> None:
    """Upsert keyframe rows by stable ``frame_id``; an empty input is a no-op."""
    upsert_frame_batches(db, (rows,))


def upsert_frame_batches(
    db: lancedb.DBConnection,
    batches: Iterable[Sequence[dict[str, Any]]],
) -> None:
    """Upsert frame batches under one publication lock.

    A legacy backfill may need bounded batches for memory, but its complete
    run must not interleave with a film re-publication and reintroduce stale
    frame rows after that film's replacement boundary.
    """
    with _PUBLICATION_LOCK, _database_write_lock(db):
        for rows in batches:
            _merge_rows(db, "frames", "frame_id", rows)


def _merge_rows(
    db: lancedb.DBConnection,
    table_name: str,
    key: str,
    rows: Sequence[dict[str, Any]],
    *,
    delete_condition: str | None = None,
) -> None:
    """Merge rows by *key* in one table transaction; empty input is a no-op."""
    if not rows and delete_condition is None:
        return
    builder = (
        db.open_table(table_name)
        .merge_insert(key)
        .when_matched_update_all()
        .when_not_matched_insert_all()
    )
    if delete_condition is not None:
        builder = builder.when_not_matched_by_source_delete(delete_condition)
    builder.execute(list(rows))


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
    write_units(
        db,
        film,
        [
            UnitWrite(
                shot=shot,
                annotation=annotation,
                img_vec=img_vec,
                txt_vec=txt_vec,
                dialogue=dialogue or (),
            )
        ],
    )


def write_units(
    db: lancedb.DBConnection,
    film: FilmRecord,
    units: Sequence[UnitWrite],
) -> None:
    """Upsert prepared units in one LanceDB transaction.

    Lance creates a new table version for every merge. Ingest therefore
    buffers a film's units and sends them here together, keeping storage and
    manifest growth roughly constant as the library expands. Native FTS is
    synchronized before this function returns.
    """
    if not units:
        return

    rows = [_make_unit_row(film, unit) for unit in units]
    with _PUBLICATION_LOCK, _database_write_lock(db):
        _upsert_unit_rows(db, rows)
        _ensure_search_indexes_locked(db)


def publish_film_index(
    db: lancedb.DBConnection,
    film: FilmRecord,
    units: Sequence[UnitWrite],
    frames: Sequence[FrameWrite],
) -> None:
    """Replace and publish one complete film without exposing partial search.

    All rows are materialized and validated first. Frames are replaced with
    one film-scoped merge, then the final film-scoped unit merge atomically
    updates/inserts the new generation and deletes its obsolete units. Existing
    complete units therefore remain searchable until that final boundary.
    Native FTS is synchronized immediately after the unit merge and before
    this function reports a successful publication.
    """
    unit_rows = [_make_unit_row(film, unit) for unit in units]
    frame_rows = [_make_frame_row(film.film_id, frame) for frame in frames]
    _validate_publication_rows(film.film_id, unit_rows, frame_rows)

    # Lance publication spans three tables and API ingest jobs run in distinct
    # subprocesses.  The in-process lock protects threads/tests; the file lock
    # prevents different film ingests from interleaving table/index versions.
    with _PUBLICATION_LOCK, _database_write_lock(db):
        _replace_frame_rows(db, film.film_id, frame_rows)

        # Metadata may be prepared before the visibility boundary. The
        # /library endpoint gates film rows on a representative unit, so a
        # crash or failed final merge cannot present this row as indexed.
        write_film(db, film)
        _replace_unit_rows(db, film.film_id, unit_rows)
        _ensure_search_indexes_locked(db)


def _replace_frame_rows(
    db: lancedb.DBConnection,
    film_id: str,
    rows: Sequence[dict[str, Any]],
) -> None:
    _replace_film_rows(db, "frames", "frame_id", film_id, rows)


def _replace_unit_rows(
    db: lancedb.DBConnection,
    film_id: str,
    rows: Sequence[dict[str, Any]],
) -> None:
    _replace_film_rows(db, "units", "unit_id", film_id, rows)


def _replace_film_rows(
    db: lancedb.DBConnection,
    table_name: str,
    key: str,
    film_id: str,
    rows: Sequence[dict[str, Any]],
) -> None:
    """Atomically replace one film's rows while preserving every other film."""
    if not rows:
        _delete_film_rows(db, table_name, film_id)
        return
    _merge_rows(
        db,
        table_name,
        key,
        rows,
        delete_condition=_film_condition(film_id),
    )


def _upsert_unit_rows(
    db: lancedb.DBConnection,
    rows: Sequence[dict[str, Any]],
) -> None:
    """Merge already-materialized unit rows in one table transaction."""
    _merge_rows(db, "units", "unit_id", rows)


def _make_unit_row(film: FilmRecord, unit: UnitWrite) -> dict[str, Any]:
    """Convert one prepared unit to the stable LanceDB row contract."""
    shot = unit.shot
    keyframe_dir = film.asset_dir / "keyframes"
    keyframe_paths = [
        str(keyframe_dir / f"{shot.shot_id}_{index}.webp")
        for index in range(len(shot.keyframe_times))
    ]
    people_count = unit.annotation.get("people_count")
    return (
        {
            "unit_id": shot.shot_id,
            "film_id": film.film_id,
            "shot_id": shot.shot_id,
            "parent_shot_id": shot.parent_shot_id,
            "t_start": float(shot.t_start),
            "t_end": float(shot.t_end),
            "is_representative": True,
            "img_vec": unit.img_vec.astype(np.float32).tolist(),
            "txt_vec": unit.txt_vec.astype(np.float32).tolist(),
            "caption": unit.annotation["caption"],
            "searchable_text": unit.annotation["searchable_text"],
            "mood": json.dumps(unit.annotation["mood"]),
            "dialogue": json.dumps(list(unit.dialogue)),
            "keyframe_paths": json.dumps(keyframe_paths),
            "framing": str(unit.annotation.get("framing") or "unknown"),
            "setting": str(unit.annotation.get("setting") or "unknown"),
            "time_of_day": str(unit.annotation.get("time_of_day") or "unknown"),
            "people_count": int(people_count) if people_count is not None else None,
            "energy": str(unit.annotation.get("energy") or "unknown"),
            "camera_motion": str(unit.annotation.get("camera_motion") or "unknown"),
            "palette": json.dumps(list(unit.annotation.get("palette") or [])),
            "subjects": json.dumps(list(unit.annotation.get("subjects") or [])),
            "on_screen_text": str(unit.annotation.get("on_screen_text") or ""),
        }
    )


def _make_frame_row(film_id: str, frame: FrameWrite) -> dict[str, Any]:
    """Convert one prepared frame to the versioned LanceDB row contract."""
    if not frame.path.is_file():
        raise FileNotFoundError(f"keyframe does not exist: {frame.path}")
    stat = frame.path.stat()
    if stat.st_size <= 0:
        raise ValueError(f"keyframe is empty: {frame.path}")
    return {
        "schema_version": FRAMES_SCHEMA_VERSION,
        "frame_id": f"{frame.unit_id}::frame::{frame.frame_index}",
        "film_id": film_id,
        "unit_id": frame.unit_id,
        "shot_id": frame.shot_id,
        "frame_index": frame.frame_index,
        "timestamp": float(frame.timestamp),
        "timestamp_source": _INGEST_TIMESTAMP_SOURCE,
        "path": str(frame.path),
        "is_representative": frame.is_representative,
        "quality_score": None,
        "source_size": stat.st_size,
        "source_mtime_ns": stat.st_mtime_ns,
        "visual_encoder": frame.visual_encoder,
        "visual_vec": frame.visual_vec.astype(np.float32).tolist(),
    }


def _validate_publication_rows(
    film_id: str,
    unit_rows: Sequence[dict[str, Any]],
    frame_rows: Sequence[dict[str, Any]],
) -> None:
    """Fail before unpublishing if a prepared generation is inconsistent."""
    unit_ids = [str(row["unit_id"]) for row in unit_rows]
    frame_ids = [str(row["frame_id"]) for row in frame_rows]
    if len(set(unit_ids)) != len(unit_ids):
        raise ValueError(f"film {film_id!r} contains duplicate unit IDs")
    if len(set(frame_ids)) != len(frame_ids):
        raise ValueError(f"film {film_id!r} contains duplicate frame IDs")
    if any(row["film_id"] != film_id for row in (*unit_rows, *frame_rows)):
        raise ValueError("publication rows crossed film boundaries")

    expected_frames = {
        (str(row["unit_id"]), frame_index): str(path)
        for row in unit_rows
        for frame_index, path in enumerate(json.loads(row["keyframe_paths"]))
    }
    actual_frames = {
        (str(row["unit_id"]), int(row["frame_index"])): str(row["path"])
        for row in frame_rows
    }
    if actual_frames.keys() != expected_frames.keys():
        missing = len(expected_frames.keys() - actual_frames.keys())
        unexpected = len(actual_frames.keys() - expected_frames.keys())
        raise ValueError(
            f"film {film_id!r} frame set is incomplete: "
            f"{missing} missing, {unexpected} unexpected"
        )
    mismatched_paths = [
        key
        for key, expected_path in expected_frames.items()
        if actual_frames[key] != expected_path
    ]
    if mismatched_paths:
        raise ValueError(
            f"film {film_id!r} has {len(mismatched_paths)} frame path mismatch(es)"
        )


def _delete_film_rows(
    db: lancedb.DBConnection,
    table_name: str,
    film_id: str,
) -> None:
    """Delete rows for exactly one film, escaping the scalar SQL literal."""
    db.open_table(table_name).delete(_film_condition(film_id))


def _film_condition(film_id: str) -> str:
    """Return a safely quoted scalar condition for one film ID."""
    escaped = film_id.replace("'", "''")
    return f"film_id = '{escaped}'"


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
            "fps": float(film.fps),
        }
    ]

    tbl = db.open_table("films")
    (
        tbl.merge_insert("film_id")
        .when_matched_update_all()
        .when_not_matched_insert_all()
        .execute(row)
    )
