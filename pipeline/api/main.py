"""main.py — FastAPI application for scene-recall semantic search.

Endpoints
---------
GET /search?q=...                   Dense semantic search; {"results": [...]}
POST /search/recipe                 Typed modular scene-search clauses
POST /search/recipe/image           Recipe with one uploaded Look/Framing clause
POST /search/image?q=...            Reference composition + optional text
GET /bookmarks                      List durable saved scenes
PUT /bookmarks/{unit_id}            Save one indexed scene
DELETE /bookmarks/{bookmark_id}     Remove one saved scene
GET /unit/{unit_id}                 Full unit record from the LanceDB units table
GET /media/keyframe/{shot_id}/{n}   Serve a WebP keyframe image
GET /media/preview/{shot_id}        Serve a WebM preview clip
GET /video/{film_id}                Stream source video with HTTP range support
GET /video/{film_id}/playback       Resolve the scene player's prepared media URL
GET /library                        List indexed films plus source-directory files
GET /library/scenes                 Browse selected published films chronologically
GET /library/storage                Cached background inventory of library files
GET /incoming                       List completed downloads awaiting review
POST /films/import                  Move a reviewed film into the library
POST /ingest                        Queue a background ingest job for a film
GET /ingest/jobs                    Poll current and completed ingest jobs

Start with::

    uv run uvicorn pipeline.api.main:app --host 127.0.0.1 --port 8000
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import hashlib
import json
import mimetypes
import os
import re
import subprocess
import sys
import threading
import time
from collections import deque
from contextlib import asynccontextmanager
from io import BytesIO
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Annotated, Any, BinaryIO, Callable, Iterator, Literal, Sequence

from dotenv import load_dotenv
from PIL import Image, ImageOps, UnidentifiedImageError

load_dotenv()

from fastapi import (
    FastAPI,
    File,
    Form,
    HTTPException,
    Path as ApiPath,
    Query,
    Request,
    Response,
    UploadFile,
)
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse
from starlette.background import BackgroundTask
from lancedb.expr import col, lit
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from pipeline.bookmarks import Bookmark, BookmarkStore
from pipeline.config import VIDEO_EXTENSIONS, Config, load_config
from pipeline.index.snapshot import SearchLibraryUnavailable
from pipeline.index.writer import (
    ensure_search_indexes,
    open_db,
    published_film_ids,
    table_names,
)
from pipeline.intake import (
    _IMPORTED_RELEASE_MARKER,
    canonical_film_filename as _canonical_film_filename,
    release_suggestion as _release_suggestion,
    is_regular_video as _is_regular_video,
    is_link_or_junction as _is_link_or_junction,
    videos_in_release as _videos_in_release,
    resolve_external_sidecars as _resolve_external_sidecars,
    copy_file_no_replace as _copy_file_no_replace,
    move_file_no_replace as _move_file_no_replace,
    probe_intake_duration as _probe_intake_duration,
)
from pipeline.ingest.subtitles import validate_external_srt
from pipeline.ingest.playback import PlaybackPreparationError, lookup_playback, playback_representation_token
from pipeline.lab.api import router as lab_router
from pipeline.matching.api import router as matching_router
from pipeline.transitions.api import router as transitions_router
from pipeline.acquisition.api import router as acquisition_router
from pipeline.project_info import router as project_info_router
from pipeline.acquisition.service import AcquisitionService
from pipeline.lab.jobs import DurableIngestQueue as _IngestQueue
from pipeline.lab.store import DuplicateJob as _DuplicateIngestError, LabStore
from pipeline.library_storage import LibraryStorageStats, scan_library_storage
from pipeline.index.maintenance import api_database_read_lease
from pipeline.search.retrieve import (
    resolve_result_limit,
    search as _search,
    search_by_image as _search_by_image,
)
from pipeline.search.browse import browse_scenes as _browse_scenes
from pipeline.search.recipe import (
    RecipeSourceNotFound,
    RecipeSourceUnavailable,
    SearchClause as InternalSearchClause,
    SemanticTextProfileUnavailable,
    SourceReference as InternalSourceReference,
    execute_search_recipe as _execute_search_recipe,
)


# ---------------------------------------------------------------------------
# Module-level state
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Lifespan — load config and open DB once at startup
# ---------------------------------------------------------------------------


def _warm_search_models(config: Config, app: FastAPI) -> None:
    """Load the visual encoder before the first user search needs it.

    Search endpoints answer 503 until this finishes so a loading model reads
    as "warming up" in the UI instead of a silently hung request.  Warmup
    failures still flip the ready flag: the next search then attempts the
    load itself and surfaces the real error to the caller.
    """
    started = time.perf_counter()
    print("[startup] loading visual encoder...", flush=True)
    try:
        from pipeline.ingest.embed import embed_text

        embed_text(["warmup"], config)
        print(
            f"[startup] visual encoder ready in {time.perf_counter() - started:.1f}s",
            flush=True,
        )
        from pipeline.index.text_features import resolve_ready_text_profile
        from pipeline.ingest.text_embed import embed_semantic_query

        if resolve_ready_text_profile(config, app.state.db) is not None:
            embed_semantic_query("warmup", config)
            print("[startup] semantic text encoder ready", flush=True)
        # Load the resident vector matrices (text views, frames) with one real
        # query, so the first user search does not pay the load.
        from pipeline.search.retrieve import search

        search("warmup", app.state.db, config, result_limit=1)
        print(f"[startup] search ready in {time.perf_counter() - started:.1f}s", flush=True)
    except Exception as exc:
        print(f"[startup] search warmup failed: {exc}", flush=True)
    finally:
        app.state.encoder_ready = True


def _require_search_ready(request: Request) -> None:
    """Reject search work with a clear 503 while the encoder is warming up."""
    if not request.app.state.encoder_ready:
        raise HTTPException(
            status_code=503,
            detail="Search engine is warming up — try again in a few seconds",
        )


def _resolve_api_result_limit(
    config: Config,
    requested_limit: int | None,
) -> int:
    """Resolve a public search window and report config-bound errors as 422."""
    try:
        return resolve_result_limit(config, requested_limit)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


def _result_probe_limit(config: Config, result_limit: int) -> int:
    """Request one extra eligible row so ``has_more`` is authoritative."""
    return min(
        result_limit + 1,
        int(config.retrieval.max_result_limit),
    )


def _search_response(
    results: Sequence[dict[str, Any]],
    config: Config,
    result_limit: int,
    **extra: Any,
) -> dict[str, Any]:
    """Return one complete stable prefix plus bounded deepening metadata."""
    max_limit = int(config.retrieval.max_result_limit)
    has_more = result_limit < max_limit and len(results) > result_limit
    next_limit = min(max_limit, result_limit * 2) if has_more else None
    return {
        "results": list(results[:result_limit]),
        **extra,
        "display_batch_size": config.retrieval.diversity.page_size,
        "limit": result_limit,
        "max_limit": max_limit,
        "has_more": has_more,
        "next_limit": next_limit,
    }


def _require_writable_runtime_directory(
    directory: Path,
    *,
    purpose: str,
) -> None:
    """Fail startup before a native lock can hide a permission problem."""
    try:
        directory.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(
            dir=directory,
            prefix=".scene-recall-write-test-",
        ) as probe:
            probe.write(b"ok")
            probe.flush()
    except OSError as exc:
        raise RuntimeError(
            f"Scene Recall cannot write to its {purpose} directory at "
            f"{directory}. Start the API with filesystem access to that "
            f"configured path. Underlying error: {exc}"
        ) from exc


def _preflight_runtime_paths(config: Config) -> None:
    """Verify every API-owned mutable root before opening native storage."""
    _require_writable_runtime_directory(
        config.paths.assets_dir / "db",
        purpose="database",
    )
    _require_writable_runtime_directory(
        config.paths.state_dir,
        purpose="user-state",
    )


@asynccontextmanager
async def _runtime_lifespan(app: FastAPI, config: Config):
    """Open config and DB on startup; yield; clean up on shutdown.

    ``SCENE_RECALL_SKIP_WARMUP`` (set by the test suite) skips the encoder
    warmup thread: tests must not load real model weights, and their
    endpoints should be immediately ready.
    """
    db = open_db(config)
    # One-time legacy migration plus a correctness check for rows added by a
    # prior interrupted ingest. Search traffic is not accepted until native
    # FTS covers the complete units table.
    ensure_search_indexes(db)
    app.state.config = config
    app.state.db = db
    if os.environ.get("SCENE_RECALL_SKIP_WARMUP"):
        app.state.encoder_ready = True
    else:
        app.state.encoder_ready = False
        threading.Thread(
            target=_warm_search_models,
            args=(config, app),
            daemon=True,
            name="encoder-warmup",
        ).start()
    app.state.bookmarks = BookmarkStore(config.paths.state_dir)
    app.state.bookmarks.initialize()
    app.state.ready_units_version = None
    app.state.ready_film_ids = frozenset()
    app.state.film_titles_version = None
    app.state.film_titles = {}
    app.state.image_search_lock = asyncio.Lock()
    app.state.image_search_slots = asyncio.Queue(maxsize=2)
    app.state.image_search_slots.put_nowait(None)
    app.state.image_search_slots.put_nowait(None)
    app.state.lab = LabStore(config.paths.state_dir, config.paths.assets_dir)
    app.state.lab.initialize()
    app.state.ingest_queue = _IngestQueue(app.state.lab)
    app.state.acquisition = AcquisitionService(config)
    storage_cancelled = threading.Event()
    app.state.library_storage = LibraryStorageStats(
        lambda: scan_library_storage(
            config, lambda: _library_source_paths(db), cancelled=storage_cancelled,
        ),
        cancel=storage_cancelled.set,
    )
    try:
        yield
    finally:
        app.state.library_storage.close()
        app.state.ingest_queue.close()
        # LanceDB connections do not require explicit closing.


@asynccontextmanager
async def lifespan(app: FastAPI):
    config = load_config()
    _preflight_runtime_paths(config)
    with api_database_read_lease(config):
        async with _runtime_lifespan(app, config):
            yield


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------

app = FastAPI(title="scene-recall", version="0.1.0", lifespan=lifespan)


@app.exception_handler(SearchLibraryUnavailable)
async def search_publication_busy(_request, exc):
    return JSONResponse(status_code=503, content={"detail": str(exc)}, headers={"Retry-After": "1"})
app.include_router(lab_router)
app.include_router(matching_router)
app.include_router(transitions_router)
app.include_router(acquisition_router)
app.include_router(project_info_router)

_DEFAULT_ALLOWED_ORIGINS = "http://localhost:3000,http://127.0.0.1:3000"
_allowed_origins = [
    origin.strip()
    for origin in os.environ.get(
        "SCENE_RECALL_ALLOWED_ORIGINS",
        _DEFAULT_ALLOWED_ORIGINS,
    ).split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_CHUNK_SIZE: int = 1024 * 1024  # 1 MiB per streaming chunk
_MAX_REFERENCE_IMAGE_BYTES: int = 10 * 1024 * 1024
_MAX_REFERENCE_IMAGE_PIXELS: int = 40_000_000
_REFERENCE_IMAGE_TYPES: frozenset[str] = frozenset(
    {
        "image/jpeg",
        "image/png",
        "image/webp",
        "application/octet-stream",
    }
)
_REFERENCE_IMAGE_FORMATS: frozenset[str] = frozenset(
    {"JPEG", "PNG", "WEBP"}
)


def _ready_films_for_units_version(
    request: Request,
    db: Any,
) -> frozenset[str]:
    """Return published film IDs, rescanning only when the units version changes."""
    if "units" not in table_names(db):
        request.app.state.ready_units_version = None
        request.app.state.ready_film_ids = frozenset()
        return frozenset()

    version = db.open_table("units").version
    if request.app.state.ready_units_version == version:
        return request.app.state.ready_film_ids

    ready_film_ids = published_film_ids(db)
    # Update only after a successful scan; transient failures must not poison
    # the cache for this table version.
    request.app.state.ready_units_version = version
    request.app.state.ready_film_ids = ready_film_ids
    return ready_film_ids


def _film_titles_for_version(
    request: Request,
    db: Any,
) -> dict[str, str]:
    """Return cached human-readable film titles for the current table version."""
    if "films" not in table_names(db):
        request.app.state.film_titles_version = None
        request.app.state.film_titles = {}
        return {}

    table = db.open_table("films")
    version = table.version
    if request.app.state.film_titles_version == version:
        return request.app.state.film_titles

    rows = (
        table.search()
        .select(["film_id", "title"])
        .limit(None)
        .to_list()
    )
    titles = {
        str(row["film_id"]): str(row["title"])
        for row in rows
        if row.get("film_id") and row.get("title")
    }
    # Publish the version only after its complete title map was read.
    request.app.state.film_titles = titles
    request.app.state.film_titles_version = version
    return titles


def _with_film_titles(
    request: Request,
    results: list[dict],
) -> list[dict]:
    """Attach display metadata without coupling retrieval to the films table."""
    if not results:
        return results
    try:
        titles = _film_titles_for_version(request, request.app.state.db)
    except Exception as exc:
        # Titles are optional decoration and the frontend has a stable ID
        # fallback. A transient films-table read must not discard otherwise
        # valid retrieval results.
        request.app.state.film_titles_version = None
        request.app.state.film_titles = {}
        print(f"[search] film title lookup failed: {exc}", flush=True)
        return results
    return [
        {
            **result,
            **(
                {"film_title": title}
                if (title := titles.get(str(result.get("film_id") or "")))
                else {}
            ),
        }
        for result in results
    ]


_BOOKMARK_UNIT_FIELDS = [
    "unit_id",
    "film_id",
    "shot_id",
    "t_start",
    "t_end",
    "is_representative",
    "caption",
    "keyframe_paths",
]


def _bookmark_unit_by_id(db: Any, unit_id: str) -> dict[str, Any] | None:
    """Return one current representative unit by ID."""
    if "units" not in table_names(db):
        return None
    rows = (
        db.open_table("units")
        .search()
        .where(col("unit_id") == lit(unit_id))
        .select(_BOOKMARK_UNIT_FIELDS)
        .limit(2)
        .to_list()
    )
    if len(rows) != 1 or rows[0].get("is_representative") is False:
        return None
    return dict(rows[0])


def _unit_contains_timestamp(unit: dict[str, Any], timestamp: float) -> bool:
    try:
        start = float(unit["t_start"])
        end = float(unit["t_end"])
    except (KeyError, TypeError, ValueError):
        return False
    return start <= timestamp < end


def _resolve_bookmark_unit(db: Any, bookmark: Bookmark) -> dict[str, Any] | None:
    """Resolve a bookmark against the current unit generation.

    The derived unit identifier is only a fast path.  If shot boundaries were
    regenerated, the immutable film identity and saved source timestamp select
    the current containing unit without mutating the durable anchor.
    """
    exact = _bookmark_unit_by_id(db, bookmark.source_unit_id)
    if (
        exact is not None
        and str(exact.get("film_id") or "") == bookmark.film_id
        and _unit_contains_timestamp(exact, bookmark.evidence_timestamp)
    ):
        return exact

    if "units" not in table_names(db):
        return None
    rows = (
        db.open_table("units")
        .search()
        .where(col("film_id") == lit(bookmark.film_id))
        .select(_BOOKMARK_UNIT_FIELDS)
        .limit(None)
        .to_list()
    )
    containing = [
        dict(row)
        for row in rows
        if row.get("is_representative") is not False
        and _unit_contains_timestamp(row, bookmark.evidence_timestamp)
    ]
    def preference(unit: dict[str, Any]) -> tuple[float, float, str]:
        start = float(unit["t_start"])
        end = float(unit["t_end"])
        return (
            end - start,
            abs(((start + end) / 2.0) - bookmark.evidence_timestamp),
            str(unit.get("unit_id") or ""),
        )

    if containing:
        return min(containing, key=preference)

    # Half-open ranges make a shared boundary belong only to the following
    # unit.  A film's absolute final boundary has no following unit, so retain
    # that one exact terminal anchor as a deterministic fallback.
    representative = [
        dict(row)
        for row in rows
        if row.get("is_representative") is not False
    ]
    if not representative:
        return None
    final_end = max(float(row["t_end"]) for row in representative)
    if abs(bookmark.evidence_timestamp - final_end) > 1e-6:
        return None
    ending = [
        row
        for row in representative
        if abs(float(row["t_end"]) - final_end) <= 1e-6
    ]
    return min(ending, key=preference) if ending else None


def _bookmark_frame_index(
    db: Any,
    bookmark: Bookmark,
    unit: dict[str, Any],
) -> int:
    """Select the original current frame or the frame nearest the anchor."""
    unit_id = str(unit.get("unit_id") or "")
    if "frames" in table_names(db):
        rows = (
            db.open_table("frames")
            .search()
            .where(col("unit_id") == lit(unit_id))
            .select(["frame_index", "timestamp"])
            .limit(None)
            .to_list()
        )
        frames = [
            row
            for row in rows
            if row.get("frame_index") is not None
        ]
        if unit_id == bookmark.source_unit_id and frames:
            if bookmark.frame_index is not None:
                for frame in frames:
                    if int(frame["frame_index"]) == bookmark.frame_index:
                        return bookmark.frame_index
        timestamped = [
            frame
            for frame in frames
            if frame.get("timestamp") is not None
        ]
        if timestamped:
            nearest = min(
                timestamped,
                key=lambda frame: abs(
                    float(frame["timestamp"]) - bookmark.evidence_timestamp
                ),
            )
            return int(nearest["frame_index"])
        if frames:
            ordered_frames = sorted(
                frames,
                key=lambda frame: int(frame["frame_index"]),
            )
            return int(ordered_frames[len(ordered_frames) // 2]["frame_index"])

    try:
        paths = json.loads(str(unit.get("keyframe_paths") or "[]"))
    except json.JSONDecodeError:
        paths = []
    return len(paths) // 2 if isinstance(paths, list) and paths else 0


def _bookmark_frame_timestamp(
    db: Any,
    unit: dict[str, Any],
    frame_index: int,
) -> float | None:
    """Validate a frame locator and return its timestamp when indexed."""
    unit_id = str(unit.get("unit_id") or "")
    if "frames" in table_names(db):
        from pipeline.index.reads import filtered_rows
        rows = filtered_rows(
            db.open_table("frames"),
            where=(
                (col("unit_id") == lit(unit_id))
                & (col("frame_index") == lit(frame_index))
            ),
            columns=["timestamp"],
            limit=2,
        )
        if len(rows) != 1:
            raise HTTPException(status_code=422, detail="Frame is not indexed")
        timestamp = rows[0].get("timestamp")
        return float(timestamp) if timestamp is not None else None

    try:
        paths = json.loads(str(unit.get("keyframe_paths") or "[]"))
    except json.JSONDecodeError:
        paths = []
    if not isinstance(paths, list) or not 0 <= frame_index < len(paths):
        raise HTTPException(status_code=422, detail="Frame is not indexed")
    return None


def _bookmark_response(request: Request, bookmark: Bookmark) -> dict[str, Any]:
    """Hydrate a durable bookmark from the current derived index generation."""
    db = request.app.state.db
    titles = _film_titles_for_version(request, db)
    title = titles.get(bookmark.film_id, bookmark.film_title_snapshot)
    created_at = datetime.fromtimestamp(
        bookmark.created_at_ms / 1000.0,
        tz=timezone.utc,
    ).isoformat()
    unit = _resolve_bookmark_unit(db, bookmark)
    if unit is None:
        return {
            "bookmark_id": bookmark.bookmark_id,
            "film_id": bookmark.film_id,
            "film_title": title,
            "source_unit_id": bookmark.source_unit_id,
            "evidence_timestamp": bookmark.evidence_timestamp,
            "frame_index": bookmark.frame_index,
            "created_at": created_at,
            "availability": (
                "source_only" if bookmark.film_id in titles else "missing"
            ),
            "scene": None,
        }

    unit_id = str(unit["unit_id"])
    shot_id = str(unit.get("shot_id") or unit_id)
    frame_index = _bookmark_frame_index(db, bookmark, unit)
    keyframe_url = f"/media/keyframe/{shot_id}/{frame_index}"
    return {
        "bookmark_id": bookmark.bookmark_id,
        "film_id": bookmark.film_id,
        "film_title": title,
        "source_unit_id": bookmark.source_unit_id,
        "evidence_timestamp": bookmark.evidence_timestamp,
        "frame_index": bookmark.frame_index,
        "created_at": created_at,
        "availability": "indexed",
        "scene": {
            "unit_id": unit_id,
            "film_id": bookmark.film_id,
            "film_title": title,
            "t_start": float(unit["t_start"]),
            "t_end": float(unit["t_end"]),
            "caption": str(unit.get("caption") or ""),
            "keyframe_url": keyframe_url,
            "keyframe_index": frame_index,
            "preview_url": f"/media/preview/{shot_id}",
            "evidence_timestamp": bookmark.evidence_timestamp,
            "matched_frame_url": keyframe_url,
            "matched_frame_index": frame_index,
            "matched_frame_timestamp": bookmark.evidence_timestamp,
        },
    }


def _parse_range(range_header: str, file_size: int) -> tuple[int, int]:
    """Parse ``Range: bytes=<start>-<end>`` and return ``(start, end)``.

    Raises
    ------
    HTTPException(416)
        If the header is malformed or the range is unsatisfiable.
    """
    m = re.match(r"^bytes=(\d*)-(\d*)$", range_header.strip())
    if not m:
        raise HTTPException(416, detail="Invalid Range header")
    raw_start, raw_end = m.group(1), m.group(2)
    if not raw_start and raw_end:
        # Suffix range: bytes=-N means the last N bytes.
        suffix_length = int(raw_end)
        start = max(file_size - suffix_length, 0)
        end = file_size - 1
    else:
        start = int(raw_start) if raw_start else 0
        end = int(raw_end) if raw_end else file_size - 1
    if start > end or start >= file_size:
        raise HTTPException(416, detail="Range Not Satisfiable")
    end = min(end, file_size - 1)
    return start, end


def _stream_file(path: Path, start: int, end: int) -> Iterator[bytes]:
    """Yield *_CHUNK_SIZE*-byte chunks from *path[start:end+1]*."""
    with open(path, "rb") as fh:
        fh.seek(start)
        remaining = end - start + 1
        while remaining > 0:
            chunk = fh.read(min(_CHUNK_SIZE, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk


_SHOT_ID_PATTERN = re.compile(
    r"^(?P<film_id>[0-9a-f]{64})_(?P<index>[0-9]{4,})$"
)


def _unit_for_shot(shot_id: str, request: Request) -> dict:
    """Return the indexed unit for *shot_id*, or raise HTTP 404."""
    match = _SHOT_ID_PATTERN.fullmatch(shot_id)
    if match is None:
        raise HTTPException(status_code=404, detail=f"Shot {shot_id!r} not found")

    db = request.app.state.db
    rows = (
        db.open_table("units")
        .search()
        .where(col("shot_id") == lit(shot_id))
        .limit(1)
        .to_list()
    )
    if not rows:
        raise HTTPException(status_code=404, detail=f"Shot {shot_id!r} not found")
    unit = dict(rows[0])
    if (
        unit.get("shot_id") != shot_id
        or unit.get("film_id") != match.group("film_id")
    ):
        raise HTTPException(status_code=404, detail=f"Shot {shot_id!r} not found")
    return unit


def _safe_media_path(
    assets_dir: Path,
    film_id: str,
    media_dir: str,
    filename: str,
) -> Path:
    """Resolve a file inside one film's media directory without traversal."""
    if re.fullmatch(r"[0-9a-f]{64}", film_id) is None:
        raise HTTPException(status_code=404, detail="Media not found")

    root = assets_dir.resolve()
    media_root = root.joinpath(film_id, media_dir).resolve()
    candidate = media_root.joinpath(filename).resolve()
    try:
        media_root.relative_to(root)
    except ValueError:
        raise HTTPException(status_code=404, detail="Media not found") from None
    if candidate.parent != media_root:
        raise HTTPException(status_code=404, detail="Media not found")
    return candidate


