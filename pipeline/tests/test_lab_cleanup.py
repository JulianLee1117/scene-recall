"""Storage lifecycle tests exercise owned files, crash recovery and live-work safety."""
import os
import sqlite3
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from threading import Event

import pytest

from pipeline.lab import cleanup
from pipeline.lab.store import ActiveProjectJobs, LabStore


@pytest.fixture
def store(tmp_path):
    assets = tmp_path / "assets"
    assets.mkdir()
    result = LabStore(tmp_path / "state", assets)
    result.initialize()
    return result


def put(store, name, data=b"artifact"):
    path = store.assets_dir / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def old_tree(path):
    entries = [path, *path.rglob("*")] if path.is_dir() else [path]
    for entry in entries:
        os.utime(entry, (1, 1))


def finished(store):
    project = store.create_project("Delete me", "music-sketch")
    job = store.enqueue("render", project["id"], 1)
    store.claim()
    store.finish(job["id"], result={"message": "Done"})
    return project, job


def tickets(store):
    with store.connection() as con:
        return [dict(row) for row in con.execute("SELECT * FROM artifact_cleanup")]


def test_project_delete_removes_all_owned_variants_and_protects_every_other_namespace(store):
    project, job = finished(store)
    identity = job["id"]
    owned = [
        f"lab/renders/{identity}/output.mp4", f"lab/renders/{identity}/manifest.json",
        f"lab/renders/{identity}-candidate/output.mp4",
        f"lab/renders/{identity}-candidate-proposed/output.mp4",
        f"lab/renders/{identity}-candidate-original/output.mp4",
        f"lab/requests/{identity}-source-context-input.json",
        f"lab/requests/{identity}-part-01-inspect-abcdef.json",
        f"lab/requests/{identity}-draft.json.random.tmp",
        f"matching/results/{identity}/frame.jpg",
    ]
    paths = [put(store, name) for name in owned]
    retained = [put(store, name) for name in [
        f"lab/renders/{identity}x/output.mp4", f"lab/requests/{identity}x-draft.json",
        "lab/renders/eval-frozen/output.mp4", "lab/interpretations/shared.json",
        "lab/rhythm/shared.json", "lab/footage-observations/shared.json",
        "context/profile/film/evidence.json", "lab/beat-this/checkpoint.ckpt",
        f"lab/renders/{uuid.uuid4()}/output.mp4",
    ]]
    track = store.root / "tracks" / "original.mp3"
    track.write_bytes(b"original")
    result = store.delete_project(project["id"], 1)
    assert result == {"deleted": project["id"], "cleanup_pending": False}
    assert all(not path.exists() for path in paths)
    assert all(path.read_bytes() == b"artifact" for path in retained)
    assert track.read_bytes() == b"original"
    assert tickets(store) == []


def test_locked_file_keeps_ticket_and_retries_after_restart(store, monkeypatch):
    project, job = finished(store)
    path = put(store, f"lab/renders/{job['id']}/output.mp4")
    remove = cleanup._remove
    monkeypatch.setattr(cleanup, "_remove", lambda *_: (_ for _ in ()).throw(PermissionError("file in use")))
    assert store.delete_project(project["id"], 1)["cleanup_pending"]
    assert path.exists() and "file in use" in tickets(store)[0]["error"]
    with pytest.raises(KeyError):
        store.get_project(project["id"])
    monkeypatch.setattr(cleanup, "_remove", remove)
    restarted = LabStore(store.root.parent, store.assets_dir)
    restarted.initialize()
    assert cleanup.drain_cleanup(restarted)["completed"] == 1
    assert not path.exists() and tickets(restarted) == []
    assert cleanup.drain_cleanup(restarted)["completed"] == 0


def test_crash_after_commit_recovers_recorded_assets_root(store, tmp_path, monkeypatch):
    project, job = finished(store)
    path = put(store, f"lab/requests/{job['id']}-draft.json")
    drain = cleanup.drain_cleanup
    monkeypatch.setattr(cleanup, "drain_cleanup", lambda *_args, **_kwargs: {})
    assert store.delete_project(project["id"], 1)["cleanup_pending"]
    assert tickets(store)[0]["job_id"] == job["id"]
    # A configuration change must not acknowledge old files at the new root.
    new_assets = tmp_path / "new-assets"
    new_assets.mkdir()
    restarted = LabStore(store.root.parent, new_assets)
    assert drain(restarted)["completed"] == 1
    assert not path.exists()


