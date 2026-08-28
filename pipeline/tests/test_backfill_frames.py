"""Focused tests for the existing-keyframe frame-index backfill."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

from pipeline.config import Config
from pipeline.index.backfill_frames import backfill_frames
from pipeline.index.schema import FRAMES_SCHEMA_VERSION
from pipeline.index.writer import create_tables, open_db


VEC_DIM = 1024


def _add_unit(
    config: Config,
    tmp_path: Path,
    *,
    film_id: str = "film_a",
    unit_id: str = "film_a_0001",
    t_start: float = 10.0,
    t_end: float = 18.0,
    frame_count: int = 3,
    create_files: bool = True,
) -> list[Path]:
    db = open_db(config)
    create_tables(db, vector_dim=VEC_DIM)
    paths = [
        tmp_path / "keyframes" / f"{unit_id}_{index}.webp"
        for index in range(frame_count)
    ]
    if create_files:
        for index, path in enumerate(paths):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(f"frame-{index}".encode())
    db.open_table("units").add(
        [
            {
                "unit_id": unit_id,
                "film_id": film_id,
                "shot_id": unit_id,
                "t_start": t_start,
                "t_end": t_end,
                "is_representative": True,
                "img_vec": np.zeros(VEC_DIM, dtype=np.float32).tolist(),
                "txt_vec": np.zeros(VEC_DIM, dtype=np.float32).tolist(),
                "caption": "",
                "searchable_text": "",
                "mood": "[]",
                "dialogue": "[]",
                "keyframe_paths": json.dumps([str(path) for path in paths]),
            }
        ]
    )
    return paths


def _fake_vectors(paths: list[Path], _config: Config) -> np.ndarray:
    vectors = np.zeros((len(paths), VEC_DIM), dtype=np.float32)
    for index in range(len(paths)):
        vectors[index, index] = 1.0
    return vectors


def test_create_tables_adds_versioned_frames_schema(config: Config) -> None:
    db = open_db(config)
    create_tables(db, vector_dim=VEC_DIM)

    assert "frames" in db.list_tables().tables
    schema = db.open_table("frames").schema
    assert schema.field("visual_vec").type.list_size == VEC_DIM
    assert schema.field("frame_id").type == schema.field("path").type


def test_frame_backfill_refuses_to_race_an_ingest(config: Config) -> None:
    from pipeline.ingest.locks import global_ingest_lock

    with global_ingest_lock(config.paths.assets_dir):
        with pytest.raises(RuntimeError, match="already running"):
            backfill_frames(config)


def test_backfill_writes_one_independent_row_per_keyframe(
    config: Config,
    tmp_path: Path,
) -> None:
    paths = _add_unit(config, tmp_path)

    with patch(
        "pipeline.index.backfill_frames.embed_images",
        side_effect=_fake_vectors,
    ):
        result = backfill_frames(config, batch_size=2)

    assert result.discovered == 3
    assert result.embedded == 3
    assert result.upserted == 3
    assert result.skipped_current == 0

    rows = sorted(
        open_db(config).open_table("frames").to_arrow().to_pylist(),
        key=lambda row: row["frame_index"],
    )
    assert [row["frame_id"] for row in rows] == [
        "film_a_0001::frame::0",
        "film_a_0001::frame::1",
        "film_a_0001::frame::2",
    ]
    assert [row["timestamp"] for row in rows] == [12.0, 14.0, 16.0]
    assert [row["path"] for row in rows] == [str(path) for path in paths]
    assert [row["is_representative"] for row in rows] == [False, True, False]
    assert all(row["schema_version"] == FRAMES_SCHEMA_VERSION for row in rows)
    assert all(row["visual_encoder"] == "pe_core_l14" for row in rows)
    assert all(row["quality_score"] is None for row in rows)
    np.testing.assert_array_equal(rows[0]["visual_vec"][:3], [1.0, 0.0, 0.0])
    np.testing.assert_array_equal(rows[1]["visual_vec"][:3], [0.0, 1.0, 0.0])


def test_backfill_is_idempotent_without_reembedding(
    config: Config,
    tmp_path: Path,
) -> None:
    _add_unit(config, tmp_path)

    with patch(
        "pipeline.index.backfill_frames.embed_images",
        side_effect=_fake_vectors,
    ) as embed:
        first = backfill_frames(config)
        second = backfill_frames(config)

    assert first.upserted == 3
    assert second.upserted == 0
    assert second.skipped_current == 3
    assert embed.call_count == 1
    assert open_db(config).open_table("frames").count_rows() == 3


def test_backfill_can_scope_one_film(config: Config, tmp_path: Path) -> None:
    _add_unit(config, tmp_path, film_id="film_a", unit_id="film_a_0001")
    _add_unit(config, tmp_path, film_id="film_b", unit_id="film_b_0001")

    with patch(
        "pipeline.index.backfill_frames.embed_images",
        side_effect=_fake_vectors,
    ):
        result = backfill_frames(config, film_id="film_b")

    assert result.discovered == 3
    rows = open_db(config).open_table("frames").to_arrow().to_pylist()
    assert {row["film_id"] for row in rows} == {"film_b"}


def test_backfill_reconstructs_short_first_seek_pad(
    config: Config,
    tmp_path: Path,
) -> None:
    _add_unit(
        config,
        tmp_path,
        t_start=4.0,
        t_end=4.1,
        frame_count=1,
    )

    with patch(
        "pipeline.index.backfill_frames.embed_images",
        side_effect=_fake_vectors,
    ):
        backfill_frames(config)

    row = open_db(config).open_table("frames").to_arrow().to_pylist()[0]
    assert row["timestamp"] == pytest.approx(4.1)
    assert row["timestamp_source"] == "reconstructed_legacy_keyframe_seek_v1"


def test_backfill_fails_before_embedding_when_keyframe_missing(
    config: Config,
    tmp_path: Path,
) -> None:
    _add_unit(config, tmp_path, create_files=False)

    with patch("pipeline.index.backfill_frames.embed_images") as embed:
        with pytest.raises(FileNotFoundError, match="3 indexed keyframe"):
            backfill_frames(config)

    embed.assert_not_called()
    assert open_db(config).open_table("frames").count_rows() == 0


def test_backfill_rejects_nonpositive_batch_size(config: Config) -> None:
    with pytest.raises(ValueError, match="batch_size"):
        backfill_frames(config, batch_size=0)
