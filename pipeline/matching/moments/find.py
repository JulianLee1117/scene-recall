"""Find incoming instants that match-cut from a reference instant (or outgoing ones that cut into it).

1. The reference is the indexed grid instant nearest the requested time.
2. Coarse retrieval: one matrix-vector product over every coarse moment,
   with parts weighted by focus (reframing and vertical output lower the
   weight of fixed position, which a crop can fix).
3. Exact scoring (``score``) of every usable instant near the best coarse
   hits of the best shots, so the cut point is chosen inside the shot, not
   taken from a thumbnail.
4. One result per shot, at most one per scene and a few per film, strongest
   first.

Nothing here decodes video; a search costs milliseconds of arithmetic plus
reading the candidates' rows from the memory-mapped index.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import time
from typing import Any

import numpy as np

from pipeline.matching.moments import index as moment_index
from pipeline.matching.moments import score as scoring

# Coarse part weights per focus (the dot product is a weighted sum of part cosines).
PART_WEIGHTS = {
    "auto_subject": {"layout": 1.0, "light": 0.6, "lines": 0.3, "shape": 0.6, "motion": 0.5, "color": 0.2},
    "auto_picture": {"layout": 0.3, "light": 1.0, "lines": 0.6, "shape": 0.1, "motion": 0.6, "color": 0.4},
    "subject": {"layout": 1.2, "light": 0.3, "lines": 0.1, "shape": 0.5, "motion": 0.2, "color": 0.1},
    "shape": {"layout": 0.5, "light": 0.3, "lines": 0.3, "shape": 1.2, "motion": 0.1, "color": 0.1},
    "motion": {"layout": 0.3, "light": 0.3, "lines": 0.1, "shape": 0.1, "motion": 1.5, "color": 0.1},
    "composition": {"layout": 0.4, "light": 1.0, "lines": 0.8, "shape": 0.1, "motion": 0.2, "color": 0.3},
    "color": {"layout": 0.2, "light": 0.6, "lines": 0.1, "shape": 0.0, "motion": 0.1, "color": 1.2},
}
COARSE_HITS = 6000
SHOTS = 360
HITS_PER_SHOT = 3
NEAR_HIT_S = 0.8
PER_FILM = 3


@dataclass
class Request:
    unit_id: str
    time: float
    direction: str = "next"                   # next: find what follows; previous: what leads in
    focus: str = "auto"
    output: str = scoring.LANDSCAPE
    reframe: bool = False
    zoom_max: float = 1.5
    outgoing_crop: tuple[float, float, float, float] | None = None
    film_ids: list[str] = field(default_factory=list)
    include_same_film: bool = False
    exclude_unit_ids: list[str] = field(default_factory=list)
    min_seconds: float = 1.0                  # footage the candidate needs after (next) or before (previous) the cut
    limit: int = 24


def reference_row(index: moment_index.Index, unit_id: str, time_value: float) -> int:
    """The usable grid instant of the shot nearest the requested time."""
    unit = index.unit_index(unit_id)
    rows = index.unit_rows(unit)
    if not len(rows):
        raise ValueError("This shot has no described moments")
    usable = rows[index.columns["ok"][rows]]
    pool = usable if len(usable) else rows
    return int(pool[np.argmin(np.abs(index.columns["time"][pool] - time_value))])


def _query(index: moment_index.Index, row: int, request: Request, has_subject: bool) -> np.ndarray:
    weights = dict(PART_WEIGHTS["auto_subject" if has_subject else "auto_picture"] if request.focus == "auto"
                   else PART_WEIGHTS[request.focus])
    if request.reframe:
        weights["layout"] *= 0.4
        weights["shape"] = weights.get("shape", 0.0) * 1.5 + 0.2
    if request.output != scoring.LANDSCAPE:
        weights["layout"] *= 0.6
        weights["light"] *= 0.7
    vector = index.query_vector(row).copy()
    for name, part in moment_index.part_slices().items():
        vector[part] *= weights.get(name, 0.0)
    return vector


def _similarity(coarse: np.ndarray, query: np.ndarray) -> np.ndarray:
    """Coarse dot products in float32, converting the float16 matrix a block at a time."""
    out = np.empty(len(coarse), dtype=np.float32)
    step = 1 << 18
    for start in range(0, len(coarse), step):
        out[start:start + step] = coarse[start:start + step].astype(np.float32) @ query
    return out


def _allowed_units(index: moment_index.Index, reference_unit: int, request: Request) -> np.ndarray:
    allowed = np.ones(len(index.unit_ids), dtype=bool)
    reference_film = int(index.unit_film[reference_unit])
    if request.film_ids:
        wanted = {position for position, film_id in enumerate(index.film_ids) if film_id in set(request.film_ids)}
        allowed &= np.isin(index.unit_film, list(wanted))
    if not request.include_same_film:
        allowed &= index.unit_film != reference_film
    else:
        scene = index.unit_scene[reference_unit]
        if scene:
            allowed &= np.array([other != scene for other in index.unit_scene])
    allowed[reference_unit] = False
    for unit_id in request.exclude_unit_ids:
        try:
            allowed[index.unit_index(unit_id)] = False
        except KeyError:
            continue
    return allowed


def _candidate_rows(index: moment_index.Index, units: np.ndarray, hits: dict[int, list[float]], request: Request) -> np.ndarray:
    """Usable instants near each shot's coarse hits with enough footage on the needed side of the cut."""
    columns = index.columns
    rows_out = []
    for unit in units:
        rows = index.unit_rows(int(unit))
        times = columns["time"][rows]
        keep = columns["ok"][rows].copy()
        if request.direction == "next":
            keep &= index.unit_end[unit] - times >= request.min_seconds - 1e-6
        else:
            keep &= times - index.unit_start[unit] >= request.min_seconds - 1e-6
        near = np.zeros(len(rows), dtype=bool)
        for hit in hits.get(int(unit), []):
            near |= np.abs(times - hit) <= NEAR_HIT_S
        rows_out.append(rows[keep & near])
    return np.concatenate(rows_out) if rows_out else np.zeros(0, dtype=np.int64)


