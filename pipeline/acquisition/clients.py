"""Small, bounded clients for explicitly configured qBittorrent and Prowlarr.

Transport errors deliberately omit response bodies and URLs: both services can
return credentials or private tracker tokens in their responses.
"""

from http.cookiejar import CookieJar
from collections.abc import Callable
import json
import os
from pathlib import PureWindowsPath
import re
import secrets
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit
from urllib.request import HTTPCookieProcessor, HTTPRedirectHandler, ProxyHandler, Request, build_opener

from .settings import AcquisitionSettings
from .sources import (
    MAX_MAGNET_LENGTH, MAX_TORRENT_BYTES, MAX_TORRENT_FILES, SourceError,
    TorrentSource, normalize_info_hash, parse_magnet, parse_torrent, validate_relative_path,
)

MAX_JSON_BYTES = 4 * 1024 * 1024
CATEGORY = "scene-recall"
_TAG = re.compile(r"scene-recall-[A-Za-z0-9_-]{1,100}")
# qBittorrent applies share limits only to finished, normally started torrents.
# Stop locally even when the acquisition monitor is unavailable, without
# inheriting a global action that might delete the torrent or its source files.
_COMPLETION_LIMITS = {
    "ratioLimit": "0", "seedingTimeLimit": "0",
    "inactiveSeedingTimeLimit": "-1", "shareLimitAction": "Stop",
}


class ClientError(RuntimeError):
    """A public-safe integration failure."""


class _AuthenticationError(ClientError):
    pass


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class _Transport:
    def __init__(self, base_url: str, timeout: float, service: str) -> None:
        self.base_url = base_url
        self.timeout = timeout
        self.service = service
        # A user's generic HTTP_PROXY must not intercept these credentialed calls.
        self.opener = build_opener(ProxyHandler({}), HTTPCookieProcessor(CookieJar()), _NoRedirect())

    def request(self, path: str, *, data: bytes | None = None, headers: dict | None = None,
                max_bytes: int = MAX_JSON_BYTES, allow_magnet_redirect: bool = False) -> tuple[bytes, str]:
        if not self.base_url:
            raise ClientError(f"{self.service} is not configured.")
        req = Request(self.base_url + path, data=data, headers={"Accept-Encoding": "identity", **(headers or {})})
        try:
            with self.opener.open(req, timeout=self.timeout) as response:
                content_length = response.headers.get("Content-Length")
                if content_length and int(content_length) > max_bytes:
                    raise ClientError(f"{self.service} returned an oversized response.")
                payload = response.read(max_bytes + 1)
                if len(payload) > max_bytes:
                    raise ClientError(f"{self.service} returned an oversized response.")
                return payload, response.headers.get("Content-Type", "")
        except HTTPError as exc:
            if allow_magnet_redirect and exc.code in {301, 302, 303, 307, 308}:
                location = exc.headers.get("Location", "")
                exc.close()
                if location.lower().startswith("magnet:") and len(location) <= MAX_MAGNET_LENGTH:
                    return location.encode("utf-8"), "text/x-magnet"
                raise ClientError(f"{self.service} returned an unsupported release redirect.") from None
            code = exc.code
            exc.close()
            if code in {401, 403}:
                raise _AuthenticationError(f"{self.service} authentication failed; check its server configuration.") from None
            raise ClientError(f"{self.service} request failed (HTTP {code}).") from None
        except (URLError, TimeoutError, OSError):
            raise ClientError(f"{self.service} is unreachable or the request timed out.") from None
        except (ValueError, UnicodeError):
            raise ClientError(f"{self.service} returned an invalid response.") from None

    def json(self, path: str, *, headers: dict | None = None):
        payload, _ = self.request(path, headers=headers)
        try:
            return json.loads(payload)
        except (ValueError, UnicodeError, RecursionError):
            raise ClientError(f"{self.service} returned invalid JSON.") from None


