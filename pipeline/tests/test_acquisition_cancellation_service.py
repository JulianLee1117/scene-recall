"""Cancellation intent survives monitor races and retries respect cleaned staging."""
import json
from unittest.mock import MagicMock

import pytest

from pipeline.acquisition.service import AcquisitionService
from pipeline.acquisition.settings import AcquisitionSettings
from pipeline.acquisition.store import AcquisitionConflict, AcquisitionStore


@pytest.fixture
def service(config):
    result = AcquisitionService(config, settings=AcquisitionSettings(),
                                downloader=MagicMock(), search_client=MagicMock())
    result.downloader.reset_mock()
    return result


def create(service, number=1, **changes):
    item = service.store.create(f"{number:040x}", title="Film", year=2000, edition="",
                                source={"kind": "magnet", "magnet": "private-source"})
    return service.store.patch(item["id"], **changes) if changes else item


@pytest.mark.parametrize("stage", ["queued", "downloading", "validating", "needs_review",
                                   "importing", "ingest_queued", "ingesting", "cleanup"])
def test_cancel_enters_durable_cleanup_without_client_or_filesystem_work(service, stage):
    plan = {"destination": "preserved canonical path", "film_id": "source-id"}
    item = create(service, status=stage, resume_status="outdated-stage", import_plan=plan)
    result = service.cancel(item["id"], item["revision"])
    assert result["status"] == "cancelling"
    assert result["cancel_requested"] is True
    assert result["cancellation_cleanup"] == "pending"
    assert result["revision"] == item["revision"] + 1
    assert "import_plan" not in result and "source" not in result
    reopened = AcquisitionStore(service.store.root.parent)
    current = reopened.get(item["id"])
    assert current["resume_status"] == stage
    assert current["import_plan"] == plan
    assert current["source"] == item["source"]
    assert current["retry_requested"] is False
    assert [i["id"] for i in reopened.list(active=True)] == [item["id"]]
    assert not service.downloader.mock_calls


@pytest.mark.parametrize("prior,expected", [
    ({"resume_status": "validating"}, "validating"),
    ({}, "queued"),
    ({"import_plan": {"destination": "movie.mkv"}}, "importing"),
    ({"film_path": "movie.mkv", "import_plan": {"destination": "movie.mkv"}}, "ingest_queued"),
])
def test_failed_acquisition_can_be_cancelled_and_retains_recovery_stage(service, prior, expected):
    item = create(service, status="failed", error="previous problem", **prior)
    service.cancel(item["id"], item["revision"])
    current = service.store.get(item["id"])
    assert current["status"] == "cancelling"
    assert current["resume_status"] == expected
    assert current["error"] is None


@pytest.mark.parametrize("stage", ["ready", "cancelled", "cancelling"])
def test_cancel_rejects_completed_or_already_requested_states(service, stage):
    item = create(service, status=stage)
    with pytest.raises(AcquisitionConflict):
        service.cancel(item["id"], item["revision"])
    assert service.store.get(item["id"]) == item


def test_cancel_stale_revision_does_not_overwrite_a_changed_item(service):
    item = create(service)
    changed = service.store.patch(item["id"], message="advanced")
    with pytest.raises(AcquisitionConflict, match="changed"):
        service.cancel(item["id"], item["revision"])
    assert service.store.get(item["id"]) == changed


def test_failed_cleanup_is_allowed_even_when_active_queue_is_full(service):
    failed = create(service, status="failed", resume_status="downloading")
    for number in range(2, 102):
        create(service, number)
    result = service.cancel(failed["id"], failed["revision"])
    assert result["status"] == "cancelling"
    assert len(service.store.list(active=True, limit=1000)) == 101


@pytest.mark.parametrize("stale_stage", ["downloading", "importing", "ingest_queued", "ingesting",
                                         "cleanup", "ready", "failed"])
