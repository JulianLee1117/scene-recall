"""Refresh insufficient short-shot evidence without redetecting a film.

Planning reads only published metadata. Execution serializes with ordinary
ingestion, retains the original unit identities/ranges and dialogue strings,
and publishes bounded, fully prepared batches. Failed or cancelled work keeps
its profile-scoped media and paid annotation caches for a later retry.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
import json
import math
import os
from pathlib import Path
from typing import Any, Callable, Sequence

from filelock import Timeout as FileLockTimeout

from pipeline.config import Config
from pipeline.index.backfill_text import backfill_text_features_during_ingest
from pipeline.index.writer import (
    FrameWrite,
    UnitWrite,
    open_db,
    publish_unit_updates,
    published_film_ids,
    require_current_film_source,
    require_visual_encoder_profile,
    table_names,
)
from pipeline.ingest.annotate import annotate_shot
from pipeline.ingest.dialogue import DialogueLine
from pipeline.ingest.embed import embed_images, embed_text, pool_image_embeddings
from pipeline.ingest.locks import (
    film_operation_lock,
    global_ingest_lock,
    require_no_pending_film_relink,
)
from pipeline.ingest.media import (
    extract_media,
    keyframe_paths,
    keyframe_timestamp,
    keyframe_timestamp_source,
)
from pipeline.ingest.probe import FilmRecord, _content_hash, probe_film
from pipeline.ingest.shots import SHORT_SHOT_SAMPLING_PROFILE, Shot, resample_shot


_UNIT_COLUMNS = (
    "unit_id", "film_id", "shot_id", "parent_shot_id", "t_start", "t_end",
    "keyframe_paths", "dialogue",
)
_MAX_BATCH_SIZE = 128
Progress = Callable[[dict[str, Any]], None]
Cancelled = Callable[[], bool]


class _Cancelled(Exception):
    """Internal cooperative cancellation; already-running paid calls cache."""


def _ids(values: Sequence[str] | None) -> set[str] | None:
    if values is None:
        return None
    result = {str(value).strip() for value in values}
    if "" in result:
        raise ValueError("film and unit IDs must not be empty")
    return result


def _read_index(
    config: Config,
    *,
    film_ids: Sequence[str] | None = None,
    unit_ids: Sequence[str] | None = None,
) -> tuple[Any | None, list[dict], list[dict]]:
    # open_db is a create-if-missing helper: do not call it on an empty library
    # from a dry run, since even creating its directory would be a mutation.
    if not (config.paths.assets_dir / "db").is_dir():
        return None, [], []
    db = open_db(config)
    if not {"films", "units"}.issubset(table_names(db)):
        return db, [], []
    ready = published_film_ids(db)
    films = [
        row for row in db.open_table("films").search().limit(None).to_list()
        if str(row["film_id"]) in ready
    ]
    query = db.open_table("units").search().select(list(_UNIT_COLUMNS))
    # Explicit unit scope is read across films so ownership errors cannot be
    # mistaken for missing IDs. Ordinary per-film jobs need no library scan.
    scope, column = (
        (unit_ids, "unit_id") if unit_ids is not None else (film_ids, "film_id")
    )
    if scope is not None:
        values = _ids(scope)
        if not values:
            return db, films, []
        literals = ", ".join(
            "'" + value.replace("'", "''") + "'" for value in sorted(values)
        )
        query = query.where(f"{column} IN ({literals})")
    units = query.limit(None).to_list()
    return db, films, units


def _paths(row: dict) -> list[str]:
    value = row.get("keyframe_paths")
    try:
        paths = json.loads(value) if isinstance(value, str) else value
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid keyframe paths for unit {row['unit_id']!r}") from exc
    if not isinstance(paths, list) or not paths or any(
        not isinstance(path, str) or not path for path in paths
    ):
        raise ValueError(f"invalid keyframe paths for unit {row['unit_id']!r}")
    return paths


def _path_key(path: str | Path) -> str:
    return os.path.normcase(os.path.abspath(path))


def _unit_status(row: dict, config: Config) -> str:
    """Recognize this exact sampling profile without mistaking retained files.

    An old file remaining on disk is not proof of an old published unit. The
    published path list is authoritative. Current native sampling can contain
    only one or two images when a shot contains fewer than three source frames.
    Unknown profile directories are intentionally not silently downgraded.
    """
    start, end = float(row["t_start"]), float(row["t_end"])
    if (
        not math.isfinite(start) or not math.isfinite(end)
        or start < 0 or end <= start
    ):
        raise ValueError(
            f"invalid published boundaries for unit {row['unit_id']!r}"
        )
    shot_id = str(row["shot_id"])
    if (
        shot_id != str(row["unit_id"]) or Path(shot_id).name != shot_id
        or "/" in shot_id or "\\" in shot_id
    ):
        raise ValueError(
            f"unsupported published unit identity {row['unit_id']!r}"
        )
    paths = [_path_key(path) for path in _paths(row)]
    directory = config.paths.assets_dir / str(row["film_id"]) / "keyframes"
    current = [
        _path_key(directory / SHORT_SHOT_SAMPLING_PROFILE / f"{shot_id}_{i}.webp")
        for i in range(len(paths))
    ]
    if 1 <= len(paths) <= 3 and paths == current:
        return "current"
    if end - start >= config.thresholds.keyframe_short_shot_s:
        return "ineligible"
    legacy = [_path_key(directory / f"{shot_id}_0.webp")]
    return "eligible" if paths == legacy else "ineligible"


def _plan(
    config: Config,
    films: list[dict],
    units: list[dict],
    *,
    film_ids: Sequence[str] | None,
    unit_ids: Sequence[str] | None,
) -> dict[str, Any]:
    selected_films, selected_units = _ids(film_ids), _ids(unit_ids)
    known_films = {str(row["film_id"]) for row in films}
    if selected_films is not None and selected_films - known_films:
        raise ValueError(
            "unknown or unpublished film IDs: "
            + ", ".join(sorted(selected_films - known_films))
        )
    unit_owners = {str(row["unit_id"]): str(row["film_id"]) for row in units}
    if selected_units is not None:
        unknown = selected_units - unit_owners.keys()
        if unknown:
            raise ValueError("unknown unit IDs: " + ", ".join(sorted(unknown)))
        outside = {
            unit_id for unit_id in selected_units
            if unit_owners[unit_id] not in known_films or (
                selected_films is not None
                and unit_owners[unit_id] not in selected_films
            )
        }
        if outside:
            raise ValueError(
                "unit IDs belong outside the selected published films: "
                + ", ".join(sorted(outside))
            )
    result: dict[str, Any] = {
        "sampling_profile": SHORT_SHOT_SAMPLING_PROFILE,
        "films": [], "film_count": 0, "units_scanned": 0,
        "eligible_units": 0, "skipped_current": 0, "skipped_ineligible": 0,
    }
    grouped: dict[str, list[dict]] = {}
    for row in units:
        grouped.setdefault(str(row["film_id"]), []).append(row)
    for film in sorted(
        films, key=lambda row: (str(row["title"]), str(row["film_id"]))
    ):
        film_id = str(film["film_id"])
        if selected_films is not None and film_id not in selected_films:
            continue
        rows = [
            row for row in grouped.get(film_id, [])
            if selected_units is None or str(row["unit_id"]) in selected_units
        ]
        if selected_units is not None and not rows:
            continue
        entry = {
            "film_id": film_id, "title": str(film["title"]),
            "path": str(film["path"]), "unit_ids": [],
            "current_unit_ids": [],
            "units_scanned": len(rows), "eligible_units": 0,
            "skipped_current": 0, "skipped_ineligible": 0,
        }
        for row in sorted(
            rows,
            key=lambda value: (float(value["t_start"]), str(value["unit_id"])),
        ):
            status = _unit_status(row, config)
            if status == "eligible":
                entry["unit_ids"].append(str(row["unit_id"]))
                entry["eligible_units"] += 1
            elif status == "current":
                entry["skipped_current"] += 1
                if selected_units is not None:
                    entry["current_unit_ids"].append(str(row["unit_id"]))
            else:
                if selected_units is not None:
                    raise ValueError(
                        f"unit {row['unit_id']!r} is not a legacy "
                        "single-image short shot"
                    )
                entry["skipped_ineligible"] += 1
        result["films"].append(entry)
        for count in (
            "units_scanned", "eligible_units", "skipped_current",
            "skipped_ineligible",
        ):
            result[count] += entry[count]
    result["film_count"] = sum(bool(entry["unit_ids"]) for entry in result["films"])
    return result


def plan_temporal_backfill(
    config: Config,
    *,
    film_ids: Sequence[str] | None = None,
    unit_ids: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Return an exact, JSON-safe dry-run manifest without opening a film."""
    _db, films, units = _read_index(config, film_ids=film_ids, unit_ids=unit_ids)
    return _plan(config, films, units, film_ids=film_ids, unit_ids=unit_ids)


