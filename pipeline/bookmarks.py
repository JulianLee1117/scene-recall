"""Durable local storage for user-saved film moments.

Bookmarks are user-authored state, not a search-index derivation.  They live
in a small SQLite database outside ``assets_dir`` and retain a film/timestamp
anchor even when a later index generation changes unit identifiers.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
import sqlite3
import time
from uuid import uuid4


BOOKMARK_DATABASE_NAME = "scene-recall.sqlite3"
_SCHEMA_VERSION = 2


@dataclass(frozen=True)
class Bookmark:
    """One saved source moment and its original derived locator."""

    bookmark_id: str
    film_id: str
    source_unit_id: str
    evidence_timestamp_ms: int
    frame_index: int | None
    film_title_snapshot: str
    created_at_ms: int

    @property
    def evidence_timestamp(self) -> float:
        """Return the durable source timestamp in API-compatible seconds."""
        return self.evidence_timestamp_ms / 1000.0


class BookmarkStore:
    """SQLite-backed bookmark repository with explicit schema versioning."""

    def __init__(self, state_dir: Path) -> None:
        self.path = state_dir / BOOKMARK_DATABASE_NAME

    def initialize(self) -> None:
        """Create or validate the current bookmark schema."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if version == 0:
                _create_schema_v2(connection)
                connection.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
            elif version == 1:
                _migrate_v1_to_v2(connection)
            elif version != _SCHEMA_VERSION:
                raise RuntimeError(
                    "unsupported bookmark database schema "
                    f"{version}; expected {_SCHEMA_VERSION}"
                )

    def save(
        self,
        *,
        film_id: str,
        source_unit_id: str,
        evidence_timestamp: float,
        frame_index: int | None,
        film_title_snapshot: str,
    ) -> Bookmark:
        """Idempotently save one exact source moment."""
        if not film_id or not source_unit_id:
            raise ValueError("film_id and source_unit_id are required")
        if not math.isfinite(evidence_timestamp) or evidence_timestamp < 0:
            raise ValueError("evidence_timestamp must be finite and non-negative")
        if frame_index is not None and frame_index < 0:
            raise ValueError("frame_index cannot be negative")

        timestamp_ms = round(evidence_timestamp * 1000)
        bookmark_id = uuid4().hex
        created_at_ms = time.time_ns() // 1_000_000
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO bookmarks (
                    bookmark_id,
                    film_id,
                    source_unit_id,
                    evidence_timestamp_ms,
                    frame_index,
                    film_title_snapshot,
                    created_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (
                    film_id,
                    evidence_timestamp_ms
                ) DO UPDATE SET
                    source_unit_id = excluded.source_unit_id,
                    frame_index = excluded.frame_index,
                    film_title_snapshot = excluded.film_title_snapshot
                """,
                (
                    bookmark_id,
                    film_id,
                    source_unit_id,
                    timestamp_ms,
                    frame_index,
                    film_title_snapshot,
                    created_at_ms,
                ),
            )
            row = connection.execute(
                """
                SELECT * FROM bookmarks
                WHERE film_id = ?
                  AND evidence_timestamp_ms = ?
                """,
                (film_id, timestamp_ms),
            ).fetchone()
        if row is None:  # pragma: no cover - SQLite statement invariant
            raise RuntimeError("bookmark save did not return its durable row")
        return _bookmark_from_row(row)

    def list_all(self) -> list[Bookmark]:
        """Return every bookmark, newest first."""
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM bookmarks
                ORDER BY created_at_ms DESC, bookmark_id
                """
            ).fetchall()
        return [_bookmark_from_row(row) for row in rows]

    def delete(self, bookmark_id: str) -> bool:
        """Delete one bookmark and report whether it existed."""
        if not bookmark_id:
            return False
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM bookmarks WHERE bookmark_id = ?",
                (bookmark_id,),
            )
        return cursor.rowcount == 1

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection


def _create_schema_v2(connection: sqlite3.Connection) -> None:
    """Create the current table and its presentation-order index."""
    connection.execute(
        """
        CREATE TABLE bookmarks (
            bookmark_id TEXT PRIMARY KEY,
            film_id TEXT NOT NULL,
            source_unit_id TEXT NOT NULL,
            evidence_timestamp_ms INTEGER NOT NULL
                CHECK (evidence_timestamp_ms >= 0),
            frame_index INTEGER
                CHECK (frame_index IS NULL OR frame_index >= 0),
            film_title_snapshot TEXT NOT NULL,
            created_at_ms INTEGER NOT NULL,
            UNIQUE (film_id, evidence_timestamp_ms)
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX bookmarks_created_at_idx
        ON bookmarks (created_at_ms DESC, bookmark_id)
        """
    )


def _migrate_v1_to_v2(connection: sqlite3.Connection) -> None:
    """Replace derived-unit uniqueness with one durable film/time anchor.

    Version 1 could contain the same film moment more than once after a unit-ID
    change.  Preserve the oldest bookmark identity and creation time while
    retaining the newest row's locator, frame, and title hints.  Bookmark IDs
    make ties deterministic.
    """
    connection.execute("ALTER TABLE bookmarks RENAME TO bookmarks_v1")
    rows = connection.execute(
        """
        SELECT * FROM bookmarks_v1
        ORDER BY
            film_id,
            evidence_timestamp_ms,
            created_at_ms,
            bookmark_id
        """
    ).fetchall()

    # The v1 index retains its global name after the table rename.  Dropping
    # it before creating v2 avoids an index-name collision inside the atomic
    # migration transaction.
    connection.execute("DROP INDEX IF EXISTS bookmarks_created_at_idx")
    _create_schema_v2(connection)

    grouped: dict[tuple[str, int], list[sqlite3.Row]] = {}
    for row in rows:
        key = (str(row["film_id"]), int(row["evidence_timestamp_ms"]))
        grouped.setdefault(key, []).append(row)

    migrated_rows = []
    for anchor_rows in grouped.values():
        identity = anchor_rows[0]
        latest_hints = anchor_rows[-1]
        migrated_rows.append(
            (
                str(identity["bookmark_id"]),
                str(identity["film_id"]),
                str(latest_hints["source_unit_id"]),
                int(identity["evidence_timestamp_ms"]),
                (
                    int(latest_hints["frame_index"])
                    if latest_hints["frame_index"] is not None
                    else None
                ),
                str(latest_hints["film_title_snapshot"]),
                int(identity["created_at_ms"]),
            )
        )

    connection.executemany(
        """
        INSERT INTO bookmarks (
            bookmark_id,
            film_id,
            source_unit_id,
            evidence_timestamp_ms,
            frame_index,
            film_title_snapshot,
            created_at_ms
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        migrated_rows,
    )
    connection.execute("DROP TABLE bookmarks_v1")
    connection.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")


def _bookmark_from_row(row: sqlite3.Row) -> Bookmark:
    return Bookmark(
        bookmark_id=str(row["bookmark_id"]),
        film_id=str(row["film_id"]),
        source_unit_id=str(row["source_unit_id"]),
        evidence_timestamp_ms=int(row["evidence_timestamp_ms"]),
        frame_index=(
            int(row["frame_index"])
            if row["frame_index"] is not None
            else None
        ),
        film_title_snapshot=str(row["film_title_snapshot"]),
        created_at_ms=int(row["created_at_ms"]),
    )
