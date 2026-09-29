"""How well two instants cut together: measured, explainable, vectorized over candidates.

A cut pair is an outgoing instant ``a`` (the last frame of the outgoing shot)
and an incoming instant ``b`` (the first frame of the next). Each is seen
through a *crop* ``(x, y, w, h)`` of its content picture, in content
fractions; the output frame shows that crop. Without reframing the crop is the
whole picture; a vertical edit crops a window of the output's aspect; a
reframe zooms the incoming crop (up to ``zoom_max``) and moves it so its
subject lands where the outgoing subject was. Every comparison happens in
output coordinates, so reframing, letterboxing and vertical crops are the same
arithmetic.

Rewards, each calibrated against random pairs of library instants (0 = no
better than chance, 0.8 = top 1%, 1 = top 0.1%):

* ``subject`` — the salient objects overlap (instance IoU after assignment,
  as in Netflix's match-cutting study, where it beat learned embeddings for
  framing matches); class families only discount, because detectors misname
  things that still read as the same shape;
* ``eyes`` — the eye-trace point (a person's eyes, else the subject's centre)
  lands where the viewer was already looking; a person whose head is out of
  frame has no eye point;
* ``pose`` — people's keypoints agree (object keypoint similarity), counting
  body parts shown in only one frame against the match, so a close-up meets a
  close-up and a full figure a full figure;
* ``shape`` — the main silhouettes agree regardless of where they are;
* ``light`` — the light and dark masses fall in the same places;
* ``lines`` — dominant edge orientations line up (horizons, doorways, limbs);
* ``color`` — the colour layout carries over;
* ``motion`` — camera movement and subject travel continue across the cut.

Penalties: a brightness jump (``tone``) and resolution given up by zooming.
Weights depend on the focus. Scores are ordering heuristics, not
probabilities.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Any

import numpy as np

SILHOUETTE = 16
LANDSCAPE, VERTICAL, SQUARE = "landscape", "vertical", "square"
OUTPUT_ASPECT = {LANDSCAPE: 16 / 9, VERTICAL: 9 / 16, SQUARE: 1.0}
STILL = 0.02            # image speed (frame fractions per second) below which a camera counts as still
REWARDS = ("subject", "eyes", "pose", "shape", "light", "lines", "color", "motion")
FOCUS_WEIGHTS = {
    "auto_subject": {"subject": 0.2, "eyes": 0.14, "pose": 0.16, "shape": 0.06, "light": 0.12, "lines": 0.05,
                     "color": 0.05, "motion": 0.22},
    "auto_picture": {"light": 0.36, "lines": 0.2, "color": 0.14, "motion": 0.3},
    "subject": {"subject": 0.34, "eyes": 0.22, "pose": 0.16, "shape": 0.06, "light": 0.06, "color": 0.04, "motion": 0.12},
    "shape": {"subject": 0.2, "eyes": 0.04, "pose": 0.2, "shape": 0.36, "light": 0.08, "lines": 0.07, "motion": 0.05},
    "motion": {"subject": 0.1, "eyes": 0.06, "pose": 0.04, "light": 0.1, "color": 0.05, "motion": 0.65},
    "composition": {"subject": 0.12, "eyes": 0.05, "light": 0.4, "lines": 0.3, "color": 0.08, "motion": 0.05},
    "color": {"subject": 0.1, "light": 0.2, "lines": 0.05, "color": 0.55, "motion": 0.1},
}
# COCO keypoint sigmas (x2) for object keypoint similarity; indices: 0 nose, 1-2 eyes, 3-4 ears,
# 5-6 shoulders, 7-8 elbows, 9-10 wrists, 11-12 hips, 13-14 knees, 15-16 ankles.
KEYPOINT_KAPPA = 2 * np.array([.026, .025, .025, .035, .035, .079, .079, .072, .072, .062, .062, .107, .107,
                               .087, .087, .089, .089])
SEEN = 0.35             # keypoint confidence at which a body part counts as shown
FOCI = ("auto", "subject", "shape", "motion", "composition", "color")
TONE_PENALTY = 0.12
# Quantiles (p50, p90, p99, p99.9) of each raw component over random library pairs;
# an index build measures its own (``pipeline.matching.moments.index``). These defaults
# come from a 155k-instant pilot index and only apply to an index without them.
DEFAULT_CALIBRATION = {
    "subject": [0.09, 0.3, 0.56, 0.73], "eyes": [0.05, 0.45, 0.9, 0.99], "shape": [0.38, 0.6, 0.75, 0.85],
    "light": [0.02, 0.36, 0.62, 0.82], "lines": [0.01, 0.15, 0.32, 0.51], "color": [0.05, 0.55, 0.75, 0.87],
    "motion": [0.4, 0.64, 0.7, 0.86], "pose": [0.0, 0.2, 0.55, 0.75],
}
# COCO classes as dense codes 1..80 (see pipeline.evidence.moments): families for compatibility.
PERSON = 1
ANIMALS = set(range(15, 25))          # bird .. giraffe
VEHICLES = set(range(2, 10))          # bicycle .. boat


def family(code: int) -> int:
    if code == PERSON:
        return 0
    if code in ANIMALS:
        return 1
    if code in VEHICLES:
        return 2
    return 3


_FAMILY = np.array([family(code) for code in range(256)], dtype=np.int8)
# Same family 1.0; people and animals often read alike on screen (and detectors confuse them).
_COMPATIBLE = np.array([[1.0, 0.8, 0.55, 0.55], [0.8, 1.0, 0.6, 0.6], [0.55, 0.6, 1.0, 0.7], [0.55, 0.6, 0.7, 1.0]],
                       dtype=np.float32)


@dataclass
class Moments:
    """N described instants (one side of N cut pairs), in content coordinates."""

    gray: np.ndarray            # N x 18 x 32 float32 in [0, 1]
    field: np.ndarray           # N x 2 x 9 x 16 float32 doubled-angle edge field in [-1, 1]
    color: np.ndarray           # N x 5 x 8 x 3 float32 in [0, 1]
    brightness: np.ndarray      # N
    camera: np.ndarray          # N x 4 image motion (vx, vy, div, curl) per second; NaN when unknown
    velocity: np.ndarray        # N x 2 main-subject travel (content fractions per second); NaN when unknown
    aspect: np.ndarray          # N display aspect (width / height) of the content picture
    inst_ptr: np.ndarray        # N + 1 offsets into the instance arrays
    boxes: np.ndarray           # K x 4 (x0, y0, x1, y1)
    masks: np.ndarray           # K x 16 x 16 bool silhouettes inside the boxes
    classes: np.ndarray         # K dense COCO codes
    scores: np.ndarray          # K detection scores
    pose_ptr: np.ndarray | None = None     # N + 1 offsets into the pose arrays (largest person first)
    pose_xy: np.ndarray | None = None      # P x 17 x 2 keypoints (content fractions)
    pose_conf: np.ndarray | None = None    # P x 17 keypoint confidence

    def __len__(self) -> int:
        return len(self.gray)

    def take(self, rows: np.ndarray) -> "Moments":
        rows = np.asarray(rows, dtype=np.int64)
        starts, ends = self.inst_ptr[rows], self.inst_ptr[rows + 1]
        counts = ends - starts
        index = np.concatenate([np.arange(s, e) for s, e in zip(starts, ends)]).astype(np.int64) if counts.sum() \
            else np.zeros(0, np.int64)
        poses = {}
        if self.pose_ptr is not None:
            pose_starts, pose_ends = self.pose_ptr[rows], self.pose_ptr[rows + 1]
            pose_counts = pose_ends - pose_starts
            pose_index = np.concatenate([np.arange(s, e) for s, e in zip(pose_starts, pose_ends)]).astype(np.int64) \
                if pose_counts.sum() else np.zeros(0, np.int64)
            poses = {"pose_ptr": np.concatenate([[0], np.cumsum(pose_counts)]).astype(np.int64),
                     "pose_xy": self.pose_xy[pose_index], "pose_conf": self.pose_conf[pose_index]}
        return Moments(self.gray[rows], self.field[rows], self.color[rows], self.brightness[rows], self.camera[rows],
                       self.velocity[rows], self.aspect[rows], np.concatenate([[0], np.cumsum(counts)]).astype(np.int64),
                       self.boxes[index], self.masks[index], self.classes[index], self.scores[index], **poses)

    def main_pose(self) -> np.ndarray:
        """Index of each moment's largest person pose, or -1."""
        if self.pose_ptr is None:
            return np.full(len(self), -1, dtype=np.int64)
        return np.where(np.diff(self.pose_ptr) > 0, self.pose_ptr[:-1], -1)


