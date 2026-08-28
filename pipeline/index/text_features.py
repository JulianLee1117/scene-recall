"""Versioned semantic-text feature tables and activation manifests.

The ``units`` table remains the stable result/evidence record.  Each text
embedding profile receives its own LanceDB table so incompatible model
revisions or dimensions can coexist without a destructive schema migration.
Search uses a profile only when its manifest proves complete coverage of the
current ``units`` table; otherwise it falls back to the legacy PE text vector.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Iterable, Sequence
from uuid import uuid4

import lancedb
import numpy as np

from pipeline.config import Config
from pipeline.index.schema import (
    TEXT_FEATURES_SCHEMA_VERSION,
    make_text_features_schema,
)
from pipeline.ingest.text_embed import (
    SEMANTIC_QUERY_INSTRUCTION,
    SEMANTIC_QUERY_INSTRUCTION_VERSION,
    TEXT_EMBEDDING_CONTRACT_VERSION,
    TextModelSpec,
    get_text_model_spec,
)


_TABLE_PREFIX = "unit_text"
_MANIFEST_SCHEMA_VERSION = 1
TEXT_VIEWS = ("caption", "dialogue", "ocr", "facets")


@dataclass(frozen=True)
class TextIndexProfile:
    """One compatible text-vector space and its physical table."""

    profile_id: str
    table_name: str
    model_id: str
    model_revision: str
    dimension: int


@dataclass(frozen=True)
class TextFeatureSource:
    """One independent textual view derived from a durable unit row."""

    feature_id: str
    film_id: str
    unit_id: str
    view: str
    text: str
    source_sha256: str
    is_representative: bool


@dataclass(frozen=True)
class TextIndexManifest:
    """Proof that a derived profile completely matches one units generation."""

    schema_version: int
    profile_id: str
    table_name: str
    model_id: str
    model_revision: str
    dimension: int
    embedding_contract_version: int
    query_instruction: str
    query_instruction_version: str
    units_version: int
    units_row_count: int
    feature_table_version: int
    feature_row_count: int
    generated_at: str


def _slug(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    if not normalized:
        raise ValueError("text profile name cannot be empty")
    return normalized


def profile_for_spec(spec: TextModelSpec) -> TextIndexProfile:
    """Build the stable table/profile identity for a configured model."""
    table_name = (
        f"{_TABLE_PREFIX}_{_slug(spec.config_name)}_"
        f"{_slug(spec.revision)[:12]}_d{spec.dimension}_"
        f"v{TEXT_EMBEDDING_CONTRACT_VERSION}"
    )
    return TextIndexProfile(
        profile_id=spec.profile_id,
        table_name=table_name,
        model_id=spec.model_id,
        model_revision=spec.revision,
        dimension=spec.dimension,
    )


def configured_text_profile(config: Config) -> TextIndexProfile:
    return profile_for_spec(get_text_model_spec(config))


def manifest_path(config: Config, profile: TextIndexProfile) -> Path:
    return (
        config.paths.assets_dir
        / "feature-manifests"
        / "text"
        / f"{_slug(profile.profile_id)}.json"
    )


def create_text_feature_table(
    db: lancedb.DBConnection,
    profile: TextIndexProfile,
) -> None:
    """Create one profile table and strictly verify its complete schema."""
    from pipeline.index.writer import _PUBLICATION_LOCK, _database_write_lock

    expected = make_text_features_schema(profile.dimension)
    with _PUBLICATION_LOCK, _database_write_lock(db):
        try:
            db.create_table(profile.table_name, schema=expected, exist_ok=True)
        except ValueError as exc:
            raise RuntimeError(
                f"text feature table {profile.table_name!r} is incompatible; "
                "build the model as a new profile instead of reusing this table"
            ) from exc
        actual = db.open_table(profile.table_name).schema
        if actual.names != expected.names or any(
            actual.field(name).type != expected.field(name).type
            for name in expected.names
        ):
            raise RuntimeError(
                f"text feature table {profile.table_name!r} has an incompatible "
                "schema; do not mix vector profiles"
            )


def _json_strings(raw: Any, *, field: str, unit_id: str) -> list[str]:
    if raw in (None, ""):
        return []
    if isinstance(raw, list):
        values = raw
    else:
        try:
            values = json.loads(str(raw))
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"unit {unit_id!r} has invalid {field} JSON"
            ) from exc
    if not isinstance(values, list):
        raise ValueError(f"unit {unit_id!r} {field} must be a list")
    return [
        value.strip()
        for value in values
        if isinstance(value, str) and value.strip()
    ]


def _source_hash(view: str, text: str) -> str:
    payload = json.dumps(
        {"view": view, "text": text},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_text_feature_sources(row: dict[str, Any]) -> list[TextFeatureSource]:
    """Project one unit into independent non-empty caption/dialogue/OCR/facet views."""
    unit_id = str(row.get("unit_id") or "").strip()
    film_id = str(row.get("film_id") or "").strip()
    if not unit_id or not film_id:
        raise ValueError("text feature source requires unit_id and film_id")

    views: dict[str, str] = {}
    caption = str(row.get("caption") or "").strip()
    if caption:
        views["caption"] = caption

    dialogue = _json_strings(
        row.get("dialogue"), field="dialogue", unit_id=unit_id
    )
    if dialogue:
        views["dialogue"] = " ".join(dialogue)

    ocr = str(row.get("on_screen_text") or "").strip()
    if ocr:
        views["ocr"] = ocr

    facet_parts: list[str] = []
    for name, label in (
        ("framing", "framing"),
        ("setting", "setting"),
        ("time_of_day", "time of day"),
        ("energy", "energy"),
        ("camera_motion", "camera movement"),
    ):
        value = str(row.get(name) or "").strip()
        if value and value.lower() != "unknown":
            facet_parts.append(f"{label}: {value}")
    for name, label in (
        ("mood", "mood"),
        ("palette", "palette"),
        ("subjects", "subjects"),
    ):
        values = _json_strings(row.get(name), field=name, unit_id=unit_id)
        if values:
            facet_parts.append(f"{label}: {', '.join(values)}")
    if facet_parts:
        views["facets"] = "; ".join(facet_parts)

    representative = bool(row.get("is_representative", True))
    return [
        TextFeatureSource(
            feature_id=f"{unit_id}::text::{view}",
            film_id=film_id,
            unit_id=unit_id,
            view=view,
            text=views[view],
            source_sha256=_source_hash(view, views[view]),
            is_representative=representative,
        )
        for view in TEXT_VIEWS
        if view in views
    ]


def make_text_feature_rows(
    sources: Sequence[TextFeatureSource],
    vectors: np.ndarray,
    profile: TextIndexProfile,
) -> list[dict[str, Any]]:
    """Materialize validated Lance rows for a single embedding profile."""
    expected_shape = (len(sources), profile.dimension)
    if vectors.shape != expected_shape:
        raise ValueError(
            f"text encoder returned {vectors.shape}; expected {expected_shape}"
        )
    if vectors.dtype != np.float32:
        vectors = vectors.astype(np.float32)
    rows: list[dict[str, Any]] = []
    for source, vector in zip(sources, vectors, strict=True):
        if source.view not in TEXT_VIEWS:
            raise ValueError(f"unsupported text view {source.view!r}")
        rows.append(
            {
                "schema_version": TEXT_FEATURES_SCHEMA_VERSION,
                "feature_id": source.feature_id,
                "profile_id": profile.profile_id,
                "model_id": profile.model_id,
                "model_revision": profile.model_revision,
                "film_id": source.film_id,
                "unit_id": source.unit_id,
                "view": source.view,
                "text": source.text,
                "source_sha256": source.source_sha256,
                "is_representative": source.is_representative,
                "vector": vector.tolist(),
            }
        )
    return rows


def replace_film_text_features(
    db: lancedb.DBConnection,
    profile: TextIndexProfile,
    film_id: str,
    rows: Sequence[dict[str, Any]],
    *,
    purge_existing: bool = False,
) -> None:
    """Replace one film's derived rows without touching other films.

    Ordinary reconciliation is one merge transaction. ``purge_existing`` is
    the repair path for duplicate primary keys: the active manifest is already
    invalid in that state, so deleting the film's derived rows before their
    exact replacement cannot expose a partial semantic profile to search.
    """
    from pipeline.index.writer import (
        _PUBLICATION_LOCK,
        _database_write_lock,
        _replace_film_rows,
    )

    if any(str(row.get("film_id") or "") != film_id for row in rows):
        raise ValueError("text feature rows crossed film boundaries")
    feature_ids = [str(row.get("feature_id") or "") for row in rows]
    if any(not feature_id for feature_id in feature_ids):
        raise ValueError("text feature rows require feature_id")
    if len(feature_ids) != len(set(feature_ids)):
        raise ValueError(f"film {film_id!r} has duplicate text feature IDs")

    with _PUBLICATION_LOCK, _database_write_lock(db):
        if purge_existing:
            _replace_film_rows(
                db,
                profile.table_name,
                "feature_id",
                film_id,
                [],
            )
        _replace_film_rows(
            db,
            profile.table_name,
            "feature_id",
            film_id,
            rows,
        )


def _table_version(table: Any) -> int:
    return int(table.version)


def publish_text_index_manifest(
    config: Config,
    db: lancedb.DBConnection,
    profile: TextIndexProfile,
) -> TextIndexManifest:
    """Atomically activate a profile for the exact current table generations."""
    from pipeline.index.writer import table_names

    names = table_names(db)
    if "units" not in names or profile.table_name not in names:
        raise RuntimeError("cannot activate text profile before both tables exist")
    units = db.open_table("units")
    features = db.open_table(profile.table_name)
    manifest = TextIndexManifest(
        schema_version=_MANIFEST_SCHEMA_VERSION,
        profile_id=profile.profile_id,
        table_name=profile.table_name,
        model_id=profile.model_id,
        model_revision=profile.model_revision,
        dimension=profile.dimension,
        embedding_contract_version=TEXT_EMBEDDING_CONTRACT_VERSION,
        query_instruction=SEMANTIC_QUERY_INSTRUCTION,
        query_instruction_version=SEMANTIC_QUERY_INSTRUCTION_VERSION,
        units_version=_table_version(units),
        units_row_count=int(units.count_rows()),
        feature_table_version=_table_version(features),
        feature_row_count=int(features.count_rows()),
        generated_at=datetime.now(UTC).isoformat(),
    )
    if manifest.units_row_count > 0 and manifest.feature_row_count == 0:
        raise RuntimeError("refusing to activate an empty text feature table")

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


def _read_manifest(path: Path) -> TextIndexManifest | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    try:
        return TextIndexManifest(**payload)
    except (TypeError, ValueError):
        return None


def resolve_ready_text_profile(
    config: Config,
    db: lancedb.DBConnection,
) -> TextIndexProfile | None:
    """Return the active profile only when its coverage proof is still valid."""
    from pipeline.index.writer import table_names

    profile = configured_text_profile(config)
    manifest = _read_manifest(manifest_path(config, profile))
    if manifest is None:
        return None
    expected = {
        "schema_version": _MANIFEST_SCHEMA_VERSION,
        "profile_id": profile.profile_id,
        "table_name": profile.table_name,
        "model_id": profile.model_id,
        "model_revision": profile.model_revision,
        "dimension": profile.dimension,
        "embedding_contract_version": TEXT_EMBEDDING_CONTRACT_VERSION,
        "query_instruction": SEMANTIC_QUERY_INSTRUCTION,
        "query_instruction_version": SEMANTIC_QUERY_INSTRUCTION_VERSION,
    }
    if any(getattr(manifest, key) != value for key, value in expected.items()):
        return None

    names = table_names(db)
    if "units" not in names or profile.table_name not in names:
        return None
    units = db.open_table("units")
    features = db.open_table(profile.table_name)
    if (
        manifest.units_version != _table_version(units)
        or manifest.units_row_count != int(units.count_rows())
        or manifest.feature_table_version != _table_version(features)
        or manifest.feature_row_count != int(features.count_rows())
    ):
        return None
    return profile


def existing_feature_metadata(
    rows: Iterable[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Index the minimal columns needed for idempotent backfill decisions."""
    return {
        str(row["feature_id"]): row
        for row in rows
        if row.get("feature_id")
    }


def feature_is_current(
    source: TextFeatureSource,
    existing: dict[str, Any] | None,
    profile: TextIndexProfile,
) -> bool:
    if existing is None:
        return False
    return (
        existing.get("schema_version") == TEXT_FEATURES_SCHEMA_VERSION
        and existing.get("profile_id") == profile.profile_id
        and existing.get("model_id") == profile.model_id
        and existing.get("model_revision") == profile.model_revision
        and existing.get("film_id") == source.film_id
        and existing.get("unit_id") == source.unit_id
        and existing.get("view") == source.view
        and existing.get("text") == source.text
        and existing.get("source_sha256") == source.source_sha256
        and bool(existing.get("is_representative")) == source.is_representative
    )
