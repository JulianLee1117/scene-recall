"""Fresh local worker processes without interrupting or replaying durable jobs."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from filelock import FileLock

from pipeline.lab import worker, worker_runtime as runtime
from pipeline.lab.store import LabStore


def test_runtime_fingerprint_tracks_added_changed_removed_python_only(tmp_path):
    source = tmp_path / "pipeline"
    source.mkdir()
    code = source / "models.py"
    code.write_text("PACE = 'balanced'\n")
    baseline = runtime.source_fingerprint(source)
    for folder in ("tests", "assets", "__pycache__", ".venv"):
        (source / folder).mkdir()
        (source / folder / "ignored.py").write_text("ignored = True")
    (source / "config.yaml").write_text("pacing: rapid")
    os.utime(code, None)
    assert runtime.source_fingerprint(source) == baseline
    code.write_text("PACE = 'rapid'\n")
    changed = runtime.source_fingerprint(source)
    assert changed != baseline
    added = source / "new_job.py"
    added.write_text("pass\n")
    assert runtime.source_fingerprint(source) != changed
    added.unlink()
    assert runtime.source_fingerprint(source) == changed


def queued_store(config, tmp_path):
    store = LabStore(config.paths.state_dir)
    store.initialize()
    first = store.enqueue("ingest", path=tmp_path / "one.mp4")
    second = store.enqueue("ingest", path=tmp_path / "two.mp4")
    return store, first, second


def test_idle_source_change_exits_before_claim_and_releases_singleton(config, tmp_path, monkeypatch):
    store, first, second = queued_store(config, tmp_path)
    monkeypatch.setattr(worker, "source_changed", lambda original: True)
    execute = MagicMock()
    monkeypatch.setattr(worker, "execute_job", execute)
    assert worker.run_worker(config, db=MagicMock(), reload_fingerprint="old") == runtime.RELOAD_EXIT_CODE
    execute.assert_not_called()
    assert store.get_job(first["id"])["status"] == "queued"
    assert store.get_job(second["id"])["status"] == "queued"
    with FileLock(store.root / ".worker.lock", timeout=0, preserve_lock_file=True):
        pass


def test_source_change_during_job_finishes_once_then_leaves_next_for_fresh_worker(config, tmp_path, monkeypatch):
    store, first, second = queued_store(config, tmp_path)
    version = ["old"]
    calls = []
    monkeypatch.setattr(worker, "source_changed", lambda original: version[0] != original)

    def execute(job, _config, _db, ledger, **_kwargs):
        calls.append(job["id"])
        version[0] = "new"
        assert ledger.get_job(job["id"])["status"] == "running"
        ledger.finish(job["id"])

    monkeypatch.setattr(worker, "execute_job", execute)
    assert worker.run_worker(config, db=MagicMock(), reload_fingerprint="old") == runtime.RELOAD_EXIT_CODE
    assert calls == [first["id"]]
    assert store.get_job(first["id"])["status"] == "completed"
    assert store.get_job(second["id"])["status"] == "queued"
    worker.run_worker(config, once=True, db=MagicMock(), reload_fingerprint="new")
    assert calls == [first["id"], second["id"]]
    assert all(store.get_job(identity)["status"] == "completed" for identity in calls)


def test_parent_stop_during_job_finishes_it_without_claiming_or_reloading(config, tmp_path, monkeypatch):
    store, first, second = queued_store(config, tmp_path)
    stop = threading.Event()

    def execute(job, _config, _db, ledger, **_kwargs):
        stop.set()
        ledger.finish(job["id"])

    monkeypatch.setattr(worker, "execute_job", execute)
    monkeypatch.setattr(worker, "source_changed", lambda original: False)
    worker.run_worker(config, db=MagicMock(), stop=stop, reload_fingerprint="current")
    assert store.get_job(first["id"])["status"] == "completed"
    assert store.get_job(second["id"])["status"] == "queued"


def test_stop_arriving_during_source_scan_does_not_claim_a_job(config, tmp_path, monkeypatch):
    store, first, _second = queued_store(config, tmp_path)
    stop = threading.Event()

    def scan(_original):
        stop.set()
        return False

    monkeypatch.setattr(worker, "source_changed", scan)
    execute = MagicMock()
    monkeypatch.setattr(worker, "execute_job", execute)
    worker.run_worker(config, db=MagicMock(), stop=stop, reload_fingerprint="current")
    execute.assert_not_called()
    assert store.get_job(first["id"])["status"] == "queued"


class ClockStop:
    def __init__(self):
        self.now = 0.0
        self.stopped = False

    def is_set(self):
        return self.stopped

    def set(self):
        self.stopped = True

    def wait(self, seconds):
        self.now += seconds
        assert self.now < 15, "reload logic did not settle"
        return self.stopped


def test_partial_source_writes_wait_for_a_full_stable_second(monkeypatch):
    stop = ClockStop()
    monkeypatch.setattr(runtime.time, "monotonic", lambda: stop.now)

    def fingerprint():
        if stop.now < 0.25:
            raise FileNotFoundError("atomic replacement")
        return "partial" if stop.now < 0.75 else "complete"

    monkeypatch.setattr(runtime, "source_fingerprint", fingerprint)
    assert runtime.stable_sources(stop) == "complete"
    assert stop.now == 1.75


def test_failed_child_waits_for_changed_sources_instead_of_spinning(monkeypatch, capsys):
    stop = ClockStop()
    monkeypatch.setattr(runtime.time, "monotonic", lambda: stop.now)
    monkeypatch.setattr(runtime, "source_fingerprint", lambda: "old" if stop.now < 3 else "fixed")
    starts, children = [], []

    def spawn(command, **options):
        starts.append(stop.now)
        assert options["stdin"] == subprocess.PIPE
        assert "stdout" not in options and "stderr" not in options
        if os.name == "nt":
            assert options["creationflags"] == subprocess.CREATE_NO_WINDOW
        child = MagicMock(pid=100 + len(starts))
        child.wait.return_value = 1 if len(starts) == 1 else 0
        children.append(child)
        return child

    monkeypatch.setattr(runtime.subprocess, "Popen", spawn)
    assert runtime.supervise([sys.executable, "-m", "pipeline.lab.worker", "--reload-child"], stop) == 0
    assert starts == [1.0, 4.0]
    assert all(child.stdin.close.called for child in children)
    assert "queued jobs remain saved" in capsys.readouterr().out


def test_parent_shutdown_closes_control_pipe_and_waits_without_terminating_child(monkeypatch):
    stop = threading.Event()
    monkeypatch.setattr(runtime, "stable_sources", lambda *_args, **_kwargs: "current")
    child = MagicMock(pid=123)
    calls = []

    def wait(timeout):
        calls.append(timeout)
        if len(calls) == 1:
            stop.set()
            raise subprocess.TimeoutExpired("worker", timeout)
        assert child.stdin.close.called
        return 0

    child.wait.side_effect = wait
    monkeypatch.setattr(runtime.subprocess, "Popen", lambda *_args, **_kwargs: child)
    assert runtime.supervise([sys.executable, "worker"], stop) == 0
    child.terminate.assert_not_called()
    child.kill.assert_not_called()
    assert len(calls) == 2


def test_control_pipe_eof_sets_child_stop_event(monkeypatch):
    stop = threading.Event()
    if os.name == "nt":
        monkeypatch.setattr(runtime, "_wait_for_windows_pipe_close", lambda event: None)
        monkeypatch.setattr(runtime.os, "read", lambda *_args: pytest.fail("Windows must not block on a CRT read"))
    else:
        monkeypatch.setattr(runtime.os, "read", lambda fd, count: b"")
    runtime.watch_parent(stop)
    assert stop.wait(1)


def test_reload_cli_preserves_config_path_and_does_not_combine_with_once(tmp_path, monkeypatch):
    monkeypatch.setenv("CINEMA_CONFIG", "original")
    monkeypatch.setattr(worker.signal, "signal", lambda *_args: None)
    calls = []
    monkeypatch.setattr(worker, "supervise", lambda command, _stop, **_kwargs: calls.append(command) or 0)
    path = tmp_path / "a project" / "config.yaml"
    assert worker.main(["--reload", "--role", "all", "--config", str(path)]) == 0
    assert calls == [[sys.executable, "-m", "pipeline.lab.worker", "--role", "all", "--reload-child", "--config", str(path.resolve())]]
    with pytest.raises(SystemExit) as exc:
        worker.main(["--reload", "--once"])
    assert exc.value.code == 2


def test_worker_bootstrap_import_does_not_load_stale_job_schemas():
    result = subprocess.run([sys.executable, "-c", "import sys; import pipeline.lab.worker; "
                             "assert 'pipeline.lab.models' not in sys.modules; "
                             "assert 'pipeline.config' not in sys.modules"], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr


def test_real_controlled_child_imports_and_opens_database_before_parent_pipe_closes(config, tmp_path):
    """Exercise NumPy/LanceDB imports with the control pipe open, not mocked."""
    log = tmp_path / "worker.log"
    command = [sys.executable, "-m", "pipeline.lab.worker", "--worker-child",
               "--config", str(tmp_path / "config.yaml")]
    options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    with log.open("wb") as output:
        child = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=output,
                                 stderr=subprocess.STDOUT, **options)
        try:
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline and child.poll() is None:
                if "Lab worker ready:" in log.read_text(errors="replace"):
                    break
                time.sleep(0.05)
            assert "Lab worker ready:" in log.read_text(errors="replace"), log.read_text(errors="replace")
            assert child.poll() is None, log.read_text(errors="replace")
            assert LabStore(config.paths.state_dir).path.exists()
            child.stdin.close()
            assert child.wait(timeout=10) == 0, log.read_text(errors="replace")
        finally:
            child.stdin.close()
            if child.poll() is None:
                # Only this test's isolated process; never a shared worker.
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=5)


def test_reload_spawns_fresh_interpreters_and_preserves_completed_attempts(tmp_path, monkeypatch):
    receipt = tmp_path / "pids.txt"
    script = tmp_path / "worker_fixture.py"
    script.write_text("import os, sys\nfrom pathlib import Path\np = Path(sys.argv[1])\n"
                      "old = p.read_text() if p.exists() else ''\n"
                      "p.write_text(old + str(os.getpid()) + '\\n')\n"
                      f"sys.exit({runtime.RELOAD_EXIT_CODE} if not old else 0)\n")
    monkeypatch.setattr(runtime, "stable_sources", lambda *_args, **_kwargs: "fixture")
    assert runtime.supervise([sys.executable, str(script), str(receipt)], threading.Event()) == 0
    pids = receipt.read_text().splitlines()
    assert len(pids) == 2 and pids[0] != pids[1]


def test_job_failure_keeps_original_error_after_deferred_imports(config, monkeypatch):
    from pipeline.lab import music
    store = LabStore(config.paths.state_dir)
    store.initialize()
    project = store.create_project("Failure fixture", "music-sketch")
    queued = store.enqueue("analyze", project["id"], 1)

    def reject(*_args, **_kwargs):
        raise ValueError("Provider rejected the requested schema")

    monkeypatch.setattr(music, "run_music_job", reject)
    worker.execute_job(store.claim(), config, None, store)
    failed = store.get_job(queued["id"])
    assert failed["status"] == "failed"
    assert failed["error"] == "Provider rejected the requested schema"
