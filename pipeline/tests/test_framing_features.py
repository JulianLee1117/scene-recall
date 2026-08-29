"""Versioning, integrity, and numeric tests for the Framing spatial cache."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import numpy as np

from pipeline.index.framing_features import (
    FramingSpatialProfile,
    FramingSpatialSource,
    create_framing_feature_table,
    decode_descriptor,
    encode_descriptor,
    load_framing_grids,
    manifest_path,
    make_framing_feature_rows,
    publish_framing_manifest,
    resolve_ready_framing_profile,
)
from pipeline.index.writer import create_tables, open_db


def _profile(*, feature_dim: int = 4) -> FramingSpatialProfile:
    return FramingSpatialProfile(
        profile_id="framing-spatial-test-v1",
        table_name="frame_framing_test_v1",
        encoder_name="pe_core_l14",
        model_id="timm/PE-Core-L-14-336",
        model_revision="a" * 40,
        open_clip_version="3.3.0",
        timm_version="1.0.27",
        torch_version="2.11.0",
        torchvision_version="0.26.0",
        pillow_version="12.1.1",
        row_schema_version=1,
        grid_size=6,
        feature_dim=feature_dim,
        extraction_contract_version=1,
        storage_dtype="float16-le",
    )


def _frame_row(path: Path, *, frame_id: str = "frame-a") -> dict:
    stat = path.stat()
    return {
        "schema_version": 1,
        "frame_id": frame_id,
        "film_id": "film-a",
        "unit_id": "unit-a",
        "shot_id": "unit-a",
        "frame_index": 0,
        "timestamp": 1.0,
        "timestamp_source": "test",
        "path": str(path),
        "is_representative": True,
        "quality_score": None,
        "source_size": stat.st_size,
        "source_mtime_ns": stat.st_mtime_ns,
        "visual_encoder": "pe_core_l14",
        "visual_vec": np.zeros(1024, dtype=np.float32).tolist(),
    }


def test_float16_descriptor_has_bounded_spatial_score_error() -> None:
    profile = _profile()
    rng = np.random.default_rng(17)
    query = rng.normal(size=(6, 6, 4)).astype(np.float32)
    query /= np.linalg.norm(query, axis=-1, keepdims=True)
    candidate = rng.normal(size=(6, 6, 4)).astype(np.float32)
    candidate /= np.linalg.norm(candidate, axis=-1, keepdims=True)

    cached = decode_descriptor(encode_descriptor(candidate, profile), profile)
    exact = float(np.mean(np.sum(query * candidate, axis=-1)))
    restored = float(np.mean(np.sum(query * cached, axis=-1)))

    assert cached.dtype == np.float32
    assert abs(exact - restored) < 1e-4


def test_manifest_requires_exact_frames_generation(
    config,
    tmp_path: Path,
) -> None:
    path = tmp_path / "frame.webp"
    path.write_bytes(b"frame")
    profile = _profile()
    db = open_db(config)
    create_tables(db, vector_dim=1024)
    db.open_table("frames").add([_frame_row(path)])
    create_framing_feature_table(db, profile)
    source = FramingSpatialSource(
        frame_id="frame-a",
        film_id="film-a",
        unit_id="unit-a",
        path=path,
        source_size=path.stat().st_size,
        source_mtime_ns=path.stat().st_mtime_ns,
    )
    grid = np.ones((1, 6, 6, 4), dtype=np.float32) / 2
    rows = make_framing_feature_rows([source], grid, profile)
    db.open_table(profile.table_name).add(rows)
    publish_framing_manifest(config, db, profile, frame_ids=["frame-a"])

    with patch(
        "pipeline.index.framing_features.configured_framing_spatial_profile",
        return_value=profile,
    ):
        assert resolve_ready_framing_profile(config, db) == profile
        loaded = load_framing_grids(db, profile, ["frame-a"])
        assert loaded is not None
        np.testing.assert_allclose(loaded, grid, atol=0.0)

        second = _frame_row(path, frame_id="frame-b")
        second["unit_id"] = "unit-b"
        second["shot_id"] = "unit-b"
        db.open_table("frames").add([second])
        assert resolve_ready_framing_profile(config, db) is None


def test_descriptor_checksum_failure_disables_whole_lookup(
    config,
    tmp_path: Path,
) -> None:
    path = tmp_path / "frame.webp"
    path.write_bytes(b"frame")
    profile = _profile()
    db = open_db(config)
    create_framing_feature_table(db, profile)
    source = FramingSpatialSource(
        frame_id="frame-a",
        film_id="film-a",
        unit_id="unit-a",
        path=path,
        source_size=path.stat().st_size,
        source_mtime_ns=path.stat().st_mtime_ns,
    )
    row = make_framing_feature_rows(
        [source],
        np.zeros((1, 6, 6, 4), dtype=np.float32),
        profile,
    )[0]
    row["descriptor_sha256"] = "0" * 64
    db.open_table(profile.table_name).add([row])

    assert load_framing_grids(db, profile, ["frame-a"]) is None


def test_manifest_frame_id_digest_is_actually_validated(
    config,
    tmp_path: Path,
) -> None:
    path = tmp_path / "frame.webp"
    path.write_bytes(b"frame")
    profile = _profile()
    db = open_db(config)
    create_tables(db, vector_dim=1024)
    db.open_table("frames").add([_frame_row(path)])
    create_framing_feature_table(db, profile)
    source = FramingSpatialSource(
        frame_id="frame-a",
        film_id="film-a",
        unit_id="unit-a",
        path=path,
        source_size=path.stat().st_size,
        source_mtime_ns=path.stat().st_mtime_ns,
    )
    db.open_table(profile.table_name).add(
        make_framing_feature_rows(
            [source],
            np.zeros((1, 6, 6, 4), dtype=np.float32),
            profile,
        )
    )
    publish_framing_manifest(config, db, profile, frame_ids=["frame-a"])
    path_to_manifest = manifest_path(config, profile)
    payload = json.loads(path_to_manifest.read_text(encoding="utf-8"))
    payload["frame_ids_sha256"] = "0" * 64
    path_to_manifest.write_text(
        json.dumps(payload),
        encoding="utf-8",
    )

    with patch(
        "pipeline.index.framing_features.configured_framing_spatial_profile",
        return_value=profile,
    ):
        assert resolve_ready_framing_profile(config, db) is None


def test_manifest_digest_rejects_same_shape_frames_replacement(
    config,
    tmp_path: Path,
) -> None:
    path = tmp_path / "frame.webp"
    path.write_bytes(b"frame")
    profile = _profile()
    db = open_db(config)
    create_tables(db, vector_dim=1024)
    db.open_table("frames").add([_frame_row(path)])
    original_version = int(db.open_table("frames").version)
    create_framing_feature_table(db, profile)
    source = FramingSpatialSource(
        frame_id="frame-a",
        film_id="film-a",
        unit_id="unit-a",
        path=path,
        source_size=path.stat().st_size,
        source_mtime_ns=path.stat().st_mtime_ns,
    )
    db.open_table(profile.table_name).add(
        make_framing_feature_rows(
            [source],
            np.zeros((1, 6, 6, 4), dtype=np.float32),
            profile,
        )
    )
    publish_framing_manifest(config, db, profile, frame_ids=["frame-a"])

    db.drop_table("frames")
    create_tables(db, vector_dim=1024)
    replacement = _frame_row(path, frame_id="frame-b")
    replacement["unit_id"] = "unit-b"
    replacement["shot_id"] = "unit-b"
    db.open_table("frames").add([replacement])
    assert int(db.open_table("frames").version) == original_version
    assert db.open_table("frames").count_rows() == 1

    with patch(
        "pipeline.index.framing_features.configured_framing_spatial_profile",
        return_value=profile,
    ):
        assert resolve_ready_framing_profile(config, db) is None
