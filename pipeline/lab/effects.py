"""Feature-locked effects (ADR-0106): a compositing pass over a rendered cut sequence.

The cut renderer writes one picture per output frame. When a document carries
effects, this pass decodes that sequence and composites, frame by frame:

- ``overlay``: another source window over the picture. With ``align`` it is
  moved, scaled and turned so its feature lands on the picture's feature, for
  example eye on eye, tracked through the span;
- ``lock_cut``: a feature-locked dissolve across one cut. The incoming shot
  fades in over the outgoing one with its feature pinned to the outgoing
  feature, and after the cut it eases back to its own framing while the
  outgoing shot fades out on top of it;
- ``zoom_through``: a push into the outgoing feature that lands, after the cut,
  in the incoming feature and pulls back out;
- ``punch``: a sudden zoom around the feature that decays;
- ``flash``: an exposure flash;
- ``echo``: a decaying trail of earlier frames.

Features come from the library moment index (ADR-0099): a person's two eyes
(COCO keypoints) when both are shown, else the eye-trace point of the main
subject with its box height as scale. Instants sit on a 4 fps grid; features
are interpolated between them. Without an index, or for an unindexed shot,
alignment falls back to the picture centre at unit scale.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math
from pathlib import Path
import subprocess
from typing import Any, Callable, Iterable

import numpy as np

EFFECTS_PROFILE = "feature-locked-effects-v1"
FEATURE_KINDS = ("overlay", "lock_cut", "zoom_through", "punch")
SCALE_LIMITS = (0.6, 3.0)       # below 0.6 an overlay reads as a pasted thumbnail
MAX_TURN = math.radians(15)
FEATHER = 0.08              # overlay edges fade over this share of the picture's shorter side
COVER_LIMIT = 2.0          # extra zoom allowed to keep a moved picture covering its own frame
LUMA = np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)


# -- geometry -----------------------------------------------------------------

def fit_box(display_aspect: float, crop: dict | None, width: int, height: int) -> tuple[float, float, float, float]:
    """Where the renderer places a (cropped) source picture: (left, top, width, height) in output pixels."""
    crop = crop or {"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0}
    aspect = display_aspect * crop["width"] / crop["height"]
    if aspect >= width / height:
        w, h = float(width), width / aspect
    else:
        w, h = height * aspect, float(height)
    return (width - w) / 2, (height - h) / 2, w, h


def source_to_output(point: Iterable[float], display_aspect: float, crop: dict | None, width: int, height: int) -> np.ndarray:
    """A point in whole-source fractions, seen through the clip's crop and fit, in output pixels."""
    crop = crop or {"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0}
    left, top, w, h = fit_box(display_aspect, crop, width, height)
    x, y = point
    return np.array([left + (x - crop["x"]) / crop["width"] * w, top + (y - crop["y"]) / crop["height"] * h])


def content_to_source(point: Iterable[float], content_box: list[float] | None) -> np.ndarray:
    x0, y0, x1, y1 = content_box or (0.0, 0.0, 1.0, 1.0)
    x, y = point
    return np.array([x0 + x * (x1 - x0), y0 + y * (y1 - y0)])


@dataclass
class Feature:
    """A feature in output pixels: one or two points and a size (eye distance or head size)."""

    points: np.ndarray              # 1 x 2 or 2 x 2
    size: float

    def centre(self) -> np.ndarray:
        return self.points.mean(axis=0)


def similarity(moving: Feature | None, fixed: Feature | None, centre: tuple[float, float]) -> np.ndarray:
    """2 x 3 matrix taking the moving picture so its feature lands on the fixed feature.

    Two points on both sides fix scale and turn (the turn is dropped beyond
    15 degrees: a larger tilt reads as a tilted card, and a half turn means
    the faces are mirrored). Otherwise
    the centres meet and the scale is the size ratio. Without both features the
    identity is returned.
    """
    if moving is None or fixed is None:
        return np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
    turn = 0.0
    if len(moving.points) == 2 and len(fixed.points) == 2:
        a, b = moving.points[1] - moving.points[0], fixed.points[1] - fixed.points[0]
        scale = float(np.hypot(*b) / max(np.hypot(*a), 1e-6))
        turn = math.atan2(b[1], b[0]) - math.atan2(a[1], a[0])
        turn = (turn + math.pi) % (2 * math.pi) - math.pi
        if abs(turn) > MAX_TURN:
            turn = 0.0
    else:
        scale = fixed.size / max(moving.size, 1e-6)
    scale = float(np.clip(scale, *SCALE_LIMITS))
    rotation = np.array([[math.cos(turn), -math.sin(turn)], [math.sin(turn), math.cos(turn)]]) * scale
    shift = fixed.centre() - rotation @ moving.centre()
    return np.hstack([rotation, shift[:, None]])


def scale_about(point: np.ndarray, scale: float, target: np.ndarray | None = None) -> np.ndarray:
    """2 x 3 matrix scaling about ``point`` and moving it to ``target`` (default: stays put)."""
    target = point if target is None else target
    return np.array([[scale, 0.0, target[0] - scale * point[0]], [0.0, scale, target[1] - scale * point[1]]])


def cover(matrix: np.ndarray, box: tuple[float, float, float, float], anchor: np.ndarray,
          limit: float = COVER_LIMIT) -> np.ndarray:
    """Keep a moved picture filling its own box (no new black corners).

    First zoom about ``anchor`` (the feature, so it stays put) up to ``limit``.
    When even that leaves a corner uncovered, the move itself is weakened
    toward the picture's own framing until a zoom within the limit fills it.
    """
    left, top, w, h = box
    corners = np.array([[left, top], [left + w, top], [left, top + h], [left + w, top + h]])

    def fills(m):
        inverse = np.linalg.inv(np.vstack([m, [0.0, 0.0, 1.0]]))[:2]
        back = corners @ inverse[:, :2].T + inverse[:, 2]
        return bool(np.all((back[:, 0] >= left - 0.5) & (back[:, 0] <= left + w + 0.5) &
                           (back[:, 1] >= top - 0.5) & (back[:, 1] <= top + h + 0.5)))

    def zoomed(m, point):
        if fills(m):
            return m
        if not fills(compose(scale_about(point, limit), m)):
            return None
        low, high = 1.0, limit
        for _ in range(24):
            middle = (low + high) / 2
            low, high = (low, middle) if fills(compose(scale_about(point, middle), m)) else (middle, high)
        return compose(scale_about(point, high), m)

    identity = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
    inverse = np.linalg.inv(np.vstack([matrix, [0.0, 0.0, 1.0]]))[:2]
    source_anchor = inverse[:, :2] @ anchor + inverse[:, 2]           # the feature in the unmoved picture
    for strength in (1.0, 0.8, 0.6, 0.4, 0.2, 0.0):
        weaker = blend_matrices(identity, matrix, strength) if strength < 1 else matrix
        point = weaker[:, :2] @ source_anchor + weaker[:, 2]
        result = zoomed(weaker, point)
        if result is not None:
            return result
    return identity


def blend_matrices(a: np.ndarray, b: np.ndarray, weight: float) -> np.ndarray:
    """Interpolate two similarity matrices through their scale, turn and shift."""
    def parts(m):
        scale = math.hypot(m[0, 0], m[1, 0])
        return scale, math.atan2(m[1, 0], m[0, 0]), m[:, 2]
    sa, ta, pa = parts(a)
    sb, tb, pb = parts(b)
    scale = math.exp(math.log(sa) * (1 - weight) + math.log(sb) * weight)
    delta = (tb - ta + math.pi) % (2 * math.pi) - math.pi
    turn = ta + delta * weight
    shift = pa * (1 - weight) + pb * weight
    return np.array([[scale * math.cos(turn), -scale * math.sin(turn), shift[0]],
                     [scale * math.sin(turn), scale * math.cos(turn), shift[1]]])


def compose(outer: np.ndarray, inner: np.ndarray) -> np.ndarray:
    """outer after inner, both 2 x 3."""
    full = lambda m: np.vstack([m, [0.0, 0.0, 1.0]])
    return (full(outer) @ full(inner))[:2]


def ease(value: float, kind: str = "smooth") -> float:
    value = min(max(value, 0.0), 1.0)
    if kind == "in":
        return value ** 3
    if kind == "out":
        return 1 - (1 - value) ** 3
    return value * value * (3 - 2 * value)


def envelope(t: float, start: float, end: float, attack: float, release: float) -> float:
    """1 inside the span, ramping over ``attack`` after the start and ``release`` before the end."""
    if t < start or t >= end:
        return 0.0
    level = 1.0
    if attack > 0:
        level = min(level, ease((t - start) / attack))
    if release > 0:
        level = min(level, ease((end - t) / release))
    return level


# -- pixels --------------------------------------------------------------------

def warp(picture: np.ndarray, matrix: np.ndarray, width: int, height: int, order: int = 3) -> np.ndarray:
    """Resample ``picture`` (H x W x C uint8 or float) through ``matrix`` (picture -> output pixels)."""
    from PIL import Image
    full = np.vstack([matrix, [0.0, 0.0, 1.0]])
    inverse = np.linalg.inv(full)[:2].ravel()
    resample = Image.BICUBIC if order == 3 else Image.BILINEAR
    if picture.dtype != np.uint8:
        picture = np.clip(picture * 255 + 0.5, 0, 255).astype(np.uint8)
    mode = "L" if picture.ndim == 2 else "RGB"
    image = Image.fromarray(picture, mode).transform((width, height), Image.AFFINE, tuple(inverse), resample=resample)
    return np.asarray(image)


def blend(base: np.ndarray, top: np.ndarray, alpha: np.ndarray, mode: str) -> np.ndarray:
    """Composite float pictures in [0, 1]; ``alpha`` is H x W x 1."""
    if mode == "screen":
        mixed = 1 - (1 - base) * (1 - top)
    elif mode == "lighten":
        mixed = np.maximum(base, top)
    elif mode == "multiply":
        mixed = base * top
    elif mode == "difference":
        mixed = np.abs(base - top)
    elif mode == "luma":
        # Double exposure: the top picture shows through the dark parts of the base.
        alpha = alpha * (1 - (base @ LUMA)[..., None]) ** 1.5
        mixed = top
    else:
        mixed = top
    return base + (mixed - base) * alpha


# -- features ------------------------------------------------------------------

class FeatureSource:
    """Eye pairs and subject anchors from the moment index, interpolated between grid instants."""

    def __init__(self, index: Any | None):
        self.index = index
        self._films: dict[str, dict] = {}
        self._row_features: dict[int, tuple[np.ndarray, float] | None] = {}

    def _unit(self, film_id: str, unit_id: str | None, time: float) -> int | None:
        index = self.index
        if index is None:
            return None
        if unit_id:
            try:
                unit = index.unit_index(unit_id)
                if index.unit_start[unit] - 0.5 <= time <= index.unit_end[unit] + 0.5:
                    return unit
            except KeyError:
                pass
        try:
            film = index.film_ids.index(film_id)
        except ValueError:
            return None
        units = np.where((index.unit_film == film) & (index.unit_start <= time) & (index.unit_end > time))[0]
        return int(units[0]) if len(units) else None

    def content_box(self, film_id: str) -> list[float] | None:
        if self.index is None:
            return None
        if film_id not in self._films:
            rows = {row["film_id"]: row for row in self.index.manifest.get("films", [])}
            self._films.update(rows)
        return (self._films.get(film_id) or {}).get("content_box")

    def _row(self, row: int, kind: str) -> tuple[np.ndarray, float] | None:
        """(points in content fractions (1 or 2 x 2), size as a fraction of picture height) of one instant."""
        key = row * 2 + (kind == "subject")
        if key in self._row_features:
            return self._row_features[key]
        from pipeline.matching.moments import score as scoring
        moments = self.index.moments(np.array([row]))
        result = None
        if kind == "eyes" and moments.pose_xy is not None and len(moments.pose_xy):
            main = moments.main_pose()[0]
            if main >= 0:
                xy, conf = moments.pose_xy[main].astype(np.float64), moments.pose_conf[main]
                if conf[1] >= scoring.SEEN and conf[2] >= scoring.SEEN:
                    pair = xy[[1, 2]]
                    span = float(np.hypot(*(pair[1] - pair[0])))
                    if span > 0.004:
                        result = (pair, span)
        if result is None:
            _, main = scoring.salient(moments)
            if main[0] >= 0:
                point, exists = scoring.eye_points(moments, main)
                box = moments.boxes[main[0]]
                height = float(box[3] - box[1])
                if exists[0]:
                    size = height * (0.12 if moments.classes[main[0]] == scoring.PERSON else 0.5)
                    result = (point[:1].astype(np.float64), max(size, 0.01))
                elif kind == "subject":
                    centre = np.array([[(box[0] + box[2]) / 2, (box[1] + box[3]) / 2]])
                    result = (centre, max(height * 0.5, 0.01))
        self._row_features[key] = result
        return result

    def at(self, film_id: str, unit_id: str | None, time: float, kind: str = "eyes") -> tuple[np.ndarray, float] | None:
        """The feature of a source instant in content fractions, interpolated between grid instants."""
        unit = self._unit(film_id, unit_id, time)
        if unit is None:
            return None
        rows = self.index.unit_rows(unit)
        if not len(rows):
            return None
        times = np.asarray(self.index.columns["time"][rows], dtype=np.float64)
        position = int(np.searchsorted(times, time))
        candidates = [rows[max(position - 1, 0)], rows[min(position, len(rows) - 1)]]
        weights = [1.0, 0.0]
        if candidates[0] != candidates[1]:
            t0, t1 = times[max(position - 1, 0)], times[min(position, len(rows) - 1)]
            weight = float(np.clip((time - t0) / max(t1 - t0, 1e-6), 0, 1))
            weights = [1 - weight, weight]
        found = [self._row(int(row), kind) for row in candidates]
        if found[0] is None and found[1] is None:
            return None
        if found[0] is None or found[1] is None or len(found[0][0]) != len(found[1][0]):
            nearest = found[0] if (found[0] is not None and (weights[0] >= 0.5 or found[1] is None)) else found[1]
            return nearest
        points = found[0][0] * weights[0] + found[1][0] * weights[1]
        return points, found[0][1] * weights[0] + found[1][1] * weights[1]


@dataclass
class Picture:
    """A source window as the renderer frames it: film, crop and display aspect."""

    film_id: str
    unit_id: str | None
    path: str
    display_aspect: float
    crop: dict | None

    def feature(self, features: FeatureSource, source_time: float, kind: str, width: int, height: int) -> Feature | None:
        if kind == "none":
            return None
        found = features.at(self.film_id, self.unit_id, source_time, kind)
        if found is None:
            return None
        points, size = found
        box = features.content_box(self.film_id)
        out = np.array([source_to_output(content_to_source(p, box), self.display_aspect, self.crop, width, height) for p in points])
        if len(points) == 2:
            pixel_size = float(np.hypot(*(out[1] - out[0])))
        else:
            _, _, _, fitted_height = fit_box(self.display_aspect, self.crop, width, height)
            content_height = (box[3] - box[1]) if box else 1.0
            pixel_size = size * content_height / (self.crop["height"] if self.crop else 1.0) * fitted_height
        return Feature(out, max(pixel_size, 1.0))


# -- source frames -------------------------------------------------------------

def display_aspect(path: str) -> float:
    from pipeline.lab.media import probe_media
    probe = probe_media(Path(path))
    video = next(stream for stream in probe["streams"] if stream["codec_type"] == "video")
    width, height = int(video["width"]), int(video["height"])
    sar = video.get("sample_aspect_ratio") or "1:1"
    try:
        num, den = (int(part) for part in sar.split(":"))
        ratio = num / den if num > 0 and den > 0 else 1.0
    except ValueError:
        ratio = 1.0
    return width * ratio / height


def read_frames(picture: Picture, start: float, count: int, fps: int, width: int, height: int,
                hold_before: float | None = None, hold_after: float | None = None) -> list[np.ndarray]:
    """``count`` output-sized RGB frames of a source window, framed like the cut renderer.

    Frames before ``hold_before`` (a shot start) repeat the first frame at or
    after it, and frames from ``hold_after`` (a shot end) on repeat the last
    frame before it, so a pre- or post-roll never shows a neighbouring shot.
    """
    if count <= 0:
        return []
    begin = start if hold_before is None else max(start, hold_before)
    if hold_after is not None and begin > hold_after - 1 / fps:
        begin = hold_after - 1 / fps
    held = 0 if hold_before is None else min(count - 1, max(0, int(round((begin - start) * fps))))
    filters = []
    if picture.crop:
        c = picture.crop
        filters.append(f"crop=trunc(iw*{c['width']}/2)*2:trunc(ih*{c['height']}/2)*2:iw*{c['x']}:ih*{c['y']}")
    filters += [f"scale=w='max(2,trunc(min({width},{height}*dar)/2)*2)':h='max(2,trunc(min({height},{width}/dar)/2)*2)'",
                "setsar=1", f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black", f"fps={fps}"]
    needed = count - held
    if hold_after is not None:
        needed = min(needed, int((hold_after - begin) * fps))
    needed = max(1, min(needed, count))          # never ask the decoder for an unbounded read
    raw = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-ss", f"{begin:.4f}", "-i", picture.path,
                          "-map", "0:v:0", "-an", "-vf", ",".join(filters), "-frames:v", str(needed), "-f", "rawvideo",
                          "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
    size = width * height * 3
    frames = [np.frombuffer(raw[i * size:(i + 1) * size], np.uint8).reshape(height, width, 3) for i in range(len(raw) // size)]
    if not frames:
        frames = [np.zeros((height, width, 3), np.uint8)]
    frames = [frames[0]] * held + frames
    while len(frames) < count:
        frames.append(frames[-1])
    return frames[:count]


def valid_mask(picture: Picture, width: int, height: int, feather: float = FEATHER) -> np.ndarray:
    """The output area a framed source picture covers, its edges feathered (letterbox pads are transparent)."""
    left, top, w, h = fit_box(picture.display_aspect, picture.crop, width, height)
    yy, xx = np.mgrid[0:height, 0:width].astype(np.float32) + 0.5
    inside = np.minimum(np.minimum(xx - left, left + w - xx), np.minimum(yy - top, top + h - yy))
    ramp = np.clip(inside / max(min(w, h) * feather, 1.0), 0.0, 1.0)
    return (ramp * ramp * (3 - 2 * ramp) * 255 + 0.5).astype(np.uint8)


# -- plan ------------------------------------------------------------------------

@dataclass
class Layer:
    """An overlay for a run of output frames."""

    first: int
    frames: list[np.ndarray]
    mask: np.ndarray
    matrices: list[np.ndarray]
    opacity: list[float]
    blend: str


@dataclass
class Plan:
    width: int
    height: int
    fps: int
    base: dict[int, np.ndarray] = field(default_factory=dict)          # frame -> base picture matrix
    layers: list[Layer] = field(default_factory=list)
    flash: dict[int, float] = field(default_factory=dict)
    echo: dict[int, float] = field(default_factory=dict)
    skipped: list[str] = field(default_factory=list)
    unaligned: list[str] = field(default_factory=list)

    def touched(self) -> set[int]:
        frames = set(self.base) | set(self.flash) | set(self.echo)
        for layer in self.layers:
            frames.update(range(layer.first, layer.first + len(layer.frames)))
        return frames


def _film_path(db: Any, film_id: str, cache: dict[str, tuple[str, float]]) -> tuple[str, float]:
    if film_id not in cache:
        from pipeline.lab.media import resolve_film
        path = str(resolve_film(db, film_id)["path"])
        cache[film_id] = (path, display_aspect(path))
    return cache[film_id]


def build_plan(manifest: dict, db: Any, features: FeatureSource, progress: Callable[[str], None] = lambda _: None,
               cancelled: Callable[[], bool] = lambda: False) -> Plan:
    """Resolve every effect of a render manifest into per-frame work."""
    fps, width, height = manifest["fps"], manifest["width"], manifest["height"]
    plan = Plan(width, height, fps)
    films: dict[str, tuple[str, float]] = {}
    origin = (manifest.get("music") or {}).get("envelope_start", (manifest.get("music") or {}).get("start", 0.0)) \
        if manifest.get("music") else 0.0
    origin += (manifest.get("music") or {}).get("offset_seconds", 0.0) if manifest.get("music") else 0.0
    total = sum(clip["frame_count"] for clip in manifest["clips"])
    # Frame ranges of the cut sequence.
    spans, first = [], 0
    for clip in manifest["clips"]:
        spans.append((first, first + clip["frame_count"], clip))
        first += clip["frame_count"]
    pictures: dict[int, Picture | None] = {}

    def picture_of(position: int) -> Picture | None:
        if position not in pictures:
            clip = spans[position][2]
            if clip.get("gap"):
                pictures[position] = None
            else:
                path, aspect = _film_path(db, clip["film_id"], films)
                pictures[position] = Picture(clip["film_id"], clip.get("unit_id"), path, aspect, clip.get("crop"))
        return pictures[position]

    def base_at(frame: int) -> tuple[int, float] | None:
        """(clip position, source time) of an output frame."""
        for position, (a, b, clip) in enumerate(spans):
            if a <= frame < b:
                return position, clip["source_start"] + (frame - a) / fps
        return None

    def frame_of(t: float) -> int:
        return int(round((t - origin) * fps))

    def base_feature(frame: int, kind: str) -> Feature | None:
        located = base_at(frame)
        if located is None:
            return None
        picture = picture_of(located[0])
        return None if picture is None else picture.feature(features, located[1], kind, width, height)

    centre = np.array([width / 2, height / 2])
    effect_list = manifest.get("effects", [])
    for number, effect in enumerate(effect_list):
        if cancelled():
            raise RuntimeError("Render cancelled")
        if number % 10 == 0:
            progress(f"Placing effects: {number + 1} of {len(effect_list)}")
        label = f"{effect['kind']} {effect['id']}"
        a, b = max(frame_of(effect["start"]), 0), min(frame_of(effect["end"]), total)
        if b <= a:
            plan.skipped.append(f"{label}: outside the reel")
            continue
        kind = effect.get("align", "eyes")
        if effect["kind"] == "flash":
            for f in range(a, b):
                t = effect["start"] + (f - a) / fps
                level = envelope(t, effect["start"], effect["end"], effect.get("attack", 0.0), effect.get("release", 0.0))
                plan.flash[f] = max(plan.flash.get(f, 0.0), level * effect.get("opacity", 1.0))
            continue
        if effect["kind"] == "echo":
            for f in range(a, b):
                t = effect["start"] + (f - a) / fps
                level = envelope(t, effect["start"], effect["end"], effect.get("attack", 0.0), effect.get("release", 0.0))
                plan.echo[f] = max(plan.echo.get(f, 0.0), level * effect.get("strength", 0.5))
            continue
        if effect["kind"] == "punch":
            for f in range(a, b):
                feature = base_feature(f, kind)
                point = feature.centre() if feature is not None else centre
                progress_value = (f - a) / max(b - a - 1, 1)
                scale = 1 + (effect.get("zoom", 1.3) - 1) * (1 - ease(progress_value, "out"))
                plan.base[f] = compose(plan.base.get(f, np.eye(2, 3)), scale_about(point, scale))
            continue
        if effect["kind"] == "overlay":
            source = effect["source"]
            path, aspect = _film_path(db, source["film_id"], films)
            picture = Picture(source["film_id"], source.get("unit_id"), path, aspect, source.get("crop"))
            count = b - a
            frames = read_frames(picture, source["source_start"], count, fps, width, height)
            matrices, opacity, fixed = [], [], None
            aligned = kind == "none"
            for i in range(count):
                f = a + i
                t = effect["start"] + i / fps
                matrix = np.eye(2, 3)
                if kind != "none" and (effect.get("track", True) or fixed is None):
                    moving = picture.feature(features, source["source_start"] + i / fps, kind, width, height)
                    target = base_feature(f, kind)
                    if moving is not None and target is not None:
                        matrix = similarity(moving, target, (width / 2, height / 2))
                        aligned = True
                    fixed = matrix
                elif fixed is not None:
                    matrix = fixed
                matrices.append(matrix)
                opacity.append(effect.get("opacity", 1.0) *
                               envelope(t, effect["start"], effect["end"], effect.get("attack", 0.0), effect.get("release", 0.0)))
            if not aligned:
                plan.unaligned.append(label)
            plan.layers.append(Layer(a, frames, valid_mask(picture, width, height), matrices, opacity, effect.get("blend", "normal")))
            continue
        # Cut effects: find the cut nearest ``at``.
        cut_frame = frame_of(effect["at"])
        boundaries = [end for _, end, _ in spans[:-1]]
        if not boundaries or min(abs(cut_frame - boundary) for boundary in boundaries) > 1:
            plan.skipped.append(f"{label}: no cut at {effect['at']:.2f}s")
            continue
        cut = min(boundaries, key=lambda boundary: abs(cut_frame - boundary))
        out_position = next(i for i, (_, end, _) in enumerate(spans) if end == cut)
        outgoing, incoming = picture_of(out_position), picture_of(out_position + 1)
        if outgoing is None or incoming is None:
            plan.skipped.append(f"{label}: a black gap meets the cut")
            continue
        out_clip, in_clip = spans[out_position][2], spans[out_position + 1][2]
        out_end = out_clip["source_start"] + out_clip["frame_count"] / fps
        in_start = in_clip["source_start"]
        before, after = cut - max(a, spans[out_position][0]), min(b, spans[out_position + 1][1]) - cut
        if effect["kind"] == "zoom_through":
            zoom = effect.get("zoom", 4.0)
            out_feature = outgoing.feature(features, out_end - 1 / fps, kind, width, height)
            in_feature = incoming.feature(features, in_start, kind, width, height)
            if out_feature is None or in_feature is None:
                plan.unaligned.append(label)
            out_point = out_feature.centre() if out_feature is not None else centre
            in_point = in_feature.centre() if in_feature is not None else centre
            out_box = fit_box(outgoing.display_aspect, outgoing.crop, width, height)
            in_box = fit_box(incoming.display_aspect, incoming.crop, width, height)
            for i in range(before):
                weight = ease((i + 1) / before, "in")
                f = cut - before + i
                moved = out_point + (centre - out_point) * weight
                matrix = cover(scale_about(out_point, 1 + (zoom - 1) * weight, moved), out_box, moved)
                plan.base[f] = compose(plan.base.get(f, np.eye(2, 3)), matrix)
            for i in range(after):
                weight = 1 - ease(i / after, "out")
                f = cut + i
                moved = in_point + (centre - in_point) * weight
                matrix = cover(scale_about(in_point, 1 + (zoom - 1) * weight, moved), in_box, moved)
                plan.base[f] = compose(plan.base.get(f, np.eye(2, 3)), matrix)
            continue
        # lock_cut: the incoming shot fades in eye on eye, then settles while the outgoing one fades out.
        peak = effect.get("opacity", 0.85)
        in_unit_start = _unit_bound(features, incoming, in_start, start=True)
        out_unit_end = _unit_bound(features, outgoing, out_end, start=False)
        pre = read_frames(incoming, in_start - before / fps, before, fps, width, height, hold_before=in_unit_start)
        post = read_frames(outgoing, out_end, after, fps, width, height, hold_after=out_unit_end)
        aligned = True
        pre_matrices, pre_opacity = [], []
        for i in range(before):
            f = cut - before + i
            moving = incoming.feature(features, in_start - (before - i) / fps, kind, width, height)
            target = base_feature(f, kind)
            if moving is None or target is None:
                aligned = False
            pre_matrices.append(similarity(moving, target, tuple(centre)))
            pre_opacity.append(peak * ease((i + 1) / (before + 1)))
        if before:
            plan.layers.append(Layer(cut - before, pre, valid_mask(incoming, width, height), pre_matrices, pre_opacity,
                                     effect.get("blend", "normal")))
        settle = pre_matrices[-1] if pre_matrices else similarity(
            incoming.feature(features, in_start, kind, width, height),
            outgoing.feature(features, out_end - 1 / fps, kind, width, height), tuple(centre))
        post_matrices, post_opacity = [], []
        in_box = fit_box(incoming.display_aspect, incoming.crop, width, height)
        in_feature = incoming.feature(features, in_start, kind, width, height)
        for i in range(after):
            f = cut + i
            weight = ease(i / max(after - 1, 1), "out")
            base_matrix = blend_matrices(settle, np.eye(2, 3), weight)
            anchor = base_matrix[:, :2] @ (in_feature.centre() if in_feature is not None else centre) + base_matrix[:, 2]
            base_matrix = cover(base_matrix, in_box, anchor)
            plan.base[f] = compose(plan.base.get(f, np.eye(2, 3)), base_matrix)
            moving = outgoing.feature(features, out_end + i / fps, kind, width, height)
            target_raw = incoming.feature(features, in_start + i / fps, kind, width, height)
            if moving is None or target_raw is None:
                aligned = False
                post_matrices.append(np.eye(2, 3))
            else:
                target = Feature(np.array([base_matrix[:, :2] @ p + base_matrix[:, 2] for p in target_raw.points]),
                                 target_raw.size * math.hypot(base_matrix[0, 0], base_matrix[1, 0]))
                post_matrices.append(similarity(moving, target, tuple(centre)))
            post_opacity.append((1 - peak) * (1 - ease(i / max(after, 1))))
        if after:
            plan.layers.append(Layer(cut, post, valid_mask(outgoing, width, height), post_matrices, post_opacity,
                                     effect.get("blend", "normal")))
        if not aligned:
            plan.unaligned.append(label)
    for message in plan.skipped + [f"{label}: no feature found; centred" for label in plan.unaligned]:
        progress(f"Effects: {message}")
    return plan


def _unit_bound(features: FeatureSource, picture: Picture, time: float, start: bool) -> float | None:
    index = features.index
    if index is None:
        return None
    unit = features._unit(picture.film_id, picture.unit_id, time)
    if unit is None:
        return None
    return float(index.unit_start[unit]) + 0.04 if start else float(index.unit_end[unit]) - 0.04


# -- compositing -----------------------------------------------------------------

def composite(source: Path, target: Path, plan: Plan, expected_frames: int, progress: Callable[[str], None],
              cancelled: Callable[[], bool] = lambda: False) -> None:
    """Re-encode the cut sequence with the plan applied (frames without effects pass through)."""
    import av
    from fractions import Fraction
    touched = plan.touched()
    width, height, fps = plan.width, plan.height, plan.fps
    layers_at: dict[int, list[tuple[Layer, int]]] = {}
    for layer in plan.layers:
        for i in range(len(layer.frames)):
            layers_at.setdefault(layer.first + i, []).append((layer, i))
    previous = None
    written = 0
    with av.open(str(source)) as reader, av.open(str(target), "w") as writer:
        stream = writer.add_stream("libx264", rate=fps)
        stream.width, stream.height, stream.pix_fmt = width, height, "yuv420p"
        stream.time_base = Fraction(1, fps)
        stream.options = {"crf": "18", "preset": "fast"}
        for index, frame in enumerate(reader.decode(video=0)):
            if cancelled():
                raise RuntimeError("Render cancelled")
            if index % 240 == 0:
                progress(f"Applying effects: frame {index + 1} of {expected_frames}")
            picture = frame.to_ndarray(format="rgb24")
            if index in touched or (index + 1) in plan.echo:
                work = picture.astype(np.float32) / 255
                if index in plan.base:
                    work = warp(work, plan.base[index], width, height).astype(np.float32) / 255
                for layer, i in layers_at.get(index, []):
                    opacity = layer.opacity[i]
                    if opacity <= 0.001:
                        continue
                    top = warp(layer.frames[i], layer.matrices[i], width, height).astype(np.float32) / 255
                    alpha = warp(layer.mask, layer.matrices[i], width, height, order=1).astype(np.float32)[..., None] / 255
                    work = blend(work, top, alpha * opacity, layer.blend)
                if index in plan.flash:
                    work = work + (1 - work) * plan.flash[index]
                if index in plan.echo and previous is not None:
                    work = work * (1 - plan.echo[index]) + previous * plan.echo[index]
                previous = work if index + 1 in plan.echo else None
                picture = np.clip(work * 255 + 0.5, 0, 255).astype(np.uint8)
            out = av.VideoFrame.from_ndarray(picture, format="rgb24")
            out.pts = index
            for packet in stream.encode(out):
                writer.mux(packet)
            written += 1
        for packet in stream.encode():
            writer.mux(packet)
    if written != expected_frames:
        raise ValueError(f"Effects pass wrote {written} frames, expected {expected_frames}")
