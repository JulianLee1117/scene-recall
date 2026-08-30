"""Tests for versioned, independently backfillable semantic-text features."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pyarrow as pa
import pytest

from pipeline.config import Config
from pipeline.ingest.probe import FilmRecord
from pipeline.ingest.shots import Shot


_DIM = 1024


def _film(tmp_path: Path, film_id: str) -> FilmRecord:
    asset_dir = tmp_path / "assets" / film_id
    asset_dir.mkdir(parents=True, exist_ok=True)
    return FilmRecord(
        film_id=film_id,
        path=tmp_path / f"{film_id}.mkv",
        asset_dir=asset_dir,
        duration=100.0,
        fps=24.0,
        has_embedded_subs=False,
        title=film_id,
    )


def _shot(film_id: str, index: int = 1) -> Shot:
    return Shot(
        shot_id=f"{film_id}_{index:04d}",
        t_start=float(index * 10),
        t_end=float(index * 10 + 5),
        parent_shot_id=None,
        keyframe_times=[float(index * 10 + 2.5)],
    )


def _annotation(caption: str = "A woman waits beneath red neon.") -> dict:
    return {
        "caption": caption,
        "searchable_text": f"{caption} Don't leave me.",
        "mood": ["lonely", "tense"],
        "framing": "medium",
        "setting": "exterior",
        "time_of_day": "night",
        "people_count": 1,
        "energy": "calm",
        "camera_motion": "static",
        "palette": ["red", "black"],
        "subjects": ["woman", "neon sign"],
        "on_screen_text": "HOTEL",
    }


def _vector() -> np.ndarray:
    vector = np.zeros(_DIM, dtype=np.float32)
    vector[0] = 1.0
    return vector


def _write_unit(
    config: Config,
    tmp_path: Path,
    film_id: str,
    *,
    index: int = 1,
) -> None:
    from pipeline.index.writer import create_tables, open_db, write_unit

    db = open_db(config)
    create_tables(db)
    write_unit(
        db,
        _film(tmp_path, film_id),
        _shot(film_id, index),
        _annotation(),
        _vector(),
        _vector(),
        dialogue=["Don't leave me."],
    )


def _fake_embeddings(texts: list[str], _config: Config) -> np.ndarray:
    matrix = np.zeros((len(texts), _DIM), dtype=np.float32)
    for index in range(len(texts)):
        matrix[index, index % _DIM] = 1.0
    return matrix


def test_build_text_sources_keeps_views_independent() -> None:
    from pipeline.index.text_features import build_text_feature_sources

    sources = build_text_feature_sources(
        {
            "unit_id": "film_1_0001",
            "film_id": "film_1",
            "is_representative": True,
            "caption": "A figure waits in a hallway.",
            "dialogue": json.dumps(["Who is there?", "Come in."]),
            "on_screen_text": "ROOM 237",
            "framing": "wide",
            "setting": "interior",
            "time_of_day": "unknown",
            "energy": "calm",
            "camera_motion": "static",
            "mood": json.dumps(["uneasy", "quiet"]),
            "palette": json.dumps(["green"]),
            "subjects": json.dumps(["figure"]),
        }
    )

    by_view = {source.view: source.text for source in sources}
    assert tuple(by_view) == (
        "caption",
        "dialogue",
        "ocr",
        "facets",
        "mood",
    )
    assert by_view["caption"] == "A figure waits in a hallway."
    assert by_view["dialogue"] == "Who is there? Come in."
    assert by_view["ocr"] == "ROOM 237"
    assert "framing: wide" in by_view["facets"]
    assert by_view["mood"] == "mood: uneasy, quiet; energy: calm"
    assert "framing" not in by_view["mood"]
    assert "palette" not in by_view["mood"]
    assert "Who is there?" not in by_view["caption"]
    assert len({source.source_sha256 for source in sources}) == 5


def test_mood_view_uses_known_energy_when_mood_labels_are_empty() -> None:
    from pipeline.index.text_features import build_mood_view_text

    assert build_mood_view_text(
        {"unit_id": "unit-a", "mood": "[]", "energy": "high"}
    ) == "energy: high"
    assert build_mood_view_text(
        {"unit_id": "unit-b", "mood": "[]", "energy": "unknown"}
    ) == ""


def test_backfill_builds_and_activates_complete_profile(
    tmp_path: Path,
    config: Config,
) -> None:
    from pipeline.index.backfill_text import backfill_text_features
    from pipeline.index.text_features import (
        configured_text_profile,
        resolve_ready_text_profile,
    )
    from pipeline.index.writer import open_db

    _write_unit(config, tmp_path, "film_a")
    with patch(
        "pipeline.index.backfill_text.embed_semantic_documents",
        side_effect=_fake_embeddings,
    ) as embed:
        result = backfill_text_features(config)

    assert result.units_discovered == 1
    assert result.features_discovered == 5
    assert result.embedded == 5
    assert result.activated is True
    embed.assert_called_once()

    db = open_db(config)
    profile = configured_text_profile(config)
    rows = db.open_table(profile.table_name).search().limit(None).to_list()
    assert {row["view"] for row in rows} == {
        "caption",
        "dialogue",
        "ocr",
        "facets",
        "mood",
    }
    assert all(row["model_id"] == profile.model_id for row in rows)
    assert resolve_ready_text_profile(config, db) == profile


def test_backfill_is_idempotent_and_does_not_reembed_current_views(
    tmp_path: Path,
    config: Config,
) -> None:
    from pipeline.index.backfill_text import backfill_text_features

    _write_unit(config, tmp_path, "film_a")
    with patch(
        "pipeline.index.backfill_text.embed_semantic_documents",
        side_effect=_fake_embeddings,
    ):
        backfill_text_features(config)

    with patch(
        "pipeline.index.backfill_text.embed_semantic_documents"
    ) as embed:
        result = backfill_text_features(config)

    embed.assert_not_called()
    assert result.embedded == 0
    assert result.replaced == 0
    assert result.skipped_current == 5
    assert result.activated is True


def test_scoped_backfill_does_not_activate_partial_library(
    tmp_path: Path,
    config: Config,
) -> None:
    from pipeline.index.backfill_text import backfill_text_features
    from pipeline.index.text_features import resolve_ready_text_profile
    from pipeline.index.writer import open_db

    _write_unit(config, tmp_path, "film_a")
    _write_unit(config, tmp_path, "film_b")
    with patch(
        "pipeline.index.backfill_text.embed_semantic_documents",
        side_effect=_fake_embeddings,
    ):
        result = backfill_text_features(config, film_id="film_a")

    assert result.activated is False
    assert resolve_ready_text_profile(config, open_db(config)) is None


def test_units_change_invalidates_active_manifest(
    tmp_path: Path,
    config: Config,
) -> None:
    from pipeline.index.backfill_text import backfill_text_features
    from pipeline.index.text_features import resolve_ready_text_profile
    from pipeline.index.writer import open_db

    _write_unit(config, tmp_path, "film_a")
    with patch(
        "pipeline.index.backfill_text.embed_semantic_documents",
        side_effect=_fake_embeddings,
    ):
        backfill_text_features(config)
    assert resolve_ready_text_profile(config, open_db(config)) is not None

    _write_unit(config, tmp_path, "film_b")

    assert resolve_ready_text_profile(config, open_db(config)) is None


def test_manifest_requires_the_current_text_view_contract(
    tmp_path: Path,
    config: Config,
) -> None:
    from pipeline.index.backfill_text import backfill_text_features
    from pipeline.index.text_features import (
        TEXT_VIEW_CONTRACT_VERSION,
        TEXT_VIEWS,
        configured_text_profile,
        manifest_path,
        resolve_ready_text_profile,
    )
    from pipeline.index.writer import open_db

    _write_unit(config, tmp_path, "film_a")
    with patch(
        "pipeline.index.backfill_text.embed_semantic_documents",
        side_effect=_fake_embeddings,
    ):
        backfill_text_features(config)

    db = open_db(config)
    profile = configured_text_profile(config)
    path = manifest_path(config, profile)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["view_contract_version"] == TEXT_VIEW_CONTRACT_VERSION
    assert payload["views"] == list(TEXT_VIEWS)

    payload["views"] = [view for view in TEXT_VIEWS if view != "mood"]
    path.write_text(json.dumps(payload), encoding="utf-8")

    assert resolve_ready_text_profile(config, db) is None


def test_full_backfill_prunes_features_for_deleted_films(
    tmp_path: Path,
    config: Config,
) -> None:
    from pipeline.index.backfill_text import backfill_text_features
    from pipeline.index.text_features import (
        configured_text_profile,
        resolve_ready_text_profile,
    )
    from pipeline.index.writer import open_db

    _write_unit(config, tmp_path, "film_a")
    _write_unit(config, tmp_path, "film_b")
    with patch(
        "pipeline.index.backfill_text.embed_semantic_documents",
        side_effect=_fake_embeddings,
    ):
        backfill_text_features(config)

    db = open_db(config)
    db.open_table("units").delete("film_id = 'film_b'")

    with patch(
        "pipeline.index.backfill_text.embed_semantic_documents",
        side_effect=_fake_embeddings,
    ) as embed:
        result = backfill_text_features(config)

    embed.assert_not_called()
    profile = configured_text_profile(config)
    rows = db.open_table(profile.table_name).search().limit(None).to_list()
    assert {row["film_id"] for row in rows} == {"film_a"}
    assert result.activated is True
    assert resolve_ready_text_profile(config, db) == profile


def test_full_backfill_repairs_duplicate_feature_ids(
    tmp_path: Path,
    config: Config,
) -> None:
    from pipeline.index.backfill_text import backfill_text_features
    from pipeline.index.text_features import configured_text_profile
    from pipeline.index.writer import open_db

    _write_unit(config, tmp_path, "film_a")
    with patch(
        "pipeline.index.backfill_text.embed_semantic_documents",
        side_effect=_fake_embeddings,
    ):
        backfill_text_features(config)

    db = open_db(config)
    profile = configured_text_profile(config)
    table = db.open_table(profile.table_name)
    duplicate = table.search().limit(1).to_list()[0]
    table.add([duplicate])
    assert table.count_rows() == 6

    with patch(
        "pipeline.index.backfill_text.embed_semantic_documents",
        side_effect=_fake_embeddings,
    ) as embed:
        result = backfill_text_features(config)

    embed.assert_not_called()
    assert db.open_table(profile.table_name).count_rows() == 5
    assert result.activated is True


def test_text_feature_table_rejects_incompatible_schema(
    config: Config,
) -> None:
    from pipeline.index.text_features import (
        configured_text_profile,
        create_text_feature_table,
    )
    from pipeline.index.writer import open_db

    db = open_db(config)
    profile = configured_text_profile(config)
    db.create_table(
        profile.table_name,
        schema=pa.schema([pa.field("feature_id", pa.string())]),
    )

    with pytest.raises(RuntimeError, match="incompatible"):
        create_text_feature_table(db, profile)


def test_text_backfill_refuses_to_race_an_ingest(config: Config) -> None:
    from pipeline.index.backfill_text import backfill_text_features
    from pipeline.ingest.locks import global_ingest_lock

    with global_ingest_lock(config.paths.assets_dir):
        with pytest.raises(RuntimeError, match="already running"):
            backfill_text_features(config)
