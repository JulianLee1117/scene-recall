"""Managed scalar lookup indexes without changing searchable evidence.

Only this module may carry a still-valid coverage manifest across an index-only
version change. An already stale manifest is never repaired by index creation.
"""
from contextlib import contextmanager
from dataclasses import asdict, replace
import json
from uuid import uuid4

from pipeline.index import framing_features, text_features
from pipeline.index.search_storage import reserve_search_storage
from pipeline.index.writer import _PUBLICATION_LOCK, _database_write_lock, table_names


def _write_manifest(path, document):
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(asdict(document), sort_keys=True, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def physical_index_change(config, db):
    """Keep evidence/profile identity stable across a controlled physical edit."""
    with _PUBLICATION_LOCK, _database_write_lock(db):
        manifests = []
        text = text_features.resolve_ready_text_profile(config, db)
        if text is not None:
            path = text_features.manifest_path(config, text)
            manifests.append((path, text_features._read_manifest(path), "units", "units_version"))
        framing = framing_features.resolve_ready_framing_profile(config, db)
        if framing is not None:
            path = framing_features.manifest_path(config, framing)
            manifests.append((path, framing_features._read_manifest(path), "frames", "frames_version"))
        from pathlib import Path
        from pipeline.index.composition import ready_profile, _atomic_json
        compact_manifests = []
        for path in (Path(config.paths.assets_dir) / "search-profiles").glob("composition_*/coverage.json"):
            try:
                ready = ready_profile(config, db, path.parent.name)
                if ready is not None:
                    compact_manifests.append((path, ready[0], json.loads(path.read_text(encoding="utf-8"))))
            except (OSError, ValueError, KeyError, TypeError):
                continue
        yield
        for path, manifest, source_table, version_field in manifests:
            source = db.open_table(source_table)
            features = db.open_table(manifest.table_name)
            expected_count = getattr(manifest, source_table + "_row_count")
            if source.count_rows() != expected_count or features.count_rows() != manifest.feature_row_count:
                raise RuntimeError("Evidence rows changed during index-only maintenance")
            _write_manifest(path, replace(manifest, **{version_field: int(source.version),
                                                      "feature_table_version": int(features.version)}))
        for path, profile, manifest in compact_manifests:
            source, features = db.open_table("frames"), db.open_table(profile.table_name)
            if source.count_rows() != manifest["frame_count"] or features.count_rows() != manifest["frame_count"]:
                raise RuntimeError("Composition evidence changed during index-only maintenance")
            _atomic_json(path, {**manifest, "frames_version": int(source.version), "feature_version": int(features.version)})


def lookup_plan(db):
    definitions = []
    for name in table_names(db):
        table = db.open_table(name)
        fields = set(table.schema.names)
        if name not in {"frames", "units", "films"} and not name.startswith(("unit_text_", "frame_framing", "frame_composition")):
            continue
        for column in ("film_id", "unit_id", "frame_id", "feature_id", "view", "is_representative"):
            if column not in fields:
                continue
            kind = "BITMAP" if column in {"view", "is_representative"} else "BTREE"
            index_name = f"scene_lookup_{column}_v1"
            existing = [index for index in table.list_indices() if index.name == index_name]
            if existing and (len(existing) != 1 or list(existing[0].columns) != [column]
                             or str(existing[0].index_type).upper() != kind):
                raise ValueError(f"Managed lookup {name}.{index_name} has an incompatible definition")
            definitions.append({"table": name, "column": column, "type": kind,
                                "name": index_name, "ready": bool(existing)})
    return definitions


def install_lookup_indexes(config, db):
    """Idempotent explicit migration; searches and startup never build indexes."""
    created = []
    for item in lookup_plan(db):
        if item["ready"]:
            continue
        table = db.open_table(item["table"])
        # String IDs dominate scalar index size. Reserve conservatively for
        # dictionary data, index structures, temporary files and one revision.
        estimate = int(table.count_rows()) * 512 + 4 * 1024**2
        with reserve_search_storage(config, estimate), physical_index_change(config, db):
            table = db.open_table(item["table"])
            # Another managed invocation can finish between planning and locking.
            if not any(index.name == item["name"] for index in table.list_indices()):
                table.create_scalar_index(item["column"], index_type=item["type"],
                                          name=item["name"], replace=False)
                created.append({**item, "ready": True})
    return created
