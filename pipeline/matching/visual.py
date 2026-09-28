"""Class-agnostic region proposals over independently retrieved dense features."""

from __future__ import annotations

from dataclasses import asdict
import numpy as np

from pipeline.experiments.dense_geometry import region_descriptor, dense_similarity
from pipeline.experiments.region_geometry import (
    Box,
    propose_crop,
    NoFeasibleCrop,
)


def regions(reference: Box):
    result = [Box(0, 0, 1, 1)]
    for scale in (0.5, 0.75, 1.0):
        width, height = reference.width * scale, reference.height * scale
        factor = min(1 / width, 1 / height, 2.0)
        width, height = width * factor, height * factor
        for y in (0, (1 - height) / 2, 1 - height):
            for x in (0, (1 - width) / 2, 1 - width):
                box = Box(x, y, width, height)
                if box not in result:
                    result.append(box)
    return result[:28]


def correspondence_region(query, qview, selected, candidate, cview):
    """Mutual patch matches propose a positive uniform scale/translation only."""
    query_grid = region_descriptor(query, qview, selected).reshape(64, -1)
    # Use an image-normalized 8x8 grid so padding cannot vote for geometry.
    target_grid = region_descriptor(candidate, cview).reshape(64, -1)
    scores = query_grid @ target_grid.T
    indices = scores.argmax(axis=1)
    mutual = scores.argmax(axis=0)[indices] == np.arange(64)
    good = mutual & (scores[np.arange(64), indices] > 0.25)
    if good.sum() < 6:
        return None
    xy = np.stack(
        np.meshgrid((np.arange(8) + 0.5) / 8, (np.arange(8) + 0.5) / 8), axis=-1
    ).reshape(64, 2)
    source = xy[good] * np.array([selected.width, selected.height])
    target = xy[indices[good]]
    centered = source - source.mean(axis=0)
    denominator = np.sum(centered * centered)
    if denominator < 1e-6:
        return None
    scale = float(np.sum(centered * (target - target.mean(axis=0))) / denominator)
    offset = target.mean(axis=0) - scale * source.mean(axis=0)
    error = np.median(np.linalg.norm(source * scale + offset - target, axis=1))
    extent = scale * np.array([selected.width, selected.height])
    if (
        scale <= 0
        or np.any(extent < 0.03)
        or error > 0.15
        or np.any(offset < 0)
        or np.any(offset + extent > 1)
    ):
        return None
    return Box(float(offset[0]), float(offset[1]), float(extent[0]), float(extent[1]))


def compare(
    query,
    qview,
    selected,
    candidate,
    cview,
    *,
    reference_picture,
    candidate_picture,
    output,
    allow_crop=False,
    reference_crop=None,
    use_correspondence=True,
):
    selected = selected or Box(0, 0, 1, 1)
    query_descriptor = region_descriptor(query, qview, selected)
    options = regions(selected) if selected != Box(0, 0, 1, 1) else [Box(0, 0, 1, 1)]
    if use_correspondence and selected != Box(0, 0, 1, 1):
        proposal = correspondence_region(query, qview, selected, candidate, cview)
        if proposal and proposal not in options:
            options.append(proposal)
    best = None
    for region in options[:32]:
        correspondence = dense_similarity(
            query_descriptor, region_descriptor(candidate, cview, region)
        )
        crop, penalties = None, 0.0
        if allow_crop:
            try:
                proposed = propose_crop(
                    reference_picture,
                    selected,
                    candidate_picture,
                    region,
                    output=output,
                    reference_crop=reference_crop,
                    max_upscale=2,
                )
                crop = proposed.as_document()
                penalties = (
                    0.15 * proposed.crop_loss
                    + 0.20 * proposed.context_loss
                    + 0.20 * max(0, 1 - proposed.resolution_headroom)
                )
            except NoFeasibleCrop:
                continue
        else:
            # Region appearance alone must not ignore the position/scale of the cut.
            position = np.linalg.norm(
                np.array(selected.center) - np.array(region.center)
            )
            size = abs(np.log(selected.area / region.area))
            penalties = 0.20 * float(position) + 0.10 * float(size)
        score = correspondence - penalties
        if best is None or score > best["score"]:
            best = {
                "score": score,
                "correspondence": correspondence,
                "region": asdict(region),
                "crop": crop,
            }
    return best
