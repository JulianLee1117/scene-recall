"""Relocation verifies bytes before deleting only the legacy playback copy."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from filelock import FileLock

from pipeline.ingest import playback
from pipeline.ingest import relocate_playback as relocation
from pipeline.ingest.locks import film_operation_lock


@pytest.fixture
def legacy_playback(config, tmp_path):
    config.paths.films_dir.mkdir()
    source = config.paths.films_dir / "Film.mkv"
    source.write_bytes(b"original evidence, never altered")
    asset_dir = config.paths.assets_dir / "film_test"
    directory = playback.playback_directory(asset_dir)
    directory.mkdir(parents=True)
    old = directory / ("video-" + "a" * 32 + ".mp4")
    old.write_bytes(b"0123456789-validated-prepared-video")
    manifest = {"version": 1, "profile": playback.PROFILE, "filename": old.name,
                "source": playback._fingerprint(source),
                "output": playback._fingerprint(old, include_path=False),
                "validation": {"samples": [0, 3, 4]}}
    (directory / "manifest.json").write_text(json.dumps(manifest))
    config.paths.playback_dir = tmp_path / "playback-drive"
    return config, source, asset_dir, old


def test_dry_run_is_read_only_and_lists_unreferenced_files(legacy_playback):
    config, source, asset_dir, old = legacy_playback
    other = old.with_name("video-" + "b" * 32 + ".mp4")
    other.write_bytes(b"unreferenced previous generation")
    before = playback._fingerprint(source)
    report = relocation.relocate_playback(config)
    assert report["errors"] == report["busy"] == report["bytes_removed"] == 0
    assert report["bytes_planned"] == old.stat().st_size
    assert report["items"][0]["status"] == "would_move"
    assert report["items"][0]["unreferenced_files"] == [str(other)]
    assert old.exists() and other.exists()
    assert not config.paths.playback_dir.exists()
    assert not (asset_dir / ".scene-recall-film.lock").exists()
    assert playback._fingerprint(source) == before


def test_apply_rebinds_identity_preserves_token_and_can_repeat(legacy_playback):
    config, source, asset_dir, old = legacy_playback
    original_identity = playback._fingerprint(source)
    original_bytes, old_bytes = source.read_bytes(), old.read_bytes()
    token = playback.playback_representation_token(old)
    report = relocation.relocate_playback(config, apply=True)
    item = report["items"][0]
    new = Path(item["destination"])
    assert item["status"] == "moved" and report["bytes_removed"] == len(old_bytes)
    assert item["sha256"] == hashlib.sha256(old_bytes).hexdigest()
    assert new.read_bytes() == old_bytes and not old.exists()
    assert source.read_bytes() == original_bytes and playback._fingerprint(source) == original_identity
    assert playback.lookup_playback(source, asset_dir, config.paths.playback_dir) == new
    assert playback.lookup_playback(source, asset_dir) is None
    assert playback.playback_representation_token(new) == token
    receipt = json.loads((new.parent / "manifest.json").read_text())
    assert receipt["output"] == playback._fingerprint(new, include_path=False)
    assert receipt["source"] == original_identity
    assert (old.parent / "manifest.json").exists()
    assert relocation.relocate_playback(config, apply=True)["items"][0]["status"] == "already_moved"


@pytest.mark.parametrize("lock_kind", ["film", "legacy", "destination"])
def test_busy_film_or_profile_is_skipped_without_removing_source(legacy_playback, lock_kind):
    config, _, asset_dir, old = legacy_playback
    destination = playback.playback_directory(asset_dir, config.paths.playback_dir)
    destination.mkdir(parents=True)
    lock = (film_operation_lock(asset_dir) if lock_kind == "film" else
            FileLock((old.parent if lock_kind == "legacy" else destination) / ".prepare.lock"))
    with lock:
        report = relocation.relocate_playback(config, apply=True)
    assert report["busy"] == 1 and report["errors"] == report["bytes_removed"] == 0
    assert old.exists()


@pytest.mark.parametrize("phase", ["copy", "publication"])
def test_failed_copy_or_publication_preserves_legacy_and_retry_recovers(legacy_playback, monkeypatch, phase):
    config, source, asset_dir, old = legacy_playback
    original_source = source.read_bytes()
    function = "_copy_verified" if phase == "copy" else "_publish_manifest"
    actual = getattr(relocation, function)

    def fail(*args, **kwargs):
        if phase == "copy":
            args[1].write_bytes(b"interrupted partial")
        raise OSError("simulated interruption")

    monkeypatch.setattr(relocation, function, fail)
    report = relocation.relocate_playback(config, apply=True)
    assert report["errors"] == 1 and report["bytes_removed"] == 0
    assert playback.lookup_playback(source, asset_dir) == old
    assert source.read_bytes() == original_source
    destination = playback.playback_directory(asset_dir, config.paths.playback_dir)
    assert not list(destination.glob(".relocate-*.mp4"))
    monkeypatch.setattr(relocation, function, actual)
    assert relocation.relocate_playback(config, apply=True)["items"][0]["status"] == "moved"


def test_failed_legacy_cleanup_can_resume_from_published_receipt(legacy_playback, monkeypatch):
    config, source, asset_dir, old = legacy_playback
    actual = Path.unlink

    def fail_legacy(path, *args, **kwargs):
        if path == old:
            raise PermissionError("video is open")
        return actual(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_legacy)
    report = relocation.relocate_playback(config, apply=True)
    assert report["errors"] == 1 and old.exists()
    assert playback.lookup_playback(source, asset_dir, config.paths.playback_dir) != old
    monkeypatch.setattr(Path, "unlink", actual)
    assert relocation.relocate_playback(config, apply=True)["items"][0]["status"] == "moved"


def test_existing_different_destination_bytes_are_never_overwritten(legacy_playback):
    config, _, asset_dir, old = legacy_playback
    destination = playback.playback_directory(asset_dir, config.paths.playback_dir)
    destination.mkdir(parents=True)
    conflict = destination / old.name
    conflict.write_bytes(b"different destination")
    report = relocation.relocate_playback(config, apply=True)
    assert report["errors"] == 1 and old.exists()
    assert conflict.read_bytes() == b"different destination"


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("phase", ["before_publication", "before_cleanup"])
def test_replacement_after_verification_never_rebinds_unverified_bytes(
    legacy_playback, monkeypatch, existing, phase,
):
    config, source, asset_dir, old = legacy_playback
    original_bytes = old.read_bytes()
    destination = playback.playback_directory(asset_dir, config.paths.playback_dir)
    destination.mkdir(parents=True)
    output = destination / old.name
    if existing:
        output.write_bytes(original_bytes)
    state = {"verified": False, "replaced": False, "temporary": None}
    actual_copy = relocation._copy_verified
    actual_hash = relocation._sha256
    actual_require = relocation._require_source

    def verified_copy(old_path, temporary, expected):
        result = actual_copy(old_path, temporary, expected)
        state.update(verified=True, temporary=temporary)
        return result

    def verified_hash(path):
        result = actual_hash(path)
        if path == output:
            state["verified"] = True
        return result

    def replace_after_verification(*args):
        actual_require(*args)
        receipt_exists = (destination / "manifest.json").exists()
        at_phase = phase == "before_publication" or receipt_exists
        if state["verified"] and not state["replaced"] and at_phase:
            target = output if existing or receipt_exists else state["temporary"]
            replacement = destination / "unverified-replacement.mp4"
            replacement.write_bytes(b"x" * len(original_bytes))
            os.replace(replacement, target)
            state["replaced"] = True

    monkeypatch.setattr(relocation, "_copy_verified", verified_copy)
    monkeypatch.setattr(relocation, "_sha256", verified_hash)
    monkeypatch.setattr(relocation, "_require_source", replace_after_verification)
    report = relocation.relocate_playback(config, apply=True)
    assert state["replaced"] and report["errors"] == 1 and report["bytes_removed"] == 0
    assert old.read_bytes() == original_bytes
    assert playback.lookup_playback(source, asset_dir, config.paths.playback_dir) == old


def test_replacement_during_rename_is_not_trusted(legacy_playback, monkeypatch):
    config, source, asset_dir, old = legacy_playback
    actual_move = relocation.move_file_no_replace

    def replace_after_move(temporary, output):
        actual_move(temporary, output)
        replacement = output.with_suffix(".unverified")
        replacement.write_bytes(b"different unverified bytes")
        os.replace(replacement, output)

    monkeypatch.setattr(relocation, "move_file_no_replace", replace_after_move)
    report = relocation.relocate_playback(config, apply=True)
    assert report["errors"] == 1 and old.exists()
    destination = playback.playback_directory(asset_dir, config.paths.playback_dir)
    assert not (destination / "manifest.json").exists()
    assert playback.lookup_playback(source, asset_dir, config.paths.playback_dir) == old


def test_destination_appearing_at_publication_is_not_overwritten(legacy_playback, monkeypatch):
    config, _, asset_dir, old = legacy_playback
    actual_move = relocation.move_file_no_replace

    def race_destination(temporary, output):
        output.write_bytes(b"concurrent destination")
        actual_move(temporary, output)

    monkeypatch.setattr(relocation, "move_file_no_replace", race_destination)
    report = relocation.relocate_playback(config, apply=True)
    output = playback.playback_directory(asset_dir, config.paths.playback_dir) / old.name
    assert report["errors"] == 1 and old.exists()
    assert output.read_bytes() == b"concurrent destination"
    assert not (output.parent / "manifest.json").exists()


def test_independent_copy_verification_rejects_corruption(legacy_playback, monkeypatch):
    config, _, _, old = legacy_playback
    monkeypatch.setattr(relocation, "_sha256", lambda _path: "0" * 64)
    report = relocation.relocate_playback(config, apply=True)
    assert report["errors"] == 1 and old.exists()
    assert "SHA-256" in report["items"][0]["reason"]


def test_source_change_during_copy_prevents_publication_and_deletion(legacy_playback, monkeypatch):
    config, source, asset_dir, old = legacy_playback
    actual = relocation._copy_verified

    def change_source(*args):
        result = actual(*args)
        source.write_bytes(b"unexpected outside edit")
        return result

    monkeypatch.setattr(relocation, "_copy_verified", change_source)
    report = relocation.relocate_playback(config, apply=True)
    assert report["errors"] == 1 and old.exists()
    destination = playback.playback_directory(asset_dir, config.paths.playback_dir)
    assert not (destination / "manifest.json").exists()


@pytest.mark.parametrize("kind", ["assets", "films", "parent", "root", "traversal"])
def test_unsafe_destinations_and_film_ids_are_rejected(legacy_playback, kind):
    config, _, _, old = legacy_playback
    destination = {"assets": config.paths.assets_dir / "nested", "films": config.paths.films_dir,
                   "parent": config.paths.assets_dir.parent, "root": Path(config.paths.assets_dir.anchor)}
    with pytest.raises(playback.PlaybackPreparationError):
        relocation.relocate_playback(config, destination.get(kind), apply=True,
                                     film_ids=["../outside"] if kind == "traversal" else None)
    assert old.exists()


@pytest.mark.parametrize("invalid", [None, [], {"size": "big"}])
def test_malformed_output_identity_reports_error_per_film(legacy_playback, invalid):
    config, _, _, old = legacy_playback
    receipt = old.parent / "manifest.json"
    manifest = json.loads(receipt.read_text())
    manifest["output"] = invalid
    receipt.write_text(json.dumps(manifest))
    report = relocation.relocate_playback(config, apply=True)
    assert report["errors"] == 1 and old.exists()


def test_pinned_http_ranges_survive_storage_relocation(legacy_playback):
    from pipeline.api import main

    config, source, _, old = legacy_playback
    old_bytes = old.read_bytes()
    db = MagicMock()
    db.open_table.return_value.search.return_value.where.return_value.to_list.return_value = [
        {"film_id": "film_test", "path": str(source)},
    ]
    with (patch.object(main, "load_config", return_value=config),
          patch.object(main, "open_db", return_value=db),
          patch.object(main, "ensure_search_indexes"), TestClient(main.app) as client):
        url = client.get("/video/film_test/playback").json()["url"]
        assert client.get(url, headers={"Range": "bytes=1-7"}).content == old_bytes[1:8]
        assert relocation.relocate_playback(config, apply=True)["items"][0]["status"] == "moved"
        assert client.get("/video/film_test/playback").json()["url"] == url
        result = client.get(url, headers={"Range": "bytes=8-15"})
        assert result.status_code == 206 and result.content == old_bytes[8:16]
        assert client.get("/video/film_test").content == source.read_bytes()


def test_api_retries_if_relocation_removes_path_between_lookup_and_open(legacy_playback):
    from pipeline.api import main

    config, source, _, old = legacy_playback
    token = playback.playback_representation_token(old)
    original_lookup = main._prepared_video
    request = MagicMock()
    request.app.state.config = config
    calls = 0

    def racing_lookup(*args):
        nonlocal calls
        path = original_lookup(*args)
        calls += 1
        if calls == 1:
            assert relocation.relocate_playback(config, apply=True)["items"][0]["status"] == "moved"
        return path

    with patch.object(main, "_prepared_video", side_effect=racing_lookup):
        new, handle, size = main._open_prepared_video(source, "film_test", request, token)
    try:
        assert new != old and handle.read() == new.read_bytes() and size == new.stat().st_size
        assert calls == 2
    finally:
        handle.close()
