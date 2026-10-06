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
- ``echo``: a decaying trail of earlier frames;
- ``screen`` (ADR-0108): another source playing on a screen in the picture,
  in perspective inside keyed corners, optionally pushing into the screen
  until the source fills the frame.

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

EFFECTS_PROFILE = "feature-locked-effects-v2"
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
          limit: float = COVER_LIMIT, region: tuple[float, float, float, float] | None = None) -> np.ndarray:
    """Keep a moved picture filling its own box, or ``region`` of the output (no new black corners).

    First zoom about ``anchor`` (the feature, so it stays put) up to ``limit``.
    When even that leaves a corner uncovered, the move itself is weakened
    toward the picture's own framing until a zoom within the limit fills it.
    """
    left, top, w, h = box
    r_left, r_top, r_w, r_h = region or box
    corners = np.array([[r_left, r_top], [r_left + r_w, r_top], [r_left, r_top + r_h], [r_left + r_w, r_top + r_h]])

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
    """outer after inner: 2 x 3 when both are affine, else the 3 x 3 projective product."""
    full = lambda m: m if m.shape == (3, 3) else np.vstack([m, [0.0, 0.0, 1.0]])
    product = full(outer) @ full(inner)
    return product[:2] if outer.shape != (3, 3) and inner.shape != (3, 3) else product


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
    """Resample ``picture`` (H x W x C uint8 or float) through ``matrix`` (picture -> output pixels).

    ``matrix`` is affine (2 x 3) or projective (3 x 3, for screens in perspective).
    """
    from PIL import Image
    full = matrix if matrix.shape == (3, 3) else np.vstack([matrix, [0.0, 0.0, 1.0]])
    inverse = np.linalg.inv(full)
    resample = Image.BICUBIC if order == 3 else Image.BILINEAR
    if picture.dtype != np.uint8:
        picture = np.clip(picture * 255 + 0.5, 0, 255).astype(np.uint8)
    mode = "L" if picture.ndim == 2 else "RGB"
    image = Image.fromarray(picture, mode)
    if np.allclose(inverse[2], [0.0, 0.0, inverse[2, 2]]):
        coefficients = tuple((inverse[:2] / inverse[2, 2]).ravel())
        return np.asarray(image.transform((width, height), Image.AFFINE, coefficients, resample=resample))
    coefficients = tuple((inverse / inverse[2, 2]).ravel()[:8])
    return np.asarray(image.transform((width, height), Image.PERSPECTIVE, coefficients, resample=resample))


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
    """An overlay for a run of output frames.

    ``mask`` (static) or ``masks`` (per frame) say where it shows. They live in
    the overlay picture's own pixels and move with its matrix, unless
    ``mask_space`` is ``output``. ``base_classes`` replaces the mask with the
    base picture's own segmented subject (a fill). ``matte`` paints the shown
    area a flat colour (a silhouette). ``window`` (output pixels) further limits
    where it shows (a split screen). ``occluders`` are segmented classes of the
    picture underneath that stay in front of the layer (people before a screen);
    with ``screens`` (the screen's corners per frame), instances lying mostly on
    the screen are its own picture, not in front of it.
    """

    first: int
    frames: list[np.ndarray]
    mask: np.ndarray | None
    matrices: list[np.ndarray]
    opacity: list[float]
    blend: str
    masks: list[np.ndarray] | None = None
    mask_space: str = "picture"
    base_classes: list[str] | None = None
    matte: tuple[float, float, float] | None = None
    window: np.ndarray | None = None
    occluders: list[str] | None = None
    screens: list[np.ndarray] | None = None
    tone: list[float] | None = None


