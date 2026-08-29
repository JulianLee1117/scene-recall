"""Versioned spatial-descriptor cache for production Framing search.

Framing first retrieves candidates from the frozen legacy PE frame table and
then compares corresponding cells in a learned 6x6 grid.  This module caches
only that bounded-reranking evidence.  It is not an ANN vector space and does
not change candidate generation or Match Cut semantics.

Each compatible model revision and extraction environment receives its own
table. Search consumes a table only when an atomic manifest proves exact
coverage of the current ``frames`` generation; otherwise the established
on-demand candidate encoding remains the whole-query fallback.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import hashlib
from importlib import metadata
import json
from pathlib import Path
import re
from typing import Any, Iterable, Sequence
from uuid import uuid4

import lancedb
from lancedb.expr import col, lit
import numpy as np

from pipeline.config import Config
from pipeline.index.schema import (
    FRAMING_FEATURES_SCHEMA_VERSION,
    make_framing_features_schema,
)
from pipeline.ingest.embed import (
    get_vector_dim,
    resolve_visual_model_lineage,
)


FRAMING_SPATIAL_GRID_SIZE = 6
FRAMING_SPATIAL_EXTRACTION_CONTRACT_VERSION = 1
FRAMING_SPATIAL_STORAGE_DTYPE = "float16-le"

_TABLE_PREFIX = "frame_framing"
_MANIFEST_SCHEMA_VERSION = 1
_LOOKUP_BATCH_SIZE = 32


@dataclass(frozen=True)
class FramingSpatialProfile:
    """One exact spatial-grid extraction and storage contract."""

    profile_id: str
    table_name: str
    encoder_name: str
    model_id: str
    model_revision: str
    open_clip_version: str
    timm_version: str
    torch_version: str
    torchvision_version: str
    pillow_version: str
    row_schema_version: int
    grid_size: int
    feature_dim: int
    extraction_contract_version: int
    storage_dtype: str


@dataclass(frozen=True)
class FramingSpatialSource:
    """Stable frame evidence from which one descriptor is derived."""

    frame_id: str
    film_id: str
    unit_id: str
    path: Path
    source_size: int
    source_mtime_ns: int


@dataclass(frozen=True)
class FramingSpatialManifest:
    """Proof that one cache profile covers an exact frames generation."""

    schema_version: int
    profile_id: str
    table_name: str
    encoder_name: str
    model_id: str
    model_revision: str
    open_clip_version: str
    timm_version: str
    torch_version: str
    torchvision_version: str
    pillow_version: str
    row_schema_version: int
    grid_size: int
    feature_dim: int
    extraction_contract_version: int
    storage_dtype: str
    frames_version: int
    frames_row_count: int
    frame_ids_sha256: str
    feature_table_version: int
    feature_row_count: int
    generated_at: str


def _slug(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    if not normalized:
        raise ValueError("Framing profile name cannot be empty")
    return normalized


def _package_version(distribution: str) -> str:
    try:
        return metadata.version(distribution)
    except metadata.PackageNotFoundError:
        return "unavailable"


def configured_framing_spatial_profile(
    config: Config,
    *,
    ensure_weights: bool = False,
) -> FramingSpatialProfile | None:
    """Resolve the compatible profile for the locally selected PE weights.

    ``None`` means the configured encoder cannot expose the current spatial
    feature contract or its local immutable revision is not yet available.
    Backfills pass ``ensure_weights=True`` and receive a clear error instead.
    """
    encoder_name = config.models.visual_encoder
    if encoder_name != "pe_core_l14":
        if ensure_weights:
            raise RuntimeError(
                f"visual encoder {encoder_name!r} does not support the "
                "Framing spatial-cache contract"
            )
        return None
    resolved = resolve_visual_model_lineage(
        encoder_name,
        ensure_weights=ensure_weights,
    )
    if resolved is None:
        return None
    model_id = resolved.model_id
    revision = resolved.model_revision
    open_clip_version = _package_version("open-clip-torch")
    timm_version = _package_version("timm")
    torch_version = _package_version("torch")
    torchvision_version = _package_version("torchvision")
    pillow_version = _package_version("Pillow")
    feature_dim = get_vector_dim(config)
    lineage = {
        "encoder_name": encoder_name,
        "model_id": model_id,
        "model_revision": revision,
        "open_clip_version": open_clip_version,
        "timm_version": timm_version,
        "torch_version": torch_version,
        "torchvision_version": torchvision_version,
        "pillow_version": pillow_version,
        "row_schema_version": FRAMING_FEATURES_SCHEMA_VERSION,
        "grid_size": FRAMING_SPATIAL_GRID_SIZE,
        "feature_dim": feature_dim,
        "extraction_contract_version": (
            FRAMING_SPATIAL_EXTRACTION_CONTRACT_VERSION
        ),
        "storage_dtype": FRAMING_SPATIAL_STORAGE_DTYPE,
    }
    digest = hashlib.sha256(
        json.dumps(
            lineage,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    profile_id = (
        f"framing-spatial-{encoder_name}-{revision[:12]}-"
        f"g{FRAMING_SPATIAL_GRID_SIZE}-d{feature_dim}-"
        f"s{FRAMING_FEATURES_SCHEMA_VERSION}-"
        f"e{FRAMING_SPATIAL_EXTRACTION_CONTRACT_VERSION}-{digest[:12]}"
    )
    return FramingSpatialProfile(
        profile_id=profile_id,
        table_name=(
            f"{_TABLE_PREFIX}_{_slug(encoder_name)}_{revision[:12]}_"
            f"g{FRAMING_SPATIAL_GRID_SIZE}_d{feature_dim}_"
            f"s{FRAMING_FEATURES_SCHEMA_VERSION}_"
            f"e{FRAMING_SPATIAL_EXTRACTION_CONTRACT_VERSION}_{digest[:12]}"
        ),
        encoder_name=encoder_name,
        model_id=model_id,
        model_revision=revision,
        open_clip_version=open_clip_version,
        timm_version=timm_version,
        torch_version=torch_version,
        torchvision_version=torchvision_version,
        pillow_version=pillow_version,
        row_schema_version=FRAMING_FEATURES_SCHEMA_VERSION,
        grid_size=FRAMING_SPATIAL_GRID_SIZE,
        feature_dim=feature_dim,
        extraction_contract_version=(
            FRAMING_SPATIAL_EXTRACTION_CONTRACT_VERSION
        ),
        storage_dtype=FRAMING_SPATIAL_STORAGE_DTYPE,
    )


def manifest_path(config: Config, profile: FramingSpatialProfile) -> Path:
    return (
        config.paths.assets_dir
        / "feature-manifests"
        / "framing"
        / f"{_slug(profile.profile_id)}.json"
    )


def create_framing_feature_table(
    db: lancedb.DBConnection,
    profile: FramingSpatialProfile,
) -> None:
    """Create one profile table and strictly verify its schema."""
    from pipeline.index.writer import _PUBLICATION_LOCK, _database_write_lock

    if profile.row_schema_version != FRAMING_FEATURES_SCHEMA_VERSION:
        raise RuntimeError("Framing profile uses an unsupported row schema")
    expected = make_framing_features_schema()
    with _PUBLICATION_LOCK, _database_write_lock(db):
        try:
            db.create_table(profile.table_name, schema=expected, exist_ok=True)
        except ValueError as exc:
            raise RuntimeError(
                f"Framing cache table {profile.table_name!r} is incompatible; "
                "build changed extraction as a new profile"
            ) from exc
        actual = db.open_table(profile.table_name).schema
        if actual.names != expected.names or any(
            actual.field(name).type != expected.field(name).type
            for name in expected.names
        ):
            raise RuntimeError(
                f"Framing cache table {profile.table_name!r} has an "
                "incompatible schema; do not mix profiles"
            )


def encode_descriptor(grid: np.ndarray, profile: FramingSpatialProfile) -> bytes:
    """Serialize one validated grid using the profile's bounded float16 cache."""
    expected = (profile.grid_size, profile.grid_size, profile.feature_dim)
    if grid.shape != expected:
        raise ValueError(
            f"Framing grid has shape {grid.shape}; expected {expected}"
        )
    if not np.isfinite(grid).all():
        raise ValueError("Framing grid contains non-finite values")
    return np.asarray(grid, dtype="<f2", order="C").tobytes(order="C")


