"""main.py — FastAPI application for scene-recall semantic search.

Endpoints
---------
GET /search?q=...                   Dense semantic search; {"results": [...]}
GET /unit/{unit_id}                 Full unit record from the LanceDB units table
GET /media/keyframe/{shot_id}/{n}   Serve a WebP keyframe image
GET /media/preview/{shot_id}        Serve a WebM preview clip
GET /video/{film_id}                Stream source video with HTTP range support

Start with::

    uv run uvicorn pipeline.api.main:app --reload
"""

from __future__ import annotations

import mimetypes
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Iterator

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from lancedb.expr import col, lit

from pipeline.config import Config, load_config
from pipeline.index.writer import open_db
from pipeline.search.retrieve import search as _search


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
