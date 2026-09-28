"""Real LanceDB coverage for scoped, profile-preserving film removal."""

from dataclasses import replace
import json
from pathlib import Path

import numpy as np
import pytest

from pipeline.index import remove_film as removal
from pipeline.index.framing_features import (
    FramingSpatialSource, create_framing_feature_table, make_framing_feature_rows,
    publish_framing_manifest, resolve_ready_framing_profile,
)
from pipeline.index.text_features import (
    build_text_feature_sources, configured_text_profile, create_text_feature_table,
    make_text_feature_rows, publish_text_index_manifest, resolve_ready_text_profile,
)
from pipeline.index.writer import (
    FrameWrite, UnitWrite, create_tables, open_db, publish_film_index, table_names,
)
from pipeline.ingest.locks import global_ingest_lock
from pipeline.ingest.probe import FilmRecord
from pipeline.ingest.shots import Shot
from pipeline.tests.test_framing_features import _profile


TARGET = "a" * 64
OTHER = "b" * 64


@pytest.fixture
def library(config, tmp_path, monkeypatch):
    db = open_db(config)
    create_tables(db, vector_dim=4)
    vector = np.ones(4, dtype=np.float32) / 2
    for film_id in (TARGET, OTHER):
        asset = config.paths.assets_dir / film_id
        asset.mkdir()
        frame_path = asset / "keyframes" / f"{film_id[0]}_0000_0.webp"
        frame_path.parent.mkdir()
        frame_path.write_bytes(b"frame evidence")
        film = FilmRecord(
            film_id=film_id, path=tmp_path / f"{film_id}.mkv", asset_dir=asset,
            duration=10, fps=24, has_embedded_subs=False, title=film_id,
        )
        shot = Shot(shot_id=f"{film_id[0]}_0000", t_start=0, t_end=4,
                    parent_shot_id=None, keyframe_times=[2])
        publish_film_index(db, film, [UnitWrite(
            shot=shot, annotation={"caption": "red coat", "searchable_text": "red coat", "mood": []},
            img_vec=vector, txt_vec=vector,
        )], [FrameWrite(
            unit_id=shot.shot_id, shot_id=shot.shot_id, frame_index=0,
            timestamp=2, path=frame_path, visual_encoder="pe_core_l14",
            visual_vec=vector, is_representative=True,
        )])

    text = configured_text_profile(config)
    sources = [source for row in db.open_table("units").search().limit(None).to_list()
               for source in build_text_feature_sources(row)]
    text_rows = make_text_feature_rows(sources, np.zeros((len(sources), text.dimension), dtype=np.float32), text)
    create_text_feature_table(db, text)
    db.open_table(text.table_name).add(text_rows)
    publish_text_index_manifest(config, db, text)
    # Older profiles must lose this film's derivations as well.
    archived_text = replace(text, table_name="unit_text_archived")
    create_text_feature_table(db, archived_text)
    db.open_table(archived_text.table_name).add(text_rows)

    framing = _profile()
    monkeypatch.setattr("pipeline.index.framing_features.configured_framing_spatial_profile", lambda config: framing)
    frames = db.open_table("frames").search().limit(None).to_list()
    frame_sources = [FramingSpatialSource(
        frame_id=row["frame_id"], film_id=row["film_id"], unit_id=row["unit_id"],
        path=Path(row["path"]), source_size=row["source_size"], source_mtime_ns=row["source_mtime_ns"],
    ) for row in frames]
    create_framing_feature_table(db, framing)
    db.open_table(framing.table_name).add(make_framing_feature_rows(
        frame_sources, np.ones((len(frames), 6, 6, 4), dtype=np.float32) / 2, framing,
    ))
    publish_framing_manifest(config, db, framing, frame_ids=[row["frame_id"] for row in frames])
    # Authored state is not a removal target, even if it contains a film ID.
    db.create_table("bookmarks", [{"film_id": TARGET, "note": "keep my saved selection"}])
    return db, text, framing, tmp_path / f"{TARGET}.mkv"


def _versions(db):
    return {name: db.open_table(name).version for name in table_names(db)}


def test_dry_run_is_exact_and_does_not_change_tables(config, library):
    db, text, framing, path = library
    before = _versions(db)
    report = removal.remove_film_index(config, TARGET, path)
    assert report["status"] == "dry_run"
    assert report["film"]["path"] == str(path)
    assert {item["table"] for item in report["tables"]} == set(before) - {"bookmarks"}
    assert all(item["target_rows"] == 1 for item in report["tables"])
    assert _versions(db) == before
    assert resolve_ready_text_profile(config, db) == text
    assert resolve_ready_framing_profile(config, db) == framing