def _decode_reference_image(payload: bytes) -> Image.Image:
    """Decode a bounded user-supplied still into a detached RGB image."""
    try:
        with Image.open(BytesIO(payload)) as source:
            if str(source.format or "").upper() not in _REFERENCE_IMAGE_FORMATS:
                raise HTTPException(
                    status_code=415,
                    detail="Use a JPEG, PNG, or WebP reference image",
                )
            if source.width * source.height > _MAX_REFERENCE_IMAGE_PIXELS:
                raise HTTPException(
                    status_code=413,
                    detail="Reference image has too many pixels",
                )
            source.load()
            return ImageOps.exif_transpose(source).convert("RGB")
    except HTTPException:
        raise
    except (Image.DecompressionBombError, UnidentifiedImageError, OSError):
        raise HTTPException(
            status_code=400,
            detail="Reference image could not be decoded",
        ) from None


async def _run_serialized_image_work(
    request: Request,
    function: Callable[..., Any],
    /,
    *args: Any,
    **kwargs: Any,
) -> Any:
    """Serialize GPU-heavy reference work and outlive caller cancellation."""
    async with request.app.state.image_search_lock:
        if await request.is_disconnected():
            raise HTTPException(
                status_code=499,
                detail="Client disconnected before image search",
            )
        work = asyncio.create_task(
            asyncio.to_thread(function, *args, **kwargs)
        )
        try:
            return await asyncio.shield(work)
        except asyncio.CancelledError:
            # Cancelling ``to_thread`` does not stop GPU work.  Keep both the
            # serialized lock and bounded slot occupied until it really ends.
            await work
            raise


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class IngestRequest(BaseModel):
    path: str


class SubtitleUseDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["use_as_english"]
    relative_path: str = Field(min_length=1, max_length=1024)


class SubtitleSkipDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["skip"]


class SubtitleAutoDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["auto"]


SubtitleImportDecision = Annotated[
    SubtitleUseDecision | SubtitleSkipDecision | SubtitleAutoDecision,
    Field(discriminator="action"),
]


class FilmImportRequest(BaseModel):
    relative_path: str
    title: str
    year: int
    edition: str | None = None
    ingest: bool = True
    confirm_finished: bool
    subtitle_decision: SubtitleImportDecision | None = None


class BookmarkRequest(BaseModel):
    evidence_timestamp: float | None = Field(
        default=None,
        ge=0,
        allow_inf_nan=False,
    )
    frame_index: int | None = Field(default=None, ge=0)


_RECIPE_CLAUSE_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$"


class SearchSourceReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    unit_id: str = Field(min_length=1, max_length=256)
    frame_index: int | None = Field(default=None, ge=0, le=32_767)

    @field_validator("unit_id")
    @classmethod
    def normalize_unit_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("unit_id cannot be blank")
        return normalized


class SearchTextClause(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(
        min_length=1,
        max_length=64,
        pattern=_RECIPE_CLAUSE_ID_PATTERN,
    )
    kind: Literal["text"]
    facet: Literal["all", "scene", "words", "look", "mood"]
    text: str = Field(min_length=1, max_length=500)

    @field_validator("text")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("text cannot be blank")
        return normalized


class SearchSourceClause(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(
        min_length=1,
        max_length=64,
        pattern=_RECIPE_CLAUSE_ID_PATTERN,
    )
    kind: Literal["source"]
    facet: Literal["scene", "words", "look", "composition", "mood"]
    source: SearchSourceReference

    @model_validator(mode="after")
    def require_visual_frame(self) -> "SearchSourceClause":
        if self.facet in {"look", "composition"} and self.source.frame_index is None:
            raise ValueError(f"{self.facet} source requires frame_index")
        return self


class SearchImageClause(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(
        min_length=1,
        max_length=64,
        pattern=_RECIPE_CLAUSE_ID_PATTERN,
    )
    kind: Literal["image"]
    facet: Literal["look", "composition"]


SearchRecipeClause = Annotated[
    SearchTextClause | SearchSourceClause,
    Field(discriminator="kind"),
]

SearchImageRecipeClause = Annotated[
    SearchTextClause | SearchSourceClause | SearchImageClause,
    Field(discriminator="kind"),
]


class SearchRecipeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    clauses: list[SearchRecipeClause] = Field(min_length=1, max_length=3)
    film_ids: list[str] = Field(default_factory=list, max_length=1_000)
    limit: int | None = Field(default=None, ge=1, strict=True)
    preset: Literal["balanced", "famous", "gems"] = "balanced"

    @field_validator("film_ids")
    @classmethod
    def normalize_film_ids(cls, values: list[str]) -> list[str]:
        return list(
            dict.fromkeys(
                normalized
                for value in values
                if (normalized := value.strip())
            )
        )

    @model_validator(mode="after")
    def require_unique_clauses(self) -> "SearchRecipeRequest":
        clause_ids = [clause.id for clause in self.clauses]
        if len(clause_ids) != len(set(clause_ids)):
            raise ValueError("search recipe clause IDs must be unique")
        facets = [clause.facet for clause in self.clauses]
        if len(facets) != len(set(facets)):
            raise ValueError("search recipe facets must be unique")
        return self


class SearchImageRecipeRequest(BaseModel):
    """Multipart recipe metadata for one uploaded Look/Framing clause."""

    model_config = ConfigDict(extra="forbid")

    clauses: list[SearchImageRecipeClause] = Field(min_length=1, max_length=3)
    film_ids: list[str] = Field(default_factory=list, max_length=1_000)
    limit: int | None = Field(default=None, ge=1, strict=True)
    preset: Literal["balanced", "famous", "gems"] = "balanced"

    @field_validator("film_ids")
    @classmethod
    def normalize_film_ids(cls, values: list[str]) -> list[str]:
        return list(
            dict.fromkeys(
                normalized
                for value in values
                if (normalized := value.strip())
            )
        )

    @model_validator(mode="after")
    def require_valid_image_recipe(self) -> "SearchImageRecipeRequest":
        clause_ids = [clause.id for clause in self.clauses]
        if len(clause_ids) != len(set(clause_ids)):
            raise ValueError("search recipe clause IDs must be unique")
        facets = [clause.facet for clause in self.clauses]
        if len(facets) != len(set(facets)):
            raise ValueError("search recipe facets must be unique")
        image_count = sum(
            isinstance(clause, SearchImageClause) for clause in self.clauses
        )
        if image_count != 1:
            raise ValueError(
                "uploaded-image recipes require exactly one image clause"
            )
        return self


def _internal_recipe_clauses(
    clauses: Sequence[
        SearchTextClause | SearchSourceClause | SearchImageClause
    ],
    *,
    uploaded_image: Image.Image | None = None,
) -> list[InternalSearchClause]:
    return [
        InternalSearchClause(
            clause_id=clause.id,
            kind=clause.kind,
            facet=clause.facet,
            text=clause.text if isinstance(clause, SearchTextClause) else None,
            source=(
                InternalSourceReference(
                    unit_id=clause.source.unit_id,
                    frame_index=clause.source.frame_index,
                )
                if isinstance(clause, SearchSourceClause)
                else None
            ),
            image=(
                uploaded_image
                if isinstance(clause, SearchImageClause)
                else None
            ),
        )
        for clause in clauses
    ]


async def _read_uploaded_reference(upload: UploadFile) -> Image.Image:
    media_type = (
        str(upload.content_type or "").partition(";")[0].strip().lower()
    )
    if media_type not in _REFERENCE_IMAGE_TYPES:
        raise HTTPException(
            status_code=415,
            detail="Use a JPEG, PNG, or WebP reference image",
        )

    payload_buffer = bytearray()
    while chunk := await upload.read(_CHUNK_SIZE):
        if len(payload_buffer) + len(chunk) > _MAX_REFERENCE_IMAGE_BYTES:
            raise HTTPException(
                status_code=413,
                detail="Reference image must be 10 MB or smaller",
            )
        payload_buffer.extend(chunk)
    if not payload_buffer:
        raise HTTPException(status_code=400, detail="Reference image is empty")
    return _decode_reference_image(bytes(payload_buffer))


def _acquire_image_search_slot(request: Request) -> None:
    """Reserve one bounded reference-image work slot without waiting."""
    try:
        request.app.state.image_search_slots.get_nowait()
    except asyncio.QueueEmpty:
        raise HTTPException(
            status_code=429,
            detail="Reference search is busy; try again in a moment",
        ) from None


def _release_image_search_slot(request: Request) -> None:
    """Return one previously reserved reference-image work slot."""
    request.app.state.image_search_slots.put_nowait(None)


async def _run_recipe_execution(
    request: Request,
    clauses: list[InternalSearchClause],
    film_ids: list[str],
    result_limit: int,
    *,
    preset: str = "balanced",
    image_slot_already_acquired: bool = False,
) -> Any:
    """Execute one validated recipe under the required GPU guard."""
    config: Config = request.app.state.config
    db = request.app.state.db
    uses_serialized_image_work = any(
        clause.kind == "image" or clause.facet == "composition"
        for clause in clauses
    )
    acquired_slot_here = False
    if uses_serialized_image_work and not image_slot_already_acquired:
        _acquire_image_search_slot(request)
        acquired_slot_here = True

    try:
        return await (
            _run_serialized_image_work(
                request,
                _execute_search_recipe,
                clauses,
                db,
                config,
                film_ids=film_ids,
                result_limit=result_limit,
                preset=preset,
            )
            if uses_serialized_image_work
            else asyncio.to_thread(
                _execute_search_recipe,
                clauses,
                db,
                config,
                film_ids=film_ids,
                result_limit=result_limit,
                preset=preset,
            )
        )
    except RecipeSourceNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    except RecipeSourceUnavailable as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except SemanticTextProfileUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None
    finally:
        if acquired_slot_here:
            _release_image_search_slot(request)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.get("/search")
def search_endpoint(
    request: Request,
    q: str = Query(min_length=1, max_length=500),
    film_id: list[str] | None = Query(default=None),
    limit: int | None = Query(default=None, ge=1),
    preset: Literal["balanced", "famous", "gems"] = Query(default="balanced"),
) -> dict:
    """Run hybrid search in FastAPI's worker threadpool."""
    _require_search_ready(request)
    config: Config = request.app.state.config
    db = request.app.state.db
    result_limit = _resolve_api_result_limit(config, limit)
    probe_limit = _result_probe_limit(config, result_limit)
    results = _with_film_titles(
        request,
        _search(
            q,
            db,
            config,
            film_ids=film_id,
            result_limit=probe_limit,
            preset=preset,
        ),
    )
    return _search_response(results, config, result_limit)


@app.post("/search/recipe")
async def search_recipe_endpoint(
    payload: SearchRecipeRequest,
    request: Request,
) -> dict:
    """Search one to three explicit, independently evidenced clauses."""
    _require_search_ready(request)
    config: Config = request.app.state.config
    result_limit = _resolve_api_result_limit(config, payload.limit)
    probe_limit = _result_probe_limit(config, result_limit)
    clauses = _internal_recipe_clauses(payload.clauses)
    execution = await _run_recipe_execution(
        request,
        clauses,
        payload.film_ids,
        probe_limit,
        preset=payload.preset,
    )

    return _search_response(
        _with_film_titles(request, execution.results),
        config,
        result_limit,
        source_evidence=execution.source_evidence,
    )


@app.post("/search/recipe/image")
async def image_recipe_endpoint(
    request: Request,
    recipe: Annotated[str, Form()],
    image: Annotated[UploadFile, File()],
) -> dict:
    """Search a multipart recipe containing one transient uploaded still."""
    _require_search_ready(request)
    acquired_slot = False
    try:
        try:
            payload = SearchImageRecipeRequest.model_validate_json(recipe)
        except ValidationError as exc:
            raise RequestValidationError(exc.errors(), body=recipe) from exc
        config: Config = request.app.state.config
        result_limit = _resolve_api_result_limit(config, payload.limit)
        probe_limit = _result_probe_limit(config, result_limit)
        _acquire_image_search_slot(request)
        acquired_slot = True
        decoded_image = await _read_uploaded_reference(image)
        clauses = _internal_recipe_clauses(
            payload.clauses,
            uploaded_image=decoded_image,
        )
        execution = await _run_recipe_execution(
            request,
            clauses,
            payload.film_ids,
            probe_limit,
            preset=payload.preset,
            image_slot_already_acquired=True,
        )
    finally:
        try:
            await image.close()
        finally:
            if acquired_slot:
                _release_image_search_slot(request)

    return _search_response(
        _with_film_titles(request, execution.results),
        config,
        result_limit,
        source_evidence=execution.source_evidence,
    )


@app.post("/search/image")
async def image_search_endpoint(
    request: Request,
    film_id: list[str] | None = Query(default=None),
    exclude_unit_id: str | None = Query(default=None),
    exclude_film_id: str | None = Query(default=None),
    q: str | None = Query(default=None, max_length=500),
    limit: int | None = Query(default=None, ge=1),
) -> dict:
    """Find similar framing, optionally constrained by a text query."""
    _require_search_ready(request)
    config: Config = request.app.state.config
    result_limit = _resolve_api_result_limit(config, limit)
    probe_limit = _result_probe_limit(config, result_limit)
    content_type = request.headers.get("content-type", "")
    media_type = content_type.partition(";")[0].strip().lower()
    if media_type not in _REFERENCE_IMAGE_TYPES:
        raise HTTPException(
            status_code=415,
            detail="Use a JPEG, PNG, or WebP reference image",
        )

    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > _MAX_REFERENCE_IMAGE_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail="Reference image must be 10 MB or smaller",
                )
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail="Invalid Content-Length header",
            ) from None

    _acquire_image_search_slot(request)

    try:
        payload_buffer = bytearray()
        async for chunk in request.stream():
            if len(payload_buffer) + len(chunk) > _MAX_REFERENCE_IMAGE_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail="Reference image must be 10 MB or smaller",
                )
            payload_buffer.extend(chunk)
        payload = bytes(payload_buffer)
        if not payload:
            raise HTTPException(
                status_code=400,
                detail="Reference image is empty",
            )

        image = _decode_reference_image(payload)
        db = request.app.state.db
        results = await _run_serialized_image_work(
            request,
            _search_by_image,
            image,
            db,
            config,
            film_ids=film_id,
            exclude_unit_id=exclude_unit_id,
            exclude_film_id=exclude_film_id,
            result_limit=probe_limit,
            text_query=q,
        )
        return _search_response(
            _with_film_titles(request, results),
            config,
            result_limit,
        )
    finally:
        _release_image_search_slot(request)