def test_stale_monitor_journals_cannot_erase_pending_cancellation(service, stale_stage):
    item = create(service, status="validating")
    service.cancel(item["id"], item["revision"])
    before = service.store.get(item["id"])
    plan = {"destination": "canonical.mkv"}
    current = service.store.patch(item["id"], status=stale_stage, cancel_requested=False,
                                  cancellation_cleanup=None, retry_requested=True,
                                  message="stale stage message", error="stale stage failure",
                                  resume_status="stale recovery", import_plan=plan,
                                  film_path="canonical.mkv", film_id="source-id", ingest_job_id="new-job")
    assert current["status"] == "cancelling"
    assert current["cancel_requested"] is True
    assert current["cancellation_cleanup"] == "pending"
    assert current["retry_requested"] is False
    assert current["resume_status"] == "validating"
    assert current["message"] == before["message"]
    assert current["error"] == before["error"]
    assert current["import_plan"] == plan
    assert current["film_path"] == "canonical.mkv"
    assert current["film_id"] == "source-id"
    assert current["ingest_job_id"] == "new-job"
    assert service.store.list(active=True)[0]["id"] == item["id"]


def test_stale_ingest_failure_does_not_drop_cancellation_job_reference(service):
    item = create(service, status="ingesting", ingest_job_id="running-job", film_path="canonical.mkv")
    service.cancel(item["id"], item["revision"])
    current = service.store.patch(item["id"], status="failed", ingest_job_id=None,
                                  film_path=None, resume_status="ingest_queued")
    assert current["status"] == "cancelling"
    assert current["ingest_job_id"] == "running-job"
    assert current["film_path"] == "canonical.mkv"
    assert current["resume_status"] == "ingesting"


def test_cancellation_wait_errors_remain_visible_until_explicit_completion(service):
    item = create(service)
    service.cancel(item["id"], item["revision"])
    waiting = service.store.patch(item["id"], status="cancelling", cancel_requested=False,
                                  error="client unavailable", message="Waiting to stop the client")
    assert waiting["cancel_requested"] is True
    assert waiting["error"] == "client unavailable"
    complete = service.store.patch(item["id"], status="cancelled", cancel_requested=False,
                                   cancellation_cleanup="complete", resume_status="queued", error=None)
    assert complete["status"] == "cancelled"
    assert complete["cancel_requested"] is False
    assert complete["cancellation_cleanup"] == "complete"
    assert service.store.list(active=True) == []


@pytest.mark.parametrize("state", [
    {"status": "cancelling", "cancel_requested": True, "cancellation_cleanup": "pending"},
    {"status": "failed", "cancel_requested": True},
    {"status": "failed", "cancellation_cleanup": "pending"},
])
def test_retry_cannot_race_unfinished_cancellation(service, state):
    item = create(service, **state)
    with pytest.raises(AcquisitionConflict, match="cleanup"):
        service.retry(item["id"], item["revision"])
    assert service.store.get(item["id"]) == item


def test_retry_after_preimport_cleanup_restarts_download_with_original_source(service):
    item = create(service, status="cancelled", cancellation_cleanup="complete",
                  resume_status="importing", import_plan={"stale": True}, metadata_checked=True,
                  detach_requested=True, review={"old": "choices"}, video_choice="old.mkv",
                  subtitle_decision={"action": "use", "relative_path": "old.srt"}, progress=1.0,
                  download_rate=123, eta_seconds=1, bytes_total=999, bytes_done=999,
                  ingest_job_id="old-job", film_id="old-id", archive_path="old archive",
                  submission_attempted=True, submission_seen=True, cancel_detach_requested=True,
                  cancellation_owner={"device": 1, "inode": 123}, cancellation_retry_at=9999)
    result = service.retry(item["id"], item["revision"])
    current = service.store.get(item["id"])
    assert result["status"] == current["resume_status"] == "queued"
    assert result["cancellation_cleanup"] is None
    assert current["source"] == item["source"]
    assert current["info_hash"] == item["info_hash"]
    assert current["retry_requested"] is True
    assert current["metadata_checked"] is False and current["detach_requested"] is False
    for key in ("import_plan", "review", "video_choice", "subtitle_decision", "progress",
                "download_rate", "eta_seconds", "bytes_total", "bytes_done", "ingest_job_id",
                "film_id", "film_path", "archive_path", "cancellation_owner", "cancellation_retry_at"):
        assert current[key] is None, key
    for key in ("submission_attempted", "submission_seen", "cancel_detach_requested"):
        assert current[key] is False, key
    assert not service.downloader.mock_calls