@dataclass
class Options:
    focus: str = "auto"
    output: str = LANDSCAPE
    reframe: bool = False
    zoom_max: float = 1.5
    outgoing_crop: tuple[float, float, float, float] | None = None
    weights: dict[str, float] | None = None
    calibration: dict[str, list[float]] | None = None


@dataclass
class Scored:
    """Per candidate: total score, components, incoming crop and zoom (arrays of length N)."""

    total: np.ndarray
    parts: dict[str, np.ndarray]           # raw measurements
    calibrated: dict[str, np.ndarray]      # rewards on the common scale
    crop: np.ndarray                       # N x 4 incoming crops
    zoom: np.ndarray                       # N
    outgoing_crop: tuple[float, float, float, float]
    weights: dict[str, float] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------


def grid_for(output: str) -> tuple[int, int]:
    """Comparison grid (rows, columns) for an output shape, about 576 cells."""
    return {LANDSCAPE: (18, 32), VERTICAL: (32, 18), SQUARE: (24, 24)}[output]


def base_crop(aspect: np.ndarray | float, output: str) -> np.ndarray:
    """Largest centred crop of the output's shape: the whole picture for landscape (letterboxed if needed)."""
    aspect = np.atleast_1d(np.asarray(aspect, dtype=np.float64))
    if output == LANDSCAPE:
        return np.tile(np.array([0.0, 0.0, 1.0, 1.0]), (len(aspect), 1))
    width = np.minimum(1.0, OUTPUT_ASPECT[output] / aspect)
    height = np.minimum(1.0, aspect / OUTPUT_ASPECT[output]) * np.ones_like(width)
    return np.stack([(1 - width) / 2, (1 - height) / 2, width, height], axis=1)


def box_area(boxes: np.ndarray) -> np.ndarray:
    return np.clip(boxes[..., 2] - boxes[..., 0], 0, None) * np.clip(boxes[..., 3] - boxes[..., 1], 0, None)


def to_output(boxes: np.ndarray, crops: np.ndarray) -> np.ndarray:
    """Content boxes (..., 4) seen through crops (..., 4) as output fractions (unclipped)."""
    x, y, w, h = (crops[..., i] for i in range(4))
    return np.stack([(boxes[..., 0] - x) / w, (boxes[..., 1] - y) / h, (boxes[..., 2] - x) / w, (boxes[..., 3] - y) / h], axis=-1)


def point_to_output(points: np.ndarray, crops: np.ndarray) -> np.ndarray:
    return np.stack([(points[..., 0] - crops[..., 0]) / crops[..., 2], (points[..., 1] - crops[..., 1]) / crops[..., 3]], axis=-1)


