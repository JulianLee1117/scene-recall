"""Concurrent local lanes share a ledger without sharing claims or recovery."""
from concurrent.futures import ThreadPoolExecutor
import os
import subprocess
import sys
import threading
import time
from unittest.mock import MagicMock

from filelock import FileLock, Timeout
import pytest

from pipeline.lab import worker, worker_runtime as runtime
from pipeline.lab.job_roles import ROLE_KINDS, kinds_for_role, role_for_kind
from pipeline.lab.store import LabStore
from pipeline.lab.worker_control import WorkerControl, request_worker_stop, worker_status
from pipeline.lab.worker_locks import worker_lock


def _queue(store, kinds):
    store.initialize()
    with store.connection() as con:
        for index, kind in enumerate(kinds):
            con.execute("INSERT INTO jobs (id,kind,status,snapshot,created_at) VALUES (?,?,?,?,?)",
                        (f"job-{index}", kind, "waiting_worker" if kind == "backfill-temporal" else "queued", "{}", 100.))


def test_explicit_routing_keeps_gpu_matching_with_ingestion_and_previews_with_editor():
    assert set(kinds_for_role()) == set(ROLE_KINDS["editor"] + ROLE_KINDS["ingest"])
    assert len(kinds_for_role()) == len(set(kinds_for_role()))
    for kind in ("ingest", "backfill-temporal", "match", "match-search"):
        assert role_for_kind(kind) == "ingest"
    for kind in ("generate", "rhythm", "analyze", "draft", "plan", "next-scene", "render",
                 "match-preview", "match-search-preview", "next-scene-preview"):
        assert role_for_kind(kind) == "editor"
    assert role_for_kind("future-unknown-job") is None
    with pytest.raises(ValueError):
        kinds_for_role("unknown")


def test_each_lane_claims_fifo_without_waiting_for_the_other_and_maintenance_stays_last(tmp_path):
    store = LabStore(tmp_path)
    _queue(store, ["backfill-temporal", "ingest", "generate", "match-search", "plan", "backfill-temporal", "render"])
    assert [store.claim("editor")["id"] for _ in range(3)] == ["job-2", "job-4", "job-6"]
    assert store.claim("editor") is None
    assert [store.claim("ingest")["id"] for _ in range(4)] == ["job-1", "job-3", "job-0", "job-5"]
    assert store.claim("ingest") is None
    assert store.get_job("job-1")["worker_role"] == "ingest"
    assert store.get_job("job-2")["worker_role"] == "editor"


def test_simultaneous_claims_never_duplicate_or_cross_roles(tmp_path):
    store = LabStore(tmp_path)
    _queue(store, ["ingest", "generate"] * 16)
    barrier = threading.Barrier(8)

    def claims(role):
        barrier.wait(timeout=5)
        found = []
        while job := store.claim(role):
            assert job["worker_role"] == role
            found.append(job["id"])
        return found

    with ThreadPoolExecutor(max_workers=8) as pool:
        groups = list(pool.map(claims, ["editor", "ingest"] * 4))
    identities = [identity for group in groups for identity in group]
    assert len(identities) == len(set(identities)) == 32


def test_restarting_one_lane_does_not_interrupt_another_lanes_active_job(tmp_path):
    store = LabStore(tmp_path)
    _queue(store, ["ingest", "generate", "plan", "backfill-temporal"])
    ingest = store.claim("ingest")
    editor = store.claim("editor")
    assert store.recover_interrupted("editor") == 1
    assert store.get_job(ingest["id"])["status"] == "running"
    assert store.get_job(editor["id"])["status"] == "interrupted"
    assert store.claim("editor")["id"] == "job-2"
    assert store.get_job("job-3")["status"] == "waiting_worker"


def test_two_role_locks_coexist_but_duplicate_and_legacy_workers_are_excluded(tmp_path):
    with worker_lock(tmp_path, "editor"), worker_lock(tmp_path, "ingest"):
        for role in ("editor", "ingest", "all"):
            with pytest.raises(Timeout), worker_lock(tmp_path, role):
                pytest.fail("A competing owner acquired a worker lock")
        with pytest.raises(Timeout), FileLock(tmp_path / ".worker.lock", timeout=0, preserve_lock_file=True):
            pytest.fail("A legacy worker acquired a live role's guard")
    with FileLock(tmp_path / ".worker.lock", timeout=0, preserve_lock_file=True):
        for role in ("editor", "ingest"):
            with pytest.raises(Timeout), worker_lock(tmp_path, role):
                pytest.fail("A role started during legacy ownership")
    with worker_lock(tmp_path, "editor"), worker_lock(tmp_path, "ingest"):
        pass  # All guards were released and their stable file identity survived.


