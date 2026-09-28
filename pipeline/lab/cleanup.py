"""Small, explicit Lab storage lifecycle; no source or evidence cache eviction.

Run ``python -m pipeline.lab.cleanup`` for a read-only plan, add ``--apply``
to reclaim it. The existing editor worker performs the same maintenance idle.
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sqlite3
import stat
import time
from contextlib import nullcontext
from pathlib import Path

from filelock import FileLock, Timeout

log = logging.getLogger(__name__)
GRACE_SECONDS = 24 * 60 * 60
BATCH_SIZE = 100
UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
OWNER = re.compile(rf"^({UUID})(?:$|[-.])")
INTERMEDIATE = re.compile(r"(?:clip-\d+\.mp4|clip-[01]\.(?:native|flow)\.nut|clips\.txt|audio-filters\.txt|output\.partial\.mp4)")
TRANSITION_INTERMEDIATE = re.compile(r"(?:original\.partial|manifest\.json\.partial)")
TRANSITION_KINDS = {"transition-render", "transition-bridge", "transition-generate"}
OWNED_ROOTS = ("lab/renders", "lab/requests", "matching/results")
ACTIVE = {"queued", "running", "waiting_worker"}


def is_render_intermediate(name, kind=None):
    return bool(INTERMEDIATE.fullmatch(name) or
                (kind in TRANSITION_KINDS and TRANSITION_INTERMEDIATE.fullmatch(name)))


def _owner(path):
    match = OWNER.match(path.name)
    return match[1] if match else None


def _checked(path: Path, root: Path):
    """Check lexical/resolved containment and refuse symlinks/Windows junctions."""
    if path == root or not path.is_relative_to(root):
        raise ValueError("Cleanup target must be inside its configured assets root")
    if path.resolve() == root.resolve() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError(f"Cleanup target escapes its assets root: {path}")
    for entry in (path, *path.parents):
        if entry.exists() or entry.is_symlink():
            info = entry.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                raise ValueError(f"Cleanup refuses linked/reparse paths: {entry}")
        if entry == root:
            break


def _tree(path, root):
    """Inventory before deletion, never walking through a linked directory."""
    _checked(path, root)
    if not path.exists():
        return []
    entries = [path]
    if path.is_dir():
        for child in path.iterdir():
            entries.extend(_tree(child, root))
    return entries


def _remove(path, root):
    entries = _tree(path, root)
    size = sum(entry.stat().st_size for entry in entries if entry.is_file())
    for entry in reversed(entries):
        _checked(entry, root)
        if entry.is_dir():
            entry.rmdir()
        else:
            entry.unlink(missing_ok=True)
    return size


def _assets(store, recorded=None):
    root = Path(recorded).absolute() if recorded else store.assets_dir
    if root is None or not root.is_dir():
        raise OSError("Configured assets storage is unavailable; cleanup will retry")
    return root


def _owned_paths(root, identities=None):
    for name in OWNED_ROOTS:
        directory = root / name
        _checked(directory, root)
        if directory.is_dir():
            for path in directory.iterdir():
                owner = _owner(path)
                if owner and (identities is None or owner in identities):
                    yield path, owner


def drain_cleanup(store, *, job_ids=None, limit=BATCH_SIZE):
    """Retry committed project deletions. Errors retain their durable ticket."""
    report = {"removed_bytes": 0, "completed": 0, "errors": []}
    try:
        with FileLock(store.root / ".cleanup.lock", timeout=0, preserve_lock_file=True):
            with store.connection() as con:
                rows = con.execute("SELECT * FROM artifact_cleanup ORDER BY created_at").fetchall()
            rows = [row for row in rows if job_ids is None or row["job_id"] in job_ids][:limit]
            paths_by_root = {}
            for row in rows:
                try:
                    root = _assets(store, row["assets_root"])
                    identity = row["job_id"]
                    if not re.fullmatch(UUID, identity):
                        raise ValueError("Cleanup ticket has an invalid job identity")
                    with store.connection() as con:
                        if con.execute("SELECT 1 FROM jobs WHERE id=?", (identity,)).fetchone():
                            raise ValueError("Cleanup ticket still belongs to an existing job")
                    if root not in paths_by_root:
                        index = {}
                        for path, owner in _owned_paths(root, {item["job_id"] for item in rows}):
                            index.setdefault(owner, []).append(path)
                        paths_by_root[root] = index
                    for path in paths_by_root[root].get(identity, []):
                        report["removed_bytes"] += _remove(path, root)
                    with store.connection() as con:
                        con.execute("DELETE FROM artifact_cleanup WHERE job_id=?", (identity,))
                    report["completed"] += 1
                except (OSError, ValueError) as exc:
                    report["errors"].append(str(exc))
                    with store.connection() as con:
                        con.execute("UPDATE artifact_cleanup SET error=? WHERE job_id=?", (str(exc)[:1000], row["job_id"]))
    except (OSError, Timeout, sqlite3.Error) as exc:
        report["errors"].append(str(exc))
    return report


def _reason(path, owner, jobs, cutoff, root, *, audio=False, kind=None):
    entries = _tree(path, root)
    if not entries:
        return None
    if audio:
        return "stale decoded audio" if max(p.stat().st_mtime for p in entries) < cutoff else None
    job = jobs.get(owner)
    if job is None:
        return "orphaned job files" if max(p.stat().st_mtime for p in entries) < cutoff else None
    if job not in ACTIVE and is_render_intermediate(path.name, kind):
        return "finished render intermediate"
    return None


def collect_garbage(store, *, apply=False, now=None, limit=BATCH_SIZE):
    """Reconcile legacy leftovers with the ledger; keep live outputs/receipts.

    Each removal rechecks the ledger under a write transaction, so a worker
    cannot claim work between the active-job check and removal. External
    experiments keep their non-UUID namespaces; nothing outside the allowlist
    is considered. The 24h grace protects freshly staged unknown files/audio.
    """
    cutoff = (time.time() if now is None else now) - GRACE_SECONDS
    root = _assets(store)
    report = {"apply": apply, "files": [], "bytes": 0, "errors": [], "busy": False}
    with store.connection() as con:
        records = list(con.execute("SELECT id,status,kind FROM jobs"))
        jobs = {row["id"]: row["status"] for row in records}
        kinds = {row["id"]: row["kind"] for row in records}
    candidates = []
    from pipeline.lab.dialogue_assets import PROFILE as DIALOGUE_PROFILE
    dialogue_audio = root / "lab/dialogue-audio" / DIALOGUE_PROFILE / "audio"
    try:
        for path, owner in _owned_paths(root):
            if owner not in jobs:
                candidates.append((path, owner, False))
            elif jobs[owner] not in ACTIVE and path.parent == root / "lab/renders":
                _checked(path, root)
                if path.is_dir():
                    candidates.extend((child, owner, False) for child in path.iterdir()
                                      if is_render_intermediate(child.name, kinds[owner]))
        audio = root / "lab/audio"
        _checked(audio, root)
        if audio.is_dir():
            candidates.extend((path, None, True) for path in audio.iterdir() if re.fullmatch(r"[0-9a-f]{64}\.wav", path.name))
        _checked(dialogue_audio, root)
        if dialogue_audio.is_dir():
            candidates.extend((path, None, True) for path in dialogue_audio.iterdir() if re.fullmatch(r"[0-9a-f]{64}\.wav", path.name))
    except (OSError, ValueError) as exc:
        report["errors"].append(str(exc))
    for path, owner, audio in candidates:
        if len(report["files"]) >= limit:
            break
        try:
            if path.parent == dialogue_audio:
                _checked(path, root)
                _checked(path.with_suffix(".lock"), root)
            guard = FileLock(path.with_suffix(".lock"), timeout=0, preserve_lock_file=True) if apply and path.parent == dialogue_audio else nullcontext()
            with guard, store.connection() as con:
                if apply:
                    con.execute("BEGIN IMMEDIATE")
                row = con.execute("SELECT status,kind FROM jobs WHERE id=?", (owner,)).fetchone()
                current = {owner: row[0]} if row else {}
                # Audio is shared scratch. Do not touch it while either worker
                # has active editor work, including a cancellation still draining.
                if audio:
                    from pipeline.lab.job_roles import role_for_kind
                    if any(role_for_kind(row["kind"]) == "editor" for row in con.execute("SELECT kind FROM jobs WHERE status IN ('queued','running')")):
                        continue
                reason = _reason(path, owner, current, cutoff, root, audio=audio, kind=row["kind"] if row else None)
                if reason is None:
                    continue
                size = sum(p.stat().st_size for p in _tree(path, root) if p.is_file())
                if apply:
                    _remove(path, root)
                report["files"].append({"path": str(path), "bytes": size, "reason": reason})
                report["bytes"] += size
        except Timeout:
            continue
        except (OSError, ValueError) as exc:
            report["errors"].append(str(exc))
    return report


def maintain_storage(store, *, apply=True):
    """Bounded existing-worker maintenance; report errors without stopping jobs."""
    result = {"deletions": {}, "garbage": {}}
    try:
        if apply:
            result["deletions"] = drain_cleanup(store)
        else:
            with store.connection() as con:
                exists = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='artifact_cleanup'").fetchone()
                result["deletions"] = {"pending": [dict(row) for row in con.execute("SELECT * FROM artifact_cleanup")] if exists else []}
        lock = FileLock(store.root / ".cleanup.lock", timeout=0, preserve_lock_file=True) if apply else nullcontext()
        with lock:
            result["garbage"] = collect_garbage(store, apply=apply)
    except (OSError, ValueError, Timeout, sqlite3.Error) as exc:
        result["garbage"] = {"errors": [str(exc)]}
    for section in result.values():
        for error in section.get("errors", []):
            log.warning("Lab storage cleanup deferred: %s", error)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--apply", action="store_true", help="Delete only the eligible files; otherwise report a dry run")
    args = parser.parse_args(argv)
    from pipeline.config import load_config
    from pipeline.lab.store import LabStore
    config = load_config(args.config)
    store = LabStore(config.paths.state_dir, config.paths.assets_dir)
    if not store.path.is_file():
        parser.error("Lab database is unavailable; start Scene Recall first")
    if args.apply:
        store.initialize()
    result = maintain_storage(store, apply=args.apply)
    print(json.dumps(result, indent=2))
    return int(any(section.get("errors") for section in result.values()))


if __name__ == "__main__":
    raise SystemExit(main())
