"""Local worker visibility and graceful stop requests; file locks own execution.

Heartbeats are diagnostics, never leases or permission to replay a job. This
small control table shares the existing durable ledger and needs no service.
"""
from __future__ import annotations

import os
import threading
import time
import uuid

from pipeline.lab.job_roles import WORKER_ROLES, role_for_kind


HEARTBEAT_SECONDS = 2.0
OFFLINE_AFTER_SECONDS = 15.0


def _initialize(store):
    with store.connection() as con:
        con.execute("""CREATE TABLE IF NOT EXISTS worker_controls (
            role TEXT PRIMARY KEY, token TEXT NOT NULL, pid INTEGER NOT NULL,
            started_at REAL NOT NULL, heartbeat_at REAL NOT NULL,
            stopped_at REAL, active_job_id TEXT,
            stop_requested INTEGER NOT NULL DEFAULT 0
        )""")


class WorkerControl:
    """Use only while holding the worker's lifetime ownership locks."""

    def __init__(self, store, role, stop):
        if role not in (*WORKER_ROLES, "all"):
            raise ValueError(f"Unknown worker role: {role}")
        self.store, self.role, self.stop = store, role, stop
        self.token = uuid.uuid4().hex
        self.closed = threading.Event()
        self.thread = None

    def __enter__(self):
        _initialize(self.store)
        now = time.time()
        with self.store.connection() as con:
            # Lifetime locks prove these conflicting registrations cannot still
            # execute. Do not let a dead process's recent heartbeat mask us.
            if self.role == "all":
                con.execute("UPDATE worker_controls SET stopped_at=COALESCE(stopped_at,?) WHERE role IN ('editor','ingest')", (now,))
            else:
                con.execute("UPDATE worker_controls SET stopped_at=COALESCE(stopped_at,?) WHERE role='all'", (now,))
            con.execute("""INSERT INTO worker_controls
                (role,token,pid,started_at,heartbeat_at,stopped_at,active_job_id,stop_requested)
                VALUES (?,?,?,?,?,NULL,NULL,0)
                ON CONFLICT(role) DO UPDATE SET token=excluded.token,pid=excluded.pid,
                started_at=excluded.started_at,heartbeat_at=excluded.heartbeat_at,
                stopped_at=NULL,active_job_id=NULL,stop_requested=0""",
                (self.role, self.token, os.getpid(), now, now))
        self.thread = threading.Thread(target=self._watch, name=f"{self.role}-worker-control", daemon=True)
        self.thread.start()
        return self

    def _heartbeat(self):
        with self.store.connection() as con:
            row = con.execute("SELECT stop_requested FROM worker_controls WHERE role=? AND token=?",
                              (self.role, self.token)).fetchone()
            if row is None:
                self.stop.set()
                return
            if row["stop_requested"]:
                self.stop.set()
            con.execute("UPDATE worker_controls SET heartbeat_at=?,stop_requested=MAX(stop_requested,?) WHERE role=? AND token=?",
                        (time.time(), int(self.stop.is_set()), self.role, self.token))

    def check_stop(self):
        """Check persisted stop intent at every claim boundary, not only by timer."""
        with self.store.connection() as con:
            row = con.execute("SELECT stop_requested FROM worker_controls WHERE role=? AND token=?",
                              (self.role, self.token)).fetchone()
        if row is None or row["stop_requested"]:
            self.stop.set()
        return self.stop.is_set()

    def _watch(self):
        reported = False
        while not self.closed.wait(HEARTBEAT_SECONDS):
            try:
                self._heartbeat()
                reported = False
            except Exception as exc:
                # A temporary busy database is not evidence that another process
                # may take ownership. Lifetime locks and normal recovery decide.
                if not reported:
                    print(f"{self.role.capitalize()} worker status update unavailable: {type(exc).__name__}", flush=True)
                    reported = True

    def set_job(self, identity):
        with self.store.connection() as con:
            con.execute("UPDATE worker_controls SET active_job_id=?,heartbeat_at=? WHERE role=? AND token=?",
                        (identity, time.time(), self.role, self.token))

    def __exit__(self, *_):
        self.closed.set()
        if self.thread is not None:
            self.thread.join(timeout=HEARTBEAT_SECONDS + 1)
        with self.store.connection() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT stop_requested FROM worker_controls WHERE role=? AND token=?",
                              (self.role, self.token)).fetchone()
            if row and row["stop_requested"]:
                self.stop.set()
            con.execute("UPDATE worker_controls SET stopped_at=?,active_job_id=NULL WHERE role=? AND token=?",
                        (time.time(), self.role, self.token))


