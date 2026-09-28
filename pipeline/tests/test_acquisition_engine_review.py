"""Managed acquisitions survive crashes without losing evidence or replaying jobs."""

from __future__ import annotations

from copy import deepcopy
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from pipeline.acquisition import engine
from pipeline.acquisition.service import AcquisitionService
from pipeline.acquisition.settings import AcquisitionSettings
from pipeline.acquisition.sources import parse_magnet
from pipeline.tests.subtitle_helpers import full_english_srt


MAGNET = "magnet:?xt=urn:btih:" + "a" * 40
SRT = full_english_srt().encode()
SPARSE_SRT = "".join(f"{cue}\n00:00:{cue * 3:02d},000 --> 00:00:{cue * 3 + 2:02d},000\n"
                     "The house is on fire.\n\n" for cue in range(1, 9)).encode()
FILES = {
    "Mirror.1975.mkv": b"verified-film-fixture" * 4000,
    "Mirror.1975.en.srt": SRT,
    "release-notes.txt": b"Original release notes retained as provenance.\n",
}


class Crash(BaseException):
    """A process dies after an external side effect and before ledger update."""


class FakeDownloader:
    def __init__(self):
        self.record = None
        self.inventory = []
        self.calls = []

    def app_version(self):
        return "5.0.0"

    def info(self, info_hash):
        self.calls.append(("info", info_hash))
        return deepcopy(self.record)

    def add_magnet(self, magnet, save_path, tag, *, before_submit=None):
        if before_submit:
            before_submit()
        self.calls.append(("add", save_path, tag))
        self.record = {"hash": parse_magnet(magnet).info_hash, "save_path": save_path,
                       "tags": tag, "category": "scene-recall", "state": "downloading",
                       "progress": 0.0, "dlspeed": 0, "eta": 30, "completed": 0, "total_size": 0}

    def files(self, info_hash):
        self.calls.append(("files", info_hash))
        return deepcopy(self.inventory)

    def stop(self, info_hash, *, tag):
        self.calls.append(("stop", info_hash, tag))
        # qBittorrent acknowledges asynchronously; tests explicitly advance it.

    def start(self, info_hash, *, tag):
        self.calls.append(("start", info_hash, tag))
        self.record["state"] = "downloading"

    def remove(self, info_hash, *, delete_files, tag):
        assert delete_files is False, "Never delegate source deletion to a downloader"
        self.calls.append(("remove", info_hash, delete_files, tag))


@pytest.fixture
def managed(config):
    downloader = FakeDownloader()
    service = AcquisitionService(config, settings=AcquisitionSettings(qbittorrent_url="http://127.0.0.1:8080"),
                                 downloader=downloader)
    item = service.add_magnet(MAGNET, title="Mirror", year=1975)
    inspected = []

    def inspect(path):
        inspected.append(path)
        return {"duration": 6000, "streams": [{"codec_type": "video", "width": 1920}], "decode_samples": 3}

    runner = engine.AcquisitionRunner(service, media_inspector=inspect, publication_check=lambda item: True)
    return SimpleNamespace(service=service, runner=runner, client=downloader, identity=item["id"],
                           config=config, inspected=inspected)


def current(managed):
    return managed.service.store.get(managed.identity)


