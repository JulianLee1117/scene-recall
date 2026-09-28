"""Bounded, read-only storage accounting and its nonblocking API cache."""

from __future__ import annotations

from pathlib import Path
import os
import stat
import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import pipeline.library_storage as storage


def _write(path: Path, length: int) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * length)
    return path


def _prepare(config):
    for root in (config.paths.films_dir, config.paths.assets_dir, config.paths.state_dir):
        root.mkdir(parents=True, exist_ok=True)


def _scan(config, sources=(), **kwargs):
    return storage.scan_library_storage(config, lambda: sources, model_scopes=lambda _: [], **kwargs)


def _categories(snapshot):
    return {category["id"]: category for category in snapshot["categories"]}


def _wait_ready(service):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        result = service.get()
        if result["status"] != "scanning":
            return result
        time.sleep(0.005)
    pytest.fail("background storage scan did not complete")


def test_counts_all_known_roots_and_external_files_without_overlap(config, tmp_path):
    config.paths.state_dir = config.paths.assets_dir / "state"
    _prepare(config)
    film = _write(config.paths.films_dir / "film.mkv", 100)
    _write(config.paths.films_dir / "film.srt", 7)
    external = _write(tmp_path / "outside" / "external.mkv", 23)
    _write(external.parent / "unrelated.mkv", 999)
    _write(config.paths.assets_dir / "matching" / "models" / "weights", 11)
    _write(config.paths.assets_dir / "lab" / "beat-this" / "weights", 13)
    _write(config.paths.assets_dir / "db" / "units.lance" / "data", 17)
    _write(config.paths.assets_dir / "keyframes" / "frame.webp", 19)
    _write(config.paths.state_dir / "lab" / "tracks" / "original.wav", 29)
    _write(config.paths.incoming_dir / "download.part", 31)
    _write(config.paths.incoming_dir.parent / "evidence" / "imported-releases" / "extra.mkv", 37)
    _write(config.paths.incoming_dir.parent / "evidence" / "managed-releases" / "subtitle.srt", 41)
    snapshot = _scan(config, [film, external, external])
    counts = _categories(snapshot)
    assert {key: value["bytes"] for key, value in counts.items()} == {
        "source_films": 130, "models": 24, "indexes": 17, "derived_assets": 19,
        "user_state": 29, "incoming": 31, "managed_archive": 78,
    }
    assert snapshot["total_bytes"] == 328
    assert snapshot["file_count"] == 11
    assert snapshot["measurement"] == "logical_file_bytes"
    assert not snapshot["incomplete"]
    assert sum(item["bytes"] for item in snapshot["volumes"]) == 328


def test_hardlinks_count_once_across_sources_downloads_and_indexes(config):
    _prepare(config)
    film = _write(config.paths.films_dir / "film.mkv", 71)
    aliases = [config.paths.incoming_dir / "film.mkv", config.paths.assets_dir / "db" / "alias"]
    for alias in aliases:
        alias.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.link(film, alias)
        except OSError as exc:
            pytest.skip(f"Hard links unavailable: {exc}")
    snapshot = _scan(config, [film])
    assert snapshot["total_bytes"] == 71
    assert snapshot["file_count"] == 1
    assert _categories(snapshot)["source_films"]["bytes"] == 71
    assert not snapshot["incomplete"]


def test_separate_playback_root_is_counted_once_and_missing_root_is_reported(config, tmp_path):
    _prepare(config)
    config.paths.playback_dir = tmp_path / "separate-playback"
    _write(config.paths.playback_dir / "film" / "profile" / "video.mp4", 113)
    snapshot = _scan(config)
    assert _categories(snapshot)["derived_assets"]["bytes"] == 113
    assert snapshot["total_bytes"] == 113
    assert not snapshot["incomplete"]

    config.paths.playback_dir = config.paths.assets_dir / "playback"
    _write(config.paths.playback_dir / "video.mp4", 23)
    assert _scan(config)["total_bytes"] == 23
    config.paths.playback_dir = tmp_path / "offline-playback"
    snapshot = _scan(config)
    assert snapshot["incomplete"]
    assert _categories(snapshot)["derived_assets"]["incomplete"]


def test_missing_metadata_and_unreadable_files_return_partial_totals(config, monkeypatch):
    _prepare(config)
    _write(config.paths.films_dir / "ok.mkv", 8)
    denied = _write(config.paths.assets_dir / "unreadable", 12)
    real_stat = os.stat

    def read_stat(path, **kwargs):
        if Path(path) == denied:
            raise PermissionError("private error details")
        return real_stat(path, **kwargs)

    def unavailable_sources():
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(storage.os, "stat", read_stat)
    snapshot = storage.scan_library_storage(config, unavailable_sources, model_scopes=lambda _: [])
    assert snapshot["total_bytes"] == 8
    assert snapshot["incomplete"]
    assert _categories(snapshot)["source_films"]["incomplete"]
    assert _categories(snapshot)["derived_assets"]["incomplete"]
    assert len(snapshot["issues"]) == 2
    assert "private error" not in str(snapshot)


def test_registered_directory_never_scans_unrelated_contents(config, tmp_path):
    _prepare(config)
    outside = tmp_path / "outside"
    _write(outside / "unrelated", 999)
    _write(config.paths.films_dir / "inside.mkv", 10)
    snapshot = _scan(config, [outside, config.paths.films_dir])
    assert snapshot["total_bytes"] == 10
    assert snapshot["incomplete"]
    assert any("expected source" in issue["reason"] for issue in snapshot["issues"])