def _check_cancelled(cancelled: Cancelled | None) -> None:
    if cancelled is not None and cancelled():
        raise _Cancelled()


def _source_stat(path: Path) -> tuple[int, int, int]:
    stat = path.stat()
    return stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


def _dialogue(row: dict, shot: Shot) -> list[DialogueLine]:
    value = row.get("dialogue")
    strings = json.loads(value) if isinstance(value, str) else value
    if not isinstance(strings, list) or any(
        not isinstance(text, str) for text in strings
    ):
        raise ValueError(f"invalid stored dialogue for unit {shot.shot_id!r}")
    # These are already scoped published strings, not invented utterance
    # timings. The synthetic range only feeds annotate_shot's overlap filter.
    return [
        DialogueLine(start=shot.t_start, end=shot.t_end, text=text)
        for text in strings
    ]


def _annotate_batch(
    film: FilmRecord,
    shots: list[Shot],
    paths: list[list[Path]],
    dialogue: list[list[DialogueLine]],
    config: Config,
    cancelled: Cancelled | None,
) -> list[dict]:
    annotations: list[dict | None] = [None] * len(shots)

    def one(index: int) -> dict:
        _check_cancelled(cancelled)
        return annotate_shot(
            shots[index], paths[index], dialogue[index], config,
            cache_dir=film.asset_dir / "annotations",
        )

    workers = min(max(1, config.ingest.annotation_concurrency), len(shots))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(one, i): i for i in range(len(shots))}
        try:
            for future in as_completed(futures):
                annotations[futures[future]] = future.result()
                _check_cancelled(cancelled)
        except BaseException:
            for future in futures:
                future.cancel()
            raise
    return [annotation for annotation in annotations if annotation is not None]


