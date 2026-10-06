"""Find and follow TV screens in film frames, for ``screen`` effects (ADR-0108).

Editor tooling, not part of the renderer: a document stores the corners these
functions return, and the render only interpolates them.

A screen is the lit part of a segmented TV. Its four corners come from lines
fitted to each edge of that region and intersected, so the rounded corners of
a CRT do not pull them inward. Detection is a starting point: a TV's picture
can be dark in places and people can cover it, so an editor may give the
corners instead.

Following a screen: ``follow_corners`` moves given corners with the set,
matching patches on the casing frame to frame and fitting scale, turn and
shift; ``follow_screen`` detects the screen again on every frame, holding
through misses and jumps, and smooths the path.
"""

from __future__ import annotations

from typing import Callable

import numpy as np
from scipy import ndimage

Corners = np.ndarray            # 4 x 2 pixels: top-left, top-right, bottom-right, bottom-left

LIT_PERCENTILE = 45             # the screen is the part of the set brighter than this share of it
JUMP = 0.06                     # a corner moving further than this share of the frame width in one frame is a misdetection


def _edge_line(points: np.ndarray) -> tuple[np.ndarray, np.ndarray] | None:
    """(point, direction) of the total-least-squares line through ``points``."""
    if len(points) < 4:
        return None
    centre = points.mean(axis=0)
    _, _, axes = np.linalg.svd(points - centre)
    return centre, axes[0]


def _cross(first: tuple[np.ndarray, np.ndarray], second: tuple[np.ndarray, np.ndarray]) -> np.ndarray | None:
    (p, d), (q, e) = first, second
    matrix = np.array([d, -e]).T
    if abs(np.linalg.det(matrix)) < 1e-6:
        return None
    s, _ = np.linalg.solve(matrix, q - p)
    return p + s * d


def screen_corners(frame: np.ndarray, tv: np.ndarray, near: Corners | None = None) -> Corners | None:
    """The corners of the lit screen of the TV in ``tv`` (a mask), or of the TV nearest ``near``."""
    labels, count = ndimage.label(tv > 127)
    if count == 0:
        return None
    sizes = ndimage.sum(np.ones(tv.shape), labels, range(1, count + 1))
    if near is not None:
        centres = ndimage.center_of_mass(np.ones(tv.shape), labels, range(1, count + 1))
        target = near.mean(axis=0)[::-1]
        pick = int(np.argmin([np.hypot(*(np.array(c) - target)) for c in centres]))
    else:
        pick = int(np.argmax(sizes))
    if sizes[pick] < 0.002 * tv.size:
        return None
    set_mask = labels == pick + 1
    luma = frame.astype(np.float32) @ np.array([0.299, 0.587, 0.114], np.float32)
    lit = set_mask & (luma > np.percentile(luma[set_mask], LIT_PERCENTILE))
    lit = ndimage.binary_opening(lit, iterations=2)
    parts, found = ndimage.label(lit)
    if found == 0:
        return None
    biggest = int(np.argmax(ndimage.sum(lit, parts, range(1, found + 1)))) + 1
    screen = ndimage.binary_fill_holes(parts == biggest)
    edge = screen & ~ndimage.binary_erosion(screen)
    ys, xs = np.nonzero(edge)
    if len(xs) < 20:
        return None
    points = np.stack([xs, ys], axis=1).astype(float)
    rough = np.array([points[np.argmin(xs + ys)], points[np.argmax(xs - ys)],
                      points[np.argmax(xs + ys)], points[np.argmin(xs - ys)]])
    lines = []
    for k in range(4):
        a, b = rough[k], rough[(k + 1) % 4]
        direction = b - a
        length = np.linalg.norm(direction)
        if length < 4:
            return None
        direction /= length
        along = (points - a) @ direction
        away = np.abs((points - a) @ np.array([-direction[1], direction[0]]))
        chosen = points[(along > 0.2 * length) & (along < 0.8 * length) & (away < 0.06 * length + 2)]
        line = _edge_line(chosen)
        if line is None:
            return None
        lines.append(line)
    corners = [_cross(lines[(k + 3) % 4], lines[k]) for k in range(4)]
    if any(c is None for c in corners):
        return None
    result = np.array(corners)
    height, width = tv.shape
    if np.any(result < [-0.5 * width, -0.5 * height]) or np.any(result > [1.5 * width, 1.5 * height]):
        return None
    return result


def _grey(frame: np.ndarray) -> np.ndarray:
    return frame.astype(np.float32) @ np.array([0.299, 0.587, 0.114], np.float32) if frame.ndim == 3 else frame.astype(np.float32)


