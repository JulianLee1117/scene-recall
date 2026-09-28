"""Projectless transition sessions using the existing durable Lab worker."""
import asyncio
import time

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import ValidationError
from starlette.formparsers import MultiPartException

from pipeline.transitions.contracts import RenderRequest, catalog
from pipeline.transitions import bridges, generation, jobs, providers
from pipeline.transitions.storage import measure

MAX_UPLOAD_BODY_BYTES = 129 * 1024 * 1024
MAX_UPLOAD_RECEIVE_SECONDS = 120


class TransitionRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def validated(request):
            upload_error = None
            # Bound the actual multipart stream, including chunked requests,
            # before FastAPI's form parser can spool an unbounded upload.
            if request.method == "POST" and request.url.path.rstrip("/").endswith("/bridges"):
                length = request.headers.get("content-length")
                if length is not None:
                    try:
                        too_large = int(length) > MAX_UPLOAD_BODY_BYTES
                    except ValueError:
                        raise HTTPException(400, "Invalid upload length") from None
                    if too_large:
                        raise HTTPException(413, "Bridge uploads must be at most 128 MiB")
                original_receive = request.receive
                received = 0
                deadline = time.monotonic() + MAX_UPLOAD_RECEIVE_SECONDS

                def reject_upload(status, detail):
                    nonlocal upload_error
                    upload_error = HTTPException(status, detail)
                    # The multipart parser closes its partial spools on this
                    # exception; a direct HTTPException bypasses that cleanup.
                    raise MultiPartException(detail)

                async def bounded_receive():
                    nonlocal received
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        reject_upload(408, "Bridge upload exceeded its two-minute reception budget")
                    try:
                        message = await asyncio.wait_for(original_receive(), timeout=remaining)
                    except TimeoutError:
                        reject_upload(408, "Bridge upload exceeded its two-minute reception budget")
                    received += len(message.get("body", b""))
                    if received > MAX_UPLOAD_BODY_BYTES:
                        reject_upload(413, "Bridge uploads must be at most 128 MiB")
                    return message

                request = Request(request.scope, receive=bounded_receive)
            try:
                return await handler(request)
            except RequestValidationError as exc:
                # Avoid echoing local paths or serializing nonfinite user input.
                raise HTTPException(422, [{key: error[key] for key in ("type", "loc", "msg")}
                                          for error in exc.errors()]) from None
            except Exception:
                if upload_error is not None:
                    raise upload_error from None
                raise
        return validated


router = APIRouter(prefix="/lab/transitions", tags=["transitions"], route_class=TransitionRoute)


