"""Worker management must never cancel, steal or replay durable jobs."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import threading
import time
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from pipeline.lab.api import router
from pipeline.lab.store import LabStore
from pipeline.lab.worker_control import WorkerControl, worker_status, request_worker_stop


@pytest.fixture
def store(config):
    value = LabStore(config.paths.state_dir)
    value.initialize()
    return value


def test_status_without_worker_table_is_read_only(store):
    before = store.path.read_bytes()
    status = worker_status(store)
    assert [(row["role"], row["state"]) for row in status["workers"]] == [("editor", "offline"), ("ingest", "offline")]
    assert request_worker_stop(store) == {"requested": []}
    assert store.path.read_bytes() == before
    with store.connection() as con:
        assert con.execute("SELECT name FROM sqlite_master WHERE name='worker_controls'").fetchone() is None


def test_stopping_editor_preserves_active_job_and_other_lane(store, tmp_path):
    project = store.create_project("Edit", "music-sketch")
    editor_job = store.enqueue("render", project["id"], project["revision"])
    ingest_job = store.enqueue("ingest", path=tmp_path / "film.mkv")
    assert store.claim("editor")["id"] == editor_job["id"]
    editor_stop, ingest_stop = threading.Event(), threading.Event()
    with WorkerControl(store, "editor", editor_stop) as editor, WorkerControl(store, "ingest", ingest_stop) as ingest:
        editor.set_job(editor_job["id"])
        statuses = {row["role"]: row for row in worker_status(store)["workers"]}
        assert statuses["editor"]["state"] == "busy"
        assert statuses["ingest"]["queued_count"] == 1
        assert request_worker_stop(store, "editor") == {"requested": ["editor"]}
        assert editor.check_stop() is True
        assert ingest.check_stop() is False
        editor._heartbeat()
        assert worker_status(store)["workers"][0]["state"] == "stopping"
        assert store.get_job(editor_job["id"])["status"] == "running"
        assert store.get_job(editor_job["id"])["cancel_requested"] is False
        assert store.get_job(ingest_job["id"])["status"] == "queued"
    assert all(row["state"] == "offline" for row in worker_status(store)["workers"])


def test_stale_heartbeat_cannot_recover_or_replay_running_work(store):
    project = store.create_project("Edit", "music-sketch")
    job = store.enqueue("render", project["id"], project["revision"])
    store.claim("editor")
    with WorkerControl(store, "editor", threading.Event()) as control:
        control.set_job(job["id"])
        with store.connection() as con:
            con.execute("UPDATE worker_controls SET heartbeat_at=? WHERE role='editor'", (time.time() - 100,))
        assert worker_status(store)["workers"][0]["state"] == "offline"
        assert store.get_job(job["id"])["status"] == "running"
        assert request_worker_stop(store, "editor") == {"requested": []}


def test_old_control_cannot_clear_a_replacement_registration(store):
    stop = threading.Event()
    with WorkerControl(store, "editor", stop) as control:
        with store.connection() as con:
            con.execute("UPDATE worker_controls SET token='replacement' WHERE role='editor'")
        assert control.check_stop() is True
    with store.connection() as con:
        row = con.execute("SELECT stopped_at,token FROM worker_controls WHERE role='editor'").fetchone()
        assert row["token"] == "replacement" and row["stopped_at"] is None


def test_serial_worker_status_and_role_stop_are_honest(store):
    stop = threading.Event()
    with WorkerControl(store, "all", stop) as control:
        assert all(row["mode"] == "serial" for row in worker_status(store)["workers"])
        assert request_worker_stop(store, "editor") == {"requested": ["all"]}
        assert control.check_stop() is True


def test_worker_api_reports_independent_lanes_without_job_mutation(store):
    app = FastAPI()
    app.state.lab = store
    app.include_router(router)
    with WorkerControl(store, "editor", threading.Event()):
        with TestClient(app) as client:
            response = client.get("/lab/workers")
        assert response.status_code == 200
        editor, ingest = response.json()["workers"]
        assert editor["online"] and editor["mode"] == "separate"
        assert not ingest["online"]
        assert all("token" not in row for row in response.json()["workers"])


def test_new_role_registration_retires_recent_dead_serial_heartbeat(store):
    with WorkerControl(store, "all", threading.Event()):
        pass
    with store.connection() as con:
        con.execute("UPDATE worker_controls SET stopped_at=NULL WHERE role='all'")
    with WorkerControl(store, "editor", threading.Event()):
        editor, ingest = worker_status(store)["workers"]
        assert editor["online"] and editor["mode"] == "separate"
        assert not ingest["online"]


def test_stop_committed_while_closing_is_observed_without_timer_poll(store):
    stop = threading.Event()
    with WorkerControl(store, "editor", stop):
        assert request_worker_stop(store, "editor") == {"requested": ["editor"]}
        assert not stop.is_set()
    assert stop.is_set()


def test_stop_selection_and_update_cannot_be_split_by_reload_shutdown(store, monkeypatch):
    stop = threading.Event()
    control = WorkerControl(store, "editor", stop).__enter__()
    connection = store.connection
    selected, release, closing, closed = (threading.Event() for _ in range(4))
    closing_thread = []

    @contextmanager
    def paused_connection():
        with connection() as con:
            def execute(sql, *args):
                if sql == "BEGIN IMMEDIATE" and closing_thread == [threading.get_ident()]:
                    closing.set()
                cursor = con.execute(sql, *args)
                if sql.startswith("SELECT role FROM worker_controls WHERE role IN"):
                    def fetchall():
                        rows = cursor.fetchall()
                        selected.set()
                        assert release.wait(5), "Test did not release the stop transaction"
                        return rows
                    return SimpleNamespace(fetchall=fetchall)
                return cursor
            yield SimpleNamespace(execute=execute)

    def close_worker():
        closing_thread.append(threading.get_ident())
        control.__exit__(None, None, None)
        closed.set()

    monkeypatch.setattr(store, "connection", paused_connection)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            request = pool.submit(request_worker_stop, store, "editor")
            try:
                assert selected.wait(5)
                close = pool.submit(close_worker)
                assert closing.wait(5)
                # The stop lookup already selected an active worker. Shutdown
                # must wait for that same transaction's stop update to commit.
                assert not closed.wait(.1)
            finally:
                release.set()
            assert request.result(timeout=5) == {"requested": ["editor"]}
            close.result(timeout=5)
        assert stop.is_set()
        with connection() as con:
            row = con.execute("SELECT stop_requested,stopped_at FROM worker_controls WHERE role='editor'").fetchone()
        assert row["stop_requested"] == 1 and row["stopped_at"] is not None
    finally:
        release.set()
        if not closed.is_set():
            control.__exit__(None, None, None)
