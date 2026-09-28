"""Acquisition HTTP boundaries reject unsafe or stale input before mutation."""

from __future__ import annotations

from unittest.mock import MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from pipeline.acquisition.api import MAX_JSON_BYTES, MAX_MULTIPART_BYTES, MAX_TORRENT_BYTES, router
from pipeline.acquisition.clients import ClientError
from pipeline.acquisition.store import AcquisitionConflict

MAGNET = "magnet:?xt=urn:btih:" + "a" * 40
FILM = {"title": "Mirror", "year": 1975, "edition": ""}
ITEM = {"id": "acq-a", "revision": 1, "title": "Mirror", "status": "queued"}


@pytest.fixture
def acquisition_api():
    service = MagicMock()
    service.status.return_value = {"downloader": {"configured": False, "available": False},
                                   "search": {"configured": False, "available": False},
                                   "monitor": {"running": False, "last_seen": None}}
    service.list.return_value = {"items": [ITEM]}
    service.search.return_value = {"results": [{"id": "release-a", "title": "Mirror 1975"}]}
    for name in ("add_magnet", "add_torrent", "add_release", "cancel", "retry", "review"):
        getattr(service, name).return_value = ITEM
    app = FastAPI()
    app.state.acquisition = service
    app.include_router(router)
    with TestClient(app) as client:
        yield client, service


def test_read_boundaries_do_not_need_a_downloader(acquisition_api):
    client, service = acquisition_api
    assert client.get("/acquisition/status").json() == service.status.return_value
    assert client.get("/acquisition").json() == {"items": [ITEM]}
    response = client.get("/acquisition/search", params={"q": "  Mirror 1975  "})
    assert response.json() == service.search.return_value
    service.search.assert_called_once_with("Mirror 1975")


def test_add_magnet_forwards_normalized_identity(acquisition_api):
    client, service = acquisition_api
    response = client.post("/acquisition/magnet", json={"magnet": MAGNET, "title": " Mirror ", "year": 1975})
    assert response.status_code == 200 and response.json() == {"item": ITEM}
    service.add_magnet.assert_called_once_with(magnet=MAGNET, **FILM)


@pytest.mark.parametrize("override", [
    {"magnet": "https://example.org/release.torrent"}, {"magnet": "x" * 16_385},
    {"title": "   "}, {"title": "x" * 181}, {"year": True}, {"year": "1975"},
    {"year": 1800}, {"year": 2101}, {"edition": "x" * 81}, {"save_path": "C:/films"},
], ids=["http-url", "large-magnet", "blank-title", "long-title", "bool-year", "text-year",
        "early-year", "late-year", "long-edition", "extra-field"])
def test_magnet_rejects_unbounded_or_extra_input(acquisition_api, override):
    client, service = acquisition_api
    response = client.post("/acquisition/magnet", json={"magnet": MAGNET, **FILM, **override})
    assert response.status_code == 422
    service.add_magnet.assert_not_called()


def test_validation_does_not_echo_magnet_credentials(acquisition_api):
    client, _ = acquisition_api
    response = client.post("/acquisition/magnet", json={"magnet": "https://tracker/?token=very-secret", **FILM})
    assert response.status_code == 422
    assert "very-secret" not in response.text and '"input"' not in response.text


def test_json_body_is_bounded_before_parsing(acquisition_api):
    client, service = acquisition_api
    response = client.post("/acquisition/magnet", content=b"x" * (MAX_JSON_BYTES + 1),
                           headers={"Content-Type": "application/json"})
    assert response.status_code == 413
    assert response.json() == {"detail": "Acquisition request is too large"}
    service.add_magnet.assert_not_called()


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity"])
def test_nonfinite_revision_rejection_remains_valid_json(acquisition_api, value):
    client, service = acquisition_api
    response = client.post("/acquisition/acq-a/cancel", content='{"revision":' + value + '}',
                           headers={"Content-Type": "application/json"})
    assert response.status_code == 422
    assert isinstance(response.json()["detail"], list)
    assert '"input"' not in response.text
    service.cancel.assert_not_called()


def test_search_rejects_empty_or_unbounded_queries(acquisition_api):
    client, service = acquisition_api
    for query in ("", "   ", "x" * 201):
        assert client.get("/acquisition/search", params={"q": query}).status_code == 422
    service.search.assert_not_called()


def test_release_queue_accepts_only_a_returned_identifier(acquisition_api):
    client, service = acquisition_api
    response = client.post("/acquisition/release", json={"release_id": "release-a", **FILM})
    assert response.status_code == 200 and response.json() == {"item": ITEM}
    service.add_release.assert_called_once_with(release_id="release-a", **FILM)
    for identity in ("https://host/release", "../release", "", "x" * 257):
        assert client.post("/acquisition/release", json={"release_id": identity, **FILM}).status_code == 422


