"""Backfill production Framing spatial grids from existing keyframes.

This local derivation reads the published ``frames`` table and keyframe files;
it does not decode source films or call a hosted model.  A scoped run can fill
one film, but the cache activates only after every frame in the current
generation has a current row in one compatible profile.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

from filelock import Timeout as FileLockTimeout
from PIL import Image

from pipeline.config import Config
from pipeline.index.framing_features import (
    FramingSpatialProfile,
    FramingSpatialSource,
    configured_framing_spatial_profile,
    create_framing_feature_table,
    existing_framing_metadata,
    framing_feature_is_current,
    make_framing_feature_rows,
    publish_framing_manifest,
)
from pipeline.index.writer import (
    open_db,
    require_visual_encoder_profile,
    table_names,
)
from pipeline.ingest.embed import embed_spatial_images
from pipeline.ingest.locks import global_ingest_lock


_METADATA_COLUMNS = (
    "schema_version",
    "frame_id",
    "profile_id",
    "model_id",
    "model_revision",
    "extraction_contract_version",
    "grid_size",
    "feature_dim",
    "storage_dtype",
    "film_id",
    "unit_id",
    "source_path",
    "source_size",
    "source_mtime_ns",
    "descriptor_sha256",
)
_ENCODER_BATCH_SIZE = 32


@dataclass(frozen=True)
class FramingBackfillResult:
    profile_id: str
    table_name: str
    discovered: int
    embedded: int
    upserted: int
    skipped_current: int
    activated: bool


@dataclass(frozen=True)
class FramingBackfillProgress:
    """UI-neutral progress snapshot for one Framing reconciliation."""

    discovered: int
    completed: int
    embedded: int
    skipped_current: int


FramingProgressCallback = Callable[[FramingBackfillProgress], None]


def _report_progress(
    callback: FramingProgressCallback | None,
    *,
    discovered: int,
    embedded: int,
    skipped_current: int,
) -> None:
    if callback is None:
        return
    callback(
        FramingBackfillProgress(
            discovered=discovered,
            completed=embedded + skipped_current,
            embedded=embedded,
            skipped_current=skipped_current,
        )
    )


def _where_film(query: Any, film_id: str | None) -> Any:
    if film_id is None:
        return query
    escaped = film_id.replace("'", "''")
    return query.where(f"film_id = '{escaped}'")


def _frame_rows(db: Any, *, film_id: str | None) -> list[dict[str, Any]]:
    table = db.open_table("frames")
    required = {
        "frame_id",
        "film_id",
        "unit_id",
        "path",
        "source_size",
        "source_mtime_ns",
        "visual_encoder",
    }
    missing = required - set(table.schema.names)
    if missing:
        raise RuntimeError(
            "frames table cannot build Framing features; missing columns: "
            + ", ".join(sorted(missing))
        )
    query = table.search().select(sorted(required))
    return _where_film(query, film_id).limit(None).to_list()


def _feature_rows(
    db: Any,
    profile: FramingSpatialProfile,
    *,
    film_id: str | None,
    include_descriptor: bool = False,
) -> list[dict[str, Any]]:
    columns = list(_METADATA_COLUMNS)
    if include_descriptor:
        columns.append("descriptor")
    query = db.open_table(profile.table_name).search().select(columns)
    return _where_film(query, film_id).limit(None).to_list()


def _sources(
    rows: Iterable[dict[str, Any]],
    profile: FramingSpatialProfile,
    *,
    verify_files: bool,
) -> list[FramingSpatialSource]:
    sources: list[FramingSpatialSource] = []
    seen: set[str] = set()
    for row in rows:
        frame_id = str(row.get("frame_id") or "").strip()
        film_id = str(row.get("film_id") or "").strip()
        unit_id = str(row.get("unit_id") or "").strip()
        path_text = str(row.get("path") or "").strip()
        if not frame_id or not film_id or not unit_id or not path_text:
            raise ValueError("Framing source frame has incomplete identity")
        if frame_id in seen:
            raise ValueError(f"frames table has duplicate frame ID {frame_id!r}")
        seen.add(frame_id)
        if row.get("visual_encoder") != profile.encoder_name:
            raise RuntimeError(
                f"frame {frame_id!r} uses visual encoder "
                f"{row.get('visual_encoder')!r}, not {profile.encoder_name!r}"
            )
        path = Path(path_text)
        source_size = int(row.get("source_size") or 0)
        source_mtime_ns = int(row.get("source_mtime_ns") or 0)
        if verify_files:
            if not path.is_file():
                raise FileNotFoundError(
                    f"indexed keyframe is missing: {path}"
                )
            stat = path.stat()
            if (
                stat.st_size != source_size
                or stat.st_mtime_ns != source_mtime_ns
            ):
                raise RuntimeError(
                    f"indexed keyframe metadata is stale for {path}; run "
                    "index-frames before index-framing"
                )
        sources.append(
            FramingSpatialSource(
                frame_id=frame_id,
                film_id=film_id,
                unit_id=unit_id,
                path=path,
                source_size=source_size,
                source_mtime_ns=source_mtime_ns,
            )
        )
    return sorted(sources, key=lambda item: item.frame_id)


def _delete_rows(
    db: Any,
    profile: FramingSpatialProfile,
    *,
    film_ids: Sequence[str] = (),
    frame_ids: Sequence[str] = (),
) -> None:
    """Remove invalid inactive-profile rows under the publication lock."""
    from pipeline.index.writer import _PUBLICATION_LOCK, _database_write_lock

    def quoted(values: Sequence[str]) -> str:
        return ", ".join(
            "'" + value.replace("'", "''") + "'" for value in values
        )

    with _PUBLICATION_LOCK, _database_write_lock(db):
        table = db.open_table(profile.table_name)
        for start in range(0, len(film_ids), 100):
            batch = film_ids[start : start + 100]
            table.delete(f"film_id IN ({quoted(batch)})")
        for start in range(0, len(frame_ids), 500):
            batch = frame_ids[start : start + 500]
            table.delete(f"frame_id IN ({quoted(batch)})")


def _upsert_rows(
    db: Any,
    profile: FramingSpatialProfile,
    rows: Sequence[dict[str, Any]],
) -> None:
    from pipeline.index.writer import (
        _PUBLICATION_LOCK,
        _database_write_lock,
        _merge_rows,
    )

    with _PUBLICATION_LOCK, _database_write_lock(db):
        _merge_rows(db, profile.table_name, "frame_id", rows)


def _profile_is_complete(
    db: Any,
    profile: FramingSpatialProfile,
) -> tuple[bool, tuple[str, ...]]:
    frame_sources = _sources(
        _frame_rows(db, film_id=None),
        profile,
        verify_files=False,
    )
    expected = {source.frame_id: source for source in frame_sources}
    actual_rows = _feature_rows(
        db,
        profile,
        film_id=None,
        include_descriptor=False,
    )
    if len(actual_rows) != len(expected):
        return False, tuple(expected)
    actual = existing_framing_metadata(actual_rows)
    if expected.keys() != actual.keys():
        return False, tuple(expected)
    if not all(
        framing_feature_is_current(
            source,
            actual.get(frame_id),
            profile,
            verify_descriptor=False,
        )
        for frame_id, source in expected.items()
    ):
        return False, tuple(expected)

    sources_by_film = _sources_by_film(frame_sources)
    for current_film_id, film_sources in sources_by_film.items():
        payload_rows = _feature_rows(
            db,
            profile,
            film_id=current_film_id,
            include_descriptor=True,
        )
        payloads = existing_framing_metadata(payload_rows)
        if (
            len(payload_rows) != len(film_sources)
            or payloads.keys()
            != {source.frame_id for source in film_sources}
            or not all(
                framing_feature_is_current(
                    source,
                    payloads.get(source.frame_id),
                    profile,
                )
                for source in film_sources
            )
        ):
            return False, tuple(expected)
    return True, tuple(expected)


def _sources_by_film(
    sources: Iterable[FramingSpatialSource],
) -> dict[str, list[FramingSpatialSource]]:
    grouped: dict[str, list[FramingSpatialSource]] = {}
    for source in sources:
        grouped.setdefault(source.film_id, []).append(source)
    return {
        film_id: sorted(items, key=lambda item: item.frame_id)
        for film_id, items in sorted(grouped.items())
    }


def _embed_sources(
    db: Any,
    config: Config,
    profile: FramingSpatialProfile,
    sources: Sequence[FramingSpatialSource],
    *,
    batch_size: int,
    on_batch: Callable[[int], None] | None = None,
) -> int:
    embedded = 0
    for start in range(0, len(sources), batch_size):
        batch = sources[start : start + batch_size]
        rows: list[dict[str, Any]] = []
        # Keep decoded-image memory bounded independently from the larger
        # Lance write batch; 512 rows are only ~36 MiB once stored as float16.
        for embed_start in range(0, len(batch), _ENCODER_BATCH_SIZE):
            embed_batch = batch[
                embed_start : embed_start + _ENCODER_BATCH_SIZE
            ]
            images: list[Image.Image] = []
            for source in embed_batch:
                with Image.open(source.path) as image:
                    images.append(image.convert("RGB"))
            _global, grids = embed_spatial_images(
                images,
                config,
                grid_size=profile.grid_size,
                model_revision=profile.model_revision,
            )
            if grids is None:
                raise RuntimeError(
                    f"visual encoder {profile.encoder_name!r} did not return "
                    "spatial features"
                )
            rows.extend(
                make_framing_feature_rows(embed_batch, grids, profile)
            )
        _upsert_rows(db, profile, rows)
        embedded += len(batch)
        if on_batch is not None:
            on_batch(len(batch))
    return embedded


def backfill_framing_features(
    config: Config,
    *,
    film_id: str | None = None,
    batch_size: int = 512,
    progress_callback: FramingProgressCallback | None = None,
) -> FramingBackfillResult:
    """Build current Framing descriptors without racing film publication.

    When supplied, ``progress_callback`` receives UI-neutral snapshots after
    discovery and after each durable descriptor write batch.
    """
    if batch_size < 1:
        raise ValueError("batch_size must be at least 1")
    lock = global_ingest_lock(config.paths.assets_dir)
    try:
        lock.acquire()
    except FileLockTimeout as exc:
        raise RuntimeError(
            "another film ingest or derived-index backfill is already running"
        ) from exc
    try:
        return _backfill_framing_features_locked(
            config,
            film_id=film_id,
            batch_size=batch_size,
            progress_callback=progress_callback,
        )
    finally:
        lock.release()


def _backfill_framing_features_locked(
    config: Config,
    *,
    film_id: str | None,
    batch_size: int,
    progress_callback: FramingProgressCallback | None,
) -> FramingBackfillResult:
    db = open_db(config)
    if "frames" not in table_names(db):
        raise RuntimeError("no frames table exists; ingest a film first")
    require_visual_encoder_profile(db, config)
    profile = configured_framing_spatial_profile(
        config,
        ensure_weights=True,
    )
    if profile is None:  # pragma: no cover - ensure_weights raises instead
        raise RuntimeError("configured encoder cannot build Framing features")
    create_framing_feature_table(db, profile)

    source_rows = _frame_rows(db, film_id=film_id)
    scoped_existing_rows: list[dict[str, Any]] = []
    if film_id is not None:
        scoped_existing_rows = _feature_rows(
            db,
            profile,
            film_id=film_id,
            include_descriptor=False,
        )
        if not source_rows and not scoped_existing_rows:
            raise ValueError(f"film {film_id!r} has no indexed frames")
    sources = _sources(source_rows, profile, verify_files=True)
    sources_by_film = _sources_by_film(sources)

    # Metadata is cheap to scan globally and identifies derived rows whose
    # film no longer exists. Full reconciliation may remove those films;
    # scoped reconciliation never mutates rows outside its requested film.
    if film_id is None:
        all_feature_metadata = _feature_rows(
            db,
            profile,
            film_id=None,
            include_descriptor=False,
        )
        current_films = set(sources_by_film)
        orphan_films = sorted(
            {
                str(row.get("film_id") or "")
                for row in all_feature_metadata
                if str(row.get("film_id") or "") not in current_films
                and str(row.get("film_id") or "")
            }
        )
        if orphan_films:
            _delete_rows(db, profile, film_ids=orphan_films)
    elif not source_rows:
        # The last frame for this film was removed from the canonical table.
        # A scoped reconciliation may safely remove only that film's obsolete
        # derivations, then reactivate if all remaining films are complete.
        _delete_rows(db, profile, film_ids=[film_id])

    discovered = len(sources)
    embedded = 0
    skipped_current = 0
    _report_progress(
        progress_callback,
        discovered=discovered,
        embedded=embedded,
        skipped_current=skipped_current,
    )
    for current_film_id, film_sources in sources_by_film.items():
        existing_rows = _feature_rows(
            db,
            profile,
            film_id=current_film_id,
            include_descriptor=True,
        )
        expected_ids = {source.frame_id for source in film_sources}
        existing_ids = [
            str(row.get("frame_id") or "") for row in existing_rows
        ]
        # Delete and reconstruct this film if it contains obsolete or duplicate
        # rows. This is safe for a scoped run and cannot delete another film's
        # derivations. The old manifest becomes stale at the delete version.
        if (
            len(existing_ids) != len(set(existing_ids))
            or bool(set(existing_ids) - expected_ids)
        ):
            _delete_rows(db, profile, film_ids=[current_film_id])
            existing_rows = []

        existing = existing_framing_metadata(existing_rows)
        stale = [
            source
            for source in film_sources
            if not framing_feature_is_current(
                source,
                existing.get(source.frame_id),
                profile,
            )
        ]
        newly_skipped = len(film_sources) - len(stale)
        if newly_skipped:
            skipped_current += newly_skipped
            _report_progress(
                progress_callback,
                discovered=discovered,
                embedded=embedded,
                skipped_current=skipped_current,
            )

        def record_embedded(batch_count: int) -> None:
            nonlocal embedded
            embedded += batch_count
            _report_progress(
                progress_callback,
                discovered=discovered,
                embedded=embedded,
                skipped_current=skipped_current,
            )

        embedded_for_film = _embed_sources(
            db,
            config,
            profile,
            stale,
            batch_size=batch_size,
            on_batch=record_embedded,
        )
        if embedded_for_film != len(stale):
            raise RuntimeError(
                "Framing backfill did not process every stale frame"
            )
    upserted = embedded

    if embedded + skipped_current != discovered:
        raise RuntimeError("Framing backfill progress did not cover every frame")

    from pipeline.index.writer import _PUBLICATION_LOCK, _database_write_lock

    with _PUBLICATION_LOCK, _database_write_lock(db):
        activated, frame_ids = _profile_is_complete(db, profile)
        if activated:
            publish_framing_manifest(
                config,
                db,
                profile,
                frame_ids=frame_ids,
            )

    return FramingBackfillResult(
        profile_id=profile.profile_id,
        table_name=profile.table_name,
        discovered=discovered,
        embedded=embedded,
        upserted=upserted,
        skipped_current=skipped_current,
        activated=activated,
    )