@app.get("/bookmarks")
def bookmarks_endpoint(request: Request) -> dict[str, list[dict[str, Any]]]:
    """List durable saved moments hydrated from the current index."""
    bookmarks: BookmarkStore = request.app.state.bookmarks
    return {
        "bookmarks": [
            _bookmark_response(request, bookmark)
            for bookmark in bookmarks.list_all()
        ]
    }


@app.put("/bookmarks/{unit_id}")
def save_bookmark_endpoint(
    request: Request,
    payload: BookmarkRequest,
    unit_id: str = ApiPath(min_length=1, max_length=256),
) -> dict[str, Any]:
    """Idempotently save one current unit and its exact source moment."""
    db = request.app.state.db
    unit = _bookmark_unit_by_id(db, unit_id)
    if unit is None:
        raise HTTPException(status_code=404, detail=f"Unit {unit_id!r} not found")

    frame_timestamp = None
    if payload.frame_index is not None:
        frame_timestamp = _bookmark_frame_timestamp(
            db,
            unit,
            payload.frame_index,
        )
    evidence_timestamp = (
        payload.evidence_timestamp
        if payload.evidence_timestamp is not None
        else frame_timestamp
    )
    if evidence_timestamp is None:
        evidence_timestamp = (
            float(unit["t_start"]) + float(unit["t_end"])
        ) / 2.0
    if not _unit_contains_timestamp(unit, evidence_timestamp):
        raise HTTPException(
            status_code=422,
            detail="Evidence timestamp is outside the indexed scene",
        )

    film_id = str(unit["film_id"])
    titles = _film_titles_for_version(request, db)
    bookmark = request.app.state.bookmarks.save(
        film_id=film_id,
        source_unit_id=unit_id,
        evidence_timestamp=evidence_timestamp,
        frame_index=payload.frame_index,
        film_title_snapshot=titles.get(film_id, film_id),
    )
    return _bookmark_response(request, bookmark)