def fill_download(managed, files=None):
    files = FILES if files is None else files
    managed.runner.tick()
    assert current(managed)["status"] == "downloading"
    root = managed.runner.root(current(managed))
    managed.client.inventory = []
    for relative, content in files.items():
        path = root.joinpath(*relative.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        managed.client.inventory.append({"name": relative, "size": len(content), "progress": 1.0})
    total = sum(len(content) for content in files.values())
    managed.client.record.update(progress=1.0, state="uploading", completed=total, total_size=total)
    return root


def validate_download(managed, files=None):
    root = fill_download(managed, files)
    managed.runner.tick()
    assert current(managed)["status"] == "validating"
    managed.client.record["state"] = "stoppedUP"
    managed.runner.tick()
    assert current(managed)["status"] == "importing", current(managed)
    return root


def detach_download(managed):
    managed.runner.tick()
    assert current(managed)["status"] == "importing"
    assert any(call[0] == "remove" for call in managed.client.calls)
    managed.client.record = None


def import_download(managed):
    root = validate_download(managed)
    detach_download(managed)
    managed.runner.tick()
    assert current(managed)["status"] == "ingest_queued", current(managed)
    return root


def finish_ingestion(managed):
    managed.runner.tick()
    job = managed.runner.lab.claim()
    assert job["kind"] == "ingest"
    managed.runner.lab.finish(job["id"], result={"published": True})
    managed.runner.tick()
    assert current(managed)["status"] == "cleanup"
    return job


def test_full_owned_lifecycle_waits_for_stop_and_detach_before_moving(managed):
    root = fill_download(managed)
    source = root / "Mirror.1975.mkv"
    managed.runner.tick()
    assert current(managed)["status"] == "validating"
    managed.runner.tick()  # stop request is not yet acknowledged
    assert current(managed)["status"] == "validating"
    assert not managed.inspected and source.exists()
    managed.client.record["state"] = "stoppedUP"
    managed.runner.tick()
    assert current(managed)["status"] == "importing"
    assert managed.inspected == [source]
    managed.runner.tick()
    assert current(managed)["status"] == "importing" and source.exists()
    assert not Path(current(managed)["import_plan"]["destination"]).exists()
    managed.client.record = None
    managed.runner.tick()
    item = current(managed)
    assert item["status"] == "ingest_queued" and not source.exists()
    canonical = Path(item["film_path"])
    assert canonical.read_bytes() == FILES["Mirror.1975.mkv"]
    assert canonical.with_suffix(".en.srt").read_bytes() == SRT
    finish_ingestion(managed)
    managed.runner.tick()
    item = current(managed)
    assert item["status"] == "ready" and not root.exists()
    archive = Path(item["archive_path"])
    assert next(archive.rglob("Mirror.1975.en.srt")).read_bytes() == SRT
    assert next(archive.rglob("release-notes.txt")).read_bytes() == FILES["release-notes.txt"]
    assert canonical.read_bytes() == FILES["Mirror.1975.mkv"]
    assert len(managed.runner.lab.ingest_snapshots()) == 1


def test_restart_after_source_move_reuses_the_frozen_import_plan(managed, monkeypatch):
    root = validate_download(managed)
    detach_download(managed)
    original = managed.service.store.patch

    def crash_after_move(identity, **changes):
        if changes.get("status") == "ingest_queued":
            raise Crash()
        return original(identity, **changes)

    with monkeypatch.context() as patcher:
        patcher.setattr(managed.service.store, "patch", crash_after_move)
        with pytest.raises(Crash):
            managed.runner.step(current(managed))
    item = current(managed)
    assert item["status"] == "importing"
    assert not (root / "Mirror.1975.mkv").exists()
    assert Path(item["import_plan"]["destination"]).exists()
    managed.runner.tick()
    assert current(managed)["status"] == "ingest_queued"
    assert not managed.runner.lab.ingest_snapshots()


def test_restart_after_enqueue_reconciles_job_without_replaying_ingestion(managed, monkeypatch):
    import_download(managed)
    original = managed.service.store.patch

    def crash_after_enqueue(identity, **changes):
        if changes.get("ingest_job_id"):
            raise Crash()
        return original(identity, **changes)

    with monkeypatch.context() as patcher:
        patcher.setattr(managed.service.store, "patch", crash_after_enqueue)
        with pytest.raises(Crash):
            managed.runner.step(current(managed))
    assert current(managed)["ingest_job_id"] is None
    jobs = managed.runner.lab.ingest_snapshots()
    assert len(jobs) == 1
    managed.runner.tick()
    assert current(managed)["ingest_job_id"] == jobs[0]["job_id"]
    assert len(managed.runner.lab.ingest_snapshots()) == 1


def test_restart_after_archive_move_recovers_without_losing_raw_evidence(managed, monkeypatch):
    root = import_download(managed)
    finish_ingestion(managed)
    original = managed.service.store.patch

    def crash_after_archive(identity, **changes):
        if changes.get("status") == "ready":
            raise Crash()
        return original(identity, **changes)

    with monkeypatch.context() as patcher:
        patcher.setattr(managed.service.store, "patch", crash_after_archive)
        with pytest.raises(Crash):
            managed.runner.step(current(managed))
    assert current(managed)["status"] == "cleanup" and not root.exists()
    managed.runner.tick()
    assert current(managed)["status"] == "ready"
    archive = Path(current(managed)["archive_path"])
    assert next(archive.rglob("Mirror.1975.en.srt")).read_bytes() == SRT


@pytest.mark.parametrize("change", ["tags", "category", "save_path"])
def test_unowned_torrent_is_never_stopped_detached_or_imported(managed, change, tmp_path):
    root = fill_download(managed)
    managed.client.calls.clear()
    managed.client.record[change] = str(tmp_path / "somebody-elses-download")
    managed.runner.tick()
    assert current(managed)["status"] == "failed"
    assert {call[0] for call in managed.client.calls} == {"info"}
    assert (root / "Mirror.1975.mkv").read_bytes() == FILES["Mirror.1975.mkv"]
    assert not managed.config.paths.films_dir.exists()


def test_canonical_collision_preserves_both_sources(managed):
    root = validate_download(managed)
    detach_download(managed)
    destination = Path(current(managed)["import_plan"]["destination"])
    destination.write_bytes(b"Existing user film")
    managed.runner.tick()
    assert current(managed)["status"] == "failed"
    assert destination.read_bytes() == b"Existing user film"
    assert (root / "Mirror.1975.mkv").read_bytes() == FILES["Mirror.1975.mkv"]
    assert not managed.runner.lab.ingest_snapshots()


def test_source_mutation_after_validation_prevents_import(managed):
    root = validate_download(managed)
    detach_download(managed)
    source = root / "Mirror.1975.mkv"
    source.write_bytes(b"Changed after verification")
    managed.runner.tick()
    assert current(managed)["status"] == "failed"
    assert source.read_bytes() == b"Changed after verification"
    assert not Path(current(managed)["import_plan"]["destination"]).exists()


@pytest.mark.parametrize("change", ["extra-file", "changed-evidence", "missing-evidence", "missing-film"])
def test_cleanup_stops_when_verified_sources_or_evidence_change(managed, change):
    root = import_download(managed)
    finish_ingestion(managed)
    if change == "extra-file":
        (root / "unrecorded.txt").write_text("Unrelated new material")
    elif change == "changed-evidence":
        (root / "Mirror.1975.en.srt").write_bytes(b"Changed subtitle")
    elif change == "missing-evidence":
        (root / "Mirror.1975.en.srt").unlink()
    else:
        Path(current(managed)["film_path"]).unlink()
    managed.runner.tick()
    assert current(managed)["status"] == "failed"
    assert root.is_dir()
    assert (root / "release-notes.txt").is_file()


def test_cleanup_never_runs_without_publication(managed):
    root = import_download(managed)
    managed.runner.publication_check = lambda item: False
    managed.runner.tick()
    job = managed.runner.lab.claim()
    managed.runner.lab.finish(job["id"])
    managed.runner.tick()
    assert current(managed)["status"] == "failed"
    assert Path(current(managed)["film_path"]).exists() and root.exists()


def test_cancelled_download_waits_for_detach_cleans_and_retry_redownloads(managed):
    root = fill_download(managed)
    managed.client.record.update(progress=.5, state="downloading")
    item = current(managed)
    managed.service.cancel(item["id"], item["revision"])
    managed.runner.tick()
    assert current(managed)["cancel_requested"] is True
    managed.client.record["state"] = "stoppedDL"
    managed.runner.tick()
    assert current(managed)["status"] == "cancelling"
    assert (root / "Mirror.1975.mkv").exists()
    assert any(call[0] == "remove" for call in managed.client.calls)
    managed.client.record = None
    managed.runner.tick()
    item = current(managed)
    assert item["status"] == "cancelled"
    assert not root.parent.exists()
    assert item["cancellation_cleanup"] == "complete"
    managed.service.retry(item["id"], item["revision"])
    managed.runner.tick()
    assert current(managed)["status"] == "downloading"
    assert sum(call[0] == "add" for call in managed.client.calls) == 2
    assert not (root / "Mirror.1975.mkv").exists()


def test_cancelled_ingest_one_retry_creates_one_new_job(managed):
    import_download(managed)
    managed.runner.tick()
    item = current(managed)
    original_job = item["ingest_job_id"]
    managed.service.cancel(item["id"], item["revision"])
    managed.runner.tick()
    managed.runner.tick()
    item = current(managed)
    assert item["status"] == "cancelled"
    assert managed.runner.lab.get_job(original_job)["status"] == "cancelled"
    managed.service.retry(item["id"], item["revision"])
    managed.runner.tick()
    item = current(managed)
    assert item["status"] == "ingest_queued" and item["ingest_job_id"] != original_job
    assert len(managed.runner.lab.ingest_snapshots()) == 2
    managed.runner.tick()
    assert len(managed.runner.lab.ingest_snapshots()) == 2


def test_missing_client_download_explicit_retry_resubmits_same_owned_identity(managed):
    root = fill_download(managed)
    managed.client.record = None
    managed.runner.tick()
    item = current(managed)
    assert item["status"] == "failed"
    managed.service.retry(item["id"], item["revision"])
    managed.runner.tick()
    if current(managed)["status"] == "queued":
        managed.runner.tick()
    assert current(managed)["status"] == "downloading"
    assert managed.client.record["save_path"] == str(root.resolve())
    assert (root / "Mirror.1975.mkv").exists()


def test_inference_failure_is_never_retried_without_user_action(managed):
    root = import_download(managed)
    managed.runner.tick()
    job = managed.runner.lab.claim()
    managed.runner.lab.finish(job["id"], error="Hosted annotation completion uncertain")
    managed.runner.tick()
    assert current(managed)["status"] == "failed"
    managed.runner.tick()
    assert len(managed.runner.lab.ingest_snapshots()) == 1
    assert root.exists() and Path(current(managed)["film_path"]).exists()
    item = current(managed)
    managed.service.retry(item["id"], item["revision"])
    managed.runner.tick()
    assert current(managed)["status"] == "ingest_queued"
    assert len(managed.runner.lab.ingest_snapshots()) == 2


def test_subtitle_review_requires_choice_after_video_only_selection(managed):
    files = {"feature-one.mkv": b"first-film" * 1000, "feature-two.mkv": b"second-film" * 1000,
             "feature-two.srt": SPARSE_SRT}
    root = fill_download(managed, files)
    managed.runner.tick()
    managed.client.record["state"] = "stoppedUP"
    managed.runner.tick()
    item = current(managed)
    assert item["status"] == "needs_review" and item["review"]["selected_video"] is None
    managed.service.review(item["id"], item["revision"], "feature-two.mkv", None)
    managed.runner.tick()
    item = current(managed)
    assert item["status"] == "needs_review"
    assert item["review"]["selected_video"] == "feature-two.mkv"
    assert item["review"]["subtitles"]
    with pytest.raises(ValueError, match="subtitle"):
        managed.service.review(item["id"], item["revision"], "feature-two.mkv", None)
    managed.service.review(item["id"], item["revision"], "feature-two.mkv", {"action": "skip"})
    managed.runner.tick()
    assert current(managed)["status"] == "importing"
    assert current(managed)["import_plan"]["subtitle"] is None
    assert (root / "feature-two.srt").read_bytes() == SPARSE_SRT


def test_unmarked_complete_subtitle_passes_without_manual_review(managed):
    root = validate_download(managed, {"Mirror.1975.mkv": FILES["Mirror.1975.mkv"], "Mirror.1975.srt": SRT})
    plan = current(managed)["import_plan"]
    assert plan["subtitle"] == "Mirror.1975.srt"
    assert plan["subtitle_validation"]["automatic_eligible"] is True
    assert plan["subtitle_validation"]["profile"] == "external-srt-intake-v1"
    assert (root / "Mirror.1975.srt").read_bytes() == SRT


def test_automatic_review_uses_fallback_for_sparse_track(managed):
    root = fill_download(managed, {"Mirror.1975.mkv": FILES["Mirror.1975.mkv"], "Mirror.1975.en.srt": SPARSE_SRT})
    managed.runner.tick()
    managed.client.record["state"] = "stoppedUP"
    managed.runner.tick()
    item = current(managed)
    assert item["status"] == "needs_review"
    assert "sparse" in item["review"]["subtitles"][0]["validation"]
    managed.service.review(item["id"], item["revision"], "Mirror.1975.mkv", {"action": "auto"})
    managed.runner.tick()
    assert current(managed)["status"] == "importing"
    assert current(managed)["import_plan"]["subtitle"] is None
    assert (root / "Mirror.1975.en.srt").read_bytes() == SPARSE_SRT


def test_automatic_choice_can_check_a_newly_selected_video(managed):
    fill_download(managed, {"feature-one.mkv": b"film1", "feature-two.mkv": b"film2", "feature-two.srt": SRT})
    managed.runner.tick()
    managed.client.record["state"] = "stoppedUP"
    managed.runner.tick()
    item = current(managed)
    managed.service.review(item["id"], item["revision"], "feature-two.mkv", {"action": "auto"})
    managed.runner.tick()
    assert current(managed)["status"] == "importing"
    assert current(managed)["import_plan"]["subtitle"] == "feature-two.srt"


def test_verified_subtitle_mutation_cannot_be_imported(managed):
    root = validate_download(managed)
    detach_download(managed)
    (root / "Mirror.1975.en.srt").write_bytes(SRT.replace(b"house", b"garden"))
    managed.runner.tick()
    assert current(managed)["status"] == "failed"
    assert "subtitle changed" in current(managed)["error"].lower()
    assert (root / "Mirror.1975.mkv").exists()
    assert not (managed.config.paths.films_dir / "Mirror (1975).en.srt").exists()


def test_executable_release_is_rejected_before_detachment(managed):
    fill_download(managed, {**FILES, "setup.exe": b"Executable fixture"})
    managed.runner.tick()
    managed.client.record["state"] = "stoppedUP"
    managed.runner.tick()
    assert current(managed)["status"] == "failed"
    assert not any(call[0] == "remove" for call in managed.client.calls)


def test_inventory_cannot_traverse_outside_managed_root(managed, tmp_path):
    fill_download(managed)
    external = tmp_path / "outside.mkv"
    external.write_bytes(b"Unrelated source")
    managed.client.inventory.append({"name": "../outside.mkv", "size": external.stat().st_size, "progress": 1})
    managed.runner.tick()
    managed.client.record["state"] = "stoppedUP"
    managed.runner.tick()
    assert current(managed)["status"] == "failed"
    assert external.read_bytes() == b"Unrelated source"
    assert not any(call[0] == "remove" for call in managed.client.calls)


@pytest.mark.skipif(os.name != "nt", reason="Windows extended-path diagnostic")
@pytest.mark.parametrize("winerror", [3, 206])
@pytest.mark.parametrize("exists", [True, False])
def test_inventory_distinguishes_windows_path_limit_from_missing_long_file(managed, monkeypatch, winerror, exists):
    root = fill_download(managed)
    relative = "release-" + "x" * 110 + "/notes-" + "y" * 110 + ".nfo"
    path = root.joinpath(*relative.split("/"))
    assert len(str(path)) >= 260
    extended = Path("\\\\?\\" + str(path))
    content = b"Original release evidence"
    if exists:
        extended.parent.mkdir(parents=True)
        extended.write_bytes(content)
    managed.client.inventory.append({"name": relative, "size": len(content), "progress": 1})
    original_stat = Path.stat
    failure = FileNotFoundError(2, "private provider https://secret.invalid/token")
    failure.winerror = winerror

    def stat(candidate, *args, **kwargs):
        if candidate == path and kwargs.get("follow_symlinks", True):
            raise failure
        return original_stat(candidate, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", stat)
    managed.runner.tick()
    managed.client.record["state"] = "stoppedUP"
    managed.runner.tick()
    item = current(managed)
    assert item["status"] == "failed"
    if exists:
        assert "path is too long" in item["error"]
        assert "through qBittorrent" in item["error"] and "then retry" in item["error"]
        assert extended.read_bytes() == content
    else:
        assert item["error"] == "A required file or job is unavailable; inspect the acquisition and retry"
    assert "secret.invalid" not in item["error"] and "token" not in item["error"]
    assert not managed.inspected
    assert not any(call[0] == "remove" for call in managed.client.calls)
    assert not managed.runner.lab.ingest_snapshots()


def test_inventory_missing_normal_file_keeps_missing_file_guard(managed):
    root = fill_download(managed)
    managed.client.inventory.append({"name": "missing.nfo", "size": 10, "progress": 1})
    managed.runner.tick()
    managed.client.record["state"] = "stoppedUP"
    managed.runner.tick()
    assert current(managed)["error"] == "A required file or job is unavailable; inspect the acquisition and retry"
    assert (root / "Mirror.1975.mkv").read_bytes() == FILES["Mirror.1975.mkv"]
    assert not managed.inspected
    assert not any(call[0] == "remove" for call in managed.client.calls)


@pytest.mark.skipif(os.name != "nt", reason="Windows extended-path fixture")
def test_inventory_accepts_long_path_when_normal_filesystem_access_succeeds(managed, monkeypatch):
    root = fill_download(managed)
    relative = "release-" + "x" * 110 + "/notes-" + "y" * 110 + ".nfo"
    path = root.joinpath(*relative.split("/"))
    assert len(str(path)) >= 260
    extended = Path("\\\\?\\" + str(path))
    extended.parent.mkdir(parents=True)
    extended.write_bytes(b"Original evidence")
    managed.client.inventory.append({"name": relative, "size": extended.stat().st_size, "progress": 1})
    original_stat = Path.stat

    def stat(candidate, *args, **kwargs):
        # Emulate an OS/runtime with long-path support using the real fixture.
        return original_stat(extended if candidate == path else candidate, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", stat)
    inventory = managed.runner._inventory(current(managed), managed.client.record)
    record = next(row for row in inventory if row["relative_path"] == relative)
    assert record["size"] == record["fingerprint"]["size"] == len(b"Original evidence")
    assert extended.read_bytes() == b"Original evidence"


def test_import_rechecks_canonical_directory_for_reparse_changes(managed, monkeypatch):
    root = validate_download(managed)
    detach_download(managed)
    original = engine.is_link_or_junction
    films = managed.config.paths.films_dir
    monkeypatch.setattr(engine, "is_link_or_junction", lambda path: Path(path) == films or original(path))
    managed.runner.tick()
    assert current(managed)["status"] == "failed"
    assert (root / "Mirror.1975.mkv").exists()
    assert not Path(current(managed)["import_plan"]["destination"]).exists()


def test_archive_recovery_rechecks_raw_evidence_after_crash(managed, monkeypatch):
    import_download(managed)
    finish_ingestion(managed)
    original = managed.service.store.patch

    def crash_after_archive(identity, **changes):
        if changes.get("status") == "ready":
            raise Crash()
        return original(identity, **changes)

    with monkeypatch.context() as patcher:
        patcher.setattr(managed.service.store, "patch", crash_after_archive)
        with pytest.raises(Crash):
            managed.runner.step(current(managed))
    archive = managed.config.paths.incoming_dir.parent / "evidence" / "managed-releases" / managed.identity
    next(archive.rglob("Mirror.1975.en.srt")).unlink()
    managed.runner.tick()
    assert current(managed)["status"] == "failed"
    assert Path(current(managed)["film_path"]).exists()


def test_media_validation_cannot_open_network_resources(tmp_path, monkeypatch):
    path = tmp_path / "untrusted.mkv"
    path.write_bytes(b"Only a fixture")
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        if args[0] == "ffprobe":
            return SimpleNamespace(stdout=b'{"format":{"duration":"10"},"streams":[{"codec_type":"video","width":1920}]}')
        return SimpleNamespace(stdout=b"x" * (64 * 64 * 3))

    monkeypatch.setattr(engine.subprocess, "run", run)
    assert engine.inspect_media(path)["decode_samples"] == 3
    assert len(calls) == 4
    for command in calls:
        assert "-protocol_whitelist" in command
        protocols = command[command.index("-protocol_whitelist") + 1].split(",")
        assert set(protocols) <= {"file", "pipe"}


def test_completed_path_history_cannot_replace_this_acquisitions_ingest(managed):
    canonical = managed.config.paths.films_dir / "Mirror (1975).mkv"
    previous = managed.runner.lab.enqueue("ingest", path=canonical)
    managed.runner.lab.claim()
    managed.runner.lab.finish(previous["id"])
    with managed.runner.lab.connection() as con:
        con.execute("UPDATE jobs SET created_at=? WHERE id=?",
                    (current(managed)["created_at"] - 1000, previous["id"]))
    import_download(managed)
    managed.runner.publication_check = lambda item: False
    managed.runner.tick()
    item = current(managed)
    assert item["status"] == "ingest_queued"
    assert item["ingest_job_id"] != previous["id"]
    assert len(managed.runner.lab.ingest_snapshots()) == 2


def test_explicit_retry_can_repair_completed_ingest_missing_publication(managed):
    import_download(managed)
    managed.runner.tick()
    previous = managed.runner.lab.claim()
    managed.runner.lab.finish(previous["id"])
    managed.runner.publication_check = lambda item: False
    managed.runner.tick()
    item = current(managed)
    assert item["status"] == "failed"
    managed.service.retry(item["id"], item["revision"])
    managed.runner.tick()
    item = current(managed)
    assert item["status"] == "ingest_queued"
    assert item["ingest_job_id"] != previous["id"]
    assert len(managed.runner.lab.ingest_snapshots()) == 2