def test_retry_after_imported_cleanup_preserves_movie_plan_and_cleanup_receipt(service):
    plan = {"destination": "canonical.mkv", "source_fingerprint": {"size": 123}}
    item = create(service, status="cancelled", cancellation_cleanup="complete", resume_status="cleanup",
                  film_path="canonical.mkv", film_id="film-id", import_plan=plan,
                  ingest_job_id="cancelled-job", detach_requested=True,
                  cancellation_owner={"device": 1, "inode": 123}, cancel_detach_requested=True,
                  submission_attempted=True, submission_seen=True, cancellation_retry_at=9999)
    result = service.retry(item["id"], item["revision"])
    current = service.store.get(item["id"])
    assert result["status"] == current["resume_status"] == "ingest_queued"
    assert result["cancellation_cleanup"] == "complete"
    assert current["film_path"] == "canonical.mkv" and current["film_id"] == "film-id"
    assert current["import_plan"] == plan
    assert current["ingest_job_id"] is None
    assert current["source"] == item["source"]
    assert current["detach_requested"] is True
    assert current["cancellation_owner"] == item["cancellation_owner"]
    assert current["cancel_detach_requested"] is True
    assert current["submission_attempted"] is True and current["submission_seen"] is True
    assert current["cancellation_retry_at"] is None
    assert current["retry_requested"] is True


@pytest.mark.parametrize("stage,expected", [("downloading", "downloading"), ("needs_review", "validating"),
                                           ("importing", "importing"), ("ingest_queued", "ingest_queued")])
def test_legacy_cancelled_items_keep_old_retry_behavior(service, stage, expected):
    item = create(service, status="cancelled", resume_status=stage, metadata_checked=True,
                  import_plan={"retained": True}, ingest_job_id="old-job")
    service.retry(item["id"], item["revision"])
    current = service.store.get(item["id"])
    assert current["status"] == expected
    assert current["metadata_checked"] is True
    assert current["import_plan"] == {"retained": True}
    assert current.get("cancellation_cleanup") is None


def managed_root(service, item):
    root = service.config.paths.incoming_dir / ".scene-recall-managed" / item["id"]
    (root / "data" / "release").mkdir(parents=True)
    (root / ".acquisition.json").write_text(
        json.dumps({"id": item["id"], "info_hash": item["info_hash"]}))
    (root / "data" / "release" / "film.mkv").write_bytes(b"leftover")
    return root


@pytest.mark.parametrize("stage", ["queued", "downloading", "needs_review", "importing", "ingesting"])
def test_dismiss_rejects_an_acquisition_that_is_still_active(service, stage):
    item = create(service, status=stage)
    with pytest.raises(AcquisitionConflict, match="stopped"):
        service.dismiss(item["id"], item["revision"])
    assert service.store.get(item["id"]) == item


def test_dismiss_rejects_a_stale_revision(service):
    item = create(service, status="cancelled")
    changed = service.store.patch(item["id"], message="advanced")
    with pytest.raises(AcquisitionConflict, match="changed"):
        service.dismiss(item["id"], item["revision"])
    assert service.store.get(item["id"]) == changed