def test_torrent_upload_is_bounded_and_passes_bytes(acquisition_api):
    client, service = acquisition_api
    response = client.post("/acquisition/torrent", data={"title": "Mirror", "year": "1975"},
                           files={"file": ("release.torrent", b"fixture-bencode", "application/x-bittorrent")})
    assert response.status_code == 200 and response.json() == {"item": ITEM}
    service.add_torrent.assert_called_once_with(b"fixture-bencode", "release.torrent", **FILM)


@pytest.mark.parametrize("filename,data,status", [
    ("release.torrent", b"", 422), ("release.exe", b"abc", 422),
    ("../release.torrent", b"abc", 422), ("C:release.torrent", b"abc", 422),
    ("release.torrent", b"a" * (MAX_TORRENT_BYTES + 1), 413),
    ("release.torrent", b"a" * (MAX_MULTIPART_BYTES + 1), 413),
], ids=["empty", "wrong-extension", "traversal", "drive-path", "large-file", "large-body"])
def test_torrent_rejects_invalid_upload_before_mutation(acquisition_api, filename, data, status):
    client, service = acquisition_api
    response = client.post("/acquisition/torrent", data={"title": "Mirror", "year": "1975"},
                           files={"file": (filename, data)})
    assert response.status_code == status, response.text
    service.add_torrent.assert_not_called()


@pytest.mark.parametrize("fields", [
    {"title": "   ", "year": "1975"},
    {"title": "Mirror", "year": "1975", "save_path": "C:/films"},
    {"title": ["Mirror", "Other"], "year": "1975"},
])
def test_torrent_rejects_invalid_or_duplicate_metadata(acquisition_api, fields):
    client, service = acquisition_api
    response = client.post("/acquisition/torrent", data=fields,
                           files={"file": ("release.torrent", b"abc")})
    assert response.status_code == 422, response.text
    service.add_torrent.assert_not_called()


@pytest.mark.parametrize("action", ["cancel", "retry"])
def test_actions_require_current_revision(acquisition_api, action):
    client, service = acquisition_api
    response = client.post(f"/acquisition/acq-a/{action}", json={"revision": 1})
    assert response.status_code == 200 and response.json() == {"item": ITEM}
    getattr(service, action).assert_called_once_with("acq-a", 1)
    for payload in ({}, {"revision": 0}, {"revision": True}, {"revision": "1"}, {"revision": 1, "delete_files": True}):
        assert client.post(f"/acquisition/acq-a/{action}", json=payload).status_code == 422


def test_dismiss_forwards_identity_and_revision_without_echoing_an_item(acquisition_api):
    client, service = acquisition_api
    response = client.post("/acquisition/acq-a/dismiss", json={"revision": 1})
    assert response.status_code == 200 and response.json() == {"ok": True}
    service.dismiss.assert_called_once_with("acq-a", 1)
    for payload in ({}, {"revision": 0}, {"revision": True}, {"revision": "1"}, {"revision": 1, "delete_files": True}):
        assert client.post("/acquisition/acq-a/dismiss", json=payload).status_code == 422


def test_review_passes_explicit_selection_and_normalizes_relative_paths(acquisition_api):
    client, service = acquisition_api
    response = client.post("/acquisition/acq-a/review", json={
        "revision": 1, "video_path": "release\\film.mkv",
        "subtitle_decision": {"action": "use", "relative_path": "release\\film.en.srt"},
    })
    assert response.status_code == 200
    service.review.assert_called_once_with("acq-a", 1, "release/film.mkv",
                                           {"action": "use", "relative_path": "release/film.en.srt"})


@pytest.mark.parametrize("path", ["../film.mkv", "/film.mkv", "C:/film.mkv", "\\\\host\\film.mkv",
                                  "release/../film.mkv", "release//film.mkv", "release/film.mkv:stream"])
def test_review_rejects_filesystem_paths_outside_acquisition(acquisition_api, path):
    client, service = acquisition_api
    for payload in (
        {"revision": 1, "video_path": path, "subtitle_decision": {"action": "skip"}},
        {"revision": 1, "video_path": "film.mkv", "subtitle_decision": {"action": "use", "relative_path": path}},
    ):
        assert client.post("/acquisition/acq-a/review", json=payload).status_code == 422
    service.review.assert_not_called()


def test_review_requires_deliberate_subtitle_choice(acquisition_api):
    client, service = acquisition_api
    for decision in ({"action": "use"}, {"action": "automatic"}, {"action": "skip", "relative_path": "film.srt"}):
        response = client.post("/acquisition/acq-a/review", json={
            "revision": 1, "video_path": "film.mkv", "subtitle_decision": decision,
        })
        assert response.status_code == 422
    service.review.assert_not_called()


def test_review_can_select_video_before_subtitle_discovery(acquisition_api):
    client, service = acquisition_api
    response = client.post("/acquisition/acq-a/review", json={
        "revision": 1, "video_path": "film.mkv", "subtitle_decision": None,
    })
    assert response.status_code == 200
    service.review.assert_called_once_with("acq-a", 1, "film.mkv", None)


