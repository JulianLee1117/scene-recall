"""Managed scalar lookup indexes without changing searchable evidence.

Only this module may carry a still-valid coverage manifest across an index-only
version change. An already stale manifest is never repaired by index creation.
"""
from contextlib import contextmanager
from dataclasses import asdict, replace
import json
import shutil
from uuid import uuid4

from pipeline.index import text_features
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
        yield
        for path, manifest, source_table, version_field in manifests:
            source = db.open_table(source_table)
            features = db.open_table(manifest.table_name)
            expected_count = getattr(manifest, source_table + "_row_count")
            if source.count_rows() != expected_count or features.count_rows() != manifest.feature_row_count:
                raise RuntimeError("Evidence rows changed during index-only maintenance")
            _write_manifest(path, replace(manifest, **{version_field: int(source.version),
                                                      "feature_table_version": int(features.version)}))


def lookup_plan(db):
    definitions = []
    for name in table_names(db):
        table = db.open_table(name)
        fields = set(table.schema.names)
        if name not in {"frames", "units", "films"} and not name.startswith("unit_text_"):
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
        if shutil.disk_usage(config.paths.assets_dir).free < estimate + 1024**3:
            raise RuntimeError("Not enough free disk space for a lookup index; free some space and retry")
        with physical_index_change(config, db):
            table = db.open_table(item["table"])
            # Another managed invocation can finish between planning and locking.
            if not any(index.name == item["name"] for index in table.list_indices()):
                table.create_scalar_index(item["column"], index_type=item["type"],
                                          name=item["name"], replace=False)
                created.append({**item, "ready": True})
    return created