def find(index: moment_index.Index, request: Request) -> dict[str, Any]:
    started = time.perf_counter()
    if request.focus not in scoring.FOCI:
        raise ValueError(f"Choose a focus among {', '.join(scoring.FOCI)}")
    row = reference_row(index, request.unit_id, request.time)
    reference_unit = int(index.columns["unit"][row])
    reference = index.moments(np.array([row]))
    group, main = scoring.salient(reference)
    has_subject = bool(main[0] >= 0 and scoring.box_area(reference.boxes[main[0]]) >= 0.01)

    # Coarse retrieval.
    query = _query(index, row, request, has_subject)
    allowed_units = _allowed_units(index, reference_unit, request)
    coarse_units = index.columns["unit"][index.coarse_rows]
    similarity = _similarity(index.coarse, query)
    similarity[~allowed_units[coarse_units]] = -np.inf
    top = np.argpartition(-similarity, min(COARSE_HITS, len(similarity) - 1))[:COARSE_HITS]
    top = top[np.isfinite(similarity[top])]
    top = top[np.argsort(-similarity[top])]
    hits: dict[int, list[float]] = {}
    order: list[int] = []
    for position in top:
        unit = int(coarse_units[position])
        if unit not in hits:
            if len(order) >= SHOTS:
                continue
            hits[unit] = []
            order.append(unit)
        if len(hits[unit]) < HITS_PER_SHOT:
            hits[unit].append(float(index.columns["time"][index.coarse_rows[position]]))
    coarse_s = time.perf_counter() - started

    # Exact scoring of instants near the hits.
    rows = _candidate_rows(index, np.array(order, dtype=np.int64), hits, request)
    options = scoring.Options(focus=request.focus, output=request.output, reframe=request.reframe,
                              zoom_max=request.zoom_max, outgoing_crop=request.outgoing_crop,
                              calibration=index.calibration)
    results: list[dict[str, Any]] = []
    scored = None
    if len(rows):
        candidates = index.moments(rows)
        scored = scoring.score(reference, candidates, options)
        sharp = index.columns["sharpness"][rows]
        film_of = index.columns["film"][rows]
        # A soft frame is a weaker cut point: compare with the film's own middling sharpness.
        blur = np.zeros(len(rows))
        for film in np.unique(film_of):
            members = film_of == film
            median = float(np.median(sharp[members])) if members.any() else 0.0
            blur[members] = np.clip(0.5 - sharp[members] / max(median, 1e-6), 0.0, 0.5) * 0.08
        total = scored.total - blur
        unit_of = index.columns["unit"][rows]
        best: dict[int, int] = {}
        for position in np.argsort(-total):
            unit = int(unit_of[position])
            if unit not in best:
                best[unit] = int(position)
        ranked = sorted(best.values(), key=lambda position: -total[position])
        per_film: dict[int, int] = {}
        scenes: set[str] = set()
        deferred = []
        for position in ranked:
            unit = int(unit_of[position])
            film = int(index.unit_film[unit])
            scene = index.unit_scene[unit]
            if scene and scene in scenes:
                continue
            if per_film.get(film, 0) >= PER_FILM:
                deferred.append(position)
                continue
            per_film[film] = per_film.get(film, 0) + 1
            if scene:
                scenes.add(scene)
            results.append(_result(index, reference, candidates, rows, scored, total, position))
            if len(results) >= request.limit:
                break
        for position in deferred:
            if len(results) >= request.limit:
                break
            results.append(_result(index, reference, candidates, rows, scored, total, position))
    reference_info = {
        "unit_id": index.unit_ids[reference_unit], "film_id": index.film_ids[int(index.unit_film[reference_unit])],
        "film_title": index.film_titles[int(index.unit_film[reference_unit])], "time": float(index.columns["time"][row]),
        "t_start": float(index.unit_start[reference_unit]), "t_end": float(index.unit_end[reference_unit]),
        "crop": list(scored.outgoing_crop) if scored is not None else None,
        "subject": _subject_summary(reference), "aspect": float(index.film_aspect[int(index.unit_film[reference_unit])]),
        "content_box": index.manifest["films"][int(index.unit_film[reference_unit])].get("content_box"),
    }
    return {"reference": reference_info, "results": results, "index_id": index.id,
            "weights": scored.weights if scored is not None else {},
            "searched": {"moments": int(index.manifest["moments"]), "films": len(index.film_ids),
                         "shots": len(order), "instants": int(len(rows))},
            "elapsed_ms": {"coarse": round(coarse_s * 1000, 1), "total": round((time.perf_counter() - started) * 1000, 1)}}


