"""Explicit removal of one film from the index, optionally rejecting its files too.

Run ``python -m pipeline.index.remove_film FILM_ID --expected-path PATH`` to
inspect the exact target. Apply also requires a new ``--receipt`` JSON path.
By default only index rows go; source media and asset files stay.

``--delete-files`` rejects the release entirely (ADR-0102). After the index
rows, it deletes the source video (only while its content still hashes to
FILM_ID, so a replacement at the same filename is kept), the film's asset
folder and its playback copy. Rerun it to finish a film that is already out of
the index. Jobs, bookmarks and saved edits are never deleted.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Any

from filelock import Timeout

from pipeline.config import Config, load_config
from pipeline.ingest.locks import (
    _FILM_OPERATION_LOCK,
    film_operation_lock,
    global_ingest_lock,
    require_no_pending_film_relink,
)
from pipeline.index.framing_features import (
    publish_framing_manifest,
    resolve_ready_framing_profile,
)
from pipeline.index.text_features import (
    publish_text_index_manifest,
    resolve_ready_text_profile,
)
from pipeline.index.writer import (
    _PUBLICATION_LOCK,
    _database_write_lock,
    _ensure_search_indexes_locked,
    _film_condition,
    table_names,
)
from pipeline.ingest.probe import _content_hash
from pipeline.lab.cleanup import _remove


def _path_key(path: Path | str) -> str:
    # Compare only indexed identity: the user may already have moved the bad
    # release aside, or placed a replacement at the same filename.
    return os.path.normcase(os.path.abspath(path))


def _validate_target(db: Any, film_id: str, expected_path: Path, *,
                     allow_missing: bool = False) -> dict[str, Any] | None:
    if len(film_id) != 64 or any(c not in "0123456789abcdef" for c in film_id):
        raise ValueError("film ID must be 64 lowercase hexadecimal characters")
    if not expected_path.is_absolute():
        raise ValueError("expected source path must be absolute")
    if "films" not in table_names(db):
        raise ValueError("no indexed films table exists")
    rows = (
        db.open_table("films").search().where(_film_condition(film_id))
        .limit(None).to_list()
    )
    if not rows and allow_missing:
        return None   # already withdrawn: only its files remain to delete
    if len(rows) != 1:
        raise ValueError(f"expected exactly one indexed film for {film_id}; found {len(rows)}")
    if _path_key(rows[0]["path"]) != _path_key(expected_path):
        raise ValueError("indexed source path does not match --expected-path")
    return rows[0]


def _file_targets(config: Config, film_id: str, expected_path: Path) -> list[dict[str, Any]]:
    """What --delete-files removes: the rejected source, its asset folder and its playback copy."""
    source: dict[str, Any] = {"kind": "source", "path": str(expected_path), "delete": False}
    if not expected_path.is_file():
        source["reason"] = "already gone"
    elif _content_hash(expected_path) != film_id:
        source["reason"] = "a different file now has this name; it is kept"
    else:
        source.update(delete=True, bytes=expected_path.stat().st_size)
    targets = [source]
    for kind, root in (("assets", config.paths.assets_dir), ("playback", config.paths.playback_dir)):
        if root is None:
            continue   # without a playback_dir, playback copies live inside the asset folder
        folder = root / film_id
        files = [path for path in folder.rglob("*") if path.is_file()] if folder.is_dir() else []
        targets.append({"kind": kind, "path": str(folder), "delete": folder.is_dir(), "files": len(files),
                        "bytes": sum(path.stat().st_size for path in files)})
    return targets


def _delete_locked_files(config: Config, film_id: str, report: dict[str, Any]) -> None:
    """Under the film lock: the verified source and the asset folder's contents except the lock."""
    for target in report["files"]:
        path = Path(target["path"])
        if not target["delete"]:
            continue
        if target["kind"] == "source":
            # Recheck at deletion time; the name may have been reused since the plan.
            if not path.is_file() or _content_hash(path) != film_id:
                target.update(delete=False, reason="changed before deletion; it is kept")
                continue
            _remove(path, path.parent)
            target["deleted"] = True
        elif target["kind"] == "assets":
            for child in list(path.iterdir()):
                if child.name != _FILM_OPERATION_LOCK:
                    _remove(child, config.paths.assets_dir)