def test_role_guard_also_excludes_a_legacy_worker_in_a_fresh_process(tmp_path):
    code = ("import sys; from filelock import FileLock, Timeout; "
            "lock=FileLock(sys.argv[1],timeout=0,preserve_lock_file=True)\n"
            "try: lock.acquire()\nexcept Timeout: sys.exit(7)\nelse: lock.release()\n")
    with worker_lock(tmp_path, "editor"):
        result = subprocess.run([sys.executable, "-c", code, str(tmp_path / ".worker.lock")],
                                capture_output=True, timeout=10)
    assert result.returncode == 7, result.stderr


@pytest.mark.parametrize("reload", [False, True])
def test_default_cli_starts_both_roles_with_one_config_and_matching_reload_mode(tmp_path, monkeypatch, reload):
    monkeypatch.setattr(worker.signal, "signal", lambda *_args: None)
    calls = []
    monkeypatch.setattr(worker, "supervise_roles", lambda commands, _stop, **options: calls.append((commands, options)) or 0)
    config_path = tmp_path / "config.yaml"
    args = ["--config", str(config_path)] + (["--reload"] if reload else [])
    assert worker.main(args) == 0
    commands, options = calls[0]
    assert options == {"reload": reload} and set(commands) == {"editor", "ingest"}
    for role, command in commands.items():
        assert command == [sys.executable, "-m", "pipeline.lab.worker", "--role", role,
                           "--reload-child" if reload else "--worker-child", "--config", str(config_path.resolve())]


def test_worker_dispatch_passes_role_and_leaves_another_lanes_running_job_untouched(config, tmp_path, monkeypatch):
    store = LabStore(config.paths.state_dir)
    _queue(store, ["ingest", "generate", "plan"])
    store.claim("ingest")
    calls = []

    def execute(job, _config, _db, ledger, *, role):
        calls.append((job["id"], role))
        status = worker_status(ledger)
        current = next(row for row in status["workers"] if row["role"] == role)
        assert current["current_job"]["id"] == job["id"]
        ledger.finish(job["id"])

    monkeypatch.setattr(worker, "execute_job", execute)
    worker.run_worker(config, once=True, db=MagicMock(), role="editor")
    assert calls == [("job-1", "editor")]
    assert store.get_job("job-0")["status"] == "running"
    assert store.get_job("job-2")["status"] == "queued"
    assert next(row for row in worker_status(store)["workers"] if row["role"] == "editor")["state"] == "offline"


def test_fixed_code_supervisor_does_not_restart_failed_child_or_wait_for_sources(monkeypatch):
    child = MagicMock(pid=123)
    child.wait.return_value = 3
    spawn = MagicMock(return_value=child)
    monkeypatch.setattr(runtime.subprocess, "Popen", spawn)
    monkeypatch.setattr(runtime, "stable_sources", lambda *_args, **_kwargs: pytest.fail("Fixed-code launch must not watch sources"))
    assert runtime.supervise(["worker"], threading.Event(), reload=False, role="editor") == 3
    assert spawn.call_count == 1 and child.stdin.close.called


def test_durable_role_stop_finishes_active_work_then_stops_before_another_claim(config, monkeypatch):
    store = LabStore(config.paths.state_dir)
    _queue(store, ["generate", "plan", "ingest"])
    calls = []

    def execute(job, _config, _db, ledger, *, role):
        calls.append(job["id"])
        assert request_worker_stop(ledger, role=role)["requested"] == [role]
        assert ledger.get_job(job["id"])["cancel_requested"] is False
        ledger.finish(job["id"])

    monkeypatch.setattr(worker, "execute_job", execute)
    worker.run_worker(config, db=MagicMock(), role="editor")
    assert calls == ["job-0"]
    assert store.get_job("job-0")["status"] == "completed"
    assert store.get_job("job-1")["status"] == store.get_job("job-2")["status"] == "queued"


def test_stop_during_source_scan_wins_over_reload_exit_and_leaves_queue_untouched(config, monkeypatch):
    store = LabStore(config.paths.state_dir)
    _queue(store, ["generate"])

    def changed(_fingerprint):
        request_worker_stop(store, role="editor")
        return True

    monkeypatch.setattr(worker, "source_changed", changed)
    assert worker.run_worker(config, db=MagicMock(), role="editor", reload_fingerprint="old") == 0
    assert store.get_job("job-0")["status"] == "queued"


