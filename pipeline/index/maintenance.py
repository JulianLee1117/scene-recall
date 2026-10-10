"""Explicit, idle-only Lance version pruning without compaction or reindexing.

Run with ``uv run --group maintenance python -m pipeline.index.maintenance``.
The default is a native read-only explanation retaining fourteen days. Apply
requires all API readers and workers to be stopped; shared/lifetime locks make
that requirement enforceable. No inferred manifest/file names are deleted.

Apply first drops the derived tables and files that no current code reads,
listed in ``_RETIRED_TABLE_PREFIXES`` and ``_RETIRED_PATHS``; a dry run lists
them with their size.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager, ExitStack
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import importlib.metadata
import json
import math
from pathlib import Path
import shutil
from typing import Any

from filelock import FileLock, Timeout

from pipeline.config import Config, load_config


_API_READERS_LOCK = ".scene-recall-api-readers.lock"
_STATS_FIELDS = (
    "bytes_removed", "old_versions", "data_files_removed",
    "transaction_files_removed", "index_files_removed", "deletion_files_removed",
)


# Framing's grid caches and compact composition challenger, retired by ADR-0120:
# derived tables and files that no current code reads.
_RETIRED_TABLE_PREFIXES = ("frame_framing_", "frame_composition_")
_RETIRED_PATHS = ("feature-manifests/framing", "search-profiles", "search-builds", ".search-storage.lock")


class MaintenanceUnavailable(RuntimeError):
    """A maintenance prerequisite needs operator attention."""


@dataclass(frozen=True)
class Retention:
    older_than_days: float | None = 14
    retain_versions: int | None = None

    def __post_init__(self) -> None:
        if (self.older_than_days is None) == (self.retain_versions is None):
            raise ValueError("Choose either a retention age or a retained version count")
        if self.older_than_days is not None and (
            not math.isfinite(self.older_than_days) or self.older_than_days <= 0
        ):
            raise ValueError("Retention age must be positive; use --retain-versions 1 for latest only")
        if self.retain_versions is not None and (
            isinstance(self.retain_versions, bool) or not isinstance(self.retain_versions, int)
            or self.retain_versions < 1
        ):
            raise ValueError("At least one latest version must be retained")

    def native_options(self) -> dict[str, Any]:
        return {
            "older_than": (
                timedelta(days=self.older_than_days)
                if self.older_than_days is not None else None
            ),
            "retain_versions": self.retain_versions,
            "delete_unverified": False,
            "error_if_tagged_old_versions": True,
        }


@contextmanager
def api_database_read_lease(config: Config):
    """Hold for the entire API lifetime, including startup and active requests."""
    from pipeline.lab.worker_locks import _shared_legacy_lock

    root = config.paths.assets_dir / "db"
    root.mkdir(parents=True, exist_ok=True)
    with ExitStack() as stack:
        try:
            stack.enter_context(_shared_legacy_lock(root / _API_READERS_LOCK))
        except Timeout as exc:
            raise MaintenanceUnavailable(
                "Database maintenance is active. Start the API after it completes."
            ) from exc
        yield


def _require_native_pruning() -> None:
    try:
        db_version = importlib.metadata.version("lancedb")
        lance_version = importlib.metadata.version("pylance")
    except importlib.metadata.PackageNotFoundError as exc:
        raise MaintenanceUnavailable(
            "Pruning requires the certified optional runtime: "
            "uv run --group maintenance python -m pipeline.index.maintenance"
        ) from exc
    if not db_version.startswith("0.33.") or lance_version != "9.0.0":
        raise MaintenanceUnavailable(
            "This pruning path is certified for LanceDB 0.33.x and pylance 9.0.0; "
            f"found {db_version} and {lance_version}. Validate new versions before pruning."
        )


@contextmanager
def _idle_maintenance(config: Config, db: Any):
    """Exclude workers, independent ingestion, API readers and publication."""
    from pipeline.ingest.locks import global_ingest_lock
    from pipeline.index.writer import _PUBLICATION_LOCK, _database_write_lock
    from pipeline.lab.worker_locks import worker_lock

    with ExitStack() as stack:
        guards = (
            (lambda: worker_lock(config.paths.state_dir / "lab", "all"),
             "Stop both editor and ingest workers gracefully before applying maintenance."),
            (lambda: global_ingest_lock(config.paths.assets_dir),
             "An independent ingest or backfill is active; wait for it to finish."),
            (lambda: FileLock(
                config.paths.assets_dir / "db" / _API_READERS_LOCK,
                timeout=0, preserve_lock_file=True,
            ), "Stop the API before applying maintenance; it may retain older database readers."),
        )
        for guard, message in guards:
            try:
                stack.enter_context(guard())
            except Timeout as exc:
                raise MaintenanceUnavailable(message) from exc
        if not _PUBLICATION_LOCK.acquire(blocking=False):
            raise MaintenanceUnavailable("Database publication is active; retry after it finishes.")
        stack.callback(_PUBLICATION_LOCK.release)
        try:
            stack.enter_context(_database_write_lock(db).acquire(timeout=0))
        except Timeout as exc:
            raise MaintenanceUnavailable("Database publication is active; retry after it finishes.") from exc
        yield


def _stats(value: Any) -> dict[str, int]:
    return {name: int(getattr(value, name)) for name in _STATS_FIELDS}


def _current_identity(table: Any) -> dict[str, Any]:
    indices = sorted(
        (index.name, str(index.index_type), tuple(index.columns))
        for index in table.list_indices()
    )
    return {"version": int(table.version), "rows": int(table.count_rows()), "indices": indices}


def _run(db: Any, retention: Retention, *, apply: bool) -> dict[str, Any]:
    from pipeline.index.writer import table_names

    options = retention.native_options()
    plans = []
    # Explain every table before deleting anything. A tagged-version conflict
    # or unsupported dataset therefore fails before a partial batch is applied.
    for name in sorted(table_names(db)):
        if name.startswith(_RETIRED_TABLE_PREFIXES):
            continue  # dropped whole on apply, never pruned
        table = db.open_table(name)
        identity = _current_identity(table)
        dataset = table.to_lance()
        explanation = dataset.explain_cleanup_old_versions(**options)
        if int(explanation.read_version) != identity["version"]:
            raise MaintenanceUnavailable(f"Table {name} changed during planning; retry.")
        warnings = [str(warning) for warning in explanation.warnings]
        if apply and warnings:
            raise MaintenanceUnavailable(f"Table {name} needs inspection before pruning: {'; '.join(warnings)}")
        plans.append((name, identity, dataset, {
            "table": name,
            "head_version": identity["version"],
            "row_count": identity["rows"],
            "versions_before": len(table.list_versions()),
            "candidate": _stats(explanation.stats),
            "warnings": warnings,
        }))
    report = {
        "mode": "apply" if apply else "dry_run",
        "measured_at": datetime.now(timezone.utc).isoformat(),
        "retention": {"older_than_days": retention.older_than_days, "retain_versions": retention.retain_versions},
        "delete_unverified": False,
        "compaction": False,
        "estimated_bytes_removed": sum(item[3]["candidate"]["bytes_removed"] for item in plans),
        "tables": [item[3] for item in plans],
    }
    if not apply:
        return report
    for name, identity, dataset, item in plans:
        if _current_identity(db.open_table(name)) != identity:
            raise MaintenanceUnavailable(f"Table {name} changed before pruning; maintenance stopped.")
        item["removed"] = _stats(dataset.cleanup_old_versions(**options))
        fresh = db.open_table(name)
        if _current_identity(fresh) != identity:
            raise MaintenanceUnavailable(f"Table {name} changed unexpectedly; inspect the database before restarting.")
        item["versions_after"] = len(fresh.list_versions())
    report["actual_bytes_removed"] = sum(item[3]["removed"]["bytes_removed"] for item in plans)
    return report


def _disk_size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    return sum(child.stat().st_size for child in path.rglob("*") if child.is_file())


def _retired(db: Any, assets: Path) -> dict[str, Any]:
    """The retired derived tables and files present, with their size on disk."""
    from pipeline.index.writer import table_names

    root = assets.resolve()
    tables = sorted(name for name in table_names(db) if name.startswith(_RETIRED_TABLE_PREFIXES))
    paths = []
    for relative in _RETIRED_PATHS:
        path = assets / relative
        if path.is_symlink() or (path.exists() and not path.resolve().is_relative_to(root)):
            raise MaintenanceUnavailable(f"Retired path {path} is a link or leaves the assets directory")
        if path.exists():
            paths.append(path)
    sizes = [assets / "db" / f"{name}.lance" for name in tables] + paths
    return {"tables": tables, "paths": [str(path) for path in paths],
            "bytes": sum(_disk_size(path) for path in sizes if path.exists())}


def _remove_retired(db: Any, retired: dict[str, Any]) -> None:
    for name in retired["tables"]:
        db.drop_table(name)
    for raw in retired["paths"]:
        path = Path(raw)
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()


def maintain_database(config: Config, retention: Retention = Retention(), *, apply: bool = False) -> dict[str, Any]:
    """Explain or apply native pruning to the existing configured database.

    Applying first removes retired derived tables and files (see the module docstring).
    """
    _require_native_pruning()
    root = config.paths.assets_dir / "db"
    if not root.is_dir():
        raise MaintenanceUnavailable(f"Configured database does not exist: {root}")
    import lancedb

    db = lancedb.connect(str(root))
    assets = Path(config.paths.assets_dir)
    if apply:
        with _idle_maintenance(config, db):
            retired = _retired(db, assets)
            _remove_retired(db, retired)
            return {**_run(db, retention, apply=True), "retired_removed": retired}
    return {**_run(db, retention, apply=False), "retired": _retired(db, assets)}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    policy = parser.add_mutually_exclusive_group()
    policy.add_argument("--older-than-days", type=float, default=None)
    policy.add_argument("--retain-versions", type=int, default=None)
    parser.add_argument("--apply", action="store_true", help="Prune only after the API and both workers have stopped")
    args = parser.parse_args(argv)
    try:
        retention = Retention(
            older_than_days=None if args.retain_versions is not None else (
                args.older_than_days if args.older_than_days is not None else 14
            ),
            retain_versions=args.retain_versions,
        )
        print(json.dumps(maintain_database(load_config(), retention, apply=args.apply), indent=2))
    except (MaintenanceUnavailable, ValueError, OSError) as exc:
        parser.exit(2, f"Maintenance unavailable: {exc}\n")


if __name__ == "__main__":
    main()
