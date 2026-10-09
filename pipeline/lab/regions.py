"""Regions: one small spec that turns a shot into per-frame masks, for any treatment, lab or agent.

A region says *where* an effect applies. It is resolved once per shot window into boolean masks
(one per frame) and can be reused by the mosaic, the dots, the effects pass or a future lab. The
spec is plain data so an agent can write it and a receipt can record it.

    {"kind": "subject", "classes": ["person"], "largest": true, "dilate": 6, "feather": 0}
    {"kind": "background", "classes": ["person", "car"]}
    {"kind": "box", "x": 0.1, "y": 0.2, "w": 0.5, "h": 0.4}
    {"kind": "near", "share": 0.25}             # the nearest quarter of the frame, by monocular depth
    {"kind": "far", "share": 0.25}              # everything but that
    {"kind": "all"} / {"kind": "none"}

Masks are temporally smoothed (a short majority vote) so a detector's one-frame miss does not
flash. Segmentation uses the Lab's existing Segmenter (RF-DETR, the ``measure`` extra); when it is
not installed, subject and background regions resolve to None and the caller treats that as "all".
"""
from __future__ import annotations

from typing import Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

CLASSES = ("person", "car", "truck", "bus", "train", "boat", "motorcycle", "bicycle", "dog", "cat", "horse")


class Region(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    kind: Literal["all", "none", "subject", "background", "box", "near", "far"] = "all"
    share: float = Field(default=0.25, ge=0.02, le=0.9)   # near/far: the nearest share of the frame
    classes: list[str] = Field(default_factory=lambda: ["person", "car", "truck", "bus"])
    largest: bool = False                       # keep only the biggest instance per frame
    matte: bool = True                          # people by a video matte (hair-level edges) when available
    dilate: int = Field(default=0, ge=0, le=64)    # px grown outward (negative shrink: see erode)
    erode: int = Field(default=0, ge=0, le=64)
    feather: int = Field(default=0, ge=0, le=64)   # px of soft edge, when a treatment can use a float mask
    smooth: int = Field(default=3, ge=1, le=9)     # frames in the temporal majority vote
    x: float = Field(default=0, ge=0, le=1)        # box, as fractions of the frame
    y: float = Field(default=0, ge=0, le=1)
    w: float = Field(default=1, ge=0, le=1)
    h: float = Field(default=1, ge=0, le=1)


def segmentation_available() -> bool:
    try:
        import rfdetr  # noqa: F401
    except ImportError:
        return False
    return True


def _largest(instances: list[np.ndarray]) -> np.ndarray | None:
    if not instances:
        return None
    return max(instances, key=lambda m: int(m.sum()))


def _morph(mask: np.ndarray, dilate: int, erode: int) -> np.ndarray:
    if not dilate and not erode:
        return mask
    import cv2
    out = mask.astype(np.uint8)
    if erode:
        out = cv2.erode(out, np.ones((2 * erode + 1, 2 * erode + 1), np.uint8))
    if dilate:
        out = cv2.dilate(out, np.ones((2 * dilate + 1, 2 * dilate + 1), np.uint8))
    return out.astype(bool)


def smooth_masks(masks: list[np.ndarray | None], window: int = 3) -> list[np.ndarray | None]:
    """Temporal majority over a small window; frames without a mask borrow their neighbours'."""
    out: list[np.ndarray | None] = []
    for i in range(len(masks)):
        lo, hi = max(0, i - window // 2), min(len(masks), i + window // 2 + 1)
        group = [m for m in masks[lo:hi] if m is not None]
        if not group:
            out.append(None)
            continue
        out.append(np.mean([m.astype(np.float32) for m in group], axis=0) >= 0.5)
    return out


def resolve(frames: list[np.ndarray], region: Region | dict, *, progress=lambda _m: None,
            segmenter=None) -> list[np.ndarray | None]:
    """Per-frame boolean masks for ``region`` over ``frames`` (RGB uint8, all the same size).

    ``all`` -> a list of None (the caller treats None as everything). ``none`` -> all-False masks.
    ``subject`` / ``background`` need segmentation; without it they resolve to None.
    """
    spec = Region.model_validate(region)
    h, w = frames[0].shape[:2]
    if spec.kind == "all":
        return [None] * len(frames)
    if spec.kind == "none":
        return [np.zeros((h, w), bool) for _ in frames]
    if spec.kind == "box":
        mask = np.zeros((h, w), bool)
        x0, y0 = int(spec.x * w), int(spec.y * h)
        mask[y0:int((spec.y + spec.h) * h), x0:int((spec.x + spec.w) * w)] = True
        return [mask] * len(frames)
    if spec.kind in ("near", "far"):
        from pipeline.lab.depth import DepthEstimator
        near = DepthEstimator().shot(frames, progress=lambda f: progress(f"depth {int(f * len(frames)) + 1}/{len(frames)}"))
        sample = np.concatenate([n[::6, ::6].ravel() for n in near])
        threshold = float(np.percentile(sample, 100 * (1 - spec.share)))
        masks = smooth_masks([n >= threshold for n in near], spec.smooth)
        return [_morph(m, spec.dilate, spec.erode) if spec.kind == "near" else ~_morph(m, spec.dilate, spec.erode) for m in masks]
    alphas = _person_alphas(frames, spec, progress)
    if alphas is not None:
        masks = smooth_masks([a >= 0.5 for a in alphas], spec.smooth)
        if spec.largest:
            masks = [_largest_component(m) for m in masks]
        out = []
        for mask in masks:
            if mask is None or not mask.any():
                out.append(np.zeros((h, w), bool) if spec.kind == "subject" else np.ones((h, w), bool))
                continue
            mask = _morph(mask, spec.dilate, spec.erode)
            out.append(mask if spec.kind == "subject" else ~mask)
        return out
    if not segmentation_available():
        return [None] * len(frames)
    if segmenter is None:
        from pipeline.lab.effects import Segmenter
        segmenter = Segmenter()
    raw: list[np.ndarray | None] = []
    for i, frame in enumerate(frames):
        if i % 30 == 0:
            progress(f"region {i + 1}/{len(frames)}")
        instances = segmenter.instances(frame, spec.classes)
        mask = _largest(instances) if spec.largest else (np.any(instances, axis=0) if instances else None)
        raw.append(None if mask is None or not mask.any() else mask)
    masks = smooth_masks(raw, spec.smooth)
    out: list[np.ndarray | None] = []
    for mask in masks:
        if mask is None:
            out.append(np.zeros((h, w), bool) if spec.kind == "subject" else np.ones((h, w), bool))
            continue
        mask = _morph(mask, spec.dilate, spec.erode)
        out.append(mask if spec.kind == "subject" else ~mask)
    return out


def _wants_matte(spec: Region) -> bool:
    return spec.matte and spec.kind in ("subject", "background") and set(spec.classes) == {"person"}


def _person_alphas(frames, spec: Region, progress) -> list[np.ndarray] | None:
    """Matte alphas for a person region when the matte is wanted and the model runs; else None."""
    if not _wants_matte(spec):
        return None
    from pipeline.lab import matte
    if not matte.available():
        return None
    progress("matte")
    return matte.PersonMatte().alphas(frames, progress=lambda f: progress(f"matte {int(f * len(frames)) + 1}/{len(frames)}"))


def _largest_component(mask: np.ndarray | None) -> np.ndarray | None:
    if mask is None or not mask.any():
        return mask
    import cv2
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), connectivity=8)
    if n <= 2:
        return mask
    biggest = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    return labels == biggest