class Segmenter:
    """Per-frame instance masks (RF-DETR segmentation, the evidence model), loaded on first use."""

    def __init__(self, threshold: float = 0.4):
        self.threshold = threshold
        self._model = None
        self._names: dict[int, str] = {}

    def _load(self):
        if self._model is None:
            import torch
            from rfdetr import RFDETRSegSmall
            from rfdetr.assets.coco_classes import COCO_CLASSES
            self._model = RFDETRSegSmall()
            if torch.cuda.is_available():
                self._model.inference(compile=False, dtype=torch.float16)
            self._names = {int(key): str(value) for key, value in COCO_CLASSES.items()}
        return self._model

    def instances(self, rgb: np.ndarray, classes: list[str]) -> list[np.ndarray]:
        """Each detected instance of the wanted classes, as an H x W bool mask."""
        from PIL import Image
        model = self._load()
        wanted = {name.lower() for name in (classes or ["person"])}
        detections = model.predict(Image.fromarray(rgb), threshold=self.threshold)
        masks = getattr(detections, "mask", None)
        if masks is None or not len(detections):
            return []
        return [np.asarray(instance, bool) for class_id, instance in zip(detections.class_id, masks)
                if self._names.get(int(class_id), "").lower() in wanted]

    def mask(self, rgb: np.ndarray, classes: list[str]) -> np.ndarray:
        """The union of the wanted classes' masks (H x W uint8, 0 or 255)."""
        out = np.zeros(rgb.shape[:2], np.uint8)
        for instance in self.instances(rgb, classes):
            out[instance] = 255
        return out


def feature_patch(feature: Feature, region: str, width: int, height: int) -> np.ndarray:
    """A hard rectangle around a face feature (eyes strip, mouth or face), in the picture's pixels."""
    centre, span = feature.centre(), feature.size
    if len(feature.points) == 2:
        across = feature.points[1] - feature.points[0]
        across = across / max(np.hypot(*across), 1e-6)
        if across[0] < 0:
            across = -across
    else:
        across = np.array([1.0, 0.0])
    down = np.array([-across[1], across[0]])
    if region == "eyes":
        middle, half_w, half_h = centre, 1.35 * span, 0.42 * span
    elif region == "mouth":
        middle, half_w, half_h = centre + down * 1.15 * span, 0.85 * span, 0.42 * span
    else:                                                                       # face
        middle, half_w, half_h = centre + down * 0.55 * span, 1.35 * span, 1.6 * span
    yy, xx = np.mgrid[0:height, 0:width].astype(np.float32)
    dx, dy = xx - middle[0], yy - middle[1]
    inside = (np.abs(dx * across[0] + dy * across[1]) <= half_w) & (np.abs(dx * down[0] + dy * down[1]) <= half_h)
    return inside.astype(np.uint8) * 255


def rect_mask(rect: dict, turn: float, width: int, height: int) -> np.ndarray:
    """A hard (optionally turned) rectangle given in output fractions."""
    cx, cy = (rect["x"] + rect["width"] / 2) * width, (rect["y"] + rect["height"] / 2) * height
    half_w, half_h = rect["width"] * width / 2, rect["height"] * height / 2
    angle = math.radians(turn)
    yy, xx = np.mgrid[0:height, 0:width].astype(np.float32)
    dx, dy = xx - cx, yy - cy
    u = dx * math.cos(angle) + dy * math.sin(angle)
    v = -dx * math.sin(angle) + dy * math.cos(angle)
    return ((np.abs(u) <= half_w) & (np.abs(v) <= half_h)).astype(np.uint8) * 255


def panel_matrix(rect: dict, turn: float, width: int, height: int) -> np.ndarray:
    """Takes the whole output-sized picture into ``rect``, covering it, turned about its centre."""
    rw, rh = rect["width"] * width, rect["height"] * height
    scale = max(rw / width, rh / height)
    angle = math.radians(turn)
    rotation = np.array([[math.cos(angle), -math.sin(angle)], [math.sin(angle), math.cos(angle)]]) * scale
    target = np.array([(rect["x"] + rect["width"] / 2) * width, (rect["y"] + rect["height"] / 2) * height])
    shift = target - rotation @ np.array([width / 2, height / 2])
    return np.hstack([rotation, shift[:, None]])


def hex_colour(value: str | None) -> tuple[float, float, float] | None:
    if not value:
        return None
    return tuple(int(value[i:i + 2], 16) / 255 for i in (1, 3, 5))


# -- screens (ADR-0108) -----------------------------------------------------------

SCREEN_GLASS = 0.3          # how much a screen's picture darkens toward its edges (a CRT's falloff)
ON_SCREEN = 0.8             # an instance with this share of itself on the screen is the screen's own picture