def test_reparse_directory_and_registered_path_through_it_are_not_followed(config, tmp_path, monkeypatch):
    _prepare(config)
    junction = config.paths.assets_dir / "junction"
    external = _write(junction / "unrelated.mkv", 999)
    real_stat = os.stat

    def read_stat(path, **kwargs):
        info = real_stat(path, **kwargs)
        if Path(path) == junction:
            return SimpleNamespace(st_mode=info.st_mode, st_file_attributes=0x400)
        return info

    monkeypatch.setattr(storage.os, "stat", read_stat)
    snapshot = _scan(config, [external])
    assert snapshot["total_bytes"] == 0
    assert snapshot["incomplete"]
    assert any("junction" in issue["reason"] for issue in snapshot["issues"])


def test_selected_hf_cache_counts_blob_once_and_ignores_safe_snapshot_link(config, tmp_path, monkeypatch):
    _prepare(config)
    cache = tmp_path / "hub" / "models--selected--model"
    blob = _write(cache / "blobs" / "weights", 53)
    link = _write(cache / "snapshots" / "rev" / "weights", 53)
    _write(cache.parent / "models--unrelated--model" / "weights", 999)
    real_stat = os.stat

    def read_stat(path, **kwargs):
        if Path(path) == link and kwargs.get("follow_symlinks") is False:
            return SimpleNamespace(st_mode=stat.S_IFLNK, st_file_attributes=0x400)
        return real_stat(path, **kwargs)

    monkeypatch.setattr(storage.os, "stat", read_stat)
    monkeypatch.setattr(storage.os, "readlink", lambda path: str(blob))
    snapshot = storage.scan_library_storage(config, lambda: [], model_scopes=lambda _: [
        storage.StorageScope("models", cache, cache_links=True),
    ])
    assert snapshot["total_bytes"] == 53
    assert snapshot["file_count"] == 1
    assert not snapshot["incomplete"]


def test_shared_cache_resolution_is_limited_to_configured_model_repositories(config, tmp_path, monkeypatch):
    import huggingface_hub.constants

    monkeypatch.setattr(huggingface_hub.constants, "HF_HUB_CACHE", str(tmp_path / "hub"))
    scopes = storage.configured_model_scopes(config)
    assert {scope.path.name for scope in scopes} == {
        "models--timm--PE-Core-L-14-336",
        "models--Qwen--Qwen3-Embedding-0.6B",
        "models--Systran--faster-whisper-large-v3",
    }
    assert all(scope.cache_links for scope in scopes)
    assert all(scope.path.parent == tmp_path / "hub" for scope in scopes)


def test_background_cache_coalesces_refresh_preserves_snapshot_and_reports_failure():
    entered, release = threading.Event(), threading.Event()
    calls = []

    def scan():
        calls.append(threading.current_thread().name)
        entered.set()
        assert release.wait(3)
        if len(calls) == 3:
            raise OSError("internal storage error")
        return {"total_bytes": len(calls)}

    service = storage.LibraryStorageStats(scan)
    try:
        first = service.get()
        assert first["status"] == "scanning" and first["snapshot"] is None
        assert entered.wait(1)
        assert calls == ["library-storage"]
        for _ in range(4):
            assert service.get(refresh=True)["status"] == "scanning"
        release.set()
        ready = _wait_ready(service)
        assert ready["snapshot"] == {"total_bytes": 1}
        ready["snapshot"]["total_bytes"] = 999
        assert service.get()["snapshot"]["total_bytes"] == 1
        release.clear()
        refreshing = service.get(refresh=True)
        assert refreshing["status"] == "scanning"
        assert refreshing["snapshot"]["total_bytes"] == 1
        release.set()
        assert _wait_ready(service)["snapshot"]["total_bytes"] == 2
        service.get(refresh=True)
        failed = _wait_ready(service)
        assert failed["status"] == "error"
        assert failed["snapshot"]["total_bytes"] == 2
        assert "internal storage" not in failed["error"]
        assert len(calls) == 3
    finally:
        release.set()
        service.close()


def test_scan_can_be_cancelled_and_closed_cache_does_not_start_work(config):
    cancelled = threading.Event()
    cancelled.set()
    with pytest.raises(storage.StorageScanCancelled):
        _scan(config, cancelled=cancelled)
    scan = MagicMock()
    cancel = MagicMock()
    service = storage.LibraryStorageStats(scan, cancel=cancel)
    service.close()
    service.get(refresh=True)
    scan.assert_not_called()
    cancel.assert_called_once()


def test_storage_route_uses_cached_service_and_registered_lookup_reads_all_paths(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    import pipeline.api.main as api

    service = MagicMock()
    service.get.return_value = {"status": "scanning", "started_at": None, "snapshot": None, "error": None}
    monkeypatch.setattr(api.app.state, "library_storage", service, raising=False)
    client = TestClient(api.app)
    assert client.get("/library/storage?refresh=true").json()["status"] == "scanning"
    service.get.assert_called_once_with(refresh=True)
    db = MagicMock()
    monkeypatch.setattr(api, "table_names", lambda _: ["films"])
    query = db.open_table.return_value.search.return_value
    query.select.return_value.limit.return_value.to_list.return_value = [{"path": str(tmp_path / "outside.mkv")}]
    assert api._library_source_paths(db) == [tmp_path / "outside.mkv"]
    query.select.assert_called_once_with(["path"])
    query.select.return_value.limit.assert_called_once_with(None)