def backfill_temporal(
    config: Config,
    *,
    film_id: str,
    unit_ids: Sequence[str] | None = None,
    batch_size: int = 32,
    progress: Progress | None = None,
    cancelled: Cancelled | None = None,
) -> dict[str, Any]:
    """Refresh selected short shots under the existing heavy-work locks.

    Explicit IDs are a frozen plan: unknown or cross-film IDs fail closed.
    Already-published current-profile IDs are skipped, including native shots
    with fewer than three distinct frames. No shot detector, transcript pass,
    preview regeneration, or whole-film publication is performed.
    """
    if (
        isinstance(batch_size, bool) or not isinstance(batch_size, int)
        or not 1 <= batch_size <= _MAX_BATCH_SIZE
    ):
        raise ValueError(f"batch_size must be between 1 and {_MAX_BATCH_SIZE}")
    film_id = str(film_id).strip()
    if not film_id:
        raise ValueError("film_id must not be empty")
    ingest_lock = global_ingest_lock(config.paths.assets_dir)
    try:
        ingest_lock.acquire()
    except FileLockTimeout as exc:
        raise RuntimeError(
            "another film ingest or derived-index backfill is already running"
        ) from exc
    try:
        return _backfill_locked(
            config, film_id, unit_ids, batch_size, progress, cancelled,
        )
    finally:
        ingest_lock.release()