def test_active_and_cancel_requested_jobs_block_deletion(store):
    project = store.create_project("Working", "music-sketch")
    job = store.enqueue("render", project["id"], 1)
    path = put(store, f"lab/renders/{job['id']}/clip-000.mp4")
    store.claim()
    store.cancel(job["id"])
    with pytest.raises(ActiveProjectJobs):
        store.delete_project(project["id"], 1)
    assert path.exists() and tickets(store) == []
    cleanup.collect_garbage(store, apply=True)
    assert path.exists()


def test_maintenance_reclaims_only_known_terminal_intermediates_and_old_orphans(store):
    _, job = finished(store)
    temporary = put(store, f"lab/renders/{job['id']}/clip-000.mp4")
    output = put(store, f"lab/renders/{job['id']}/output.mp4")
    manifest = put(store, f"lab/renders/{job['id']}/manifest.json")
    # A terminal projectless discovery still owns its outputs and thumbnails.
    with store.connection() as con:
        con.execute("UPDATE jobs SET project_id=NULL,kind='match-search' WHERE id=?", (job["id"],))
    thumbnail = put(store, f"matching/results/{job['id']}/frame.jpg")
    orphan_id = str(uuid.uuid4())
    orphan = put(store, f"lab/renders/{orphan_id}-candidate/output.mp4")
    old_tree(orphan.parent)
    receipt = put(store, f"lab/requests/{orphan_id}-draft.json")
    old_tree(receipt)
    fresh_id = str(uuid.uuid4())
    fresh = put(store, f"lab/renders/{fresh_id}/output.mp4")
    old_tree(fresh.parent)
    recent_child = put(store, f"lab/renders/{fresh_id}/new.json")
    os.utime(fresh.parent, (1, 1))
    experiment = put(store, "lab/renders/context-pilot/clip-000.mp4")
    old_tree(experiment.parent)
    result = cleanup.collect_garbage(store, apply=True)
    assert result["errors"] == []
    assert not temporary.exists() and not orphan.exists() and not receipt.exists()
    assert all(path.exists() for path in [output, manifest, thumbnail, fresh, recent_child, experiment])


def test_dry_run_is_read_only_and_batches_are_bounded(store):
    _, job = finished(store)
    paths = [put(store, f"lab/renders/{job['id']}/clip-{i:03}.mp4") for i in range(3)]
    before = {str(path): path.read_bytes() for path in store.root.parent.parent.rglob("*") if path.is_file()}
    result = cleanup.maintain_storage(store, apply=False)
    after = {str(path): path.read_bytes() for path in store.root.parent.parent.rglob("*") if path.is_file()}
    assert before == after
    assert result["garbage"]["bytes"] == 3 * len(b"artifact")
    assert cleanup.collect_garbage(store, apply=True, limit=1)["bytes"] == len(b"artifact")
    assert sum(path.exists() for path in paths) == 2


def test_transition_native_and_flow_scratch_are_owned_only_after_job_finishes(store):
    project, terminal = finished(store)
    active = store.enqueue("render", project["id"], 1)
    with store.connection() as con:
        con.execute("UPDATE jobs SET project_id=NULL,kind='transition-render' WHERE id IN (?,?)", (terminal["id"], active["id"]))
    names = [f"clip-{side}.{kind}.nut" for side in (0, 1) for kind in ("native", "flow")]
    disposable = [put(store, f"lab/renders/{terminal['id']}/{name}") for name in names]
    live = [put(store, f"lab/renders/{active['id']}/{name}") for name in names]
    retained = [put(store, f"lab/renders/{terminal['id']}/{name}") for name in
                ("manifest.json", "output.mp4", "frame-a.jpg", "original.nut", "clip-2.flow.nut")]
    experiment = put(store, "lab/renders/transition-review/clip-0.native.nut")
    result = cleanup.collect_garbage(store, apply=True)
    assert result["errors"] == []
    assert not any(path.exists() for path in disposable)
    assert all(path.exists() for path in [*live, *retained, experiment])


def test_dry_run_on_previous_schema_does_not_migrate_database(store):
    _, job = finished(store)
    path = put(store, f"lab/renders/{job['id']}/clips.txt")
    with store.connection() as con:
        con.execute("DROP TABLE artifact_cleanup")
    report = cleanup.maintain_storage(store, apply=False)
    assert report["deletions"] == {"pending": []}
    assert len(report["garbage"]["files"]) == 1 and path.exists()
    with store.connection() as con:
        assert con.execute("SELECT 1 FROM sqlite_master WHERE name='artifact_cleanup'").fetchone() is None


