"""Composite face: a face rebuilt from other faces, aligned feature to feature.

The host face is cut into cells. Each cell shows the *same region* of a different face from the
library, warped so that face's eyes and nose sit exactly on the host's. Eyes land on eyes, mouths
on mouths, so every tile reads as the part of a face it replaces and the whole reads as the host.
Alignment comes from the library's own per-instant keypoints (the moments index, ADR-0099); no
new detector. The faces follow the host's head frame by frame.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

NOSE, LEFT_EYE, RIGHT_EYE = 0, 1, 2


@dataclass
class FacePoints:
    eyes: np.ndarray          # 2 x 2 px (left, right)
    nose: np.ndarray          # 2 px

    @property
    def triangle(self) -> np.ndarray:
        return np.vstack([self.eyes, self.nose[None]]).astype(np.float32)

    @property
    def interocular(self) -> float:
        return float(np.hypot(*(self.eyes[1] - self.eyes[0])))


class Landmarks:
    """Eyes and nose of the main person at any instant of any indexed shot, in content fractions."""

    def __init__(self, index, seen: float):
        self.index = index
        self.seen = seen
        self._films = {row["film_id"]: row for row in index.manifest.get("films", [])}

    def content_box(self, film_id: str) -> list[float]:
        return (self._films.get(film_id) or {}).get("content_box") or [0.0, 0.0, 1.0, 1.0]

    def _row_points(self, row: int) -> np.ndarray | None:
        m = self.index.moments(np.array([row]))
        if m.pose_xy is None or not len(m.pose_xy):
            return None
        main = m.main_pose()[0]
        if main < 0:
            return None
        conf = m.pose_conf[main]
        if conf[LEFT_EYE] < self.seen or conf[RIGHT_EYE] < self.seen or conf[NOSE] < self.seen:
            return None
        xy = m.pose_xy[main].astype(np.float64)
        pts = xy[[LEFT_EYE, RIGHT_EYE, NOSE]]
        if np.hypot(*(pts[1] - pts[0])) < 0.01:
            return None
        return pts

    def at(self, unit_id: str, time: float) -> np.ndarray | None:
        """(3 x 2) eyes and nose in content fractions, interpolated between the grid instants around ``time``."""
        try:
            unit = self.index.unit_index(unit_id)
        except KeyError:
            return None
        rows = self.index.unit_rows(unit)
        if not len(rows):
            return None
        times = np.asarray(self.index.columns["time"][rows], dtype=np.float64)
        pos = int(np.searchsorted(times, time))
        a, b = rows[max(pos - 1, 0)], rows[min(pos, len(rows) - 1)]
        pa, pb = self._row_points(int(a)), self._row_points(int(b))
        if pa is None and pb is None:
            return None
        if pa is None or pb is None or a == b:
            return pa if pa is not None else pb
        t0, t1 = times[max(pos - 1, 0)], times[min(pos, len(rows) - 1)]
        w = float(np.clip((time - t0) / max(t1 - t0, 1e-6), 0, 1))
        return pa * (1 - w) + pb * w

    def pixels(self, film_id: str, unit_id: str, time: float, width: int, height: int) -> FacePoints | None:
        pts = self.at(unit_id, time)
        if pts is None:
            return None
        x0, y0, x1, y1 = self.content_box(film_id)
        px = np.stack([(x0 + pts[:, 0] * (x1 - x0)) * width, (y0 + pts[:, 1] * (y1 - y0)) * height], axis=1)
        return FacePoints(eyes=px[:2], nose=px[2])


def load_landmarks(config):
    from pipeline.matching.moments import index as moment_index
    from pipeline.matching.moments import score as scoring
    index = moment_index.load(config)
    if index is None:
        raise ValueError("The moments index is not built; run the evidence moments pass first")
    return Landmarks(index, scoring.SEEN)


@dataclass
class DonorFace:
    film_id: str
    unit: int
    path: Path                 # keyframe
    points: FacePoints         # in keyframe pixels
    _image: np.ndarray | None = field(default=None, repr=False)

    def image(self) -> np.ndarray:
        if self._image is None:
            from PIL import Image
            with Image.open(self.path) as im:
                self._image = np.asarray(im.convert("RGB"), np.uint8)
        return self._image


def donor_faces(assets_dir: Path, landmarks: Landmarks, units: list[dict], *, exclude_film: str | None = None,
                limit: int = 400) -> list[DonorFace]:
    """Keyframes of the given shots whose main person shows both eyes and the nose."""
    from PIL import Image
    out: list[DonorFace] = []
    face_words = ("face", "close-up", "eyes", "portrait", "profile")
    for u in units:
        film, unit = u["film_id"], int(u["unit"])
        if film == exclude_film:
            continue
        caption = str(u.get("caption", "")).lower()
        if caption and not any(word in caption for word in face_words):
            continue
        shots_path = Path(assets_dir) / film / "shots.json"
        if not shots_path.is_file():
            continue
        shots = {s["shot_id"]: s for s in json.load(shots_path.open(encoding="utf-8")).get("shots", [])}
        shot = shots.get(f"{film}_{unit:04d}")
        if not shot:
            continue
        for k, t in enumerate(shot.get("keyframe_times", [])):
            path = Path(assets_dir) / film / "keyframes" / f"{film}_{unit:04d}_{k}.webp"
            if not path.is_file():
                continue
            with Image.open(path) as im:
                w, h = im.size
            pts = landmarks.pixels(film, f"{film}_{unit:04d}", float(t), w, h)
            if pts is None or pts.interocular < w * 0.06:          # only big faces: a small one enlarges to blur
                continue
            if abs(pts.eyes[1][1] - pts.eyes[0][1]) > 0.6 * pts.interocular:   # a tilted or wrong detection
                continue
            out.append(DonorFace(film, unit, path, pts))
            if len(out) >= limit:
                return out
    return out


def face_box(p: FacePoints) -> tuple[int, int, int, int]:
    """(x0, y0, w, h) around a face from its eyes: ample forehead, chin and cheeks."""
    d = p.interocular
    cx = float(p.eyes[:, 0].mean()); cy = float(p.eyes[:, 1].mean())
    x0, x1 = cx - 1.9 * d, cx + 1.9 * d
    y0, y1 = cy - 1.7 * d, cy + 2.4 * d
    return int(x0), int(y0), int(x1 - x0), int(y1 - y0)


# Feature cells in face units: x in interoculars from the eye midpoint (left negative), y down from
# the eye line. Each is (name, x0, x1, y0, y1). Eyes, nose, mouth and chin are single cells so the
# feature a donor contributes is whole; cheeks and forehead are larger, calmer cells.
FACE_CELLS = [
    ("brow-l", -1.9, -0.25, -1.7, -0.5), ("brow-c", -0.25, 0.25, -1.7, -0.5), ("brow-r", 0.25, 1.9, -1.7, -0.5),
    ("temple-l", -1.9, -1.3, -0.5, 0.5), ("eye-l", -1.3, -0.25, -0.5, 0.5), ("bridge", -0.25, 0.25, -0.5, 0.5),
    ("eye-r", 0.25, 1.3, -0.5, 0.5), ("temple-r", 1.3, 1.9, -0.5, 0.5),
    ("cheek-l", -1.9, -0.5, 0.5, 1.05), ("nose", -0.5, 0.5, 0.5, 1.05), ("cheek-r", 0.5, 1.9, 0.5, 1.05),
    ("jaw-l", -1.9, -0.75, 1.05, 1.65), ("mouth", -0.75, 0.75, 1.05, 1.65), ("jaw-r", 0.75, 1.9, 1.05, 1.65),
    ("chin-l", -1.9, -0.9, 1.65, 2.4), ("chin", -0.9, 0.9, 1.65, 2.4), ("chin-r", 0.9, 1.9, 1.65, 2.4),
]


def face_frame(p: FacePoints) -> tuple[np.ndarray, np.ndarray, np.ndarray, float]:
    """Origin (eye midpoint), unit vectors along and down the eye line, and the interocular distance."""
    origin = p.eyes.mean(axis=0)
    across = p.eyes[1] - p.eyes[0]
    d = float(np.hypot(*across)) or 1.0
    across = across / d
    down = np.array([-across[1], across[0]])
    if np.dot(down, p.nose - origin) < 0:               # "down" points toward the nose
        down = -down
    return origin, across, down, d


def cell_polygon(p: FacePoints, cell) -> np.ndarray:
    origin, across, down, d = face_frame(p)
    _, x0, x1, y0, y1 = cell
    corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    return np.array([origin + across * (x * d) + down * (y * d) for x, y in corners], np.float32)


def _similarity(src: np.ndarray, dst: np.ndarray) -> np.ndarray:
    import cv2
    M, _ = cv2.estimateAffinePartial2D(src.reshape(-1, 1, 2), dst.reshape(-1, 1, 2), method=cv2.LMEDS)
    if M is None:
        M = cv2.getAffineTransform(src[:3], dst[:3])
    return M


def _luma(image: np.ndarray) -> np.ndarray:
    return image.astype(np.float32) @ np.array([0.299, 0.587, 0.114], np.float32)


@dataclass
class CompositeFace:
    """Feature-anchored cells, each filled by the donor whose aligned face fits the host there best."""
    donors: list[DonorFace]
    light: float = 0.85            # host light carried into each donor cell
    colour: float = 0.35           # host colour carried in
    seed: int = 7
    max_scale: float = 2.5         # a donor may be enlarged at most this much (blur guard)
    assigned: dict = field(default_factory=dict, repr=False)

    def choose(self, frame: np.ndarray, host: FacePoints, *, probe: int = 480) -> None:
        """Pick a donor per cell once, by luma structure of the aligned donor against the host cell."""
        import cv2
        h, w = frame.shape[:2]
        s = probe / h
        small = cv2.resize(frame, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA)
        host_s = FacePoints(host.eyes * s, host.nose * s)
        host_l = _luma(small)
        hs = cv2.GaussianBlur(host_l, (0, 0), 1.5)
        polys = [cell_polygon(host_s, c) for c in FACE_CELLS]
        masks = []
        for poly in polys:
            m = np.zeros(host_l.shape, np.uint8)
            cv2.fillConvexPoly(m, np.round(poly).astype(np.int32), 1)
            masks.append(m.astype(bool))
        scores = np.full((len(self.donors), len(FACE_CELLS)), np.inf)
        dst = host_s.triangle
        for i, donor in enumerate(self.donors):
            scale = host.interocular / max(donor.points.interocular, 1e-3)
            if scale > self.max_scale:
                continue
            M = _similarity(donor.points.triangle, dst)
            image = donor.image()
            warped = cv2.warpAffine(image, M, (small.shape[1], small.shape[0]), flags=cv2.INTER_AREA,
                                    borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0))
            cover = cv2.warpAffine(np.ones(image.shape[:2], np.uint8), M, (small.shape[1], small.shape[0]),
                                   flags=cv2.INTER_NEAREST, borderMode=cv2.BORDER_CONSTANT, borderValue=0)
            wl = cv2.GaussianBlur(_luma(warped), (0, 0), 1.5)
            for j, m in enumerate(masks):
                if m.sum() < 16 or cover[m].mean() < 0.98:              # the donor's frame must cover the cell
                    continue
                a, b = hs[m], wl[m]
                a = (a - a.mean()) / (a.std() + 1e-3)
                b = (b - b.mean()) / (b.std() + 1e-3)
                structure = float(np.mean((a - b) ** 2))
                # chroma: the donor cell should be the same kind of colour (skin, not sky)
                hc = small[m].reshape(-1, 3).mean(0); dc = warped[m].reshape(-1, 3).mean(0)
                hc = hc / (hc.sum() + 1e-3); dc = dc / (dc.sum() + 1e-3)
                chroma = float(np.abs(hc - dc).sum())
                scores[i, j] = structure + 6.0 * chroma
        rng = np.random.RandomState(self.seed)
        used: set[int] = set()
        for j in range(len(FACE_CELLS)):
            order = np.argsort(scores[:, j])
            good = [int(i) for i in order[:8] if np.isfinite(scores[i, j])]
            fresh = [i for i in good if i not in used] or good
            if not fresh:
                continue
            pick = fresh[rng.randint(min(3, len(fresh)))]
            self.assigned[j] = pick
            used.add(pick)

    def step(self, frame: np.ndarray, host: FacePoints, *, keep: np.ndarray | None = None,
             progress: float | None = None) -> np.ndarray:
        import cv2
        from pipeline.algmods.mosaic import tone_transfer
        if not self.assigned:
            self.choose(frame, host)
        h, w = frame.shape[:2]
        out = frame.copy()
        dst = host.triangle
        warped: dict[int, np.ndarray] = {}
        rs = np.random.RandomState(self.seed)
        order = rs.uniform(0, 1, len(FACE_CELLS))
        for j, cell in enumerate(FACE_CELLS):
            if j not in self.assigned:
                continue
            if progress is not None and progress < order[j] * 0.85:
                continue
            d = self.assigned[j]
            if d not in warped:
                donor = self.donors[d]
                M = _similarity(donor.points.triangle, dst)
                warped[d] = cv2.warpAffine(donor.image(), M, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
            poly = np.round(cell_polygon(host, cell)).astype(np.int32)
            x0, y0 = max(0, poly[:, 0].min()), max(0, poly[:, 1].min())
            x1, y1 = min(w, poly[:, 0].max() + 1), min(h, poly[:, 1].max() + 1)
            if x1 - x0 < 2 or y1 - y0 < 2:
                continue
            m = np.zeros((y1 - y0, x1 - x0), np.uint8)
            cv2.fillConvexPoly(m, poly - [x0, y0], 1)
            cv2.polylines(m, [poly - [x0, y0]], True, 0, 1)             # a hairline of the real picture
            tile = tone_transfer(warped[d][y0:y1, x0:x1], frame[y0:y1, x0:x1], light=self.light, colour=self.colour)
            region = out[y0:y1, x0:x1]
            region[m.astype(bool)] = tile[m.astype(bool)]
        if keep is not None:
            k = keep[..., None].astype(np.float32)
            out = (out.astype(np.float32) * (1 - k) + frame.astype(np.float32) * k).astype(np.uint8)
        return out


# ---------------------------------------------------------------------------------------------
# Memory patches (after ohnohanajo): one feature at a time, a hard-edged rectangle of another
# face's same feature set exactly on the host's, held for a few frames on a beat, then gone.
# Sparse, rhythmic, never a grid.

PATCHES = {                       # name: (x0, x1, y0, y1) in face units, tight around the feature
    "eye-l": (-1.2, -0.3, -0.38, 0.38),
    "eye-r": (0.3, 1.2, -0.38, 0.38),
    "eyes": (-1.25, 1.25, -0.42, 0.42),
    "nose": (-0.45, 0.45, 0.3, 1.0),
    "mouth": (-0.72, 0.72, 1.0, 1.6),
    "brow": (-1.3, 1.3, -1.1, -0.4),
}


def patch_schedule(frames: int, *, fps: int = 30, bpm: float = 120.0, seed: int = 7,
                   hold: tuple[int, int] = (6, 12), cutin_every: int = 5) -> list[tuple[int, int, str]]:
    """(start, length, feature) events on a beat grid; some beats rest; every few patches a full cut-in."""
    rng = np.random.RandomState(seed)
    beat = fps * 60.0 / bpm
    names = ["eye-l", "mouth", "eye-r", "nose", "eyes", "brow", "mouth", "eye-l"]
    events, k, n = [], 0, 0
    t = beat * 2                                         # a bar of the real face first
    while t < frames - hold[1]:
        if rng.uniform() < 0.25:                         # a rest
            t += beat
            continue
        n += 1
        if n % cutin_every == 0:
            events.append((int(t), int(beat * 0.6), "cutin"))
        else:
            events.append((int(t), int(rng.randint(hold[0], hold[1] + 1)), names[k % len(names)]))
            k += 1
        t += beat * (1 if rng.uniform() < 0.6 else 0.5)
    return events


@dataclass
class PatchFace:
    donors: list[DonorFace]
    light: float = 0.5             # some of the host's light into the patch; the rest stays a sticker
    seed: int = 7
    max_scale: float = 2.0
    tolerance: float = 0.1         # a donor's warped eye must land within this many interoculars of the host's
    _choice: dict = field(default_factory=dict, repr=False)

    def _fit(self, donor: DonorFace, host: FacePoints) -> np.ndarray | None:
        import cv2
        if host.interocular / max(donor.points.interocular, 1e-3) > self.max_scale:
            return None
        M = _similarity(donor.points.triangle, host.triangle)
        moved = cv2.transform(donor.points.triangle.reshape(-1, 1, 2), M).reshape(-1, 2)
        if np.abs(moved - host.triangle).max() > self.tolerance * host.interocular:
            return None
        return M

    def choose(self, frame: np.ndarray, host: FacePoints, feature: str, used: set[int]) -> int | None:
        """The donor whose aligned feature best matches the host's in structure and tone, unused so far."""
        import cv2
        h, w = frame.shape[:2]
        s = 480 / h
        small = cv2.resize(frame, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA)
        host_s = FacePoints(host.eyes * s, host.nose * s)
        poly = np.round(cell_polygon(host_s, ("p", *PATCHES[feature]))).astype(np.int32)
        m = np.zeros(small.shape[:2], np.uint8); cv2.fillConvexPoly(m, poly, 1); m = m.astype(bool)
        if m.sum() < 16:
            return None
        hl = cv2.GaussianBlur(_luma(small), (0, 0), 1.2)
        a = hl[m]; a = (a - a.mean()) / (a.std() + 1e-3)
        hc = small[m].reshape(-1, 3).mean(0); hc = hc / (hc.sum() + 1e-3)
        best, best_score = None, np.inf
        rng = np.random.RandomState(self.seed + len(used))
        order = rng.permutation(len(self.donors))
        for i in order[:220]:
            if int(i) in used:
                continue
            donor = self.donors[int(i)]
            M = self._fit(donor, host_s)
            if M is None:
                continue
            img = donor.image()
            warped = cv2.warpAffine(img, M, (small.shape[1], small.shape[0]), flags=cv2.INTER_AREA, borderMode=cv2.BORDER_CONSTANT)
            cover = cv2.warpAffine(np.ones(img.shape[:2], np.uint8), M, (small.shape[1], small.shape[0]), flags=cv2.INTER_NEAREST)
            if cover[m].mean() < 0.99:
                continue
            wl = cv2.GaussianBlur(_luma(warped), (0, 0), 1.2)
            b = wl[m]; b = (b - b.mean()) / (b.std() + 1e-3)
            dc = warped[m].reshape(-1, 3).mean(0); dc = dc / (dc.sum() + 1e-3)
            score = float(np.mean((a - b) ** 2)) + 5.0 * float(np.abs(hc - dc).sum())
            if score < best_score:
                best, best_score = int(i), score
        return best

    def patch(self, frame: np.ndarray, host: FacePoints, feature: str, donor_index: int) -> np.ndarray:
        import cv2
        from pipeline.algmods.mosaic import tone_transfer
        h, w = frame.shape[:2]
        donor = self.donors[donor_index]
        M = _similarity(donor.points.triangle, host.triangle)
        warped = cv2.warpAffine(donor.image(), M, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
        poly = np.round(cell_polygon(host, ("p", *PATCHES[feature]))).astype(np.int32)
        x0, y0 = max(0, poly[:, 0].min()), max(0, poly[:, 1].min())
        x1, y1 = min(w, poly[:, 0].max() + 1), min(h, poly[:, 1].max() + 1)
        out = frame.copy()
        if x1 - x0 < 2 or y1 - y0 < 2:
            return out
        m = np.zeros((y1 - y0, x1 - x0), np.uint8); cv2.fillConvexPoly(m, poly - [x0, y0], 1)
        tile = tone_transfer(warped[y0:y1, x0:x1], frame[y0:y1, x0:x1], light=self.light, colour=0.0)
        region = out[y0:y1, x0:x1]
        region[m.astype(bool)] = tile[m.astype(bool)]
        return out

    def cutin(self, frame: np.ndarray, host: FacePoints, donor_index: int) -> np.ndarray:
        """The whole donor face aligned to the host, full frame: a flash of someone else."""
        import cv2
        h, w = frame.shape[:2]
        donor = self.donors[donor_index]
        M = _similarity(donor.points.triangle, host.triangle)
        return cv2.warpAffine(donor.image(), M, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
