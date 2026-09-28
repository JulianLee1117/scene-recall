"""Durable queue updates remain atomic, private, bounded, and recoverable."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from threading import Barrier

import pytest

from pipeline.acquisition import store as module
from pipeline.acquisition.store import AcquisitionConflict, AcquisitionStore


@pytest.fixture
def ledger(tmp_path):
    store = AcquisitionStore(tmp_path)
    store.initialize()
    return store


def create(store, number=1, **overrides):
    return store.create(f"{number:040x}", title="Mirror", year=1975, edition="",
                        source={"kind": "magnet", "magnet": "magnet:?private=secret"}, **overrides)


def test_acquisition_persists_after_reopening_and_inputs_are_snapshots(ledger):
    item = create(ledger)
    item["source"]["magnet"] = "Changed caller state"
    ledger.patch(item["id"], revision=1, status="downloading", progress=.25)
    reopened = AcquisitionStore(ledger.root.parent)
    reopened.initialize()
    persisted = reopened.get(item["id"])
    assert persisted["status"] == "downloading" and persisted["revision"] == 2
    assert persisted["source"]["magnet"] == "magnet:?private=secret"


def test_concurrent_duplicate_intents_create_exactly_one_acquisition(ledger):
    barrier = Barrier(5)

    def attempt(_):
        barrier.wait(timeout=5)
        try:
            return create(ledger)
        except AcquisitionConflict:
            return None

    with ThreadPoolExecutor(max_workers=5) as executor:
        result = list(executor.map(attempt, range(5)))
    assert sum(item is not None for item in result) == 1
    assert len(ledger.list()) == 1


def test_concurrent_revision_updates_do_not_overwrite_each_other(ledger):
    item = create(ledger)
    barrier = Barrier(2)

    def attempt(message):
        barrier.wait(timeout=5)
        try:
            return ledger.patch(item["id"], revision=1, message=message)
        except AcquisitionConflict:
            return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        result = list(executor.map(attempt, ["First review", "Second review"]))
    successful = [item for item in result if item is not None]
    assert len(successful) == 1
    assert ledger.get(item["id"]) == successful[0]


def test_failed_validation_rolls_back_revision_and_document(ledger):
    item = create(ledger)
    for changes in ({"progress": float("nan")}, {"status": "invented"}, {"info_hash": "different"},
                    {"id": "different"}, {"created_at": 1}):
        with pytest.raises(ValueError):
            ledger.patch(item["id"], revision=1, **changes)
        assert ledger.get(item["id"]) == item


def test_delete_requires_a_stopped_acquisition_and_matching_revision(ledger):
    item = create(ledger)
    with pytest.raises(AcquisitionConflict, match="stopped"):
        ledger.delete(item["id"], item["revision"])
    stopped = ledger.patch(item["id"], revision=item["revision"], status="cancelled")
    with pytest.raises(AcquisitionConflict, match="changed"):
        ledger.delete(item["id"], stopped["revision"] - 1)
    assert ledger.get(item["id"]) == stopped
    with pytest.raises(KeyError):
        ledger.delete("missing", 1)


def test_deleting_an_acquisition_frees_its_info_hash_for_reuse(ledger):
    item = ledger.patch(create(ledger)["id"], status="failed")
    ledger.delete(item["id"], item["revision"])
    with pytest.raises(KeyError):
        ledger.get(item["id"])
    assert ledger.list() == []
    fresh = create(ledger)
    assert fresh["info_hash"] == item["info_hash"] and fresh["id"] != item["id"]


def test_queue_capacity_is_atomic_under_concurrent_creates(ledger):
    for number in range(1, 100):
        create(ledger, number)
    barrier = Barrier(4)

    def attempt(number):
        barrier.wait(timeout=5)
        try:
            return create(ledger, number)
        except AcquisitionConflict:
            return None

    with ThreadPoolExecutor(max_workers=4) as executor:
        result = list(executor.map(attempt, range(100, 104)))
    assert sum(item is not None for item in result) == 1
    assert len(ledger.list(active=True, limit=1000)) == 100


def test_retry_cannot_exceed_active_queue_capacity(ledger):
    stopped = create(ledger)
    ledger.patch(stopped["id"], status="failed")
    for number in range(2, 102):
        create(ledger, number)
    with pytest.raises(AcquisitionConflict, match="100"):
        ledger.patch(stopped["id"], revision=2, status="queued")
    assert ledger.get(stopped["id"])["status"] == "failed"
    assert len(ledger.list(active=True, limit=1000)) == 100


def test_active_items_cannot_be_hidden_by_recent_finished_history(ledger):
    active = create(ledger)
    for number in range(2, 12):
        item = create(ledger, number)
        ledger.patch(item["id"], status="ready")
    assert ledger.list(limit=1)[0]["id"] == active["id"]
    assert [item["id"] for item in ledger.list(active=True)] == [active["id"]]


def test_public_state_excludes_private_sources_and_recovery_manifests(ledger):
    item = create(ledger)
    item = ledger.patch(item["id"], import_plan={"secret": "credentials"},
                        archive_path="private archive", detach_requested=True, retry_requested=True)
    public = ledger.public(item)
    assert {"source", "info_hash", "import_plan", "archive_path", "detach_requested", "retry_requested"}.isdisjoint(public)
    assert not any(secret in json.dumps(public) for secret in ("secret", "credentials", "private archive"))
    assert public["id"] == item["id"] and public["revision"] == item["revision"]


def test_release_results_are_opaque_bounded_and_expire(ledger, monkeypatch):
    timestamp = 1000.0
    monkeypatch.setattr(module.time, "time", lambda: timestamp)
    candidate = {"title": "Mirror", "size": 1234, "seeders": 4, "indexer": "Fixture indexer",
                 "download_url": "http://provider/release?apikey=secret", "magnet": "magnet:?secret=token"}
    results = ledger.save_releases([candidate] * 51)
    assert len(results) == 50
    assert len({item["id"] for item in results}) == 50
    assert all(set(item) == {"id", "title", "size", "seeders", "indexer"} for item in results)
    assert "secret" not in json.dumps(results)
    assert ledger.release(results[0]["id"]) == candidate
    timestamp += 1801
    with pytest.raises(AcquisitionConflict, match="expired"):
        ledger.release(results[0]["id"])
    with pytest.raises(AcquisitionConflict):
        ledger.release("http://arbitrary-url/")
    ledger.save_releases([])
    with ledger.connection() as con:
        assert con.execute("SELECT count(*) FROM releases").fetchone()[0] == 0


def test_failed_release_batch_does_not_leave_partial_results(ledger):
    with pytest.raises(ValueError):
        ledger.save_releases([{"title": "First"}, {"title": "Second", "size": float("nan")}])
    with ledger.connection() as con:
        assert con.execute("SELECT count(*) FROM releases").fetchone()[0] == 0


def test_monitor_liveness_expires_without_deleting_queue(ledger, monkeypatch):
    timestamp = 1000.0
    monkeypatch.setattr(module.time, "time", lambda: timestamp)
    item = create(ledger)
    assert ledger.monitor_status() == {"running": False, "last_seen": None}
    ledger.heartbeat()
    assert ledger.monitor_status() == {"running": True, "last_seen": timestamp}
    timestamp += 91
    assert ledger.monitor_status() == {"running": False, "last_seen": 1000.0}
    assert ledger.get(item["id"])["status"] == "queued"


def test_unknown_database_version_is_never_overwritten(ledger):
    item = create(ledger)
    with ledger.connection() as con:
        con.execute("PRAGMA user_version=999")
    with pytest.raises(RuntimeError, match="Unsupported"):
        ledger.initialize()
    with ledger.connection() as con:
        assert con.execute("PRAGMA user_version").fetchone()[0] == 999
    assert ledger.get(item["id"]) == item