def _backfill_locked(
    config: Config, film_id: str, unit_ids: Sequence[str] | None,
    batch_size: int, progress: Progress | None, cancelled: Cancelled | None,
) -> dict[str, Any]:
    db, films, rows = _read_index(config, film_ids=[film_id], unit_ids=unit_ids)
    manifest = _plan(
        config, films, rows, film_ids=[film_id], unit_ids=unit_ids,
    )
    result = {
        "film_id": film_id, "sampling_profile": SHORT_SHOT_SAMPLING_PROFILE,
        "planned_units": manifest["eligible_units"], "published_units": 0,
        "published_frames": 0, "skipped_current": manifest["skipped_current"],
        "cancelled": False, "semantic_text": {"status": "not_needed"},
    }

    def report(stage: str) -> None:
        if progress is not None:
            progress({
                "stage": stage, "film_id": film_id,
                "completed": result["published_units"],
                "total": result["planned_units"],
            })

    if not manifest["eligible_units"] and not manifest["skipped_current"]:
        return result
    film_row = next(row for row in films if str(row["film_id"]) == film_id)
    asset_dir = config.paths.assets_dir / film_id
    if asset_dir.resolve().parent != config.paths.assets_dir.resolve():
        raise ValueError("published film identity is unsafe for its asset directory")
    with film_operation_lock(asset_dir):
        try:
            _check_cancelled(cancelled)
            require_no_pending_film_relink(asset_dir)
            film = probe_film(Path(film_row["path"]), config)
            if film.film_id != film_id:
                raise RuntimeError("source film identity changed since publication")
            require_current_film_source(db, film)
            require_visual_encoder_profile(db, config)
            source_stat = _source_stat(film.path)
            target_ids = {
                unit for entry in manifest["films"] for unit in entry["unit_ids"]
            }
            selected = sorted(
                (row for row in rows if str(row["unit_id"]) in target_ids),
                key=lambda row: (float(row["t_start"]), str(row["unit_id"])),
            )
            for offset in range(0, len(selected), batch_size):
                _check_cancelled(cancelled)
                batch = selected[offset:offset + batch_size]
                shots = [
                    resample_shot(
                        Shot(
                            shot_id=str(row["shot_id"]),
                            t_start=float(row["t_start"]),
                            t_end=float(row["t_end"]),
                            parent_shot_id=row["parent_shot_id"],
                        ),
                        fps=film.fps,
                        threshold=config.thresholds.keyframe_short_shot_s,
                    )
                    for row in batch
                ]
                dialogues = [
                    _dialogue(row, shot)
                    for row, shot in zip(batch, shots, strict=True)
                ]
                report("extracting temporal evidence")
                extract_media(film, shots, config, extract_previews=False)
                _check_cancelled(cancelled)
                paths = [keyframe_paths(film, shot) for shot in shots]
                all_paths = [path for shot_paths in paths for path in shot_paths]
                report("embedding temporal evidence")
                vectors = embed_images(all_paths, config)
                if len(vectors) != len(all_paths):
                    raise ValueError(
                        "visual encoder returned an unexpected number "
                        "of frame vectors"
                    )
                _check_cancelled(cancelled)
                report("describing temporal changes")
                annotations = _annotate_batch(
                    film, shots, paths, dialogues, config, cancelled,
                )
                _check_cancelled(cancelled)
                text_vectors = embed_text(
                    [annotation["searchable_text"] for annotation in annotations],
                    config,
                )
                if len(text_vectors) != len(shots):
                    raise ValueError("text encoder returned an unexpected number of vectors")
                prepared_units, prepared_frames = [], []
                cursor = 0
                for shot, shot_paths, dialogue, annotation, text_vector in zip(
                    shots, paths, dialogues, annotations, text_vectors,
                    strict=True,
                ):
                    frame_vectors = vectors[cursor:cursor + len(shot_paths)]
                    cursor += len(shot_paths)
                    prepared_units.append(UnitWrite(
                        shot=shot, annotation=annotation,
                        img_vec=pool_image_embeddings(frame_vectors),
                        txt_vec=text_vector,
                        dialogue=[line.text for line in dialogue],
                    ))
                    for index, (path, vector) in enumerate(
                        zip(shot_paths, frame_vectors, strict=True)
                    ):
                        prepared_frames.append(FrameWrite(
                            unit_id=shot.shot_id, shot_id=shot.shot_id,
                            frame_index=index,
                            timestamp=keyframe_timestamp(film, shot, index),
                            timestamp_source=keyframe_timestamp_source(shot),
                            path=path,
                            visual_encoder=config.models.visual_encoder,
                            visual_vec=vector,
                            is_representative=index == len(shot_paths) // 2,
                        ))
                _check_cancelled(cancelled)
                require_no_pending_film_relink(asset_dir)
                require_current_film_source(db, film)
                if (
                    _source_stat(film.path) != source_stat
                    or _content_hash(film.path) != film_id
                ):
                    raise RuntimeError(
                        "source film changed while temporal evidence "
                        "was being prepared"
                    )
                publish_unit_updates(db, film, prepared_units, prepared_frames)
                result["published_units"] += len(prepared_units)
                result["published_frames"] += len(prepared_frames)
                report("published temporal evidence")
            _check_cancelled(cancelled)
            report("refreshing semantic text")
            try:
                text_result = backfill_text_features_during_ingest(
                    config, film_id=film_id,
                )
                result["semantic_text"] = {
                    **asdict(text_result),
                    "status": "active" if text_result.activated else "shadow",
                }
            except Exception as exc:
                result["semantic_text"] = {"status": "deferred", "error": str(exc)}
            report("complete")
        except _Cancelled:
            result["cancelled"] = True
            if result["published_units"] or result["skipped_current"]:
                result["semantic_text"] = {
                    "status": "deferred",
                    "error": "cancelled before semantic text refresh; "
                    "retry this backfill or index-text",
                }
            report("cancelled")
    return result
