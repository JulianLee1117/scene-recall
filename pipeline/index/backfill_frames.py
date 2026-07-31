"""Backfill the frame index from already-ingested keyframes.

This migration is local-only: it reads ``units.keyframe_paths``, embeds those
existing images with the configured visual encoder, and upserts one ``frames``
row per image.  It never extracts media or calls an annotator.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
from typing import Any

import pyarrow as pa

from pipeline.config import Config
from pipeline.index.schema import FRAMES_SCHEMA_VERSION, make_frames_schema
from pipeline.index.writer import create_tables, open_db, upsert_frames
from pipeline.ingest.embed import embed_images, get_vector_dim
from pipeline.ingest.media import _KEYFRAME_START_PAD


_TIMESTAMP_SOURCE = "reconstructed_legacy_keyframe_seek_v1"


@dataclass(frozen=True)
class FrameBackfillResult:
    """Counts returned by :func:`backfill_frames` for CLI/reporting use."""

    discovered: int
    embedded: int
    upserted: int
    skipped_current: int


@dataclass(frozen=True)
class _FrameSource:
    frame_id: str
    film_id: str
    unit_id: str
    shot_id: str
    frame_index: int
    timestamp: float
    path: Path
    is_representative: bool
    source_size: int
    source_mtime_ns: int


def backfill_frames(
    config: Config,
    *,
    film_id: str | None = None,
    batch_size: int = 128,
) -> FrameBackfillResult:
    """Create/update frame rows from existing unit keyframe paths.

    Existing rows are skipped when their schema version, encoder, source path,
    file size, modification time, and reconstructed timestamp still match.
    """
    if batch_size < 1:
        raise ValueError("batch_size must be at least 1")

    vector_dim = get_vector_dim(config)
    db = open_db(config)
    create_tables(db, vector_dim=vector_dim)
    _validate_frames_table(db.open_table("frames").schema, vector_dim)

    units = _read_film_rows(
        db.open_table("units"),
        columns=[
            "unit_id",
            "film_id",
            "shot_id",
            "t_start",
            "t_end",
            "keyframe_paths",
        ],
        film_id=film_id,
    )
    sources = _collect_sources(units, film_id=film_id)
    frame_metadata = _read_film_rows(
        db.open_table("frames"),
        columns=[
            "frame_id",
            "schema_version",
            "timestamp",
            "path",
            "source_size",
            "source_mtime_ns",
            "visual_encoder",
        ],
        film_id=film_id,
    )
    existing = _existing_frame_metadata(frame_metadata)
    stale = [
        source
        for source in sources
        if not _is_current(
            source,
            existing.get(source.frame_id),
            visual_encoder=config.models.visual_encoder,
        )
    ]

    if not stale:
        return FrameBackfillResult(
            discovered=len(sources),
            embedded=0,
            upserted=0,
            skipped_current=len(sources),
        )

    vectors = embed_images([source.path for source in stale], config)
    if vectors.shape != (len(stale), vector_dim):
        raise ValueError(
            "visual encoder returned unexpected frame matrix shape: "
            f"expected {(len(stale), vector_dim)}, got {vectors.shape}"
        )

    rows = [
        {
            "schema_version": FRAMES_SCHEMA_VERSION,
            "frame_id": source.frame_id,
            "film_id": source.film_id,
            "unit_id": source.unit_id,
            "shot_id": source.shot_id,
            "frame_index": source.frame_index,
            "timestamp": source.timestamp,
            "timestamp_source": _TIMESTAMP_SOURCE,
            "path": str(source.path),
            "is_representative": source.is_representative,
            "quality_score": None,
            "source_size": source.source_size,
            "source_mtime_ns": source.source_mtime_ns,
            "visual_encoder": config.models.visual_encoder,
            "visual_vec": vector.tolist(),
        }
        for source, vector in zip(stale, vectors, strict=True)
    ]
    for start in range(0, len(rows), batch_size):
        upsert_frames(db, rows[start : start + batch_size])

    return FrameBackfillResult(
        discovered=len(sources),
        embedded=len(stale),
        upserted=len(stale),
        skipped_current=len(sources) - len(stale),
    )


def _read_film_rows(
    table: Any,
    *,
    columns: list[str],
    film_id: str | None,
) -> pa.Table:
    """Read only one film and requested scalar columns from a Lance table."""
    query = table.search().select(columns)
    if film_id is not None:
        escaped = film_id.replace("'", "''")
        query = query.where(f"film_id = '{escaped}'")
    return query.to_arrow()


def _collect_sources(
    units: pa.Table,
    *,
    film_id: str | None,
) -> list[_FrameSource]:
    required = {
        "unit_id",
        "film_id",
        "shot_id",
        "t_start",
        "t_end",
        "keyframe_paths",
    }
    missing_columns = required.difference(units.column_names)
    if missing_columns:
        raise ValueError(
            "units table cannot backfill frames; missing columns: "
            + ", ".join(sorted(missing_columns))
        )

    sources: list[_FrameSource] = []
    missing_paths: list[Path] = []
    for unit in units.select(sorted(required)).to_pylist():
        if film_id is not None and unit["film_id"] != film_id:
            continue

        paths = _decode_keyframe_paths(unit["keyframe_paths"], unit["unit_id"])
        count = len(paths)
        if count == 0:
            continue

        t_start = float(unit["t_start"])
        t_end = float(unit["t_end"])
        if not math.isfinite(t_start) or not math.isfinite(t_end) or t_end <= t_start:
            raise ValueError(
                f"unit {unit['unit_id']!r} has invalid time range "
                f"[{t_start}, {t_end}]"
            )

        for frame_index, path_text in enumerate(paths):
            path = Path(path_text)
            if not path.is_file():
                missing_paths.append(path)
                continue
            stat = path.stat()
            timestamp = _reconstruct_seek_time(
                t_start,
                t_end,
                frame_index=frame_index,
                frame_count=count,
            )
            sources.append(
                _FrameSource(
                    frame_id=f"{unit['unit_id']}::frame::{frame_index}",
                    film_id=str(unit["film_id"]),
                    unit_id=str(unit["unit_id"]),
                    shot_id=str(unit["shot_id"]),
                    frame_index=frame_index,
                    timestamp=timestamp,
                    path=path,
                    is_representative=frame_index == count // 2,
                    source_size=stat.st_size,
                    source_mtime_ns=stat.st_mtime_ns,
                )
            )

    if missing_paths:
        sample = ", ".join(str(path) for path in missing_paths[:3])
        suffix = "" if len(missing_paths) <= 3 else f" (+{len(missing_paths) - 3} more)"
        raise FileNotFoundError(
            f"{len(missing_paths)} indexed keyframe path(s) are missing: "
            f"{sample}{suffix}"
        )

    return sorted(
        sources,
        key=lambda item: (item.film_id, item.unit_id, item.frame_index),
    )


def _decode_keyframe_paths(raw: Any, unit_id: str) -> list[str]:
    try:
        decoded = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError(
            f"unit {unit_id!r} has invalid keyframe_paths JSON"
        ) from exc
    if not isinstance(decoded, list) or not all(
        isinstance(path, str) and path for path in decoded
    ):
        raise ValueError(
            f"unit {unit_id!r} keyframe_paths must encode a list of paths"
        )
    return decoded


def _reconstruct_seek_time(
    t_start: float,
    t_end: float,
    *,
    frame_index: int,
    frame_count: int,
) -> float:
    """Reconstruct the seek requested by the legacy extractor."""
    nominal = t_start + (frame_index + 1) * (t_end - t_start) / (frame_count + 1)
    if frame_index == 0:
        return max(nominal, t_start + _KEYFRAME_START_PAD)
    return nominal


def _existing_frame_metadata(table: pa.Table) -> dict[str, dict[str, Any]]:
    columns = {
        "frame_id",
        "schema_version",
        "timestamp",
        "path",
        "source_size",
        "source_mtime_ns",
        "visual_encoder",
    }
    if not columns.issubset(table.column_names):
        return {}
    return {
        str(row["frame_id"]): row
        for row in table.select(sorted(columns)).to_pylist()
    }


def _is_current(
    source: _FrameSource,
    existing: dict[str, Any] | None,
    *,
    visual_encoder: str,
) -> bool:
    if existing is None:
        return False
    return (
        existing["schema_version"] == FRAMES_SCHEMA_VERSION
        and existing["visual_encoder"] == visual_encoder
        and existing["path"] == str(source.path)
        and existing["source_size"] == source.source_size
        and existing["source_mtime_ns"] == source.source_mtime_ns
        and math.isclose(
            float(existing["timestamp"]),
            source.timestamp,
            rel_tol=0.0,
            abs_tol=1e-9,
        )
    )


def _validate_frames_table(schema: pa.Schema, vector_dim: int) -> None:
    expected = make_frames_schema(vector_dim)
    try:
        vector_type = schema.field("visual_vec").type
        version_type = schema.field("schema_version").type
    except KeyError as exc:
        raise ValueError(
            "existing frames table is not schema version 1; migrate or remove "
            "that derived table before backfilling"
        ) from exc
    if not pa.types.is_fixed_size_list(vector_type):
        raise ValueError("frames.visual_vec must be a fixed-size list")
    if vector_type.list_size != vector_dim:
        raise ValueError(
            "frames.visual_vec dimension does not match configured encoder: "
            f"table={vector_type.list_size}, configured={vector_dim}"
        )
    if not pa.types.is_int16(version_type):
        raise ValueError("frames.schema_version must be int16")
    if not schema.equals(expected, check_metadata=False):
        raise ValueError(
            "existing frames table does not match schema version 1; migrate "
            "or remove that derived table before backfilling"
        )