def in_front(instances: list[np.ndarray], quad: np.ndarray | None) -> np.ndarray:
    """The union (H x W uint8) of instances standing in front of a screen: those not lying mostly on it."""
    if not instances:
        return np.zeros((0, 0), np.uint8)
    out = np.zeros(instances[0].shape, np.uint8)
    screen = None
    if quad is not None:
        from PIL import Image, ImageDraw
        centre = quad.mean(axis=0)
        grown = centre + (quad - centre) * 1.04
        image = Image.new("1", (out.shape[1], out.shape[0]), 0)
        ImageDraw.Draw(image).polygon([tuple(p) for p in grown], fill=1)
        screen = np.asarray(image, bool)
    for instance in instances:
        area = instance.sum()
        if area == 0:
            continue
        if screen is not None and (instance & screen).sum() >= ON_SCREEN * area:
            continue
        out[instance] = 255
    return out


def homography(points: np.ndarray, targets: np.ndarray) -> np.ndarray:
    """The 3 x 3 projective map taking four points onto four targets."""
    rows, values = [], []
    for (x, y), (u, v) in zip(points, targets):
        rows += [[x, y, 1, 0, 0, 0, -u * x, -u * y], [0, 0, 0, x, y, 1, -v * x, -v * y]]
        values += [u, v]
    return np.append(np.linalg.solve(np.array(rows, float), np.array(values, float)), 1.0).reshape(3, 3)


def quad_at(keys: list[dict], t: float, width: int, height: int) -> np.ndarray:
    """A screen's corners (output pixels: top-left, top-right, bottom-right, bottom-left) at song time ``t``."""
    times = [key["t"] for key in keys]
    if t <= times[0] or len(keys) == 1:
        corners = np.array(keys[0]["corners"], float)
    elif t >= times[-1]:
        corners = np.array(keys[-1]["corners"], float)
    else:
        k = int(np.searchsorted(times, t)) - 1
        weight = (t - times[k]) / (times[k + 1] - times[k])
        corners = np.array(keys[k]["corners"], float) * (1 - weight) + np.array(keys[k + 1]["corners"], float) * weight
    return corners * [width, height]


def pushed_quad(quad: np.ndarray, progress: float, width: int, height: int) -> np.ndarray:
    """The screen partway to filling the frame: an accelerating zoom at a steady exponential rate."""
    frame = np.array([[0.0, 0.0], [width, 0.0], [width, height], [0.0, height]])
    eased = min(max(progress, 0.0), 1.0) ** 1.5
    x, y = quad[:, 0], quad[:, 1]
    area = 0.5 * abs(np.dot(x, np.roll(y, 1)) - np.dot(y, np.roll(x, 1)))
    size = min(math.sqrt(max(area, 1.0) / (width * height)), 0.999)
    weight = (size ** (1 - eased) - size) / (1 - size)
    return quad + weight * (frame - quad)


def screen_rect(quad: np.ndarray, width: int, height: int) -> tuple[float, float, float, float]:
    """The middle of a full frame cut to the screen's own shape (x, y, w, h in pixels): what the screen shows."""
    across = (np.linalg.norm(quad[1] - quad[0]) + np.linalg.norm(quad[2] - quad[3])) / 2
    down = (np.linalg.norm(quad[3] - quad[0]) + np.linalg.norm(quad[2] - quad[1])) / 2
    aspect = across / max(down, 1e-6)
    w, h = (width, width / aspect) if aspect > width / height else (height * aspect, height)
    return (width - w) / 2, (height - h) / 2, w, h


def screen_mask(rect: tuple[float, float, float, float], radius: float, width: int, height: int) -> np.ndarray:
    """A rounded rectangle (anti-aliased) in the source frame's pixels; ``radius`` is a share of its shorter side."""
    from PIL import Image, ImageDraw
    x, y, w, h = rect
    big = Image.new("L", (width * 2, height * 2), 0)
    ImageDraw.Draw(big).rounded_rectangle([x * 2, y * 2, (x + w) * 2 - 1, (y + h) * 2 - 1],
                                          radius=max(radius * min(w, h) * 2, 0.0), fill=255)
    return np.asarray(big.resize((width, height), Image.BOX))


