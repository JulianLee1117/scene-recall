"""The CLI sends bounded requests to the shared HTTP acquisition boundary."""

from __future__ import annotations

from io import BytesIO
import json
from urllib.error import HTTPError, URLError

from click.testing import CliRunner
import pytest

from pipeline.acquisition import cli as module


@pytest.fixture
def api_calls(monkeypatch):
    calls = []

    def request(request, timeout):
        calls.append(request)
        assert timeout == 30
        return BytesIO(b'{"item":{"id":"acq-a","revision":2}}')

    monkeypatch.setattr(module, "urlopen", request)
    return calls


@pytest.mark.parametrize("arguments,path,method,payload", [
    (["status"], "/status", "GET", None),
    (["list"], "", "GET", None),
    (["search", "Mirror & 1975"], "/search?q=Mirror+%26+1975", "GET", None),
    (["add-magnet", "magnet:?xt=urn:btih:abc", "--title", "Mirror", "--year", "1975"],
     "/magnet", "POST", {"magnet": "magnet:?xt=urn:btih:abc", "title": "Mirror", "year": 1975, "edition": ""}),
    (["add-release", "release-a", "--title", "Mirror", "--year", "1975", "--edition", "Restored"],
     "/release", "POST", {"release_id": "release-a", "title": "Mirror", "year": 1975, "edition": "Restored"}),
    (["cancel", "acq-a", "--revision", "2"], "/acq-a/cancel", "POST", {"revision": 2}),
    (["retry", "acq-a", "--revision", "2"], "/acq-a/retry", "POST", {"revision": 2}),
    (["review", "acq-a", "--revision", "2", "--video", "release/film.mkv", "--skip-subtitles"],
     "/acq-a/review", "POST", {"revision": 2, "video_path": "release/film.mkv", "subtitle_decision": {"action": "skip"}}),
    (["review", "acq-a", "--revision", "2", "--video", "film.mkv", "--subtitle", "film.srt"],
     "/acq-a/review", "POST", {"revision": 2, "video_path": "film.mkv",
                               "subtitle_decision": {"action": "use", "relative_path": "film.srt"}}),
    (["review", "acq-a", "--revision", "2", "--video", "film.mkv"],
     "/acq-a/review", "POST", {"revision": 2, "video_path": "film.mkv", "subtitle_decision": None}),
])
def test_commands_use_the_shared_api(api_calls, arguments, path, method, payload):
    result = CliRunner().invoke(module.acquisition, arguments)
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["item"]["revision"] == 2
    assert len(api_calls) == 1
    request = api_calls[0]
    assert request.full_url == "http://127.0.0.1:8000/acquisition" + path
    assert request.method == method
    assert (json.loads(request.data) if request.data else None) == payload


def test_torrent_is_a_bounded_local_upload(api_calls, tmp_path):
    path = tmp_path / "fixture.torrent"
    path.write_bytes(b"fixture-bencode")
    result = CliRunner().invoke(module.acquisition, ["add-torrent", str(path), "--title", "Mirror", "--year", "1975"])
    assert result.exit_code == 0, result.output
    request = api_calls[0]
    assert request.full_url.endswith("/acquisition/torrent")
    assert request.headers["Content-type"].startswith("multipart/form-data; boundary=")
    assert b'filename="release.torrent"' in request.data and b"fixture-bencode" in request.data
    assert b'name="title"\r\n\r\nMirror\r\n' in request.data
    assert b'name="year"\r\n\r\n1975\r\n' in request.data


@pytest.mark.parametrize("name,data", [("film.torrent", b""), ("film.exe", b"abc"),
                                        ("film.torrent", b"a" * (module.MAX_TORRENT_BYTES + 1))],
                         ids=["empty", "wrong-extension", "large-file"])
def test_bad_torrent_does_not_contact_api(api_calls, tmp_path, name, data):
    path = tmp_path / name
    path.write_bytes(data)
    result = CliRunner().invoke(module.acquisition, ["add-torrent", str(path), "--title", "Mirror", "--year", "1975"])
    assert result.exit_code == 1
    assert not api_calls


def test_review_rejects_conflicting_subtitle_decisions(api_calls):
    result = CliRunner().invoke(module.acquisition, ["review", "acq-a", "--revision", "1", "--video", "film.mkv",
                                                     "--subtitle", "film.srt", "--skip-subtitles"])
    assert result.exit_code == 2
    assert "Choose only one" in result.output and not api_calls


def test_mutation_requires_revision(api_calls):
    result = CliRunner().invoke(module.acquisition, ["cancel", "acq-a"])
    assert result.exit_code == 2 and "--revision" in result.output
    assert not api_calls


def test_api_url_is_configurable_and_credential_free(api_calls):
    result = CliRunner().invoke(module.acquisition, ["--api-url", "http://localhost:9000/", "status"])
    assert result.exit_code == 0
    assert api_calls[0].full_url == "http://localhost:9000/acquisition/status"
    for address in ("file:///local/file", "http://user:secret@localhost", "http://localhost?token=secret", "localhost:8000",
                    "http://[invalid", "http://localhost:invalid", "http://local host", "http://localhost\\host"):
        result = CliRunner().invoke(module.acquisition, ["--api-url", address, "status"])
        assert result.exit_code == 2
        assert "secret" not in result.output


@pytest.mark.parametrize("payload,expected", [
    (b'{"detail":"Revision changed; reload"}', "Revision changed; reload"),
    (b'{"detail":[{"loc":["year"],"msg":"Input should be an integer"}]}', "Input should be an integer"),
    (b"<html>private upstream failure</html>", "HTTP 503"),
])
def test_api_errors_are_readable_without_dumping_response(monkeypatch, payload, expected):
    def unavailable(*args, **kwargs):
        raise HTTPError("http://127.0.0.1:8000/acquisition", 503, "failure", {}, BytesIO(payload))

    monkeypatch.setattr(module, "urlopen", unavailable)
    result = CliRunner().invoke(module.acquisition, ["status"])
    assert result.exit_code == 1
    assert expected in result.output and "<html>" not in result.output


def test_connection_failure_explains_local_api_requirement(monkeypatch):
    def unavailable(*args, **kwargs):
        raise URLError("private host detail")

    monkeypatch.setattr(module, "urlopen", unavailable)
    result = CliRunner().invoke(module.acquisition, ["status"])
    assert result.exit_code == 1
    assert "start it or check --api-url" in result.output
    assert "private host detail" not in result.output


def test_invalid_api_json_is_reported(monkeypatch):
    monkeypatch.setattr(module, "urlopen", lambda *args, **kwargs: BytesIO(b"not json"))
    result = CliRunner().invoke(module.acquisition, ["status"])
    assert result.exit_code == 1 and "invalid JSON" in result.output