def decode_descriptor(
    payload: bytes,
    profile: FramingSpatialProfile,
) -> np.ndarray:
    """Decode one descriptor and reject malformed or non-finite evidence."""
    expected_count = profile.grid_size * profile.grid_size * profile.feature_dim
    if len(payload) != expected_count * np.dtype("<f2").itemsize:
        raise ValueError(
            "Framing descriptor byte length does not match its profile"
        )
    grid = np.frombuffer(payload, dtype="<f2").reshape(
        profile.grid_size,
        profile.grid_size,
        profile.feature_dim,
    ).astype(np.float32)
    if not np.isfinite(grid).all():
        raise ValueError("Framing descriptor contains non-finite values")
    return grid


def make_framing_feature_rows(
    sources: Sequence[FramingSpatialSource],
    grids: np.ndarray,
    profile: FramingSpatialProfile,
) -> list[dict[str, Any]]:
    """Materialize checked cache rows for one embedding batch."""
    expected = (
        len(sources),
        profile.grid_size,
        profile.grid_size,
        profile.feature_dim,
    )
    if grids.shape != expected:
        raise ValueError(
            f"spatial encoder returned {grids.shape}; expected {expected}"
        )
    rows: list[dict[str, Any]] = []
    for source, grid in zip(sources, grids, strict=True):
        payload = encode_descriptor(grid, profile)
        rows.append(
            {
                "schema_version": FRAMING_FEATURES_SCHEMA_VERSION,
                "frame_id": source.frame_id,
                "profile_id": profile.profile_id,
                "model_id": profile.model_id,
                "model_revision": profile.model_revision,
                "extraction_contract_version": (
                    profile.extraction_contract_version
                ),
                "grid_size": profile.grid_size,
                "feature_dim": profile.feature_dim,
                "storage_dtype": profile.storage_dtype,
                "film_id": source.film_id,
                "unit_id": source.unit_id,
                "source_path": str(source.path),
                "source_size": source.source_size,
                "source_mtime_ns": source.source_mtime_ns,
                "descriptor_sha256": hashlib.sha256(payload).hexdigest(),
                "descriptor": payload,
            }
        )
    return rows