@pytest.mark.parametrize("stage", ["ready", "failed", "cancelled"])
def test_dismiss_removes_leftover_staging_and_the_record(service, stage):
    item = create(service, status=stage)
    root = managed_root(service, item)
    service.dismiss(item["id"], item["revision"])
    assert not root.exists()
    with pytest.raises(KeyError):
        service.store.get(item["id"])
    assert item["id"] not in [row["id"] for row in service.store.list()]


def test_dismiss_without_leftover_staging_only_removes_the_record(service):
    service.config.paths.incoming_dir.mkdir(parents=True, exist_ok=True)
    item = create(service, status="cancelled")
    service.dismiss(item["id"], item["revision"])
    with pytest.raises(KeyError):
        service.store.get(item["id"])


def test_dismissing_an_acquisition_frees_its_info_hash_for_reuse(service):
    service.config.paths.incoming_dir.mkdir(parents=True, exist_ok=True)
    item = create(service, status="cancelled")
    service.dismiss(item["id"], item["revision"])
    fresh = create(service)
    assert fresh["info_hash"] == item["info_hash"] and fresh["id"] != item["id"]


def _import_on_disk(service, config, *, subtitle=True):
    films = config.paths.films_dir
    films.mkdir(parents=True, exist_ok=True)
    film = films / "Film (2000).mp4"
    film.write_bytes(b"video")
    sidecar = films / "Film (2000).en.srt"
    sidecar.write_text("1\n00:00:00,000 --> 00:00:01,000\nhello\n", encoding="utf-8")
    assets = config.paths.assets_dir / "film-id"
    assets.mkdir(parents=True, exist_ok=True)
    (assets / "dialogue.json").write_text("{}", encoding="utf-8")
    plan = {"destination": str(film), "subtitle": "release/sub.srt" if subtitle else None}
    item = create(service, status="cancelled", film_path=str(film), film_id="film-id", import_plan=plan)
    return item, film, sidecar, assets


def test_dismiss_removes_an_import_that_never_became_searchable(service, config, monkeypatch):
    service.config.paths.incoming_dir.mkdir(parents=True, exist_ok=True)
    item, film, sidecar, assets = _import_on_disk(service, config)
    monkeypatch.setattr(service, "_published", lambda film_id: False)
    assert service.dismiss(item["id"], item["revision"]) == {"removed_import": True}
    assert not film.exists() and not sidecar.exists() and not assets.exists()
    assert item["id"] not in [row["id"] for row in service.store.list()]


def test_dismiss_keeps_a_searchable_film_and_a_subtitle_it_did_not_place(service, config, monkeypatch):
    service.config.paths.incoming_dir.mkdir(parents=True, exist_ok=True)
    item, film, sidecar, assets = _import_on_disk(service, config, subtitle=False)
    monkeypatch.setattr(service, "_published", lambda film_id: True)
    assert service.dismiss(item["id"], item["revision"]) == {"removed_import": False}
    assert film.exists() and sidecar.exists() and assets.exists()
    other, film, sidecar, assets = _import_on_disk(service, config, subtitle=False)
    other = service.store.patch(other["id"], film_id="other-id")
    monkeypatch.setattr(service, "_published", lambda film_id: False)
    assert service.dismiss(other["id"], other["revision"]) == {"removed_import": True}
    assert not film.exists() and sidecar.exists()


def test_dismiss_refuses_while_the_import_is_queued_for_preparation(service, config, monkeypatch):
    from pipeline.lab.store import LabStore
    service.config.paths.incoming_dir.mkdir(parents=True, exist_ok=True)
    item, film, sidecar, assets = _import_on_disk(service, config)
    lab = LabStore(config.paths.state_dir)
    lab.root.mkdir(parents=True, exist_ok=True)
    lab.path.touch()                                             # the lab database now exists
    monkeypatch.setattr(LabStore, "ingest_snapshots", lambda self: [{"path": str(film), "status": "queued"}])
    with pytest.raises(AcquisitionConflict):
        service.dismiss(item["id"], item["revision"])
    assert film.exists() and service.store.get(item["id"])
