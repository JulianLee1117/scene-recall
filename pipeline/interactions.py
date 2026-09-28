"""Append-only log of search interactions: the raw material for a later personal taste model.

Searches, plays and saves are user-authored signals, so they live beside
bookmarks in ``state_dir`` (never in the replaceable ``assets_dir``) and stay
on this machine. Nothing reads them for ranking yet (docs/current-work.md,
Phase 4); the log only has to be complete, compact and stable.
"""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import threading
import time
from typing import Any
from uuid import uuid4

INTERACTIONS_DATABASE_NAME = "interactions.sqlite3"
KINDS = frozenset({"search", "play", "save", "unsave", "place", "replace"})
_SCHEMA_VERSION = 1
_MAX_TEXT = 500
_MAX_CONTEXT = 4_000


class InteractionLog:
    def __init__(self, state_dir: Path) -> None:
        self.path = Path(state_dir) / INTERACTIONS_DATABASE_NAME
        self._lock = threading.Lock()

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if version == 0:
                connection.execute("""
                    CREATE TABLE events (
                        event_id TEXT PRIMARY KEY,
                        created_at_ms INTEGER NOT NULL,
                        kind TEXT NOT NULL,
                        film_id TEXT,
                        unit_id TEXT,
                        t REAL,
                        query TEXT,
                        preset TEXT,
                        rank INTEGER,
                        context TEXT
                    )""")
                connection.execute("CREATE INDEX events_by_time ON events (created_at_ms)")
                connection.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
            elif version != _SCHEMA_VERSION:
                raise RuntimeError(f"unsupported interaction log schema {version}; expected {_SCHEMA_VERSION}")

    def record(self, kind: str, *, film_id: str | None = None, unit_id: str | None = None, t: float | None = None,
               query: str | None = None, preset: str | None = None, rank: int | None = None,
               context: dict[str, Any] | None = None) -> str:
        if kind not in KINDS:
            raise ValueError(f"unknown interaction kind {kind!r}")
        encoded = json.dumps(context, ensure_ascii=False, sort_keys=True) if context else None
        if encoded is not None and len(encoded) > _MAX_CONTEXT:
            raise ValueError("interaction context is too large")
        event_id = uuid4().hex
        row = (event_id, int(time.time() * 1000), kind, film_id, unit_id, t,
               (query or None) and query[:_MAX_TEXT], preset, rank, encoded)
        with self._lock, self._connect() as connection:
            connection.execute("INSERT INTO events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", row)
        return event_id

    def recent(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute("SELECT * FROM events ORDER BY created_at_ms DESC LIMIT ?", (limit,)).fetchall()
        return [dict(row) for row in rows]

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=5.0, isolation_level=None)
