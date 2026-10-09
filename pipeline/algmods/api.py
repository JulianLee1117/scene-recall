"""Projectless Alg Mods sessions on the existing durable Lab worker."""
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import ValidationError

from pipeline.algmods import jobs
from pipeline.algmods.contracts import RenderRequest, catalog
from pipeline.algmods.mosaic import looks_like_text
from pipeline.transitions.storage import measure

router = APIRouter(prefix="/lab/alg-mods", tags=["alg-mods"])


def _call(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except KeyError:
        raise HTTPException(404, "Alg Mods render not found") from None
    except ValidationError as exc:
        raise HTTPException(422, [{key: error[key] for key in ("type", "loc", "msg")} for error in exc.errors()]) from None
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    except OSError:
        raise HTTPException(422, "The source media is unavailable; restore it before rendering") from None


def _job(request, identity):
    try:
        job = request.app.state.lab.get_job(identity, private=True)
    except KeyError:
        raise HTTPException(404, "Alg Mods render not found") from None
    if job["kind"] != jobs.KIND:
        raise HTTPException(404, "Alg Mods render not found")
    return job


def _public(request, job):
    result = request.app.state.lab.get_job(job["id"])
    proposal = job["snapshot"]["algmods_render"]
    result["request"] = proposal["request"]
    result["source_title"] = str(proposal["source"].get("title") or "")
    result["storage"] = measure(jobs.output_root(request.app.state.config, job["id"]), job["kind"])
    if result["cancel_requested"]:
        result["result"] = None
    return result


@router.get("/catalog")
def treatment_catalog():
    return catalog()


def _tile_units(request):
    """Shots the library search returns for each query, as (film_id, unit); the host film is excluded later."""
    def resolve(queries):
        from pipeline.api.main import _search
        found = {}
        for q in queries:
            for row in _search(q, request.app.state.db, request.app.state.config, result_limit=200):
                if looks_like_text(str(row.get("caption", ""))):
                    continue
                unit = str(row.get("unit_id", ""))
                film, _sep, number = unit.rpartition("_")
                if film and number.isdigit():
                    found[unit] = (film, int(number))
        return list(found.values())
    return resolve


@router.post("/renders")
def render(body: RenderRequest, request: Request):
    proposal = _call(jobs.freeze, body, request.app.state.db, resolve_tiles=_tile_units(request))
    job = _call(jobs.enqueue, proposal, request.app.state.config, request.app.state.lab)
    return {**_public(request, _job(request, job["id"])), "reused": job["status"] == "completed"}


@router.get("/renders")
def recent(request: Request, limit: int = Query(default=20, ge=1, le=100)):
    return {"renders": [_public(request, job) for job in request.app.state.lab.algmods_renders(limit)]}


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
    return _download(request, identity, "video", "video/mp4", f"alg-mods-{identity}.mp4" if download else None)


@router.get("/renders/{identity}/manifest")
def manifest(identity: str, request: Request):
    return _download(request, identity, "manifest", "application/json", f"alg-mods-{identity}.json")
