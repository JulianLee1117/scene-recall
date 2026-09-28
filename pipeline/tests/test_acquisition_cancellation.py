"""Cancellation discards only detached managed staging and survives interruptions."""
import json
from pathlib import Path

import pytest

from pipeline.acquisition import cleanup, engine
from pipeline.acquisition.clients import ClientError
from pipeline.tests.test_acquisition_engine_review import (
    Crash, current, fill_download, import_download, managed, validate_download,
)


def cancel(managed):
    item = current(managed)
    managed.service.cancel(item["id"], item["revision"])


def detach_cancel(managed):
    cancel(managed)
    managed.client.record["state"] = "stoppedDL"
    managed.runner.tick()
    managed.client.record = None


def test_cancel_before_submission_makes_no_download_or_folder(managed):
    managed.config.paths.incoming_dir.mkdir(parents=True, exist_ok=True)
    cancel(managed)
    managed.runner.tick()
    assert current(managed)["status"] == "cancelled"
    assert not managed.runner.root(current(managed)).parent.exists()
    assert not any(call[0] in {"add", "remove", "stop"} for call in managed.client.calls)


def test_failed_executable_release_can_be_cancelled_and_removed(managed):
    root = fill_download(managed, {"film.mkv.!qB": b"partial", "unsafe.exe": b"fixture"})
    managed.runner.tick()
    assert current(managed)["status"] == "failed"
    detach_cancel(managed)
    managed.runner.tick()
    assert current(managed)["status"] == "cancelled"
    assert not root.parent.exists()


def test_partial_nested_download_removes_only_its_owned_directory(managed):
    root = fill_download(managed, {"release/deep/film.mkv.!qB": b"partial", ".unwanted/piece": b"x"})
    unrelated = root.parent.parent / "unrelated.txt"
    unrelated.write_text("keep")
    manual = managed.config.paths.incoming_dir / "manual.mkv"
    manual.write_bytes(b"original")
    detach_cancel(managed)
    managed.runner.tick()
    assert not root.parent.exists()
    assert unrelated.read_text() == "keep" and manual.read_bytes() == b"original"


def test_cleanup_waits_for_running_ingestion_and_preserves_library(managed):
    root = import_download(managed)
    managed.runner.tick()
    job = managed.runner.lab.claim()
    film = Path(current(managed)["film_path"])
    subtitle = film.with_suffix(".en.srt")
    original = film.read_bytes(), subtitle.read_bytes()
    cancel(managed)
    managed.runner.tick()
    managed.runner.tick()
    assert current(managed)["status"] == "cancelling"
    assert root.exists() and managed.runner.lab.is_cancelled(job["id"])
    managed.runner.lab.finish(job["id"], error="Cancelled")
    managed.runner.tick()
    assert current(managed)["status"] == "cancelled"
    assert not root.parent.exists()
    assert (film.read_bytes(), subtitle.read_bytes()) == original


def test_unjournaled_ingest_job_is_cancelled_before_cleanup(managed):
    root = import_download(managed)
    item = current(managed)
    job = managed.runner.lab.enqueue("ingest", path=Path(item["film_path"]))
    assert item["ingest_job_id"] is None
    cancel(managed)
    managed.runner.tick()
    assert root.exists()
    assert managed.runner.lab.get_job(job["id"])["status"] == "cancelled"
    managed.runner.tick()
    assert not root.parent.exists() and current(managed)["status"] == "cancelled"


def test_canonical_move_before_journal_is_recovered_on_cancel(managed):
    root = validate_download(managed)
    item = current(managed)
    plan = item["import_plan"]
    destination = Path(plan["destination"])
    (root / plan["source"]).rename(destination)
    managed.service.store.patch(item["id"], detach_requested=True)
    managed.client.record = None
    cancel(managed)
    managed.runner.tick()
    assert current(managed)["film_path"] == str(destination)
    assert current(managed)["status"] == "cancelled"
    assert destination.is_file() and not root.parent.exists()


def test_retried_imported_cancel_can_publish_without_deleted_staging(managed):
    root = import_download(managed)
    cancel(managed)
    managed.runner.tick()
    item = current(managed)
    assert item["status"] == "cancelled" and not root.parent.exists()
    managed.service.retry(item["id"], item["revision"])
    managed.runner.tick()
    job = managed.runner.lab.claim()
    managed.runner.lab.finish(job["id"])
    managed.runner.tick()
    managed.runner.tick()
    assert current(managed)["status"] == "ready"
    assert Path(current(managed)["film_path"]).exists()


def test_unknown_add_is_not_mistaken_for_confirmed_detachment(managed):
    managed.runner.tick()
    root = managed.runner.root(current(managed))
    late_record = managed.client.record
    managed.client.record = None
    cancel(managed)
    managed.runner.tick()
    assert current(managed)["status"] == "cancelling" and root.exists()
    assert "resolve the submitted torrent" in current(managed)["message"]
    managed.client.record = late_record
    managed.runner.tick()
    assert any(call[0] == "stop" for call in managed.client.calls)
    managed.client.record["state"] = "stoppedDL"
    managed.runner.tick()
    assert root.exists()
    managed.client.record = None
    managed.runner.tick()
    assert current(managed)["status"] == "cancelled" and not root.parent.exists()


