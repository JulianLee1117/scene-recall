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
GET /library                        List indexed films plus source-directory files
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
import json
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from contextlib import asynccontextmanager
from io import BytesIO
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Annotated, Any, Callable, Iterator, Literal, Sequence

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
from fastapi.responses import FileResponse, StreamingResponse
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
from pipeline.index.writer import (
    ensure_search_indexes,
    open_db,
    published_film_ids,
    table_names,
)
from pipeline.ingest.subtitles import external_srt_is_usable
from pipeline.search.retrieve import (
    resolve_result_limit,
    search as _search,
    search_by_image as _search_by_image,
)
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
    except Exception as exc:
        print(f"[startup] visual encoder warmup failed: {exc}", flush=True)
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
async def lifespan(app: FastAPI):
    """Open config and DB on startup; yield; clean up on shutdown.

    ``SCENE_RECALL_SKIP_WARMUP`` (set by the test suite) skips the encoder
    warmup thread: tests must not load real model weights, and their
    endpoints should be immediately ready.
    """
    config: Config = load_config()
    _preflight_runtime_paths(config)
    db = open_db(config)
    # One-time legacy migration plus a correctness check for rows added by a
    # prior interrupted ingest. Search traffic is not accepted until native
    # FTS covers the complete units table.
    ensure_search_indexes(db)
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
    app.state.config = config
    app.state.db = db
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
    app.state.ingest_queue = _IngestQueue(_run_ingest_subprocess)
    try:
        yield
    finally:
        app.state.ingest_queue.close()
        # LanceDB connections do not require explicit closing.


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------

app = FastAPI(title="scene-recall", version="0.1.0", lifespan=lifespan)

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
        rows = (
            db.open_table("frames")
            .search()
            .where(
                (col("unit_id") == lit(unit_id))
                & (col("frame_index") == lit(frame_index))
            )
            .select(["timestamp"])
            .limit(2)
            .to_list()
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


class FilmImportRequest(BaseModel):
    relative_path: str
    title: str
    year: int
    edition: str | None = None
    ingest: bool = True
    confirm_finished: bool


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
            )
            if uses_serialized_image_work
            else asyncio.to_thread(
                _execute_search_recipe,
                clauses,
                db,
                config,
                film_ids=film_ids,
                result_limit=result_limit,
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

    path = _safe_media_path(
        config.paths.assets_dir,
        str(unit["film_id"]),
        "keyframes",
        f"{shot_id}_{n}.webp",
    )
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"Keyframe not found: {shot_id}_{n}.webp")
    return FileResponse(str(path), media_type="image/webp")


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


@app.get("/video/{film_id}")
def video_endpoint(film_id: str, request: Request) -> StreamingResponse:
    """Stream a source video file with HTTP range-request support."""
    db = request.app.state.db
    tbl = db.open_table("films")
    rows = tbl.search().where(col("film_id") == lit(film_id)).to_list()
    if not rows:
        raise HTTPException(status_code=404, detail=f"Film {film_id!r} not found")

    path = Path(rows[0]["path"])
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Video file not found: {path.name}")

    file_size = path.stat().st_size
    media_type, _ = mimetypes.guess_type(str(path))
    media_type = media_type or "application/octet-stream"

    range_header = request.headers.get("Range")
    if range_header:
        start, end = _parse_range(range_header, file_size)
        content_length = end - start + 1
        return StreamingResponse(
            _stream_file(path, start, end),
            status_code=206,
            media_type=media_type,
            headers={
                "Content-Range": f"bytes {start}-{end}/{file_size}",
                "Accept-Ranges": "bytes",
                "Content-Length": str(content_length),
            },
        )

    return StreamingResponse(
        _stream_file(path, 0, file_size - 1),
        status_code=200,
        media_type=media_type,
        headers={
            "Content-Length": str(file_size),
            "Accept-Ranges": "bytes",
        },
    )


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


_INGEST_LOG_TAIL_LINES = 30
_REPO_ROOT = Path(__file__).resolve().parents[2]
_IMPORTED_RELEASE_MARKER = ".scene-recall-imported"
_WINDOWS_INVALID_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WINDOWS_RESERVED_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{number}" for number in range(1, 10)}
    | {f"LPT{number}" for number in range(1, 10)}
)
_RELEASE_YEAR = re.compile(r"(?<!\d)((?:18|19|20)\d{2})(?!\d)")
_RELEASE_TECHNICAL = re.compile(
    r"\b(?:480p|576p|720p|1080[pi]|2160p|4k|uhd|bluray|blu-ray|brrip|"
    r"webrip|web-dl|webdl|dvdrip|hdtv|remux|x26[45]|h[.-]?26[45]|hevc|"
    r"av1|hdr10?|dolby[ .]?vision|aac|dts|truehd|atmos)\b",
    re.IGNORECASE,
)
_EDITION_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bdirector'?s\s+cut\b", re.IGNORECASE), "Director's Cut"),
    (re.compile(r"\bextended(?:\s+(?:cut|edition))?\b", re.IGNORECASE), "Extended"),
    (re.compile(r"\bfinal\s+cut\b", re.IGNORECASE), "Final Cut"),
    (re.compile(r"\bcriterion(?:\s+collection)?\b", re.IGNORECASE), "Criterion"),
    (re.compile(r"\bunrated\b", re.IGNORECASE), "Unrated"),
    (re.compile(r"\btheatrical(?:\s+(?:cut|edition))?\b", re.IGNORECASE), "Theatrical"),
    (re.compile(r"\bspecial\s+edition\b", re.IGNORECASE), "Special Edition"),
    (re.compile(r"\bremaster(?:ed)?\b", re.IGNORECASE), "Remastered"),
)