@app.delete("/bookmarks/{bookmark_id}", status_code=204)
def delete_bookmark_endpoint(
    request: Request,
    bookmark_id: str = ApiPath(min_length=1, max_length=64),
) -> Response:
    """Remove one saved moment without touching source or index data."""
    if not request.app.state.bookmarks.delete(bookmark_id):
        raise HTTPException(status_code=404, detail="Bookmark not found")
    return Response(status_code=204)


@app.get("/unit/{unit_id}")
def unit_endpoint(unit_id: str, request: Request) -> dict:
    """Return the full unit record for *unit_id*."""
    db = request.app.state.db
    tbl = db.open_table("units")
    rows = tbl.search().where(col("unit_id") == lit(unit_id)).to_list()
    if not rows:
        raise HTTPException(status_code=404, detail=f"Unit {unit_id!r} not found")
    return dict(rows[0])


@app.get("/media/keyframe/{shot_id}/{n}")
def keyframe_endpoint(shot_id: str, n: int, request: Request) -> FileResponse:
    """Serve the *n*-th WebP keyframe for *shot_id*."""
    from pipeline.ingest.shots import SHORT_SHOT_SAMPLING_PROFILE

    config: Config = request.app.state.config
    unit = _unit_for_shot(shot_id, request)
    try:
        keyframe_paths = json.loads(unit["keyframe_paths"])
    except (KeyError, TypeError, json.JSONDecodeError):
        keyframe_paths = []
    if (
        not isinstance(keyframe_paths, list)
        or not 0 <= n < len(keyframe_paths)
        or not isinstance(keyframe_paths[n], str)
    ):
        raise HTTPException(
            status_code=404,
            detail=f"Keyframe not found: {shot_id}_{n}.webp",
        )

    filename = f"{shot_id}_{n}.webp"
    stored = Path(keyframe_paths[n]).absolute()
    keyframe_root = (
        config.paths.assets_dir.absolute() / str(unit["film_id"]) / "keyframes"
    )
    if stored == keyframe_root / filename:
        media_dir = "keyframes"
    elif stored == keyframe_root / SHORT_SHOT_SAMPLING_PROFILE / filename:
        media_dir = f"keyframes/{SHORT_SHOT_SAMPLING_PROFILE}"
    else:
        raise HTTPException(status_code=404, detail="Keyframe not found")
    path = _safe_media_path(
        config.paths.assets_dir,
        str(unit["film_id"]),
        media_dir,
        filename,
    )
    # Resolving the configured asset root is allowed; a symlink beneath it
    # must not redirect this film's indexed image into another file or film.
    expected = (
        config.paths.assets_dir.resolve() / str(unit["film_id"])
        / media_dir / filename
    )
    if path != expected:
        raise HTTPException(status_code=404, detail="Keyframe not found")
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"Keyframe not found: {shot_id}_{n}.webp")
    return FileResponse(str(path), media_type="image/webp")