class QBittorrentClient:
    def __init__(self, settings: AcquisitionSettings) -> None:
        self.settings = settings
        self._transport = _Transport(settings.qbittorrent_url, settings.request_timeout_seconds, "qBittorrent")
        self._authenticated = False
        self._version: str | None = None

    def _headers(self) -> dict[str, str]:
        return {"Referer": self.settings.qbittorrent_url + "/"}

    def _login(self) -> None:
        if self._authenticated:
            return
        if not self.settings.downloads_enabled:
            raise ClientError("qBittorrent is not configured; set QBITTORRENT_URL on the server.")
        # Blank credentials permit an explicitly configured local auth bypass.
        payload, _ = self._transport.request(
            "/api/v2/auth/login", data=urlencode({"username": self.settings.qbittorrent_username,
            "password": self.settings.qbittorrent_password}).encode(),
            headers={**self._headers(), "Content-Type": "application/x-www-form-urlencoded"}, max_bytes=1024,
        )
        # 5.0 returns "Ok."; newer 5.x returns HTTP 200 with an empty body
        # and rejects bad credentials with 401. The next protected API call
        # validates the session cookie in either version.
        if payload.strip() not in {b"Ok.", b""}:
            raise ClientError("qBittorrent authentication failed; check its server configuration.")
        self._authenticated = True

    def _request(self, path: str, **kwargs) -> tuple[bytes, str]:
        self._login()
        try:
            return self._transport.request("/api/v2/" + path, **kwargs)
        except _AuthenticationError:
            # The denied request did not execute. Renew one expired cookie and
            # retry once; never retry an ambiguous transport failure here.
            self._authenticated = False
            self._login()
            return self._transport.request("/api/v2/" + path, **kwargs)

    def _json(self, path: str):
        payload, _ = self._request(path, headers=self._headers())
        try:
            return json.loads(payload)
        except (ValueError, UnicodeError, RecursionError):
            raise ClientError("qBittorrent returned invalid JSON.") from None

    def _post(self, path: str, values: dict[str, str]) -> bytes:
        payload, _ = self._request(path, data=urlencode(values).encode(),
            headers={**self._headers(), "Content-Type": "application/x-www-form-urlencoded"}, max_bytes=64 * 1024)
        return payload

    def app_version(self) -> str:
        if self._version is None:
            payload, _ = self._request("app/version", headers=self._headers(), max_bytes=100)
            try:
                version = payload.decode("ascii").strip()
            except UnicodeError:
                raise ClientError("qBittorrent returned an invalid version.") from None
            if not re.fullmatch(r"v?5\.\d+(?:\.\d+)?(?:[A-Za-z0-9.-]*)", version):
                raise ClientError("Managed downloads require qBittorrent 5.x.")
            self._version = version
        return self._version

    def status(self) -> dict:
        # Settings/status surfaces must reflect a disconnected service even if
        # this long-lived client successfully checked its version earlier.
        self._version = None
        return {"configured": self.settings.downloads_enabled, "connected": True, "version": self.app_version()}

    def info(self, info_hash: str) -> dict | None:
        info_hash = normalize_info_hash(info_hash)
        rows = self._json("torrents/info?" + urlencode({"hashes": info_hash}))
        if not isinstance(rows, list) or len(rows) > 1 or any(not isinstance(row, dict) for row in rows):
            raise ClientError("qBittorrent returned an invalid torrent record.")
        if not rows:
            return None
        if str(rows[0].get("hash", "")).lower() != info_hash:
            raise ClientError("qBittorrent returned a different torrent identity.")
        return rows[0]

    @staticmethod
    def _assert_owner(row: dict, tag: str | None = None) -> None:
        tags = {part.strip() for part in str(row.get("tags", "")).split(",")}
        if row.get("category") != CATEGORY or not any(_TAG.fullmatch(item) for item in tags) or (tag is not None and tag not in tags):
            raise ClientError("This torrent already exists outside this acquisition; it will not be modified.")

    def files(self, info_hash: str) -> list[dict]:
        info_hash = normalize_info_hash(info_hash)
        rows = self._json("torrents/files?" + urlencode({"hash": info_hash}))
        if not isinstance(rows, list) or len(rows) > MAX_TORRENT_FILES:
            raise ClientError("qBittorrent returned an invalid or oversized file list.")
        paths: set[str] = set()
        for row in rows:
            if not isinstance(row, dict):
                raise ClientError("qBittorrent returned an invalid file record.")
            path = validate_relative_path(row.get("name"))
            folded = path.casefold()
            if folded in paths:
                raise SourceError("Torrent contains conflicting file paths.")
            paths.add(folded)
        for path in paths:
            if any("/".join(path.split("/")[:i]) in paths for i in range(1, len(path.split("/")))):
                raise SourceError("Torrent contains conflicting file paths.")
        return rows

    def _add(self, source: TorrentSource, save_path: str, tag: str, *,
             before_submit: Callable[[], None] | None = None) -> str:
        if not isinstance(tag, str) or not _TAG.fullmatch(tag):
            raise SourceError("A unique acquisition ownership tag is required.")
        save_path = str(save_path)
        if not (os.path.isabs(save_path) or PureWindowsPath(save_path).is_absolute()) or any(ord(c) < 32 for c in save_path):
            raise SourceError("An absolute acquisition staging path is required.")
        self.app_version()
        existing = self.info(source.info_hash)
        if existing is not None:
            self._assert_owner(existing, tag)
            if os.path.normcase(os.path.normpath(str(existing.get("save_path", "")))) != os.path.normcase(os.path.normpath(save_path)):
                raise ClientError("The existing torrent's staging path does not match this acquisition.")
            self._post("torrents/setShareLimits", {"hashes": source.info_hash, **_COMPLETION_LIMITS})
            return source.info_hash
        preferences = self._json("app/preferences")
        if not isinstance(preferences, dict):
            raise ClientError("qBittorrent returned invalid preferences.")
        if preferences.get("autorun_enabled") or preferences.get("autorun_on_torrent_added_enabled"):
            raise ClientError("Disable qBittorrent's external-program hooks before using managed downloads.")
        categories = self._json("torrents/categories")
        if not isinstance(categories, dict):
            raise ClientError("qBittorrent returned invalid categories.")
        if CATEGORY not in categories:
            self._post("torrents/createCategory", {"category": CATEGORY})
        fields = {
            "savepath": save_path, "downloadPath": save_path, "useDownloadPath": "false",
            "category": CATEGORY, "tags": tag, "autoTMM": "false", "contentLayout": "Original",
            "skip_checking": "false", "stopped": "false", "stopCondition": "None",
            "sequentialDownload": "false", "firstLastPiecePrio": "false",
            **_COMPLETION_LIMITS,
        }
        if source.magnet is not None:
            fields["urls"] = source.magnet
        boundary = "scene-recall-" + secrets.token_hex(16)
        parts = []
        for key, value in fields.items():
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode())
        if source.torrent_bytes is not None:
            parts.extend([f'--{boundary}\r\nContent-Disposition: form-data; name="torrents"; filename="release.torrent"\r\nContent-Type: application/x-bittorrent\r\n\r\n'.encode(), source.torrent_bytes, b"\r\n"])
        parts.append(f"--{boundary}--\r\n".encode())
        body = b"".join(parts)
        headers = {**self._headers(), "Content-Type": f"multipart/form-data; boundary={boundary}"}
        # Journal uncertain submission only after every local/preflight step
        # succeeds. A failing callback prevents the external add altogether.
        if before_submit is not None:
            before_submit()
        payload, _ = self._request("torrents/add", data=body, headers=headers, max_bytes=64 * 1024)
        if payload.strip() != b"Ok.":
            try:
                result = json.loads(payload)
                accepted = isinstance(result, dict) and (result.get("success_count", 0) + result.get("pending_count", 0) > 0)
            except (ValueError, TypeError, UnicodeError):
                accepted = False
            if not accepted:
                raise ClientError("qBittorrent did not accept the torrent; retry will reconcile its identity first.")
        # A successful add can become visible asynchronously. The durable worker
        # must confirm category, tag and path before it manages the result.
        return source.info_hash

    def add_magnet(self, magnet: str, save_path: str, tag: str, *,
                   before_submit: Callable[[], None] | None = None) -> str:
        return self._add(parse_magnet(magnet), save_path, tag, before_submit=before_submit)

    def add_torrent(self, data: bytes, save_path: str, tag: str, *,
                    before_submit: Callable[[], None] | None = None) -> str:
        return self._add(parse_torrent(data), save_path, tag, before_submit=before_submit)

    def stop(self, info_hash: str, *, tag: str | None = None) -> None:
        info_hash = normalize_info_hash(info_hash)
        row = self.info(info_hash)
        if row is not None:
            self._assert_owner(row, tag)
            self._post("torrents/stop", {"hashes": info_hash})

    def start(self, info_hash: str, *, tag: str | None = None) -> None:
        info_hash = normalize_info_hash(info_hash)
        row = self.info(info_hash)
        if row is not None:
            self._assert_owner(row, tag)
            self._post("torrents/setShareLimits", {"hashes": info_hash, **_COMPLETION_LIMITS})
            self._post("torrents/start", {"hashes": info_hash})

    def remove(self, info_hash: str, delete_files: bool = False, *, tag: str | None = None) -> None:
        if delete_files is not False:
            raise ClientError("Torrent-client file deletion is disabled; acquisition cleanup owns staging files.")
        info_hash = normalize_info_hash(info_hash)
        row = self.info(info_hash)
        if row is not None:
            self._assert_owner(row, tag)
            self._post("torrents/delete", {"hashes": info_hash, "deleteFiles": "false"})


