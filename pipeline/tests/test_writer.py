"""Tests for pipeline/index/schema.py and pipeline/index/writer.py — TDD.

Tests:
  - open_db returns a lancedb DBConnection pointed at assets_dir/db
  - create_tables is idempotent (safe to call twice)
  - write_unit persists a row; read-back fields match inputs
  - write_unit is idempotent (calling twice with same unit_id does not duplicate)
  - write_film persists a row; read-back fields match
  - write_film is idempotent
  - Vector dimension is 1024 (PE core L/14)
  - img_vec and txt_vec are stored and retrieved accurately
  - mood and keyframe_paths are round-tripped as JSON
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from pipeline.config import Config
from pipeline.ingest.probe import FilmRecord
from pipeline.ingest.shots import Shot


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

VEC_DIM = 1024  # PE core L/14 — fixed for Phase 1


def _make_film(tmp_path: Path) -> FilmRecord:
    asset_dir = tmp_path / "assets" / "film_abc123"
    asset_dir.mkdir(parents=True, exist_ok=True)
    return FilmRecord(
        film_id="film_abc123",
        path=tmp_path / "test_film.mkv",
        asset_dir=asset_dir,
        duration=120.5,
        fps=24.0,
        has_embedded_subs=False,
        title="Test Film",
    )


def _make_shot() -> Shot:
    return Shot(
        shot_id="film_abc123_0001",
        t_start=10.0,
        t_end=15.5,
        parent_shot_id=None,
        keyframe_times=[11.375, 12.75, 14.125],
    )


def _make_annotation() -> dict:
    return {
        "caption": "A tense scene in a dimly lit corridor.",
        "mood": ["tense", "dark", "suspenseful"],
        "searchable_text": "A tense scene in a dimly lit corridor. Don't move.",
    }


def _rand_vec() -> np.ndarray:
    """Return a random L2-normalised float32 vector of dim 1024."""
    v = np.random.randn(VEC_DIM).astype(np.float32)
    return v / np.linalg.norm(v)


# ---------------------------------------------------------------------------
# open_db
# ---------------------------------------------------------------------------


def test_open_db_returns_connection(config: Config) -> None:
    """open_db returns a lancedb DBConnection; the db/ sub-directory is created."""
    import lancedb
    from pipeline.index.writer import open_db

    db = open_db(config)

    db_path = config.paths.assets_dir / "db"
    assert db_path.exists(), "DB directory should be created by open_db"
    assert isinstance(db, lancedb.DBConnection)


# ---------------------------------------------------------------------------
# create_tables
# ---------------------------------------------------------------------------


def test_create_tables_creates_units_and_films(config: Config) -> None:
    """create_tables creates 'units' and 'films' tables."""
    from pipeline.index.writer import open_db, create_tables

    db = open_db(config)
    create_tables(db)

    names = db.list_tables().tables
    assert "units" in names
    assert "films" in names


def test_create_tables_is_idempotent(config: Config) -> None:
    """create_tables can be called twice without raising."""
    from pipeline.index.writer import open_db, create_tables

    db = open_db(config)
    create_tables(db)
    create_tables(db)  # must not raise


# ---------------------------------------------------------------------------
# write_unit / read back
# ---------------------------------------------------------------------------


def test_write_unit_round_trip_basic_fields(tmp_path: Path, config: Config) -> None:
    """write_unit persists a unit; scalar fields can be read back accurately."""
    from pipeline.index.writer import open_db, create_tables, write_unit

    film = _make_film(tmp_path)
    shot = _make_shot()
    ann = _make_annotation()
    img_vec = _rand_vec()
    txt_vec = _rand_vec()

    db = open_db(config)
    create_tables(db)
    write_unit(db, film, shot, ann, img_vec, txt_vec)

    tbl = db.open_table("units")
    rows = tbl.search().where(f"unit_id = '{shot.shot_id}'").to_list()

    assert len(rows) == 1, "Exactly one row should exist after write_unit"
    row = rows[0]

    assert row["unit_id"] == shot.shot_id
    assert row["film_id"] == film.film_id
    assert row["shot_id"] == shot.shot_id
    assert abs(row["t_start"] - shot.t_start) < 1e-6
    assert abs(row["t_end"] - shot.t_end) < 1e-6
    assert row["is_representative"] is True
    assert row["caption"] == ann["caption"]
    assert row["searchable_text"] == ann["searchable_text"]


def test_write_unit_mood_round_trip(tmp_path: Path, config: Config) -> None:
    """mood is stored as JSON and round-trips to a list of strings."""
    from pipeline.index.writer import open_db, create_tables, write_unit

    film = _make_film(tmp_path)
    shot = _make_shot()
    ann = _make_annotation()
    img_vec = _rand_vec()
    txt_vec = _rand_vec()

    db = open_db(config)
    create_tables(db)
    write_unit(db, film, shot, ann, img_vec, txt_vec)

    tbl = db.open_table("units")
    rows = tbl.search().where(f"unit_id = '{shot.shot_id}'").to_list()
    row = rows[0]

    mood_stored = json.loads(row["mood"])
    assert mood_stored == ann["mood"]


def test_write_unit_vectors_round_trip(tmp_path: Path, config: Config) -> None:
    """img_vec and txt_vec are stored and retrieved with acceptable precision."""
    from pipeline.index.writer import open_db, create_tables, write_unit

    film = _make_film(tmp_path)
    shot = _make_shot()
    ann = _make_annotation()
    img_vec = _rand_vec()
    txt_vec = _rand_vec()

    db = open_db(config)
    create_tables(db)
    write_unit(db, film, shot, ann, img_vec, txt_vec)

    tbl = db.open_table("units")
    rows = tbl.search().where(f"unit_id = '{shot.shot_id}'").to_list()
    row = rows[0]

    retrieved_img = np.array(row["img_vec"], dtype=np.float32)
    retrieved_txt = np.array(row["txt_vec"], dtype=np.float32)

    assert retrieved_img.shape == (VEC_DIM,)
    assert retrieved_txt.shape == (VEC_DIM,)
    np.testing.assert_allclose(retrieved_img, img_vec, atol=1e-5)
    np.testing.assert_allclose(retrieved_txt, txt_vec, atol=1e-5)


def test_write_unit_keyframe_paths_stored(tmp_path: Path, config: Config) -> None:
    """keyframe_paths is stored as JSON and contains the expected paths."""
    from pipeline.index.writer import open_db, create_tables, write_unit

    film = _make_film(tmp_path)
    shot = _make_shot()
    ann = _make_annotation()
    img_vec = _rand_vec()
    txt_vec = _rand_vec()

    db = open_db(config)
    create_tables(db)
    write_unit(db, film, shot, ann, img_vec, txt_vec)

    tbl = db.open_table("units")
    rows = tbl.search().where(f"unit_id = '{shot.shot_id}'").to_list()
    row = rows[0]

    paths = json.loads(row["keyframe_paths"])
    assert isinstance(paths, list)
    assert len(paths) == len(shot.keyframe_times)


def test_write_unit_is_idempotent(tmp_path: Path, config: Config) -> None:
    """write_unit called twice with the same unit_id does not create a duplicate."""
    from pipeline.index.writer import open_db, create_tables, write_unit

    film = _make_film(tmp_path)
    shot = _make_shot()
    ann = _make_annotation()
    img_vec = _rand_vec()
    txt_vec = _rand_vec()

    db = open_db(config)
    create_tables(db)
    write_unit(db, film, shot, ann, img_vec, txt_vec)
    write_unit(db, film, shot, ann, img_vec, txt_vec)  # second write — same unit_id

    tbl = db.open_table("units")
    rows = tbl.search().where(f"unit_id = '{shot.shot_id}'").to_list()
    assert len(rows) == 1, "Idempotent write must not create duplicate rows"


# ---------------------------------------------------------------------------
# write_film / read back
# ---------------------------------------------------------------------------


def test_write_film_round_trip(tmp_path: Path, config: Config) -> None:
    """write_film persists a FilmRecord; fields can be read back accurately."""
    from pipeline.index.writer import open_db, create_tables, write_film

    film = _make_film(tmp_path)

    db = open_db(config)
    create_tables(db)
    write_film(db, film)

    tbl = db.open_table("films")
    rows = tbl.search().where(f"film_id = '{film.film_id}'").to_list()

    assert len(rows) == 1
    row = rows[0]

    assert row["film_id"] == film.film_id
    assert row["title"] == film.title
    assert row["path"] == str(film.path)
    assert abs(row["duration"] - film.duration) < 1e-6


def test_write_film_is_idempotent(tmp_path: Path, config: Config) -> None:
    """write_film called twice with the same film_id does not create a duplicate."""
    from pipeline.index.writer import open_db, create_tables, write_film

    film = _make_film(tmp_path)

    db = open_db(config)
    create_tables(db)
    write_film(db, film)
    write_film(db, film)  # second write

    tbl = db.open_table("films")
    rows = tbl.search().where(f"film_id = '{film.film_id}'").to_list()
    assert len(rows) == 1, "Idempotent write must not create duplicate film rows"