@app.get("/media/hero/{unit_id}")
def hero_endpoint(unit_id: str, request: Request) -> FileResponse:
    """Serve the shot's hero frame (evidence v2): the best still near its peak moment."""
    from pipeline.evidence.tables import SHOT_EVIDENCE
    from pipeline.index.writer import table_names

    db = request.app.state.db
    if SHOT_EVIDENCE not in table_names(db) or re.fullmatch(r"[0-9a-f]{64}_[0-9A-Za-z_.-]+", unit_id) is None:
        raise HTTPException(status_code=404, detail="Hero frame not found")
    rows = (db.open_table(SHOT_EVIDENCE).search().select(["film_id", "hero_path"])
            .where(f"unit_id = '{unit_id}'").limit(1).to_list())
    stored = rows[0].get("hero_path") if rows else None
    if not stored:
        raise HTTPException(status_code=404, detail="Hero frame not found")
    config: Config = request.app.state.config
    film_id = str(rows[0]["film_id"])
    relative = Path(stored)
    if relative.name != f"{unit_id}.webp" or relative.parts[:3] != (film_id, "evidence", "hero"):
        raise HTTPException(status_code=404, detail="Hero frame not found")
    path = _safe_media_path(config.paths.assets_dir, film_id, relative.parent.relative_to(film_id).as_posix(),
                            relative.name)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Hero frame not found")
    return FileResponse(str(path), media_type="image/webp", headers={"Cache-Control": "public, max-age=86400"})


@app.get("/media/preview/{shot_id}")
def preview_endpoint(shot_id: str, request: Request) -> FileResponse:
    """Serve the WebM preview clip for *shot_id*."""
    config: Config = request.app.state.config
    unit = _unit_for_shot(shot_id, request)
    path = _safe_media_path(
        config.paths.assets_dir,
        str(unit["film_id"]),
        "previews",
        f"{shot_id}.webm",
    )
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"Preview not found: {shot_id}.webm")
    return FileResponse(str(path), media_type="video/webm")


def _video_source(film_id: str, request: Request) -> Path:
    """Resolve an indexed source, including films outside the library folder."""
    db = request.app.state.db
    tbl = db.open_table("films")
    rows = tbl.search().where(col("film_id") == lit(film_id)).to_list()
    if not rows:
        raise HTTPException(status_code=404, detail=f"Film {film_id!r} not found")

    path = Path(rows[0]["path"])
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"Video file not found: {path.name}")
    return path


def _prepared_video(source: Path, film_id: str, request: Request) -> Path | None:
    # IDs are components, never paths; legacy/test IDs also remain usable.
    if re.fullmatch(r"[A-Za-z0-9_-]+", film_id) is None:
        return None
    config: Config = request.app.state.config
    return lookup_playback(source, config.paths.assets_dir / film_id,
                           config.paths.playback_dir, film_id=film_id)


def _playback_token(path: Path) -> str:
    return playback_representation_token(path)