class _DuplicateIngestError(RuntimeError):
    pass


class _IngestQueue:
    """Small in-memory FIFO with one dedicated worker thread."""

    def __init__(
        self,
        runner: Callable[[Path, Callable[[str], None]], None],
    ) -> None:
        self._runner = runner
        self._jobs: dict[str, dict[str, Any]] = {}
        self._pending: deque[str] = deque()
        self._condition = threading.Condition()
        self._worker: threading.Thread | None = None
        self._closed = False

    def enqueue(self, path: Path) -> dict:
        canonical_path = path.resolve()
        path_key = os.path.normcase(str(canonical_path))
        with self._condition:
            if self._closed:
                raise RuntimeError("ingest queue is shutting down")
            if any(
                job["_path_key"] == path_key
                and job["status"] in {"queued", "running"}
                for job in self._jobs.values()
            ):
                raise _DuplicateIngestError("Already queued for ingestion")

            job_id = str(uuid.uuid4())[:8]
            self._jobs[job_id] = {
                "job_id": job_id,
                "path": str(canonical_path),
                "_path_key": path_key,
                "filename": canonical_path.name,
                "status": "queued",
                "queued_at": time.time(),
                "started_at": None,
                "finished_at": None,
                "error": None,
                "log": deque(maxlen=_INGEST_LOG_TAIL_LINES),
            }
            self._pending.append(job_id)
            if self._worker is None or not self._worker.is_alive():
                self._worker = threading.Thread(
                    target=self._worker_loop,
                    daemon=True,
                    name="ingest-runner",
                )
                self._worker.start()
            self._condition.notify()
            return self._response_locked(self._jobs[job_id])

    def snapshots(self) -> list[dict]:
        with self._condition:
            return [self._response_locked(job) for job in self._jobs.values()]

    def close(self) -> None:
        with self._condition:
            self._closed = True
            self._condition.notify_all()

    def _append_log(self, job_id: str, line: str) -> None:
        line = line.rstrip()
        if not line:
            return
        with self._condition:
            self._jobs[job_id]["log"].append(line)

    def _response_locked(self, job: dict[str, Any]) -> dict:
        queued_ids = list(self._pending)
        try:
            queue_position: int | None = queued_ids.index(job["job_id"]) + 1
        except ValueError:
            queue_position = None
        log = list(job["log"])
        return {
            key: value
            for key, value in job.items()
            if key not in {"_path_key", "log"}
        } | {
            "queue_position": queue_position,
            "log": log,
            "progress": log[-1] if log else None,
        }

    def _worker_loop(self) -> None:
        while True:
            with self._condition:
                while not self._pending and not self._closed:
                    self._condition.wait()
                if self._closed:
                    return
                job_id = self._pending.popleft()
                job = self._jobs[job_id]
                job["status"] = "running"
                job["started_at"] = time.time()
                path = Path(job["path"])

            error: str | None = None
            try:
                self._runner(
                    path,
                    lambda line, current=job_id: self._append_log(current, line),
                )
            except Exception as exc:
                error = str(exc) or type(exc).__name__

            with self._condition:
                job = self._jobs[job_id]
                job["status"] = "error" if error else "done"
                job["error"] = error
                job["finished_at"] = time.time()


