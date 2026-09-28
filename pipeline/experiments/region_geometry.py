"""Shadow-only crop proposals for human-selected regions and instants.

Coordinates describe the full decoded, square-pixel source image, including
any letterboxing. Crops resize the whole image uniformly; objects are never
segmented, stretched, rotated, mirrored, or pasted into a new background.
This scorer knows geometry and crop costs, not semantic or motion similarity.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from typing import Any

PROFILE_ID = "region-crop-shadow-v1"
COORDINATE_SPACE = "decoded-source-normalized-v1"
EPSILON = 1e-9


def finite(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name} must be a finite number")
    return result


@dataclass(frozen=True)
class Box:
    x: float
    y: float
    width: float
    height: float

    def __post_init__(self):
        for key in ("x", "y", "width", "height"):
            object.__setattr__(self, key, finite(getattr(self, key), key))
        if self.x < 0 or self.y < 0 or self.width <= 0 or self.height <= 0:
            raise ValueError("A region must have positive size and nonnegative origin")
        if self.x + self.width > 1 + EPSILON or self.y + self.height > 1 + EPSILON:
            raise ValueError("A region must remain inside the source picture")

    @property
    def area(self) -> float:
        return self.width * self.height

    @property
    def center(self) -> tuple[float, float]:
        return self.x + self.width / 2, self.y + self.height / 2

    def contains(self, other: Box) -> bool:
        return (
            other.x >= self.x - EPSILON and other.y >= self.y - EPSILON
            and other.x + other.width <= self.x + self.width + EPSILON
            and other.y + other.height <= self.y + self.height + EPSILON
        )

    def intersection_area(self, other: Box) -> float:
        return max(0, min(self.x + self.width, other.x + other.width) - max(self.x, other.x)) * max(
            0, min(self.y + self.height, other.y + other.height) - max(self.y, other.y)
        )


@dataclass(frozen=True)
class Picture:
    width: int
    height: int

    def __post_init__(self):
        if any(isinstance(v, bool) or not isinstance(v, int) or v <= 0 for v in (self.width, self.height)):
            raise ValueError("Picture dimensions must be positive pixel integers")


@dataclass(frozen=True)
class CropProposal:
    reference_crop: Box
    candidate_crop: Box
    reference_region: Box
    candidate_region: Box
    reference_output_region: Box
    candidate_output_region: Box
    candidate_uniform_scale: float
    reference_uniform_scale: float
    geometry_similarity: float
    score: float
    crop_loss: float
    context_loss: float
    resolution_headroom: float
    reference_resolution_headroom: float

    def as_document(self) -> dict:
        return {
            "profile_id": PROFILE_ID,
            "coordinate_space": COORDINATE_SPACE,
            "shadow_only": True,
            "transform": "whole-image uniform scale and translation; no padding",
            **asdict(self),
        }


class NoFeasibleCrop(ValueError):
    """No legal crop retains the selected object within the resolution limit."""


def _crop_at_scale(
    picture: Picture, region: Box, output: Picture, scale: float,
    target_center: tuple[float, float],
) -> Box | None:
    width = output.width / scale / picture.width
    height = output.height / scale / picture.height
    if width > 1 + EPSILON or height > 1 + EPSILON:
        return None
    width, height = min(1.0, width), min(1.0, height)
    # Clamp the desired crop origin to the intersection of legal-image origins
    # and origins retaining the entire chosen object. Do not silently clip it.
    low_x, high_x = max(0, region.x + region.width - width), min(1 - width, region.x)
    low_y, high_y = max(0, region.y + region.height - height), min(1 - height, region.y)
    if low_x > high_x + EPSILON or low_y > high_y + EPSILON:
        return None
    x = min(high_x, max(low_x, region.center[0] - target_center[0] * width))
    y = min(high_y, max(low_y, region.center[1] - target_center[1] * height))
    return Box(max(0, x), max(0, y), width, height)


def _output_region(region: Box, crop: Box) -> Box:
    if not crop.contains(region):
        raise NoFeasibleCrop("The crop would remove part of the selected region")
    return Box(
        max(0, (region.x - crop.x) / crop.width),
        max(0, (region.y - crop.y) / crop.height),
        region.width / crop.width, region.height / crop.height,
    )


def propose_crop(
    reference_picture: Picture, reference_region: Box,
    candidate_picture: Picture, candidate_region: Box,
    *, output: Picture = Picture(1920, 1080), reference_crop: Box | None = None,
    protected_context: Box | None = None, max_upscale: float = 2.0,
) -> CropProposal:
    """Propose a bounded crop; position/shape mismatch remains visible in score.

    The reference crop is fixed if supplied. Otherwise use its largest
    output-aspect crop that retains the whole selected region. The candidate
    search is a deterministic small scale grid plus analytical alignments.
    Region class is deliberately absent: a circle may be an eye or a sun.
    """
    max_upscale = finite(max_upscale, "max_upscale")
    if not 1 <= max_upscale <= 8:
        raise ValueError("max_upscale must be between 1 and 8")
    if reference_crop is None:
        base_scale = max(output.width / reference_picture.width, output.height / reference_picture.height)
        reference_crop = _crop_at_scale(reference_picture, reference_region, output, base_scale, (0.5, 0.5))
        if reference_crop is None:
            raise NoFeasibleCrop("Reference region cannot fit the output aspect without padding")
    ratio = reference_crop.width * reference_picture.width / (reference_crop.height * reference_picture.height)
    if not math.isclose(ratio, output.width / output.height, rel_tol=1e-6):
        raise ValueError("Reference crop must have the output pixel aspect; distortion is forbidden")
    target = _output_region(reference_region, reference_crop)
    reference_scale = output.width / (reference_crop.width * reference_picture.width)
    if reference_scale > max_upscale + EPSILON:
        raise NoFeasibleCrop("Reference crop exceeds the permitted output enlargement")
    minimum = max(output.width / candidate_picture.width, output.height / candidate_picture.height)
    maximum = min(
        max_upscale,
        output.width / (candidate_region.width * candidate_picture.width),
        output.height / (candidate_region.height * candidate_picture.height),
    )
    if minimum > maximum + EPSILON:
        raise NoFeasibleCrop("No candidate crop retains the region within the output enlargement limit")
    rw, rh = candidate_region.width * candidate_picture.width, candidate_region.height * candidate_picture.height
    tw, th = target.width * output.width, target.height * output.height
    scales = {minimum, maximum, (rw * tw + rh * th) / (rw * rw + rh * rh), tw / rw, th / rh}
    scales.update(minimum * (maximum / minimum) ** (index / 32) for index in range(33))
    proposals = []
    for scale in sorted(scales):
        if scale < minimum - EPSILON or scale > maximum + EPSILON:
            continue
        crop = _crop_at_scale(candidate_picture, candidate_region, output, scale, target.center)
        if crop is None:
            continue
        shown = _output_region(candidate_region, crop)
        center_error = math.hypot(shown.center[0] - target.center[0], shown.center[1] - target.center[1])
        size_error = abs(math.log(shown.width / target.width)) + abs(math.log(shown.height / target.height))
        geometry = math.exp(-5 * center_error - size_error)
        crop_loss = 1 - crop.area
        context_loss = (
            1 - crop.intersection_area(protected_context) / protected_context.area
            if protected_context else
            1 - (crop.area - candidate_region.area) / (1 - candidate_region.area)
            if candidate_region.area < 1 - EPSILON else 0
        )
        headroom = 1 / scale
        score = max(0, geometry - 0.15 * crop_loss - 0.20 * context_loss - 0.20 * max(0, 1 - headroom))
        proposals.append(CropProposal(
            reference_crop, crop, reference_region, candidate_region, target, shown,
            scale, reference_scale, geometry, score, crop_loss, max(0, context_loss),
            headroom, 1 / reference_scale,
        ))
    if not proposals:
        raise NoFeasibleCrop("No feasible uniform crop remains")
    return max(proposals, key=lambda p: (p.score, p.geometry_similarity, p.candidate_crop.area))
