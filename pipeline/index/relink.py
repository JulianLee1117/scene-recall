"""Hash-verified, metadata-only relocation of indexed raw film sources.

Relinking never copies, moves, or deletes a movie.  It verifies that an
already-copied destination is byte-for-byte identical to the indexed source,
updates relocation-sensitive cache identities, and finally changes the one
authoritative ``films.path`` row.  Units, frames, vectors, and derived media
are deliberately untouched.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
from typing import Any
from uuid import uuid4

import lancedb
from lancedb.expr import col, lit

from pipeline.config import VIDEO_EXTENSIONS, Config
from pipeline.ingest.locks import film_operation_lock, film_relink_journal_path
from pipeline.ingest.media import (
    _MEDIA_CACHE_SCHEMA_VERSION,
    _media_manifest_path,
)
from pipeline.ingest.probe import _content_hash
from pipeline.index.writer import published_film_ids, update_film_source


_HASH_CHUNK_BYTES = 4 * 1024 * 1024
_RELINK_JOURNAL_SCHEMA_VERSION = 1
_WINDOWS_MOVE_FILE_EX: Any = None


class FilmRelinkError(RuntimeError):
    """Raised when a source relocation cannot be proven safe."""


@dataclass(frozen=True)
class SourceSnapshot:
    """Stable source-file evidence captured during relocation planning."""

    path: Path
    size_bytes: int
    mtime_ns: int
    sha256: str


@dataclass(frozen=True)
class CacheChange:
    """One validated atomic JSON replacement."""

    path: Path
    before: bytes = field(repr=False)
    after: bytes = field(repr=False)


@dataclass(frozen=True)
class FilmRelinkPlan:
    """Complete, write-free evidence and edits for one film relocation."""

    film_id: str
    old_path: Path
    new_path: Path
    old_title: str
    new_title: str
    sha256: str
    shot_cache_changes: int
    media_manifest_changes: int
    cache_changes: tuple[CacheChange, ...] = field(repr=False)
    asset_dir: Path = field(repr=False)
    old_snapshot: SourceSnapshot = field(repr=False)
    new_snapshot: SourceSnapshot = field(repr=False)
    expected_db_path: str = field(repr=False)

    @property
    def is_noop(self) -> bool:
        """Return whether applying this plan would change no durable state."""
        return (
            _path_key(self.old_path) == _path_key(self.new_path)
            and self.old_title == self.new_title
            and not self.cache_changes
        )


def plan_film_relink(
    db: lancedb.DBConnection,
    config: Config,
    new_path: Path,
    *,
    expected_film_id: str | None = None,
    title: str | None = None,
    title_from_filename: bool = False,
) -> FilmRelinkPlan:
    """Validate a copied movie and prepare all relocation metadata changes."""
    if title is not None and title_from_filename:
        raise FilmRelinkError(
            "title and title_from_filename are mutually exclusive"
        )

    destination = _resolve_video_file(new_path, label="destination")
    film_id = _stable_content_hash(destination)
    if expected_film_id is not None and film_id != expected_film_id:
        raise FilmRelinkError(
            f"destination film ID {film_id} does not match expected "
            f"{expected_film_id}"
        )
    if film_id not in published_film_ids(db):
        raise FilmRelinkError(
            f"destination content is not a fully indexed film: {film_id}"
        )

    rows = (
        db.open_table("films")
        .search()
        .where(col("film_id") == lit(film_id))
        .limit(2)
        .to_list()
    )
    if len(rows) != 1:
        raise FilmRelinkError(
            f"expected exactly one films row for {film_id}, found {len(rows)}"
        )
    row = dict(rows[0])
    expected_db_path = str(row.get("path") or "")
    if not expected_db_path:
        raise FilmRelinkError("indexed film row has no source path")
    _require_destination_unclaimed(db, film_id, destination)

    old_path = _resolve_video_file(Path(expected_db_path), label="indexed source")
    new_snapshot = _snapshot(destination)
    old_snapshot = (
        new_snapshot
        if _path_key(old_path) == _path_key(destination)
        else _snapshot(old_path)
    )
    if old_snapshot.size_bytes != new_snapshot.size_bytes:
        raise FilmRelinkError(
            "source and destination sizes differ: "
            f"{old_snapshot.size_bytes} != {new_snapshot.size_bytes}"
        )
    if old_snapshot.sha256 != new_snapshot.sha256:
        raise FilmRelinkError(
            "full SHA-256 mismatch; destination is not an exact source copy"
        )

    old_title = str(row.get("title") or "")
    if title_from_filename:
        new_title = destination.stem.strip()
    elif title is not None:
        new_title = title.strip()
    else:
        new_title = old_title
    if not new_title.strip():
        raise FilmRelinkError("film title cannot be empty")

    asset_dir = (config.paths.assets_dir / film_id).resolve()
    changes, shot_change_count, manifest_change_count = _plan_cache_changes(
        asset_dir,
        film_id,
        old_snapshot,
        new_snapshot,
    )
    return FilmRelinkPlan(
        film_id=film_id,
        old_path=old_path,
        new_path=destination,
        old_title=old_title,
        new_title=new_title,
        sha256=new_snapshot.sha256,
        shot_cache_changes=shot_change_count,
        media_manifest_changes=manifest_change_count,
        cache_changes=changes,
        asset_dir=asset_dir,
        old_snapshot=old_snapshot,
        new_snapshot=new_snapshot,
        expected_db_path=expected_db_path,
    )


def relink_film(
    db: lancedb.DBConnection,
    config: Config,
    new_path: Path,
    *,
    expected_film_id: str | None = None,
    title: str | None = None,
    title_from_filename: bool = False,
    apply: bool = False,
) -> FilmRelinkPlan:
    """Plan or apply a safe source relink, serialized with film ingest."""
    if not apply:
        return plan_film_relink(
            db,
            config,
            new_path,
            expected_film_id=expected_film_id,
            title=title,
            title_from_filename=title_from_filename,
        )

    # Hash only enough to choose the content-addressed operation lock, then do
    # the complete plan while holding the same lock used by ingest. This avoids
    # reading half-published cache metadata from an in-flight pipeline run.
    destination = _resolve_video_file(new_path, label="destination")
    candidate_film_id = _stable_content_hash(destination)
    if (
        expected_film_id is not None
        and candidate_film_id != expected_film_id
    ):
        raise FilmRelinkError(
            f"destination film ID {candidate_film_id} does not match expected "
            f"{expected_film_id}"
        )
    if candidate_film_id not in published_film_ids(db):
        raise FilmRelinkError(
            "destination content is not a fully indexed film: "
            f"{candidate_film_id}"
        )
    asset_dir = (config.paths.assets_dir / candidate_film_id).resolve()
    with film_operation_lock(asset_dir):
        _recover_pending_relink(db, asset_dir, candidate_film_id)
        locked_plan = plan_film_relink(
            db,
            config,
            destination,
            expected_film_id=expected_film_id,
            title=title,
            title_from_filename=title_from_filename,
        )
        if locked_plan.film_id != candidate_film_id:
            raise FilmRelinkError(
                "destination film identity changed while waiting for its "
                "relink lock"
            )
        _apply_film_relink(db, locked_plan)
        return locked_plan


def recover_film_relink(
    db: lancedb.DBConnection,
    config: Config,
    film_id: str,
) -> str | None:
    """Recover an interrupted relink without requiring either movie path."""
    if len(film_id) != 64 or any(
        character not in "0123456789abcdef" for character in film_id
    ):
        raise FilmRelinkError("film ID must be 64 lowercase hexadecimal characters")
    asset_dir = (config.paths.assets_dir / film_id).resolve()
    with film_operation_lock(asset_dir):
        return _recover_pending_relink(db, asset_dir, film_id)


def _apply_film_relink(
    db: lancedb.DBConnection,
    plan: FilmRelinkPlan,
) -> None:
    """Journal and publish cache edits, then commit the authoritative DB path."""
    _require_unchanged(plan.old_snapshot)
    _require_unchanged(plan.new_snapshot)
    if plan.is_noop:
        return

    journal_path = film_relink_journal_path(plan.asset_dir)
    if journal_path.exists():
        raise FilmRelinkError(
            f"pending relink journal was not recovered: {journal_path}"
        )
    for change in plan.cache_changes:
        try:
            current = change.path.read_bytes()
        except OSError as exc:
            raise FilmRelinkError(
                f"cannot revalidate cache before relink: {change.path}"
            ) from exc
        if current != change.before:
            raise FilmRelinkError(
                f"cache changed after relocation was planned: {change.path}"
            )

    transaction = _make_relink_transaction(plan)
    _write_relink_transaction(journal_path, transaction)
    try:
        for change in plan.cache_changes:
            _atomic_write(change.path, change.after)
        _require_unchanged(plan.old_snapshot)
        _require_unchanged(plan.new_snapshot)
        update_film_source(
            db,
            plan.film_id,
            expected_old_path=plan.expected_db_path,
            new_path=str(plan.new_path),
            title=plan.new_title,
        )
        _require_unchanged(plan.new_snapshot)
        _durable_unlink(journal_path)
    except Exception as exc:
        try:
            outcome = _recover_pending_relink(db, plan.asset_dir, plan.film_id)
        except Exception as recovery_exc:
            raise FilmRelinkError(
                "film relink failed and automatic recovery could not determine "
                f"the committed state; journal retained at {journal_path}: "
                f"{recovery_exc}"
            ) from exc
        if outcome == "new":
            # The database merge committed and only a later verification or
            # cleanup step failed. Recovery rolled every cache forward, so the
            # requested state is complete despite the intermediate exception.
            return
        raise


def _make_relink_transaction(plan: FilmRelinkPlan) -> dict[str, Any]:
    """Build a self-contained undo/redo journal for one prepared plan."""
    root = plan.asset_dir.resolve()
    entries: list[dict[str, str]] = []
    for change in plan.cache_changes:
        try:
            relative = change.path.resolve().relative_to(root)
        except ValueError as exc:
            raise FilmRelinkError(
                f"relink cache is outside the film asset directory: {change.path}"
            ) from exc
        entries.append(
            {
                "path": relative.as_posix(),
                "before": base64.b64encode(change.before).decode("ascii"),
                "after": base64.b64encode(change.after).decode("ascii"),
                "before_sha256": hashlib.sha256(change.before).hexdigest(),
                "after_sha256": hashlib.sha256(change.after).hexdigest(),
            }
        )
    return {
        "schema_version": _RELINK_JOURNAL_SCHEMA_VERSION,
        "film_id": plan.film_id,
        "old_db_path": plan.expected_db_path,
        "new_db_path": str(plan.new_path),
        "old_title": plan.old_title,
        "new_title": plan.new_title,
        "source_sha256": plan.sha256,
        "old_source": {
            "path": str(plan.old_snapshot.path),
            "size_bytes": plan.old_snapshot.size_bytes,
            "mtime_ns": plan.old_snapshot.mtime_ns,
        },
        "new_source": {
            "path": str(plan.new_snapshot.path),
            "size_bytes": plan.new_snapshot.size_bytes,
            "mtime_ns": plan.new_snapshot.mtime_ns,
        },
        "cache_changes": entries,
    }


def _write_relink_transaction(path: Path, transaction: dict[str, Any]) -> None:
    """Atomically persist a complete relink journal."""
    payload = json.dumps(
        transaction,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    _atomic_write(path, payload)


def _recover_pending_relink(
    db: lancedb.DBConnection,
    asset_dir: Path,
    expected_film_id: str,
) -> str | None:
    """Recover a pending transaction using the film row as commit oracle.

    Returns ``"old"`` after an exact rollback, ``"new"`` after an exact
    roll-forward, or ``None`` when no journal exists. Any ambiguous or corrupt
    state is left untouched so an operator can inspect the retained journal.
    """
    journal_path = film_relink_journal_path(asset_dir)
    if not journal_path.exists():
        return None
    transaction, entries = _read_relink_transaction(
        journal_path,
        asset_dir,
        expected_film_id,
    )
    row = _read_single_film_row(db, expected_film_id)
    current_state = (str(row.get("path") or ""), str(row.get("title") or ""))
    old_state = (
        transaction["old_db_path"],
        transaction["old_title"],
    )
    new_state = (
        transaction["new_db_path"],
        transaction["new_title"],
    )
    old_matches = current_state == old_state
    new_matches = current_state == new_state
    if old_matches == new_matches:
        raise FilmRelinkError(
            "pending relink database state matches neither unique journal "
            f"endpoint for film {expected_film_id}"
        )
    outcome = "new" if new_matches else "old"
    selected_snapshot = _validate_recovery_source(transaction, outcome)

    # Validate every live cache before touching any of them. Atomic replacement
    # means an interrupted operation can only leave an exact before/after mix;
    # any third value indicates an unrelated edit and must not be clobbered.
    current_payloads: dict[Path, bytes] = {}
    for path, before, after in entries:
        try:
            current = path.read_bytes()
        except OSError as exc:
            raise FilmRelinkError(
                f"cannot inspect cache during relink recovery: {path}"
            ) from exc
        if current not in (before, after):
            raise FilmRelinkError(
                f"cache no longer matches either journal state: {path}"
            )
        current_payloads[path] = current

    target_index = 2 if new_matches else 1
    for entry in entries:
        path = entry[0]
        target = entry[target_index]
        if current_payloads[path] != target:
            _atomic_write(path, target)
    for path, before, after in entries:
        expected = after if new_matches else before
        if path.read_bytes() != expected:
            raise FilmRelinkError(
                f"cache recovery verification failed: {path}"
            )

    verified_row = _read_single_film_row(db, expected_film_id)
    verified_state = (
        str(verified_row.get("path") or ""),
        str(verified_row.get("title") or ""),
    )
    expected_state = new_state if new_matches else old_state
    if verified_state != expected_state:
        raise FilmRelinkError(
            "film database state changed during pending relink recovery"
        )
    _require_unchanged(selected_snapshot)
    _durable_unlink(journal_path)
    return outcome


def _read_relink_transaction(
    journal_path: Path,
    asset_dir: Path,
    expected_film_id: str,
) -> tuple[dict[str, Any], tuple[tuple[Path, bytes, bytes], ...]]:
    """Read and strictly validate an embedded relink undo/redo journal."""
    try:
        payload = json.loads(journal_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FilmRelinkError(
            f"cannot read pending relink journal: {journal_path}"
        ) from exc
    if not isinstance(payload, dict):
        raise FilmRelinkError("pending relink journal is not a JSON object")
    if payload.get("schema_version") != _RELINK_JOURNAL_SCHEMA_VERSION:
        raise FilmRelinkError("unsupported pending relink journal schema")
    if payload.get("film_id") != expected_film_id:
        raise FilmRelinkError("pending relink journal belongs to another film")
    for field_name in (
        "old_db_path",
        "new_db_path",
        "old_title",
        "new_title",
        "source_sha256",
    ):
        if not isinstance(payload.get(field_name), str):
            raise FilmRelinkError(
                f"pending relink journal has invalid {field_name}"
            )
    source_digest = payload["source_sha256"]
    if len(source_digest) != 64 or any(
        character not in "0123456789abcdef" for character in source_digest
    ):
        raise FilmRelinkError("pending relink journal has invalid source SHA-256")
    for endpoint in ("old", "new"):
        source = payload.get(f"{endpoint}_source")
        if not isinstance(source, dict):
            raise FilmRelinkError(
                f"pending relink journal has invalid {endpoint} source"
            )
        source_path = source.get("path")
        size_bytes = source.get("size_bytes")
        mtime_ns = source.get("mtime_ns")
        if (
            not isinstance(source_path, str)
            or not source_path
            or not isinstance(size_bytes, int)
            or isinstance(size_bytes, bool)
            or size_bytes < 0
            or not isinstance(mtime_ns, int)
            or isinstance(mtime_ns, bool)
            or mtime_ns < 0
        ):
            raise FilmRelinkError(
                f"pending relink journal has invalid {endpoint} source evidence"
            )
        if _path_key(Path(source_path)) != _path_key(
            Path(payload[f"{endpoint}_db_path"])
        ):
            raise FilmRelinkError(
                f"pending relink {endpoint} source does not match its DB path"
            )
    changes = payload.get("cache_changes")
    if not isinstance(changes, list):
        raise FilmRelinkError("pending relink journal has no cache change list")

    root = asset_dir.resolve()
    entries: list[tuple[Path, bytes, bytes]] = []
    seen: set[Path] = set()
    for index, item in enumerate(changes):
        if not isinstance(item, dict):
            raise FilmRelinkError(
                f"pending relink cache entry {index} is not an object"
            )
        relative_text = item.get("path")
        if not isinstance(relative_text, str) or not relative_text:
            raise FilmRelinkError(
                f"pending relink cache entry {index} has an invalid path"
            )
        relative = Path(relative_text)
        if relative.is_absolute() or ".." in relative.parts:
            raise FilmRelinkError(
                f"pending relink cache path escapes the asset directory: "
                f"{relative_text}"
            )
        if not _is_relink_cache_path(relative):
            raise FilmRelinkError(
                f"pending relink cache path is not an allowed identity file: "
                f"{relative_text}"
            )
        path = (root / relative).resolve()
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise FilmRelinkError(
                f"pending relink cache path escapes the asset directory: "
                f"{relative_text}"
            ) from exc
        if path in seen:
            raise FilmRelinkError(
                f"pending relink journal repeats cache path: {relative_text}"
            )
        seen.add(path)
        try:
            before = base64.b64decode(item["before"], validate=True)
            after = base64.b64decode(item["after"], validate=True)
        except (KeyError, TypeError, ValueError) as exc:
            raise FilmRelinkError(
                f"pending relink cache entry {index} has invalid payloads"
            ) from exc
        if hashlib.sha256(before).hexdigest() != item.get("before_sha256"):
            raise FilmRelinkError(
                f"pending relink cache entry {index} failed before hash"
            )
        if hashlib.sha256(after).hexdigest() != item.get("after_sha256"):
            raise FilmRelinkError(
                f"pending relink cache entry {index} failed after hash"
            )
        entries.append((path, before, after))
    return payload, tuple(entries)


def _is_relink_cache_path(relative: Path) -> bool:
    """Whitelist the only cache identities a source relocation may edit."""
    if relative.parts == ("shots.json",):
        return True
    if len(relative.parts) != 2 or relative.parts[0] != "media-manifests":
        return False
    filename = relative.parts[1]
    if not filename.endswith(".json"):
        return False
    stem = filename[:-5]
    return len(stem) == 32 and all(
        character in "0123456789abcdef" for character in stem
    )


def _read_single_film_row(
    db: lancedb.DBConnection,
    film_id: str,
) -> dict[str, Any]:
    rows = (
        db.open_table("films")
        .search()
        .where(col("film_id") == lit(film_id))
        .limit(2)
        .to_list()
    )
    if len(rows) != 1:
        raise FilmRelinkError(
            f"expected exactly one films row for {film_id}, found {len(rows)}"
        )
    return dict(rows[0])


def _validate_recovery_source(
    transaction: dict[str, Any],
    endpoint: str,
) -> SourceSnapshot:
    """Require the DB-selected raw movie to match the journal's full proof."""
    evidence = transaction[f"{endpoint}_source"]
    source_path = _resolve_video_file(
        Path(evidence["path"]),
        label=f"journal {endpoint} source",
    )
    try:
        snapshot = _snapshot(source_path)
    except OSError as exc:
        raise FilmRelinkError(
            f"cannot hash journal {endpoint} source: {source_path}"
        ) from exc
    if (
        snapshot.size_bytes != evidence["size_bytes"]
        or snapshot.mtime_ns != evidence["mtime_ns"]
        or snapshot.sha256 != transaction["source_sha256"]
    ):
        raise FilmRelinkError(
            f"journal {endpoint} source no longer matches its validated copy"
        )
    return snapshot