def _sanitize_filename_component(value: str, field: str) -> str:
    cleaned = value.replace(":", " - ")
    cleaned = _WINDOWS_INVALID_FILENAME.sub(" ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    if not cleaned:
        raise ValueError(f"{field} cannot be empty")
    if cleaned.upper() in _WINDOWS_RESERVED_NAMES:
        cleaned = f"_{cleaned}"
    return cleaned


def _canonical_film_filename(
    title: str,
    year: int | None,
    edition: str | None,
    extension: str,
) -> str:
    clean_title = _sanitize_filename_component(title, "title")
    clean_edition = (
        _sanitize_filename_component(edition, "edition") if edition else None
    )
    if extension.lower() not in VIDEO_EXTENSIONS:
        raise ValueError("unsupported video extension")
    stem = clean_title
    if year is not None:
        stem += f" ({year})"
    if clean_edition:
        stem += f" [{clean_edition}]"
    filename = stem + extension.lower()
    if len(filename) > 255:
        raise ValueError("canonical filename is longer than 255 characters")
    return filename


def _release_suggestion(label: str) -> tuple[str, int | None, str | None]:
    """Parse only obvious year, quality, and edition markers from a release."""
    stem = Path(label).stem if Path(label).suffix.lower() in VIDEO_EXTENSIONS else label
    normalized = re.sub(r"[._]+", " ", stem)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    technical_match = _RELEASE_TECHNICAL.search(normalized)
    # Numeric film titles are common. The release year is conventionally the
    # last plausible year before quality/codec metadata, not the first number.
    year_search_end = technical_match.start() if technical_match else len(normalized)
    year_matches = list(_RELEASE_YEAR.finditer(normalized, 0, year_search_end))
    year_match = year_matches[-1] if year_matches else None
    year = int(year_match.group(1)) if year_match else None
    cut_at = len(normalized)
    if year_match:
        cut_at = min(cut_at, year_match.start())
    if technical_match:
        cut_at = min(cut_at, technical_match.start())
    raw_title = re.sub(r"[\[\](){}]+", " ", normalized[:cut_at])
    raw_title = re.sub(r"\s+", " ", raw_title).strip(" -")
    if not raw_title:
        raw_title = normalized
    if raw_title.islower() or raw_title.isupper():
        raw_title = raw_title.title()

    edition_tail = normalized[year_match.end():] if year_match else ""
    edition = next(
        (
            display
            for pattern, display in _EDITION_PATTERNS
            if pattern.search(edition_tail)
        ),
        None,
    )
    return raw_title, year, edition


def _is_regular_video(path: Path) -> bool:
    try:
        return (
            not path.is_symlink()
            and path.is_file()
            and path.suffix.lower() in VIDEO_EXTENSIONS
        )
    except OSError:
        return False


def _is_link_or_junction(path: Path) -> bool:
    """Keep discovery on the configured volume and out of reparse trees."""
    try:
        return path.is_symlink() or (
            hasattr(path, "is_junction") and path.is_junction()
        )
    except OSError:
        return True


def _videos_in_release(root: Path, release: Path) -> list[Path]:
    videos: list[Path] = []
    for directory, child_dirs, filenames in os.walk(release, followlinks=False):
        directory_path = Path(directory)
        child_dirs[:] = [
            name
            for name in child_dirs
            if not _is_link_or_junction(directory_path / name)
        ]
        for filename in filenames:
            path = directory_path / filename
            if (
                path.suffix.lower() in VIDEO_EXTENSIONS
                and _is_regular_video(path)
                and path.resolve().is_relative_to(root)
            ):
                videos.append(path)
    return videos


def _subtitle_files_in_release(root: Path, release: Path) -> list[Path]:
    """Return safe SRT candidates without following release reparse points."""
    subtitles: list[Path] = []
    for directory, child_dirs, filenames in os.walk(release, followlinks=False):
        directory_path = Path(directory)
        child_dirs[:] = [
            name
            for name in child_dirs
            if not _is_link_or_junction(directory_path / name)
        ]
        for filename in filenames:
            path = directory_path / filename
            try:
                if (
                    path.suffix.lower() == ".srt"
                    and path.is_file()
                    and not path.is_symlink()
                    and path.resolve().is_relative_to(root)
                ):
                    subtitles.append(path)
            except OSError:
                continue
    return subtitles


def _select_english_sidecar(
    incoming_root: Path,
    source: Path,
    release_dir: Path | None,
) -> Path | None:
    """Choose one source-associated English SRT, failing closed on ambiguity."""
    if release_dir is None:
        candidates = [
            source.with_name(source.stem + suffix)
            for suffix in (".en.srt", ".eng.srt", ".english.srt", ".srt")
        ]
        candidates = [
            candidate
            for candidate in candidates
            if candidate.is_file() and not candidate.is_symlink()
        ]
    else:
        candidates = _subtitle_files_in_release(incoming_root, release_dir)
    candidates = [
        candidate for candidate in candidates if external_srt_is_usable(candidate)
    ]
    if not candidates:
        return None

    def label_tokens(label: str) -> set[str]:
        return {
            token
            for token in re.split(r"[^a-z0-9]+", label.casefold())
            if token
        }

    def tokens(path: Path) -> set[str]:
        return label_tokens(path.stem)

    english_markers = {"en", "eng", "english"}
    alternate_markers = {"commentary", "forced"}
    accessibility_markers = {"cc", "sdh", "hoh", "hearing", "impaired"}
    extra_markers = {
        "bonus",
        "deleted",
        "extra",
        "extras",
        "featurette",
        "interview",
        "sample",
        "trailer",
    }
    foreign_markers = {
        "arabic", "ara",
        "chinese", "chi", "zho",
        "czech", "cze", "ces",
        "danish", "dan",
        "dutch", "dut", "nld",
        "finnish", "fin",
        "french", "fre", "fra",
        "german", "ger", "deu",
        "greek", "gre", "ell",
        "hebrew", "heb",
        "hindi", "hin",
        "hungarian", "hun",
        "indonesian", "ind",
        "italian", "ita",
        "japanese", "jpn",
        "korean", "kor",
        "norwegian", "nor",
        "polish", "pol",
        "portuguese", "por",
        "romanian", "rum", "ron",
        "russian", "rus",
        "spanish", "spa",
        "swedish", "swe",
        "thai", "tha",
        "turkish", "tur",
        "ukrainian", "ukr",
        "vietnamese", "vie",
    }
    insignificant_title_tokens = {"a", "an", "and", "of", "the", "to"}
    source_title, source_year, _source_edition = _release_suggestion(source.name)
    source_identity = (
        label_tokens(source_title) - insignificant_title_tokens
    )
    if not source_identity:
        return None

    associated: list[Path] = []
    descriptors_by_path: dict[Path, set[str]] = {}
    for path in candidates:
        candidate_tokens = tokens(path)
        if not source_identity.issubset(candidate_tokens):
            continue
        candidate_years = {
            int(token)
            for token in candidate_tokens
            if re.fullmatch(r"(?:18|19|20)\d{2}", token)
        }
        if (
            source_year is not None
            and candidate_years
            and source_year not in candidate_years
        ):
            continue
        descriptors = candidate_tokens - source_identity
        if not (descriptors & english_markers):
            continue
        if descriptors & (alternate_markers | extra_markers | foreign_markers):
            continue
        associated.append(path)
        descriptors_by_path[path] = descriptors

    if not associated:
        return None
    if len(associated) == 1:
        return associated[0]

    # Prefer one ordinary full-dialogue track over an accessibility variant.
    # Multiple ordinary or multiple accessibility tracks remain ambiguous.
    standard = [
        path
        for path in associated
        if not (descriptors_by_path[path] & accessibility_markers)
    ]
    if len(standard) == 1:
        return standard[0]
    return None


def _copy_file_no_replace(source: Path, destination: Path) -> None:
    """Copy a small raw sidecar without replacing an existing peer."""
    destination_created = False
    try:
        with source.open("rb") as source_file, destination.open("xb") as target_file:
            destination_created = True
            shutil.copyfileobj(source_file, target_file)
        shutil.copystat(source, destination)
    except Exception:
        if destination_created:
            destination.unlink(missing_ok=True)
        raise


def _move_file_no_replace(source: Path, destination: Path) -> None:
    """Move a same-volume regular file without ever replacing a peer."""
    if os.name == "nt":
        # MoveFileEx without MOVEFILE_REPLACE_EXISTING is the behavior exposed
        # by Path.rename on Windows.
        source.rename(destination)
        return

    # POSIX rename replaces an existing destination, so create the destination
    # link exclusively and then remove the incoming name.
    os.link(source, destination, follow_symlinks=False)
    try:
        source.unlink()
    except OSError:
        destination.unlink(missing_ok=True)
        raise


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
    return {
        "relative_path": primary.relative_to(root).as_posix(),
        "filename": primary.name,
        "size_gb": round(size / (1024 ** 3), 2),
        "suggested_title": title,
        "suggested_year": year,
        "suggested_edition": edition,
        "suggested_filename": suggested_filename,
        "extra_video_count": len(videos) - 1,
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
    for raw_line in process.stdout:
        line = raw_line.rstrip()
        if line:
            log_tail.append(line)
            append_log(line)
    returncode = process.wait()
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
    try:
        filename = _canonical_film_filename(
            body.title, body.year, body.edition, source.suffix
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None

    try:
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
        incoming_root = config.paths.incoming_dir.resolve()
        relative_source = source.relative_to(incoming_root)
        release_dir = (
            incoming_root / relative_source.parts[0]
            if len(relative_source.parts) > 1
            else None
        )
        marker = release_dir / _IMPORTED_RELEASE_MARKER if release_dir else None
        subtitle_source = _select_english_sidecar(
            incoming_root,
            source,
            release_dir,
        )
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
                _copy_file_no_replace(subtitle_source, subtitle_destination)
                subtitle_copied = True
            _move_file_no_replace(source, destination)
        except Exception:
            if subtitle_copied and subtitle_destination is not None:
                subtitle_destination.unlink(missing_ok=True)
            if marker_created and marker is not None:
                marker.unlink(missing_ok=True)
            raise
    except HTTPException:
        raise
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
