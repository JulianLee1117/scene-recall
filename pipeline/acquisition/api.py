"""Bounded HTTP inputs for the managed acquisition service."""

from __future__ import annotations

import os
from pathlib import PurePosixPath
from typing import Annotated, Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, File, Form, HTTPException, Path, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError, field_validator
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.formparsers import MultiPartException

from pipeline.acquisition.clients import ClientError
from pipeline.acquisition.store import AcquisitionConflict

MAX_TORRENT_BYTES = 2 * 1024 * 1024
MAX_MULTIPART_BYTES = MAX_TORRENT_BYTES + 64 * 1024
MAX_JSON_BYTES = 256 * 1024
ItemId = Annotated[str, Path(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")]
CleanText = Annotated[str, StringConstraints(strict=True, strip_whitespace=True)]
DEFAULT_ALLOWED_ORIGINS = "http://localhost:3000,http://127.0.0.1:3000"


def _require_trusted_origin(request: Request) -> None:
    """CORS cannot prevent a cross-site form from starting a download."""
    origin = request.headers.get("origin")
    if origin is None:
        # CLI callers have neither header. Browser image/navigation requests
        # can omit Origin, but must not initiate indexer searches cross-site.
        if request.headers.get("sec-fetch-site") not in {None, "none", "same-origin"}:
            raise HTTPException(403, "Open acquisition controls from the configured Scene Recall app")
        return
    allowed = {value.strip() for value in os.environ.get(
        "SCENE_RECALL_ALLOWED_ORIGINS", DEFAULT_ALLOWED_ORIGINS).split(",") if value.strip()}
    own = urlsplit(str(request.base_url))
    # Automatically trust the local app origin without treating an arbitrary
    # Host header as authorization. Remote proxies need their configured origin.
    if own.hostname in {"localhost", "127.0.0.1", "::1"}:
        allowed.add(f"{own.scheme}://{own.netloc}")
    if origin == "null" or origin not in allowed:
        raise HTTPException(403, "Open acquisition controls from the configured Scene Recall app")


def _validation_detail(errors):
    # Magnet tracker credentials and other submitted values must not be echoed.
    return [{key: error[key] for key in ("type", "loc", "msg")} for error in errors]


class AcquisitionRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def validated(request: Request):
            if request.method == "POST" or self.path.endswith("/search"):
                _require_trusted_origin(request)
            torrent_upload = self.path.endswith("/torrent") and request.method == "POST"
            if request.method == "POST":
                receive = request.receive
                consumed = 0
                maximum = MAX_MULTIPART_BYTES if torrent_upload else MAX_JSON_BYTES

                async def bounded_receive():
                    nonlocal consumed
                    message = await receive()
                    consumed += len(message.get("body", b""))
                    if consumed > maximum:
                        if not torrent_upload:
                            raise HTTPException(413, "Acquisition request is too large")
                        # The multipart parser closes partial spool files when
                        # it sees its own exception type.
                        raise MultiPartException("Torrent upload must be at most 2 MiB")
                    return message

                # Limit bytes before JSON decoding or multipart spooling.
                request = Request(request.scope, bounded_receive)
            if torrent_upload:
                try:
                    form = await request.form(max_files=1, max_fields=3, max_part_size=64 * 1024)
                except StarletteHTTPException:
                    if consumed > MAX_MULTIPART_BYTES:
                        raise HTTPException(413, "Torrent upload must be at most 2 MiB") from None
                    raise
                keys = [key for key, _ in form.multi_items()]
                if set(keys) - {"file", "title", "year", "edition"} or len(keys) != len(set(keys)):
                    await form.close()
                    raise HTTPException(422, "Supply only one file, title, year, and optional edition")
            try:
                return await handler(request)
            except RequestValidationError as exc:
                raise HTTPException(422, _validation_detail(exc.errors())) from None

        return validated


router = APIRouter(prefix="/acquisition", tags=["acquisition"], route_class=AcquisitionRoute)


class FilmIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: CleanText = Field(min_length=1, max_length=180)
    year: int = Field(strict=True, ge=1888, le=2100)
    edition: CleanText = Field(default="", max_length=80)


class MagnetRequest(FilmIdentity):
    magnet: CleanText = Field(min_length=10, max_length=16_384, pattern=r"^magnet:\?")


class ReleaseRequest(FilmIdentity):
    release_id: CleanText = Field(min_length=1, max_length=256, pattern=r"^[A-Za-z0-9_-]+$")


class RevisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    revision: int = Field(strict=True, ge=1)


def _relative_path(value: str) -> str:
    normalized = value.replace("\\", "/")
    if (normalized.startswith("/") or ":" in normalized
            or any(ord(char) < 32 for char in normalized)
            or any(part in {"", ".", ".."} for part in normalized.split("/"))):
        raise ValueError("Choose a relative path from this acquisition's file list")
    return normalized


class UseSubtitle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["use"]
    relative_path: CleanText = Field(min_length=1, max_length=1024)

    _valid_path = field_validator("relative_path")(_relative_path)


class SkipSubtitle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["skip"]


class AutoSubtitle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["auto"]


class ReviewRequest(RevisionRequest):
    video_path: CleanText = Field(min_length=1, max_length=1024)
    subtitle_decision: Annotated[UseSubtitle | SkipSubtitle | AutoSubtitle, Field(discriminator="action")] | None = None

    _valid_path = field_validator("video_path")(_relative_path)


def _service(request: Request):
    service = getattr(request.app.state, "acquisition", None)
    if service is None:
        raise HTTPException(503, "Acquisition service is unavailable")
    return service


def _call(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except AcquisitionConflict as exc:
        raise HTTPException(409, str(exc)) from None
    except ClientError:
        raise HTTPException(503, "Acquisition provider is unavailable; check its configuration and connection") from None
    except KeyError:
        raise HTTPException(404, "Acquisition or release not found") from None
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None


@router.get("/status")
def status(request: Request):
    return _call(_service(request).status)


@router.get("")
def list_acquisitions(request: Request):
    return _call(_service(request).list)


@router.get("/search")
def search(request: Request, q: str = Query(min_length=1, max_length=200)):
    query = q.strip()
    if not query:
        raise HTTPException(422, "Enter a movie title to search")
    return _call(_service(request).search, query)


@router.post("/magnet")
def add_magnet(body: MagnetRequest, request: Request):
    return {"item": _call(_service(request).add_magnet, **body.model_dump())}


@router.post("/torrent")
def add_torrent(
    request: Request,
    file: UploadFile = File(...),
    title: str = Form(..., min_length=1, max_length=180),
    year: int = Form(..., ge=1888, le=2100),
    edition: str = Form("", max_length=80),
):
    try:
        filename = file.filename or ""
        if (not filename or PurePosixPath(filename).suffix.lower() != ".torrent"
                or any(char in filename for char in "/\\:")
                or any(ord(char) < 32 for char in filename)):
            raise HTTPException(422, "Choose a .torrent file")
        try:
            identity = FilmIdentity(title=title, year=year, edition=edition)
        except ValidationError as exc:
            raise HTTPException(422, _validation_detail(exc.errors())) from None
        data = file.file.read(MAX_TORRENT_BYTES + 1)
        if len(data) > MAX_TORRENT_BYTES:
            raise HTTPException(413, "Torrent upload must be at most 2 MiB")
        if not data:
            raise HTTPException(422, "Torrent file is empty")
        return {"item": _call(_service(request).add_torrent, data, filename, **identity.model_dump())}
    finally:
        file.file.close()


@router.post("/release")
def add_release(body: ReleaseRequest, request: Request):
    return {"item": _call(_service(request).add_release, **body.model_dump())}


@router.post("/{identity}/cancel")
def cancel(identity: ItemId, body: RevisionRequest, request: Request):
    return {"item": _call(_service(request).cancel, identity, body.revision)}


@router.post("/{identity}/retry")
def retry(identity: ItemId, body: RevisionRequest, request: Request):
    return {"item": _call(_service(request).retry, identity, body.revision)}


@router.post("/{identity}/dismiss")
def dismiss(identity: ItemId, body: RevisionRequest, request: Request):
    result = _call(_service(request).dismiss, identity, body.revision) or {}
    return {"ok": True, **result}


@router.post("/{identity}/review")
def review(identity: ItemId, body: ReviewRequest, request: Request):
    return {"item": _call(_service(request).review, identity, body.revision,
                          body.video_path, body.subtitle_decision.model_dump() if body.subtitle_decision else None)}