def _resolve_video_file(path: Path, *, label: str) -> Path:
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise FilmRelinkError(f"{label} does not exist: {path}") from exc
    if not resolved.is_file():
        raise FilmRelinkError(f"{label} is not a regular file: {resolved}")
    if resolved.suffix.lower() not in VIDEO_EXTENSIONS:
        raise FilmRelinkError(f"{label} is not a supported video: {resolved}")
    return resolved


def _require_destination_unclaimed(
    db: lancedb.DBConnection,
    film_id: str,
    destination: Path,
) -> None:
    """Fail planning if another film row already owns this source path."""
    rows = (
        db.open_table("films")
        .search()
        .select(["film_id", "path"])
        .limit(None)
        .to_list()
    )
    destination_key = _path_key(destination)
    conflicts = [
        row
        for row in rows
        if str(row.get("film_id") or "") != film_id
        and str(row.get("path") or "")
        and _path_key(Path(str(row["path"]))) == destination_key
    ]
    if conflicts:
        raise FilmRelinkError(
            "another indexed film already uses the destination path: "
            f"{destination}"
        )


def _stable_content_hash(path: Path) -> str:
    before = path.stat()
    content_hash = _content_hash(path)
    after = path.stat()
    if _stat_key(before) != _stat_key(after):
        raise FilmRelinkError(f"source changed while hashing: {path}")
    return content_hash