def glass(picture: np.ndarray, rect: tuple[float, float, float, float], strength: float) -> np.ndarray:
    """Darken a picture toward the edges of ``rect``, as a curved screen does."""
    if strength <= 0:
        return picture
    x, y, w, h = rect
    height, width = picture.shape[:2]
    dx = (np.arange(width, dtype=np.float32) - (x + w / 2)) / (w / 2)
    dy = (np.arange(height, dtype=np.float32) - (y + h / 2)) / (h / 2)
    falloff = 1 - strength * np.clip(dx[None, :] ** 2 + dy[:, None] ** 2, 0, 2) ** 2 / 4
    return np.clip(picture.astype(np.float32) * falloff[..., None], 0, 255).astype(np.uint8)


def screen_tone(top: np.ndarray, base: np.ndarray, alpha: np.ndarray, strength: float = 1.0) -> np.ndarray:
    """Show ``top`` (H x W x 3, 0-1) the way the set shows its own picture in ``base``: a CRT on film is softer,
    its blacks are grey, its light blooms, and the glass keeps some reflection. The insert takes about half of
    the set's brightness and tint (never darker than 0.6 x, never brighter than 1.25 x its own), its black level,
    a little bloom, and a trace of what was on the glass. ``strength`` fades it out as a push enters the screen."""
    if strength <= 0:
        return top
    region = alpha[..., 0] > 0.5
    if region.sum() < 64:
        return top
    t, b = top[region], base[region]
    weights = np.array([0.2126, 0.7152, 0.0722], np.float32)
    lum_t, lum_b = float(t.mean(axis=0) @ weights), float(b.mean(axis=0) @ weights)
    target = float(np.clip(0.5 * lum_t + 0.5 * lum_b, 0.6 * lum_t, 1.25 * lum_t))
    gain = target / max(lum_t, 1e-3)
    tint = b.mean(axis=0) / max(lum_b, 1e-3)
    tint = 1 + 0.25 * (np.clip(tint, 0.5, 1.6) - 1)
    floor = float(np.clip(0.8 * np.percentile(b @ weights, 3), 0.0, 0.1))
    out = top * gain * tint
    out = floor + out * (1 - floor) * 0.94                      # grey blacks, a touch less contrast
    from scipy import ndimage
    sigma = max(1.0, 0.012 * np.sqrt(region.sum()))
    glow = ndimage.gaussian_filter(np.clip(out - 0.6, 0, 1), (sigma, sigma, 0))
    out = out + 0.35 * glow                                     # highlights bloom
    out = out + 0.08 * base                                     # the glass's own reflections
    out = np.clip(out, 0, 1)
    return top + (out - top) * strength


