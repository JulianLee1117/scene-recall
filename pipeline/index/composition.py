"""Experimental position-preserving retrieval from existing PE spatial grids.

This profile is a candidate route, not an acceleration cache or a pose model.
Projection fitting, preparation, coverage publication and activation are explicit.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
from io import BytesIO
import json
from pathlib import Path
import re
from uuid import uuid4

import numpy as np
import pyarrow as pa

from pipeline.index.framing_cache import read_input
from pipeline.index.framing_features import _any_frame
from pipeline.index.writer import _PUBLICATION_LOCK, _database_write_lock, _merge_rows, table_names

CONTRACT = "pe-spatial-projection-v1"
MAX_SAMPLE_CELLS = 65536


@dataclass(frozen=True)
class CompositionProfile:
    profile_id: str
    table_name: str
    source_profile_id: str
    grid_size: int
    feature_dim: int
    cell_dim: int
    matrix_sha256: str
    sample_sha256: str
    contract: str = CONTRACT

    @property
    def dimension(self):
        return self.grid_size**2 * self.cell_dim


def profile_directory(config, identity):
    if not isinstance(identity, str) or re.fullmatch(r"composition_[a-f0-9]{24}", identity) is None:
        raise ValueError("Invalid composition profile identity")
    return Path(config.paths.assets_dir) / "search-profiles" / identity


def _atomic_json(path, value):
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def fit_projection(samples, source_profile, sample_sha256, cell_dim):
    """Fit once; the persisted matrix, not a repeated random run, is authority."""
    import torch
    values = np.asarray(samples, dtype=np.float32)
    if cell_dim not in {32, 64}:
        raise ValueError("The bounded composition comparison supports 32 or 64 dimensions per cell")
    if (values.ndim != 2 or values.shape[1] != source_profile.feature_dim
            or not cell_dim <= len(values) <= MAX_SAMPLE_CELLS
            or not np.isfinite(values).all()):
        raise ValueError("Invalid composition projection sample")
    if not re.fullmatch(r"[a-f0-9]{64}", sample_sha256):
        raise ValueError("Projection fitting requires a frozen sample checksum")
    with torch.random.fork_rng(devices=[]):
        torch.manual_seed(0)
        _, _, directions = torch.pca_lowrank(torch.from_numpy(values),
                                             q=min(cell_dim + 8, *values.shape), center=False, niter=4)
    matrix = directions[:, :cell_dim].numpy().astype("<f4")
    checksum = hashlib.sha256(matrix.tobytes()).hexdigest()
    identity_payload = {"source_profile_id": source_profile.profile_id,
                        "grid_size": source_profile.grid_size, "feature_dim": source_profile.feature_dim,
                        "cell_dim": cell_dim, "matrix_sha256": checksum,
                        "sample_sha256": sample_sha256, "contract": CONTRACT}
    digest = hashlib.sha256(json.dumps(identity_payload, sort_keys=True).encode()).hexdigest()[:24]
    identity = "composition_" + digest
    return CompositionProfile(identity, "frame_" + identity, **identity_payload), matrix


def save_profile(config, profile, matrix):
    from pipeline.index.search_storage import reserve_search_storage
    directory = profile_directory(config, profile.profile_id)
    payload = BytesIO()
    np.save(payload, matrix, allow_pickle=False)
    with reserve_search_storage(config, len(payload.getvalue()) + 1024**2):
        if directory.exists():
            existing, previous = load_profile(config, profile.profile_id)
            if existing != profile or not np.array_equal(previous, matrix):
                raise ValueError("Composition profile identity already has different evidence")
            return directory
        directory.mkdir(parents=True)
        try:
            (directory / "projection.npy").write_bytes(payload.getvalue())
            _atomic_json(directory / "profile.json", asdict(profile))
        except Exception:
            # Only remove files created by this attempt; never an existing profile.
            (directory / "projection.npy").unlink(missing_ok=True)
            (directory / "profile.json").unlink(missing_ok=True)
            directory.rmdir()
            raise
    return directory


def load_profile(config, identity):
    directory = profile_directory(config, identity)
    profile = CompositionProfile(**json.loads((directory / "profile.json").read_text(encoding="utf-8")))
    identity_payload = {key: value for key, value in asdict(profile).items() if key not in {"profile_id", "table_name"}}
    expected_identity = "composition_" + hashlib.sha256(json.dumps(identity_payload, sort_keys=True).encode()).hexdigest()[:24]
    if (directory / "projection.npy").stat().st_size > 2 * 1024**2:
        raise ValueError("Composition projection exceeds the supported size")
    matrix = np.load(directory / "projection.npy", allow_pickle=False)
    if (profile.profile_id != identity or identity != expected_identity or profile.table_name != "frame_" + identity
            or profile.contract != CONTRACT or profile.cell_dim not in {32, 64}
            or matrix.shape != (profile.feature_dim, profile.cell_dim)
            or matrix.dtype != np.dtype("<f4") or not np.isfinite(matrix).all()
            or hashlib.sha256(matrix.tobytes()).hexdigest() != profile.matrix_sha256):
        raise ValueError("Composition projection is corrupt or incompatible")
    return profile, matrix


def project_grids(grids, profile, matrix):
    values = np.asarray(grids, dtype=np.float32)
    if values.shape[1:] != (profile.grid_size, profile.grid_size, profile.feature_dim):
        raise ValueError("Spatial grids do not match the composition profile")
    if not np.isfinite(values).all():
        raise ValueError("Spatial grids contain nonfinite values")
    projected = values @ matrix
    # Preserve equal contributions from corresponding screen cells, then use
    # one ordinary cosine vector. MaxSim across cells would discard alignment.
    norms = np.linalg.norm(projected, axis=-1, keepdims=True)
    projected = projected / np.maximum(norms, 1e-12)
    vectors = projected.reshape(len(values), profile.dimension)
    vector_norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    if np.any(vector_norms <= 1e-12):
        raise ValueError("Projection produced an unsupported zero descriptor")
    vectors /= vector_norms
    return vectors.astype(np.float32)


def feature_schema(profile):
    return pa.schema([
        pa.field("frame_id", pa.string()), pa.field("film_id", pa.string()),
        pa.field("unit_id", pa.string()), pa.field("profile_id", pa.string()),
        pa.field("source_path", pa.string()), pa.field("source_sha256", pa.string()),
        pa.field("source_size", pa.int64()), pa.field("source_mtime_ns", pa.int64()),
        pa.field("vector", pa.list_(pa.float32(), profile.dimension)),
    ])


def write_features(db, profile, matrix, inputs, grids):
    vectors = project_grids(grids, profile, matrix)
    if len(inputs) != len(vectors):
        raise ValueError("Composition sources and vectors have different lengths")
    if not inputs:
        return 0
    rows = [{"frame_id": item.source.frame_id, "film_id": item.source.film_id,
             "unit_id": item.source.unit_id, "profile_id": profile.profile_id,
             "source_path": str(item.source.path), "source_sha256": item.sha256,
             "source_size": item.source.source_size, "source_mtime_ns": item.source.source_mtime_ns,
             "vector": vector.tolist()} for item, vector in zip(inputs, vectors, strict=True)]
    with _PUBLICATION_LOCK, _database_write_lock(db):
        current = db.open_table("frames").search().where(_any_frame([row["frame_id"] for row in rows])).limit(None).to_list()
        source_map = {row["frame_id"]: row for row in current}
        if len(source_map) != len(current):
            raise ValueError("Published frames contain duplicate identities")
        accepted = []
        for row, item in zip(rows, inputs, strict=True):
            if row["frame_id"] not in source_map:
                continue
            fresh = read_input(source_map[row["frame_id"]])
            if fresh.source == item.source and fresh.sha256 == item.sha256:
                accepted.append(row)
        db.create_table(profile.table_name, schema=feature_schema(profile), exist_ok=True)
        table = db.open_table(profile.table_name)
        if not table.schema.equals(feature_schema(profile)):
            raise ValueError("Composition feature schema is incompatible")
        existing = table.search().where(_any_frame([row["frame_id"] for row in rows])).select(
            ["frame_id", "source_sha256", "profile_id", "source_path", "source_size", "source_mtime_ns", "unit_id", "film_id"]
        ).limit(None).to_list()
        by_id = {row["frame_id"]: row for row in existing}
        if len(by_id) != len(existing):
            raise ValueError("Composition feature table contains duplicate identities")
        changed = [row for row in accepted if any(by_id.get(row["frame_id"], {}).get(key) != row[key]
                    for key in ("source_sha256", "profile_id", "source_path", "source_size", "source_mtime_ns", "unit_id", "film_id"))]
        if changed:
            _merge_rows(db, profile.table_name, "frame_id", changed)
        return len(changed)


def publish_coverage(config, db, profile):
    """Validate complete source coverage once at preparation, never per query."""
    with _PUBLICATION_LOCK, _database_write_lock(db):
        frames = db.open_table("frames")
        features = db.open_table(profile.table_name)
        if frames.count_rows() != features.count_rows():
            return False
        source_fields = ["frame_id", "film_id", "unit_id", "path", "source_size", "source_mtime_ns"]
        source = frames.search().select(source_fields).limit(None).to_list()
        derived = features.search().select(["frame_id", "film_id", "unit_id", "source_path", "source_size", "source_mtime_ns", "profile_id", "source_sha256"]).limit(None).to_list()
        actual = {row["frame_id"]: row for row in derived}
        if (not source or len(actual) != len(derived) or len(source) != len(actual)
                or len({row["frame_id"] for row in source}) != len(source)):
            return False
        for row in source:
            found = actual.get(row["frame_id"], {})
            if (found.get("profile_id") != profile.profile_id or not found.get("source_sha256")
                    or found.get("source_path") != row["path"]
                    or any(found.get(key) != row[key] for key in source_fields if key != "path")):
                return False
        _atomic_json(profile_directory(config, profile.profile_id) / "coverage.json", {
            "profile_id": profile.profile_id, "frames_version": int(frames.version),
            "feature_version": int(features.version), "frame_count": len(source),
            "source_generation": hashlib.sha256(json.dumps(sorted(source, key=lambda row: row["frame_id"]), sort_keys=True).encode()).hexdigest(),
        })
    return True


def reconcile_film(db, profile, film_id):
    """Remove only orphan derivations left by replacement of this film's frames."""
    from lancedb.expr import col, lit
    with _PUBLICATION_LOCK, _database_write_lock(db):
        current = {row["frame_id"] for row in db.open_table("frames").search()
                   .where(col("film_id") == lit(film_id)).select(["frame_id"]).limit(None).to_list()}
        features = db.open_table(profile.table_name)
        obsolete = [row["frame_id"] for row in features.search().where(col("film_id") == lit(film_id))
                    .select(["frame_id"]).limit(None).to_list() if row["frame_id"] not in current]
        for start in range(0, len(obsolete), 256):
            features.delete(_any_frame(obsolete[start:start + 256]))
    return len(obsolete)


def ready_profile(config, db, identity):
    profile, matrix = load_profile(config, identity)
    path = profile_directory(config, identity) / "coverage.json"
    raw = (db.read_profile_manifest(path) if getattr(db, "is_index_snapshot", False) is True
           else json.loads(path.read_text(encoding="utf-8")) if path.exists() else None)
    if not isinstance(raw, dict) or raw.get("profile_id") != identity or profile.table_name not in table_names(db):
        return None
    frames, features = db.open_table("frames"), db.open_table(profile.table_name)
    if (raw.get("frames_version") != int(frames.version) or raw.get("feature_version") != int(features.version)
            or raw.get("frame_count") != frames.count_rows() or raw.get("frame_count") != features.count_rows()):
        return None
    return profile, matrix