def framing_feature_is_current(
    source: FramingSpatialSource,
    existing: dict[str, Any] | None,
    profile: FramingSpatialProfile,
    *,
    verify_descriptor: bool = True,
) -> bool:
    if existing is None:
        return False
    metadata_current = (
        profile.row_schema_version == FRAMING_FEATURES_SCHEMA_VERSION
        and existing.get("schema_version") == profile.row_schema_version
        and existing.get("profile_id") == profile.profile_id
        and existing.get("model_id") == profile.model_id
        and existing.get("model_revision") == profile.model_revision
        and existing.get("extraction_contract_version")
        == profile.extraction_contract_version
        and existing.get("grid_size") == profile.grid_size
        and existing.get("feature_dim") == profile.feature_dim
        and existing.get("storage_dtype") == profile.storage_dtype
        and existing.get("film_id") == source.film_id
        and existing.get("unit_id") == source.unit_id
        and existing.get("source_path") == str(source.path)
        and existing.get("source_size") == source.source_size
        and existing.get("source_mtime_ns") == source.source_mtime_ns
        and isinstance(existing.get("descriptor_sha256"), str)
        and len(existing["descriptor_sha256"]) == 64
    )
    return metadata_current and (
        not verify_descriptor
        or framing_descriptor_is_valid(existing, profile)
    )


def framing_descriptor_is_valid(
    row: dict[str, Any],
    profile: FramingSpatialProfile,
) -> bool:
    """Verify stored bytes, checksum, shape, dtype, and finite values."""
    raw_payload = row.get("descriptor")
    if not isinstance(raw_payload, (bytes, bytearray, memoryview)):
        return False
    payload = bytes(raw_payload)
    checksum = row.get("descriptor_sha256")
    if (
        not isinstance(checksum, str)
        or hashlib.sha256(payload).hexdigest() != checksum
    ):
        return False
    try:
        decode_descriptor(payload, profile)
    except ValueError:
        return False
    return True