def _match(previous: np.ndarray, current: np.ndarray, point: np.ndarray, half: int, search: int) -> tuple[np.ndarray, float]:
    """Where the patch around ``point`` in ``previous`` sits in ``current`` (normalised correlation), and how well it matched."""
    x, y = int(round(point[0])), int(round(point[1]))
    height, width = previous.shape
    if not (half + search <= x < width - half - search and half + search <= y < height - half - search):
        return point, -1.0
    template = previous[y - half:y + half + 1, x - half:x + half + 1]
    template = template - template.mean()
    norm = np.sqrt((template ** 2).sum())
    if norm < 1e-3 * template.size:
        return point, -1.0                           # a flat patch can't be followed
    area = current[y - half - search:y + half + search + 1, x - half - search:x + half + search + 1]
    windows = np.lib.stride_tricks.sliding_window_view(area, template.shape)
    centred = windows - windows.mean(axis=(2, 3), keepdims=True)
    scores = (centred * template).sum(axis=(2, 3)) / (np.sqrt((centred ** 2).sum(axis=(2, 3))) * norm + 1e-6)
    dy, dx = np.unravel_index(int(np.argmax(scores)), scores.shape)
    shift = np.array([dx - search, dy - search], float)
    for axis, line, k in ((0, scores[dy, :], dx), (1, scores[:, dx], dy)):     # sub-pixel: a parabola through the peak
        if 0 < k < len(line) - 1:
            a, b, c = line[k - 1], line[k], line[k + 1]
            if a - 2 * b + c < 0:
                shift[axis] += 0.5 * (a - c) / (a - 2 * b + c)
    return point + shift, float(scores.max())


def _similarity(points: np.ndarray, targets: np.ndarray) -> np.ndarray:
    """Least-squares scale, turn and shift (2 x 3) taking ``points`` onto ``targets``."""
    p, q = points.mean(axis=0), targets.mean(axis=0)
    a, b = points - p, targets - q
    u, s, vt = np.linalg.svd(b.T @ a)
    turn = u @ np.diag([1, np.sign(np.linalg.det(u @ vt))]) @ vt
    scale = s.sum() / max((a ** 2).sum(), 1e-9)
    return np.hstack([scale * turn, (q - scale * turn @ p)[:, None]])


def follow_corners(frames: list[np.ndarray], corners: Corners, count: int = 20, ring: float = 0.1,
                   half: int = 12, search: int = 10) -> list[Corners]:
    """Corners given on the first frame, moved with the set on the frames after it.

    Patches on the set just outside the screen (the screen's own picture moves) are matched frame to frame, and
    the corners follow the scale, turn and shift that best explains them, ignoring patches a hand or head covers.
    """
    centre = corners.mean(axis=0)
    outline = centre + (corners - centre) * (1 + ring)
    seeds = np.concatenate([outline[k] + (outline[(k + 1) % 4] - outline[k]) * np.linspace(0, 1, count // 4, endpoint=False)[:, None]
                            for k in range(4)])
    points = seeds.copy()
    previous = _grey(frames[0])
    result = [corners.copy()]
    for frame in frames[1:]:
        current = _grey(frame)
        moved, quality = zip(*(_match(previous, current, point, half, search) for point in points))
        moved, quality = np.array(moved), np.array(quality)
        good = quality > 0.6
        model = None
        for _ in range(3):
            if good.sum() < 3:
                break
            model = _similarity(seeds[good], moved[good])
            residual = np.linalg.norm(seeds @ model[:, :2].T + model[:, 2] - moved, axis=1)
            good = good & (residual < max(2.5, np.median(residual[good]) * 3))
        if model is None:
            result.append(result[-1].copy())                         # nothing to follow: hold
            continue
        predicted = seeds @ model[:, :2].T + model[:, 2]
        points = np.where(good[:, None], moved, predicted)           # lost patches restart where the set says they are
        result.append(corners @ model[:, :2].T + model[:, 2])
        previous = current
    return result


def follow_screen(frames: list[np.ndarray], tv_mask: Callable[[np.ndarray], np.ndarray],
                  first: Corners | None = None, window: int = 5) -> list[Corners]:
    """Corners on every frame: detected near the last good corners, held through misses and jumps, then smoothed."""
    raw: list[Corners | None] = []
    last = first
    for frame in frames:
        found = screen_corners(frame, tv_mask(frame), last)
        width = frame.shape[1]
        if found is not None and last is not None and np.max(np.abs(found - last)) > JUMP * width:
            found = None
        raw.append(found)
        if found is not None:
            last = found
    if all(c is None for c in raw):
        if first is None:
            raise ValueError("No screen found in these frames")
        return [first.copy() for _ in frames]
    known = [i for i, c in enumerate(raw) if c is not None]
    track = np.array([raw[i] for i in known])                     # n x 4 x 2
    filled = np.stack([np.stack([np.interp(range(len(frames)), known, track[:, k, axis]) for axis in (0, 1)], axis=1)
                       for k in range(4)], axis=1)                # frames x 4 x 2
    if window > 1 and len(frames) >= window:
        filled = ndimage.median_filter(filled, size=(window, 1, 1), mode="nearest")
        filled = ndimage.uniform_filter1d(filled, size=3, axis=0, mode="nearest")
    return [filled[i] for i in range(len(frames))]
