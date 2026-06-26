"""Tests for pipeline/search/retrieve.py and pipeline/api/main.py — TDD.

Tests:
  - search: returns list of dicts with required keys
  - search: keyframe_url and preview_url are correctly formatted
  - search: calls embed_text with the query
  - API GET /search?q=...: returns {"results": [...]}
  - API GET /unit/{unit_id}: returns unit row dict
  - API GET /media/keyframe/{shot_id}/{n}: FileResponse when file exists
  - API GET /media/keyframe/{shot_id}/{n}: 404 when file missing
  - API GET /media/preview/{shot_id}: FileResponse when file exists
  - API GET /media/preview/{shot_id}: 404 when file missing

All LanceDB and embed_text calls are mocked — no real DB or model in CI.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from pipeline.config import Config


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VEC_DIM = 1024


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_vec() -> np.ndarray:
    """Return a random L2-normalised float32 row vector, shape (1, VEC_DIM)."""
    v = np.random.randn(VEC_DIM).astype(np.float32)
    return (v / np.linalg.norm(v)).reshape(1, -1)


def _make_unit_row(
    shot_id: str = "film_abc_0001",
    film_id: str = "film_abc",
) -> dict:
    return {
        "unit_id": shot_id,
        "film_id": film_id,
        "shot_id": shot_id,
        "t_start": 10.0,
        "t_end": 15.5,
        "is_representative": True,
        "caption": "A rainy night scene",
        "searchable_text": "rainy night alone",
        "dialogue": "[]",
        "keyframe_paths": json.dumps([f"/assets/keyframes/{shot_id}_0.webp"]),
        "mood": '["dark", "melancholic"]',
        "img_vec": [0.0] * VEC_DIM,
        "txt_vec": [0.0] * VEC_DIM,
        "_distance": 0.1,
    }


def _make_search_mock_db(rows: list[dict]) -> MagicMock:
    """Mock DB for vector-search chain:
    open_table("units").search(vec, ...).metric(...).limit(...).where(...).to_list()
    """
    chain = MagicMock()
    chain.metric.return_value = chain
    chain.limit.return_value = chain
    chain.where.return_value = chain
    chain.to_list.return_value = rows

    tbl = MagicMock()
    tbl.search.return_value = chain

    db = MagicMock()
    db.open_table.return_value = tbl
    return db


def _make_filter_mock_db(rows: list[dict]) -> MagicMock:
    """Mock DB for scalar-filter chain:
    open_table("units").search().where("unit_id = '...'").to_list()
    """
    chain = MagicMock()
    chain.where.return_value = chain
    chain.to_list.return_value = rows

    tbl = MagicMock()
    tbl.search.return_value = chain

    db = MagicMock()
    db.open_table.return_value = tbl
    return db


# ---------------------------------------------------------------------------
# search()
# ---------------------------------------------------------------------------


def test_search_returns_nonempty_list(config: Config) -> None:
    """search('rainy night alone', db, config) returns a non-empty list."""
    from pipeline.search.retrieve import search

    rows = [_make_unit_row()]
    mock_db = _make_search_mock_db(rows)

    with patch("pipeline.search.retrieve.embed_text", return_value=_fake_vec()):
        results = search("rainy night alone", mock_db, config)

    assert isinstance(results, list)
    assert len(results) > 0


def test_search_result_has_required_keys(config: Config) -> None:
    """Each search result dict contains all required keys."""
    from pipeline.search.retrieve import search

    rows = [_make_unit_row()]
    mock_db = _make_search_mock_db(rows)

    with patch("pipeline.search.retrieve.embed_text", return_value=_fake_vec()):
        results = search("rainy night alone", mock_db, config)

    result = results[0]
    required = ("unit_id", "film_id", "t_start", "t_end", "caption", "keyframe_url", "preview_url")
    for key in required:
        assert key in result, f"Missing key: {key}"


def test_search_keyframe_url_format(config: Config) -> None:
    """keyframe_url is /media/keyframe/{shot_id}/0."""
    from pipeline.search.retrieve import search

    shot_id = "film_abc_0001"
    rows = [_make_unit_row(shot_id=shot_id)]
    mock_db = _make_search_mock_db(rows)

    with patch("pipeline.search.retrieve.embed_text", return_value=_fake_vec()):
        results = search("rain", mock_db, config)

    assert results[0]["keyframe_url"] == f"/media/keyframe/{shot_id}/0"


def test_search_preview_url_format(config: Config) -> None:
    """preview_url is /media/preview/{shot_id}."""
    from pipeline.search.retrieve import search

    shot_id = "film_abc_0001"
    rows = [_make_unit_row(shot_id=shot_id)]
    mock_db = _make_search_mock_db(rows)

    with patch("pipeline.search.retrieve.embed_text", return_value=_fake_vec()):
        results = search("rain", mock_db, config)

    assert results[0]["preview_url"] == f"/media/preview/{shot_id}"


def test_search_calls_embed_text_with_query(config: Config) -> None:
    """search() calls embed_text([query], config) exactly once."""
    from pipeline.search.retrieve import search

    mock_db = _make_search_mock_db([])
    fake_vec = np.zeros((1, VEC_DIM), dtype=np.float32)

    with patch("pipeline.search.retrieve.embed_text", return_value=fake_vec) as mock_embed:
        search("rainy night alone", mock_db, config)

    mock_embed.assert_called_once_with(["rainy night alone"], config)


def test_search_empty_db_returns_empty_list(config: Config) -> None:
    """search() returns an empty list when the DB returns no rows."""
    from pipeline.search.retrieve import search

    mock_db = _make_search_mock_db([])

    with patch("pipeline.search.retrieve.embed_text", return_value=_fake_vec()):
        results = search("nothing matches", mock_db, config)

    assert results == []


# ---------------------------------------------------------------------------
# FastAPI app endpoints
# ---------------------------------------------------------------------------


def test_api_search_returns_results_dict(config: Config) -> None:
    """GET /search?q=rain returns {"results": [...]} with expected keys."""
    from fastapi.testclient import TestClient

    rows = [_make_unit_row()]
    mock_db = _make_search_mock_db(rows)
    fake_vec = _fake_vec()

    with (
        patch("pipeline.api.main.load_config", return_value=config),
        patch("pipeline.api.main.open_db", return_value=mock_db),
        patch("pipeline.search.retrieve.embed_text", return_value=fake_vec),
    ):
        import pipeline.api.main as api_mod  # noqa: PLC0415
        with TestClient(api_mod.app) as client:
            response = client.get("/search?q=rain")

    assert response.status_code == 200
    data = response.json()
    assert "results" in data
    assert isinstance(data["results"], list)
    assert len(data["results"]) > 0
    result = data["results"][0]
    for key in ("unit_id", "film_id", "t_start", "t_end", "caption", "keyframe_url", "preview_url"):
        assert key in result, f"API result missing key: {key}"


def test_api_unit_endpoint_returns_row(config: Config) -> None:
    """GET /unit/{unit_id} returns the matching unit row."""
    from fastapi.testclient import TestClient

    row = _make_unit_row()
    mock_db = _make_filter_mock_db([row])

    with (
        patch("pipeline.api.main.load_config", return_value=config),
        patch("pipeline.api.main.open_db", return_value=mock_db),
    ):
        import pipeline.api.main as api_mod  # noqa: PLC0415
        with TestClient(api_mod.app) as client:
            response = client.get(f"/unit/{row['unit_id']}")

    assert response.status_code == 200
    data = response.json()
    assert data["unit_id"] == row["unit_id"]
    assert data["film_id"] == row["film_id"]


def test_api_unit_endpoint_404_when_not_found(config: Config) -> None:
    """GET /unit/{unit_id} returns 404 when unit does not exist."""
    from fastapi.testclient import TestClient

    mock_db = _make_filter_mock_db([])  # empty result

    with (
        patch("pipeline.api.main.load_config", return_value=config),
        patch("pipeline.api.main.open_db", return_value=mock_db),
    ):
        import pipeline.api.main as api_mod  # noqa: PLC0415
        with TestClient(api_mod.app, raise_server_exceptions=False) as client:
            response = client.get("/unit/nonexistent_unit")

    assert response.status_code == 404


def test_api_keyframe_returns_file(tmp_path: Path, config: Config) -> None:
    """GET /media/keyframe/{shot_id}/{n} returns 200 when file exists."""
    from fastapi.testclient import TestClient

    keyframe_dir = config.paths.assets_dir / "keyframes"
    keyframe_dir.mkdir(parents=True, exist_ok=True)
    (keyframe_dir / "test_shot_0.webp").write_bytes(b"RIFF fake webp")

    mock_db = MagicMock()

    with (
        patch("pipeline.api.main.load_config", return_value=config),
        patch("pipeline.api.main.open_db", return_value=mock_db),
    ):
        import pipeline.api.main as api_mod  # noqa: PLC0415
        with TestClient(api_mod.app) as client:
            response = client.get("/media/keyframe/test_shot/0")

    assert response.status_code == 200


def test_api_keyframe_404_when_missing(config: Config) -> None:
    """GET /media/keyframe/{shot_id}/{n} returns 404 when file is absent."""
    from fastapi.testclient import TestClient

    mock_db = MagicMock()

    with (
        patch("pipeline.api.main.load_config", return_value=config),
        patch("pipeline.api.main.open_db", return_value=mock_db),
    ):
        import pipeline.api.main as api_mod  # noqa: PLC0415
        with TestClient(api_mod.app, raise_server_exceptions=False) as client:
            response = client.get("/media/keyframe/no_such_shot/0")

    assert response.status_code == 404


def test_api_preview_returns_file(tmp_path: Path, config: Config) -> None:
    """GET /media/preview/{shot_id} returns 200 when file exists."""
    from fastapi.testclient import TestClient

    preview_dir = config.paths.assets_dir / "previews"
    preview_dir.mkdir(parents=True, exist_ok=True)
    (preview_dir / "test_shot.webm").write_bytes(b"fake webm bytes")

    mock_db = MagicMock()

    with (
        patch("pipeline.api.main.load_config", return_value=config),
        patch("pipeline.api.main.open_db", return_value=mock_db),
    ):
        import pipeline.api.main as api_mod  # noqa: PLC0415
        with TestClient(api_mod.app) as client:
            response = client.get("/media/preview/test_shot")

    assert response.status_code == 200


def test_api_preview_404_when_missing(config: Config) -> None:
    """GET /media/preview/{shot_id} returns 404 when file is absent."""
    from fastapi.testclient import TestClient

    mock_db = MagicMock()

    with (
        patch("pipeline.api.main.load_config", return_value=config),
        patch("pipeline.api.main.open_db", return_value=mock_db),
    ):
        import pipeline.api.main as api_mod  # noqa: PLC0415
        with TestClient(api_mod.app, raise_server_exceptions=False) as client:
            response = client.get("/media/preview/no_such_shot")

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Film mock DB helper
# ---------------------------------------------------------------------------


def _make_film_mock_db(rows: list[dict]) -> MagicMock:
    """Mock DB for film lookup:
    open_table("films").search().where(...).to_list()
    """
    chain = MagicMock()
    chain.where.return_value = chain
    chain.to_list.return_value = rows

    tbl = MagicMock()
    tbl.search.return_value = chain

    db = MagicMock()
    db.open_table.return_value = tbl
    return db


# ---------------------------------------------------------------------------
# _parse_range
# ---------------------------------------------------------------------------


def test_parse_range_normal() -> None:
    """bytes=0-999 on a 1000-byte file returns (0, 999)."""
    from pipeline.api.main import _parse_range

    start, end = _parse_range("bytes=0-999", 1000)
    assert start == 0
    assert end == 999


def test_parse_range_open_end() -> None:
    """bytes=100- returns start=100, end=file_size-1."""
    from pipeline.api.main import _parse_range

    start, end = _parse_range("bytes=100-", 1000)
    assert start == 100
    assert end == 999


def test_parse_range_suffix() -> None:
    """bytes=-500 means last 500 bytes: start=file_size-500, end=file_size-1."""
    from pipeline.api.main import _parse_range

    start, end = _parse_range("bytes=-500", 1000)
    assert start == 500
    assert end == 999


def test_parse_range_invalid_returns_416() -> None:
    """A malformed Range header raises HTTPException(416)."""
    from fastapi import HTTPException

    from pipeline.api.main import _parse_range

    with pytest.raises(HTTPException) as exc_info:
        _parse_range("totally-bogus", 1000)
    assert exc_info.value.status_code == 416


# ---------------------------------------------------------------------------
# GET /video/{film_id}
# ---------------------------------------------------------------------------


def test_api_video_returns_200_with_accept_ranges(tmp_path: Path, config: Config) -> None:
    """GET /video/{film_id} returns 200, Accept-Ranges header, and file content."""
    from fastapi.testclient import TestClient

    video_file = tmp_path / "test.mp4"
    video_file.write_bytes(b"fake video content")

    row = {"film_id": "film_test", "path": str(video_file)}
    mock_db = _make_film_mock_db([row])

    with (
        patch("pipeline.api.main.load_config", return_value=config),
        patch("pipeline.api.main.open_db", return_value=mock_db),
    ):
        import pipeline.api.main as api_mod  # noqa: PLC0415
        with TestClient(api_mod.app) as client:
            response = client.get("/video/film_test")

    assert response.status_code == 200
    assert response.headers.get("Accept-Ranges") == "bytes"
    assert response.content == b"fake video content"


def test_api_video_range_request_returns_206(tmp_path: Path, config: Config) -> None:
    """GET /video/{film_id} with Range header returns 206 and Content-Range."""
    from fastapi.testclient import TestClient

    content = b"0123456789"  # 10 bytes
    video_file = tmp_path / "test.mp4"
    video_file.write_bytes(content)

    row = {"film_id": "film_test", "path": str(video_file)}
    mock_db = _make_film_mock_db([row])

    with (
        patch("pipeline.api.main.load_config", return_value=config),
        patch("pipeline.api.main.open_db", return_value=mock_db),
    ):
        import pipeline.api.main as api_mod  # noqa: PLC0415
        with TestClient(api_mod.app) as client:
            response = client.get("/video/film_test", headers={"Range": "bytes=0-4"})

    assert response.status_code == 206
    assert response.headers.get("Content-Range") == "bytes 0-4/10"
    assert response.content == b"01234"


def test_api_video_film_not_in_db_returns_404(config: Config) -> None:
    """GET /video/{film_id} returns 404 when film is absent from DB."""
    from fastapi.testclient import TestClient

    mock_db = _make_film_mock_db([])

    with (
        patch("pipeline.api.main.load_config", return_value=config),
        patch("pipeline.api.main.open_db", return_value=mock_db),
    ):
        import pipeline.api.main as api_mod  # noqa: PLC0415
        with TestClient(api_mod.app, raise_server_exceptions=False) as client:
            response = client.get("/video/nonexistent_film")

    assert response.status_code == 404


def test_api_video_file_missing_on_disk_returns_404(tmp_path: Path, config: Config) -> None:
    """GET /video/{film_id} returns 404 when the video file doesn't exist on disk."""
    from fastapi.testclient import TestClient

    row = {"film_id": "film_test", "path": str(tmp_path / "missing.mp4")}
    mock_db = _make_film_mock_db([row])

    with (
        patch("pipeline.api.main.load_config", return_value=config),
        patch("pipeline.api.main.open_db", return_value=mock_db),
    ):
        import pipeline.api.main as api_mod  # noqa: PLC0415
        with TestClient(api_mod.app, raise_server_exceptions=False) as client:
            response = client.get("/video/film_test")

    assert response.status_code == 404