def test_client_failure_keeps_cleanup_pending_without_touching_files(managed, monkeypatch):
    root = fill_download(managed)
    cancel(managed)
    def unavailable(*args):
        raise ClientError("Downloader unavailable")
    monkeypatch.setattr(managed.client, "info", unavailable)
    managed.runner.tick()
    assert current(managed)["status"] == "cancelling"
    assert current(managed)["cancellation_cleanup"] == "pending"
    assert current(managed)["error"] == "Downloader unavailable"
    assert root.exists()


def test_failed_client_preflight_does_not_leave_a_phantom_pending_add(managed, monkeypatch):
    def reject(*args, **kwargs):
        raise ClientError("Disable external hooks")
    monkeypatch.setattr(managed.client, "add_magnet", reject)
    managed.runner.tick()
    assert current(managed)["status"] == "failed"
    assert not current(managed).get("submission_attempted")
    cancel(managed)
    managed.runner.tick()
    assert current(managed)["status"] == "cancelled"


def test_resubmission_crash_clears_old_seen_and_detachment_receipts(managed, monkeypatch):
    fill_download(managed)
    managed.client.record = None
    managed.service.store.patch(managed.identity, status="queued", submission_seen=True,
                               detach_requested=True, cancel_detach_requested=True)
    def uncertain(*args, before_submit, **kwargs):
        before_submit()
        raise ClientError("The submission response was lost")
    monkeypatch.setattr(managed.client, "add_magnet", uncertain)
    managed.runner.tick()
    assert current(managed)["status"] == "failed"
    cancel(managed)
    managed.runner.tick()
    assert current(managed)["status"] == "cancelling"
    assert "resolve the submitted torrent" in current(managed)["message"]
    assert managed.runner.root(current(managed)).exists()


def test_failed_local_descriptor_read_can_still_be_cancelled(managed):
    path = managed.service.store.root / "torrents" / "missing.torrent"
    managed.service.store.patch(managed.identity, source={"kind": "torrent", "path": str(path)})
    managed.runner.tick()
    assert current(managed)["status"] == "failed"
    assert not current(managed).get("submission_attempted")
    cancel(managed)
    managed.runner.tick()
    assert current(managed)["status"] == "cancelled"


@pytest.mark.parametrize("change", ["wrong-marker", "missing-marker", "extra-wrapper", "data-link", "marker-link", "changed-client"])
def test_ownership_failures_never_delete_downloads(managed, monkeypatch, change):
    root = fill_download(managed)
    detach_cancel(managed)
    marker = root.parent / ".acquisition.json"
    if change == "wrong-marker":
        marker.write_text(json.dumps({"id": "someone-else"}))
    elif change == "missing-marker":
        marker.unlink()
    elif change == "extra-wrapper":
        (root.parent / "keep.txt").write_text("unrecognized")
    elif change.endswith("-link"):
        forbidden = root / "Mirror.1975.en.srt" if change == "data-link" else marker
        real_check = cleanup.is_link_or_junction
        monkeypatch.setattr(cleanup, "is_link_or_junction", lambda p: p == forbidden or real_check(p))
    else:
        managed.client.record = {"tags": "another-owner", "category": "scene-recall", "save_path": str(root)}
    managed.runner.tick()
    assert current(managed)["status"] == "cancelling" and current(managed)["error"]
    assert (root / "Mirror.1975.mkv").exists()


def test_locked_file_retries_after_restart_without_losing_marker(managed, monkeypatch):
    root = fill_download(managed)
    detach_cancel(managed)
    target = root / "Mirror.1975.mkv"
    unlink = Path.unlink
    def locked(path, *args, **kwargs):
        if path == target:
            raise PermissionError("locked")
        return unlink(path, *args, **kwargs)
    with monkeypatch.context() as patcher:
        patcher.setattr(Path, "unlink", locked)
        managed.runner.tick()
    assert current(managed)["status"] == "cancelling"
    assert (root.parent / ".acquisition.json").exists()
    managed.service.store.patch(managed.identity, cancellation_retry_at=None)
    restarted = engine.AcquisitionRunner(managed.service, publication_check=lambda item: True)
    restarted.tick()
    assert current(managed)["status"] == "cancelled" and not root.parent.exists()


def test_crash_after_last_marker_unlink_can_finish_empty_owned_directory(managed, monkeypatch):
    root = fill_download(managed)
    detach_cancel(managed)
    rmdir = Path.rmdir
    def crash(path):
        if path == root.parent:
            raise Crash()
        return rmdir(path)
    with monkeypatch.context() as patcher:
        patcher.setattr(Path, "rmdir", crash)
        with pytest.raises(Crash):
            managed.runner.tick()
    assert list(root.parent.iterdir()) == []
    assert current(managed)["cancellation_owner"]
    managed.runner.tick()
    assert current(managed)["status"] == "cancelled" and not root.parent.exists()


def test_replaced_owned_directory_is_not_deleted_on_retry(managed):
    root = fill_download(managed)
    detach_cancel(managed)
    owner = cleanup._identity(root.parent)
    managed.service.store.patch(managed.identity, cancellation_owner=owner)
    kept = root.parent.with_name("saved-original")
    root.parent.rename(kept)
    root.mkdir(parents=True)
    (root.parent / ".acquisition.json").write_bytes((kept / ".acquisition.json").read_bytes())
    (root / "replacement.mkv").write_bytes(b"replacement")
    managed.runner.tick()
    assert current(managed)["status"] == "cancelling"
    assert (root / "replacement.mkv").exists()
    assert (kept / "data" / "Mirror.1975.mkv").exists()
