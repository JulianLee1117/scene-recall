"""Physical accounting and reservations for optional local search derivations.

Admission never deletes Lance files. Reclamation belongs to the certified
offline maintenance command, which already protects live readers and workers.
"""
from contextlib import contextmanager
from pathlib import Path
import os
import shutil

from filelock import FileLock


class SearchStorageFull(RuntimeError):
    pass


def storage_paths(config):
    root = Path(config.paths.assets_dir).resolve()
    db = root / "db"
    if db.is_dir():
        for path in db.glob("*.lance"):
            if path.name.startswith(("frame_framing", "frame_composition")):
                yield path
            elif (path / "_indices").is_dir():
                # Conservative: count physical indexes, including retained ones.
                # Never infer that a logical index replacement reclaimed bytes.
                yield path / "_indices"
    for name in ("search-profiles", "search-builds"):
        if (root / name).exists():
            yield root / name


def storage_status(config):
    root = Path(config.paths.assets_dir).resolve()
    used = 0
    for path in storage_paths(config):
        if not path.resolve().is_relative_to(root) or path.is_symlink():
            raise SearchStorageFull("Optional search storage escapes the assets directory")
        for directory, dirs, files in os.walk(path, followlinks=False):
            for name in [*dirs, *files]:
                child = Path(directory) / name
                if child.is_symlink() or not child.resolve().is_relative_to(root):
                    raise SearchStorageFull("Optional search storage contains an external link")
            for name in files:
                used += (Path(directory) / name).stat().st_size
    budget = int(config.retrieval.optional_storage_gib) * 1024**3
    return {"used_bytes": used, "budget_bytes": budget, "available_bytes": max(0, budget - used)}


@contextmanager
def reserve_search_storage(config, expected_bytes):
    """Serialize optional allocations, including temporary and retained bytes."""
    if type(expected_bytes) is not int or expected_bytes < 0:
        raise ValueError("Expected allocation must be a nonnegative byte count")
    root = Path(config.paths.assets_dir)
    root.mkdir(parents=True, exist_ok=True)
    with FileLock(root / ".search-storage.lock", timeout=30):
        status = storage_status(config)
        free = shutil.disk_usage(root).free
        if expected_bytes > status["available_bytes"] or free < expected_bytes + 1024**3:
            raise SearchStorageFull(
                "Optional search preparation paused: storage budget or free space is insufficient. "
                "Inspect search storage and run eligible offline maintenance before resuming."
            )
        yield status
        actual = storage_status(config)
        if actual["used_bytes"] > actual["budget_bytes"]:
            raise SearchStorageFull("Optional search storage reached its physical limit; further preparation is paused")


def discard_spatial_cache(config, table_name, *, apply=False):
    """Explicit cache eviction using Lance APIs under existing idle-only guards.

    This never drops compact retrieval evidence, text features or source tables.
    Readiness falls back to live scoring; actual physical reclamation is measured.
    """
    from pipeline.index.writer import open_db, table_names
    from pipeline.index.maintenance import _idle_maintenance
    import re
    if not isinstance(table_name, str) or not re.fullmatch(r"frame_framing_[A-Za-z0-9_]+", table_name):
        raise ValueError("Choose an exact optional spatial cache table")
    db = open_db(config)
    if table_name not in table_names(db):
        return {"table": table_name, "present": False, "removed_bytes": 0}
    expected = (Path(config.paths.assets_dir) / "db" / (table_name + ".lance")).resolve()
    if expected.parent != (Path(config.paths.assets_dir) / "db").resolve():
        raise ValueError("Cache table path escapes the configured database")
    before = storage_status(config)
    result = {"table": table_name, "present": True, "apply": apply, "before": before}
    if apply:
        # Match writer lock ordering: storage admission, then publication.
        with FileLock(Path(config.paths.assets_dir) / ".search-storage.lock", timeout=30), _idle_maintenance(config, db):
            db.drop_table(table_name)
        after = storage_status(config)
        result.update(after=after, removed_bytes=max(0, before["used_bytes"] - after["used_bytes"]))
    return result


def retire_composition_profile(config, identity, *, apply=False):
    """Remove one explicitly chosen inactive derivation after jobs/readers stop."""
    from pipeline.index.composition import load_profile, profile_directory
    from pipeline.index.maintenance import _idle_maintenance
    from pipeline.index.writer import open_db
    from pipeline.lab.store import LabStore
    import json
    if identity == config.retrieval.composition_profile:
        raise ValueError("Select another composition profile or null before retiring this profile")
    profile, _ = load_profile(config, identity)
    directory = profile_directory(config, identity).resolve()
    allowed = {"profile.json", "projection.npy", "sample.json", "coverage.json", "promotion.json"}
    if directory.parent != (Path(config.paths.assets_dir) / "search-profiles").resolve():
        raise ValueError("Profile path escapes managed search storage")
    if any(path.name not in allowed or not path.is_file() or path.is_symlink() for path in directory.iterdir()):
        raise ValueError("Profile contains unknown files; inspect before retirement")
    store = LabStore(config.paths.state_dir, config.paths.assets_dir)
    def check_jobs():
        if store.path.exists():
            with store.connection() as con:
                rows = con.execute("SELECT snapshot FROM jobs WHERE kind='prepare-search-features' AND status IN ('queued','running','waiting_worker','interrupted') AND cancel_requested=0")
                if any(json.loads(row["snapshot"]).get("search_features", {}).get("composition_profile") == identity for row in rows):
                    raise ValueError("Cancel pending preparation jobs for this profile before retirement")
    check_jobs()
    before = storage_status(config)
    result = {"profile_id": identity, "apply": apply, "before": before}
    if apply:
        db = open_db(config)
        with FileLock(Path(config.paths.assets_dir) / ".search-storage.lock", timeout=30), _idle_maintenance(config, db):
            check_jobs()
            db.drop_table(profile.table_name, ignore_missing=True)
            # Known immutable profile artifacts only; no recursive deletion.
            for path in directory.iterdir():
                if path.name not in allowed or path.is_symlink():
                    raise ValueError("Profile contents changed; retirement stopped")
                path.unlink()
            directory.rmdir()
        after = storage_status(config)
        result.update(after=after, removed_bytes=max(0, before["used_bytes"] - after["used_bytes"]))
    return result