def _snapshot(path: Path) -> SourceSnapshot:
    before = path.stat()
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_HASH_CHUNK_BYTES), b""):
            digest.update(chunk)
    after = path.stat()
    if _stat_key(before) != _stat_key(after):
        raise FilmRelinkError(f"source changed while hashing: {path}")
    return SourceSnapshot(
        path=path,
        size_bytes=after.st_size,
        mtime_ns=after.st_mtime_ns,
        sha256=digest.hexdigest(),
    )


def _require_unchanged(snapshot: SourceSnapshot) -> None:
    try:
        current = snapshot.path.stat()
    except OSError as exc:
        raise FilmRelinkError(
            f"source disappeared after validation: {snapshot.path}"
        ) from exc
    if _stat_key(current) != (snapshot.size_bytes, snapshot.mtime_ns):
        raise FilmRelinkError(
            f"source changed after validation: {snapshot.path}"
        )


def _stat_key(stat: os.stat_result) -> tuple[int, int]:
    return stat.st_size, stat.st_mtime_ns


def _path_key(path: Path) -> str:
    return os.path.normcase(os.path.abspath(os.fspath(path)))


def _plan_cache_changes(
    asset_dir: Path,
    film_id: str,
    old_source: SourceSnapshot,
    new_source: SourceSnapshot,
) -> tuple[tuple[CacheChange, ...], int, int]:
    shots_path = asset_dir / "shots.json"
    shots_before, shots_payload = _read_json_object(shots_path)
    recipe = shots_payload.get("recipe")
    shot_rows = shots_payload.get("shots")
    if not isinstance(recipe, dict) or not isinstance(shot_rows, list):
        raise FilmRelinkError(f"invalid shot cache structure: {shots_path}")

    changes: list[CacheChange] = []
    shot_change_count = 0
    shot_state = _shot_source_state(recipe, film_id, old_source, new_source)
    if shot_state == "old" and _path_key(old_source.path) != _path_key(new_source.path):
        updated_shots = dict(shots_payload)
        updated_recipe = dict(recipe)
        updated_recipe.update(
            {
                "source_path": str(new_source.path),
                "source_size": new_source.size_bytes,
                "source_mtime_ns": new_source.mtime_ns,
            }
        )
        updated_shots["recipe"] = updated_recipe
        shots_after = json.dumps(
            updated_shots,
            ensure_ascii=False,
            indent=2,
        ).encode("utf-8")
        changes.append(CacheChange(shots_path, shots_before, shots_after))
        shot_change_count = 1

    shot_ids: list[str] = []
    for index, row in enumerate(shot_rows):
        if not isinstance(row, dict) or not isinstance(row.get("shot_id"), str):
            raise FilmRelinkError(
                f"shot cache row {index} has no valid shot_id: {shots_path}"
            )
        shot_ids.append(row["shot_id"])
    if len(set(shot_ids)) != len(shot_ids):
        raise FilmRelinkError(f"shot cache contains duplicate shot IDs: {shots_path}")

    manifest_dir = asset_dir / "media-manifests"
    manifest_change_count = 0
    for shot_id in shot_ids:
        manifest_path = _media_manifest_path(manifest_dir, shot_id)
        before, payload = _read_json_object(manifest_path)
        identity = payload.get("identity")
        source = identity.get("source") if isinstance(identity, dict) else None
        if not isinstance(identity, dict) or not isinstance(source, dict):
            raise FilmRelinkError(
                f"invalid media manifest identity: {manifest_path}"
            )
        shot_identity = identity.get("shot")
        if (
            payload.get("schema_version") != _MEDIA_CACHE_SCHEMA_VERSION
            or identity.get("schema_version") != _MEDIA_CACHE_SCHEMA_VERSION
            or not isinstance(shot_identity, dict)
            or shot_identity.get("shot_id") != shot_id
        ):
            raise FilmRelinkError(
                f"media manifest schema or shot identity is invalid: "
                f"{manifest_path}"
            )
        state = _media_source_state(source, film_id, old_source, new_source)
        if state != "old" or _path_key(old_source.path) == _path_key(new_source.path):
            continue

        updated_payload = dict(payload)
        updated_identity = dict(identity)
        updated_source = dict(source)
        updated_source.update(
            {
                "film_id": film_id,
                "path": str(new_source.path),
                "size_bytes": new_source.size_bytes,
                "mtime_ns": new_source.mtime_ns,
            }
        )
        updated_identity["source"] = updated_source
        updated_payload["identity"] = updated_identity
        after = json.dumps(
            updated_payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        changes.append(CacheChange(manifest_path, before, after))
        manifest_change_count += 1

    return tuple(changes), shot_change_count, manifest_change_count


def _shot_source_state(
    recipe: dict[str, Any],
    film_id: str,
    old_source: SourceSnapshot,
    new_source: SourceSnapshot,
) -> str:
    if recipe.get("film_id") != film_id:
        raise FilmRelinkError("shot cache belongs to a different film")
    current = {
        "path": recipe.get("source_path"),
        "size": recipe.get("source_size"),
        "mtime": recipe.get("source_mtime_ns"),
    }
    return _source_state(current, old_source, new_source, label="shot cache")


def _media_source_state(
    source: dict[str, Any],
    film_id: str,
    old_source: SourceSnapshot,
    new_source: SourceSnapshot,
) -> str:
    if source.get("film_id") != film_id:
        raise FilmRelinkError("media manifest belongs to a different film")
    current = {
        "path": source.get("path"),
        "size": source.get("size_bytes"),
        "mtime": source.get("mtime_ns"),
    }
    return _source_state(current, old_source, new_source, label="media manifest")


def _source_state(
    current: dict[str, Any],
    old_source: SourceSnapshot,
    new_source: SourceSnapshot,
    *,
    label: str,
) -> str:
    old_expected = {
        "path": str(old_source.path),
        "size": old_source.size_bytes,
        "mtime": old_source.mtime_ns,
    }
    new_expected = {
        "path": str(new_source.path),
        "size": new_source.size_bytes,
        "mtime": new_source.mtime_ns,
    }
    if current == new_expected:
        return "new"
    if current == old_expected:
        return "old"
    raise FilmRelinkError(
        f"{label} source identity matches neither indexed nor destination source"
    )


def _read_json_object(path: Path) -> tuple[bytes, dict[str, Any]]:
    try:
        raw = path.read_bytes()
        payload = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FilmRelinkError(f"cannot read valid cache JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise FilmRelinkError(f"cache JSON is not an object: {path}")
    return raw, payload


def _atomic_write(path: Path, payload: bytes) -> None:
    temporary = path.with_name(f".{uuid4().hex}.json")
    try:
        with temporary.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        _durable_replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _durable_replace(source: Path, destination: Path) -> None:
    """Atomically replace a file and flush its directory entry when possible."""
    if os.name == "nt":
        import ctypes

        global _WINDOWS_MOVE_FILE_EX
        if _WINDOWS_MOVE_FILE_EX is None:
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            move_file_ex = kernel32.MoveFileExW
            move_file_ex.argtypes = [
                ctypes.c_wchar_p,
                ctypes.c_wchar_p,
                ctypes.c_uint32,
            ]
            move_file_ex.restype = ctypes.c_int
            _WINDOWS_MOVE_FILE_EX = move_file_ex
        replace_existing = 0x1
        write_through = 0x8
        if not _WINDOWS_MOVE_FILE_EX(
            str(source),
            str(destination),
            replace_existing | write_through,
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        return
    source.replace(destination)
    _fsync_parent(destination)


def _durable_unlink(path: Path) -> None:
    """Durably remove the canonical journal marker after verified completion."""
    if os.name == "nt":
        tombstone = path.with_name(f".{uuid4().hex}.completed.json")
        _durable_replace(path, tombstone)
        try:
            tombstone.unlink()
        except OSError:
            # The write-through rename already removed the canonical pending
            # marker durably. A harmless completed tombstone may be cleaned up
            # on a later maintenance pass.
            pass
        return
    path.unlink()
    _fsync_parent(path)


def _fsync_parent(path: Path) -> None:
    """Durably publish a POSIX directory entry change."""
    descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