def static_frame(seed: int, width: int, height: int) -> np.ndarray:
    """A frame of TV static: coarse grey noise with rolling bands."""
    rng = np.random.default_rng(seed)
    noise = rng.random((height // 3 + 1, width // 3 + 1), dtype=np.float32)
    noise = np.repeat(np.repeat(noise, 3, axis=0), 3, axis=1)[:height, :width]
    bands = 0.75 + 0.35 * np.sin(np.linspace(0, rng.uniform(6, 14), height, dtype=np.float32) + rng.uniform(0, 6))
    grey = np.clip(noise * bands[:, None] * 255, 0, 255).astype(np.uint8)
    return np.repeat(grey[..., None], 3, axis=2)


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
               cancelled: Callable[[], bool] = lambda: False, segmenter: Segmenter | None = None) -> Plan:
    """Resolve every effect of a render manifest into per-frame work."""
    segmenter = segmenter or Segmenter()
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
        if effect["kind"] == "screen":
            source = effect["source"]
            path, aspect = _film_path(db, source["film_id"], films)
            picture = Picture(source["film_id"], source.get("unit_id"), path, aspect, source.get("crop"))
            count = b - a
            frames = read_frames(picture, source["source_start"], count, fps, width, height)
            noisy = int(round(effect.get("static", 0.0) * fps))
            push = effect.get("push")
            seed = sum(ord(c) for c in effect["id"]) * 1009
            shown, matrices, masks, opacity, corners, tone = [], [], [], [], [], []
            for i in range(count):
                t = origin + (a + i) / fps
                quad = quad_at(effect["quad"], t, width, height)
                target, entered = quad, 0.0
                if push is not None and t + 1e-6 >= push:
                    # the last frame lands with the source filling the frame, so the cut that follows is seamless
                    entered = min(1.0, (t + 1 / fps - push) / max(effect["end"] - push, 1e-6))
                    target = pushed_quad(quad, entered, width, height)
                    try:
                        plan.base[a + i] = compose(homography(quad, target), plan.base.get(a + i, np.eye(2, 3)))
                    except np.linalg.LinAlgError:
                        pass
                corners.append(target)
                rect = screen_rect(target, width, height)
                x, y, w, h = rect
                try:
                    matrices.append(homography(np.array([[x, y], [x + w, y], [x + w, y + h], [x, y + h]]), target))
                    opacity.append(effect.get("opacity", 1.0))
                except np.linalg.LinAlgError:
                    matrices.append(np.eye(2, 3))
                    opacity.append(0.0)
                frame = static_frame(seed + i, width, height) if i < noisy else frames[i]
                shown.append(glass(frame, rect, SCREEN_GLASS * (1 - entered)))
                tone.append(0.0 if i < noisy else 1 - entered)
                masks.append(screen_mask(rect, effect.get("radius", 0.06) * (1 - entered), width, height))
            plan.layers.append(Layer(a, shown, None, matrices, opacity, "normal", masks=masks,
                                     occluders=effect.get("classes") or None, screens=corners, tone=tone))
            continue
        if effect["kind"] in ("overlay", "fill", "panel"):
            source = effect["source"]
            path, aspect = _film_path(db, source["film_id"], films)
            picture = Picture(source["film_id"], source.get("unit_id"), path, aspect, source.get("crop"))
            count = b - a
            frames = read_frames(picture, source["source_start"], count, fps, width, height)
            region = effect.get("region", "full") if effect["kind"] == "overlay" else "full"
            soft_default = region == "full" and effect["kind"] == "overlay"
            hard = (effect.get("edge") or ("soft" if soft_default else "hard")) == "hard"
            matrices, opacity, fixed, masks = [], [], None, []
            placed = effect["kind"] == "panel" or (effect["kind"] == "fill" and bool(effect.get("rect")))
            aligned = kind == "none" or placed
            for i in range(count):
                f = a + i
                t = effect["start"] + i / fps
                matrix = np.eye(2, 3)
                moving = None
                if placed:
                    matrix = panel_matrix(effect["rect"], effect.get("turn", 0.0), width, height)
                elif kind != "none" and (effect.get("track", True) or fixed is None):
                    moving = picture.feature(features, source["source_start"] + i / fps, kind, width, height)
                    target = base_feature(f, kind)
                    if moving is not None and target is not None:
                        matrix = similarity(moving, target, (width / 2, height / 2))
                        if effect.get("rect"):
                            # a window stays filled: the aligned picture zooms (or eases its move) to cover it
                            rect = effect["rect"]
                            window_box = (rect["x"] * width, rect["y"] * height, rect["width"] * width, rect["height"] * height)
                            matrix = cover(matrix, fit_box(picture.display_aspect, picture.crop, width, height), target.centre(),
                                           region=window_box)
                        aligned = True
                    fixed = matrix
                elif fixed is not None:
                    matrix = fixed
                if region in ("eyes", "mouth", "face"):
                    own = moving if moving is not None else picture.feature(features, source["source_start"] + i / fps,
                                                                             "eyes", width, height)
                    masks.append(feature_patch(own, region, width, height) if own is not None
                                 else np.zeros((height, width), np.uint8))
                elif region == "subject":
                    masks.append(segmenter.mask(frames[i], effect.get("classes") or ["person"]))
                matrices.append(matrix)
                opacity.append(effect.get("opacity", 1.0) *
                               envelope(t, effect["start"], effect["end"], effect.get("attack", 0.0), effect.get("release", 0.0)))
            if not aligned:
                plan.unaligned.append(label)
            matte = hex_colour(effect.get("matte"))
            if effect["kind"] == "panel":
                plan.layers.append(Layer(a, frames, rect_mask(effect["rect"], effect.get("turn", 0.0), width, height), matrices,
                                         opacity, effect.get("blend", "normal"), mask_space="output", matte=matte))
            elif effect["kind"] == "fill":
                # with a rect the source is set into it (a screen) and shows only where the picture's own subject is
                window = rect_mask(effect["rect"], effect.get("turn", 0.0), width, height) if effect.get("rect") else None
                plan.layers.append(Layer(a, frames, None, matrices, opacity, effect.get("blend", "normal"),
                                         base_classes=effect.get("classes") or ["person"], matte=matte, window=window))
            else:
                frame_mask = valid_mask(picture, width, height, 0.0 if hard else FEATHER)
                if masks:
                    masks = [np.minimum(m, frame_mask) for m in masks]
                window = rect_mask(effect["rect"], effect.get("turn", 0.0), width, height) if effect.get("rect") else None
                plan.layers.append(Layer(a, frames, frame_mask if not masks else None, matrices, opacity,
                                         effect.get("blend", "normal"), masks=masks or None, matte=matte, window=window))
            continue
        if effect["kind"] == "strips":
            count, sources = b - a, effect["sources"]
            band = width / len(sources)
            for k, source in enumerate(sources):
                path, aspect = _film_path(db, source["film_id"], films)
                picture = Picture(source["film_id"], source.get("unit_id"), path, aspect, source.get("crop"))
                frames = read_frames(picture, source["source_start"], count, fps, width, height)
                shift = np.array([[1.0, 0.0, (k + 0.5) * band - width / 2], [0.0, 1.0, 0.0]])
                strip = {"x": k / len(sources), "y": 0.0, "width": 1 / len(sources), "height": 1.0}
                opacity = [effect.get("opacity", 1.0) * envelope(effect["start"] + i / fps, effect["start"], effect["end"],
                                                                 effect.get("attack", 0.0), effect.get("release", 0.0))
                           for i in range(count)]
                plan.layers.append(Layer(a, frames, rect_mask(strip, 0.0, width, height), [shift] * count, opacity,
                                         effect.get("blend", "normal"), mask_space="output"))
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
            if effect.get("settle", False):
                # The incoming shot starts where the outgoing feature was and eases back to its own framing.
                weight = ease(i / max(after - 1, 1), "out")
                base_matrix = blend_matrices(settle, np.eye(2, 3), weight)
                anchor = base_matrix[:, :2] @ (in_feature.centre() if in_feature is not None else centre) + base_matrix[:, 2]
                base_matrix = cover(base_matrix, in_box, anchor)
                plan.base[f] = compose(plan.base.get(f, np.eye(2, 3)), base_matrix)
            else:
                base_matrix = np.eye(2, 3)                    # no settle: the incoming shot keeps its own framing
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
              cancelled: Callable[[], bool] = lambda: False, segmenter: Segmenter | None = None) -> None:
    """Re-encode the cut sequence with the plan applied (frames without effects pass through)."""
    segmenter = segmenter or Segmenter()
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
                base_masks: dict[tuple[str, ...], np.ndarray] = {}
                found: dict[tuple[str, ...], list[np.ndarray]] = {}
                for layer, i in layers_at.get(index, []):
                    opacity = layer.opacity[i]
                    if opacity <= 0.001:
                        continue
                    top = warp(layer.frames[i], layer.matrices[i], width, height).astype(np.float32) / 255
                    if layer.base_classes is not None:
                        key = tuple(layer.base_classes)
                        if key not in base_masks:
                            base_masks[key] = segmenter.mask(np.clip(work * 255 + 0.5, 0, 255).astype(np.uint8), list(key))
                        alpha = base_masks[key].astype(np.float32)[..., None] / 255
                    else:
                        mask = layer.masks[i] if layer.masks is not None else layer.mask
                        if layer.mask_space == "output":
                            alpha = mask.astype(np.float32)[..., None] / 255
                        else:
                            alpha = warp(mask, layer.matrices[i], width, height, order=1).astype(np.float32)[..., None] / 255
                    if layer.window is not None:
                        alpha = alpha * (layer.window.astype(np.float32)[..., None] / 255)
                    if layer.occluders:
                        key = ("instances",) + tuple(layer.occluders)
                        if key not in found:
                            found[key] = segmenter.instances(np.clip(work * 255 + 0.5, 0, 255).astype(np.uint8), list(layer.occluders))
                        front = in_front(found[key], layer.screens[i] if layer.screens is not None else None)
                        if front.size:
                            alpha = alpha * (1 - front.astype(np.float32)[..., None] / 255)
                    if layer.matte is not None:
                        top = np.broadcast_to(np.array(layer.matte, np.float32), top.shape)
                    if layer.tone is not None and layer.tone[i] > 0:
                        top = screen_tone(top, work, alpha, layer.tone[i])
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
