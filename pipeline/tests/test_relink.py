"""Integration coverage for hash-verified raw-film source relocation."""

from __future__ import annotations

from dataclasses import asdict, replace
import json
from pathlib import Path
import shutil
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from click.testing import CliRunner

from pipeline.cli import cli
from pipeline.config import Config
from pipeline.ingest.media import _media_cache_identity, _media_manifest_path
from pipeline.ingest.probe import FilmRecord, _content_hash
from pipeline.ingest.shots import Shot
import pipeline.index.relink as relink_module
from pipeline.index.relink import (
    FilmRelinkError,
    _make_relink_transaction,
    _recover_pending_relink,
    _write_relink_transaction,
    plan_film_relink,
    recover_film_relink,
    relink_film,
)
from pipeline.index.writer import (
    FrameWrite,
    UnitWrite,
    create_tables,
    open_db,
    publish_film_index,
    update_film_source,
    write_film,
)


def _annotation() -> dict:
    return {
        "caption": "A carefully indexed test shot.",
        "searchable_text": "A carefully indexed test shot.",
        "mood": ["calm"],
    }


def _vector() -> np.ndarray:
    vector = np.ones(1024, dtype=np.float32)
    return vector / np.linalg.norm(vector)


class _SimulatedCrash(BaseException):
    """Bypass ordinary exception recovery to model abrupt process death."""