def _delete_folders(config: Config, report: dict[str, Any]) -> None:
    """After the film lock is released: the asset folder with its lock file, and the playback copy."""
    for target in report["files"]:
        if target["kind"] == "source" or not target["delete"]:
            continue
        path = Path(target["path"])
        root = config.paths.assets_dir if target["kind"] == "assets" else config.paths.playback_dir
        if path.exists():
            _remove(path, root)
        target["deleted"] = True


def _plan(db: Any, config: Config, film_id: str, expected_path: Path, *, delete_files: bool = False):
    film = _validate_target(db, film_id, expected_path, allow_missing=delete_files)
    text_profile = resolve_ready_text_profile(config, db)
    framing_profile = resolve_ready_framing_profile(config, db)
    tables = []
    for name in sorted(table_names(db) if film is not None else ()):
        # Authored records live outside these canonical/derived index tables.
        # Compiled evidence tables are derived too (artifacts stay on disk).
        if name not in {"films", "units", "frames", "film_meta", "shot_evidence", "scenes", "dialogue_lines"}                 and not name.startswith(("unit_text_", "frame_framing_")):
            continue
        table = db.open_table(name)
        if "film_id" not in table.schema.names:
            raise RuntimeError(f"index table {name} has no film_id; refusing removal")
        tables.append({
            "table": name,
            "version_before": int(table.version),
            "rows_before": int(table.count_rows()),
            "target_rows": int(table.count_rows(_film_condition(film_id))),
        })
    report = {
        "operation": "remove_film_index",
        "status": "dry_run",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "database": str(db.uri),
        "film": film,
        "tables": tables,
        "ready_profiles_before": {
            "text": asdict(text_profile) if text_profile else None,
            "framing": asdict(framing_profile) if framing_profile else None,
        },
        "preserved": (["jobs", "bookmarks", "projects", "download records"] if delete_files
                      else ["source media", "asset files", "jobs", "bookmarks", "projects"]),
    }
    if delete_files:
        report["files"] = _file_targets(config, film_id, expected_path)
    return report, text_profile, framing_profile


