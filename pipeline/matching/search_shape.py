"""Search-only silhouette evidence from the existing 8x8 mask summaries.

Foreground IoU alone makes nearly filled rectangles generic matching templates.
An outlined shape must also agree on the space around its foreground. Position
is deliberately absent: the search engine measures that independent cue itself.
This scorer does not change the prepared mask profile or historical Lab scores.
"""
from __future__ import annotations

import numpy as np

from pipeline.matching.subjects import silhouette_overlap

CONTRACT = "foreground-and-negative-space-outline-v1"
MIN_SCORE = .45
# At 8x8, less than three empty-cell equivalents is too little outline evidence
# to compare the negative space. Interior solid shapes use their own branch.
SOLID_OCCUPANCY = 1 - 3 / 64
MIN_FOREGROUND_OVERLAP = .6
MIN_NEGATIVE_SPACE_OVERLAP = .5
MIN_OUTLINE_OVERLAP = .6
MIN_ASPECT_AGREEMENT = .5


def _geometry(frame):
    box = frame["box"]
    x, y, width, height = (float(box[name]) for name in ("x", "y", "width", "height"))
    area, picture_aspect = float(frame["area"]), float(frame.get("picture_aspect", 1.))
    if (not np.isfinite([x, y, width, height, area, picture_aspect]).all()
            or min(x, y) < 0 or min(width, height, area, picture_aspect) <= 0
            or area > 1 or x + width > 1.0000001 or y + height > 1.0000001):
        raise ValueError("Invalid normalized subject geometry")
    border = min(x, y, 1 - x - width, 1 - y - height) <= .01
    return area, width / height * picture_aspect, border


def shape_similarity(left, right):
    """Return independently supported shape evidence, never centroid agreement.

    Majority overlap is required on both the foreground and its negative space;
    their geometric mean prevents either from covering for unrelated geometry.
    Similar scale supports the score but is not a prerequisite for recognizing
    an otherwise matching silhouette at a different size. The physical aspect
    ratio restores geometry erased when each mask is resized to 8x8.
    """
    if not left["visible"] or not right["visible"]:
        return {"reliable": False, "score": -1., "components": {}}
    foreground = silhouette_overlap(left["silhouette"], right["silhouette"])
    a, b = np.asarray(left["silhouette"], dtype=float), np.asarray(right["silhouette"], dtype=float)
    areas, aspects, borders = zip(_geometry(left), _geometry(right))
    scale = min(areas) / max(areas)
    aspect = min(aspects) / max(aspects)
    occupancy = float(a.mean()), float(b.mean())
    solid = tuple(value >= SOLID_OCCUPANCY for value in occupancy)
    negative_union = float(np.maximum(1 - a, 1 - b).sum())
    negative = float(np.minimum(1 - a, 1 - b).sum() / negative_union) if negative_union > 1e-8 else None

    if all(solid):
        # An interior filled rectangle is a legitimate geometric shape. A solid
        # region cut off by the picture border has no measured complete outline.
        mode = "interior-solid"
        outline, supported = foreground, not any(borders)
    else:
        mode = "foreground-and-negative-space"
        outline = float(np.sqrt(foreground * (negative or 0.)))
        supported = (not any(solid) and foreground >= MIN_FOREGROUND_OVERLAP
                     and negative is not None and negative >= MIN_NEGATIVE_SPACE_OVERLAP
                     and outline >= MIN_OUTLINE_OVERLAP)
    score = outline * (.75 * aspect + .25 * scale)
    components = {"silhouette": outline, "foreground_overlap": foreground,
                  "negative_space_overlap": negative, "outline_mode": mode,
                  "reference_occupancy": occupancy[0], "candidate_occupancy": occupancy[1],
                  "scale": float(scale), "aspect": float(aspect), "scorer": CONTRACT}
    return {"reliable": bool(supported and aspect >= MIN_ASPECT_AGREEMENT and score >= MIN_SCORE),
            "score": float(score), "components": components,
            "outgoing_region": left["box"], "incoming_region": right["box"]}
