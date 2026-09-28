"""Explicit, projectless scene discovery; edit placement is a separate adapter."""
from __future__ import annotations

import math
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse
from fastapi.routing import APIRoute

from pipeline.lab.models import ClipSelection
from pipeline.matching import cohort, media
from pipeline.matching.contracts import SearchRequest, same_pair as _same_pair
from pipeline.matching.jobs import preview_available, preview_path

class MatchRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def validated(request):
            try:
                return await handler(request)
            except RequestValidationError as exc:
                # Do not echo source input or serialize raw NaN/Infinity values.
                raise HTTPException(422, [{key: error[key] for key in ("type", "loc", "msg")}
                                          for error in exc.errors()]) from None
        return validated


router = APIRouter(prefix="/matching", tags=["matching"], route_class=MatchRoute)


def _call(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except KeyError as exc:
        raise HTTPException(404, str(exc).strip("'")) from None
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(422, str(exc)) from None


def _job(request, identity):
    job = _call(request.app.state.lab.get_job, identity, private=True)
    if job["kind"] not in {"match-search", "match-search-preview"}:
        raise HTTPException(404, "Match search not found")
    return job


def _public(request, job):
    result = request.app.state.lab.get_job(job["id"])
    proposal = job["snapshot"].get("match_search")
    if proposal:
        result["request"] = {key: value for key, value in proposal["request"].items() if key != "profile_id"}
        result["reference"] = proposal["reference"]
    else:
        parent = _job(request, job["snapshot"]["match_search_preview"]["search_id"])
        if parent["cancel_requested"] or parent["status"] != "completed":
            result.update(status="cancelled", cancel_requested=True, result=None)
    if job["cancel_requested"]:
        result.update(result=None, cancel_requested=True)
        if job["status"] == "completed":
            result["status"] = "cancelled"
    return result


def _reference(request, unit_id, time):
    source = _call(cohort.unit, request.app.state.db, unit_id)
    if not math.isfinite(time) or not source["t_start"] <= time < source["t_end"]:
        raise HTTPException(422, "Choose a moment inside this shot")
    film = _call(cohort.resolve_film, request.app.state.db, source["film_id"])
    start = max(source["t_start"], time - 2)
    end = min(source["t_end"], max(time + .15, start + 3))
    clip = ClipSelection(id="match-reference", unit_id=unit_id, film_id=source["film_id"],
                         title=film.get("title", ""), source_start=start, source_end=end,
                         reference_time=time).model_dump(mode="json")
    return {"reference": clip, "bounds": {"t_start": source["t_start"], "t_end": source["t_end"]}}


@router.get("/cohorts")
def cohorts(request: Request):
    rows = cohort.listed(request.app.state.config)
    from pipeline.matching.search import library_profile
    library = _call(library_profile, request.app.state.config, request.app.state.db)
    if library:
        snapshot = library["library"]
        coverage = {"ready": True, **{name: snapshot[name] for name in ("film_count", "frame_count", "unit_count", "films")}}
        rows = [{**row, "library": coverage} for row in rows]
    return {"cohorts": rows}


@router.get("/reference")
def reference(request: Request, unit_id: str, time: float = Query(ge=0)):
    return _reference(request, unit_id, time)


@router.get("/frames")
def frames(request: Request, unit_id: str, time: float = Query(ge=0)):
    resolved = _reference(request, unit_id, time)
    bounds = resolved["bounds"]
    film = _call(cohort.resolve_film, request.app.state.db, resolved["reference"]["film_id"])
    rows = _call(media.samples, Path(film["path"]), max(bounds["t_start"], time - .6),
                 min(bounds["t_end"], time + .6), native=True)
    return {**bounds, "frames": [{"time": row.time, "end": row.end} for row in rows]}


@router.post("/searches")
def search(body: SearchRequest, request: Request):
    from pipeline.matching.search import validate_request
    options = body.model_dump(mode="json")
    resolved = _reference(request, body.reference.unit_id, body.reference.time)
    profiles = _call(validate_request, request.app.state.config, request.app.state.db, options)
    job = _call(request.app.state.lab.enqueue_match_search, options, profiles, resolved["reference"])
    return _public(request, _job(request, job["id"]))


@router.get("/searches/{search_id}")
def get_search(search_id: str, request: Request):
    return _public(request, _job(request, search_id))


@router.post("/searches/{search_id}/cancel")
def cancel_search(search_id: str, request: Request):
    _job(request, search_id)
    _call(request.app.state.lab.cancel, search_id)
    return _public(request, _job(request, search_id))


def _candidate(request, identity, candidate_id):
    job = _job(request, identity)
    if job["cancel_requested"] or job["status"] not in {"running", "completed"}:
        raise HTTPException(409, "This match search is no longer available")
    if job["kind"] == "match-search":
        candidate = next((row for row in (job["result"] or {}).get("candidates", [])
                          if row["id"] == candidate_id), None)
        if candidate is None:
            raise HTTPException(404, "Match suggestion not found")
        return job, job, candidate
    proposal = job["snapshot"]["match_search_preview"]
    parent = _job(request, proposal["search_id"])
    if parent["kind"] != "match-search" or parent["status"] != "completed" or parent["cancel_requested"]:
        raise HTTPException(409, "The parent match search is no longer available")
    saved = next((row for row in (parent["result"] or {}).get("candidates", []) if row["id"] == candidate_id), None)
    candidate = (job["result"] or {}).get("candidate")
    if not saved or not candidate or not _same_pair(candidate, saved) or not _same_pair(candidate, proposal["candidate"]):
        raise HTTPException(409, "This preview does not belong to the saved match suggestion")
    return parent, job, candidate


def _available(request, parent, job, candidate):
    if job["cancel_requested"] or job["status"] not in {"running", "completed"}:
        return False
    if job["kind"] == "match-search-preview":
        proposal = job["snapshot"]["match_search_preview"]
        if job["status"] != "completed" or proposal["search_id"] != parent["id"] or not _same_pair(proposal["candidate"], candidate):
            return False
    return preview_available(request.app.state.config, request.app.state.db, request.app.state.lab, job["id"], candidate)


@router.get("/searches/{search_id}/candidates/{candidate_id}/frame")
def candidate_frame(search_id: str, candidate_id: str, request: Request):
    parent, _job_row, candidate = _candidate(request, search_id, candidate_id)
    path = request.app.state.config.paths.assets_dir / "matching" / "results" / parent["id"] / f"{candidate['id']}.jpg"
    if not path.is_file():
        raise HTTPException(404, "The match frame is not available yet")
    return FileResponse(path, media_type="image/jpeg")


@router.get("/searches/{search_id}/candidates/{candidate_id}/preview")
def candidate_preview(search_id: str, candidate_id: str, request: Request):
    parent, selected, candidate = _candidate(request, search_id, candidate_id)
    choices = [(selected, candidate)]
    if selected["kind"] == "match-search":
        choices.extend((job, (job["result"] or {}).get("candidate")) for job in
                       request.app.state.lab.match_search_previews(parent["id"], candidate["id"]))
    for job, preview in choices:
        if preview and _same_pair(preview, candidate) and _available(request, parent, job, preview):
            return FileResponse(preview_path(request.app.state.config, job["id"], candidate["id"]), media_type="video/mp4")
    raise HTTPException(409, "Prepare a fresh preview of this cut before playing it")


@router.post("/searches/{search_id}/candidates/{candidate_id}/preview")
def prepare_preview(search_id: str, candidate_id: str, request: Request):
    parent, selected, candidate = _candidate(request, search_id, candidate_id)
    if selected["kind"] != "match-search" or parent["status"] != "completed":
        raise HTTPException(409, "Wait for this search to finish before preparing another preview")
    store = request.app.state.lab
    for job in store.match_search_previews(parent["id"], candidate["id"]):
        preview = (job["result"] or {}).get("candidate")
        if preview and _same_pair(preview, candidate) and _available(request, parent, job, preview):
            return _public(request, job)
    job = _call(store.enqueue_match_search_preview, parent["id"], candidate["id"])
    return _public(request, _job(request, job["id"]))
