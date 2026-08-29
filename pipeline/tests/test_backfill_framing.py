"""Focused coverage for the idempotent Framing descriptor backfill."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
from PIL import Image

from pipeline.index.backfill_framing import (
    FramingBackfillProgress,
    backfill_framing_features,
)
from pipeline.index.framing_features import FramingSpatialProfile
from pipeline.index.writer import create_tables, open_db


def _profile() -> FramingSpatialProfile:
    return FramingSpatialProfile(
        profile_id="framing-spatial-backfill-test-v1",
        table_name="frame_framing_backfill_test_v1",
        encoder_name="pe_core_l14",
        model_id="timm/PE-Core-L-14-336",
        model_revision="b" * 40,
        open_clip_version="3.3.0",
        timm_version="1.0.27",
        torch_version="2.11.0",
        torchvision_version="0.26.0",
        pillow_version="12.1.1",
        row_schema_version=1,
        grid_size=6,
        feature_dim=4,
        extraction_contract_version=1,
        storage_dtype="float16-le",
    )


def _add_frame(
    config,
    path: Path,
    *,
    frame_id: str = "frame-a",
    film_id: str = "film-a",
    unit_id: str = "unit-a",
) -> None:
    db = open_db(config)
    create_tables(db, vector_dim=1024)
    stat = path.stat()
    db.open_table("frames").add(
        [
            {
                "schema_version": 1,
                "frame_id": frame_id,
                "film_id": film_id,
                "unit_id": unit_id,
                "shot_id": unit_id,
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
        ]
    )


def test_backfill_activates_and_repeat_skips_current(
    config,
    tmp_path: Path,
) -> None:
    paths = [tmp_path / f"frame-{index}.webp" for index in range(3)]
    for index, path in enumerate(paths):
        Image.new("RGB", (32, 18), "navy").save(path)
        _add_frame(
            config,
            path,
            frame_id=f"frame-{index}",
            unit_id=f"unit-{index}",
        )
    profile = _profile()

    def embed(images, _config, *, grid_size, model_revision):
        count = len(images)
        assert grid_size == 6
        assert model_revision == profile.model_revision
        return (
            np.zeros((count, 4), dtype=np.float32),
            np.ones((count, 6, 6, 4), dtype=np.float32) / 2,
        )

    embedding = MagicMock(side_effect=embed)
    progress: list[FramingBackfillProgress] = []

    with (
        patch(
            "pipeline.index.backfill_framing."
            "configured_framing_spatial_profile",
            return_value=profile,
        ),
        patch(
            "pipeline.index.backfill_framing.embed_spatial_images",
            embedding,
        ),
    ):
        first = backfill_framing_features(
            config,
            batch_size=2,
            progress_callback=progress.append,
        )
        second = backfill_framing_features(config, batch_size=2)

    assert first.embedded == 3
    assert first.activated
    assert second.embedded == 0
    assert second.skipped_current == 3
    assert second.activated
    assert embedding.call_count == 2
    assert progress == [
        FramingBackfillProgress(
            discovered=3,
            completed=0,
            embedded=0,
            skipped_current=0,
        ),
        FramingBackfillProgress(
            discovered=3,
            completed=2,
            embedded=2,
            skipped_current=0,
        ),
        FramingBackfillProgress(
            discovered=3,
            completed=3,
            embedded=3,
            skipped_current=0,
        ),
    ]


def test_scoped_backfill_remains_inactive_when_coverage_is_partial(
    config,
    tmp_path: Path,
) -> None:
    first_path = tmp_path / "first.webp"
    second_path = tmp_path / "second.webp"
    Image.new("RGB", (32, 18), "navy").save(first_path)
    Image.new("RGB", (32, 18), "black").save(second_path)
    _add_frame(config, first_path)
    db = open_db(config)
    second_stat = second_path.stat()
    second = {
        **db.open_table("frames").search().limit(1).to_list()[0],
        "frame_id": "frame-b",
        "film_id": "film-b",
        "unit_id": "unit-b",
        "shot_id": "unit-b",
        "path": str(second_path),
        "source_size": second_stat.st_size,
        "source_mtime_ns": second_stat.st_mtime_ns,
    }
    second.pop("_distance", None)
    db.open_table("frames").add([second])
    profile = _profile()

    with (
        patch(
            "pipeline.index.backfill_framing."
            "configured_framing_spatial_profile",
            return_value=profile,
        ),
        patch(
            "pipeline.index.backfill_framing.embed_spatial_images",
            return_value=(
                np.zeros((1, 4), dtype=np.float32),
                np.ones((1, 6, 6, 4), dtype=np.float32) / 2,
            ),
        ),
    ):
        result = backfill_framing_features(
            config,
            film_id="film-a",
            batch_size=4,
        )

    assert result.discovered == 1
    assert result.embedded == 1
    assert not result.activated


def test_idempotent_backfill_detects_and_repairs_corrupt_payload(
    config,
    tmp_path: Path,
) -> None:
    path = tmp_path / "frame.webp"
    Image.new("RGB", (32, 18), "navy").save(path)
    _add_frame(config, path)
    profile = _profile()
    embedding = MagicMock(
        return_value=(
            np.zeros((1, 4), dtype=np.float32),
            np.ones((1, 6, 6, 4), dtype=np.float32) / 2,
        )
    )

    with (
        patch(
            "pipeline.index.backfill_framing."
            "configured_framing_spatial_profile",
            return_value=profile,
        ),
        patch(
            "pipeline.index.backfill_framing.embed_spatial_images",
            embedding,
        ),
    ):
        first = backfill_framing_features(config)
        assert first.activated
        db = open_db(config)
        table = db.open_table(profile.table_name)
        row = table.search().limit(1).to_list()[0]
        row.pop("_distance", None)
        row["descriptor"] = bytes(len(row["descriptor"]))
        table.delete("frame_id = 'frame-a'")
        table.add([row])

        repaired = backfill_framing_features(config)

    assert repaired.embedded == 1
    assert repaired.activated
    assert embedding.call_count == 2


def test_scoped_backfill_removes_obsolete_rows_and_reactivates(
    config,
    tmp_path: Path,
) -> None:
    old_path = tmp_path / "old.webp"
    new_path = tmp_path / "new.webp"
    Image.new("RGB", (32, 18), "navy").save(old_path)
    Image.new("RGB", (32, 18), "black").save(new_path)
    _add_frame(config, old_path)
    profile = _profile()
    embedding = MagicMock(
        return_value=(
            np.zeros((1, 4), dtype=np.float32),
            np.ones((1, 6, 6, 4), dtype=np.float32) / 2,
        )
    )

    with (
        patch(
            "pipeline.index.backfill_framing."
            "configured_framing_spatial_profile",
            return_value=profile,
        ),
        patch(
            "pipeline.index.backfill_framing.embed_spatial_images",
            embedding,
        ),
    ):
        assert backfill_framing_features(config).activated
        db = open_db(config)
        db.open_table("frames").delete("frame_id = 'frame-a'")
        _add_frame(
            config,
            new_path,
            frame_id="frame-b",
            film_id="film-a",
            unit_id="unit-b",
        )

        reconciled = backfill_framing_features(
            config,
            film_id="film-a",
        )

    assert reconciled.embedded == 1
    assert reconciled.activated
    rows = (
        open_db(config)
        .open_table(profile.table_name)
        .search()
        .select(["frame_id"])
        .limit(None)
        .to_list()
    )
    assert rows == [{"frame_id": "frame-b"}]


def test_scoped_backfill_removes_film_with_no_remaining_frames(
    config,
    tmp_path: Path,
) -> None:
    first_path = tmp_path / "first.webp"
    second_path = tmp_path / "second.webp"
    Image.new("RGB", (32, 18), "navy").save(first_path)
    Image.new("RGB", (32, 18), "black").save(second_path)
    _add_frame(config, first_path)
    _add_frame(
        config,
        second_path,
        frame_id="frame-b",
        film_id="film-b",
        unit_id="unit-b",
    )
    profile = _profile()

    def embedding(images, _config, *, grid_size, model_revision):
        count = len(images)
        assert grid_size == 6
        assert model_revision == profile.model_revision
        return (
            np.zeros((count, 4), dtype=np.float32),
            np.ones((count, 6, 6, 4), dtype=np.float32) / 2,
        )

    with (
        patch(
            "pipeline.index.backfill_framing."
            "configured_framing_spatial_profile",
            return_value=profile,
        ),
        patch(
            "pipeline.index.backfill_framing.embed_spatial_images",
            side_effect=embedding,
        ),
    ):
        assert backfill_framing_features(config).activated
        db = open_db(config)
        db.open_table("frames").delete("film_id = 'film-a'")
        reconciled = backfill_framing_features(
            config,
            film_id="film-a",
        )

    assert reconciled.discovered == 0
    assert reconciled.embedded == 0
    assert reconciled.activated
    rows = (
        open_db(config)
        .open_table(profile.table_name)
        .search()
        .select(["frame_id", "film_id"])
        .limit(None)
        .to_list()
    )
    assert rows == [{"frame_id": "frame-b", "film_id": "film-b"}]
