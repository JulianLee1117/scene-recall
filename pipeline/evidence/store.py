"""Immutable, versioned per-film evidence artifacts.

An artifact is one JSON document written atomically to::

    assets_dir/<film_id>/evidence/<kind>/<profile_id>.json

``profile_id`` identifies the producer and everything that shapes its output.
``inputs`` records digests of what the artifact was derived from; readers pass
the digests they expect and receive ``None`` for stale evidence. Library-wide
artifacts (for example an IMDb ratings extract) live under
``assets_dir/evidence/<kind>/``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any
from uuid import uuid4


SCHEMA_VERSION = 1
EVIDENCE_DIRNAME = "evidence"
_SAFE_NAME = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,99}$")
_FILM_ID = re.compile(r"^[a-f0-9]{64}$")


def canonical_json(value: Any) -> str:
    """Serialize deterministically for hashing and comparisons."""
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def digest(value: Any) -> str:
    """Return the SHA-256 of a JSON-serializable value."""
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def file_digest(path: Path) -> str:
    """Return the SHA-256 of a file's bytes without loading it at once."""
    hasher = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


@dataclass(frozen=True)
class Producer:
    """The identity of one evidence producer and its output-shaping settings."""

    kind: str
    name: str
    version: int
    settings: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for label, value in (("kind", self.kind), ("name", self.name)):
            if not _SAFE_NAME.fullmatch(value):
                raise ValueError(f"unsafe producer {label}: {value!r}")
        if isinstance(self.version, bool) or not isinstance(self.version, int) or self.version < 1:
            raise ValueError("producer version must be a positive integer")

    @property
    def descriptor(self) -> dict[str, Any]:
        return {"kind": self.kind, "name": self.name, "version": self.version, "settings": self.settings}

    @property
    def profile_id(self) -> str:
        """Readable, collision-resistant identity: ``<name>-v<version>-<hash>``."""
        return f"{self.name}-v{self.version}-{digest(self.descriptor)[:10]}"


def _film_dir(assets_dir: Path, film_id: str) -> Path:
    if not _FILM_ID.fullmatch(film_id):
        raise ValueError(f"invalid film_id {film_id!r}")
    return Path(assets_dir) / film_id / EVIDENCE_DIRNAME


def artifact_path(assets_dir: Path, film_id: str, producer: Producer, *, suffix: str = ".json") -> Path:
    return _film_dir(assets_dir, film_id) / producer.kind / f"{producer.profile_id}{suffix}"


def library_path(assets_dir: Path, kind: str, name: str) -> Path:
    """Path for a library-wide artifact such as a dataset extract."""
    for label, value in (("kind", kind), ("name", name)):
        if not _SAFE_NAME.fullmatch(value):
            raise ValueError(f"unsafe library artifact {label}: {value!r}")
    return Path(assets_dir) / EVIDENCE_DIRNAME / kind / name


def _atomic_write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Keep temporary names short: film IDs already make paths long on Windows.
    temporary = path.with_name(f".tmp-{uuid4().hex[:12]}")
    try:
        with temporary.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def write_json(path: Path, value: Any, *, compress: bool = False) -> None:
    """Atomically write JSON (optionally gzip-compressed)."""
    payload = json.dumps(value, ensure_ascii=False, indent=None if compress else 1, allow_nan=False).encode("utf-8")
    _atomic_write_bytes(Path(path), gzip.compress(payload, mtime=0) if compress else payload)


def read_json(path: Path) -> Any | None:
    """Read JSON or gzip JSON; a missing or unreadable file is ``None``."""
    path = Path(path)
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    try:
        if raw[:2] == b"\x1f\x8b":
            raw = gzip.decompress(raw)
        return json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None


def write_artifact(
    assets_dir: Path,
    film_id: str,
    producer: Producer,
    data: Any,
    *,
    inputs: dict[str, str],
    compress: bool = False,
) -> Path:
    """Publish one producer result for a film atomically; returns its path."""
    document = {
        "schema_version": SCHEMA_VERSION,
        "kind": producer.kind,
        "producer": producer.descriptor,
        "profile_id": producer.profile_id,
        "film_id": film_id,
        "inputs": dict(sorted(inputs.items())),
        "created_at": datetime.now(UTC).isoformat(),
        "data": data,
    }
    path = artifact_path(assets_dir, film_id, producer, suffix=".json.gz" if compress else ".json")
    write_json(path, document, compress=compress)
    return path


def read_artifact(
    assets_dir: Path,
    film_id: str,
    producer: Producer,
    *,
    inputs: dict[str, str] | None = None,
) -> dict[str, Any] | None:
    """Return a matching artifact document, or ``None`` when missing or stale.

    When ``inputs`` is given, every expected digest must match the recorded
    one; extra recorded inputs are allowed so optional context never forces
    recomputation by its mere presence.
    """
    for suffix in (".json", ".json.gz"):
        document = read_json(artifact_path(assets_dir, film_id, producer, suffix=suffix))
        if document is not None:
            break
    else:
        return None
    if (
        not isinstance(document, dict)
        or document.get("schema_version") != SCHEMA_VERSION
        or document.get("film_id") != film_id
        or document.get("profile_id") != producer.profile_id
        or document.get("producer") != producer.descriptor
    ):
        return None
    if inputs is not None:
        recorded = document.get("inputs") or {}
        if any(recorded.get(name) != value for name, value in inputs.items()):
            return None
    return document


def list_artifacts(assets_dir: Path, film_id: str, kind: str) -> list[Path]:
    """List every stored profile for one evidence kind (newest first)."""
    directory = _film_dir(assets_dir, film_id) / kind
    if not directory.is_dir():
        return []
    paths = [path for path in directory.iterdir() if path.name.endswith((".json", ".json.gz")) and not path.name.startswith(".")]
    return sorted(paths, key=lambda path: path.stat().st_mtime, reverse=True)
