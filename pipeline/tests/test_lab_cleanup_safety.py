"""Exercise real Windows junctions without linking to any non-test directory."""

from contextlib import contextmanager
import os
from pathlib import Path
import shutil
import stat
import subprocess
import time

import pytest

from pipeline.lab import cleanup
from pipeline.lab.store import LabStore


@pytest.fixture
def store(tmp_path):
    assets = tmp_path / "assets"
    assets.mkdir()
    result = LabStore(tmp_path / "state", assets)
    result.initialize()
    return result


def finished_job(store):
    project = store.create_project("Junction safety", "music-sketch")
    job = store.enqueue("render", project["id"], 1)
    store.claim()
    store.finish(job["id"], result={"message": "complete"})
    return project, job


def tickets(store):
    with store.connection() as con:
        return [dict(row) for row in con.execute("SELECT * FROM artifact_cleanup")]


@contextmanager
def temporary_junction(link, target, workspace):
    if os.name != "nt":
        pytest.skip("Directory junctions are a Windows filesystem feature")
    powershell = shutil.which("powershell.exe")
    if powershell is None:
        pytest.skip("Windows PowerShell is unavailable for junction creation")
    workspace = workspace.resolve(strict=True)
    target = target.resolve(strict=True)
    link = link.absolute()
    link.parent.mkdir(parents=True, exist_ok=True)
    assert target != workspace and target.is_relative_to(workspace)
    assert link.parent.resolve(strict=True).is_relative_to(workspace)
    assert not link.exists() and not link.is_symlink()

    def quote(value):
        return "'" + str(value).replace("'", "''") + "'"

    command = ("$ErrorActionPreference = 'Stop'; New-Item -ItemType Junction -Path "
               + quote(link) + " -Target " + quote(target) + " | Out-Null")
    result = subprocess.run([powershell, "-NoProfile", "-NonInteractive", "-Command", command],
                            capture_output=True, text=True, timeout=15,
                            creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode and not link.is_junction():
        pytest.skip("Filesystem does not permit junction creation: " + result.stderr[-400:])
    try:
        assert result.returncode == 0, result.stderr
        assert link.is_junction()
        assert link.lstat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT
        assert link.resolve(strict=True) == target
        yield link
    finally:
        # rmdir on a verified Windows junction removes that link, not its tree.
        # Never hand a resolved target path to a recursive deletion operation.
        assert link.parent.resolve(strict=True).is_relative_to(workspace)
        assert link.is_junction() and link.resolve(strict=True) == target
        link.rmdir()
        assert not link.exists() and target.is_dir()


@pytest.mark.parametrize("location", ["category_root", "nested"])
@pytest.mark.parametrize("target_scope", ["outside_assets", "inside_assets"])
def test_real_junction_keeps_delete_ticket_and_never_traverses_evidence(
        store, tmp_path, monkeypatch, location, target_scope):
    project, job = finished_job(store)
    base = tmp_path if target_scope == "outside_assets" else store.assets_dir
    evidence = base / "source-evidence"
    evidence.mkdir()
    original = evidence / "original.mp4"
    original.write_bytes(b"immutable original film bytes")
    # A UUID-owned-looking subtree makes traversal dangerous even though the
    # target is otherwise outside the renderer's ownership namespace.
    remote_output = evidence / job["id"] / "output.mp4"
    remote_output.parent.mkdir()
    remote_output.write_bytes(b"protected target artifact")
    retained = [original, remote_output]
    if location == "category_root":
        link = store.assets_dir / "lab" / "renders"
    else:
        job_dir = store.assets_dir / "lab" / "renders" / job["id"]
        job_dir.mkdir(parents=True)
        adjacent = job_dir / "output.mp4"
        adjacent.write_bytes(b"inventory must finish before removal")
        retained.append(adjacent)
        link = job_dir / "nested-junction"
    before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in retained}
    visited = []
    iterdir = Path.iterdir

    def observed_iterdir(path):
        visited.append(path.absolute())
        return iterdir(path)

    with temporary_junction(link, evidence, tmp_path):
        monkeypatch.setattr(Path, "iterdir", observed_iterdir)
        result = store.delete_project(project["id"], 1)
        assert result == {"deleted": project["id"], "cleanup_pending": True}
        with pytest.raises(KeyError):
            store.get_project(project["id"])
        for _ in range(2):
            report = cleanup.drain_cleanup(store)
            assert report["completed"] == 0 and report["errors"]
            assert tickets(store)[0]["job_id"] == job["id"]
            assert tickets(store)[0]["error"]
        # Legacy reconciliation must also refuse it, even beyond orphan grace.
        report = cleanup.collect_garbage(store, apply=True, now=time.time() + 2 * cleanup.GRACE_SECONDS)
        assert report["errors"] and not report["files"]
        assert link.is_junction()
        assert link.absolute() not in visited and evidence.absolute() not in visited
        assert {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in retained} == before
        assert len(tickets(store)) == 1
    assert original.read_bytes() == b"immutable original film bytes"
    assert remote_output.read_bytes() == b"protected target artifact"
    assert evidence.is_dir() and not link.exists()