def resolve_soft(frames: list[np.ndarray], region: Region | dict, *, progress=lambda _m: None) -> list[np.ndarray | None]:
    """Float 0..1 masks: the matte's own alpha for matted person regions (hair-level edges), else the
    boolean masks of ``resolve`` as floats. None means the whole frame."""
    spec = Region.model_validate(region)
    alphas = _person_alphas(frames, spec, progress) if spec.kind in ("subject", "background") else None
    if alphas is not None:
        if spec.largest:
            keep = [_largest_component(a >= 0.5) for a in alphas]
            alphas = [a * k.astype(np.float32) if k is not None else a for a, k in zip(alphas, keep)]
        if spec.dilate or spec.erode:
            alphas = [np.maximum(a, _morph(a >= 0.5, spec.dilate, spec.erode).astype(np.float32)) if spec.dilate
                      else a * _morph(a >= 0.5, 0, spec.erode).astype(np.float32) for a in alphas]
        return alphas if spec.kind == "subject" else [1.0 - a for a in alphas]
    return [None if m is None else m.astype(np.float32) for m in resolve(frames, spec, progress=progress)]


def soft(mask: np.ndarray | None, feather: int) -> np.ndarray | None:
    """A float 0..1 mask with a feathered edge, for treatments that blend."""
    if mask is None or feather <= 0:
        return None if mask is None else mask.astype(np.float32)
    import cv2
    return cv2.GaussianBlur(mask.astype(np.float32), (0, 0), feather)


def coverage(masks: list[np.ndarray | None]) -> float:
    """Mean share of the frame the region covers (None counts as the whole frame)."""
    if not masks:
        return 0.0
    return float(np.mean([1.0 if m is None else m.mean() for m in masks]))


def harden(alpha: np.ndarray, *, erode: int = 2, width: float = 0.25) -> np.ndarray:
    """A matte alpha pulled inward: eroded by ``erode`` px and its transition narrowed to ``width`` around
    0.5, so no sliver of the real background rides along the figure's edge (a halo against paint)."""
    import cv2
    a = np.clip((alpha - 0.5) / max(width, 1e-3) + 0.5, 0.0, 1.0).astype(np.float32)
    if erode:
        a = cv2.erode(a, np.ones((2 * erode + 1, 2 * erode + 1), np.uint8))
    return a


def keep_live(frames: list[np.ndarray], treated: list[np.ndarray], region: Region | dict | None, *,
              edge: float = 1.5, progress=lambda _m: None) -> list[np.ndarray]:
    """The film shows through ``region`` and the treatment fills the rest: a live layer inside a
    treated world. A hard edge (``edge`` px of blur) so nothing reads as a see-through overlay.
    ``None`` or kind ``none`` returns ``treated`` unchanged."""
    if region is None:
        return treated
    spec = Region.model_validate(region)
    if spec.kind == "none":
        return treated
    import cv2
    masks = resolve_soft(frames, spec, progress=progress)
    matted = _wants_matte(spec)
    out = []
    for frame, done, mask in zip(frames, treated, masks):
        if mask is None:
            out.append(frame)
            continue
        alpha = harden(mask) if matted else (cv2.GaussianBlur(mask, (0, 0), edge) if edge else mask)
        alpha = alpha[..., None]
        out.append((frame.astype(np.float32) * alpha + done.astype(np.float32) * (1 - alpha)).astype(np.uint8))
    return out