def _call(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except KeyError:
        raise HTTPException(404, "Transition render not found") from None
    except ValidationError as exc:
        raise HTTPException(422, [{key: error[key] for key in ("type", "loc", "msg")}
                                  for error in exc.errors()]) from None
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    except OSError:
        raise HTTPException(422, "The source media is unavailable; restore it before rendering") from None


def _job(request, identity):
    try:
        job = request.app.state.lab.get_job(identity, private=True)
    except KeyError:
        raise HTTPException(404, "Transition render not found") from None
    if job["kind"] != "transition-render":
        raise HTTPException(404, "Transition render not found")
    return job


def _public(request, job):
    result = request.app.state.lab.get_job(job["id"])
    proposal = job["snapshot"]["transition_render"]
    result["request"] = proposal["request"]
    result["source_titles"] = {side: str(source.get("title") or "")
                               for side, source in zip(("outgoing", "incoming"), proposal["sources"])}
    result["storage"] = measure(jobs.output_root(request.app.state.config, job["id"]), job["kind"])
    if result["cancel_requested"]:
        result["result"] = None
    return result


@router.get("/recipes")
def recipes():
    return catalog()


@router.get("/providers")
def provider_readiness():
    return providers.provider_status()


@router.post("/bridges/quote")
def bridge_quote(body: providers.QuoteRequest, request: Request):
    return _call(providers.quote, body, request.app.state.config, request.app.state.db, request.app.state.lab)


@router.post("/renders")
def render(body: RenderRequest, request: Request):
    proposal = _call(jobs.freeze, body, request.app.state.db)
    job = _call(jobs.enqueue, proposal, request.app.state.config, request.app.state.lab)
    return {**_public(request, _job(request, job["id"])), "reused": job["status"] == "completed"}


def _generation_job(request, identity):
    job = _call(request.app.state.lab.get_job, identity, private=True)
    if job["kind"] != "transition-generate":
        raise HTTPException(404, "AI generation not found")
    return job


def _public_generation(request, job):
    result = request.app.state.lab.get_job(job["id"])
    proposal = job["snapshot"]["transition_generation"]
    result["request"] = proposal["request"]
    result["source_titles"] = {side: str(source.get("title") or "") for side, source in
                               zip(("outgoing", "incoming"), proposal["source_snapshot"]["sources"])}
    result["receipt_url"] = f"/lab/transitions/generations/{job['id']}/receipt"
    result["original_url"] = f"/lab/transitions/generations/{job['id']}/original"
    result["storage"] = measure(jobs.output_root(request.app.state.config, job["id"]), job["kind"])
    if result["cancel_requested"]:
        result["result"] = None
    return result


@router.post("/generations")
def generate_bridge(body: generation.GenerateRequest, request: Request):
    # HTTP retries return this exact job even if it has finished or the key
    # is subsequently removed. A repeated request never starts another charge.
    try:
        previous = request.app.state.lab.get_job(body.request_id, private=True)
    except KeyError:
        previous = None
    if previous is not None:
        if (previous["kind"] != "transition-generate" or
                providers.request_identity(previous["snapshot"]["transition_generation"]["request"]) !=
                providers.request_identity(body.model_dump(mode="json"))):
            raise HTTPException(409, "This generation request identity is already used for different settings")
        return _public_generation(request, previous)
    proposal = _call(generation.prepare, body, request.app.state.config, request.app.state.db, request.app.state.lab)
    job = _call(request.app.state.lab.enqueue_transition_generation, proposal)
    return _public_generation(request, _generation_job(request, job["id"]))


@router.get("/generations")
def recent_generations(request: Request, parent_render_id: str | None = None,
                       limit: int = Query(default=20, ge=1, le=100)):
    items = _call(request.app.state.lab.transition_generations, parent_render_id, limit)
    return {"generations": [_public_generation(request, job) for job in items]}


@router.get("/generations/{identity}")
def get_generation(identity: str, request: Request):
    return _public_generation(request, _generation_job(request, identity))


@router.post("/generations/{identity}/cancel")
def cancel_generation(identity: str, request: Request):
    previous = _generation_job(request, identity)
    _call(request.app.state.lab.cancel, identity)
    if previous["status"] not in {"queued", "running"}:
        _call(generation.cancel_remote, request.app.state.config, previous)
    return _public_generation(request, _generation_job(request, identity))


@router.get("/generations/{identity}/{kind}")
def generation_artifact(identity: str, kind: str, request: Request, download: bool = False):
    if kind not in {"video", "manifest", "original", "a", "b", "first", "last", "provider-a", "provider-b", "receipt"}:
        raise HTTPException(404, "Generation artifact not found")
    job = _generation_job(request, identity)
    if kind == "receipt":
        receipt = _call(generation.receipt, request.app.state.config, job)
        return JSONResponse(receipt, headers={"Content-Disposition": f'attachment; filename="generation-{identity}-receipt.json"'})
    try:
        path = generation.artifact(request.app.state.config, job, kind)
    except (ValueError, OSError, KeyError):
        raise HTTPException(409, "This generation artifact is not available; inspect its status before retrying") from None
    if kind in {"video", "original"}:
        media_type = "video/mp4"
        filename = f"generated-bridge-{identity}{'-original' if kind == 'original' else ''}.mp4" if download or kind == "original" else None
    elif kind in {"manifest", "receipt"}:
        media_type, filename = "application/json", f"generation-{identity}-{kind}.json"
    else:
        media_type, filename = "image/jpeg", f"generation-{identity}-{kind}.jpg"
    return FileResponse(path, media_type=media_type, filename=filename)


@router.get("/renders")
def recent(request: Request, limit: int = Query(default=20, ge=1, le=100)):
    return {"renders": [_public(request, job) for job in request.app.state.lab.transition_renders(limit)]}


@router.get("/renders/{identity}")
def get_render(identity: str, request: Request):
    return _public(request, _job(request, identity))


@router.post("/renders/{identity}/cancel")
def cancel_render(identity: str, request: Request):
    _job(request, identity)
    _call(request.app.state.lab.cancel, identity)
    return _public(request, _job(request, identity))


def _download(request, identity, kind, media_type, filename=None):
    job = _job(request, identity)
    try:
        path = jobs.artifact(request.app.state.config, job, kind)
    except (ValueError, OSError, KeyError):
        raise HTTPException(409, "This artifact is unavailable; render a fresh variant") from None
    return FileResponse(path, media_type=media_type, filename=filename)


@router.get("/renders/{identity}/video")
def video(identity: str, request: Request, download: bool = False):
    return _download(request, identity, "video", "video/mp4", f"transition-{identity}.mp4" if download else None)


@router.get("/renders/{identity}/manifest")
def manifest(identity: str, request: Request):
    return _download(request, identity, "manifest", "application/json", f"transition-{identity}.json")


@router.get("/renders/{identity}/frames/{side}")
def frame(identity: str, side: str, request: Request):
    if side not in {"a", "b"}:
        raise HTTPException(404, "Transition frame not found")
    return _download(request, identity, side, "image/jpeg", f"transition-{identity}-{side}.jpg")


def _bridge_job(request, identity):
    job = _call(request.app.state.lab.get_job, identity, private=True)
    if job["kind"] != "transition-bridge":
        raise HTTPException(404, "Bridge audition not found")
    return job


def _public_bridge(request, job):
    result = request.app.state.lab.get_job(job["id"])
    proposal = job["snapshot"]["transition_bridge"]
    result["request"] = proposal["request"]
    result["input"] = {key: proposal["input"][key] for key in
                       ("name", "size", "media", "provenance_status")}
    result["storage"] = measure(jobs.output_root(request.app.state.config, job["id"]), job["kind"])
    result["source_titles"] = {side: str(source.get("title") or "") for side, source in
                               zip(("outgoing", "incoming"), proposal["source_snapshot"]["sources"])}
    if result["cancel_requested"]:
        result["result"] = None
    return result


@router.post("/bridges")
def import_bridge(request: Request, file: UploadFile = File(...), metadata: str = Form(..., max_length=32768)):
    body = _call(bridges.BridgeImportRequest.model_validate_json, metadata)
    proposal = _call(bridges.prepare_import, body, file.file, file.filename,
                     request.app.state.config, request.app.state.db, request.app.state.lab)
    job = _call(request.app.state.lab.enqueue_transition_bridge, proposal)
    return _public_bridge(request, _bridge_job(request, job["id"]))


@router.get("/bridges")
def recent_bridges(request: Request, parent_render_id: str | None = None,
                   limit: int = Query(default=20, ge=1, le=100)):
    items = _call(request.app.state.lab.transition_bridges, parent_render_id, limit)
    return {"bridges": [_public_bridge(request, job) for job in items]}


@router.get("/bridges/{identity}")
def get_bridge(identity: str, request: Request):
    return _public_bridge(request, _bridge_job(request, identity))


@router.post("/bridges/{identity}/cancel")
def cancel_bridge(identity: str, request: Request):
    _bridge_job(request, identity)
    _call(request.app.state.lab.cancel, identity)
    return _public_bridge(request, _bridge_job(request, identity))


@router.get("/bridges/{identity}/{kind}")
def bridge_artifact(identity: str, kind: str, request: Request, download: bool = False):
    if kind not in {"video", "manifest", "original", "a", "b", "first", "last"}:
        raise HTTPException(404, "Bridge artifact not found")
    job = _bridge_job(request, identity)
    try:
        path = bridges.artifact(request.app.state.config, job, kind)
    except (ValueError, OSError, KeyError):
        raise HTTPException(409, "This bridge artifact is unavailable; import a fresh variant") from None
    if kind == "video":
        media_type, filename = "video/mp4", f"bridge-{identity}.mp4" if download else None
    elif kind == "manifest":
        media_type, filename = "application/json", f"bridge-{identity}.json"
    elif kind == "original":
        media_type, filename = "application/octet-stream", job["snapshot"]["transition_bridge"]["input"]["name"]
    else:
        media_type, filename = "image/jpeg", f"bridge-{identity}-{kind}.jpg"
    return FileResponse(path, media_type=media_type, filename=filename)