@pytest.mark.parametrize("error,status,detail", [
    (ValueError("Select an available video"), 422, "Select an available video"),
    (KeyError("private path"), 404, "Acquisition or release not found"),
    (AcquisitionConflict("Revision changed; reload"), 409, "Revision changed; reload"),
    (ClientError("https://user:secret@host/?apikey=secret"), 503,
     "Acquisition provider is unavailable; check its configuration and connection"),
])
def test_domain_failures_have_safe_http_statuses(acquisition_api, error, status, detail):
    client, service = acquisition_api
    service.cancel.side_effect = error
    response = client.post("/acquisition/acq-a/cancel", json={"revision": 1})
    assert response.status_code == status
    assert response.json() == {"detail": detail}
    assert "secret" not in response.text and "private path" not in response.text


def test_uninitialized_acquisition_service_reports_unavailable():
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        assert client.get("/acquisition/status").status_code == 503


def test_real_service_route_shapes_and_disabled_integrations(config):
    from pipeline.acquisition.service import AcquisitionService
    from pipeline.acquisition.settings import AcquisitionSettings

    service = AcquisitionService(config, settings=AcquisitionSettings())
    item = service.store.create("a" * 40, title="Mirror", year=1975, edition="",
                                source={"kind": "magnet", "magnet": MAGNET})
    app = FastAPI()
    app.state.acquisition = service
    app.include_router(router)
    with TestClient(app) as client:
        response = client.get("/acquisition")
        assert response.status_code == 200
        assert isinstance(response.json()["items"], list)
        assert response.json()["items"][0]["id"] == item["id"]
        assert "magnet" not in response.text
        response = client.get("/acquisition/status")
        assert response.json()["downloader"]["configured"] is False
        assert response.json()["search"]["configured"] is False
        response = client.post("/acquisition/magnet", json={"magnet": MAGNET, **FILM})
        assert response.status_code == 503


@pytest.mark.parametrize("origin", ["https://untrusted.example", "null", "http://localhost:3000.evil.example"])
def test_cross_origin_browser_cannot_enqueue_or_search(acquisition_api, origin):
    client, service = acquisition_api
    headers = {"Origin": origin}
    assert client.post("/acquisition/magnet", json={"magnet": MAGNET, **FILM}, headers=headers).status_code == 403
    assert client.get("/acquisition/search", params={"q": "Mirror"}, headers=headers).status_code == 403
    assert client.post("/acquisition/acq-a/cancel", json={"revision": 1}, headers=headers).status_code == 403
    assert client.post("/acquisition/torrent", data={"title": "Mirror", "year": "1975"},
                       files={"file": ("film.torrent", b"fixture")}, headers=headers).status_code == 403
    service.add_magnet.assert_not_called()
    service.add_torrent.assert_not_called()
    service.cancel.assert_not_called()
    service.search.assert_not_called()


@pytest.mark.parametrize("origin", ["http://localhost:3000", "http://127.0.0.1:3000"])
def test_configured_local_app_origin_can_mutate(acquisition_api, origin, monkeypatch):
    monkeypatch.delenv("SCENE_RECALL_ALLOWED_ORIGINS", raising=False)
    client, service = acquisition_api
    response = client.post("/acquisition/magnet", json={"magnet": MAGNET, **FILM},
                           headers={"Origin": origin, "Sec-Fetch-Site": "cross-site"})
    assert response.status_code == 200
    service.add_magnet.assert_called_once()


def test_explicit_proxy_origin_replaces_default_browser_origins(acquisition_api, monkeypatch):
    monkeypatch.setenv("SCENE_RECALL_ALLOWED_ORIGINS", " https://films.example ")
    client, service = acquisition_api
    response = client.post("/acquisition/magnet", json={"magnet": MAGNET, **FILM},
                           headers={"Origin": "https://films.example"})
    assert response.status_code == 200
    assert client.post("/acquisition/magnet", json={"magnet": MAGNET, **FILM},
                       headers={"Origin": "http://localhost:3000"}).status_code == 403
    service.add_magnet.assert_called_once()


def test_own_loopback_origin_is_allowed_but_host_header_is_not_authorization(acquisition_api):
    client, service = acquisition_api
    assert client.post("http://127.0.0.1:8000/acquisition/magnet", json={"magnet": MAGNET, **FILM},
                       headers={"Origin": "http://127.0.0.1:8000"}).status_code == 200
    assert client.post("http://untrusted.example/acquisition/magnet", json={"magnet": MAGNET, **FILM},
                       headers={"Origin": "http://untrusted.example"}).status_code == 403
    service.add_magnet.assert_called_once()


@pytest.mark.parametrize("fetch_site", ["cross-site", "same-site"])
def test_browser_search_without_origin_needs_same_origin_fetch_context(acquisition_api, fetch_site):
    client, service = acquisition_api
    assert client.get("/acquisition/search", params={"q": "Mirror"},
                      headers={"Sec-Fetch-Site": fetch_site}).status_code == 403
    service.search.assert_not_called()
