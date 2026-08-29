from __future__ import annotations

import json
import sqlite3
from unittest.mock import patch

import numpy as np

from pipeline.bookmarks import BookmarkStore
from pipeline.index.writer import create_tables, open_db


def test_bookmark_store_is_versioned_idempotent_and_deletable(tmp_path) -> None:
    store = BookmarkStore(tmp_path / "state")
    store.initialize()

    first = store.save(
        film_id="film-a",
        source_unit_id="film-a_0001",
        evidence_timestamp=12.3454,
        frame_index=1,
        film_title_snapshot="Film A",
    )
    repeated = store.save(
        film_id="film-a",
        source_unit_id="film-a_0001",
        evidence_timestamp=12.3454,
        frame_index=2,
        film_title_snapshot="Film A (renamed)",
    )

    assert repeated.bookmark_id == first.bookmark_id
    assert repeated.evidence_timestamp == 12.345
    assert repeated.frame_index == 2
    assert repeated.film_title_snapshot == "Film A (renamed)"
    assert store.list_all() == [repeated]

    with sqlite3.connect(store.path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1

    assert store.delete(first.bookmark_id) is True
    assert store.delete(first.bookmark_id) is False
    assert store.list_all() == []


def _unit_row(unit_id: str, *, start: float, end: float) -> dict:
    vector = np.zeros(1024, dtype=np.float32).tolist()
    return {
        "unit_id": unit_id,
        "film_id": "film-a",
        "shot_id": unit_id,
        "parent_shot_id": None,
        "t_start": start,
        "t_end": end,
        "is_representative": True,
        "img_vec": vector,
        "txt_vec": vector,
        "caption": f"Scene at {start:g} seconds",
        "searchable_text": "scene",
        "mood": "[]",
        "dialogue": "[]",
        "keyframe_paths": json.dumps([f"{unit_id}_0.webp"]),
        "framing": "wide",
        "setting": "interior",
        "time_of_day": "night",
        "people_count": 1,
        "energy": "still",
        "camera_motion": "static",
        "palette": "[]",
        "subjects": "[]",
        "on_screen_text": "",
    }


def _frame_row(unit_id: str, *, timestamp: float) -> dict:
    vector = np.zeros(1024, dtype=np.float32).tolist()
    return {
        "schema_version": 1,
        "frame_id": f"{unit_id}::frame::0",
        "film_id": "film-a",
        "unit_id": unit_id,
        "shot_id": unit_id,
        "frame_index": 0,
        "timestamp": timestamp,
        "timestamp_source": "keyframe_recipe",
        "path": f"{unit_id}_0.webp",
        # Any indexed keyframe may support a search hit and bookmark.  This
        # flag marks the shot's display representative, not frame validity.
        "is_representative": False,
        "quality_score": None,
        "source_size": 1,
        "source_mtime_ns": 1,
        "visual_encoder": "pe_core_l14",
        "visual_vec": vector,
    }


def test_bookmark_api_remaps_timestamp_and_preserves_unavailable_anchor(
    config,
) -> None:
    from fastapi.testclient import TestClient

    db = open_db(config)
    create_tables(db)
    db.open_table("films").add(
        [
            {
                "film_id": "film-a",
                "title": "Film A",
                "path": str(config.paths.films_dir / "Film A.mkv"),
                "duration": 120.0,
                "fps": 24.0,
            }
        ]
    )
    db.open_table("units").add([_unit_row("film-a_0001", start=10, end=20)])
    db.open_table("frames").add(
        [_frame_row("film-a_0001", timestamp=15.25)]
    )

    with (
        patch("pipeline.api.main.load_config", return_value=config),
        patch("pipeline.api.main.open_db", return_value=db),
        patch("pipeline.api.main.ensure_search_indexes"),
    ):
        import pipeline.api.main as api_mod

        with TestClient(api_mod.app) as client:
            first = client.put(
                "/bookmarks/film-a_0001",
                json={"evidence_timestamp": 15.25, "frame_index": 0},
            )
            repeated = client.put(
                "/bookmarks/film-a_0001",
                json={"evidence_timestamp": 15.25, "frame_index": 0},
            )

            assert first.status_code == 200
            assert repeated.status_code == 200
            assert repeated.json()["bookmark_id"] == first.json()["bookmark_id"]
            assert first.json()["film_id"] == "film-a"
            assert first.json()["film_title"] == "Film A"
            assert first.json()["availability"] == "indexed"
            assert first.json()["scene"]["unit_id"] == "film-a_0001"
            assert first.json()["scene"]["matched_frame_index"] == 0
            assert first.json()["created_at"].endswith("+00:00")

            db.open_table("units").delete("film_id = 'film-a'")
            db.open_table("units").add(
                [_unit_row("film-a_new_0001", start=12, end=18)]
            )
            remapped = client.get("/bookmarks")

            assert remapped.status_code == 200
            record = remapped.json()["bookmarks"][0]
            assert record["source_unit_id"] == "film-a_0001"
            assert record["evidence_timestamp"] == 15.25
            assert record["scene"]["unit_id"] == "film-a_new_0001"

            db.open_table("units").delete("film_id = 'film-a'")
            unavailable = client.get("/bookmarks").json()["bookmarks"][0]
            assert unavailable["availability"] == "source_only"
            assert unavailable["scene"] is None
            assert unavailable["film_title"] == "Film A"

            removed = client.delete(
                f"/bookmarks/{first.json()['bookmark_id']}"
            )
            assert removed.status_code == 204
            assert client.get("/bookmarks").json() == {"bookmarks": []}