def test_stop_committed_after_boundary_check_still_prevents_the_next_atomic_claim(config, monkeypatch):
    store = LabStore(config.paths.state_dir)
    _queue(store, ["generate"])
    claim = LabStore.claim
    execute = MagicMock()

    def stop_before_claim(ledger, role="all", **options):
        assert options.get("owner_token")
        request_worker_stop(ledger, role=role)
        return claim(ledger, role, **options)

    monkeypatch.setattr(LabStore, "claim", stop_before_claim)
    monkeypatch.setattr(worker, "execute_job", execute)
    worker.run_worker(config, once=True, db=MagicMock(), role="editor")
    execute.assert_not_called()
    assert store.get_job("job-0")["status"] == "queued"


def test_stop_committed_during_reload_shutdown_exits_instead_of_launching_a_replacement(config, monkeypatch):
    store = LabStore(config.paths.state_dir)
    _queue(store, ["generate"])
    close = WorkerControl.__exit__

    def stop_at_close(control, *args):
        request_worker_stop(control.store, role=control.role)
        return close(control, *args)

    monkeypatch.setattr(WorkerControl, "__exit__", stop_at_close)
    monkeypatch.setattr(worker, "source_changed", lambda _fingerprint: True)
    assert worker.run_worker(config, db=MagicMock(), role="editor", reload_fingerprint="old") == 0
    assert store.get_job("job-0")["status"] == "queued"


def test_two_role_supervisors_run_concurrently_and_one_failure_does_not_stop_the_other(monkeypatch):
    barrier = threading.Barrier(2)
    stop = threading.Event()
    calls = []

    def supervise(command, event, *, reload, role):
        barrier.wait(timeout=5)
        assert not event.is_set()
        calls.append((command, reload, role))
        return 2 if role == "ingest" else 0

    monkeypatch.setattr(runtime, "supervise", supervise)
    assert runtime.supervise_roles({"editor": ["edit"], "ingest": ["ingest"]}, stop, reload=True) == 2
    assert len(calls) == 2 and not stop.is_set()


def test_status_command_does_not_import_model_runtimes_or_create_state(config, tmp_path):
    code = ("import sys; from pipeline.lab.worker import main; "
            "assert main(['--status','--config',sys.argv[1]])==0; "
            "assert 'torch' not in sys.modules and 'lancedb' not in sys.modules")
    result = subprocess.run([sys.executable, "-c", code, str(tmp_path / "config.yaml")],
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert "Editor: offline" in result.stdout and "Ingest: offline" in result.stdout
    assert not LabStore(config.paths.state_dir).path.exists()


def test_default_launcher_runs_two_real_imported_workers_and_drains_both(config, tmp_path):
    """Real process/SQLite/Windows pipe + lock smoke, without model calls/jobs."""
    log = tmp_path / "lanes.log"
    options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    store = LabStore(config.paths.state_dir)
    with log.open("wb") as output:
        parent = subprocess.Popen([sys.executable, "-m", "pipeline.lab.worker", "--config", str(tmp_path / "config.yaml")],
                                  stdout=output, stderr=subprocess.STDOUT, **options)
        try:
            deadline = time.monotonic() + 30
            ready = False
            while time.monotonic() < deadline and parent.poll() is None:
                body = log.read_text(errors="replace")
                if "[editor] Lab worker ready:" in body and "[ingest] Lab worker ready:" in body:
                    ready = True
                    break
                time.sleep(.05)
            assert ready, log.read_text(errors="replace")
            status = worker_status(store)["workers"]
            assert all(row["online"] and row["state"] == "idle" for row in status)
            assert len({row["pid"] for row in status}) == 2
            assert set(request_worker_stop(store)["requested"]) == {"editor", "ingest"}
            assert parent.wait(timeout=15) == 0, log.read_text(errors="replace")
            assert all(row["state"] == "offline" for row in worker_status(store)["workers"])
            with FileLock(store.root / ".worker.lock", timeout=0, preserve_lock_file=True):
                pass
        finally:
            if parent.poll() is None:
                request_worker_stop(store)
                try:
                    parent.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    parent.kill()  # Only the isolated test launcher, no shared service.
                    parent.wait(timeout=5)