def _open_prepared_video(source: Path, film_id: str, request: Request,
                         representation: str) -> tuple[Path, BinaryIO, int]:
    """Pin an open generation before migration can remove its old pathname."""
    for _ in range(2):
        prepared = _prepared_video(source, film_id, request)
        if prepared is None:
            break
        handle = None
        try:
            handle = prepared.open("rb")
            opened = os.fstat(handle.fileno())
            token = _playback_token(prepared)
            current = prepared.stat()
            identity = lambda info: (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
            if token == representation and identity(opened) == identity(current):
                return prepared, handle, opened.st_size
        except (OSError, PlaybackPreparationError):
            # A verified relocation can remove the old pathname between lookup
            # and open. Retry the new location, still requiring the exact token.
            pass
        if handle is not None:
            handle.close()
    raise HTTPException(
        status_code=409, detail="Playback changed; reopen the scene or retry playback",
    )


def _stream_prepared_file(handle: BinaryIO, start: int, end: int) -> Iterator[bytes]:
    try:
        handle.seek(start)
        remaining = end - start + 1
        while remaining > 0:
            chunk = handle.read(min(_CHUNK_SIZE, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk
    finally:
        handle.close()


@app.get("/video/{film_id}/playback")
def video_playback_endpoint(film_id: str, request: Request, response: Response) -> dict:
    """Resolve once per player; preparing media is strictly an offline operation."""
    source = _video_source(film_id, request)
    url = f"/video/{film_id}"
    for _ in range(2):
        prepared = _prepared_video(source, film_id, request)
        if prepared is None:
            break
        try:
            url += f"?representation={_playback_token(prepared)}"
            break
        except (OSError, PlaybackPreparationError):
            continue
    response.headers["Cache-Control"] = "no-store"
    return {"url": url}


@app.get("/video/{film_id}")
def video_endpoint(
    film_id: str, request: Request, representation: str | None = None,
) -> StreamingResponse:
    """Stream original bytes or one explicitly pinned, immutable derivative."""
    path = _video_source(film_id, request)
    extra_headers = {}
    prepared_handle = None
    if representation is not None:
        path, prepared_handle, file_size = _open_prepared_video(path, film_id, request, representation)
        extra_headers = {"Cache-Control": "no-store", "ETag": f'"{representation}"'}
    else:
        file_size = path.stat().st_size

    media_type, _ = mimetypes.guess_type(str(path))
    media_type = media_type or "application/octet-stream"

    range_header = request.headers.get("Range")
    try:
        start, end = _parse_range(range_header, file_size) if range_header else (0, file_size - 1)
    except HTTPException:
        if prepared_handle is not None:
            prepared_handle.close()
        raise
    stream = (_stream_prepared_file(prepared_handle, start, end) if prepared_handle is not None
              else _stream_file(path, start, end))
    cleanup = BackgroundTask(prepared_handle.close) if prepared_handle is not None else None
    if range_header:
        content_length = end - start + 1
        return StreamingResponse(
            stream,
            status_code=206,
            media_type=media_type,
            background=cleanup,
            headers={
                **extra_headers,
                "Content-Range": f"bytes {start}-{end}/{file_size}",
                "Accept-Ranges": "bytes",
                "Content-Length": str(content_length),
            },
        )

    return StreamingResponse(
        stream,
        status_code=200,
        media_type=media_type,
        background=cleanup,
        headers={
            **extra_headers,
            "Content-Length": str(file_size),
            "Accept-Ranges": "bytes",
        },
    )


def _library_source_paths(db: Any) -> list[Path]:
    """Read every registered source, including unpublished or external films."""
    if "films" not in table_names(db):
        return []
    rows = db.open_table("films").search().select(["path"]).limit(None).to_list()
    paths = []
    for row in rows:
        raw_path = row.get("path")
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise ValueError("A registered film is missing its source path")
        paths.append(Path(raw_path))
    return paths


@app.get("/library/storage")
def library_storage_endpoint(request: Request, refresh: bool = False) -> dict[str, Any]:
    """Start or poll a coalesced background scan without delaying library reads."""
    return request.app.state.library_storage.get(refresh=refresh)


@app.get("/library/scenes")
def library_scenes_endpoint(
    request: Request,
    film_id: Annotated[list[str], Query(min_length=1, max_length=1_000)],
    limit: int | None = None,
) -> dict[str, Any]:
    """Browse explicit movie scopes without waiting for or loading encoders."""
    config: Config = request.app.state.config
    result_limit = _resolve_api_result_limit(config, limit)
    try:
        results = _browse_scenes(
            request.app.state.db, config, film_ids=film_id,
            result_limit=_result_probe_limit(config, result_limit),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _search_response(_with_film_titles(request, results), config, result_limit)


@app.get("/library")
def library_endpoint(request: Request) -> list[dict]:
    """Return the union of indexed films and source-directory video files.

    Indexed films remain searchable even when their source lives outside the
    configured ``films_dir``.  Conversely, supported files in ``films_dir``
    remain visible as ``not_indexed`` until ingestion completes.
    """
    config: Config = request.app.state.config
    db = request.app.state.db
    films_dir: Path = config.paths.films_dir

    try:
        if "films" in table_names(db):
            films_tbl = db.open_table("films")
            indexed_rows = films_tbl.search().limit(100_000).to_list()
        else:
            indexed_rows = []
        ready_film_ids = _ready_films_for_units_version(request, db)
        indexed_rows = [
            row
            for row in indexed_rows
            if str(row.get("film_id") or "") in ready_film_ids
        ]
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail="Indexed film metadata is temporarily unavailable",
        ) from exc

    def path_key(path: Path) -> str:
        return str(path.resolve()).casefold()

    def size_gb(path: Path) -> float:
        try:
            return round(path.stat().st_size / (1024 ** 3), 1)
        except OSError:
            # Keep indexed metadata visible when its source drive is offline.
            return 0.0

    result_by_path: dict[str, dict] = {}
    try:
        for film in indexed_rows:
            raw_path = film.get("path")
            film_id = film.get("film_id")
            if not isinstance(raw_path, str) or not raw_path:
                raise ValueError("indexed film is missing its source path")
            if not isinstance(film_id, str) or not film_id:
                raise ValueError("indexed film is missing its film_id")

            source_path = Path(raw_path)
            result_by_path[path_key(source_path)] = {
                "filename": source_path.name,
                "path": str(source_path),
                "size_gb": size_gb(source_path),
                "status": "indexed",
                "film_id": film_id,
                "title": film.get("title") or source_path.stem,
                "duration": film.get("duration"),
            }
    except (OSError, TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=503,
            detail="Indexed film metadata is temporarily unavailable",
        ) from exc

    if films_dir.exists():
        try:
            source_files = [
                path
                for path in films_dir.iterdir()
                if path.suffix.lower() in VIDEO_EXTENSIONS
            ]
        except OSError as exc:
            raise HTTPException(
                status_code=503,
                detail="Film source directory is temporarily unavailable",
            ) from exc

        for source_path in source_files:
            key = path_key(source_path)
            if key in result_by_path:
                # Refresh path spelling and size from the configured source
                # directory while preserving the authoritative index metadata.
                result_by_path[key]["filename"] = source_path.name
                result_by_path[key]["path"] = str(source_path)
                result_by_path[key]["size_gb"] = size_gb(source_path)
                continue
            result_by_path[key] = {
                "filename": source_path.name,
                "path": str(source_path),
                "size_gb": size_gb(source_path),
                "status": "not_indexed",
                "film_id": None,
                "title": source_path.stem,
                "duration": None,
            }

    return sorted(
        result_by_path.values(),
        key=lambda item: (str(item["title"]).casefold(), item["filename"].casefold()),
    )


_REPO_ROOT = Path(__file__).resolve().parents[2]



def _incoming_candidate(
    root: Path,
    release_entry: Path,
    videos: list[Path],
) -> dict:
    sized = [(path.stat().st_size, path) for path in videos]
    size, primary = max(sized, key=lambda item: (item[0], str(item[1]).casefold()))
    release_title, release_year, release_edition = _release_suggestion(
        release_entry.name
    )
    primary_title, primary_year, primary_edition = _release_suggestion(primary.name)
    if release_entry.is_file() or (release_year is None and primary_year is not None):
        title, year, edition = primary_title, primary_year, primary_edition
    else:
        title, year, edition = release_title, release_year, release_edition
    try:
        suggested_filename = _canonical_film_filename(
            title, year, edition, primary.suffix
        )
    except ValueError:
        suggested_filename = primary.name
    release_dir = release_entry if release_entry.is_dir() else None
    _automatic_subtitle, subtitle_review_candidates = _resolve_external_sidecars(
        root,
        primary,
        release_dir,
    )
    return {
        "relative_path": primary.relative_to(root).as_posix(),
        "filename": primary.name,
        "size_gb": round(size / (1024 ** 3), 2),
        "suggested_title": title,
        "suggested_year": year,
        "suggested_edition": edition,
        "suggested_filename": suggested_filename,
        "extra_video_count": len(videos) - 1,
        "subtitle_review_candidates": [
            {
                "relative_path": candidate.path.relative_to(root).as_posix(),
                "filename": candidate.path.name,
                "excerpt": candidate.excerpt,
                "validation": candidate.validation.summary,
            }
            for candidate in subtitle_review_candidates
        ],
    }


def _resolve_incoming_file(config: Config, raw_relative_path: str) -> Path:
    relative_path = Path(raw_relative_path)
    if (
        not raw_relative_path.strip()
        or relative_path.is_absolute()
        or bool(relative_path.drive)
        or ".." in relative_path.parts
    ):
        raise HTTPException(status_code=400, detail="Invalid incoming relative path")
    if relative_path.parts and relative_path.parts[0].casefold() == ".scene-recall-managed":
        raise HTTPException(status_code=409, detail="Use the managed download queue to review this acquisition")

    root = config.paths.incoming_dir.resolve()
    unresolved = root / relative_path
    if unresolved.is_symlink():
        raise HTTPException(status_code=400, detail="Incoming symlinks are not supported")
    try:
        source = unresolved.resolve(strict=True)
        relative_source = source.relative_to(root)
    except (OSError, ValueError):
        raise HTTPException(status_code=404, detail="Incoming film was not found") from None
    if (
        len(relative_source.parts) > 1
        and (root / relative_source.parts[0] / _IMPORTED_RELEASE_MARKER).is_file()
    ):
        raise HTTPException(status_code=409, detail="Release was already imported")
    if not _is_regular_video(source):
        raise HTTPException(status_code=400, detail="Incoming path is not a supported video file")
    return source


def _resolve_library_film(config: Config, raw_path: str) -> Path:
    films_root = config.paths.films_dir.resolve()
    supplied = Path(raw_path)
    unresolved = supplied if supplied.is_absolute() else films_root / supplied
    if unresolved.is_symlink():
        raise HTTPException(status_code=400, detail="Film symlinks are not supported")
    try:
        path = unresolved.resolve(strict=True)
    except OSError:
        raise HTTPException(status_code=400, detail="Film file does not exist") from None
    if path.parent != films_root or not _is_regular_video(path):
        raise HTTPException(
            status_code=400,
            detail="Film must be a supported file directly inside the library",
        )
    return path


def _enqueue_or_conflict(queue: _IngestQueue, path: Path) -> dict:
    try:
        return queue.enqueue(path)
    except _DuplicateIngestError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None


def _run_ingest_subprocess(
    path: Path,
    append_log: Callable[[str], None],
) -> None:
    """Run one low-priority CLI ingest and stream its bounded progress log."""
    popen_kwargs: dict[str, Any] = {}
    if sys.platform == "win32":
        popen_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    try:
        process = subprocess.Popen(
            [sys.executable, "-u", "-m", "pipeline.cli", "ingest", str(path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(_REPO_ROOT),
            **popen_kwargs,
        )
    except OSError as exc:
        raise RuntimeError(f"could not start ingest process: {exc}") from exc

    assert process.stdout is not None
    log_tail: deque[str] = deque(maxlen=5)
    try:
        for raw_line in process.stdout:
            line = raw_line.rstrip()
            if line:
                log_tail.append(line)
                append_log(line)
        returncode = process.wait()
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        process.stdout.close()
    if returncode != 0:
        tail = " | ".join(log_tail) or "no output"
        raise RuntimeError(f"ingest exited with code {returncode}: {tail}")


@app.get("/incoming")
def incoming_endpoint(request: Request) -> list[dict]:
    """Discover candidate films without probing or hashing their contents."""
    config: Config = request.app.state.config
    incoming_root = config.paths.incoming_dir
    if not incoming_root.exists():
        return []
    try:
        root = incoming_root.resolve()
        candidates: list[dict] = []
        for entry in root.iterdir():
            if entry.name.casefold() == ".scene-recall-managed":
                continue
            if _is_link_or_junction(entry):
                continue
            if _is_regular_video(entry):
                candidates.append(_incoming_candidate(root, entry, [entry]))
                continue
            if not entry.is_dir():
                continue
            if (entry / _IMPORTED_RELEASE_MARKER).is_file():
                continue
            videos = _videos_in_release(root, entry)
            if videos:
                candidates.append(_incoming_candidate(root, entry, videos))
    except OSError as exc:
        raise HTTPException(
            status_code=503,
            detail="Incoming film directory is temporarily unavailable",
        ) from exc
    return sorted(
        candidates,
        key=lambda item: (
            str(item["suggested_title"]).casefold(),
            str(item["relative_path"]).casefold(),
        ),
    )


@app.post("/films/import")
def import_film_endpoint(body: FilmImportRequest, request: Request) -> dict:
    """Move one reviewed incoming film into the flat source library."""
    if body.confirm_finished is not True:
        raise HTTPException(
            status_code=400,
            detail="Confirm the torrent/download is finished before importing",
        )
    if body.year < 1888 or body.year > 2100:
        raise HTTPException(status_code=400, detail="Year must be between 1888 and 2100")

    config: Config = request.app.state.config
    source = _resolve_incoming_file(config, body.relative_path)
    def source_fingerprint():
        if _is_link_or_junction(source):
            raise HTTPException(status_code=409, detail="Incoming film changed during validation")
        try:
            stat = source.stat()
        except OSError as exc:
            raise HTTPException(status_code=409, detail="Incoming film changed during validation") from exc
        return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)
    verified_source = source_fingerprint()
    try:
        filename = _canonical_film_filename(
            body.title, body.year, body.edition, source.suffix
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None

    try:
        incoming_root = config.paths.incoming_dir.resolve()
        relative_source = source.relative_to(incoming_root)
        release_dir = (
            incoming_root / relative_source.parts[0]
            if len(relative_source.parts) > 1
            else None
        )
        automatic_subtitle, subtitle_review_candidates = (
            _resolve_external_sidecars(incoming_root, source, release_dir)
        )
        # Inventory stays metadata-only. Probe just this selected film, on import,
        # and recompute before any source or sidecar mutation.
        media_duration = None
        if subtitle_review_candidates and not isinstance(body.subtitle_decision, SubtitleSkipDecision):
            media_duration = _probe_intake_duration(source)
            automatic_subtitle, subtitle_review_candidates = _resolve_external_sidecars(
                incoming_root, source, release_dir, media_duration=media_duration)
        if isinstance(body.subtitle_decision, SubtitleSkipDecision):
            subtitle_source = None
        elif isinstance(body.subtitle_decision, SubtitleUseDecision):
            eligible = [candidate.path for candidate in subtitle_review_candidates]
            if automatic_subtitle is not None:
                eligible.append(automatic_subtitle)
            subtitle_source = next((path for path in eligible
                if path.relative_to(incoming_root).as_posix() == body.subtitle_decision.relative_path), None)
            if subtitle_source is None:
                raise HTTPException(
                    status_code=409,
                    detail="Subtitle choices changed; review this film again",
                )
        elif automatic_subtitle is not None or isinstance(body.subtitle_decision, SubtitleAutoDecision):
            subtitle_source = automatic_subtitle
        elif subtitle_review_candidates:
            raise HTTPException(status_code=409,
                detail="Choose Automatic to check subtitles, select a known English track, or skip subtitles")
        else:
            subtitle_source = None
        subtitle_validation = validate_external_srt(subtitle_source, media_duration) if subtitle_source else None
        if subtitle_validation and (not subtitle_validation.valid or (
                not isinstance(body.subtitle_decision, SubtitleUseDecision) and not subtitle_validation.automatic_eligible)):
            raise HTTPException(status_code=409, detail="Subtitle changed during validation; review this film again")

        films_root = config.paths.films_dir.resolve()
        films_root.mkdir(parents=True, exist_ok=True)
        films_root = films_root.resolve()
        destination = films_root / filename
        if destination.exists() or any(
            child.name.casefold() == filename.casefold()
            for child in films_root.iterdir()
        ):
            raise HTTPException(
                status_code=409,
                detail=f"A film named {filename!r} already exists",
            )
        if source.stat().st_dev != films_root.stat().st_dev:
            raise HTTPException(
                status_code=400,
                detail="Incoming and films directories must be on the same drive",
            )
        marker = release_dir / _IMPORTED_RELEASE_MARKER if release_dir else None
        subtitle_destination = (
            destination.with_name(destination.stem + ".en.srt")
            if subtitle_source is not None
            else None
        )
        if subtitle_destination is not None and subtitle_destination.exists():
            raise HTTPException(
                status_code=409,
                detail=f"A subtitle named {subtitle_destination.name!r} already exists",
            )
        marker_created = False
        subtitle_copied = False
        if source_fingerprint() != verified_source:
            raise HTTPException(status_code=409, detail="Incoming film changed during validation")
        if marker is not None:
            try:
                with marker.open("x", encoding="utf-8") as marker_file:
                    marker_file.write(filename + "\n")
                marker_created = True
            except FileExistsError:
                raise HTTPException(
                    status_code=409,
                    detail="Release was already imported",
                ) from None
        try:
            if subtitle_source is not None and subtitle_destination is not None:
                _copy_file_no_replace(subtitle_source, subtitle_destination,
                    expected_sha256=subtitle_validation.sha256)
                subtitle_copied = True
            if source_fingerprint() != verified_source:
                raise HTTPException(status_code=409, detail="Incoming film changed during validation")
            _move_file_no_replace(source, destination)
        except Exception:
            if subtitle_copied and subtitle_destination is not None:
                subtitle_destination.unlink(missing_ok=True)
            if marker_created and marker is not None:
                marker.unlink(missing_ok=True)
            raise
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except FileExistsError:
        raise HTTPException(status_code=409, detail="Destination film already exists") from None
    except PermissionError as exc:
        raise HTTPException(
            status_code=409,
            detail="Film could not be moved; make sure the torrent is finished",
        ) from exc
    except OSError as exc:
        raise HTTPException(status_code=503, detail="Film could not be moved") from exc

    job = (
        _enqueue_or_conflict(request.app.state.ingest_queue, destination)
        if body.ingest
        else None
    )
    return {
        "path": str(destination),
        "filename": destination.name,
        "subtitle_filename": (
            subtitle_destination.name if subtitle_destination is not None else None
        ),
        "job": job,
    }


@app.post("/ingest")
def ingest_endpoint(body: IngestRequest, request: Request) -> dict:
    """Queue one direct child of films_dir for serialized ingestion."""
    config: Config = request.app.state.config
    path = _resolve_library_film(config, body.path)
    return _enqueue_or_conflict(request.app.state.ingest_queue, path)


@app.get("/ingest/jobs")
def ingest_jobs_endpoint(request: Request) -> list[dict]:
    """Return every job retained for this API process lifetime."""
    return request.app.state.ingest_queue.snapshots()
