"""Move validated playback copies to a configured root without touching films.

Default is a read-only plan. Use --apply to copy, SHA-256 verify, publish the
rebound receipt and only then unlink the verified legacy derivative. Receipts
remain at the legacy location as tiny recovery records. Busy films are skipped.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import uuid
from contextlib import ExitStack
from pathlib import Path

from filelock import FileLock, Timeout

from pipeline.config import Config, load_config
from pipeline.intake import move_file_no_replace
from pipeline.ingest.locks import film_operation_lock, require_no_pending_film_relink
from pipeline.ingest.playback import (
    PlaybackPreparationError,
    PlaybackSourceChanged,
    _MAX_MANIFEST_BYTES,
    _fingerprint,
    _lookup_in_directory,
    _ordinary_path,
    _read_manifest,
    _require_source,
    playback_directory,
    playback_representation_token,
)

_CHUNK_BYTES = 4 * 1024 * 1024


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with _ordinary_path(path).open("rb") as handle:
        while chunk := handle.read(_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def _copy_verified(old: Path, temporary: Path, expected: dict) -> tuple[str, dict]:
    """Copy only an unchanged ordinary derivative and independently read it back."""
    if shutil.disk_usage(temporary.parent).free < expected["size"]:
        raise PlaybackPreparationError("Destination lacks free space for the complete playback copy")
    digest = hashlib.sha256()
    with _ordinary_path(old).open("rb") as reader, _ordinary_path(temporary).open("xb") as writer:
        while chunk := reader.read(_CHUNK_BYTES):
            writer.write(chunk)
            digest.update(chunk)
        writer.flush()
        os.fsync(writer.fileno())
    if _fingerprint(old, include_path=False) != expected:
        raise PlaybackPreparationError("Legacy playback changed while being copied")
    # Set the final metadata before hashing, then retain this exact verified
    # identity through publication. A later stat must never bless new bytes.
    shutil.copystat(old, temporary, follow_symlinks=False)
    verified_output = _fingerprint(temporary, include_path=False)
    if _sha256(temporary) != digest.hexdigest():
        raise PlaybackPreparationError("Copied playback SHA-256 does not match the legacy bytes")
    if _fingerprint(temporary, include_path=False) != verified_output:
        raise PlaybackPreparationError("Copied playback changed during verification")
    return digest.hexdigest(), verified_output


def _publish_manifest(directory: Path, manifest: dict) -> None:
    data = json.dumps(manifest, indent=2, allow_nan=False).encode("utf-8")
    if len(data) > _MAX_MANIFEST_BYTES:
        raise PlaybackPreparationError("Relocated playback receipt exceeded its bounded size")
    temporary = _ordinary_path(directory / f".relocate-manifest-{uuid.uuid4().hex}.json")
    try:
        with temporary.open("xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, _ordinary_path(directory / "manifest.json"))
    finally:
        _ordinary_path(temporary).unlink(missing_ok=True)


def _source_and_output(directory: Path) -> tuple[dict, Path, Path]:
    manifest = _read_manifest(directory)
    identity = manifest.get("source")
    if not isinstance(identity, dict) or not isinstance(identity.get("path"), str):
        raise PlaybackPreparationError("Legacy receipt lacks the original source identity")
    if not Path(identity["path"]).is_absolute():
        raise PlaybackPreparationError("Legacy source identity must be an absolute path")
    output_identity = manifest.get("output")
    if (not isinstance(output_identity, dict)
        or any(type(output_identity.get(key)) is not int for key in ("device", "inode", "size", "mtime_ns"))
        or output_identity["size"] <= 0):
        raise PlaybackPreparationError("Legacy receipt lacks a valid playback identity")
    source = _ordinary_path(Path(identity["path"]))
    _require_source(source, identity)
    old = _ordinary_path(directory / manifest["filename"])
    return manifest, source, old


def _already_moved(manifest: dict, source: Path, destination: Path, old: Path) -> bool:
    current = _lookup_in_directory(source, destination)
    if current is None or current.name != old.name:
        return False
    receipt = _read_manifest(destination)
    relocation = receipt.get("relocation")
    return (isinstance(relocation, dict)
            and relocation.get("legacy_path") == str(old)
            and relocation.get("legacy_output") == manifest.get("output")
            and isinstance(relocation.get("sha256"), str)
            and re.fullmatch(r"[0-9a-f]{64}", relocation["sha256"]) is not None)


def _relocate_one(asset_dir: Path, destination_root: Path, *, apply: bool) -> dict:
    film_id = asset_dir.name
    legacy = playback_directory(asset_dir)
    destination = playback_directory(asset_dir, destination_root, film_id=film_id)
    manifest, source, old = _source_and_output(legacy)
    output = _ordinary_path(destination / manifest["filename"])
    item = {"film_id": film_id, "source": str(old), "destination": str(output),
            "original_film": str(source), "bytes": manifest.get("output", {}).get("size", 0)}
    # Former generations and interrupted encodes have no current validation
    # receipt. Disclose them; never guess that they are safe to remove.
    item["unreferenced_files"] = [str(path) for path in sorted(legacy.iterdir())
                                  if path.name.endswith(".mp4") and path.name != old.name]
    if not old.exists():
        if _already_moved(manifest, source, destination, old):
            return {**item, "status": "already_moved", "bytes_removed": 0}
        raise PlaybackPreparationError("Legacy output is missing without a valid relocated receipt")
    if _lookup_in_directory(source, legacy) != old:
        raise PlaybackPreparationError("Legacy playback no longer matches its validated receipt")
    if destination.exists() and (destination / "manifest.json").exists():
        current = _lookup_in_directory(source, destination)
        if current is None or current.name != old.name:
            raise PlaybackPreparationError("Destination already contains a different or invalid playback receipt")
    if not apply:
        return {**item, "status": "would_move", "bytes_removed": 0}

    temporary: Path | None = None
    try:
        destination.mkdir(parents=True, exist_ok=True)
        _ordinary_path(destination)
        with ExitStack() as locks:
            locks.enter_context(film_operation_lock(asset_dir).acquire(timeout=0))
            require_no_pending_film_relink(asset_dir)
            for directory in (legacy, destination):
                locks.enter_context(FileLock(_ordinary_path(directory / ".prepare.lock"),
                                              timeout=0, preserve_lock_file=True))
            # The plan may have raced a completed ingestion before its lock.
            current_manifest, source, current_old = _source_and_output(legacy)
            if current_manifest != manifest or current_old != old:
                raise PlaybackPreparationError("Legacy receipt changed; rerun the relocation plan")
            expected = _fingerprint(old, include_path=False)
            if expected != manifest["output"]:
                raise PlaybackPreparationError("Legacy playback changed before relocation")
            token = playback_representation_token(old)
            if output.exists():
                verified_output = _fingerprint(output, include_path=False)
                digest = _sha256(old)
                if _sha256(output) != digest or _fingerprint(output, include_path=False) != verified_output:
                    raise PlaybackPreparationError("Existing destination has different or changing bytes")
            else:
                temporary = _ordinary_path(destination / f".relocate-{uuid.uuid4().hex}.mp4")
                digest, verified_output = _copy_verified(old, temporary, expected)
                _require_source(source, manifest["source"])
                # Both offline preparers and the relocator hold this profile
                # lock. No-replace publication also protects against an
                # unrelated writer racing the destination on either OS.
                if _fingerprint(temporary, include_path=False) != verified_output:
                    raise PlaybackPreparationError("Copied playback changed before publication")
                move_file_no_replace(temporary, output)
                temporary = None
            if _fingerprint(old, include_path=False) != expected:
                raise PlaybackPreparationError("Legacy playback changed before publication")
            _require_source(source, manifest["source"])
            if _fingerprint(output, include_path=False) != verified_output:
                raise PlaybackPreparationError("Verified destination changed before publication")
            rebound = {**manifest, "output": verified_output,
                       "representation_token": token,
                       "relocation": {"legacy_path": str(old), "legacy_output": expected, "sha256": digest}}
            # Existing valid current files cannot be displaced by a different
            # receipt even if another process populated the directory earlier.
            receipt_path = _ordinary_path(destination / "manifest.json")
            if receipt_path.exists():
                current = _lookup_in_directory(source, destination)
                if current != output:
                    raise PlaybackPreparationError("Destination receipt changed; refusing to replace it")
            _publish_manifest(destination, rebound)
            if (_lookup_in_directory(source, destination) != output
                or playback_representation_token(output) != token):
                raise PlaybackPreparationError("Relocated playback did not validate after publication")
            _require_source(source, manifest["source"])
            if (_read_manifest(legacy) != manifest
                or _fingerprint(old, include_path=False) != expected
                or _fingerprint(output, include_path=False) != verified_output):
                raise PlaybackPreparationError("Playback identity changed before legacy cleanup")
            # This exact ordinary derivative is the only deleted artifact.
            # Keep the receipt so interruption after unlink is recognizable.
            _ordinary_path(old).unlink()
            return {**item, "status": "moved", "bytes_removed": expected["size"],
                    "sha256": digest, "representation_token": token,
                    "legacy_manifest_retained": True}
    finally:
        if temporary is not None:
            _ordinary_path(temporary).unlink(missing_ok=True)


def relocate_playback(config: Config, destination: Path | None = None, *,
                      apply: bool = False, film_ids: list[str] | None = None) -> dict:
    """Plan/apply independent per-film moves; busy or unsafe entries are reported."""
    root = destination if destination is not None else config.paths.playback_dir
    if root is None:
        raise PlaybackPreparationError("Set paths.playback_dir or pass --destination")
    root = _ordinary_path(root)
    assets = _ordinary_path(config.paths.assets_dir)
    films = _ordinary_path(config.paths.films_dir)
    if (root == Path(root.anchor) or root.is_relative_to(assets) or assets.is_relative_to(root)
        or root.is_relative_to(films) or films.is_relative_to(root)):
        raise PlaybackPreparationError("Playback destination must be separate from source films and legacy assets")
    if film_ids is not None:
        if any(re.fullmatch(r"[A-Za-z0-9_-]+", identity) is None for identity in film_ids):
            raise PlaybackPreparationError("Each --film-id must be a single safe component")
        candidates = [assets / identity for identity in sorted(set(film_ids))]
    else:
        candidates = sorted(path for path in assets.iterdir()
                            if re.fullmatch(r"[A-Za-z0-9_-]+", path.name))
    items = []
    for candidate in candidates:
        try:
            asset_dir = _ordinary_path(candidate)
            if not asset_dir.is_dir():
                continue
            legacy = playback_directory(asset_dir)
            if not (legacy / "manifest.json").exists():
                continue
            items.append(_relocate_one(asset_dir, root, apply=apply))
        except Timeout:
            items.append({"film_id": candidate.name, "status": "busy", "bytes_removed": 0,
                          "reason": "Film or playback preparation is active; retry after it finishes"})
        except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
            items.append({"film_id": candidate.name, "status": "error", "bytes_removed": 0,
                          "reason": str(exc)})
    return {"mode": "apply" if apply else "dry_run", "destination": str(root), "items": items,
            "bytes_planned": sum(item.get("bytes", 0) for item in items if item["status"] == "would_move"),
            "bytes_removed": sum(item["bytes_removed"] for item in items),
            "busy": sum(item["status"] == "busy" for item in items),
            "errors": sum(item["status"] == "error" for item in items)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--destination", type=Path)
    parser.add_argument("--film-id", action="append")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    try:
        report = relocate_playback(load_config(args.config), args.destination,
                                   apply=args.apply, film_ids=args.film_id)
    except (OSError, ValueError, PlaybackPreparationError, PlaybackSourceChanged) as exc:
        print(json.dumps({"mode": "apply" if args.apply else "dry_run", "error": str(exc)}, indent=2))
        return 1
    print(json.dumps(report, indent=2, allow_nan=False))
    return 1 if report["errors"] or report["busy"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
