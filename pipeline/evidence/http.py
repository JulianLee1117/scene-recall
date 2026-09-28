"""Polite JSON HTTP access for open-data producers."""

from __future__ import annotations

import time
from typing import Any

import httpx


USER_AGENT = "SceneRecall/1.0 (personal non-commercial film library tool; https://github.com/JulianLee1117/scene-recall)"


class HttpError(RuntimeError):
    pass


class JsonClient:
    """Small wrapper with a descriptive User-Agent, spacing and bounded retries."""

    def __init__(self, *, user_agent: str = USER_AGENT, min_interval: float = 0.2,
                 timeout: float = 30.0, retries: int = 3, transport: httpx.BaseTransport | None = None):
        # httpx has no "happy eyeballs": on networks with broken IPv6 every new
        # connection first waits ~20 s for the IPv6 attempt. Bind IPv4 instead.
        transport = transport or httpx.HTTPTransport(local_address="0.0.0.0")
        self._client = httpx.Client(headers={"User-Agent": user_agent, "Accept": "application/json"},
                                    timeout=timeout, follow_redirects=True, transport=transport)
        self._min_interval = min_interval
        self._retries = retries
        self._last = 0.0

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "JsonClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def get(self, url: str, params: dict[str, Any] | None = None) -> Any:
        delay = 1.0
        for attempt in range(self._retries + 1):
            wait = self._min_interval - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            try:
                response = self._client.get(url, params=params)
            except httpx.TransportError as exc:
                if attempt == self._retries:
                    raise HttpError(f"GET {url} failed: {exc}") from exc
            else:
                if response.status_code == 404:
                    return None
                if response.status_code in (429, 500, 502, 503, 504) and attempt < self._retries:
                    retry_after = response.headers.get("Retry-After")
                    time.sleep(float(retry_after) if retry_after and retry_after.isdigit() else delay)
                    delay *= 2
                    continue
                if response.status_code >= 400:
                    raise HttpError(f"GET {url} returned HTTP {response.status_code}")
                try:
                    return response.json()
                except ValueError as exc:
                    raise HttpError(f"GET {url} returned invalid JSON") from exc
            time.sleep(delay)
            delay *= 2
        raise HttpError(f"GET {url} failed after retries")

    def download(self, url: str, destination: Any) -> None:
        """Stream a (possibly large) file to *destination*."""
        with self._client.stream("GET", url, headers={"Accept": "*/*"}) as response:
            if response.status_code >= 400:
                raise HttpError(f"GET {url} returned HTTP {response.status_code}")
            with open(destination, "wb") as handle:
                for chunk in response.iter_bytes(1024 * 1024):
                    handle.write(chunk)