def _write_receipt(path: Path, report: dict[str, Any], *, initial: bool = False) -> None:
    payload = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if initial:
        # An existing receipt is an audit record, never an overwrite target.
        with path.open("x", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        return
    temporary = path.with_name(path.name + ".tmp")
    try:
        temporary.write_text(payload, encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _refresh_ready_profiles(db: Any, config: Config, text_profile, framing_profile) -> dict:
    # Exact deletion of the same film from source and derived tables preserves
    # the previously proven coverage for every remaining film. Only profiles
    # ready before the operation qualify; an incomplete cache stays inactive.
    if text_profile is not None:
        publish_text_index_manifest(config, db, text_profile)
        if resolve_ready_text_profile(config, db) != text_profile:
            raise RuntimeError("text profile failed post-removal readiness verification")
    if framing_profile is not None and db.open_table("frames").count_rows() > 0:
        frame_ids = [
            row["frame_id"] for row in db.open_table("frames").search()
            .select(["frame_id"]).limit(None).to_list()
        ]
        publish_framing_manifest(config, db, framing_profile, frame_ids=frame_ids)
        if resolve_ready_framing_profile(config, db) != framing_profile:
            raise RuntimeError("Framing profile failed post-removal readiness verification")
    return {
        "text": (profile.table_name if (profile := resolve_ready_text_profile(config, db)) else None),
        "framing": (profile.table_name if (profile := resolve_ready_framing_profile(config, db)) else None),
    }


def _apply(db: Any, config: Config, film_id: str, report: dict, text_profile,
           framing_profile, receipt: Path) -> dict:
    report["status"] = "applying"
    _write_receipt(receipt, report, initial=True)
    changed = []
    try:
        # Unpublish representative units first and the film record last.
        order = {"units": 0, "frames": 2, "films": 3}
        for item in sorted(report["tables"], key=lambda item: order.get(item["table"], 1)):
            if item["target_rows"] == 0:
                continue
            changed.append(item)
            db.open_table(item["table"]).delete(_film_condition(film_id))
            table = db.open_table(item["table"])
            if table.count_rows(_film_condition(film_id)) != 0 or table.count_rows() != (
                item["rows_before"] - item["target_rows"]
            ):
                raise RuntimeError(f"scoped deletion verification failed for {item['table']}")
            item["version_after"] = int(table.version)
            item["rows_after"] = int(table.count_rows())
        _ensure_search_indexes_locked(db)
        report["ready_profiles_after"] = _refresh_ready_profiles(
            db, config, text_profile, framing_profile,
        )
        # FTS synchronization may itself create another units generation.
        for item in report["tables"]:
            table = db.open_table(item["table"])
            item["version_after"] = int(table.version)
            item["rows_after"] = int(table.count_rows())
        report["status"] = "complete"
        _write_receipt(receipt, report)
        return report
    except BaseException as exc:
        # No other publisher can run while these locks are held. Restoring
        # retained Lance versions therefore cannot discard unrelated writes.
        # A killed process leaves the applying receipt and original versions
        # available for explicit operator recovery before version pruning.
        failures = []
        for item in reversed(changed):
            try:
                db.open_table(item["table"]).restore(item["version_before"])
            except BaseException as restore_exc:
                failures.append(f"{item['table']}: {restore_exc}")
        if not failures:
            try:
                _ensure_search_indexes_locked(db)
                _refresh_ready_profiles(db, config, text_profile, framing_profile)
            except BaseException as restore_exc:
                failures.append(f"profile/index recovery: {restore_exc}")
        report["status"] = "recovery_required" if failures else "rolled_back"
        report["error"] = str(exc)
        report["recovery_errors"] = failures
        _write_receipt(receipt, report)
        raise


def remove_film_index(config: Config, film_id: str, expected_path: Path, *,
                      apply: bool = False, receipt: Path | None = None,
                      delete_files: bool = False) -> dict:
    """Inspect or remove exactly one indexed film; with ``delete_files``, also its owned files."""
    root = config.paths.assets_dir / "db"
    if not root.is_dir():
        raise ValueError(f"configured database does not exist: {root}")
    import lancedb

    db = lancedb.connect(str(root))
    if not apply:
        # The publication guard keeps a dry-run report internally consistent;
        # unlike apply, it need not wait for lengthy model work to finish.
        with _PUBLICATION_LOCK, _database_write_lock(db):
            return _plan(db, config, film_id, expected_path, delete_files=delete_files)[0]
    if receipt is None:
        raise ValueError("apply requires a new --receipt JSON path")
    # Validate the ID before using it to construct the asset lock path.
    _validate_target(db, film_id, expected_path, allow_missing=delete_files)
    try:
        with global_ingest_lock(config.paths.assets_dir):
            with film_operation_lock(config.paths.assets_dir / film_id):
                require_no_pending_film_relink(config.paths.assets_dir / film_id)
                with _PUBLICATION_LOCK, _database_write_lock(db):
                    report, text_profile, framing_profile = _plan(
                        db, config, film_id, expected_path, delete_files=delete_files,
                    )
                    if report["film"] is not None:
                        report = _apply(db, config, film_id, report, text_profile, framing_profile, receipt)
                    else:
                        report["status"] = "already_withdrawn"
                        _write_receipt(receipt, report, initial=True)
                if not delete_files:
                    return report
                try:
                    _delete_locked_files(config, film_id, report)
                except BaseException as exc:
                    _record_pending_files(receipt, report, exc)
                    raise
            # The lock file lives in the asset folder; the global lock still excludes ingestion.
            try:
                _delete_folders(config, report)
            except BaseException as exc:
                _record_pending_files(receipt, report, exc)
                raise
            report["status"] = "complete"
            _write_receipt(receipt, report)
            return report
    except Timeout as exc:
        raise RuntimeError("another ingest or backfill is active; wait for it to finish") from exc


def _record_pending_files(receipt: Path, report: dict[str, Any], exc: BaseException) -> None:
    # The index removal stands; rerunning with --delete-files finishes the files.
    report["status"] = "files_pending"
    report["files_error"] = str(exc)
    _write_receipt(receipt, report)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("film_id")
    parser.add_argument("--expected-path", required=True, type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--receipt", type=Path, help="New audit receipt path, required with --apply")
    parser.add_argument("--delete-files", action="store_true",
                        help="Reject the release: also delete its verified source, asset folder and playback copy")
    args = parser.parse_args(argv)
    try:
        print(json.dumps(remove_film_index(
            load_config(), args.film_id, args.expected_path,
            apply=args.apply, receipt=args.receipt, delete_files=args.delete_files,
        ), indent=2))
    except (OSError, RuntimeError, ValueError) as exc:
        parser.exit(2, f"Film index removal stopped: {exc}\n")


if __name__ == "__main__":
    main()