def _make_published_film(
    config: Config,
    tmp_path: Path,
    *,
    source_bytes: bytes = b"an exact synthetic movie source",
    destination_bytes: bytes | None = None,
) -> tuple[object, FilmRecord, Path, Shot, Path, Path]:
    old_path = tmp_path / "old" / "release-name.mkv"
    old_path.parent.mkdir()
    old_path.write_bytes(source_bytes)

    new_path = tmp_path / "new" / "Canonical Film (2001).mkv"
    new_path.parent.mkdir()
    if destination_bytes is None:
        shutil.copy2(old_path, new_path)
    else:
        new_path.write_bytes(destination_bytes)

    film_id = _content_hash(old_path)
    asset_dir = config.paths.assets_dir / film_id
    asset_dir.mkdir(parents=True)
    film = FilmRecord(
        film_id=film_id,
        path=old_path.resolve(),
        asset_dir=asset_dir,
        duration=120.0,
        fps=24.0,
        has_embedded_subs=False,
        title="Original Indexed Title",
    )
    shot = Shot(
        shot_id=f"{film_id}_0000",
        t_start=0.0,
        t_end=3.0,
        parent_shot_id=None,
        keyframe_times=[1.5],
    )

    db = open_db(config)
    create_tables(db)
    keyframe_path = asset_dir / "keyframes" / f"{shot.shot_id}_0.webp"
    keyframe_path.parent.mkdir()
    keyframe_path.write_bytes(b"nonempty synthetic keyframe")
    publish_film_index(
        db,
        film,
        [
            UnitWrite(
                shot=shot,
                annotation=_annotation(),
                img_vec=_vector(),
                txt_vec=_vector(),
            )
        ],
        [
            FrameWrite(
                unit_id=shot.shot_id,
                shot_id=shot.shot_id,
                frame_index=0,
                timestamp=1.5,
                path=keyframe_path,
                visual_encoder="test-encoder",
                visual_vec=_vector(),
                is_representative=True,
            )
        ],
    )

    source_stat = old_path.stat()
    shots_path = asset_dir / "shots.json"
    shots_path.write_text(
        json.dumps(
            {
                "recipe": {
                    "version": 7,
                    "film_id": film_id,
                    "source_path": str(old_path.resolve()),
                    "source_size": source_stat.st_size,
                    "source_mtime_ns": source_stat.st_mtime_ns,
                    "unrelated_recipe_field": "preserve me",
                },
                "shots": [asdict(shot)],
                "unrelated_top_level_field": [1, 2, 3],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    manifest_path = _media_manifest_path(
        asset_dir / "media-manifests",
        shot.shot_id,
    )
    manifest_path.parent.mkdir()
    manifest_identity = _media_cache_identity(
        {
            "film_id": film_id,
            "path": str(old_path.resolve()),
            "size_bytes": source_stat.st_size,
            "mtime_ns": source_stat.st_mtime_ns,
            "unrelated_source_field": "preserve source metadata",
        },
        shot,
    )
    manifest_identity["recipe"] = {"quality": 40}
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "identity": manifest_identity,
                "artifacts": {"preview": {"sha256": "a" * 64}},
                "unknown": "preserve me too",
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return db, film, new_path, shot, shots_path, manifest_path


def _film_row(db: object, film_id: str) -> dict:
    rows = db.open_table("films").search().limit(None).to_list()
    return next(row for row in rows if row["film_id"] == film_id)


def test_relink_dry_run_validates_without_writes(
    config: Config,
    tmp_path: Path,
) -> None:
    db, film, new_path, _shot, shots_path, manifest_path = _make_published_film(
        config,
        tmp_path,
    )
    films_version = db.open_table("films").version
    shots_before = shots_path.read_bytes()
    manifest_before = manifest_path.read_bytes()

    plan = relink_film(
        db,
        config,
        new_path,
        title_from_filename=True,
        apply=False,
    )

    assert plan.film_id == film.film_id
    assert plan.new_title == "Canonical Film (2001)"
    assert plan.shot_cache_changes == 1
    assert plan.media_manifest_changes == 1
    assert _film_row(db, film.film_id)["path"] == str(film.path)
    assert db.open_table("films").version == films_version
    assert shots_path.read_bytes() == shots_before
    assert manifest_path.read_bytes() == manifest_before


def test_relink_rejects_destination_identity_change_after_lock_selection(
    config: Config,
    tmp_path: Path,
) -> None:
    db, _film, new_path, _shot, _shots_path, _manifest_path = (
        _make_published_film(config, tmp_path)
    )
    original_plan = plan_film_relink(db, config, new_path)
    changed_plan = replace(original_plan, film_id="f" * 64)

    with (
        patch(
            "pipeline.index.relink.plan_film_relink",
            return_value=changed_plan,
        ),
        patch("pipeline.index.relink._apply_film_relink") as apply_relink,
        pytest.raises(FilmRelinkError, match="identity changed"),
    ):
        relink_film(db, config, new_path, apply=True)

    apply_relink.assert_not_called()


def test_relink_updates_only_source_metadata_and_is_idempotent(
    config: Config,
    tmp_path: Path,
) -> None:
    db, film, new_path, _shot, shots_path, manifest_path = _make_published_film(
        config,
        tmp_path,
    )
    units_version = db.open_table("units").version
    frames_version = db.open_table("frames").version
    units_before = db.open_table("units").search().limit(None).to_list()
    frames_before = db.open_table("frames").search().limit(None).to_list()

    plan = relink_film(
        db,
        config,
        new_path,
        expected_film_id=film.film_id,
        title_from_filename=True,
        apply=True,
    )

    row = _film_row(db, film.film_id)
    assert row == {
        "film_id": film.film_id,
        "title": "Canonical Film (2001)",
        "path": str(new_path.resolve()),
        "duration": film.duration,
        "fps": film.fps,
    }
    assert db.open_table("units").version == units_version
    assert db.open_table("frames").version == frames_version
    assert db.open_table("units").search().limit(None).to_list() == units_before
    assert db.open_table("frames").search().limit(None).to_list() == frames_before

    shots = json.loads(shots_path.read_text(encoding="utf-8"))
    recipe = shots["recipe"]
    assert recipe["source_path"] == str(new_path.resolve())
    assert recipe["source_size"] == new_path.stat().st_size
    assert recipe["source_mtime_ns"] == new_path.stat().st_mtime_ns
    assert recipe["unrelated_recipe_field"] == "preserve me"
    assert shots["unrelated_top_level_field"] == [1, 2, 3]

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["identity"]["source"]["path"] == str(new_path.resolve())
    assert (
        manifest["identity"]["source"]["unrelated_source_field"]
        == "preserve source metadata"
    )
    assert manifest["identity"]["recipe"] == {"quality": 40}
    assert manifest["artifacts"]["preview"]["sha256"] == "a" * 64
    assert manifest["unknown"] == "preserve me too"

    films_version = db.open_table("films").version
    second = relink_film(
        db,
        config,
        new_path,
        title_from_filename=True,
        apply=True,
    )
    assert plan.is_noop is False
    assert second.is_noop is True
    assert db.open_table("films").version == films_version


def test_relink_rejects_matching_partial_hash_but_different_middle(
    config: Config,
    tmp_path: Path,
) -> None:
    chunk = 4 * 1024 * 1024
    old_bytes = b"H" * chunk + b"A" * 1024 + b"T" * chunk
    new_bytes = b"H" * chunk + b"B" * 1024 + b"T" * chunk
    db, film, new_path, _shot, shots_path, manifest_path = _make_published_film(
        config,
        tmp_path,
        source_bytes=old_bytes,
        destination_bytes=new_bytes,
    )
    assert _content_hash(film.path) == _content_hash(new_path)
    shots_before = shots_path.read_bytes()
    manifest_before = manifest_path.read_bytes()

    with pytest.raises(FilmRelinkError, match="full SHA-256 mismatch"):
        plan_film_relink(db, config, new_path)

    assert _film_row(db, film.film_id)["path"] == str(film.path)
    assert shots_path.read_bytes() == shots_before
    assert manifest_path.read_bytes() == manifest_before


def test_relink_rolls_back_caches_when_database_update_fails(
    config: Config,
    tmp_path: Path,
) -> None:
    db, film, new_path, _shot, shots_path, manifest_path = _make_published_film(
        config,
        tmp_path,
    )
    shots_before = shots_path.read_bytes()
    manifest_before = manifest_path.read_bytes()

    with (
        patch(
            "pipeline.index.relink.update_film_source",
            side_effect=RuntimeError("injected DB failure"),
        ),
        pytest.raises(RuntimeError, match="injected DB failure"),
    ):
        relink_film(db, config, new_path, apply=True)

    assert _film_row(db, film.film_id)["path"] == str(film.path)
    assert shots_path.read_bytes() == shots_before
    assert manifest_path.read_bytes() == manifest_before
    assert not (film.asset_dir / ".scene-recall-relink.json").exists()


def test_relink_rechecks_destination_immediately_before_database_commit(
    config: Config,
    tmp_path: Path,
) -> None:
    db, film, new_path, _shot, shots_path, manifest_path = _make_published_film(
        config,
        tmp_path,
    )
    shots_before = shots_path.read_bytes()
    manifest_before = manifest_path.read_bytes()
    cache_paths = {shots_path, manifest_path}
    real_atomic_write = relink_module._atomic_write
    cache_writes = 0

    def change_source_after_caches(path: Path, payload: bytes) -> None:
        nonlocal cache_writes
        real_atomic_write(path, payload)
        if path in cache_paths:
            cache_writes += 1
        if cache_writes == len(cache_paths):
            new_path.write_bytes(b"destination changed before DB commit")
            cache_writes += 1

    with (
        patch(
            "pipeline.index.relink._atomic_write",
            side_effect=change_source_after_caches,
        ),
        pytest.raises(FilmRelinkError, match="source changed after validation"),
    ):
        relink_film(db, config, new_path, apply=True)

    assert _film_row(db, film.film_id)["path"] == str(film.path)
    assert shots_path.read_bytes() == shots_before
    assert manifest_path.read_bytes() == manifest_before
    assert not (film.asset_dir / ".scene-recall-relink.json").exists()


def test_relink_recovers_forward_when_committed_database_update_raises(
    config: Config,
    tmp_path: Path,
) -> None:
    db, film, new_path, _shot, shots_path, manifest_path = _make_published_film(
        config,
        tmp_path,
    )
    expected = plan_film_relink(
        db,
        config,
        new_path,
        title_from_filename=True,
    )
    expected_cache = {change.path: change.after for change in expected.cache_changes}
    real_update = relink_module.update_film_source

    def commit_then_raise(*args, **kwargs):
        real_update(*args, **kwargs)
        raise RuntimeError("injected post-commit read-back failure")

    with patch(
        "pipeline.index.relink.update_film_source",
        side_effect=commit_then_raise,
    ):
        relink_film(
            db,
            config,
            new_path,
            title_from_filename=True,
            apply=True,
        )

    row = _film_row(db, film.film_id)
    assert row["path"] == str(new_path.resolve())
    assert row["title"] == "Canonical Film (2001)"
    assert shots_path.read_bytes() == expected_cache[shots_path]
    assert manifest_path.read_bytes() == expected_cache[manifest_path]
    assert not (film.asset_dir / ".scene-recall-relink.json").exists()


def test_relink_journal_recovers_old_state_after_mid_cache_crash(
    config: Config,
    tmp_path: Path,
) -> None:
    db, film, new_path, _shot, shots_path, manifest_path = _make_published_film(
        config,
        tmp_path,
    )
    shots_before = shots_path.read_bytes()
    manifest_before = manifest_path.read_bytes()
    cache_paths = {shots_path, manifest_path}
    real_atomic_write = relink_module._atomic_write
    crashed = False

    def crash_after_first_cache(path: Path, payload: bytes) -> None:
        nonlocal crashed
        real_atomic_write(path, payload)
        if path in cache_paths and not crashed:
            crashed = True
            raise _SimulatedCrash("injected abrupt stop")

    with (
        patch(
            "pipeline.index.relink._atomic_write",
            side_effect=crash_after_first_cache,
        ),
        pytest.raises(_SimulatedCrash, match="abrupt stop"),
    ):
        relink_film(db, config, new_path, apply=True)

    journal = film.asset_dir / ".scene-recall-relink.json"
    assert journal.is_file()
    assert _film_row(db, film.film_id)["path"] == str(film.path)

    assert _recover_pending_relink(db, film.asset_dir, film.film_id) == "old"
    assert shots_path.read_bytes() == shots_before
    assert manifest_path.read_bytes() == manifest_before
    assert not journal.exists()


def test_relink_journal_recovers_new_state_after_post_commit_crash(
    config: Config,
    tmp_path: Path,
) -> None:
    db, film, new_path, _shot, shots_path, manifest_path = _make_published_film(
        config,
        tmp_path,
    )
    manifest_before = manifest_path.read_bytes()
    expected = plan_film_relink(
        db,
        config,
        new_path,
        title_from_filename=True,
    )
    expected_cache = {change.path: change.after for change in expected.cache_changes}

    with (
        patch(
            "pipeline.index.relink._durable_unlink",
            side_effect=_SimulatedCrash("injected crash after database commit"),
        ),
        pytest.raises(_SimulatedCrash, match="after database commit"),
    ):
        relink_film(
            db,
            config,
            new_path,
            title_from_filename=True,
            apply=True,
        )

    journal = film.asset_dir / ".scene-recall-relink.json"
    assert journal.is_file()
    assert _film_row(db, film.film_id)["path"] == str(new_path.resolve())
    # Model a mixed cache set and prove the committed DB row rolls it forward.
    manifest_path.write_bytes(manifest_before)

    assert _recover_pending_relink(db, film.asset_dir, film.film_id) == "new"
    assert shots_path.read_bytes() == expected_cache[shots_path]
    assert manifest_path.read_bytes() == expected_cache[manifest_path]
    assert not journal.exists()


def test_relink_recovery_by_film_id_works_without_destination(
    config: Config,
    tmp_path: Path,
) -> None:
    db, film, new_path, _shot, shots_path, manifest_path = _make_published_film(
        config,
        tmp_path,
    )
    plan = plan_film_relink(db, config, new_path)
    transaction = _make_relink_transaction(plan)
    journal = film.asset_dir / ".scene-recall-relink.json"
    _write_relink_transaction(journal, transaction)
    changes = {change.path: change for change in plan.cache_changes}
    shots_path.write_bytes(changes[shots_path].after)
    manifest_before = manifest_path.read_bytes()
    new_path.unlink()

    assert recover_film_relink(db, config, film.film_id) == "old"

    assert shots_path.read_bytes() == changes[shots_path].before
    assert manifest_path.read_bytes() == manifest_before
    assert _film_row(db, film.film_id)["path"] == str(film.path)
    assert not journal.exists()


@pytest.mark.parametrize("committed", [False, True])
def test_relink_recovery_retains_journal_when_selected_source_changed(
    config: Config,
    tmp_path: Path,
    committed: bool,
) -> None:
    db, film, new_path, _shot, shots_path, _manifest_path = _make_published_film(
        config,
        tmp_path,
    )
    plan = plan_film_relink(
        db,
        config,
        new_path,
        title_from_filename=True,
    )
    journal = film.asset_dir / ".scene-recall-relink.json"
    _write_relink_transaction(journal, _make_relink_transaction(plan))
    changes = {change.path: change for change in plan.cache_changes}
    shots_path.write_bytes(changes[shots_path].after)
    if committed:
        update_film_source(
            db,
            film.film_id,
            expected_old_path=str(film.path),
            new_path=str(new_path.resolve()),
            title=plan.new_title,
        )
        selected_source = new_path
    else:
        selected_source = film.path
    selected_source.write_bytes(b"changed after journal validation")
    shots_current = shots_path.read_bytes()

    with pytest.raises(FilmRelinkError, match="no longer matches"):
        _recover_pending_relink(db, film.asset_dir, film.film_id)

    assert shots_path.read_bytes() == shots_current
    assert journal.is_file()


def test_relink_recovery_retains_journal_when_database_matches_neither_state(
    config: Config,
    tmp_path: Path,
) -> None:
    db, film, new_path, _shot, shots_path, _manifest_path = _make_published_film(
        config,
        tmp_path,
    )
    plan = plan_film_relink(db, config, new_path)
    journal = film.asset_dir / ".scene-recall-relink.json"
    _write_relink_transaction(journal, _make_relink_transaction(plan))
    shots_before = shots_path.read_bytes()
    update_film_source(
        db,
        film.film_id,
        expected_old_path=str(film.path),
        new_path=str(film.path),
        title="Unexpected concurrent title",
    )

    with pytest.raises(FilmRelinkError, match="neither unique journal endpoint"):
        _recover_pending_relink(db, film.asset_dir, film.film_id)

    assert shots_path.read_bytes() == shots_before
    assert journal.is_file()


def test_relink_recovery_refuses_unexpected_cache_content_without_writes(
    config: Config,
    tmp_path: Path,
) -> None:
    db, film, new_path, _shot, shots_path, manifest_path = _make_published_film(
        config,
        tmp_path,
    )
    plan = plan_film_relink(db, config, new_path)
    journal = film.asset_dir / ".scene-recall-relink.json"
    _write_relink_transaction(journal, _make_relink_transaction(plan))
    expected_cache = {change.path: change.after for change in plan.cache_changes}
    shots_path.write_bytes(expected_cache[shots_path])
    manifest_path.write_bytes(b"unrelated cache edit")
    shots_current = shots_path.read_bytes()
    manifest_current = manifest_path.read_bytes()

    with pytest.raises(FilmRelinkError, match="either journal state"):
        _recover_pending_relink(db, film.asset_dir, film.film_id)

    assert shots_path.read_bytes() == shots_current
    assert manifest_path.read_bytes() == manifest_current
    assert journal.is_file()


def test_relink_recovery_rejects_path_escaping_journal(
    config: Config,
    tmp_path: Path,
) -> None:
    db, film, new_path, _shot, _shots_path, _manifest_path = (
        _make_published_film(config, tmp_path)
    )
    plan = plan_film_relink(db, config, new_path)
    transaction = _make_relink_transaction(plan)
    transaction["cache_changes"][0]["path"] = "../escape.json"
    journal = film.asset_dir / ".scene-recall-relink.json"
    _write_relink_transaction(journal, transaction)

    with pytest.raises(FilmRelinkError, match="escapes the asset directory"):
        _recover_pending_relink(db, film.asset_dir, film.film_id)

    assert not (film.asset_dir.parent / "escape.json").exists()
    assert journal.is_file()


def test_relink_preserves_existing_title_exactly_by_default(
    config: Config,
    tmp_path: Path,
) -> None:
    db, film, new_path, _shot, _shots_path, _manifest_path = (
        _make_published_film(config, tmp_path)
    )
    preserved = "  Original Indexed Title  "
    update_film_source(
        db,
        film.film_id,
        expected_old_path=str(film.path),
        new_path=str(film.path),
        title=preserved,
    )

    relink_film(db, config, new_path, apply=True)

    assert _film_row(db, film.film_id)["title"] == preserved


def test_relink_dry_run_rejects_destination_owned_by_another_film(
    config: Config,
    tmp_path: Path,
) -> None:
    db, film, new_path, _shot, _shots_path, _manifest_path = (
        _make_published_film(config, tmp_path)
    )
    write_film(
        db,
        FilmRecord(
            film_id="another-film",
            path=new_path.resolve(),
            asset_dir=config.paths.assets_dir / "another-film",
            duration=1.0,
            fps=24.0,
            has_embedded_subs=False,
            title="Another Film",
        ),
    )

    with pytest.raises(FilmRelinkError, match="already uses the destination"):
        plan_film_relink(db, config, new_path, expected_film_id=film.film_id)


def test_ingest_from_retained_old_path_cannot_undo_relink(
    config: Config,
    tmp_path: Path,
) -> None:
    db, film, new_path, _shot, shots_path, manifest_path = _make_published_film(
        config,
        tmp_path,
    )
    relink_film(db, config, new_path, apply=True)
    row_after = _film_row(db, film.film_id)
    shots_after = shots_path.read_bytes()
    manifest_after = manifest_path.read_bytes()
    stage_mocks = {
        "extract_dialogue": MagicMock(),
        "detect_shots": MagicMock(),
        "extract_media": MagicMock(),
        "publish_film_index": MagicMock(),
    }

    with (
        patch("pipeline.ingest.pipeline.probe_film", return_value=film),
        patch.multiple("pipeline.ingest.pipeline", **stage_mocks),
        pytest.raises(RuntimeError, match="refusing ingest"),
    ):
        from pipeline.ingest.pipeline import run_pipeline

        run_pipeline(film.path, config)

    assert all(mock.call_count == 0 for mock in stage_mocks.values())
    assert _film_row(db, film.film_id) == row_after
    assert shots_path.read_bytes() == shots_after
    assert manifest_path.read_bytes() == manifest_after


def test_stale_direct_publication_and_film_write_cannot_undo_relink(
    config: Config,
    tmp_path: Path,
) -> None:
    db, film, new_path, shot, _shots_path, _manifest_path = _make_published_film(
        config,
        tmp_path,
    )
    relink_film(db, config, new_path, apply=True)
    keyframe_path = film.asset_dir / "keyframes" / f"{shot.shot_id}_0.webp"
    units_version = db.open_table("units").version
    frames_version = db.open_table("frames").version
    films_version = db.open_table("films").version
    row_after = _film_row(db, film.film_id)

    with pytest.raises(RuntimeError, match="refusing ingest"):
        publish_film_index(
            db,
            film,
            [
                UnitWrite(
                    shot=shot,
                    annotation=_annotation(),
                    img_vec=_vector(),
                    txt_vec=_vector(),
                )
            ],
            [
                FrameWrite(
                    unit_id=shot.shot_id,
                    shot_id=shot.shot_id,
                    frame_index=0,
                    timestamp=1.5,
                    path=keyframe_path,
                    visual_encoder="test-encoder",
                    visual_vec=_vector(),
                    is_representative=True,
                )
            ],
        )
    with pytest.raises(RuntimeError, match="refusing ingest"):
        write_film(db, film)

    assert db.open_table("units").version == units_version
    assert db.open_table("frames").version == frames_version
    assert db.open_table("films").version == films_version
    assert _film_row(db, film.film_id) == row_after


def test_unpublished_metadata_row_can_retry_from_a_new_path(
    config: Config,
    tmp_path: Path,
) -> None:
    db = open_db(config)
    create_tables(db)
    first_path = tmp_path / "failed-first-path.mkv"
    second_path = tmp_path / "retry-path.mkv"
    first_path.write_bytes(b"same source")
    second_path.write_bytes(b"same source")
    film_id = "a" * 64
    first = FilmRecord(
        film_id=film_id,
        path=first_path.resolve(),
        asset_dir=config.paths.assets_dir / film_id,
        duration=1.0,
        fps=24.0,
        has_embedded_subs=False,
        title="First Attempt",
    )
    retry = replace(
        first,
        path=second_path.resolve(),
        title="Retry Attempt",
    )
    write_film(db, first)

    write_film(db, retry)

    assert _film_row(db, film_id)["path"] == str(second_path.resolve())


def test_ingest_refuses_cache_with_pending_relink_journal(
    config: Config,
    tmp_path: Path,
) -> None:
    _db, film, _new_path, _shot, _shots_path, _manifest_path = (
        _make_published_film(config, tmp_path)
    )
    journal = film.asset_dir / ".scene-recall-relink.json"
    journal.write_text("{}", encoding="utf-8")
    stage_mocks = {
        "extract_dialogue": MagicMock(),
        "detect_shots": MagicMock(),
        "extract_media": MagicMock(),
        "publish_film_index": MagicMock(),
    }

    with (
        patch("pipeline.ingest.pipeline.probe_film", return_value=film),
        patch.multiple("pipeline.ingest.pipeline", **stage_mocks),
        pytest.raises(RuntimeError, match="interrupted source relink"),
    ):
        from pipeline.ingest.pipeline import run_pipeline

        run_pipeline(film.path, config)

    assert all(mock.call_count == 0 for mock in stage_mocks.values())
    assert journal.is_file()


def test_relink_cli_is_dry_run_by_default(
    config: Config,
    tmp_path: Path,
) -> None:
    db, film, new_path, _shot, _shots_path, _manifest_path = (
        _make_published_film(config, tmp_path)
    )
    films_version = db.open_table("films").version

    with patch("pipeline.cli.load_config", return_value=config):
        command = CliRunner().invoke(
            cli,
            ["relink-film", str(new_path), "--title-from-filename"],
        )

    assert command.exit_code == 0, command.output
    assert "Dry run passed" in command.output
    assert "Full SHA-256" in command.output
    assert _film_row(db, film.film_id)["path"] == str(film.path)
    assert db.open_table("films").version == films_version


def test_relink_cli_rejects_conflicting_title_options(tmp_path: Path) -> None:
    movie = tmp_path / "movie.mkv"
    movie.write_bytes(b"movie")

    command = CliRunner().invoke(
        cli,
        [
            "relink-film",
            str(movie),
            "--title",
            "Explicit",
            "--title-from-filename",
        ],
    )

    assert command.exit_code == 2
    assert "mutually exclusive" in command.output
