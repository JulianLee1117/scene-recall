"""Offline coverage for the CDN-only transport; no provider requests."""
import hashlib
import http.client
import io
import socket
import ssl
import threading
import time
from types import SimpleNamespace
from urllib.request import build_opener

import pytest

from pipeline.transitions import providers


HOST = "example.cloudfront.net"
URL = f"https://{HOST}/owned-output?signature=private"


class FakeSocket:
    def __init__(self, family, connect=None):
        self.family, self.action = family, connect
        self.timeout, self.closed = None, False

    def settimeout(self, value):
        self.timeout = value

    def connect(self, address):
        if self.action:
            self.action(self, address)

    def setsockopt(self, *_args):
        pass

    def shutdown(self, *_args):
        pass

    def close(self):
        self.closed = True


def test_download_prefers_working_ipv4_before_unreachable_ipv6(monkeypatch):
    addresses = [(socket.AF_INET6, socket.SOCK_STREAM, 0, "", ("::1", 443, 0, 0)),
                 (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("127.0.0.1", 443))]
    monkeypatch.setattr(providers.socket, "getaddrinfo", lambda *_args, **_kwargs: addresses)
    attempted = []

    def connect(sock, target):
        attempted.append(sock.family)
        if sock.family == socket.AF_INET6:
            raise TimeoutError("unreachable IPv6 route")

    monkeypatch.setattr(providers.socket, "socket", lambda family, *_args: FakeSocket(family, connect))
    with providers._DownloadBudget(lambda: False) as budget:
        connected = providers._download_connect((HOST, 443), budget, time.monotonic() + 10)
        assert connected.family == socket.AF_INET
        assert attempted == [socket.AF_INET]
    assert connected.closed


def test_address_failures_share_one_connect_deadline(monkeypatch):
    clock = SimpleNamespace(now=0.)
    monkeypatch.setattr(providers, "time", SimpleNamespace(monotonic=lambda: clock.now))
    rows = [(family, socket.SOCK_STREAM, 0, "", ("address", 443))
            for family in (socket.AF_INET, socket.AF_INET6, socket.AF_INET)]
    monkeypatch.setattr(providers, "_download_addresses", lambda *_args: rows)
    attempts, sockets = [], []

    def fail(sock, _address):
        attempts.append(sock.timeout)
        clock.now += sock.timeout
        raise TimeoutError("unreachable")

    def create(family, *_args):
        sock = FakeSocket(family, fail)
        sockets.append(sock)
        return sock

    monkeypatch.setattr(providers.socket, "socket", create)
    with providers._DownloadBudget(lambda: False) as budget:
        with pytest.raises(TimeoutError, match="time budget"):
            providers._download_connect((HOST, 443), budget, deadline=5)
    assert attempts == [3, 2], "each address receives only the remaining shared budget"
    assert clock.now == 5 and all(sock.closed for sock in sockets)


def test_stalled_dns_is_bounded_and_releases_its_resolver_slot(monkeypatch):
    release = threading.Event()
    finished = threading.Event()
    slots = threading.BoundedSemaphore(1)
    monkeypatch.setattr(providers, "_DOWNLOAD_RESOLVERS", slots)

    def blocked(*_args, **_kwargs):
        release.wait(2)
        finished.set()
        return []

    monkeypatch.setattr(providers.socket, "getaddrinfo", blocked)
    started = time.monotonic()
    try:
        with providers._DownloadBudget(lambda: False) as budget:
            with pytest.raises(TimeoutError):
                providers._download_addresses((HOST, 443), budget, started + .05)
        assert time.monotonic() - started < .5
        assert not slots.acquire(blocking=False), "a pending OS resolver still owns its bounded slot"
    finally:
        release.set()
        assert finished.wait(1)
    assert slots.acquire(timeout=1)
    slots.release()


def test_https_connection_keeps_default_verification_and_original_sni(monkeypatch):
    raw = FakeSocket(socket.AF_INET)
    with providers._DownloadBudget(lambda: False) as budget:
        connection = providers._DownloadHTTPSConnection(HOST, budget=budget)
        assert connection._context.check_hostname
        assert connection._context.verify_mode == ssl.CERT_REQUIRED
        names = []

        def reject(_sock, *, server_hostname):
            names.append(server_hostname)
            raise ssl.SSLCertVerificationError("untrusted certificate")

        monkeypatch.setattr(connection, "_create_connection", lambda *_args: raw)
        monkeypatch.setattr(connection._context, "wrap_socket", reject)
        with pytest.raises(ssl.SSLCertVerificationError):
            connection.connect()
        connection.close()
        assert names == [HOST], "TLS verifies the CDN hostname, never its resolved IP"


