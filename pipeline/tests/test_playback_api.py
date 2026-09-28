"""The player pins one byte representation across every range request."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from pipeline.config import Config


@pytest.fixture
def playback_client(tmp_path: Path, config: Config):
    from pipeline.api import main

    source = tmp_path / "source.mkv"
    source.write_bytes(b"original source bytes")
    prepared = tmp_path / "video-generation-one.mp4"
    prepared.write_bytes(b"0123456789prepared")
    db = MagicMock()
    db.open_table.return_value.search.return_value.where.return_value.to_list.return_value = [
        {"film_id": "film_test", "path": str(source)},
    ]
    with (
        patch.object(main, "load_config", return_value=config),
        patch.object(main, "open_db", return_value=db),
        patch.object(main, "ensure_search_indexes"),
        patch.object(main, "lookup_playback", return_value=prepared) as lookup,
        TestClient(main.app) as client,
    ):
        yield client, source, prepared, lookup


def test_resolver_pins_prepared_ranges_and_original_stays_original(playback_client):
    client, source, prepared, lookup = playback_client
    metadata = client.get("/video/film_test/playback")
    assert metadata.status_code == 200
    assert metadata.headers["cache-control"] == "no-store"
    url = metadata.json()["url"]
    assert url.startswith("/video/film_test?representation=")
    result = client.get(url, headers={"Range": "bytes=3-8"})
    assert result.status_code == 206
    assert result.content == prepared.read_bytes()[3:9]
    assert result.headers["content-range"] == f"bytes 3-8/{prepared.stat().st_size}"
    assert result.headers["content-type"] == "video/mp4"
    assert result.headers["etag"]
    assert client.get(url).content == prepared.read_bytes()
    lookup.reset_mock()
    assert client.get("/video/film_test").content == source.read_bytes()
    lookup.assert_not_called()


def test_missing_prepared_cache_resolves_original_without_work(playback_client):
    client, source, _, lookup = playback_client
    lookup.return_value = None
    metadata = client.get("/video/film_test/playback")
    assert metadata.json() == {"url": "/video/film_test"}
    # If preparation finishes after this resolution, the open original player
    # still receives original bytes on all of its subsequent range requests.
    lookup.return_value = source.parent / "new.mp4"
    response = client.get(metadata.json()["url"], headers={"Range": "bytes=0-7"})
    assert response.content == b"original"


def test_changed_generation_never_falls_back_or_mixes_bytes(playback_client):
    client, _, prepared, lookup = playback_client
    url = client.get("/video/film_test/playback").json()["url"]
    replacement = prepared.with_name("video-generation-two.mp4")
    replacement.write_bytes(b"different generation")
    lookup.return_value = replacement
    result = client.get(url, headers={"Range": "bytes=4-8"})
    assert result.status_code == 409
    assert "retry" in result.json()["detail"]
    new_url = client.get("/video/film_test/playback").json()["url"]
    assert new_url != url
    assert client.get(new_url).content == replacement.read_bytes()
    lookup.return_value = None
    assert client.get(new_url).status_code == 409


def test_invalid_token_and_missing_source_are_explicit(playback_client):
    client, source, _, _ = playback_client
    assert client.get("/video/film_test?representation=made-up").status_code == 409
    source.unlink()
    assert client.get("/video/film_test/playback").status_code == 404
