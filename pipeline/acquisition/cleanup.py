"""Discard only an explicitly cancelled acquisition's owned download tree."""
from __future__ import annotations

import json
import os
import re
import stat
from pathlib import Path

from pipeline.intake import is_link_or_junction


def _ordinary_path(path: Path, incoming: Path) -> None:
    """Check containment and every component before touching a saved pathname."""
    if not path.resolve().is_relative_to(incoming.resolve()) or path == incoming:
        raise ValueError("Cancellation cleanup path is outside managed download storage")
    for part in (path, *path.parents):
        if is_link_or_junction(part):
            raise ValueError("Cancellation cleanup found a link or junction; files were retained")
        if part == incoming:
            break


def _identity(path: Path) -> dict:
    value = path.lstat()
    return {"device": value.st_dev, "inode": value.st_ino}


def _file_identity(path: Path) -> tuple:
    value = path.lstat()
    if not stat.S_ISREG(value.st_mode):
        raise ValueError("Cancellation cleanup found an unsupported file type")
    return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns


def remove_cancelled_download(incoming: Path, item: dict, record_owner) -> None:
    """Preflight the complete tree; retain its marker until all data is removed.

    The monitor has already confirmed downloader detachment and job teardown.
    An owner identity journal recovers the tiny crash window after removing the
    marker but before removing its empty directory. Locked files leave the
    marker and durable cancellation intent available for the next monitor tick.
    No recursive delete or downloader-controlled file deletion is used.
    """
    incoming = Path(incoming).absolute()
    if not incoming.is_dir() or is_link_or_junction(incoming):
        raise OSError("Incoming storage is unavailable")
    if not re.fullmatch(r"[0-9a-f]{32}", item["id"]):
        raise ValueError("Invalid acquisition identity")
    directory = incoming / ".scene-recall-managed" / item["id"]
    _ordinary_path(directory, incoming)
    if not directory.exists():
        return
    if not directory.is_dir():
        raise ValueError("Cancellation ownership directory is not a directory")
    owner = _identity(directory)
    recorded = item.get("cancellation_owner")
    if recorded is not None and recorded != owner:
        raise ValueError("Cancellation ownership directory changed; files were retained")
    entries = {p.name for p in directory.iterdir()}
    marker = directory / ".acquisition.json"
    if not entries and recorded == owner:
        directory.rmdir()
        return
    if not entries.issubset({"data", ".acquisition.json"}) or ".acquisition.json" not in entries:
        raise ValueError("Cancellation ownership marker or directory contents changed")
    _ordinary_path(marker, incoming)
    marker_identity = _file_identity(marker)
    with marker.open("rb") as stream:
        data = stream.read(1025)
    if len(data) > 1024 or json.loads(data) != {"id": item["id"], "info_hash": item["info_hash"]}:
        raise ValueError("Cancellation ownership could not be verified")

    files, directories = [], []
    root = directory / "data"
    pending = [root] if "data" in entries else []
    count = 0
    while pending:
        folder = pending.pop()
        _ordinary_path(folder, incoming)
        if not folder.is_dir():
            raise ValueError("Cancellation download data is not a directory")
        directories.append((folder, _identity(folder)))
        with os.scandir(folder) as children:
            for child in children:
                count += 1
                if count > 20000:
                    raise ValueError("Cancellation download tree exceeds the cleanup limit")
                path = Path(child.path)
                _ordinary_path(path, incoming)
                if child.is_dir(follow_symlinks=False):
                    pending.append(path)
                else:
                    files.append((path, _file_identity(path)))

    # Persist before the first unlink, so retries can safely finish an empty
    # ownership directory even if the process exits after marker removal.
    if recorded is None:
        record_owner(owner)
    for path, identity in files:
        _ordinary_path(path, incoming)
        if _identity(directory) != owner or _file_identity(path) != identity:
            raise ValueError("Cancellation download changed during cleanup")
        path.unlink()
    for path, identity in reversed(directories):
        _ordinary_path(path, incoming)
        if _identity(directory) != owner or _identity(path) != identity:
            raise ValueError("Cancellation directory changed during cleanup")
        path.rmdir()
    _ordinary_path(marker, incoming)
    if _identity(directory) != owner or _file_identity(marker) != marker_identity:
        raise ValueError("Cancellation ownership marker changed during cleanup")
    marker.unlink()
    directory.rmdir()
