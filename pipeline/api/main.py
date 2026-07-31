"""main.py — FastAPI application for scene-recall semantic search.

Endpoints
---------
GET /search?q=...                   Dense semantic search; {"results": [...]}
POST /search/image                  Reference-still composition search
GET /unit/{unit_id}                 Full unit record from the LanceDB units table
GET /media/keyframe/{shot_id}/{n}   Serve a WebP keyframe image
GET /media/preview/{shot_id}        Serve a WebM preview clip
GET /video/{film_id}                Stream source video with HTTP range support
GET /library                        List indexed films plus source-directory files
POST /ingest                        Start a background ingest job for a film
GET /ingest/jobs                    Poll status of all active ingest jobs

Start with::

    uv run uvicorn pipeline.api.main:app --reload
"""

from __future__ import annotations

import asyncio
import json
import mimetypes
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from contextlib import asynccontextmanager
from io import BytesIO
from pathlib import Path
from typing import Any, Iterator

from dotenv import load_dotenv
from PIL import Image, ImageOps, UnidentifiedImageError

load_dotenv()

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from lancedb.expr import col, lit
from pydantic import BaseModel

from pipeline.config import VIDEO_EXTENSIONS, Config, load_config
from pipeline.index.writer import open_db, published_film_ids, table_names
from pipeline.search.retrieve import (
    search as _search,
    search_by_image as _search_by_image,
)


# ---------------------------------------------------------------------------
# Module-level state
# ---------------------------------------------------------------------------

_ingest_jobs: dict[str, dict] = {}


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


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Open config and DB on startup; yield; clean up on shutdown.

    ``SCENE_RECALL_SKIP_WARMUP`` (set by the test suite) skips the encoder
    warmup thread: tests must not load real model weights, and their
    endpoints should be immediately ready.
    """
    config: Config = load_config()
    db = open_db(config)
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
    app.state.ready_units_version = None
    app.state.ready_film_ids = frozenset()
    app.state.image_search_lock = asyncio.Lock()
    app.state.image_search_slots = asyncio.Queue(maxsize=2)
    app.state.image_search_slots.put_nowait(None)
    app.state.image_search_slots.put_nowait(None)
    yield
    # LanceDB connections do not require explicit closing.


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------

app = FastAPI(title="scene-recall", version="0.1.0", lifespan=lifespan)

# Allow all origins for Next.js dev server (tighten in production).
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
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


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class IngestRequest(BaseModel):
    path: str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.get("/search")
def search_endpoint(
    q: str,
    request: Request,
    film_id: list[str] | None = Query(default=None),
) -> dict:
    """Run hybrid search in FastAPI's worker threadpool."""
    _require_search_ready(request)
    config: Config = request.app.state.config
    db = request.app.state.db
    results = _search(q, db, config, film_ids=film_id)
    return {"results": results}


@app.post("/search/image")
async def image_search_endpoint(
    request: Request,
    film_id: list[str] | None = Query(default=None),
    exclude_unit_id: str | None = Query(default=None),
) -> dict:
    """Find content with similar framing using a raw JPEG, PNG, or WebP."""
    _require_search_ready(request)
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

    try:
        request.app.state.image_search_slots.get_nowait()
    except asyncio.QueueEmpty:
        raise HTTPException(
            status_code=429,
            detail="Reference search is busy; try again in a moment",
        ) from None

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
        config: Config = request.app.state.config
        db = request.app.state.db
        async with request.app.state.image_search_lock:
            if await request.is_disconnected():
                raise HTTPException(
                    status_code=499,
                    detail="Client disconnected before image search",
                )
            work = asyncio.create_task(
                asyncio.to_thread(
                    _search_by_image,
                    image,
                    db,
                    config,
                    film_ids=film_id,
                    exclude_unit_id=exclude_unit_id,
                )
            )
            try:
                results = await asyncio.shield(work)
            except asyncio.CancelledError:
                # Keep the gate occupied until the worker really finishes;
                # cancelling ``to_thread`` alone does not stop its GPU work.
                await work
                raise
        return {"results": results}
    finally:
        request.app.state.image_search_slots.put_nowait(None)


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


def _run_ingest_subprocess(job_id: str, path: Path) -> None:
    """Run one film ingest in a low-priority child process, streaming progress.

    The pipeline's heavy stages (TransNetV2, Whisper, the visual encoder)
    previously ran inside the API server process at normal priority, which
    could starve the desktop.  A child process at below-normal priority keeps
    the machine responsive and releases model memory when the job finishes.
    """
    job = _ingest_jobs[job_id]
    # The CLI ingest command lowers its own priority; the subprocess exists
    # for process isolation and model-memory release.
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
        job["status"] = "error"
        job["error"] = f"could not start ingest process: {exc}"
        job["finished_at"] = time.time()
        return

    assert process.stdout is not None
    for raw_line in process.stdout:
        line = raw_line.rstrip()
        if line:
            job["log"].append(line)

    returncode = process.wait()
    if returncode == 0:
        job["status"] = "done"
    else:
        tail = " | ".join(list(job["log"])[-5:]) or "no output"
        job["status"] = "error"
        job["error"] = f"ingest exited with code {returncode}: {tail}"
    job["finished_at"] = time.time()


@app.post("/ingest")
async def ingest_endpoint(body: IngestRequest) -> dict:
    """Start a background ingest pipeline job for one film file."""
    path = Path(body.path)

    if not path.exists() or path.suffix.lower() not in VIDEO_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Path does not exist or is not a supported video file")

    # Reject if a running job for this path already exists.
    for job in _ingest_jobs.values():
        if job["path"] == str(path) and job["status"] == "running":
            raise HTTPException(status_code=409, detail="Already ingesting this file")

    job_id = str(uuid.uuid4())[:8]
    _ingest_jobs[job_id] = {
        "job_id": job_id,
        "path": str(path),
        "filename": path.name,
        "status": "running",
        "started_at": time.time(),
        "finished_at": None,
        "error": None,
        "log": deque(maxlen=_INGEST_LOG_TAIL_LINES),
    }

    loop = asyncio.get_running_loop()
    loop.run_in_executor(None, _run_ingest_subprocess, job_id, path)

    return {"job_id": job_id, "status": "running"}


@app.get("/ingest/jobs")
async def ingest_jobs_endpoint() -> list[dict]:
    """Return all active ingest jobs; prune completed jobs older than 5 minutes."""
    now = time.time()
    stale = [
        jid
        for jid, job in _ingest_jobs.items()
        if job["status"] in ("done", "error")
        and job["finished_at"] is not None
        and (now - job["finished_at"]) > 300
    ]
    for jid in stale:
        del _ingest_jobs[jid]

    return [
        {
            **job,
            "log": list(job["log"]),
            "progress": job["log"][-1] if job["log"] else None,
        }
        for job in _ingest_jobs.values()
    ]