def salient(moments: Moments, limit: int = 4, share: float = 0.2) -> tuple[np.ndarray, np.ndarray]:
    """``(N x limit instance index or -1, N main index or -1)``: the prominent group of each moment.

    The group keeps instances at least ``share`` of the largest one's
    (area x score), so a background crowd does not count as the subject.
    """
    n = len(moments)
    group = np.full((n, limit), -1, dtype=np.int64)
    main = np.full(n, -1, dtype=np.int64)
    if len(moments.boxes) == 0:
        return group, main
    weight = box_area(moments.boxes) * np.maximum(moments.scores, 0.3)
    counts = np.diff(moments.inst_ptr)
    owner = np.repeat(np.arange(n), counts)
    order = np.lexsort((-weight, owner))                        # by moment, heaviest first
    rank = np.arange(len(order)) - np.repeat(moments.inst_ptr[:-1], counts)
    sorted_owner = owner[order]
    top = np.zeros(n)
    firsts = order[rank == 0]
    top[owner[firsts]] = weight[firsts]
    keep = (weight[order] >= share * top[sorted_owner]) & (rank < limit)
    kept = order[keep]
    kept_owner = owner[kept]
    slot = np.arange(len(kept)) - np.searchsorted(kept_owner, kept_owner, side="left")
    group[kept_owner, slot] = kept
    main[owner[firsts]] = firsts
    return group, main


def anchors(moments: Moments, instances: np.ndarray) -> np.ndarray:
    """Eye-trace point per instance (content fractions): a person's head, else the box centre.

    The head is the top of the silhouette: the mean column of the top three
    occupied rows, a row and a half below the top.
    """
    out = np.zeros((len(instances), 2))
    if not len(instances):
        return out
    boxes = moments.boxes[instances]
    masks = moments.masks[instances]
    rows_any = masks.any(axis=2)
    top = np.argmax(rows_any, axis=1)
    band = (np.arange(SILHOUETTE)[None, :] >= top[:, None]) & (np.arange(SILHOUETTE)[None, :] < top[:, None] + 3)
    weights = (masks * band[:, :, None]).sum(axis=1)                 # K x 16 columns
    column = (weights * (np.arange(SILHOUETTE) + 0.5)).sum(axis=1) / np.maximum(weights.sum(axis=1), 1)
    head_x = boxes[:, 0] + column / SILHOUETTE * (boxes[:, 2] - boxes[:, 0])
    head_y = boxes[:, 1] + (top + 1.5) / SILHOUETTE * (boxes[:, 3] - boxes[:, 1])
    person = (moments.classes[instances] == PERSON) & rows_any.any(axis=1)
    out[:, 0] = np.where(person, head_x, (boxes[:, 0] + boxes[:, 2]) / 2)
    out[:, 1] = np.where(person, head_y, (boxes[:, 1] + boxes[:, 3]) / 2)
    return out


