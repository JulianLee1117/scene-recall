"""Build a versioned semantic-text index from already-ingested unit evidence.

This backfill is local-only.  It does not decode film media or call a hosted
annotator; it projects the caption, dialogue, OCR, broad facets, and narrow
mood/energy evidence already in ``units`` into independent Qwen embeddings.
A complete-coverage manifest is published only after every current unit has
matching feature rows.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from filelock import Timeout as FileLockTimeout

from pipeline.config import Config
from pipeline.index.text_features import (
    TextFeatureSource,
    TextIndexProfile,
    build_text_feature_sources,
    configured_text_profile,
    create_text_feature_table,
    existing_feature_metadata,
    feature_is_current,
    make_text_feature_rows,
    publish_text_index_manifest,
    replace_film_text_features,
)
from pipeline.index.writer import open_db, table_names
from pipeline.ingest.locks import global_ingest_lock
from pipeline.ingest.text_embed import embed_semantic_documents


_UNIT_TEXT_COLUMNS = (
    "unit_id",
    "film_id",
    "is_representative",
    "caption",
    "dialogue",
    "on_screen_text",
    "framing",
    "setting",
    "time_of_day",
    "energy",
    "camera_motion",
    "mood",
    "palette",
    "subjects",
)
_FEATURE_METADATA_COLUMNS = (
    "schema_version",
    "feature_id",
    "profile_id",
    "model_id",
    "model_revision",
    "film_id",
    "unit_id",
    "view",
    "text",
    "source_sha256",
    "is_representative",
)


@dataclass(frozen=True)
class TextBackfillResult:
    profile_id: str
    table_name: str
    units_discovered: int
    features_discovered: int
    embedded: int
    replaced: int
    skipped_current: int
    activated: bool


def _where_film(query: Any, film_id: str | None) -> Any:
    if film_id is None:
        return query
    escaped = film_id.replace("'", "''")
    return query.where(f"film_id = '{escaped}'")


def _unit_rows(db: Any, *, film_id: str | None) -> list[dict[str, Any]]:
    units = db.open_table("units")
    available = set(units.schema.names)
    required = {"unit_id", "film_id", "caption", "dialogue"}
    missing = required - available
    if missing:
        raise RuntimeError(
            "units table cannot build semantic text features; missing columns: "
            + ", ".join(sorted(missing))
        )
    columns = [name for name in _UNIT_TEXT_COLUMNS if name in available]
    query = units.search().select(columns)
    return _where_film(query, film_id).limit(None).to_list()


def _feature_rows(
    db: Any,
    profile: TextIndexProfile,
    *,
    film_id: str | None,
    include_vectors: bool,
) -> list[dict[str, Any]]:
    columns = list(_FEATURE_METADATA_COLUMNS)
    if include_vectors:
        columns.append("vector")
    query = db.open_table(profile.table_name).search().select(columns)
    return _where_film(query, film_id).limit(None).to_list()


def _sources_by_film(
    unit_rows: list[dict[str, Any]],
) -> dict[str, list[TextFeatureSource]]:
    grouped: dict[str, list[TextFeatureSource]] = {}
    for unit in unit_rows:
        film_id = str(unit.get("film_id") or "").strip()
        if not film_id:
            raise ValueError("text feature source requires film_id")
        film_sources = grouped.setdefault(film_id, [])
        for source in build_text_feature_sources(unit):
            film_sources.append(source)
    for sources in grouped.values():
        sources.sort(key=lambda source: (source.unit_id, source.feature_id))
    return grouped


def _profile_is_complete(
    db: Any,
    profile: TextIndexProfile,
) -> bool:
    """Verify exact feature IDs and source/profile identities for all units."""
    all_units = _unit_rows(db, film_id=None)
    expected = {
        source.feature_id: source
        for sources in _sources_by_film(all_units).values()
        for source in sources
    }
    actual_rows = _feature_rows(
        db,
        profile,
        film_id=None,
        include_vectors=False,
    )
    if len(actual_rows) != len(expected):
        return False
    actual = existing_feature_metadata(actual_rows)
    if expected.keys() != actual.keys():
        return False
    return all(
        feature_is_current(source, actual.get(feature_id), profile)
        for feature_id, source in expected.items()
    )


def backfill_text_features(
    config: Config,
    *,
    film_id: str | None = None,
) -> TextBackfillResult:
    """Run a text backfill without racing a film or frame publication."""
    lock = global_ingest_lock(config.paths.assets_dir)
    try:
        lock.acquire()
    except FileLockTimeout as exc:
        raise RuntimeError(
            "another film ingest or derived-index backfill is already running"
        ) from exc
    try:
        return _backfill_text_features_locked(config, film_id=film_id)
    finally:
        lock.release()


def backfill_text_features_during_ingest(
    config: Config,
    *,
    film_id: str,
) -> TextBackfillResult:
    """Backfill one just-published film while its caller holds the ingest lock."""
    return _backfill_text_features_locked(config, film_id=film_id)


def _backfill_text_features_locked(
    config: Config,
    *,
    film_id: str | None = None,
) -> TextBackfillResult:
    """Embed stale independent text views and activate only complete coverage."""
    db = open_db(config)
    if "units" not in table_names(db):
        raise RuntimeError("no units table exists; ingest a film first")

    profile = configured_text_profile(config)
    create_text_feature_table(db, profile)
    units = _unit_rows(db, film_id=film_id)
    if film_id is not None and not units:
        raise ValueError(f"film {film_id!r} has no indexed units")
    grouped = _sources_by_film(units)

    embedded = replaced = skipped = 0
    if film_id is None:
        # A full reconciliation also removes derivations for films no longer
        # present in canonical units. Otherwise one deleted film would leave
        # the completeness manifest permanently impossible to activate.
        indexed_film_ids = {
            str(row.get("film_id") or "").strip()
            for row in _feature_rows(
                db,
                profile,
                film_id=None,
                include_vectors=False,
            )
            if str(row.get("film_id") or "").strip()
        }
        for orphan_film_id in sorted(indexed_film_ids - grouped.keys()):
            replace_film_text_features(
                db,
                profile,
                orphan_film_id,
                [],
            )

    for current_film_id, sources in sorted(grouped.items()):
        previous_rows = _feature_rows(
            db,
            profile,
            film_id=current_film_id,
            include_vectors=True,
        )
        previous = existing_feature_metadata(previous_rows)
        has_duplicates = len(previous_rows) != len(previous)
        stale = [
            source
            for source in sources
            if not feature_is_current(
                source,
                previous.get(source.feature_id),
                profile,
            )
        ]
        current_ids = {source.feature_id for source in sources}
        has_orphans = bool(previous.keys() - current_ids)
        if not stale and not has_orphans and not has_duplicates:
            skipped += len(sources)
            continue

        fresh_rows: dict[str, dict[str, Any]] = {
            source.feature_id: previous[source.feature_id]
            for source in sources
            if feature_is_current(
                source,
                previous.get(source.feature_id),
                profile,
            )
        }
        if stale:
            vectors = embed_semantic_documents(
                [source.text for source in stale],
                config,
            )
            fresh_rows.update(
                {
                    row["feature_id"]: row
                    for row in make_text_feature_rows(stale, vectors, profile)
                }
            )
        replacement = [fresh_rows[source.feature_id] for source in sources]
        replace_film_text_features(
            db,
            profile,
            current_film_id,
            replacement,
            purge_existing=has_duplicates,
        )
        embedded += len(stale)
        replaced += len(replacement)
        skipped += len(sources) - len(stale)

    # Completeness verification and the manifest's table-version snapshot are
    # one publication decision. Holding the same process/file locks as film
    # publication prevents a new units generation from slipping between them.
    from pipeline.index.writer import _PUBLICATION_LOCK, _database_write_lock

    with _PUBLICATION_LOCK, _database_write_lock(db):
        activated = _profile_is_complete(db, profile)
        if activated:
            publish_text_index_manifest(config, db, profile)

    return TextBackfillResult(
        profile_id=profile.profile_id,
        table_name=profile.table_name,
        units_discovered=len(units),
        features_discovered=sum(len(sources) for sources in grouped.values()),
        embedded=embedded,
        replaced=replaced,
        skipped_current=skipped,
        activated=activated,
    )
