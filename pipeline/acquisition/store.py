"""Durable acquisition intent and recovery journals, independent of Lab jobs."""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path


class AcquisitionConflict(ValueError):
    """The requested action no longer matches the saved acquisition."""


TERMINAL = frozenset({"ready", "failed", "cancelled"})
STATUSES = TERMINAL | {
    "queued", "downloading", "validating", "needs_review", "importing",
    "ingest_queued", "ingesting", "cleanup", "cancelling",
}


class AcquisitionStore:
    def __init__(self, state_dir: Path):
        self.root = Path(state_dir) / "acquisition"
        self.path = self.root / "acquisition.sqlite3"

    @contextmanager
    def connection(self):
        con = sqlite3.connect(self.path, timeout=10)
        con.row_factory = sqlite3.Row
        try:
            with con:
                yield con
        finally:
            con.close()

    def initialize(self):
        self.root.mkdir(parents=True, exist_ok=True)
        with self.connection() as con:
            version = con.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise RuntimeError("Unsupported acquisition database version")
            con.execute("PRAGMA journal_mode=WAL")
            con.executescript("""
                CREATE TABLE IF NOT EXISTS acquisitions (
                    id TEXT PRIMARY KEY, info_hash TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL, created_at REAL NOT NULL,
                    document TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS acquisition_active ON acquisitions(status,created_at);
                CREATE TABLE IF NOT EXISTS releases (
                    id TEXT PRIMARY KEY, expires_at REAL NOT NULL, document TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS monitor (
                    name TEXT PRIMARY KEY, last_seen REAL NOT NULL);
                PRAGMA user_version=1;
            """)

    def create(self, info_hash: str, *, title: str, year: int, edition: str,
               source: dict) -> dict:
        now, identity = time.time(), uuid.uuid4().hex
        item = dict(id=identity, revision=1, info_hash=info_hash, title=title,
                    year=year, edition=edition, source=source, status="queued",
                    message="Waiting for the download monitor", error=None,
                    progress=None, download_rate=None, eta_seconds=None,
                    bytes_total=None, bytes_done=None, ingest_job_id=None,
                    film_path=None, film_id=None, review=None,
                    cancel_requested=False, cancellation_cleanup=None,
                    created_at=now, updated_at=now)
        try:
            with self.connection() as con:
                con.execute("BEGIN IMMEDIATE")
                count = con.execute("SELECT count(*) FROM acquisitions WHERE status NOT IN ('ready','failed','cancelled')").fetchone()[0]
                if count >= 100:
                    raise AcquisitionConflict("The acquisition queue already has 100 active items")
                con.execute("INSERT INTO acquisitions VALUES (?,?,?,?,?)",
                            (identity, info_hash, "queued", now, json.dumps(item)))
        except sqlite3.IntegrityError:
            raise AcquisitionConflict("This torrent is already in the acquisition queue; use its existing entry") from None
        return item

    def get(self, identity: str) -> dict:
        with self.connection() as con:
            row = con.execute("SELECT document FROM acquisitions WHERE id=?", (identity,)).fetchone()
        if row is None:
            raise KeyError("Acquisition not found")
        return json.loads(row[0])

    def list(self, *, active=False, limit=100) -> list[dict]:
        with self.connection() as con:
            if active:
                rows = con.execute("SELECT document FROM acquisitions WHERE status NOT IN ('ready','failed','cancelled') ORDER BY created_at,id LIMIT ?", (limit,))
            else:
                rows = con.execute("SELECT document FROM acquisitions ORDER BY status IN ('ready','failed','cancelled'),created_at DESC,id LIMIT ?", (limit,))
            return [json.loads(row[0]) for row in rows]

    def patch(self, identity: str, *, revision: int | None = None, **changes) -> dict:
        with self.connection() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT document FROM acquisitions WHERE id=?", (identity,)).fetchone()
            if row is None:
                raise KeyError("Acquisition not found")
            current = json.loads(row[0])
            if revision is not None and current["revision"] != revision:
                raise AcquisitionConflict("Acquisition changed; refresh and try again")
            if changes.get("status", current["status"]) not in STATUSES:
                raise ValueError("Unknown acquisition status")
            if ((current.get("cancel_requested") or current["status"] == "cancelling")
                    and changes.get("status") != "cancelled"):
                # A monitor operation may finish after the API accepted Cancel.
                # Keep its side-effect journal (import plan, canonical path,
                # ingest job id), but never let a stale stage update hide the
                # durable cancellation or replace its original recovery stage.
                if changes.get("status", current["status"]) != "cancelling":
                    for key in ("message", "error", "resume_status"):
                        changes.pop(key, None)
                    for key in ("film_path", "film_id", "ingest_job_id", "import_plan"):
                        if changes.get(key, current.get(key)) is None and current.get(key) is not None:
                            changes.pop(key, None)
                changes.update(status="cancelling", cancel_requested=True,
                               cancellation_cleanup="pending", retry_requested=False)
            next_status = changes.get("status", current["status"])
            if current["status"] in TERMINAL and next_status not in TERMINAL | {"cancelling"}:
                count = con.execute("SELECT count(*) FROM acquisitions WHERE status NOT IN ('ready','failed','cancelled')").fetchone()[0]
                if count >= 100:
                    raise AcquisitionConflict("The acquisition queue already has 100 active items")
            forbidden = {"id", "info_hash", "created_at", "revision"} & changes.keys()
            if forbidden:
                raise ValueError("Acquisition identity is immutable")
            current.update(changes)
            current.update(revision=current["revision"] + 1, updated_at=time.time())
            con.execute("UPDATE acquisitions SET status=?,document=? WHERE id=?",
                        (current["status"], json.dumps(current, allow_nan=False), identity))
        return current

    def delete(self, identity: str, revision: int) -> None:
        """Forget a stopped acquisition; its info_hash becomes reusable."""
        with self.connection() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT document FROM acquisitions WHERE id=?", (identity,)).fetchone()
            if row is None:
                raise KeyError("Acquisition not found")
            current = json.loads(row[0])
            if current["revision"] != revision:
                raise AcquisitionConflict("Acquisition changed; refresh and try again")
            if current["status"] not in TERMINAL:
                raise AcquisitionConflict("Only a stopped acquisition can be dismissed")
            con.execute("DELETE FROM acquisitions WHERE id=?", (identity,))

    def save_releases(self, candidates: list[dict]) -> list[dict]:
        now, public = time.time(), []
        with self.connection() as con:
            con.execute("DELETE FROM releases WHERE expires_at < ?", (now,))
            for candidate in candidates[:50]:
                identity = uuid.uuid4().hex
                con.execute("INSERT INTO releases VALUES (?,?,?)",
                            (identity, now + 1800, json.dumps(candidate, allow_nan=False)))
                public.append({key: candidate.get(key) for key in ("title", "size", "seeders", "indexer")} | {"id": identity})
            # Bound transient provider results even during repeated searches.
            con.execute("DELETE FROM releases WHERE id NOT IN (SELECT id FROM releases ORDER BY expires_at DESC LIMIT 1000)")
        return public

    def release(self, identity: str) -> dict:
        with self.connection() as con:
            row = con.execute("SELECT document FROM releases WHERE id=? AND expires_at>?", (identity, time.time())).fetchone()
        if row is None:
            raise AcquisitionConflict("Release selection expired; search again")
        return json.loads(row[0])

    def heartbeat(self):
        with self.connection() as con:
            con.execute("INSERT INTO monitor VALUES ('worker',?) ON CONFLICT(name) DO UPDATE SET last_seen=excluded.last_seen", (time.time(),))

    def monitor_status(self):
        with self.connection() as con:
            row = con.execute("SELECT last_seen FROM monitor WHERE name='worker'").fetchone()
        stamp = row[0] if row else None
        return {"running": bool(stamp and time.time() - stamp < 90), "last_seen": stamp}

    @staticmethod
    def public(item: dict) -> dict:
        # Private source URLs, auth-bearing trackers and recovery manifests never
        # enter browser responses or CLI output.
        keys = ("id", "revision", "title", "year", "edition", "status", "message",
                "error", "progress", "download_rate", "eta_seconds", "bytes_total",
                "bytes_done", "ingest_job_id", "film_path", "film_id", "review",
                "cancel_requested", "cancellation_cleanup", "created_at", "updated_at")
        return {key: item.get(key) for key in keys}