def _subject_summary(reference: scoring.Moments) -> dict[str, Any] | None:
    group, main = scoring.salient(reference)
    if main[0] < 0:
        return None
    return {"box": [round(float(v), 4) for v in reference.boxes[main[0]]], "count": int((group[0] >= 0).sum()),
            "person": bool(reference.classes[main[0]] == scoring.PERSON)}


def _result(index: moment_index.Index, reference: scoring.Moments, candidates: scoring.Moments, rows: np.ndarray,
            scored: scoring.Scored, total: np.ndarray, position: int) -> dict[str, Any]:
    row = int(rows[position])
    unit = int(index.columns["unit"][row])
    film = int(index.unit_film[unit])
    parts = {name: float(values[position]) for name, values in scored.parts.items()}
    calibrated = {name: float(values[position]) for name, values in scored.calibrated.items()}
    candidate = candidates.take(np.array([position]))
    zoom = float(scored.zoom[position])
    return {
        "unit_id": index.unit_ids[unit], "film_id": index.film_ids[film], "film_title": index.film_titles[film],
        "scene_id": index.unit_scene[unit], "time": float(index.columns["time"][row]),
        "t_start": float(index.unit_start[unit]), "t_end": float(index.unit_end[unit]),
        "score": round(float(total[position]), 4),
        "parts": {name: round(value, 3) for name, value in calibrated.items()},
        "raw": {name: round(value, 3) for name, value in parts.items()},
        "reasons": [{**reason, "strength": round(float(reason["strength"]), 3)}
                    for reason in scoring.reasons(calibrated, parts, reference, candidate, zoom)],
        "crop": [round(float(v), 4) for v in scored.crop[position]], "zoom": round(zoom, 3),
        "aspect": float(index.film_aspect[film]), "content_box": index.manifest["films"][film].get("content_box"),
    }