def test_unavailable_assets_and_failed_inventory_never_acknowledge_tickets(store, monkeypatch):
    first, job1 = finished(store)
    second, job2 = finished(store)
    drain = cleanup.drain_cleanup
    monkeypatch.setattr(cleanup, "drain_cleanup", lambda *_args, **_kwargs: {})
    store.delete_project(first["id"], 1)
    store.delete_project(second["id"], 1)
    paths = [put(store, f"lab/requests/{job['id']}-draft.json") for job in (job1, job2)]
    def interrupted_inventory(*_args):
        yield paths[0], job1["id"]
        raise PermissionError("unreadable second directory")
    monkeypatch.setattr(cleanup, "_owned_paths", interrupted_inventory)
    assert drain(store)["completed"] == 0
    assert len(tickets(store)) == 2 and all(path.exists() for path in paths)
    monkeypatch.setattr(cleanup, "_assets", lambda *_args: (_ for _ in ()).throw(OSError("offline")))
    assert drain(store)["completed"] == 0
    assert len(tickets(store)) == 2


def test_database_failure_after_delete_commit_reports_pending_not_failed_delete(store, monkeypatch):
    project, job = finished(store)
    connection = store.connection
    drain = cleanup.drain_cleanup
    @contextmanager
    def unavailable():
        raise sqlite3.OperationalError("database is locked")
        yield
    def after_commit(*args, **kwargs):
        monkeypatch.setattr(store, "connection", unavailable)
        return drain(*args, **kwargs)
    monkeypatch.setattr(cleanup, "drain_cleanup", after_commit)
    assert store.delete_project(project["id"], 1)["cleanup_pending"]
    assert cleanup.maintain_storage(store)["deletions"]["errors"]
    monkeypatch.setattr(store, "connection", connection)
    assert tickets(store)[0]["job_id"] == job["id"]
    assert store.list_projects() == []


@pytest.mark.parametrize("location", ["root", "nested"])
def test_linked_paths_are_retained_and_external_targets_untouched(store, tmp_path, monkeypatch, location):
    project, job = finished(store)
    external = tmp_path / "evidence"
    external.mkdir()
    original = external / "original.mp4"
    original.write_bytes(b"original")
    target = store.assets_dir / "lab/renders" / job["id"]
    if location == "nested":
        target = target / "unexpected-link"
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        target.symlink_to(external, target_is_directory=True)
    except OSError:
        pytest.skip("OS does not permit creating symlinks")
    try:
        assert store.delete_project(project["id"], 1)["cleanup_pending"]
        assert original.read_bytes() == b"original" and target.is_symlink()
        assert tickets(store)
    finally:
        target.unlink()


def test_old_decoded_audio_retained_during_editor_work_then_reclaimed(store):
    path = put(store, f"lab/audio/{'a' * 64}.wav")
    old_tree(path)
    other = put(store, "lab/audio/not-owned.wav")
    project = store.create_project("Working", "music-sketch")
    job = store.enqueue("analyze", project["id"], 1)
    assert not cleanup.collect_garbage(store, apply=True)["files"]
    store.cancel(job["id"])
    result = cleanup.collect_garbage(store, apply=True)
    assert result["files"][0]["reason"] == "stale decoded audio"
    assert not path.exists() and other.exists()


def test_audio_removal_serializes_with_new_work(store, monkeypatch):
    path = put(store, f"lab/audio/{'b' * 64}.wav")
    old_tree(path)
    project = store.create_project("New work", "music-sketch")
    remove = cleanup._remove
    started = Event()
    futures = []
    with ThreadPoolExecutor(max_workers=1) as pool:
        def enqueue():
            started.set()
            return store.enqueue("analyze", project["id"], 1)
        def checked_remove(target, root):
            futures.append(pool.submit(enqueue))
            assert started.wait(2)
            time.sleep(.05)
            assert not futures[0].done()
            return remove(target, root)
        monkeypatch.setattr(cleanup, "_remove", checked_remove)
        cleanup.collect_garbage(store, apply=True)
        assert futures[0].result(timeout=5)["status"] == "queued"
    assert not path.exists()