class ProwlarrClient:
    def __init__(self, settings: AcquisitionSettings) -> None:
        self.settings = settings
        self._transport = _Transport(settings.prowlarr_url, settings.request_timeout_seconds, "Prowlarr")

    def _headers(self) -> dict[str, str]:
        if not self.settings.search_enabled:
            raise ClientError("Release search is not configured; set PROWLARR_URL and PROWLARR_API_KEY on the server.")
        return {"X-Api-Key": self.settings.prowlarr_api_key}

    def search(self, q: str, limit: int = 30) -> list[dict]:
        if not isinstance(q, str) or not q.strip() or len(q) > 200 or any(ord(c) < 32 for c in q):
            raise SourceError("Search must contain between 1 and 200 characters.")
        if type(limit) is not int or not 1 <= limit <= 50:
            raise SourceError("Search limit must be between 1 and 50.")
        query = q.strip()
        candidates = self._search_once(query, limit)
        # Some configured indexers return an empty broad title search despite
        # serving that title as a phrase. Retry once within the same Movies
        # category; keep successful searches and provider errors unchanged.
        if not candidates and '"' not in query and len(query) <= 198:
            candidates = self._search_once(f'"{query}"', limit)
        return candidates

    def _search_once(self, q: str, limit: int) -> list[dict]:
        rows = self._transport.json("/api/v1/search?" + urlencode({"query": q.strip(), "type": "search", "categories": 2000, "limit": limit}), headers=self._headers())
        if not isinstance(rows, list):
            raise ClientError("Prowlarr returned invalid search results.")
        candidates = []
        for row in rows:
            if len(candidates) >= limit:
                break
            if not isinstance(row, dict) or str(row.get("protocol", "torrent")).lower() not in {"torrent", "2"}:
                continue
            magnet = row.get("magnetUrl") or ""
            download = row.get("downloadUrl") or ""
            if not all(isinstance(value, str) and len(value) <= MAX_MAGNET_LENGTH for value in (magnet, download)) or not (magnet or download):
                continue
            title = row.get("title")
            if not isinstance(title, str) or not title:
                continue
            candidates.append({
                "id": secrets.token_hex(16), "title": title[:500],
                "size": self._number(row.get("size")), "seeders": self._number(row.get("seeders")),
                "indexer": str(row.get("indexer", ""))[:200],
                "_magnet_url": magnet, "_download_url": download,
            })
        return candidates

    @staticmethod
    def _number(value) -> int | None:
        return value if type(value) is int and 0 <= value <= 2**63 - 1 else None

    def _proxy_path(self, value: str) -> str:
        try:
            parsed = urlsplit(value)
            base = urlsplit(self.settings.prowlarr_url)
            same_origin = (parsed.scheme, parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80)) == (base.scheme, base.hostname, base.port or (443 if base.scheme == "https" else 80))
            if (not same_origin or parsed.username is not None or parsed.password is not None
                or parsed.fragment or "\\" in value or any(ord(c) < 33 for c in value)
                or not re.fullmatch(re.escape(base.path) + r"/[1-9][0-9]*/download", parsed.path)):
                raise ValueError
            # Authentication is always from server configuration, never a stale
            # key included in a search response or a browser-supplied URL.
            query = [(key, item) for key, item in parse_qsl(parsed.query, max_num_fields=30) if key.lower() != "apikey"]
            return parsed.path[len(base.path):] + ("?" + urlencode(query) if query else "")
        except ValueError:
            raise ClientError("Release must use the configured Prowlarr download proxy.") from None

    def fetch_release(self, candidate: dict) -> TorrentSource:
        headers = self._headers()
        magnet = candidate.get("_magnet_url") or ""
        download = candidate.get("_download_url") or ""
        value = magnet or download
        if not isinstance(value, str) or not value or len(value) > MAX_MAGNET_LENGTH:
            raise SourceError("Search release has no supported torrent source.")
        if value.lower().startswith("magnet:"):
            return parse_magnet(value)
        path = self._proxy_path(value)
        payload, _ = self._transport.request(path, headers=headers, max_bytes=MAX_TORRENT_BYTES, allow_magnet_redirect=True)
        if payload.lstrip().lower().startswith(b"magnet:"):
            try:
                return parse_magnet(payload.decode("utf-8").strip())
            except UnicodeError:
                raise SourceError("Prowlarr returned an invalid magnet.") from None
        return parse_torrent(payload)
