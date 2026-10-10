"""What the match index saw at one instant, as layers an overlay can draw.

The Match Cuts lab draws these over a frame, so you can see what a cut was
matched on. Each layer names its kind (``regions``, ``skeletons``, ``points``,
``field``, ``grid``, ``vectors``) and carries content-frame fractions
(letterbox removed), so the overlay draws every kind without knowing the
model behind it. A new moments producer changes the data, not the overlay;
the film's moments profile, returned with the layers, says which version
produced them.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from pipeline.evidence import moments as producer, subjects
from pipeline.matching.moments import score as scoring

LINE_MIN = 0.12          # weaker edge cells are texture, not a line
TRAVEL_SECONDS = 2.0     # motion arrows show this much travel


def _point(x: float, y: float) -> list[float]:
    return [round(float(x), 4), round(float(y), 4)]


def layers(moment: scoring.Moments) -> list[dict[str, Any]]:
    """The layers of one moment (``moment`` holds exactly one instant)."""
    names = producer.class_names()
    _, main = scoring.salient(moment)
    eye, has_eye = scoring.eye_points(moment, main)

    regions = [{"label": names.get(int(code), f"class {int(code)}"), "score": round(float(score), 3),
                "box": [round(float(value), 4) for value in box],
                "mask": ["".join("1" if cell else "0" for cell in row) for row in mask]}
               for box, mask, code, score in zip(moment.boxes, moment.masks, moment.classes, moment.scores)]
    skeletons = [{"points": [_point(x, y) + [round(float(confidence), 3)]
                             for (x, y), confidence in zip(moment.pose_xy[person], moment.pose_conf[person])]}
                 for person in range(int(moment.pose_ptr[-1]) if moment.pose_ptr is not None else 0)]

    field = moment.field[0]      # 2 x rows x columns: doubled-angle (cos, sin), weighted by coherent edge energy
    rows, columns = field.shape[1:]
    cells = []
    for row in range(rows):
        for column in range(columns):
            strength = math.hypot(float(field[0, row, column]), float(field[1, row, column]))
            if strength >= LINE_MIN:
                # The field holds the gradient's doubled angle; the edge runs across it.
                angle = math.atan2(float(field[1, row, column]), float(field[0, row, column])) / 2 + math.pi / 2
                cells.append([column, row, round(angle, 3), round(min(1.0, strength), 3)])

    gray = moment.gray[0]
    colour = (np.clip(moment.color[0], 0, 1) * 255).round().astype(int)
    camera, velocity = moment.camera[0], moment.velocity[0]
    vectors = []
    if np.isfinite(camera[:2]).all():
        vectors.append({"label": "camera", "from": _point(0.5, 0.5),
                        "to": _point(0.5 + camera[0] * TRAVEL_SECONDS, 0.5 + camera[1] * TRAVEL_SECONDS)})
    if main[0] >= 0 and np.isfinite(velocity).all():
        box = moment.boxes[main[0]]
        centre = ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)
        vectors.append({"label": "subject", "from": _point(*centre),
                        "to": _point(centre[0] + velocity[0] * TRAVEL_SECONDS, centre[1] + velocity[1] * TRAVEL_SECONDS)})

    return [
        {"key": "objects", "label": "Objects", "kind": "regions", "items": regions},
        {"key": "pose", "label": "Pose", "kind": "skeletons", "joints": list(producer.KEYPOINT_NAMES),
         "edges": [list(edge) for edge in producer.SKELETON], "items": skeletons},
        {"key": "eyes", "label": "Eye point", "kind": "points",
         "items": [{"label": "eyes", "at": _point(*eye[0])}] if has_eye[0] else []},
        {"key": "lines", "label": "Lines", "kind": "field", "columns": int(columns), "rows": int(rows), "cells": cells},
        {"key": "light", "label": "Light", "kind": "grid", "columns": int(gray.shape[1]), "rows": int(gray.shape[0]),
         "values": [round(float(value), 3) for value in gray.ravel()]},
        {"key": "colour", "label": "Colour", "kind": "grid", "columns": int(colour.shape[1]), "rows": int(colour.shape[0]),
         "colors": [f"#{red:02x}{green:02x}{blue:02x}" for red, green, blue in colour.reshape(-1, 3)]},
        {"key": "motion", "label": "Motion", "kind": "vectors", "seconds": TRAVEL_SECONDS,
         "zoom": round(float(camera[2]), 3) if np.isfinite(camera[2]) else None, "items": vectors},
    ]


def source_models(film_row: dict[str, Any]) -> dict[str, str] | None:
    """The models behind a film's layers, by role: known for the current producer version only.
    A film described with the grounded subject backend (ADR-0119) names its grounder, the
    silhouette source and its pose model instead of the COCO pair."""
    if film_row.get("moments_profile") != producer.PRODUCER.profile_id:
        return None
    models = producer.model_names()
    record = film_row.get("subjects") or {}
    if isinstance(record, dict) and record.get("backend") == subjects.GROUNDED:
        named = record.get("models") or {}
        return {"objects": f"{named.get('grounder', 'grounder')}, silhouettes from {named.get('masks', models['objects'])}",
                "pose": named.get("pose", models["pose"])}
    return models


def describe(index: Any, unit_id: str, time_value: float) -> dict[str, Any]:
    """The analysed instant of *unit_id* nearest *time_value*, with its layers and provenance."""
    unit = index.unit_index(unit_id)                     # KeyError when the shot is not indexed
    rows = index.unit_rows(unit)
    if not len(rows):
        raise KeyError(unit_id)
    times = index.columns["time"][rows]
    row = int(rows[int(np.argmin(np.abs(times - time_value)))])
    film = int(index.columns["film"][row])
    film_row = index.manifest["films"][film]
    profile = film_row.get("moments_profile")
    return {
        "unit_id": unit_id, "film_id": index.film_ids[film], "time": round(float(index.columns["time"][row]), 3),
        "usable": bool(index.columns["ok"][row]), "aspect": float(index.film_aspect[film]),
        "source": {"index": index.id, "profile": profile, "models": source_models(film_row),
                   "subjects": ((film_row.get("subjects") or {}).get("backend") if isinstance(film_row.get("subjects"), dict)
                                else film_row.get("subjects")) or subjects.COCO},
        "layers": layers(index.moments(np.array([row]))),
    }