def worker_status(store):
    """Read status without creating tables, claiming work or inspecting secrets."""
    now = time.time()
    controls, jobs = {}, []
    if store.path.is_file():
        with store.connection() as con:
            tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "worker_controls" in tables:
                controls = {row["role"]: dict(row) for row in con.execute("SELECT * FROM worker_controls")}
            if "jobs" in tables:
                jobs = [dict(row) for row in con.execute("""SELECT id,kind,status,progress,created_at,started_at
                    FROM jobs WHERE status IN ('queued','running','waiting_worker')
                    ORDER BY CASE WHEN kind='backfill-temporal' THEN 1 ELSE 0 END,created_at,rowid""")]

    def alive(control):
        return bool(control and control["stopped_at"] is None
                    and now - control["heartbeat_at"] <= OFFLINE_AFTER_SECONDS)

    serial = controls.get("all")
    rows = []
    for role in WORKER_ROLES:
        control = serial if alive(serial) else controls.get(role)
        online = alive(control)
        queued = [job for job in jobs if role_for_kind(job["kind"]) == role and job["status"] != "running"]
        current = next((job for job in jobs if job["status"] == "running"
                        and control and job["id"] == control["active_job_id"]), None)
        state = "offline" if not online else "stopping" if control["stop_requested"] else "busy" if current else "idle"
        rows.append({"role": role, "state": state, "online": online,
                     "mode": "serial" if online and control["role"] == "all" else "separate",
                     "pid": control["pid"] if online else None,
                     "heartbeat_at": control["heartbeat_at"] if control else None,
                     "current_job": current, "queued_count": len(queued)})
    return {"workers": rows, "checked_at": now}


def request_worker_stop(store, role=None):
    """Finish active jobs, then stop; pending jobs remain saved for next startup."""
    if role not in (None, "all", *WORKER_ROLES):
        raise ValueError(f"Unknown worker role: {role}")
    if not store.path.is_file():
        return {"requested": []}
    with store.connection() as con:
        con.execute("BEGIN IMMEDIATE")
        if con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='worker_controls'").fetchone() is None:
            return {"requested": []}
        roles = (*WORKER_ROLES, "all") if role in (None, "all") else (role, "all")
        placeholders = ",".join("?" for _ in roles)
        rows = con.execute(f"SELECT role FROM worker_controls WHERE role IN ({placeholders}) AND stopped_at IS NULL AND heartbeat_at>=?",
                           (*roles, time.time() - OFFLINE_AFTER_SECONDS)).fetchall()
        requested = [row["role"] for row in rows]
        for name in requested:
            con.execute("UPDATE worker_controls SET stop_requested=1 WHERE role=?", (name,))
    return {"requested": requested}


def format_worker_status(status):
    lines = []
    for worker in status["workers"]:
        line = f"{worker['role'].capitalize()}: {worker['state']} | {worker['queued_count']} queued"
        if worker["pid"]:
            line += f" | PID {worker['pid']}"
        if worker["mode"] == "serial":
            line += " | shared serial worker"
        current = worker["current_job"]
        if current:
            line += f"\n  {current['kind']} {current['id']}: {current['progress'] or 'Starting'}"
        lines.append(line)
    return "\n".join(lines)
