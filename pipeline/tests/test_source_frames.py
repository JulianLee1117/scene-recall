"""Saved thumbnails follow source time without depending on the moment index."""

from io import BytesIO
from concurrent.futures import ThreadPoolExecutor
import threading
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient
from PIL import Image

from pipeline import source_frames


def test_thumbnail_cache_is_bounded_and_invalidated_by_source_fingerprint(tmp_path, monkeypatch):
    source = tmp_path / "film.mkv"
    source.write_bytes(b"original")
    decode = MagicMock(return_value=Image.new("RGB", (1920, 1080), "red"))
    monkeypatch.setattr(source_frames, "decode", decode)
    source_frames._thumbnail.cache_clear()

    first = source_frames.thumbnail(source, 1105.0004)
    assert source_frames.thumbnail(source, 1105.0001) == first
    decode.assert_called_once_with(source.resolve(), 1105.0, thread_count=2)
    assert Image.open(BytesIO(first)).size == (640, 360)
    source_frames.thumbnail(source, 1105.001)
    assert decode.call_count == 2
    source.write_bytes(b"replaced source")
    source_frames.thumbnail(source, 1105.0)
    assert decode.call_count == 3
    assert source_frames._thumbnail.cache_info().maxsize == 128
    assert list(tmp_path.iterdir()) == [source]  # no new derived files
    source_frames._thumbnail.cache_clear()


def test_source_frame_api_validates_time_and_needs_no_match_index(config, tmp_path):
    from pipeline.api import main

    source = tmp_path / "film.mkv"
    source.write_bytes(b"source")
    db = MagicMock()
    db.open_table.return_value.search.return_value.where.return_value.to_list.return_value = [{
        "film_id": "film-a", "path": str(source), "duration": 1200.0,
    }]
    with (
        patch.object(main, "load_config", return_value=config),
        patch.object(main, "open_db", return_value=db),
        patch.object(main, "ensure_search_indexes"),
        patch.object(source_frames, "thumbnail", return_value=b"jpeg") as thumbnail,
        TestClient(main.app) as client,
    ):
        response = client.get("/media/frame/film-a?t=1105.000")
        assert response.status_code == 200
        assert response.content == b"jpeg"
        assert response.headers["content-type"] == "image/jpeg"
        thumbnail.assert_called_once_with(source, 1105.0)
        thumbnail.reset_mock()
        for bad in ("-1", "1200.001", "nan", "inf"):
            assert client.get(f"/media/frame/film-a?t={bad}").status_code == 422
        thumbnail.assert_not_called()
        source.unlink()
        assert client.get("/media/frame/film-a?t=1105").status_code == 404


def test_source_thumbnail_decode_concurrency_is_bounded(tmp_path, monkeypatch):
    source = tmp_path / "film.mkv"
    source.write_bytes(b"source")
    two_started, release = threading.Event(), threading.Event()
    lock = threading.Lock()
    active = peak = 0

    def decode(_source, _time, *, thread_count):
        nonlocal active, peak
        assert thread_count == 2
        with lock:
            active += 1
            peak = max(peak, active)
            if active == 2:
                two_started.set()
        try:
            assert release.wait(timeout=5)
            return Image.new("RGB", (64, 36), "red")
        finally:
            with lock:
                active -= 1

    monkeypatch.setattr(source_frames, "decode", decode)
    source_frames._thumbnail.cache_clear()
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(source_frames.thumbnail, source, timestamp) for timestamp in range(4)]
        try:
            assert two_started.wait(timeout=5)
            assert peak == 2
        finally:
            release.set()
        assert all(future.result() for future in futures)
    assert peak == 2
    source_frames._thumbnail.cache_clear()
