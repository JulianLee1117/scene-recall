from email.parser import BytesParser
from email.policy import default
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
from urllib.parse import parse_qs, urlsplit

import pytest

from pipeline.acquisition.clients import ClientError, ProwlarrClient, QBittorrentClient
from pipeline.acquisition.settings import AcquisitionSettings
from pipeline.acquisition.sources import SourceError, parse_torrent
from pipeline.tests.test_acquisition_sources import bencode, torrent_info


HASH = "a" * 40
MAGNET = "magnet:?xt=urn:btih:" + HASH
TAG = "scene-recall-test123"


@pytest.fixture
def service():
    state = {"requests": [], "overrides": {}, "row": None, "login_count": 0}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.handle_api()

        def do_POST(self):
            self.handle_api()

        def handle_api(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            path = urlsplit(self.path).path
            state["requests"].append((self.command, self.path, dict(self.headers), body))
            override = state["overrides"].get(path)
            if override is not None:
                status, response, headers = override(self) if callable(override) else override
            elif path == "/api/v2/auth/login":
                state["login_count"] += 1
                status, response, headers = 200, b"Ok.", {"Set-Cookie": "SID=local-test; Path=/"}
            elif path.startswith("/api/v2/") and self.headers.get("Cookie") != "SID=local-test":
                status, response, headers = 403, b"auth denied", {}
            elif path == "/api/v2/app/version":
                status, response, headers = 200, b"v5.0.5", {}
            elif path == "/api/v2/app/preferences":
                status, response, headers = 200, b'{"autorun_enabled": false, "max_ratio_act": 3}', {}
            elif path == "/api/v2/torrents/info":
                status, response, headers = 200, json.dumps([state["row"]] if state["row"] else []).encode(), {}
            elif path == "/api/v2/torrents/categories":
                status, response, headers = 200, b'{"scene-recall": {}}', {}
            elif path == "/api/v2/torrents/createCategory":
                status, response, headers = 200, b"", {}
            elif path == "/api/v2/torrents/add":
                message = BytesParser(policy=default).parsebytes(b"Content-Type: " + self.headers["Content-Type"].encode() + b"\r\n\r\n" + body)
                parts = {part.get_param("name", header="content-disposition"): part.get_payload(decode=True) for part in message.iter_parts()}
                state["parts"] = parts
                added_hash = parse_torrent(parts["torrents"]).info_hash if "torrents" in parts else HASH
                state["row"] = {"hash": added_hash, "category": parts["category"].decode(), "tags": parts["tags"].decode(), "save_path": parts["savepath"].decode()}
                status, response, headers = 200, b"Ok.", {}
            elif path in {"/api/v2/torrents/stop", "/api/v2/torrents/start", "/api/v2/torrents/setShareLimits"}:
                status, response, headers = 200, b"", {}
            elif path == "/api/v2/torrents/delete":
                state["row"] = None
                status, response, headers = 200, b"", {}
            else:
                status, response, headers = 404, b"not found", {}
            self.send_response(status)
            for key, value in headers.items():
                self.send_header(key, value)
            if "Content-Length" not in headers:
                self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state["url"] = f"http://127.0.0.1:{server.server_port}"
    yield state
    server.shutdown()
    server.server_close()
    thread.join(timeout=3)


def qbit(service):
    return QBittorrentClient(AcquisitionSettings(qbittorrent_url=service["url"], qbittorrent_username="user", qbittorrent_password="secret"))


def prowlarr(service):
    return ProwlarrClient(AcquisitionSettings(prowlarr_url=service["url"], prowlarr_api_key="server-secret"))


def test_cookie_auth_add_stops_on_completion_without_inheriting_removal(service, tmp_path):
    client = qbit(service)
    assert client.add_magnet(MAGNET, str(tmp_path), TAG) == HASH
    assert client.info(HASH)["tags"] == TAG
    fields = service["parts"]
    assert fields["autoTMM"] == b"false"
    assert fields["contentLayout"] == b"Original"
    assert fields["skip_checking"] == b"false"
    assert fields["shareLimitAction"] == b"Stop"
    assert fields["ratioLimit"] == fields["seedingTimeLimit"] == b"0"
    assert fields["inactiveSeedingTimeLimit"] == b"-1"
    assert fields["downloadPath"] == fields["savepath"]
    assert fields["useDownloadPath"] == b"false"
    assert service["login_count"] == 1
    assert all(headers.get("Referer") == service["url"] + "/" for _, _, headers, _ in service["requests"])


def test_duplicate_add_reconciles_owned_hash_without_second_add(service, tmp_path):
    client = qbit(service)
    client.add_magnet(MAGNET, str(tmp_path), TAG)
    client.add_magnet(MAGNET, str(tmp_path), TAG)
    assert sum(urlsplit(path).path.endswith("/add") for _, path, _, _ in service["requests"]) == 1
    limits = [parse_qs(body.decode()) for _, path, _, body in service["requests"] if urlsplit(path).path.endswith("/setShareLimits")]
    assert limits == [{"hashes": [HASH], "ratioLimit": ["0"], "seedingTimeLimit": ["0"],
                       "inactiveSeedingTimeLimit": ["-1"], "shareLimitAction": ["Stop"]}]


def test_resume_restores_completion_stop_before_starting(service, tmp_path):
    client = qbit(service)
    client.add_magnet(MAGNET, str(tmp_path), TAG)
    client.start(HASH, tag=TAG)
    mutations = [(urlsplit(path).path, parse_qs(body.decode())) for method, path, _, body in service["requests"]
                 if method == "POST" and urlsplit(path).path.endswith(("/setShareLimits", "/start"))]
    assert mutations == [
        ("/api/v2/torrents/setShareLimits", {"hashes": [HASH], "ratioLimit": ["0"], "seedingTimeLimit": ["0"],
                                           "inactiveSeedingTimeLimit": ["-1"], "shareLimitAction": ["Stop"]}),
        ("/api/v2/torrents/start", {"hashes": [HASH]}),
    ]


def test_resume_cannot_start_when_completion_stop_cannot_be_set(service):
    client = qbit(service)
    service["row"] = {"hash": HASH, "category": "scene-recall", "tags": TAG}
    service["overrides"]["/api/v2/torrents/setShareLimits"] = (503, b"unavailable", {})
    with pytest.raises(ClientError):
        client.start(HASH, tag=TAG)
    assert not any(urlsplit(path).path.endswith("/start") for _, path, _, _ in service["requests"])


def test_creates_missing_category_before_first_add(service, tmp_path):
    service["overrides"]["/api/v2/torrents/categories"] = (200, b"{}", {})
    qbit(service).add_magnet(MAGNET, str(tmp_path), TAG)
    paths = [urlsplit(path).path for _, path, _, _ in service["requests"]]
    assert paths.index("/api/v2/torrents/createCategory") < paths.index("/api/v2/torrents/add")


def test_status_checks_connection_again_after_version_was_cached(service):
    client = qbit(service)
    assert client.status()["connected"]
    service["overrides"]["/api/v2/app/version"] = (503, b"unavailable", {})
    with pytest.raises(ClientError):
        client.status()


@pytest.mark.parametrize("changes", [{"category": "personal"}, {"tags": "scene-recall-someone-else"}, {"save_path": "C:/elsewhere"}])
def test_unrelated_existing_torrent_is_never_adopted(service, tmp_path, changes):
    service["row"] = {"hash": HASH, "category": "scene-recall", "tags": TAG, "save_path": str(tmp_path), **changes}
    with pytest.raises(ClientError):
        qbit(service).add_magnet(MAGNET, str(tmp_path), TAG)
    assert not any(urlsplit(path).path.endswith(("/add", "/setShareLimits")) for _, path, _, _ in service["requests"])


def test_uploaded_torrent_uses_multipart_and_parsed_identity(service, tmp_path):
    payload = bencode({b"info": torrent_info()})
    assert qbit(service).add_torrent(payload, str(tmp_path), TAG) == parse_torrent(payload).info_hash
    assert service["parts"]["torrents"] == payload


def _submit_with_callback(client, kind, save_path, callback):
    if kind == "magnet":
        return client.add_magnet(MAGNET, str(save_path), TAG, before_submit=callback)
    return client.add_torrent(bencode({b"info": torrent_info()}), str(save_path), TAG,
                              before_submit=callback)


@pytest.mark.parametrize("kind", ["magnet", "torrent"])
def test_submission_callback_runs_once_after_preflight_and_before_add(service, tmp_path, kind):
    client = qbit(service)
    service["overrides"]["/api/v2/torrents/categories"] = (200, b"{}", {})
    snapshots = []

    def journal():
        snapshots.append([urlsplit(path).path for _, path, _, _ in service["requests"]])

    _submit_with_callback(client, kind, tmp_path, journal)
    assert len(snapshots) == 1
    assert snapshots[0][-1] == "/api/v2/torrents/createCategory"
    assert "/api/v2/torrents/add" not in snapshots[0]
    assert urlsplit(service["requests"][-1][1]).path == "/api/v2/torrents/add"
    # An already-owned duplicate is reconciled without claiming another add.
    _submit_with_callback(client, kind, tmp_path, journal)
    assert len(snapshots) == 1
    assert sum(urlsplit(path).path == "/api/v2/torrents/add"
               for _, path, _, _ in service["requests"]) == 1


@pytest.mark.parametrize("path,response", [
    ("/api/v2/auth/login", (403, b"denied", {})),
    ("/api/v2/app/version", (200, b"v4.6.0", {})),
    ("/api/v2/torrents/info", (503, b"unavailable", {})),
    ("/api/v2/app/preferences", (200, b'{"autorun_enabled": true}', {})),
    ("/api/v2/app/preferences", (200, b"[]", {})),
    ("/api/v2/torrents/categories", (200, b"[]", {})),
    ("/api/v2/torrents/createCategory", (503, b"unavailable", {})),
])
def test_preflight_failure_does_not_journal_an_add_attempt(service, tmp_path, path, response):
    if path.endswith("createCategory"):
        service["overrides"]["/api/v2/torrents/categories"] = (200, b"{}", {})
    service["overrides"][path] = response
    journal = []
    with pytest.raises(ClientError):
        qbit(service).add_magnet(MAGNET, str(tmp_path), TAG, before_submit=lambda: journal.append(True))
    assert journal == []
    assert not any(urlsplit(p).path == "/api/v2/torrents/add" for _, p, _, _ in service["requests"])


@pytest.mark.parametrize("kind", ["magnet", "torrent", "configuration", "ownership", "path"])
def test_local_validation_failure_does_not_run_submission_callback(service, tmp_path, kind):
    client = qbit(service)
    journal = []
    callback = lambda: journal.append(True)
    with pytest.raises((SourceError, ClientError)):
        if kind == "magnet":
            client.add_magnet("invalid", str(tmp_path), TAG, before_submit=callback)
        elif kind == "torrent":
            client.add_torrent(b"invalid", str(tmp_path), TAG, before_submit=callback)
        elif kind == "configuration":
            QBittorrentClient(AcquisitionSettings()).add_magnet(MAGNET, str(tmp_path), TAG,
                                                               before_submit=callback)
        elif kind == "ownership":
            client.add_magnet(MAGNET, str(tmp_path), "invalid", before_submit=callback)
        else:
            client.add_magnet(MAGNET, "relative/path", TAG, before_submit=callback)
    assert journal == []
    assert service["requests"] == []


def test_payload_preparation_failure_precedes_submission_callback(service, tmp_path, monkeypatch):
    def failed_boundary(*_):
        raise RuntimeError("local payload preparation failed")

    monkeypatch.setattr("pipeline.acquisition.clients.secrets.token_hex", failed_boundary)
    journal = []
    with pytest.raises(RuntimeError, match="payload preparation"):
        qbit(service).add_magnet(MAGNET, str(tmp_path), TAG, before_submit=lambda: journal.append(True))
    assert journal == []
    assert not any(urlsplit(p).path == "/api/v2/torrents/add" for _, p, _, _ in service["requests"])


@pytest.mark.parametrize("kind", ["magnet", "torrent"])
def test_submission_callback_failure_prevents_add(service, tmp_path, kind):
    calls = []

    def failed_journal():
        calls.append(True)
        raise RuntimeError("could not persist submission intent")

    with pytest.raises(RuntimeError, match="persist submission"):
        _submit_with_callback(qbit(service), kind, tmp_path, failed_journal)
    assert calls == [True]
    assert service["row"] is None
    assert not any(urlsplit(p).path == "/api/v2/torrents/add" for _, p, _, _ in service["requests"])


def test_transport_failure_after_callback_retains_uncertain_attempt_without_retry(service, tmp_path, monkeypatch):
    client = qbit(service)
    request = client._transport.request
    events = []

    def fail_add(path, **kwargs):
        if path == "/api/v2/torrents/add":
            events.append("add request")
            raise ClientError("qBittorrent is unreachable or the request timed out.")
        return request(path, **kwargs)

    monkeypatch.setattr(client._transport, "request", fail_add)
    with pytest.raises(ClientError, match="timed out"):
        client.add_magnet(MAGNET, str(tmp_path), TAG, before_submit=lambda: events.append("journal attempt"))
    assert events == ["journal attempt", "add request"]


def test_stop_and_remove_only_owned_single_hash_without_deleting_files(service, tmp_path):
    client = qbit(service)
    client.add_magnet(MAGNET, str(tmp_path), TAG)
    client.stop(HASH, tag=TAG)
    client.start(HASH, tag=TAG)
    client.remove(HASH, tag=TAG)
    delete = next(body for _, path, _, body in service["requests"] if urlsplit(path).path.endswith("/delete"))
    assert parse_qs(delete.decode()) == {"hashes": [HASH], "deleteFiles": ["false"]}
    with pytest.raises(ClientError, match="disabled"):
        client.remove(HASH, delete_files=True)
    with pytest.raises(SourceError):
        client.stop("all")


def test_cannot_stop_or_remove_unrelated_or_other_acquisition(service):
    client = qbit(service)
    service["row"] = {"hash": HASH, "category": "scene-recall", "tags": "scene-recall-other"}
    with pytest.raises(ClientError):
        client.stop(HASH, tag=TAG)
    with pytest.raises(ClientError):
        client.remove(HASH, tag=TAG)
    with pytest.raises(ClientError):
        client.start(HASH, tag=TAG)
    assert not any(urlsplit(path).path.endswith(("/stop", "/start", "/delete", "/setShareLimits")) for _, path, _, _ in service["requests"])


def test_external_program_hooks_prevent_add_without_changing_global_settings(service, tmp_path):
    service["overrides"]["/api/v2/app/preferences"] = (200, b'{"autorun_enabled": true}', {})
    with pytest.raises(ClientError, match="external-program"):
        qbit(service).add_magnet(MAGNET, str(tmp_path), TAG)
    assert not any(urlsplit(path).path.endswith(("/add", "/setPreferences")) for _, path, _, _ in service["requests"])


def test_file_metadata_rejects_traversal(service):
    service["overrides"]["/api/v2/torrents/files"] = (200, b'[{"name":"../escape.mkv"}]', {})
    with pytest.raises(SourceError, match="unsafe"):
        qbit(service).files(HASH)


def test_reauthenticates_expired_session_once(service):
    client = qbit(service)
    assert client.info(HASH) is None
    calls = [0]

    def expire_once(_handler):
        calls[0] += 1
        return (403, b"denied", {}) if calls[0] == 1 else (200, b"[]", {})

    service["overrides"]["/api/v2/torrents/info"] = expire_once
    assert client.info(HASH) is None
    assert service["login_count"] == 2
    assert calls[0] == 2


def test_qbit_52_empty_successful_login_body_uses_cookie(service):
    service["overrides"]["/api/v2/auth/login"] = (200, b"", {"Set-Cookie": "SID=local-test; Path=/"})
    assert qbit(service).info(HASH) is None


def test_empty_login_body_without_cookie_cannot_authenticate(service):
    service["overrides"]["/api/v2/auth/login"] = (200, b"", {})
    with pytest.raises(ClientError, match="authentication failed"):
        qbit(service).info(HASH)


@pytest.mark.parametrize("response", [(403, b"secret tracker", {}), (500, b"secret tracker", {}), (200, b"secret tracker", {})])
def test_errors_never_echo_response_bodies(service, response):
    service["overrides"]["/api/v2/torrents/info"] = response
    with pytest.raises(ClientError) as exc:
        qbit(service).info(HASH)
    assert "secret" not in str(exc.value)
    assert "tracker" not in str(exc.value)


def test_oversized_responses_and_login_redirects_are_rejected(service):
    service["overrides"]["/api/v2/torrents/info"] = (200, b"", {"Content-Length": "9000000"})
    with pytest.raises(ClientError, match="oversized"):
        qbit(service).info(HASH)
    service["overrides"]["/api/v2/auth/login"] = (302, b"", {"Location": "http://other.invalid/steal"})
    with pytest.raises(ClientError, match="HTTP 302"):
        qbit(service).info(HASH)


def test_prowlarr_search_bounds_results_and_keeps_proxy_details_private(service):
    rows = [{"title": "Film", "size": 123, "seeders": 3, "indexer": "Example", "protocol": "torrent", "downloadUrl": service["url"] + "/1/download?apikey=old-key&link=private-link"}] * 4
    service["overrides"]["/api/v1/search"] = (200, json.dumps(rows).encode(), {})
    result = prowlarr(service).search("Film", limit=2)
    assert len(result) == 2
    assert result[0]["id"] != result[1]["id"]
    assert result[0]["seeders"] == 3
    assert result[0]["_download_url"].endswith("private-link")
    _, path, headers, _ = service["requests"][-1]
    assert parse_qs(urlsplit(path).query)["limit"] == ["2"]
    assert headers["X-Api-Key"] == "server-secret"
    assert "server-secret" not in path
    assert len(service["requests"]) == 1, "A successful search must not trigger another indexer request"


@pytest.mark.parametrize("query", ["Moonlight", "aftersun", "In the Mood for Love"])
def test_empty_title_search_retries_once_as_phrase_in_same_movie_category(service, query):
    def respond(request):
        params = parse_qs(urlsplit(request.path).query)
        rows = [] if params["query"] == [query] else [
            {"title": query + " 2020 1080p", "protocol": "torrent", "magnetUrl": MAGNET},
            {"title": "Ignored usenet", "protocol": "usenet", "downloadUrl": "https://example.invalid/private"},
        ]
        return 200, json.dumps(rows).encode(), {}
    service["overrides"]["/api/v1/search"] = respond
    results = prowlarr(service).search(query)
    assert [row["title"] for row in results] == [query + " 2020 1080p"]
    params = [parse_qs(urlsplit(request[1]).query) for request in service["requests"]]
    assert [row["query"] for row in params] == [[query], ['"' + query + '"']]
    assert all(row["categories"] == ["2000"] and row["limit"] == ["30"] for row in params)


@pytest.mark.parametrize("query,requests", [("Nothing found", 2), ('"Already quoted"', 1), ("a" * 200, 1)])
def test_empty_search_fallback_is_bounded_and_does_not_nest_quotes(service, query, requests):
    service["overrides"]["/api/v1/search"] = (200, b"[]", {})
    assert prowlarr(service).search(query) == []
    assert len(service["requests"]) == requests


@pytest.mark.parametrize("status,payload", [(401, b"secret"), (503, b"secret"), (200, b"{\"unexpected\":true}")])
def test_search_does_not_retry_provider_failure_as_an_empty_result(service, status, payload):
    service["overrides"]["/api/v1/search"] = (status, payload, {})
    with pytest.raises(ClientError) as error:
        prowlarr(service).search("Moonlight")
    assert "secret" not in str(error.value)
    assert len(service["requests"]) == 1


def test_prowlarr_proxy_fetch_uses_server_auth_and_returns_torrent(service):
    payload = bencode({b"info": torrent_info()})
    service["overrides"]["/2/download"] = (200, payload, {})
    source = prowlarr(service).fetch_release({"_download_url": service["url"] + "/2/download?apikey=old-key&link=encrypted"})
    assert source.torrent_bytes == payload
    _, path, headers, _ = service["requests"][-1]
    assert "old-key" not in path
    assert headers["X-Api-Key"] == "server-secret"


def test_prowlarr_proxy_magnet_redirect_is_parsed_without_following(service):
    service["overrides"]["/1/download"] = (302, b"", {"Location": MAGNET})
    source = prowlarr(service).fetch_release({"_magnet_url": service["url"] + "/1/download?link=encrypted"})
    assert source.info_hash == HASH
    assert len(service["requests"]) == 1


@pytest.mark.parametrize("target", ["http://other.invalid/1/download", "{base}/api/v1/system/shutdown", "{base}/1/../download", "{base}/1%2fdownload", "file:///tmp/data", "{base}/1/download#fragment"])
def test_prowlarr_cannot_fetch_other_origins_or_arbitrary_api_paths(service, target):
    with pytest.raises(ClientError, match="configured Prowlarr"):
        prowlarr(service).fetch_release({"_download_url": target.format(base=service["url"])})
    assert service["requests"] == []


def test_prowlarr_http_redirect_cannot_escape_proxy(service):
    service["overrides"]["/1/download"] = (302, b"", {"Location": "http://other.invalid/secret"})
    with pytest.raises(ClientError, match="unsupported release redirect"):
        prowlarr(service).fetch_release({"_download_url": service["url"] + "/1/download"})
    assert len(service["requests"]) == 1


def test_integrations_disabled_without_configuration():
    with pytest.raises(ClientError, match="not configured"):
        QBittorrentClient(AcquisitionSettings()).info(HASH)
    with pytest.raises(ClientError, match="not configured"):
        ProwlarrClient(AcquisitionSettings()).search("Film")