def test_total_deadline_interrupts_stalled_response_headers(monkeypatch):
    monkeypatch.setattr(providers, "DOWNLOAD_SECONDS", .08)
    client, server = socket.socketpair()
    stop = threading.Event()

    def trickle_headers():
        try:
            server.recv(4096)
            server.sendall(b"HTTP/1.1 200 OK\r\nX-Slow: ")
            while not stop.wait(.01):
                server.sendall(b"x")
        except OSError:
            pass

    writer = threading.Thread(target=trickle_headers, daemon=True)
    writer.start()
    started = time.monotonic()
    try:
        with providers._DownloadBudget(lambda: False) as budget:
            connection = providers._DownloadHTTPSConnection(HOST, budget=budget)
            monkeypatch.setattr(connection, "_create_connection", lambda *_args: client)
            # Exercise actual HTTP header reads on a local socket. TLS policy is
            # tested separately above; this fixture never connects to a host.
            monkeypatch.setattr(connection._context, "wrap_socket", lambda sock, **_kwargs: sock)
            connection.request("GET", "/output")
            with pytest.raises((OSError, http.client.HTTPException)):
                connection.getresponse()
                budget.remaining()
        assert time.monotonic() - started < .5
    finally:
        stop.set()
        client.close()
        server.close()
        writer.join(timeout=1)


class Response(io.BytesIO):
    def __init__(self, data, length=None, on_read=None):
        super().__init__(data)
        self.headers = {} if length is None else {"Content-Length": str(length)}
        self.on_read = on_read

    def read1(self, size):
        if self.on_read:
            self.on_read()
        return super().read1(size)


def fake_download(monkeypatch, response):
    requests = []

    def opener(*handlers):
        assert any(isinstance(item, providers._NoRedirect) for item in handlers)
        if not any(isinstance(item, providers._DownloadHTTPSHandler) for item in handlers):
            return build_opener(*handlers)  # The separate API opener is never used.

        def open_request(request, timeout):
            requests.append(request)
            return response
        return SimpleNamespace(open=open_request)

    client = providers.RunwayClient(key="must-not-reach-cdn")
    monkeypatch.setattr(providers, "build_opener", opener)
    return client, requests


def test_download_needs_no_api_key_and_sends_no_authorization(tmp_path, monkeypatch):
    data = b"bounded original"
    client, requests = fake_download(monkeypatch, Response(data, len(data)))
    client._key = ""
    path = tmp_path / "original.partial"
    receipt = client.download(URL, path)
    assert path.read_bytes() == data
    assert receipt == {"size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    assert requests[0].get_method() == "GET"
    assert requests[0].get_header("Authorization") is None
    assert requests[0].get_header("X-Runway-Version") is None


@pytest.mark.parametrize("declared,actual", [(9, b"123456789"), (None, b"123456789"), (8, b"short")])
def test_oversized_or_truncated_download_removes_only_new_partial(tmp_path, monkeypatch, declared, actual):
    monkeypatch.setattr(providers.bridges, "MAX_UPLOAD_BYTES", 8)
    client, _ = fake_download(monkeypatch, Response(actual, declared))
    path = tmp_path / "original.partial"
    with pytest.raises(ValueError):
        client.download(URL, path)
    assert not path.exists()


def test_download_preserves_preexisting_destination_and_cancelled_partial(tmp_path, monkeypatch):
    path = tmp_path / "original.partial"
    path.write_bytes(b"owned earlier")
    client, _ = fake_download(monkeypatch, Response(b"new"))
    with pytest.raises(FileExistsError):
        client.download(URL, path)
    assert path.read_bytes() == b"owned earlier"
    path.unlink()
    stopped = [False]
    client, _ = fake_download(monkeypatch, Response(b"new", on_read=lambda: stopped.__setitem__(0, True)))
    with pytest.raises(providers.bridges.JobCancelled):
        client.download(URL, path, cancelled=lambda: stopped[0])
    assert not path.exists()


def test_body_cannot_complete_after_total_deadline(tmp_path, monkeypatch):
    clock = SimpleNamespace(now=0.)
    monkeypatch.setattr(providers, "time", SimpleNamespace(monotonic=lambda: clock.now))
    client, _ = fake_download(monkeypatch, Response(b"late", on_read=lambda: setattr(clock, "now", 121.)))
    path = tmp_path / "original.partial"
    with pytest.raises(TimeoutError):
        client.download(URL, path)
    assert not path.exists()