def test_direct_escape_is_rejected_before_removal(store, tmp_path):
    category = store.assets_dir / "lab" / "renders"
    category.mkdir(parents=True)
    external = tmp_path / "source.mp4"
    external.write_bytes(b"source")
    escaped = category / ".." / ".." / ".." / external.name
    with pytest.raises(ValueError, match="escapes|inside"):
        cleanup._remove(escaped, store.assets_dir)
    with pytest.raises(ValueError, match="inside"):
        cleanup._remove(store.assets_dir, store.assets_dir)
    # Lexically nested traversal must not turn the entire assets root into an
    # eligible target merely because resolved containment includes equality.
    with pytest.raises(ValueError, match="escapes|inside"):
        cleanup._checked(category / ".." / "..", store.assets_dir)
    assert external.read_bytes() == b"source"


@pytest.mark.parametrize("identity", ["../outside", "a" * 36, "0" * 32,
    "01234567-89ab-cdef-0123-456789abcdefx"])
def test_malformed_cleanup_ticket_cannot_claim_files(store, identity):
    unrelated = store.assets_dir / "lab" / "requests" / "research.json"
    unrelated.parent.mkdir(parents=True)
    unrelated.write_bytes(b"unrelated")
    with store.connection() as con:
        con.execute("INSERT INTO artifact_cleanup(job_id,assets_root,created_at) VALUES (?,?,?)",
                    (identity, str(store.assets_dir), time.time()))
    report = cleanup.drain_cleanup(store)
    assert report["completed"] == 0 and report["errors"]
    assert tickets(store)[0]["job_id"] == identity
    assert "invalid job identity" in tickets(store)[0]["error"]
    assert unrelated.read_bytes() == b"unrelated"


def test_uuid_lookalikes_do_not_inherit_deleted_job_ownership(store):
    project, job = finished_job(store)
    identity = job["id"]
    requests = store.assets_dir / "lab" / "requests"
    requests.mkdir(parents=True)
    owned = requests / f"{identity}-draft.json"
    owned.write_bytes(b"owned")
    lookalikes = [requests / (name + "-draft.json") for name in
                  [identity + "x", "x" + identity, identity.replace("-", ""), "g" + identity[1:]]]
    for path in lookalikes:
        path.write_bytes(b"independent namespace")
    assert not store.delete_project(project["id"], 1)["cleanup_pending"]
    assert not owned.exists() and tickets(store) == []
    assert all(path.read_bytes() == b"independent namespace" for path in lookalikes)
    report = cleanup.collect_garbage(store, apply=True, now=time.time() + 2 * cleanup.GRACE_SECONDS)
    assert not report["files"] and not report["errors"]
    assert all(path.read_bytes() == b"independent namespace" for path in lookalikes)
