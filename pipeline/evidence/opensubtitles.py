"""OpenSubtitles.com REST client: search, rank and download subtitle files.

Searching is free; each download counts against the account's daily quota
(20/day for a logged-in free account). Raw downloads are preserved byte for
byte in the evidence archive before any derivation touches them.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import os
import re
import struct
from pathlib import Path
from typing import Any

import httpx


API_ROOT = "https://api.opensubtitles.com/api/v1"
USER_AGENT = "SceneRecall v1.0"


class OpenSubtitlesError(RuntimeError):
    pass


class QuotaExhausted(OpenSubtitlesError):
    pass


def movie_hash(path: Path) -> str:
    """OpenSubtitles hash: file size plus 64-bit sums of the first/last 64 KiB."""
    size = os.path.getsize(path)
    if size < 131072:
        raise ValueError("file too small for an OpenSubtitles hash")
    value = size
    with open(path, "rb") as handle:
        for offset in (0, size - 65536):
            handle.seek(offset)
            for (word,) in struct.iter_unpack("<Q", handle.read(65536)):
                value = (value + word) & 0xFFFFFFFFFFFFFFFF
    return f"{value:016x}"


@dataclass(frozen=True)
class Candidate:
    subtitle_id: str
    file_id: int
    file_name: str
    release: str
    language: str
    download_count: int
    hearing_impaired: bool
    machine_translated: bool
    ai_translated: bool
    foreign_parts_only: bool
    from_trusted: bool
    fps: float | None
    moviehash_match: bool
    imdb_id: str | None
    files: int

    def as_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def parse_candidates(payload: dict[str, Any]) -> list[Candidate]:
    result = []
    for row in (payload or {}).get("data", []) or []:
        attributes = row.get("attributes") or {}
        files = attributes.get("files") or []
        if not files or not isinstance(files[0], dict) or "file_id" not in files[0]:
            continue
        feature = attributes.get("feature_details") or {}
        imdb = feature.get("imdb_id")
        result.append(Candidate(
            subtitle_id=str(attributes.get("subtitle_id") or row.get("id") or ""),
            file_id=int(files[0]["file_id"]),
            file_name=str(files[0].get("file_name") or ""),
            release=str(attributes.get("release") or ""),
            language=str(attributes.get("language") or ""),
            download_count=int(attributes.get("download_count") or 0),
            hearing_impaired=bool(attributes.get("hearing_impaired")),
            machine_translated=bool(attributes.get("machine_translated")),
            ai_translated=bool(attributes.get("ai_translated")),
            foreign_parts_only=bool(attributes.get("foreign_parts_only")),
            from_trusted=bool(attributes.get("from_trusted")),
            fps=float(attributes["fps"]) if attributes.get("fps") else None,
            moviehash_match=bool(attributes.get("moviehash_match")),
            imdb_id=f"tt{int(imdb):07d}" if isinstance(imdb, (int, str)) and str(imdb).isdigit() else None,
            files=len(files),
        ))
    return result


def _tokens(text: str) -> set[str]:
    return {token for token in re.split(r"[^a-z0-9]+", text.casefold()) if len(token) > 1}


def score_candidate(candidate: Candidate, *, film_file: str, film_fps: float | None) -> float | None:
    """Higher is better; ``None`` excludes unusable subtitles."""
    if candidate.language not in {"en", "en-us", "en-gb"} or candidate.foreign_parts_only or candidate.files != 1:
        return None
    score = 0.0
    score += 1000.0 if candidate.moviehash_match else 0.0
    score -= 400.0 if (candidate.machine_translated or candidate.ai_translated) else 0.0
    score += 60.0 if candidate.from_trusted else 0.0
    score -= 40.0 if candidate.hearing_impaired else 0.0
    score += 12.0 * math.log10(1 + candidate.download_count)
    if film_fps and candidate.fps:
        score += 30.0 if abs(candidate.fps - film_fps) < 0.02 else -10.0
    release_tokens = _tokens(candidate.release) | _tokens(candidate.file_name)
    film_tokens = _tokens(Path(film_file).stem)
    special = {"criterion", "remastered", "extended", "directors", "cut", "unrated", "theatrical", "imax"}
    score += 25.0 * len(release_tokens & film_tokens & special)
    return score


def rank_candidates(candidates: list[Candidate], *, film_file: str, film_fps: float | None,
                    imdb_id: str | None = None) -> list[tuple[float, Candidate]]:
    """Rank usable candidates; hash searches can return other titles, so the IMDb ID must match."""
    unique: dict[int, Candidate] = {}
    for candidate in candidates:
        if imdb_id and candidate.imdb_id != imdb_id:
            continue
        previous = unique.get(candidate.file_id)
        if previous is None or (candidate.moviehash_match and not previous.moviehash_match):
            unique[candidate.file_id] = candidate
    ranked = [(score, candidate) for candidate in unique.values()
              if (score := score_candidate(candidate, film_file=film_file, film_fps=film_fps)) is not None]
    return sorted(ranked, key=lambda item: (-item[0], item[1].file_id))


class Client:
    """Minimal authenticated client; call :meth:`login` before downloads."""

    def __init__(self, api_key: str, *, username: str | None = None, password: str | None = None,
                 transport: httpx.BaseTransport | None = None):
        if not api_key:
            raise OpenSubtitlesError("OPENSUBTITLES_API_KEY is not configured")
        self._username, self._password = username, password
        self._root = API_ROOT
        self._token: str | None = None
        transport = transport or httpx.HTTPTransport(local_address="0.0.0.0")
        self._http = httpx.Client(timeout=60.0, follow_redirects=True, transport=transport,
                                  headers={"Api-Key": api_key, "User-Agent": USER_AGENT,
                                           "Accept": "application/json", "Content-Type": "application/json"})
        self.remaining: int | None = None

    @classmethod
    def from_env(cls) -> "Client":
        return cls(os.environ.get("OPENSUBTITLES_API_KEY", ""), username=os.environ.get("OPENSUBTITLES_USERNAME"),
                   password=os.environ.get("OPENSUBTITLES_PASSWORD"))

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "Client":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def login(self) -> None:
        if self._token:
            return
        if not self._username or not self._password:
            raise OpenSubtitlesError("OPENSUBTITLES_USERNAME/PASSWORD are required for downloads")
        response = self._http.post(f"{API_ROOT}/login", json={"username": self._username, "password": self._password})
        if response.status_code != 200:
            raise OpenSubtitlesError(f"OpenSubtitles login failed (HTTP {response.status_code})")
        body = response.json()
        self._token = body.get("token")
        base = body.get("base_url")
        if base:
            self._root = f"https://{base}/api/v1" if not str(base).startswith("http") else f"{base}/api/v1"
        user = body.get("user") or {}
        if isinstance(user.get("remaining_downloads"), int):
            self.remaining = user["remaining_downloads"]
        if not self._token:
            raise OpenSubtitlesError("OpenSubtitles login returned no token")

    def search(self, *, imdb_id: str | None = None, moviehash: str | None = None, languages: str = "en") -> list[Candidate]:
        params: dict[str, Any] = {"languages": languages}
        if imdb_id:
            params["imdb_id"] = str(int(imdb_id.lstrip("t")))
        if moviehash:
            params["moviehash"] = moviehash
        params = dict(sorted(params.items()))  # the API caches sorted, lowercase queries
        response = self._http.get(f"{self._root}/subtitles", params=params)
        if response.status_code >= 400:
            raise OpenSubtitlesError(f"OpenSubtitles search failed (HTTP {response.status_code})")
        return parse_candidates(response.json())

    def download(self, file_id: int) -> bytes:
        """Download one subtitle file; consumes one unit of daily quota."""
        self.login()
        response = self._http.post(f"{self._root}/download", json={"file_id": int(file_id)},
                                   headers={"Authorization": f"Bearer {self._token}"})
        if response.status_code == 406 or (response.status_code == 429 and "download" in response.text.lower()):
            raise QuotaExhausted("OpenSubtitles daily download quota is exhausted")
        if response.status_code >= 400:
            raise OpenSubtitlesError(f"OpenSubtitles download request failed (HTTP {response.status_code})")
        body = response.json()
        if isinstance(body.get("remaining"), int):
            self.remaining = body["remaining"]
        link = body.get("link")
        if not link:
            raise OpenSubtitlesError("OpenSubtitles returned no download link")
        file_response = self._http.get(link, headers={"Accept": "*/*"})
        if file_response.status_code >= 400:
            raise OpenSubtitlesError(f"subtitle file download failed (HTTP {file_response.status_code})")
        return file_response.content