def existing_framing_metadata(
    rows: Iterable[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    return {
        str(row["frame_id"]): row
        for row in rows
        if row.get("frame_id")
    }


def _frame_ids_digest(frame_ids: Iterable[str]) -> str:
    digest = hashlib.sha256()
    for frame_id in sorted(frame_ids):
        digest.update(frame_id.encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


def _current_frames_identity(
    db: lancedb.DBConnection,
) -> tuple[int, int, str]:
    """Return one stable frames version/count/ID digest snapshot.

    Reopening the table after the scalar scan rejects a generation change
    during digest creation. The scan is intentionally uncached: a table can be
    dropped and recreated with the same URI, version, and row count.
    """
    frames = db.open_table("frames")
    version = int(frames.version)
    row_count = int(frames.count_rows())
    rows = (
        frames.search()
        .select(["frame_id"])
        .limit(None)
        .to_list()
    )
    frame_ids = [str(row.get("frame_id") or "") for row in rows]
    if (
        len(frame_ids) != row_count
        or any(not frame_id for frame_id in frame_ids)
        or len(set(frame_ids)) != row_count
    ):
        raise RuntimeError("frames table has invalid or duplicate frame IDs")
    current = db.open_table("frames")
    if int(current.version) != version or int(current.count_rows()) != row_count:
        raise RuntimeError("frames generation changed during identity scan")
    digest = _frame_ids_digest(frame_ids)
    return version, row_count, digest


def publish_framing_manifest(
    config: Config,
    db: lancedb.DBConnection,
    profile: FramingSpatialProfile,
    *,
    frame_ids: Iterable[str],
) -> FramingSpatialManifest:
    """Atomically activate one cache for the current frames generation."""
    features = db.open_table(profile.table_name)
    frame_id_list = tuple(frame_ids)
    frames_version, frames_row_count, current_ids_sha256 = (
        _current_frames_identity(db)
    )
    supplied_ids_sha256 = _frame_ids_digest(frame_id_list)
    if (
        frames_row_count != len(frame_id_list)
        or current_ids_sha256 != supplied_ids_sha256
    ):
        raise RuntimeError(
            "refusing to activate Framing cache for mismatched frame IDs"
        )
    manifest = FramingSpatialManifest(
        schema_version=_MANIFEST_SCHEMA_VERSION,
        profile_id=profile.profile_id,
        table_name=profile.table_name,
        encoder_name=profile.encoder_name,
        model_id=profile.model_id,
        model_revision=profile.model_revision,
        open_clip_version=profile.open_clip_version,
        timm_version=profile.timm_version,
        torch_version=profile.torch_version,
        torchvision_version=profile.torchvision_version,
        pillow_version=profile.pillow_version,
        row_schema_version=profile.row_schema_version,
        grid_size=profile.grid_size,
        feature_dim=profile.feature_dim,
        extraction_contract_version=profile.extraction_contract_version,
        storage_dtype=profile.storage_dtype,
        frames_version=frames_version,
        frames_row_count=frames_row_count,
        frame_ids_sha256=current_ids_sha256,
        feature_table_version=int(features.version),
        feature_row_count=int(features.count_rows()),
        generated_at=datetime.now(UTC).isoformat(),
    )
    if (
        manifest.frames_row_count == 0
        or manifest.frames_row_count != len(frame_id_list)
        or manifest.feature_row_count != manifest.frames_row_count
    ):
        raise RuntimeError("refusing to activate an incomplete Framing cache")

    path = manifest_path(config, profile)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(asdict(manifest), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return manifest


def _read_manifest(path: Path) -> FramingSpatialManifest | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    try:
        return FramingSpatialManifest(**payload)
    except (TypeError, ValueError):
        return None


def resolve_ready_framing_profile(
    config: Config,
    db: lancedb.DBConnection,
    *,
    validate_frame_ids: bool = True,
) -> FramingSpatialProfile | None:
    """Return a cache profile only while its exact coverage proof is valid."""
    from pipeline.index.writer import table_names

    try:
        profile = configured_framing_spatial_profile(config)
    except (OSError, RuntimeError, ValueError):
        return None
    if profile is None:
        return None
    manifest = _read_manifest(manifest_path(config, profile))
    if manifest is None:
        return None
    expected = {
        "schema_version": _MANIFEST_SCHEMA_VERSION,
        **asdict(profile),
    }
    expected.pop("table_name", None)
    expected["table_name"] = profile.table_name
    if any(getattr(manifest, key) != value for key, value in expected.items()):
        return None
    names = table_names(db)
    if "frames" not in names or profile.table_name not in names:
        return None
    try:
        frames = db.open_table("frames")
        features = db.open_table(profile.table_name)
        if (
            manifest.frames_version != int(frames.version)
            or manifest.frames_row_count != int(frames.count_rows())
            or manifest.feature_table_version != int(features.version)
            or manifest.feature_row_count != int(features.count_rows())
            or manifest.frames_row_count != manifest.feature_row_count
        ):
            return None
        if validate_frame_ids:
            current_version, current_count, current_digest = (
                _current_frames_identity(db)
            )
            if (
                manifest.frames_version != current_version
                or manifest.frames_row_count != current_count
                or manifest.frame_ids_sha256 != current_digest
            ):
                return None
    except (OSError, RuntimeError, ValueError):
        return None
    return profile


def _any_frame(frame_ids: Sequence[str]) -> Any:
    expression = col("frame_id") == lit(frame_ids[0])
    for frame_id in frame_ids[1:]:
        expression = expression | (col("frame_id") == lit(frame_id))
    return expression


def load_framing_grids(
    db: lancedb.DBConnection,
    profile: FramingSpatialProfile,
    frame_ids: Sequence[str],
) -> np.ndarray | None:
    """Load an all-or-nothing descriptor matrix in requested frame order."""
    if not frame_ids:
        return np.empty(
            (0, profile.grid_size, profile.grid_size, profile.feature_dim),
            dtype=np.float32,
        )
    if len(frame_ids) != len(set(frame_ids)):
        return None
    columns = [
        "schema_version",
        "frame_id",
        "profile_id",
        "model_id",
        "model_revision",
        "extraction_contract_version",
        "grid_size",
        "feature_dim",
        "storage_dtype",
        "descriptor_sha256",
        "descriptor",
    ]
    try:
        rows: list[dict[str, Any]] = []
        table = db.open_table(profile.table_name)
        for start in range(0, len(frame_ids), _LOOKUP_BATCH_SIZE):
            batch = frame_ids[start : start + _LOOKUP_BATCH_SIZE]
            rows.extend(
                table.search()
                .where(_any_frame(batch))
                .select(columns)
                .limit(None)
                .to_list()
            )
    except (OSError, RuntimeError, ValueError):
        return None
    if len(rows) != len(frame_ids):
        return None
    by_id: dict[str, np.ndarray] = {}
    for row in rows:
        frame_id = str(row.get("frame_id") or "")
        if not frame_id or frame_id in by_id:
            return None
        if (
            row.get("schema_version") != profile.row_schema_version
            or row.get("profile_id") != profile.profile_id
            or row.get("model_id") != profile.model_id
            or row.get("model_revision") != profile.model_revision
            or row.get("extraction_contract_version")
            != profile.extraction_contract_version
            or row.get("grid_size") != profile.grid_size
            or row.get("feature_dim") != profile.feature_dim
            or row.get("storage_dtype") != profile.storage_dtype
        ):
            return None
        if not framing_descriptor_is_valid(row, profile):
            return None
        by_id[frame_id] = decode_descriptor(
            bytes(row["descriptor"]),
            profile,
        )
    if by_id.keys() != set(frame_ids):
        return None
    return np.stack([by_id[frame_id] for frame_id in frame_ids]).astype(
        np.float32,
        copy=False,
    )
