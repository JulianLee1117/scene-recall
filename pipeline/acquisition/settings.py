"""Explicit, server-only configuration for optional acquisition integrations."""

from dataclasses import dataclass, field
import os
import re
from urllib.parse import urlsplit, urlunsplit


def normalize_service_url(value: str, name: str) -> str:
    """Permit a configured HTTP service, never embedded credentials or queries."""
    value = value.strip()
    if not value:
        return ""
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or any(ord(c) < 33 for c in value)
            or "\\" in value
            or "%" in parsed.netloc
            or not re.fullmatch(r"(?:/[A-Za-z0-9._~-]+)*/?", parsed.path)
            or any(part in {".", ".."} for part in parsed.path.split("/"))
        ):
            raise ValueError
        _ = parsed.port
    except ValueError:
        raise ValueError(f"{name} must be an HTTP(S) service URL without credentials, query, or fragment.") from None
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))


@dataclass(frozen=True)
class AcquisitionSettings:
    qbittorrent_url: str = ""
    qbittorrent_username: str = field(default="", repr=False)
    qbittorrent_password: str = field(default="", repr=False)
    prowlarr_url: str = ""
    prowlarr_api_key: str = field(default="", repr=False)
    request_timeout_seconds: float = 15.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "qbittorrent_url", normalize_service_url(self.qbittorrent_url, "QBITTORRENT_URL"))
        object.__setattr__(self, "prowlarr_url", normalize_service_url(self.prowlarr_url, "PROWLARR_URL"))
        if not 1 <= self.request_timeout_seconds <= 60:
            raise ValueError("Acquisition request timeout must be between 1 and 60 seconds.")

    @property
    def downloads_enabled(self) -> bool:
        return bool(self.qbittorrent_url)

    @property
    def search_enabled(self) -> bool:
        return bool(self.prowlarr_url and self.prowlarr_api_key)


def load_settings() -> AcquisitionSettings:
    return AcquisitionSettings(
        qbittorrent_url=os.getenv("QBITTORRENT_URL", ""),
        qbittorrent_username=os.getenv("QBITTORRENT_USERNAME", ""),
        qbittorrent_password=os.getenv("QBITTORRENT_PASSWORD", ""),
        prowlarr_url=os.getenv("PROWLARR_URL", ""),
        prowlarr_api_key=os.getenv("PROWLARR_API_KEY", ""),
    )