def eye_points(moments: Moments, main: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Per moment: the eye-trace point (content fractions) and whether it exists.

    With a pose for the largest person: the mean of the shown eyes (else nose,
    else ears); a person whose face is out of frame has no eye point. Without a
    pose, only an evident point counts: a person's silhouette head when the
    frame does not cut the top of the person off, else the centre of a subject
    that does not fill the picture (the centre of a frame-filling blob says
    nothing about where the eye goes).
    """
    n = len(moments)
    points = np.full((n, 2), np.nan)
    has_main = main >= 0
    if has_main.any():
        boxes = moments.boxes[main[has_main]]
        person = moments.classes[main[has_main]] == PERSON
        evident = np.where(person, boxes[:, 1] > 0.02, box_area(boxes) < 0.6)
        anchor = anchors(moments, main[has_main])
        points[has_main] = np.where(evident[:, None], anchor, np.nan)
    main_pose = moments.main_pose()
    with_pose = main_pose >= 0
    if with_pose.any():
        xy = moments.pose_xy[main_pose[with_pose]].astype(np.float64)                 # M x 17 x 2
        seen = moments.pose_conf[main_pose[with_pose]] >= SEEN
        face = np.full((len(xy), 2), np.nan)
        for group in ((1, 2), (0,), (3, 4)):
            chosen = seen[:, group]
            count = chosen.sum(axis=1)
            mean = (xy[:, group] * chosen[..., None]).sum(axis=1) / np.maximum(count, 1)[:, None]
            fill = np.isnan(face[:, 0]) & (count > 0)
            face[fill] = mean[fill]
        points[with_pose] = face
        # A person pose owns the eye point: if its face is not shown there is none.
    exists = np.isfinite(points).all(axis=1)
    return points, exists


def _sizes(boxes: np.ndarray, aspect: np.ndarray | float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Subject height, width (in picture-height units) and whether the frame's bottom cuts it off."""
    height = boxes[..., 3] - boxes[..., 1]
    width = (boxes[..., 2] - boxes[..., 0]) * aspect
    return height, width, boxes[..., 3] >= 0.97


def solve_crops(reference_anchor: np.ndarray | None, reference_size: tuple[float, float, bool] | None,
                candidates: Moments, main: np.ndarray, options: Options) -> tuple[np.ndarray, np.ndarray]:
    """Incoming crops that put each candidate's eye-trace point where the reference's is (output fractions).

    Scale follows subject height, or width when either frame cuts its subject
    off at the bottom (a waist-up framing has no comparable height). Zoom only
    enlarges (up to ``zoom_max``); the crop stays inside the picture. Without
    a reference subject, a candidate subject or reframing, the crop is the
    base crop (vertical output centres it on the subject).
    """
    base = base_crop(candidates.aspect, options.output)
    crops = base.copy()
    zoom = np.ones(len(candidates))
    has = main >= 0
    if not has.any():
        return crops, zoom
    safe = np.maximum(main, 0)
    point, point_exists = eye_points(candidates, main)
    point = np.where(point_exists[:, None], point, anchors(candidates, safe))
    boxes = candidates.boxes[safe]
    target = reference_anchor if reference_anchor is not None else np.array([0.5, 0.5])
    if options.reframe and reference_anchor is not None and reference_size:
        # Sizes in picture-height units; seen through a crop of height h they scale by 1 / h.
        ref_height, ref_width, ref_cut = reference_size
        height, width, cut = _sizes(boxes, candidates.aspect)
        use_width = cut | ref_cut
        wanted = np.where(use_width, width / max(ref_width, 1e-6), height / max(ref_height, 1e-6))
        height = np.clip(wanted, base[:, 3] / options.zoom_max, base[:, 3])
        zoom = np.where(has, base[:, 3] / height, 1.0)
        crops[:, 3] = np.where(has, height, base[:, 3])
        crops[:, 2] = np.where(has, base[:, 2] / zoom, base[:, 2])
    if options.reframe or options.output != LANDSCAPE:
        x = np.clip(point[:, 0] - target[0] * crops[:, 2], 0.0, 1.0 - crops[:, 2])
        y = np.clip(point[:, 1] - target[1] * crops[:, 3], 0.0, 1.0 - crops[:, 3])
        crops[:, 0] = np.where(has, x, crops[:, 0])
        crops[:, 1] = np.where(has, y, crops[:, 1])
    return crops, zoom


def reference_crop(reference: Moments, main: int, options: Options) -> np.ndarray:
    """The outgoing crop: given (a chain keeps it), else the base crop centred on its subject."""
    if options.outgoing_crop is not None:
        return np.asarray(options.outgoing_crop, dtype=np.float64)
    crop = base_crop(reference.aspect[:1], options.output)[0]
    if options.output != LANDSCAPE and main >= 0:
        point, exists = eye_points(reference, np.array([main]))
        point = point[0] if exists[0] else anchors(reference, np.array([main]))[0]
        crop[0] = float(np.clip(point[0] - crop[2] / 2, 0.0, 1.0 - crop[2]))
        crop[1] = float(np.clip(point[1] - crop[3] / 2, 0.0, 1.0 - crop[3]))
    return crop


# ---------------------------------------------------------------------------
# Sampling pictures through crops
# ---------------------------------------------------------------------------


def _cell_centers(rows: int, columns: int, supersample: int = 1) -> tuple[np.ndarray, np.ndarray]:
    steps_y = (np.arange(rows * supersample) + 0.5) / (rows * supersample)
    steps_x = (np.arange(columns * supersample) + 0.5) / (columns * supersample)
    return np.meshgrid(steps_y, steps_x, indexing="ij")


def _identity(crops: np.ndarray) -> bool:
    return bool(np.allclose(crops, np.array([0.0, 0.0, 1.0, 1.0])))


def sample(images: np.ndarray, crops: np.ndarray, grid: tuple[int, int]) -> np.ndarray:
    """Bilinear samples of N x H x W (or N x C x H x W) images through N crops onto a grid; NaN outside."""
    channels = images.ndim == 4
    stack = images if channels else images[:, None]
    n, c, height, width = stack.shape
    if (height, width) == tuple(grid) and _identity(crops):
        value = stack.astype(np.float32)
        return value if channels else value[:, 0]
    v, u = _cell_centers(*grid)
    x = crops[:, 0, None, None] + u[None] * crops[:, 2, None, None]
    y = crops[:, 1, None, None] + v[None] * crops[:, 3, None, None]
    outside = (x < 0) | (x > 1) | (y < 0) | (y > 1)
    px = np.clip(x * width - 0.5, 0, width - 1)
    py = np.clip(y * height - 0.5, 0, height - 1)
    x0, y0 = np.floor(px).astype(np.int64), np.floor(py).astype(np.int64)
    x1, y1 = np.minimum(x0 + 1, width - 1), np.minimum(y0 + 1, height - 1)
    fx, fy = (px - x0)[:, None].astype(np.float32), (py - y0)[:, None].astype(np.float32)
    rows = np.arange(n)[:, None, None]

    def at(yy, xx):
        return stack[rows, :, yy, xx].transpose(0, 3, 1, 2)          # N x C x R x C'

    value = (at(y0, x0) * (1 - fx) * (1 - fy) + at(y0, x1) * fx * (1 - fy)
             + at(y1, x0) * (1 - fx) * fy + at(y1, x1) * fx * fy)
    value = np.where(outside[:, None], np.nan, value).astype(np.float32)
    return value if channels else value[:, 0]


def rasterize(moments: Moments, instances: np.ndarray, crops: np.ndarray, grid: tuple[int, int],
              supersample: int = 3) -> np.ndarray:
    """Coverage (K x rows x columns) of the given instances seen through their moments' crops."""
    rows, columns = grid
    if len(instances) == 0:
        return np.zeros((0, rows, columns), dtype=np.float32)
    v, u = _cell_centers(rows, columns, supersample)
    owner = np.searchsorted(moments.inst_ptr, instances, side="right") - 1
    crop = crops[owner]
    box = moments.boxes[instances]
    x = crop[:, 0, None, None] + u[None] * crop[:, 2, None, None]
    y = crop[:, 1, None, None] + v[None] * crop[:, 3, None, None]
    width = np.maximum(box[:, 2] - box[:, 0], 1e-6)[:, None, None]
    height = np.maximum(box[:, 3] - box[:, 1], 1e-6)[:, None, None]
    sx = (x - box[:, 0, None, None]) / width
    sy = (y - box[:, 1, None, None]) / height
    inside = (sx >= 0) & (sx < 1) & (sy >= 0) & (sy < 1)
    ix = np.clip((sx * SILHOUETTE).astype(np.int64), 0, SILHOUETTE - 1)
    iy = np.clip((sy * SILHOUETTE).astype(np.int64), 0, SILHOUETTE - 1)
    hits = moments.masks[instances[:, None, None], iy, ix] & inside
    return hits.reshape(len(instances), rows, supersample, columns, supersample).mean(axis=(2, 4)).astype(np.float32)


# ---------------------------------------------------------------------------
# Components (raw)
# ---------------------------------------------------------------------------


def _pearson(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Correlation of (1 x R x C) with (N x R x C) over finite cells; 0 when flat."""
    valid = np.isfinite(a) & np.isfinite(b)
    count = np.maximum(valid.sum(axis=(1, 2)), 1)
    a0 = np.where(valid, a, 0.0)
    b0 = np.where(valid, b, 0.0)
    mean_a = a0.sum(axis=(1, 2)) / count
    mean_b = b0.sum(axis=(1, 2)) / count
    da = np.where(valid, a0 - mean_a[:, None, None], 0.0)
    db = np.where(valid, b0 - mean_b[:, None, None], 0.0)
    numerator = (da * db).sum(axis=(1, 2))
    denominator = np.sqrt((da * da).sum(axis=(1, 2)) * (db * db).sum(axis=(1, 2)))
    return np.where(denominator > 1e-6, numerator / np.maximum(denominator, 1e-9), 0.0)


def _blur(images: np.ndarray) -> np.ndarray:
    """3x3 box blur ignoring NaN cells (last two axes)."""
    finite = np.isfinite(images)
    values = np.where(finite, images, 0.0)
    weights = finite.astype(np.float32)
    pad = [(0, 0)] * (images.ndim - 2) + [(1, 1), (1, 1)]
    values, weights = np.pad(values, pad), np.pad(weights, pad)
    rows, columns = images.shape[-2:]
    total = sum(values[..., dy:dy + rows, dx:dx + columns] for dy in range(3) for dx in range(3))
    count = sum(weights[..., dy:dy + rows, dx:dx + columns] for dy in range(3) for dx in range(3))
    return np.where(finite, total / np.maximum(count, 1e-9), np.nan)


def light(reference_gray: np.ndarray, candidate_gray: np.ndarray) -> np.ndarray:
    """Where the light falls: correlation of blurred luma layouts (its positive part)."""
    return np.clip(_pearson(_blur(reference_gray), _blur(candidate_gray)), 0.0, 1.0)


def lines(reference_field: np.ndarray, candidate_field: np.ndarray) -> np.ndarray:
    """Edge orientation agreement: cosine of the doubled-angle fields (1 x 2 x R x C vs N x 2 x R x C)."""
    a = np.nan_to_num(reference_field)
    b = np.nan_to_num(candidate_field)
    numerator = (a * b).sum(axis=(1, 2, 3))
    denominator = np.sqrt((a * a).sum(axis=(1, 2, 3)) * (b * b).sum(axis=(1, 2, 3)))
    return np.clip(np.where(denominator > 1e-6, numerator / np.maximum(denominator, 1e-9), 0.0), 0.0, 1.0)


def color(reference_color: np.ndarray, candidate_color: np.ndarray) -> np.ndarray:
    """Colour layout carry-over: 1 at identical cells, 0 at a mean cell distance of 0.35 (sRGB)."""
    difference = np.sqrt(np.nansum((reference_color - candidate_color) ** 2, axis=1))       # N x R x C
    valid = np.isfinite(candidate_color).all(axis=1) & np.isfinite(reference_color).all(axis=1)
    mean = np.where(valid, difference, 0.0).sum(axis=(1, 2)) / np.maximum(valid.sum(axis=(1, 2)), 1)
    return np.clip(1.0 - mean / 0.35, 0.0, 1.0)


def tone(reference_brightness: float, candidate_brightness: np.ndarray) -> np.ndarray:
    jump = np.abs(np.log((candidate_brightness + 0.05) / (reference_brightness + 0.05)))
    return np.clip(1.0 - jump / math.log(3.0), 0.0, 1.0)


def _continuity(a: np.ndarray, b: np.ndarray, still: float) -> tuple[np.ndarray, np.ndarray]:
    """Continuation of 2-D velocity ``a`` (2,) into ``b`` (N x 2): score and whether both are known."""
    known = np.isfinite(b).all(axis=1) & bool(np.isfinite(a).all())
    speed_a = float(np.hypot(*a)) if np.isfinite(a).all() else 0.0
    speed_b = np.hypot(b[:, 0], b[:, 1])
    both_still = (speed_a < still) & (speed_b < still)
    one_still = (speed_a < still) ^ (speed_b < still)
    cosine = (np.nan_to_num(b) @ np.nan_to_num(a)) / np.maximum(speed_a * speed_b, 1e-9)
    ratio = np.minimum(speed_a, speed_b) / np.maximum(np.maximum(speed_a, speed_b), 1e-9)
    moving = np.clip((1 + cosine) / 2, 0, 1) ** 2 * np.sqrt(ratio)
    score = np.where(both_still, 0.65, np.where(one_still, 0.25, moving))
    return np.where(known, score, 0.5), known


def motion(reference: Moments, candidates: Moments, zoom: np.ndarray, reference_zoom: float = 1.0) -> dict[str, np.ndarray]:
    """Camera movement and subject travel continuing across the cut (output-frame speeds)."""
    camera_a = reference.camera[0] * reference_zoom
    camera_b = candidates.camera * zoom[:, None]
    pan, pan_known = _continuity(camera_a[:2], camera_b[:, :2], STILL)
    zoom_a, zoom_b = camera_a[2], camera_b[:, 2]
    push_known = np.isfinite(zoom_b) & bool(np.isfinite(zoom_a))
    pushing_a, pushing_b = np.abs(zoom_a) >= 0.03, np.abs(np.nan_to_num(zoom_b)) >= 0.03
    same_way = np.sign(np.nan_to_num(zoom_b)) == np.sign(np.nan_to_num(zoom_a))
    push = np.where(~pushing_a & ~pushing_b, 0.6, np.where(pushing_a & pushing_b, np.where(same_way, 0.9, 0.15), 0.4))
    push = np.where(push_known, push, 0.5)
    travel, travel_known = _continuity(reference.velocity[0] * reference_zoom, candidates.velocity * zoom[:, None], 0.04)
    camera_score = 0.75 * pan + 0.25 * push
    weight_travel = np.where(travel_known, 0.4, 0.0)
    combined = (1 - weight_travel) * camera_score + weight_travel * travel
    return {"motion": combined, "pan": pan, "push": push, "travel": travel,
            "motion_known": (pan_known | travel_known).astype(np.float32)}


def subject(reference: Moments, reference_group: np.ndarray, reference_crop_: np.ndarray, candidates: Moments,
            group: np.ndarray, crops: np.ndarray, grid: tuple[int, int]) -> np.ndarray:
    """Instance IoU after greedy assignment of the prominent groups, in output coordinates."""
    n = len(candidates)
    ref_ids = reference_group[reference_group >= 0]
    cand_ids = group[group >= 0]
    if len(ref_ids) == 0 or len(cand_ids) == 0:
        return np.zeros(n)
    flat_ref = rasterize(reference, ref_ids, reference_crop_[None], grid).reshape(len(ref_ids), -1)       # A x G
    flat_cand = rasterize(candidates, cand_ids, crops, grid).reshape(len(cand_ids), -1)                    # K x G
    compatible = _COMPATIBLE[_FAMILY[candidates.classes[cand_ids]][:, None], _FAMILY[reference.classes[ref_ids]][None, :]]
    block_all = np.minimum(flat_cand[:, None, :], flat_ref[None, :, :]).sum(axis=-1) * compatible             # K x A soft intersections
    owner = np.searchsorted(candidates.inst_ptr, cand_ids, side="right") - 1
    union_ref = np.clip(flat_ref.sum(axis=0), 0, 1)
    # Union of each candidate's group with the reference group.
    union_cand = np.zeros((n, flat_cand.shape[1]), dtype=np.float32)
    np.maximum.at(union_cand, owner, flat_cand)
    union = np.maximum(union_cand, union_ref[None]).sum(axis=1)
    matched = np.zeros(n)
    starts = np.searchsorted(owner, np.arange(n), side="left")
    ends = np.searchsorted(owner, np.arange(n), side="right")
    for candidate in np.flatnonzero(ends > starts):
        block = block_all[starts[candidate]:ends[candidate]]
        if block.shape[0] == 1 or block.shape[1] == 1:
            matched[candidate] = block.max()
            continue
        used_a, used_b, total = set(), set(), 0.0
        for flat in np.argsort(-block, axis=None):
            b_index, a_index = divmod(int(flat), block.shape[1])
            if b_index in used_b or a_index in used_a:
                continue
            total += block[b_index, a_index]
            used_a.add(a_index)
            used_b.add(b_index)
        matched[candidate] = total
    return np.clip(np.where(union > 0, matched / np.maximum(union, 1e-9), 0.0), 0.0, 1.0)


def eyes(reference_point: np.ndarray | None, candidates: Moments, main: np.ndarray, crops: np.ndarray,
         reference_is_person: bool) -> np.ndarray:
    """Eye trace: 1 when the incoming eye point lands on the outgoing one, fading over a tenth of the frame.

    People's eyes only answer people's eyes; other subjects compare centres.
    """
    out = np.zeros(len(candidates))
    if reference_point is None:
        return out
    points, exists = eye_points(candidates, main)
    person = np.zeros(len(candidates), dtype=bool)
    has = main >= 0
    person[has] = candidates.classes[main[has]] == PERSON
    exists &= person == reference_is_person
    if not exists.any():
        return out
    seen = point_to_output(points[exists], crops[exists])
    distance = np.hypot(seen[:, 0] - reference_point[0], seen[:, 1] - reference_point[1])
    out[exists] = np.exp(-(distance / 0.1) ** 2)
    return out


def _pose_output(xy: np.ndarray, confidence: np.ndarray, crops: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Keypoints seen through crops (P x 17 x 2 output fractions) and which count as shown inside the frame."""
    out = np.stack([(xy[..., 0] - crops[:, None, 0]) / crops[:, None, 2],
                    (xy[..., 1] - crops[:, None, 1]) / crops[:, None, 3]], axis=-1)
    inside = (out >= 0).all(axis=-1) & (out <= 1).all(axis=-1)
    return out, (confidence >= SEEN) & inside


def _prominent(xy: np.ndarray, seen: np.ndarray, owner: np.ndarray, share: float = 0.25) -> np.ndarray:
    """People shown at least ``share`` as large (keypoint extent) as the largest person of their frame."""
    low = np.where(seen[..., None], xy, np.inf).min(axis=1)
    high = np.where(seen[..., None], xy, -np.inf).max(axis=1)
    size = np.where(seen.sum(axis=1) >= 2, np.nan_to_num((high - low).max(axis=1), posinf=0.0, neginf=0.0), 0.0)
    largest = np.zeros(int(owner.max()) + 1 if len(owner) else 0)
    np.maximum.at(largest, owner, size)
    return (size > 0) & (size >= share * largest[owner])


def pose(reference: Moments, reference_crop_: np.ndarray, candidates: Moments, crops: np.ndarray) -> np.ndarray:
    """People's poses agree: object keypoint similarity over body parts shown in either frame.

    Greedy assignment of the prominent people of each frame (shown at least
    a quarter as large as its largest person, so extras in the background do
    not count); people left unmatched in either frame count as zero, so a
    group only fully matches a group.
    """
    n = len(candidates)
    out = np.zeros(n)
    if reference.pose_ptr is None or candidates.pose_ptr is None:
        return out
    ref_count = int(reference.pose_ptr[1] - reference.pose_ptr[0])
    if ref_count == 0 or len(candidates.pose_xy) == 0:
        return out
    ref_xy, ref_seen = _pose_output(reference.pose_xy[:ref_count].astype(np.float64),
                                    reference.pose_conf[:ref_count], np.repeat(reference_crop_[None], ref_count, axis=0))
    ref_keep = _prominent(ref_xy, ref_seen, np.zeros(ref_count, dtype=np.int64))
    ref_xy, ref_seen, ref_count = ref_xy[ref_keep], ref_seen[ref_keep], int(ref_keep.sum())
    owner = np.repeat(np.arange(n), np.diff(candidates.pose_ptr))
    cand_xy, cand_seen = _pose_output(candidates.pose_xy.astype(np.float64), candidates.pose_conf, crops[owner])
    keep = _prominent(cand_xy, cand_seen, owner)
    cand_xy, cand_seen, owner = cand_xy[keep], cand_seen[keep], owner[keep]
    if ref_count == 0 or not len(owner):
        return out
    # Scale: the reference person's shown extent (at least a twentieth of the frame).
    extent = np.array([np.ptp(ref_xy[r][ref_seen[r]], axis=0).max() if ref_seen[r].sum() >= 2 else 0.05
                       for r in range(ref_count)])
    scale = np.maximum(extent, 0.05)                                                          # R
    distance2 = ((cand_xy[:, None] - ref_xy[None]) ** 2).sum(axis=-1)                         # K x R x 17
    kappa2 = (scale[None, :, None] * KEYPOINT_KAPPA[None, None, :]) ** 2 * 2
    similarity = np.exp(-distance2 / np.maximum(kappa2, 1e-9))
    both = cand_seen[:, None] & ref_seen[None]
    either = cand_seen[:, None] | ref_seen[None]
    oks = (similarity * both).sum(axis=-1) / np.maximum(either.sum(axis=-1), 1)             # K x R
    starts = np.searchsorted(owner, np.arange(n), side="left")
    ends = np.searchsorted(owner, np.arange(n), side="right")
    for candidate in np.flatnonzero(ends > starts):
        block = oks[starts[candidate]:ends[candidate]]
        used_a, used_b, total = set(), set(), 0.0
        for flat in np.argsort(-block, axis=None):
            b_index, a_index = divmod(int(flat), block.shape[1])
            if b_index in used_b or a_index in used_a:
                continue
            total += block[b_index, a_index]
            used_a.add(a_index)
            used_b.add(b_index)
        out[candidate] = total / max(block.shape[0], block.shape[1])
    return out


def shape(reference: Moments, reference_main: int, candidates: Moments, main: np.ndarray) -> np.ndarray:
    """Main silhouettes compared inside their own boxes (position free), times aspect agreement."""
    n = len(candidates)
    out = np.zeros(n)
    has = main >= 0
    if reference_main < 0 or not has.any():
        return out
    a = reference.masks[reference_main].astype(np.float32).reshape(-1)
    b = candidates.masks[main[has]].astype(np.float32).reshape(has.sum(), -1)
    intersection = b @ a
    union = a.sum() + b.sum(axis=1) - intersection
    iou = np.where(union > 0, intersection / np.maximum(union, 1e-9), 0.0)
    box_a = reference.boxes[reference_main]
    boxes_b = candidates.boxes[main[has]]
    aspect_a = (box_a[2] - box_a[0]) * reference.aspect[0] / max(box_a[3] - box_a[1], 1e-6)
    aspect_b = (boxes_b[:, 2] - boxes_b[:, 0]) * candidates.aspect[has] / np.maximum(boxes_b[:, 3] - boxes_b[:, 1], 1e-6)
    agreement = np.minimum(aspect_a, aspect_b) / np.maximum(np.maximum(aspect_a, aspect_b), 1e-9)
    # A silhouette that fills its box says little: solid blocks all look alike.
    fill = np.minimum(a.mean(), b.mean(axis=1))
    informative = np.clip((0.97 - fill) / 0.25, 0.0, 1.0)
    out[has] = iou * np.sqrt(agreement) * (0.5 + 0.5 * informative)
    return out


# ---------------------------------------------------------------------------
# Whole pair
# ---------------------------------------------------------------------------


def weights_for(focus: str, reference_has_subject: bool, override: dict[str, float] | None = None) -> dict[str, float]:
    if override:
        return dict(override)
    if focus == "auto":
        return dict(FOCUS_WEIGHTS["auto_subject" if reference_has_subject else "auto_picture"])
    if focus not in FOCUS_WEIGHTS:
        raise ValueError(f"Unknown match focus {focus!r}")
    return dict(FOCUS_WEIGHTS[focus])


def calibrate(raw: np.ndarray, quantiles: list[float]) -> np.ndarray:
    """Raw component -> common scale: 0 at the random-pair median, 0.4/0.8/1.0 at p90/p99/p99.9."""
    points = np.asarray(quantiles, dtype=np.float64).copy()
    for position in range(1, len(points)):                  # keep the knots strictly increasing
        points[position] = max(points[position], points[position - 1] + 1e-6)
    return np.interp(raw, points, [0.0, 0.4, 0.8, 1.0])


def score(reference: Moments, candidates: Moments, options: Options) -> Scored:
    """Score N incoming instants against one outgoing instant (``reference`` has length 1)."""
    grid = grid_for(options.output)
    coarse_grid = (grid[0] // 2, grid[1] // 2)
    ref_group, ref_main = salient(reference)
    ref_main_index = int(ref_main[0])
    out_crop = reference_crop(reference, ref_main_index, options)
    reference_point = reference_size = None
    has_subject = False
    if ref_main_index >= 0:
        box = to_output(reference.boxes[ref_main_index], out_crop)
        has_subject = box_area(np.clip(box, 0, 1)) >= 0.01
        ref_points, ref_exists = eye_points(reference, np.array([ref_main_index]))
        reference_anchor = point_to_output(anchors(reference, np.array([ref_main_index]))[0], out_crop)
        reference_eye = point_to_output(ref_points[0], out_crop) if ref_exists[0] else None
        reference_point = reference_eye if reference_eye is not None else reference_anchor
        height, width, cut = _sizes(reference.boxes[ref_main_index], reference.aspect[0])
        reference_size = (float(height) / float(out_crop[3]), float(width) / float(out_crop[3]), bool(cut))
    group, main = salient(candidates)
    crops, zoom = solve_crops(reference_point if has_subject else None, reference_size, candidates, main, options)
    reference_zoom = 1.0 / float(out_crop[3]) if options.output == LANDSCAPE else 1.0
    weights = dict(weights_for(options.focus, has_subject, options.weights))
    calibration = {**DEFAULT_CALIBRATION, **(options.calibration or {})}
    n = len(candidates)

    ref_crop = out_crop[None]
    parts: dict[str, np.ndarray] = {}
    gray_a = sample(reference.gray, ref_crop, grid)
    parts["light"] = light(gray_a, sample(candidates.gray, crops, grid))
    parts["lines"] = lines(sample(reference.field, ref_crop, coarse_grid), sample(candidates.field, crops, coarse_grid))
    color_grid = (5, 8) if options.output == LANDSCAPE else (max(4, grid[0] // 4), max(4, grid[1] // 4))
    parts["color"] = color(sample(reference.color.transpose(0, 3, 1, 2), ref_crop, color_grid),
                           sample(candidates.color.transpose(0, 3, 1, 2), crops, color_grid))
    parts["tone"] = tone(float(reference.brightness[0]), candidates.brightness)
    parts.update(motion(reference, candidates, zoom, reference_zoom))
    if has_subject:
        parts["subject"] = subject(reference, ref_group[0], out_crop, candidates, group, crops, coarse_grid)
        reference_is_person = bool(reference.classes[ref_main_index] == PERSON)
        has_eye = reference_eye is not None
        parts["eyes"] = eyes(reference_point, candidates, main, crops, reference_is_person) if has_eye else np.zeros(n)
        parts["shape"] = shape(reference, ref_main_index, candidates, main)
        parts["pose"] = pose(reference, out_crop, candidates, crops)
        if not has_eye:                     # a head out of frame gives no eye line to follow
            weights["subject"] = weights.get("subject", 0.0) + weights.pop("eyes", 0.0)
        if reference.main_pose()[0] < 0:    # no person pose: its weight goes to the silhouettes
            weights["subject"] = weights.get("subject", 0.0) + weights.pop("pose", 0.0)
    else:
        parts["subject"] = parts["eyes"] = parts["shape"] = parts["pose"] = np.zeros(n)
    # A flat picture (sky, wall, darkness) has no light layout to match: its
    # correlation is noise, so the light and line parts give their weight to
    # the subject (or, without one, to movement).
    contrast = float(np.nanstd(_blur(gray_a)))
    structure = float(np.clip((contrast - 0.03) / 0.09, 0.0, 1.0))
    weights = dict(weights)
    receiver = "subject" if has_subject else "motion"
    for name in ("light", "lines"):
        freed = weights.get(name, 0.0) * (1 - structure)
        weights[name] = weights.get(name, 0.0) * structure
        weights[receiver] = weights.get(receiver, 0.0) + freed
    # A subject that fills the frame overlaps anything large: its overlap says
    # little, so most of that weight moves to the pose, eyes and light.
    if has_subject:
        coverage = float(rasterize(reference, ref_group[0][ref_group[0] >= 0], out_crop[None], coarse_grid).max(axis=0).mean())
        filled = float(np.clip((coverage - 0.55) / 0.35, 0.0, 0.8))
        moved = weights.get("subject", 0.0) * filled
        weights["subject"] = weights.get("subject", 0.0) - moved
        others = [name for name in ("pose", "eyes", "light") if weights.get(name, 0.0) > 0]
        for name in others:
            weights[name] += moved / len(others)
        parts["filled"] = np.full(n, filled)
    calibrated = {name: calibrate(parts[name], calibration[name]) for name in REWARDS}
    parts["zoom_cost"] = np.clip((zoom - 1.0) * 0.12, 0.0, 0.2)
    parts["structure"] = np.full(n, structure)
    total = sum(weights.get(name, 0.0) * calibrated[name] for name in REWARDS)
    total = total - TONE_PENALTY * (1.0 - parts["tone"]) - parts["zoom_cost"]
    return Scored(total=np.asarray(total, dtype=np.float64), parts=parts, calibrated=calibrated, crop=crops, zoom=zoom,
                  outgoing_crop=tuple(float(v) for v in out_crop), weights=weights)


# ---------------------------------------------------------------------------
# Explanations
# ---------------------------------------------------------------------------


def _direction(vx: float, vy: float) -> str:
    """Screen direction of image motion (the scene moves this way on screen)."""
    if abs(vx) >= abs(vy):
        return "right" if vx > 0 else "left"
    return "down" if vy > 0 else "up"


def reasons(calibrated: dict[str, float], parts: dict[str, float], reference: Moments, candidate: Moments,
            zoom: float) -> list[dict[str, Any]]:
    """Short, measured reasons for one pair, strongest first (only clearly better than chance)."""
    out: list[dict[str, Any]] = []
    labels = {"subject": "Same place and size", "eyes": "Eye line carries over", "pose": "Same pose",
              "shape": "Matching silhouette",
              "light": "Light falls alike", "lines": "Lines up", "color": "Colour carries over"}
    for code, label in labels.items():
        if calibrated.get(code, 0) >= 0.5:
            out.append({"code": code, "label": label, "strength": calibrated[code]})
    camera_a, camera_b = reference.camera[0], candidate.camera[0] * zoom
    if calibrated.get("motion", 0) >= 0.5 and np.isfinite(camera_a).all() and np.isfinite(camera_b).all():
        speed_a, speed_b = math.hypot(camera_a[0], camera_a[1]), math.hypot(camera_b[0], camera_b[1])
        if speed_a >= STILL and speed_b >= STILL and parts.get("pan", 0) >= 0.6:
            out.append({"code": "motion", "label": f"Movement continues {_direction(float(camera_b[0]), float(camera_b[1]))}",
                        "strength": calibrated["motion"]})
    if parts.get("travel", 0) >= 0.6 and np.isfinite(reference.velocity[0]).all() and np.isfinite(candidate.velocity[0]).all():
        velocity = candidate.velocity[0] * zoom
        if math.hypot(*velocity) >= 0.04:
            out.append({"code": "travel", "label": f"Subject keeps moving {_direction(float(velocity[0]), float(velocity[1]))}",
                        "strength": parts["travel"]})
    if parts.get("push", 0) >= 0.9 and np.isfinite(camera_a[2]):
        out.append({"code": "push", "label": "Push carries through" if camera_a[2] > 0 else "Pull carries through",
                    "strength": parts["push"]})
    if zoom > 1.02:
        out.append({"code": "reframe", "label": f"Reframed {zoom:.2f}x", "strength": 0.0})
    return sorted(out, key=lambda item: -item["strength"])
