"""Prune obsolete Lance versions while protecting readers and current data."""

from pathlib import Path
from unittest.mock import patch

from filelock import FileLock, Timeout
import lancedb
import pytest

from pipeline.index.maintenance import (
    MaintenanceUnavailable, Retention, _API_READERS_LOCK, _idle_maintenance,
    api_database_read_lease, maintain_database,
)
from pipeline.ingest.locks import global_ingest_lock
from pipeline.lab.worker_locks import worker_lock


def _db(config):
    root = config.paths.assets_dir / "db"
    root.mkdir(parents=True, exist_ok=True)
    return lancedb.connect(str(root))


def _native_runtime():
    module = pytest.importorskip("lance", reason="run with uv run --group maintenance pytest")
    assert module.__version__ == "9.0.0"


def _populated(config):
    db = _db(config)
    table = db.create_table("units", data=[{
        "id": 1, "text": "red room", "vector": [1.0, 0.0], "tags": ["red", "room"],
    }])
    for text in ("blue room", "green room", "red sunset"):
        table.merge_insert("id").when_matched_update_all().when_not_matched_insert_all().execute([{
            "id": 1, "text": text, "vector": [1.0, 0.0], "tags": [text],
        }])
        table.create_fts_index("text", replace=True)
    return db, table


def _files(root: Path):
    return {
        str(path.relative_to(root)): (path.stat().st_size, path.stat().st_mtime_ns)
        for path in root.rglob("*") if path.is_file()
        and not path.name.startswith(".scene-recall")
    }


def test_native_dry_run_and_pruning_keep_current_vectors_rows_and_fts(config):
    _native_runtime()
    db, table = _populated(config)
    root = config.paths.assets_dir / "db"
    version = table.version
    rows = table.search().limit(None).to_list()
    before = _files(root)
    policy = Retention(older_than_days=None, retain_versions=1)
    with patch("lancedb.table.LanceTable.optimize", side_effect=AssertionError("Compaction must never run")):
        plan = maintain_database(config, policy)
        assert _files(root) == before
        assert plan["mode"] == "dry_run"
        assert plan["estimated_bytes_removed"] > 0
        assert plan["tables"][0]["candidate"]["old_versions"] == 6
        result = maintain_database(config, policy, apply=True)
    assert result["actual_bytes_removed"] == plan["estimated_bytes_removed"]
    assert result["tables"][0]["removed"]["index_files_removed"] > 0
    fresh = db.open_table("units")
    assert fresh.version == version
    assert fresh.search().limit(None).to_list() == rows
    assert fresh.search("sunset", query_type="fts").limit(3).to_list()
    assert len(fresh.list_versions()) == 1
    assert fresh.index_stats("text_idx").num_unindexed_rows == 0


def test_apply_drops_retired_framing_derivations_before_pruning(config):
    _native_runtime()
    db, _ = _populated(config)
    db.create_table("frame_framing_old_cache", data=[{"frame_id": "f-0", "film_id": "f"}])
    manifests = config.paths.assets_dir / "feature-manifests" / "framing"
    manifests.mkdir(parents=True)
    (manifests / "old.json").write_text("{}", encoding="utf-8")
    kept = config.paths.assets_dir / "feature-manifests" / "text"
    kept.mkdir()

    plan = maintain_database(config, Retention(None, 1))
    assert plan["retired"]["tables"] == ["frame_framing_old_cache"]
    assert plan["retired"]["paths"] == [str(manifests)] and plan["retired"]["bytes"] > 0
    assert "frame_framing_old_cache" not in [item["table"] for item in plan["tables"]]
    assert "frame_framing_old_cache" in _db(config).table_names(), "a dry run removes nothing"

    result = maintain_database(config, Retention(None, 1), apply=True)
    assert result["retired_removed"]["tables"] == ["frame_framing_old_cache"]
    assert _db(config).table_names() == ["units"]
    assert not manifests.exists() and kept.is_dir()


def test_default_retention_preserves_recent_versions(config):
    _native_runtime()
    _db(config).create_table("films", [{"id": 1}]).add([{"id": 2}])
    plan = maintain_database(config)
    assert plan["retention"] == {"older_than_days": 14, "retain_versions": None}
    assert plan["estimated_bytes_removed"] == 0
    assert plan["tables"][0]["candidate"]["old_versions"] == 0


def test_tag_conflict_is_checked_for_all_tables_before_any_pruning(config):
    _native_runtime()
    db, table = _populated(config)
    other = db.create_table("z_tagged", [{"id": 1}])
    other.tags.create("keep-for-review", other.version)
    other.add([{"id": 2}])
    before = _files(config.paths.assets_dir / "db")
    with pytest.raises(Exception, match="[Tt]ag"):
        maintain_database(config, Retention(None, 1), apply=True)
    assert _files(config.paths.assets_dir / "db") == before
    assert len(table.list_versions()) == 7


@pytest.mark.parametrize("role", ["editor", "ingest", "all"])
def test_maintenance_refuses_worker_lifetime_locks(config, role):
    db = _db(config)
    with worker_lock(config.paths.state_dir / "lab", role):
        with pytest.raises(MaintenanceUnavailable, match="workers gracefully"):
            with _idle_maintenance(config, db):
                pytest.fail("active worker must block pruning")


def test_maintenance_refuses_independent_ingest_and_live_api(config):
    db = _db(config)
    with global_ingest_lock(config.paths.assets_dir):
        with pytest.raises(MaintenanceUnavailable, match="independent ingest"):
            with _idle_maintenance(config, db):
                pytest.fail("active ingest must block pruning")
    with api_database_read_lease(config), api_database_read_lease(config):
        with pytest.raises(MaintenanceUnavailable, match="Stop the API"):
            with _idle_maintenance(config, db):
                pytest.fail("API readers must block pruning")
    with _idle_maintenance(config, db):
        with pytest.raises(MaintenanceUnavailable, match="maintenance is active"):
            with api_database_read_lease(config):
                pytest.fail("new API readers must wait for maintenance")


def test_api_lifespan_holds_and_releases_database_reader_lease(config):
    from pipeline.tests.test_api_intake import _api_client

    path = config.paths.assets_dir / "db" / _API_READERS_LOCK
    with _api_client(config):
        with pytest.raises(Timeout):
            with FileLock(path, timeout=0, preserve_lock_file=True):
                pytest.fail("API lifetime must protect retained readers")
    with FileLock(path, timeout=0, preserve_lock_file=True):
        pass


@pytest.mark.parametrize("age,count", [(0, None), (-1, None), (float("nan"), None), (None, 0), (None, -1), (14, 2)])
def test_invalid_retention_never_reaches_native_code(age, count):
    with pytest.raises(ValueError):
        Retention(age, count)
