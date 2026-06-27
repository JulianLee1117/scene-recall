"""main.py — FastAPI application for scene-recall semantic search.

Endpoints
---------
GET /search?q=...                   Dense semantic search; {"results": [...]}
GET /unit/{unit_id}                 Full unit record from the LanceDB units table
GET /media/keyframe/{shot_id}/{n}   Serve a WebP keyframe image
GET /media/preview/{shot_id}        Serve a WebM preview clip
GET /video/{film_id}                Stream source video with HTTP range support
GET /library                        List all video files in films_dir with index status
POST /ingest                        Start a background ingest job for a film
GET /ingest/jobs                    Poll status of all active ingest jobs

Start with::

    uv run uvicorn pipeline.api.main:app --reload
"""

from __future__ import annotations

import asyncio
import mimetypes
import re
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Iterator

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from lancedb.expr import col, lit
from pydantic import BaseModel

from pipeline.config import Config, load_config
from pipeline.index.writer import open_db
from pipeline.ingest.pipeline import run_pipeline
from pipeline.search.retrieve import search as _search


# ---------------------------------------------------------------------------
# Module-level state
# ---------------------------------------------------------------------------

_ingest_jobs: dict[str, dict] = {}

_VIDEO_EXTENSIONS: frozenset[str] = frozenset({
    ".mkv", ".mp4", ".avi", ".mov", ".m4v", ".webm"
})


# ---------------------------------------------------------------------------
# Lifespan — load config and open DB once at startup
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Open config and DB on startup; yield; clean up on shutdown."""
    config: Config = load_config()
    db = open_db(config)
    app.state.config = config
    app.state.db = db
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


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class IngestRequest(BaseModel):
    path: str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.get("/search")
async def search_endpoint(q: str, request: Request) -> dict:
    """Dense semantic search over unit text embeddings."""
    config: Config = request.app.state.config
    db = request.app.state.db
    results = _search(q, db, config)
    return {"results": results}


@app.get("/unit/{unit_id}")
async def unit_endpoint(unit_id: str, request: Request) -> dict:
    """Return the full unit record for *unit_id*."""
    db = request.app.state.db
    tbl = db.open_table("units")
    rows = tbl.search().where(col("unit_id") == lit(unit_id)).to_list()
    if not rows:
        raise HTTPException(status_code=404, detail=f"Unit {unit_id!r} not found")
    return dict(rows[0])


@app.get("/media/keyframe/{shot_id}/{n}")
async def keyframe_endpoint(shot_id: str, n: int, request: Request) -> FileResponse:
    """Serve the *n*-th WebP keyframe for *shot_id*."""
    config: Config = request.app.state.config
    path = config.paths.assets_dir / "keyframes" / f"{shot_id}_{n}.webp"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Keyframe not found: {shot_id}_{n}.webp")
    return FileResponse(str(path), media_type="image/webp")


@app.get("/media/preview/{shot_id}")
async def preview_endpoint(shot_id: str, request: Request) -> FileResponse:
    """Serve the WebM preview clip for *shot_id*."""
    config: Config = request.app.state.config
    path = config.paths.assets_dir / "previews" / f"{shot_id}.webm"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Preview not found: {shot_id}.webm")
    return FileResponse(str(path), media_type="video/webm")


@app.get("/video/{film_id}")
async def video_endpoint(film_id: str, request: Request) -> StreamingResponse:
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
async def library_endpoint(request: Request) -> list[dict]:
    """Scan films_dir for video files and report index status for each."""
    config: Config = request.app.state.config
    db = request.app.state.db
    films_dir: Path = config.paths.films_dir

    if not films_dir.exists():
        return []

    # Collect all indexed paths from the films table (empty set if table absent).
    indexed_paths: set[str] = set()
    try:
        if "films" in db.table_names():
            films_tbl = db.open_table("films")
            rows = films_tbl.search().limit(100_000).to_list()
            indexed_paths = {row["path"] for row in rows}
    except Exception:
        pass

    result: list[dict] = []
    for f in films_dir.iterdir():
        if f.suffix.lower() not in _VIDEO_EXTENSIONS:
            continue
        size_gb = round(f.stat().st_size / (1024 ** 3), 1)
        status = "indexed" if str(f) in indexed_paths else "not_indexed"
        result.append({
            "filename": f.name,
            "path": str(f),
            "size_gb": size_gb,
            "status": status,
        })

    result.sort(key=lambda x: x["filename"].lower())
    return result


@app.post("/ingest")
async def ingest_endpoint(body: IngestRequest, request: Request) -> dict:
    """Start a background ingest pipeline job for one film file."""
    path = Path(body.path)

    if not path.exists() or path.suffix.lower() not in _VIDEO_EXTENSIONS:
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
    }

    config: Config = request.app.state.config
    loop = asyncio.get_running_loop()
    future = loop.run_in_executor(None, run_pipeline, path, config)

    def _on_done(fut: asyncio.Future) -> None:  # type: ignore[type-arg]
        try:
            fut.result()
            _ingest_jobs[job_id]["status"] = "done"
        except Exception as exc:
            _ingest_jobs[job_id]["status"] = "error"
            _ingest_jobs[job_id]["error"] = str(exc)
        _ingest_jobs[job_id]["finished_at"] = time.time()

    future.add_done_callback(_on_done)

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

    return list(_ingest_jobs.values())