def test_removal_preserves_other_rows_files_authored_state_and_both_profiles(config, library, tmp_path):
    db, text, framing, path = library
    path.write_bytes(b"replacement source already at the old filename")
    before = {name: db.open_table(name).search().where(f"film_id = '{OTHER}'").limit(None).to_list()
              for name in table_names(db)}
    receipt = tmp_path / "removal.json"
    report = removal.remove_film_index(config, TARGET, path, apply=True, receipt=receipt)
    assert report["status"] == "complete"
    assert json.loads(receipt.read_text())["ready_profiles_after"] == {
        "text": text.table_name, "framing": framing.table_name,
    }
    for name in table_names(db) - {"bookmarks"}:
        assert db.open_table(name).count_rows(f"film_id = '{TARGET}'") == 0
        assert db.open_table(name).search().limit(None).to_list() == before[name]
    assert db.open_table("bookmarks").count_rows() == 1
    assert path.read_bytes() == b"replacement source already at the old filename"
    assert (config.paths.assets_dir / TARGET / "keyframes" / "a_0000_0.webp").read_bytes() == b"frame evidence"
    assert resolve_ready_text_profile(config, db) == text
    assert resolve_ready_framing_profile(config, db) == framing
    assert db.open_table("units").search("red", query_type="fts").to_list()[0]["film_id"] == OTHER


def test_incomplete_framing_stays_inactive(config, library, tmp_path):
    db, text, framing, path = library
    db.open_table(framing.table_name).delete(f"film_id = '{OTHER}'")
    assert resolve_ready_framing_profile(config, db) is None
    report = removal.remove_film_index(config, TARGET, path, apply=True, receipt=tmp_path / "removal.json")
    assert report["ready_profiles_after"] == {"text": text.table_name, "framing": None}


def test_wrong_path_and_active_ingest_refuse_before_mutation(config, library, tmp_path):
    db, _, _, path = library
    before = _versions(db)
    with pytest.raises(ValueError, match="does not match"):
        removal.remove_film_index(config, TARGET, tmp_path / "another.mkv", apply=True, receipt=tmp_path / "wrong.json")
    with pytest.raises(ValueError, match="requires a new"):
        removal.remove_film_index(config, TARGET, path, apply=True)
    receipt = tmp_path / "existing.json"
    receipt.write_text("do not overwrite")
    with pytest.raises(FileExistsError):
        removal.remove_film_index(config, TARGET, path, apply=True, receipt=receipt)
    assert receipt.read_text() == "do not overwrite"
    with global_ingest_lock(config.paths.assets_dir):
        with pytest.raises(RuntimeError, match="another ingest or backfill"):
            removal.remove_film_index(config, TARGET, path, apply=True, receipt=tmp_path / "busy.json")
    assert _versions(db) == before
    assert not (tmp_path / "wrong.json").exists()
    assert not (tmp_path / "busy.json").exists()


@pytest.mark.parametrize("failure_type", [OSError, KeyboardInterrupt])
def test_manifest_failure_restores_all_rows_and_profile_readiness(config, library, tmp_path, monkeypatch, failure_type):
    db, text, framing, path = library
    before = {name: db.open_table(name).search().limit(None).to_list() for name in table_names(db)}
    publish = removal.publish_text_index_manifest
    calls = 0

    def fail_once(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise failure_type("simulated manifest write failure")
        return publish(*args, **kwargs)

    monkeypatch.setattr(removal, "publish_text_index_manifest", fail_once)
    receipt = tmp_path / "rollback.json"
    with pytest.raises(failure_type, match="simulated"):
        removal.remove_film_index(config, TARGET, path, apply=True, receipt=receipt)
    assert {name: db.open_table(name).search().limit(None).to_list() for name in table_names(db)} == before
    assert json.loads(receipt.read_text())["status"] == "rolled_back"
    assert resolve_ready_text_profile(config, db) == text
    assert resolve_ready_framing_profile(config, db) == framing


def test_final_film_removal_keeps_empty_text_profile_valid(config, library, tmp_path):
    db, text, _, path = library
    removal.remove_film_index(config, TARGET, path, apply=True, receipt=tmp_path / "first.json")
    report = removal.remove_film_index(
        config, OTHER, tmp_path / f"{OTHER}.mkv", apply=True, receipt=tmp_path / "last.json",
    )
    assert report["ready_profiles_after"] == {"text": text.table_name, "framing": None}
    for name in table_names(db) - {"bookmarks"}:
        assert db.open_table(name).count_rows() == 0
