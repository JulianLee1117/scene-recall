"""Read-only, bounded-root library file-size inventory and on-demand cache.

This is a sum of regular file lengths, not allocated blocks or a filesystem
snapshot. Shared files are counted once by identity. No cleanup, database
mutation, model resolution/download or scan of unrelated home/disk contents.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import os
from pathlib import Path
import shutil
import stat
import threading
import time
from typing import Any

from pipeline.config import Config


_CATEGORIES = {
    "source_films": "Films",
    "models": "Models",
    "indexes": "Search indexes",
    "user_state": "Projects and original music",
    "derived_assets": "Search and playback assets",
    "incoming": "Incoming downloads",
    "managed_archive": "Release archives",
}
_ISSUE_LIMIT = 50


@dataclass(frozen=True)
class StorageScope:
    category: str
    path: Path
    required: bool = False
    # HF snapshot links point into the same repository's counted blobs.
    cache_links: bool = False
    file_only: bool = False


class StorageScanCancelled(Exception):
    """Cooperative shutdown, distinct from a filesystem read failure."""


def _absolute(path: Path) -> Path:
    return Path(os.path.abspath(path))


def _key(path: Path) -> str:
    return os.path.normcase(str(_absolute(path)))


def _within(path: Path, root: Path) -> bool:
    try:
        return os.path.commonpath((_key(path), _key(root))) == _key(root)
    except ValueError:
        return False


def _is_link(info: os.stat_result) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


def configured_model_scopes(config: Config) -> list[StorageScope]:
    """Resolve only cache locations for selected models; never load weights."""
    from huggingface_hub.constants import HF_HUB_CACHE
    from pipeline.ingest.embed import _MODEL_REPOS
    from pipeline.ingest.text_embed import get_text_model_spec

    repos = {_MODEL_REPOS[config.models.visual_encoder], get_text_model_spec(config).model_id}
    whisper = config.models.whisper
    local_whisper = Path(whisper).expanduser()
    scopes = []
    if local_whisper.is_dir():
        scopes.append(StorageScope("models", local_whisper))
    else:
        from faster_whisper.utils import _MODELS

        if whisper in _MODELS:
            repos.add(_MODELS[whisper])
        elif len(whisper.split("/")) == 2 and not Path(whisper).is_absolute():
            repos.add(whisper)
        else:
            raise ValueError("The configured Whisper model cache could not be identified")
    hub = Path(HF_HUB_CACHE)
    for repo in sorted(repos):
        scopes.append(StorageScope("models", hub / ("models--" + repo.replace("/", "--")), cache_links=True))
    return scopes


def _volume_location(path: Path, info: os.stat_result) -> tuple[str, str, Path]:
    """Group actual devices, using the configured drive/mount as display text."""
    drive, _ = os.path.splitdrive(str(path))
    if drive:
        location = Path(drive + os.sep)
    else:
        location = path if stat.S_ISDIR(info.st_mode) else path.parent
        while location.parent != location and not os.path.ismount(location):
            location = location.parent
    return f"device-{info.st_dev}", str(location), location


def scan_library_storage(
    config: Config,
    registered_sources: Callable[[], Iterable[Path]],
    *,
    model_scopes: Callable[[Config], list[StorageScope]] = configured_model_scopes,
    cancelled: threading.Event | None = None,
) -> dict[str, Any]:
    """Measure known library roots without crossing links or counting aliases twice."""
    categories = {
        key: {"id": key, "label": label, "bytes": 0, "file_count": 0, "paths": [], "incomplete": False}
        for key, label in _CATEGORIES.items()
    }
    issues: list[dict[str, str]] = []
    omitted_issues = 0

    def issue(category: str, path: Path | str, reason: str) -> None:
        nonlocal omitted_issues
        categories[category]["incomplete"] = True
        if len(issues) < _ISSUE_LIMIT:
            issues.append({"path": str(path), "reason": reason})
        else:
            omitted_issues += 1

    paths = config.paths
    scopes = [
        StorageScope("source_films", paths.films_dir, required=True),
        StorageScope("models", paths.assets_dir / "matching" / "models"),
        StorageScope("models", paths.assets_dir / "lab" / "beat-this"),
        StorageScope("indexes", paths.assets_dir / "db"),
        StorageScope("user_state", paths.state_dir, required=True),
        StorageScope("derived_assets", paths.assets_dir, required=True),
        StorageScope("incoming", paths.incoming_dir),
        StorageScope("managed_archive", paths.incoming_dir.parent / "evidence"),
    ]
    if paths.playback_dir is not None:
        scopes.append(StorageScope("derived_assets", paths.playback_dir, required=True))
    if config.lab.beat_checkpoint:
        scopes.append(StorageScope("models", config.lab.beat_checkpoint, required=True, file_only=True))
    try:
        # Exact registered source files can live on other drives. Their parent
        # directories are deliberately not recursively scanned.
        scopes[:0] = [StorageScope("source_films", Path(path), required=True, file_only=True) for path in registered_sources()]
    except Exception:
        issue("source_films", "Registered film paths", "Film metadata was unavailable; external source files may be missing from this total.")
    try:
        scopes.extend(model_scopes(config))
    except Exception:
        issue("models", "Configured model caches", "One or more configured model cache locations could not be identified.")
    scopes = [StorageScope(scope.category, _absolute(scope.path), scope.required, scope.cache_links, scope.file_only) for scope in scopes]
    for scope in scopes:
        if str(scope.path) not in categories[scope.category]["paths"]:
            categories[scope.category]["paths"].append(str(scope.path))

    # More-specific configured roots own nested files; source/model/index scopes
    # precede broader roots for ties. The global identity sets also cover roots
    # that overlap and hard links between unrelated directory trees.
    ownership: dict[str, StorageScope] = {}
    for scope in scopes:
        ownership.setdefault(_key(scope.path), scope)
    seen_files: set[tuple[Any, ...]] = set()
    seen_directories: set[tuple[Any, ...]] = set()
    seen_roots: set[tuple[str, bool]] = set()
    volumes: dict[str, dict[str, Any]] = {}
    volume_for_device: dict[int, str] = {}
    missing_identity: set[str] = set()

    def volume(path: Path, info: os.stat_result) -> dict[str, Any]:
        known = volume_for_device.get(info.st_dev)
        if known is not None:
            return volumes[known]
        identifier, label, location = _volume_location(path, info)
        try:
            usage = shutil.disk_usage(location)
            free, capacity = usage.free, usage.total
        except OSError:
            free, capacity = None, None
        result = {"id": identifier, "label": label, "bytes": 0, "file_count": 0,
                  "free_bytes": free, "total_capacity_bytes": capacity, "incomplete": False}
        volumes[identifier] = result
        volume_for_device[info.st_dev] = identifier
        return result

    def identity(path: Path, info: os.stat_result, category: str) -> tuple[Any, ...]:
        if info.st_ino:
            return info.st_dev, info.st_ino
        if category not in missing_identity:
            missing_identity.add(category)
            issue(category, path, "This filesystem did not expose file identities; hard-link deduplication may be incomplete.")
        return ("path", _key(path))

    def visit(path: Path, scope: StorageScope, *, root: bool = False) -> None:
        if cancelled is not None and cancelled.is_set():
            raise StorageScanCancelled("Storage scan stopped")
        try:
            # DirEntry.stat() on Windows can report zero file identities. A
            # fresh no-follow stat is required to deduplicate NTFS hard links.
            info = os.stat(path, follow_symlinks=False)
        except FileNotFoundError:
            if not root or scope.required:
                issue(scope.category, path, "File or directory was missing during the scan.")
            return
        except OSError:
            issue(scope.category, path, "File or directory could not be read.")
            return
        if _is_link(info):
            if scope.cache_links and stat.S_ISLNK(info.st_mode):
                try:
                    target = _absolute(path.parent / os.readlink(path))
                    target_info = os.stat(target, follow_symlinks=False)
                    if _within(target, scope.path) and stat.S_ISREG(target_info.st_mode) and not _is_link(target_info):
                        return  # The same repo's blobs are counted directly.
                except OSError:
                    pass
            issue(scope.category, path, "Symbolic link or junction was not followed.")
            return
        actual_scope = ownership.get(_key(path), scope)
        if stat.S_ISDIR(info.st_mode) and actual_scope.file_only:
            if scope.file_only:
                issue(scope.category, path, "An expected source or checkpoint file was a directory; it was not scanned.")
                return
            actual_scope = scope
        category = categories[actual_scope.category]
        current_volume = volume(path, info)
        inode = identity(path, info, actual_scope.category)
        if stat.S_ISDIR(info.st_mode):
            if inode in seen_directories:
                return
            seen_directories.add(inode)
            try:
                with os.scandir(path) as entries:
                    for entry in entries:
                        visit(Path(entry.path), actual_scope)
            except OSError:
                issue(actual_scope.category, path, "Directory contents could not be read completely.")
                current_volume["incomplete"] = True
        elif stat.S_ISREG(info.st_mode):
            if inode in seen_files:
                return
            seen_files.add(inode)
            category["bytes"] += info.st_size
            category["file_count"] += 1
            current_volume["bytes"] += info.st_size
            current_volume["file_count"] += 1

    for scope in scopes:
        key = (_key(scope.path), scope.file_only)
        if key in seen_roots:
            continue
        seen_roots.add(key)
        if not scope.file_only and scope.path in (Path(scope.path.anchor), _absolute(Path.home())):
            issue(scope.category, scope.path, "A whole drive or home directory is outside the bounded library inventory.")
            continue
        # A link in a root's ancestry must not turn an exact registered path
        # into an implicit scan of a different tree.
        blocked = False
        for ancestor in scope.path.parents:
            try:
                if _is_link(os.stat(ancestor, follow_symlinks=False)):
                    issue(scope.category, scope.path, "A parent directory is a symbolic link or junction; this location was not scanned.")
                    blocked = True
                    break
            except FileNotFoundError:
                continue
            except OSError:
                issue(scope.category, scope.path, "A parent directory could not be inspected.")
                blocked = True
                break
        if not blocked:
            visit(scope.path, scope, root=True)
    incomplete = any(category["incomplete"] for category in categories.values())
    if incomplete:
        # Attribution of an unreadable path to a device is not always possible
        # (e.g. an offline drive). Do not label any per-drive subtotal complete.
        for current_volume in volumes.values():
            current_volume["incomplete"] = True
    return {
        "measured_at": datetime.now(timezone.utc).isoformat(),
        "measurement": "logical_file_bytes",
        "total_bytes": sum(category["bytes"] for category in categories.values()),
        "file_count": sum(category["file_count"] for category in categories.values()),
        "incomplete": incomplete,
        "categories": list(categories.values()),
        "volumes": sorted(volumes.values(), key=lambda item: item["label"].casefold()),
        "issues": issues,
        "omitted_issue_count": omitted_issues,
        "excluded": [
            "Unrelated application, developer and temporary files are not scanned.",
            "Shared model caches are limited to configured model repositories; other caches and arbitrary archives are excluded.",
            "File lengths are counted once by identity; sparse/compressed allocation and files changing during the scan can differ from disk usage.",
        ],
    }


class LibraryStorageStats:
    """One coalesced background scan; cached GETs never walk the filesystem."""

    def __init__(self, scan: Callable[[], dict[str, Any]], *, ttl_seconds: float = 300,
                 cancel: Callable[[], None] | None = None) -> None:
        self._scan = scan
        self._ttl = ttl_seconds
        self._lock = threading.Lock()
        self._snapshot: dict[str, Any] | None = None
        self._status = "ready"
        self._started_at: str | None = None
        self._completed = 0.0
        self._error: str | None = None
        self._closed = False
        self._cancel = cancel

    def get(self, *, refresh: bool = False) -> dict[str, Any]:
        with self._lock:
            stale = not self._completed or time.monotonic() - self._completed >= self._ttl
            if not self._closed and self._status != "scanning" and (refresh or stale):
                self._status = "scanning"
                self._error = None
                self._started_at = datetime.now(timezone.utc).isoformat()
                threading.Thread(target=self._run, daemon=True, name="library-storage").start()
            return deepcopy({"status": self._status, "started_at": self._started_at,
                             "snapshot": self._snapshot, "error": self._error})

    def _run(self) -> None:
        try:
            snapshot = self._scan()
        except Exception:
            with self._lock:
                self._status = "error"
                self._error = "Storage could not be measured. Refresh to try again."
                self._completed = time.monotonic()
        else:
            with self._lock:
                self._snapshot = snapshot
                self._status = "ready"
                self._completed = time.monotonic()

    def close(self) -> None:
        with self._lock:
            self._closed = True
        if self._cancel is not None:
            self._cancel()
