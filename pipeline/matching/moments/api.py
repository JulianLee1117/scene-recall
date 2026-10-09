"""Match Cuts workspace API: synchronous moment search, shot moments and still frames.

A search is arithmetic over the memory-mapped moment index (well under a
second), so it answers directly instead of queueing a job. Frames are decoded
on demand from the retained films and cached. Nothing here writes a project.
"""

from __future__ import annotations

import math
import threading
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from pipeline.matching.moments import find, frames, vision
from pipeline.matching.moments import index as moment_index
from pipeline.matching.moments import score as scoring

router = APIRouter(prefix="/matching/moments", tags=["matching"])
_SEARCH_LOCK = threading.Lock()


class SearchBody(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    unit_id: str = Field(min_length=1, max_length=240)
    time: float = Field(ge=0)
    direction: Literal["next", "previous"] = "next"
    focus: Literal["auto", "subject", "shape", "motion", "composition", "color"] = "auto"
    output: Literal["landscape", "vertical", "square"] = "landscape"
    reframe: bool = False
    zoom_max: float = Field(default=1.5, ge=1.0, le=2.0)
    outgoing_crop: tuple[float, float, float, float] | None = None
    film_ids: list[str] = Field(default_factory=list, max_length=400)
    include_same_film: bool = False
    exclude_unit_ids: list[str] = Field(default_factory=list, max_length=200)
    min_seconds: float = Field(default=1.0, ge=0.25, le=30)
    limit: int = Field(default=24, ge=1, le=60)

    @field_validator("outgoing_crop")
    @classmethod
    def inside(cls, crop):
        if crop is None:
            return crop
        x, y, width, height = crop
        if width <= 0 or height <= 0 or x < -1e-6 or y < -1e-6 or x + width > 1 + 1e-6 or y + height > 1 + 1e-6:
            raise ValueError("The outgoing crop must lie inside the picture")
        return crop


def _index(request: Request) -> moment_index.Index:
    loaded = moment_index.load(request.app.state.config)
    if loaded is None:
        raise HTTPException(503, "The match index is not built yet. Run `python -m pipeline.matching.moments index`.")
    return loaded


def _frame_url(film_id: str, time_value: float, width: int = 640) -> str:
    return f"/matching/moments/frame?film_id={film_id}&time={time_value:.3f}&w={width}"


@router.get("/status")
def status(request: Request):
    loaded = moment_index.load(request.app.state.config)
    if loaded is None:
        return {"ready": False}
    manifest = loaded.manifest
    return {"ready": True, "index_id": loaded.id, "created_at": manifest["created_at"], "moments": manifest["moments"],
            "films": [{"film_id": film["film_id"], "title": film["title"]} for film in manifest["films"]],
            "foci": list(scoring.FOCI), "outputs": list(scoring.OUTPUT_ASPECT)}


@router.get("/shot")
def shot(request: Request, unit_id: str = Query(min_length=1, max_length=240), time: float | None = Query(default=None, ge=0)):
    loaded = _index(request)
    try:
        unit = loaded.unit_index(unit_id)
    except KeyError as exc:
        raise HTTPException(404, "This shot is not in the match index yet") from exc
    rows = loaded.unit_rows(unit)
    film = int(loaded.unit_film[unit])
    times = loaded.columns["time"][rows]
    usable = loaded.columns["ok"][rows]
    start, end = float(loaded.unit_start[unit]), float(loaded.unit_end[unit])
    default = time if time is not None and start <= time <= end else float(times[usable][-1]) if usable.any() else start
    film_row = loaded.manifest["films"][film]
    return {"unit_id": unit_id, "film_id": loaded.film_ids[film], "film_title": loaded.film_titles[film],
            "scene_id": loaded.unit_scene[unit], "t_start": start, "t_end": end, "time": default,
            "aspect": float(loaded.film_aspect[film]), "content_box": film_row.get("content_box"),
            "moments": [{"time": round(float(t), 3), "ok": bool(ok)} for t, ok in zip(times, usable)]}


@router.post("/search")
def search(body: SearchBody, request: Request):
    loaded = _index(request)
    options = body.model_dump()
    try:
        with _SEARCH_LOCK:
            result = find.find(loaded, find.Request(**options))
    except KeyError as exc:
        raise HTTPException(404, str(exc).strip("'")) from None
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    reference = result["reference"]
    reference["frame_url"] = _frame_url(reference["film_id"], reference["time"])
    for row in result["results"]:
        row["frame_url"] = _frame_url(row["film_id"], row["time"])
    frames.prefetch(request.app.state.config, request.app.state.db,
                    [(reference["film_id"], reference["time"], reference["content_box"])]
                    + [(row["film_id"], row["time"], row["content_box"]) for row in result["results"][:16]])
    result["request"] = options
    return result


@router.get("/vision")
def vision_layers(request: Request, unit_id: str = Query(min_length=1, max_length=240), time: float = Query(ge=0)):
    """What the index saw at the analysed instant of a shot nearest *time*: layers for the lab's overlay."""
    if not math.isfinite(time):
        raise HTTPException(422, "Choose a finite time")
    try:
        return vision.describe(_index(request), unit_id, time)
    except KeyError as exc:
        raise HTTPException(404, "This shot is not in the match index yet") from exc


@router.get("/frame")
def frame(request: Request, film_id: str = Query(pattern=r"^[a-f0-9]{64}$"), time: float = Query(ge=0),
          w: int = Query(default=640, ge=64, le=1920)):
    if not math.isfinite(time):
        raise HTTPException(422, "Choose a finite time")
    loaded = _index(request)
    try:
        film = loaded.film_ids.index(film_id)
    except ValueError:
        raise HTTPException(404, "This film is not in the match index") from None
    content = loaded.manifest["films"][film].get("content_box")
    try:
        path = frames.frame(request.app.state.config, request.app.state.db, film_id, time, w, content)
    except (KeyError, ValueError, OSError) as exc:
        raise HTTPException(404, f"No frame at this time ({exc})") from None
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=604800, immutable"})
