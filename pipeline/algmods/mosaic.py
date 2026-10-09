"""Cross-film mosaic: a shot rebuilt from other shots, matched cell by cell, with meaning first.

Each cell of the host frame is matched on its small colour block (not only its mean), so edges and
gradients inside a cell line up and the picture reads at a distance. What matters more than the
matching is which shots are allowed in: the host film's own moments (a film made of itself), the
shots a library search returns (a hand made of hands, a silhouette made of silhouettes), or the
whole library. Tiles persist so the surface is calm; they can play their own preview clip; the
mosaic can be confined to the segmented subject or its background; the real shot can shatter into
tiles and re-form; and the picture can zoom through one tile into the shot behind it.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from pipeline.algmods import tiles as tile_index

LUMA = np.array([0.299, 0.587, 0.114], np.float32)
_NAME = re.compile(r"^(?P<film>[0-9a-f]{64})_(?P<unit>\d{4})_(?P<k>\d)\.webp$")


def coarse_key(blocks: np.ndarray) -> np.ndarray:
    """A 2x2-quadrant colour key (12 values): what a KD-tree can search."""
    n, h, w, _ = blocks.shape
    hh, hw = h // 2, w // 2
    quads = np.stack([blocks[:, :hh, :hw].reshape(n, -1, 3).mean(1), blocks[:, :hh, hw:].reshape(n, -1, 3).mean(1),
                      blocks[:, hh:, :hw].reshape(n, -1, 3).mean(1), blocks[:, hh:, hw:].reshape(n, -1, 3).mean(1)], axis=1)
    return quads.reshape(n, -1).astype(np.float32)


def block_of(cell: np.ndarray, w: int, h: int) -> np.ndarray:
    import cv2
    return cv2.resize(cell, (w, h), interpolation=cv2.INTER_AREA).astype(np.float32) / 255.0


def _ease(t: float) -> float:
    t = min(1.0, max(0.0, t))
    return t * t * (3 - 2 * t)


@dataclass
class TileBank:
    """The indexed keyframes, with an allowed subset and a KD-tree over it."""
    assets_dir: Path
    exclude_film: str | None = None
    k: int = 48                                   # coarse candidates refined per cell
    min_detail: float = 0.012                     # only near-featureless tiles are dropped: a flat sky is a fine tile for a flat sky
    min_mean: float = 0.05                        # near-black frames are never tiles
    detail_bonus: float = 0.15                    # distance discount for detailed tiles: pictures, not swatches
    data: dict = field(default_factory=dict, repr=False)
    keys: np.ndarray | None = field(default=None, repr=False)
    blocks: np.ndarray | None = field(default=None, repr=False)
    detail: np.ndarray | None = field(default=None, repr=False)
    allowed: np.ndarray | None = field(default=None, repr=False)
    _tree: object = field(default=None, repr=False)
    _pixels: dict = field(default_factory=dict, repr=False)
    _units: dict = field(default_factory=dict, repr=False)      # (film, unit) -> bank rows

    head_seconds: float = 150.0                   # opening titles: never tiles
    tail_seconds: float = 420.0                   # end credits: never tiles
    _credits: np.ndarray | None = field(default=None, repr=False)

    def __post_init__(self):
        self.data = tile_index.load(self.assets_dir)
        self.blocks = self.data["blocks"].astype(np.float32) / 255.0
        self.keys = coarse_key(self.blocks)
        self.detail = self.blocks.reshape(len(self.blocks), -1).std(axis=1)
        for row, name in enumerate(self.data["names"].tolist()):
            m = _NAME.match(name)
            if m:
                self._units.setdefault((m["film"], int(m["unit"])), []).append(row)
        self._credits = self._credit_rows()
        self.restrict(None)

    def _credit_rows(self) -> np.ndarray:
        """Rows that fall in a film's opening titles or end credits, from its shots.json."""
        bad = np.zeros(len(self.blocks), bool)
        for film in self.data["film_ids"].tolist():
            path = Path(self.assets_dir) / film / "shots.json"
            if not path.is_file():
                continue
            try:
                shots = json.load(path.open(encoding="utf-8")).get("shots", [])
            except (OSError, ValueError):
                continue
            if not shots:
                continue
            duration = max(float(s["t_end"]) for s in shots)
            for shot in shots:
                if float(shot["t_start"]) < self.head_seconds or float(shot["t_end"]) > duration - self.tail_seconds:
                    unit = int(str(shot["shot_id"]).rsplit("_", 1)[-1])
                    for row in self._units.get((film, unit), []):
                        bad[row] = True
        return bad

    # ----- which shots may be tiles
    def restrict(self, rows: np.ndarray | None) -> None:
        """Allow only ``rows`` (bank indices), or everything, always minus flat tiles and the host film."""
        from scipy.spatial import cKDTree
        idx = np.arange(len(self.blocks)) if rows is None else np.asarray(sorted(set(int(r) for r in rows)), np.int64)
        if len(idx):
            idx = idx[(self.detail[idx] >= self.min_detail) & (self.blocks[idx].reshape(len(idx), -1).mean(axis=1) >= self.min_mean)]
        if self._credits is not None:
            idx = idx[~self._credits[idx]]
        film_ids = self.data["film_ids"]
        if self.exclude_film is not None and self.exclude_film in set(film_ids.tolist()):
            host = int(np.nonzero(film_ids == self.exclude_film)[0][0])
            idx = idx[self.data["film_index"][idx] != host]
        if len(idx) < self.k:
            raise ValueError(f"Only {len(idx)} tiles allowed; the set is too small for a mosaic")
        self.allowed = idx
        self._tree = cKDTree(self.keys[idx])

    def rows_for_film(self, film_id: str) -> np.ndarray:
        film_ids = self.data["film_ids"]
        if film_id not in set(film_ids.tolist()):
            return np.zeros(0, np.int64)
        f = int(np.nonzero(film_ids == film_id)[0][0])
        return np.nonzero(self.data["film_index"] == f)[0]

    def rows_for_units(self, units: list[tuple[str, int]]) -> np.ndarray:
        rows = [r for key in units for r in self._units.get(key, [])]
        return np.asarray(rows, np.int64)

    def unit_of(self, bank_index: int) -> tuple[str, int, int]:
        m = _NAME.match(str(self.data["names"][bank_index]))
        return m["film"], int(m["unit"]), int(m["k"])

    @property
    def block_shape(self) -> tuple[int, int]:
        return int(self.data["block_h"]), int(self.data["block_w"])

    # ----- matching
    def candidates(self, cell_blocks: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        _d, near = self._tree.query(coarse_key(cell_blocks), k=self.k)
        near = self.allowed[near]
        cand = self.blocks[near]
        diff = (cand - cell_blocks[:, None]) ** 2
        dist = (diff * LUMA[None, None, None, None, :] * 3.0).sum(axis=(2, 3, 4)) + 0.3 * diff.sum(axis=(2, 3, 4))
        dist = dist * (1.0 - self.detail_bonus * np.clip(self.detail[near] / 0.2, 0, 1))
        order = np.argsort(dist, axis=1)
        return np.take_along_axis(near, order, axis=1), np.take_along_axis(dist, order, axis=1)

    def distance(self, bank_index: np.ndarray, cell_blocks: np.ndarray) -> np.ndarray:
        diff = (self.blocks[bank_index] - cell_blocks) ** 2
        return (diff * LUMA * 3.0).sum(axis=(1, 2, 3)) + 0.3 * diff.sum(axis=(1, 2, 3))

    # ----- pixels
    def keyframe_path(self, bank_index: int) -> Path:
        film = str(self.data["film_ids"][self.data["film_index"][bank_index]])
        return Path(self.assets_dir) / film / "keyframes" / str(self.data["names"][bank_index])

    def preview_path(self, bank_index: int) -> Path:
        film, unit, _k = self.unit_of(bank_index)
        return Path(self.assets_dir) / film / "previews" / f"{film}_{unit:04d}.webm"

    def pixels(self, bank_index: int, size: tuple[int, int]) -> np.ndarray:
        key = (int(bank_index), size)
        hit = self._pixels.get(key)
        if hit is not None:
            return hit
        from PIL import Image
        with Image.open(self.keyframe_path(bank_index)) as im:
            out = _fit(np.asarray(im.convert("RGB"), np.uint8), size)
        if len(self._pixels) > 6000:
            self._pixels.clear()
        self._pixels[key] = out
        return out


def _unletterbox(image: np.ndarray) -> np.ndarray:
    """Drop black bars (letterbox or pillarbox) so a tile never carries a stripe of nothing."""
    gray = image.mean(axis=2)
    rows = np.nonzero(gray.mean(axis=1) > 10)[0]
    cols = np.nonzero(gray.mean(axis=0) > 10)[0]
    if len(rows) < image.shape[0] * 0.4 or len(cols) < image.shape[1] * 0.4:
        return image
    return image[rows[0]:rows[-1] + 1, cols[0]:cols[-1] + 1]


def _fit(image: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    """Centre-crop to the cell aspect and resize to (w, h)."""
    import cv2
    w, h = size
    image = _unletterbox(image)
    ih, iw = image.shape[:2]
    target = w / h
    if iw / ih > target:
        cw = int(round(ih * target)); x0 = (iw - cw) // 2; image = image[:, x0:x0 + cw]
    else:
        ch = int(round(iw / target)); y0 = (ih - ch) // 2; image = image[y0:y0 + ch]
    return cv2.resize(image, (w, h), interpolation=cv2.INTER_AREA if image.shape[1] > w else cv2.INTER_LINEAR)


@dataclass
class MovingTiles:
    """Each used tile plays its own preview clip inside its cell, decoded once at cell size."""
    bank: TileBank
    size: tuple[int, int]
    max_frames: int = 96
    _clips: dict = field(default_factory=dict, repr=False)

    def frames(self, bank_index: int) -> list[np.ndarray]:
        key = int(bank_index)
        hit = self._clips.get(key)
        if hit is not None:
            return hit
        clip: list[np.ndarray] = []
        path = self.bank.preview_path(bank_index)
        if path.is_file():
            import av
            try:
                with av.open(str(path)) as container:
                    for frame in container.decode(video=0):
                        clip.append(_fit(frame.to_ndarray(format="rgb24"), self.size))
                        if len(clip) >= self.max_frames:
                            break
            except Exception:
                clip = []
        if not clip:
            clip = [self.bank.pixels(bank_index, self.size)]
        self._clips[key] = clip
        return clip

    def at(self, bank_index: int, t: int, phase: int = 0) -> np.ndarray:
        clip = self.frames(bank_index)
        return clip[(t + phase) % len(clip)]


@dataclass
class Mosaic:
    """Persistent cell-to-tile assignment over a clip, with optional region, motion, reveal."""
    bank: TileBank
    columns: int = 12
    drift: float = 0.10            # re-match a cell when its tile's distance exceeds its best by this much
    hold: int = 8                  # frames a tile stays at least
    window: int = 3                # a tile is not reused within this many cells of another copy
    slack: float = 0.25            # candidates within best * (1 + slack) are equally good: one is chosen at random
    moving: bool = False           # tiles play their preview clips
    tint: float = 0.0              # 0..1: pull each tile's tone toward the host cell (content stays, tone follows)
    grout: int = 0                 # px gap between tiles, in the host's own colour
    seed: int = 7
    assigned: np.ndarray | None = field(default=None, repr=False)
    age: np.ndarray | None = field(default=None, repr=False)
    phase: np.ndarray | None = field(default=None, repr=False)
    _movers: MovingTiles | None = field(default=None, repr=False)
    _t: int = 0
    _reveal_order: np.ndarray | None = field(default=None, repr=False)

    def grid(self, h: int, w: int) -> tuple[int, int, int, int]:
        bh, bw = self.bank.block_shape
        cell_w = max(4, w // self.columns)
        cell_h = max(3, int(round(cell_w * bh / bw)))
        return (h + cell_h - 1) // cell_h, self.columns, cell_w, cell_h

    def assign(self, frame: np.ndarray, region: np.ndarray | None = None) -> None:
        """Update the cell-to-tile assignment for this frame; ``region`` (rows, cols) bool limits which cells need tiles."""
        h, w = frame.shape[:2]
        rows, cols, cw, ch = self.grid(h, w)
        bh, bw = self.bank.block_shape
        if self.assigned is None or self.assigned.shape != (rows, cols):
            self.assigned = np.full((rows, cols), -1, np.int64)
            self.age = np.zeros((rows, cols), np.int64)
            self.phase = np.random.RandomState(self.seed).randint(0, 96, (rows, cols))
        padded = np.zeros((rows * ch, cols * cw, 3), np.uint8)
        padded[:min(h, rows * ch), :min(w, cols * cw)] = frame[:rows * ch, :cols * cw]
        cells = padded.reshape(rows, ch, cols, cw, 3).transpose(0, 2, 1, 3, 4).reshape(rows * cols, ch, cw, 3)
        blocks = np.stack([block_of(c, bw, bh) for c in cells])
        flat_assigned = self.assigned.reshape(-1)
        flat_age = self.age.reshape(-1)
        wanted = np.ones(rows * cols, bool) if region is None else region.reshape(-1)
        current = np.where(flat_assigned >= 0, self.bank.distance(np.maximum(flat_assigned, 0), blocks), np.inf)
        need = wanted & ((flat_assigned < 0) | (flat_age >= self.hold))
        if need.any():
            cand, dist = self.bank.candidates(blocks[need])
            idx = np.nonzero(need)[0]
            swap = ((current[idx] - dist[:, 0]) > self.drift) | (flat_assigned[idx] < 0)
            chosen = cand[:, 0].copy()
            rng = np.random.RandomState(self.seed + self._t)
            grid = self.assigned
            for n, (i, go) in enumerate(zip(idx, swap)):
                if not go:
                    continue
                r, c = divmod(int(i), cols)
                near_tiles = grid[max(0, r - self.window):r + self.window + 1, max(0, c - self.window):c + self.window + 1].ravel()
                nearby = {self.bank.unit_of(int(t))[:2] for t in near_tiles if t >= 0}      # one tile per shot nearby
                good = cand[n][dist[n] <= dist[n, 0] * (1 + self.slack) + 1e-6]
                options = ([t for t in good if self.bank.unit_of(int(t))[:2] not in nearby]
                           or [t for t in cand[n] if self.bank.unit_of(int(t))[:2] not in nearby] or [cand[n, 0]])
                chosen[n] = options[rng.randint(len(options))]
                grid[r, c] = chosen[n]
            flat_assigned[idx[swap]] = chosen[swap]
            flat_age[idx[swap]] = 0
        flat_age += 1
        self.assigned = flat_assigned.reshape(rows, cols)
        self.age = flat_age.reshape(rows, cols)

    def tile_image(self, r: int, c: int, size: tuple[int, int]) -> np.ndarray:
        index = int(self.assigned[r, c])
        if self.moving:
            if self._movers is None or self._movers.size != size:
                self._movers = MovingTiles(self.bank, size)
            return self._movers.at(index, self._t, int(self.phase[r, c]))
        return self.bank.pixels(index, size)

    def compose(self, frame: np.ndarray, *, region: np.ndarray | None = None, progress: float | None = None,
                origin: tuple[float, float] | None = None, pop: int = 6, keep: np.ndarray | None = None) -> np.ndarray:
        """Draw tiles over ``frame``. ``region`` (rows, cols) bool: only those cells get tiles, the rest stay real.
        ``progress`` 0..1 reveals the mosaic cell by cell from ``origin`` (fractions), each tile popping in.
        ``keep`` (h, w) float 0..1: the real picture is composited back by this soft mask, so a kept subject
        has its true outline rather than a staircase of cells."""
        h, w = frame.shape[:2]
        rows, cols, cw, ch = self.grid(h, w)
        out = frame.copy()
        if progress is not None and self._reveal_order is None:
            oy, ox = (origin[1] if origin else 0.5), (origin[0] if origin else 0.5)
            rr, cc = np.mgrid[0:rows, 0:cols]
            d = np.hypot((cc + 0.5) / cols - ox, (rr + 0.5) / rows - oy)
            jitter = np.random.RandomState(self.seed).uniform(0, 0.18, (rows, cols))
            order = d / max(d.max(), 1e-6) * 0.82 + jitter
            self._reveal_order = order
        for r in range(rows):
            y0 = r * ch
            if y0 >= h:
                break
            for c in range(cols):
                if region is not None and not region[r, c]:
                    continue
                if self.assigned[r, c] < 0:
                    continue
                scale = 1.0
                if progress is not None:
                    start = float(self._reveal_order[r, c])
                    if progress < start:
                        continue
                    scale = _ease((progress - start) / max(pop / 100.0, 1e-6))
                x0 = c * cw
                tile = self.tile_image(r, c, (cw, ch))
                th, tw = min(ch, h - y0), min(cw, w - x0)
                if self.tint > 0:
                    host = frame[y0:y0 + th, x0:x0 + tw].reshape(-1, 3).mean(axis=0)
                    own = tile[:th, :tw].reshape(-1, 3).mean(axis=0)
                    gain = (host + 8) / (own + 8)
                    toned = np.clip(tile[:th, :tw].astype(np.float32) * gain, 0, 255)
                    tile = (tile[:th, :tw].astype(np.float32) * (1 - self.tint) + toned * self.tint).astype(np.uint8)
                if self.grout > 0:
                    g = self.grout
                    inner = tile[:th, :tw].copy()
                    inner[:g] = frame[y0:y0 + g, x0:x0 + tw]; inner[th - g:] = frame[y0 + th - g:y0 + th, x0:x0 + tw]
                    inner[:, :g] = frame[y0:y0 + th, x0:x0 + g]; inner[:, tw - g:] = frame[y0:y0 + th, x0 + tw - g:x0 + tw]
                    tile = inner
                if scale >= 0.999:
                    out[y0:y0 + th, x0:x0 + tw] = tile[:th, :tw]
                else:
                    sw, sh = max(1, int(round(tw * scale))), max(1, int(round(th * scale)))
                    import cv2
                    small = cv2.resize(tile[:th, :tw], (sw, sh), interpolation=cv2.INTER_AREA)
                    ox0, oy0 = x0 + (tw - sw) // 2, y0 + (th - sh) // 2
                    out[oy0:oy0 + sh, ox0:ox0 + sw] = small
        if keep is not None:
            k = keep[..., None].astype(np.float32)
            out = (out.astype(np.float32) * (1 - k) + frame.astype(np.float32) * k).astype(np.uint8)
        self._t += 1
        return out

    def step(self, frame: np.ndarray, **compose_kwargs) -> np.ndarray:
        self.assign(frame, compose_kwargs.get("region"))
        return self.compose(frame, **compose_kwargs)

    def cell_region(self, mask: np.ndarray | None, h: int, w: int, *, where: str = "subject", cover: float = 0.5) -> np.ndarray | None:
        """Cells whose coverage by ``mask`` passes ``cover`` (subject) or stays below it (background)."""
        if where == "all" or mask is None:
            return None if where == "all" else (np.ones(self.grid(h, w)[:2], bool) if where == "background" else np.zeros(self.grid(h, w)[:2], bool))
        rows, cols, cw, ch = self.grid(h, w)
        padded = np.zeros((rows * ch, cols * cw), np.float32)
        padded[:min(h, rows * ch), :min(w, cols * cw)] = mask[:rows * ch, :cols * cw].astype(np.float32)
        coverage = padded.reshape(rows, ch, cols, cw).mean(axis=(1, 3))
        return coverage >= cover if where == "subject" else coverage < cover


TEXT_WORDS = ("title", "logo", "credits", "text", "lettering", "typography", "caption", "subtitle", "intertitle", "poster", "sign reading")


def looks_like_text(caption: str) -> bool:
    """Shots that are mostly writing (titles, logos, credits) make poor tiles."""
    low = caption.lower()
    return any(word in low for word in TEXT_WORDS)


def units_from_search(api: str, queries: list[str], *, limit: int = 200) -> list[dict]:
    """Shots a library search returns for each query: film_id, unit number, times."""
    import urllib.parse
    import urllib.request
    found: dict[str, dict] = {}
    for q in queries:
        url = f"{api}/search?" + urllib.parse.urlencode({"q": q, "limit": limit})
        data = json.load(urllib.request.urlopen(url, timeout=120))
        for row in data.get("results", []):
            unit = row["unit_id"]
            if unit in found or looks_like_text(row.get("caption", "")):
                continue
            m = re.match(r"^(?P<film>[0-9a-f]{64})_(?P<n>\d+)$", unit)
            if not m:
                continue
            found[unit] = {"film_id": m["film"], "unit": int(m["n"]), "t_start": float(row["t_start"]), "t_end": float(row["t_end"]),
                           "title": row.get("film_title") or "", "caption": row.get("caption") or ""}
    return list(found.values())


def shot_times(assets_dir: Path, film_id: str, unit: int) -> tuple[float, float] | None:
    path = Path(assets_dir) / film_id / "shots.json"
    if not path.is_file():
        return None
    for shot in json.load(path.open(encoding="utf-8")).get("shots", []):
        if shot["shot_id"] == f"{film_id}_{unit:04d}":
            return float(shot["t_start"]), float(shot["t_end"])
    return None


def zoom_through(mosaic_frames: list[np.ndarray], cell: tuple[int, int], grid: tuple[int, int, int, int],
                 target_frames: list[np.ndarray], *, blend: int = 4) -> list[np.ndarray]:
    """Push into one cell until it fills the width, then hand over to the shot behind it.

    ``mosaic_frames`` play while the push happens; the zoom factor runs 1 -> columns with an ease,
    centred on the cell; the last ``blend`` frames cross to ``target_frames`` (same size)."""
    import cv2
    rows, cols, cw, ch = grid
    r, c = cell
    h, w = mosaic_frames[0].shape[:2]
    cx, cy = (c + 0.5) * cw, (r + 0.5) * ch
    n = len(mosaic_frames)
    out = []
    for i, frame in enumerate(mosaic_frames):
        z = 1 + (cols - 1) * _ease(i / max(n - 1, 1))
        vw, vh = w / z, h / z
        x0 = min(max(cx - vw / 2, 0), w - vw); y0 = min(max(cy - vh / 2, 0), h - vh)
        M = np.array([[z, 0, -x0 * z], [0, z, -y0 * z]], np.float32)
        view = cv2.warpAffine(frame, M, (w, h), flags=cv2.INTER_LINEAR)
        k = i - (n - blend)
        if k >= 0 and target_frames:
            a = _ease((k + 1) / blend)
            target = target_frames[min(k, len(target_frames) - 1)]
            view = (view.astype(np.float32) * (1 - a) + target.astype(np.float32) * a).astype(np.uint8)
        out.append(view)
    return out


# ---------------------------------------------------------------------------------------------
# Quad mosaic: cells follow the picture. Large tiles where the host is flat, small along its
# edges, the cell layout fixed for the clip so the surface is calm. Tone transfer carries the
# host's light into each tile and only a share of its colour, so tiles keep their own hue.

def quad_cells(detail: np.ndarray, *, min_size: int, max_size: int, threshold: float, aspect: float = 1.6) -> list[tuple[int, int, int, int]]:
    """Axis-aligned cells (x0, y0, w, h) in the tile aspect, split where ``detail`` (h, w float) is high."""
    h, w = detail.shape
    integral = np.pad(detail, ((1, 0), (1, 0))).cumsum(0).cumsum(1)
    sq = np.pad(detail ** 2, ((1, 0), (1, 0))).cumsum(0).cumsum(1)

    def std(x0, y0, cw, ch):
        x1, y1 = min(w, x0 + cw), min(h, y0 + ch)
        n = max(1, (x1 - x0) * (y1 - y0))
        s = integral[y1, x1] - integral[y0, x1] - integral[y1, x0] + integral[y0, x0]
        s2 = sq[y1, x1] - sq[y0, x1] - sq[y1, x0] + sq[y0, x0]
        return float(np.sqrt(max(s2 / n - (s / n) ** 2, 0.0)))

    out = []
    cw0, ch0 = max_size, int(round(max_size / aspect))
    stack = [(x, y, cw0, ch0) for y in range(0, h, ch0) for x in range(0, w, cw0)]
    while stack:
        x0, y0, cw, ch = stack.pop()
        if x0 >= w or y0 >= h:
            continue
        cw, ch = min(cw, w - x0), min(ch, h - y0)
        if cw // 2 >= min_size and std(x0, y0, cw, ch) > threshold:
            hw, hh = cw // 2, ch // 2
            stack.extend([(x0, y0, hw, hh), (x0 + hw, y0, cw - hw, hh), (x0, y0 + hh, hw, ch - hh), (x0 + hw, y0 + hh, cw - hw, ch - hh)])
        else:
            out.append((x0, y0, min(cw, w - x0), min(ch, h - y0)))
    return [c for c in out if c[2] > 0 and c[3] > 0]


def tone_transfer(tile: np.ndarray, host: np.ndarray, *, light: float = 1.0, colour: float = 0.3) -> np.ndarray:
    """Carry the host cell's light into the tile (mean and contrast of luma) and a share of its colour."""
    t = tile.astype(np.float32)
    hst = host.astype(np.float32)
    tl = t @ LUMA
    hl = hst @ LUMA
    t_mean, t_std = float(tl.mean()), float(tl.std()) + 1e-3
    h_mean, h_std = float(hl.mean()), float(hl.std()) + 1e-3
    target_l = (tl - t_mean) * min(2.5, max(0.4, h_std / t_std)) + h_mean
    new_l = tl * (1 - light) + target_l * light
    out = t * (new_l / np.maximum(tl, 1e-3))[..., None]
    shift = hst.reshape(-1, 3).mean(0) - out.reshape(-1, 3).mean(0)
    out = out + shift * colour
    return np.clip(out, 0, 255).astype(np.uint8)


@dataclass
class QuadMosaic:
    bank: TileBank
    min_size: int = 24
    max_size: int = 192
    threshold: float = 12.0        # luma std above which a cell splits
    light: float = 0.9             # how much of the host's light each tile takes
    colour: float = 0.3            # how much of the host's colour
    grout: int = 1
    moving: bool = False
    hold: int = 8
    drift: float = 0.1
    window_px: int = 220           # one tile per shot within this many px
    seed: int = 7
    cells: list | None = field(default=None, repr=False)
    assigned: dict = field(default_factory=dict, repr=False)
    _movers: dict = field(default_factory=dict, repr=False)
    _t: int = 0

    def layout(self, frames: list[np.ndarray], region: np.ndarray | None = None) -> None:
        """Fix the cell layout for the clip from the clip's mean detail; cells outside ``region`` are dropped."""
        import cv2
        sample = frames[::max(1, len(frames) // 8)]
        gray = np.mean([cv2.cvtColor(f, cv2.COLOR_RGB2GRAY).astype(np.float32) for f in sample], axis=0)
        cells = quad_cells(gray, min_size=self.min_size, max_size=self.max_size, threshold=self.threshold)
        if region is not None:
            cells = [c for c in cells if region[c[1]:c[1] + c[3], c[0]:c[0] + c[2]].mean() >= 0.5]
        self.cells = cells

    def _tile(self, index: int, size: tuple[int, int]) -> np.ndarray:
        if self.moving:
            if size not in self._movers:
                self._movers[size] = MovingTiles(self.bank, size)
            return self._movers[size].at(index, self._t, int(index) % 96)
        return self.bank.pixels(index, size)

    def step(self, frame: np.ndarray, *, keep: np.ndarray | None = None, progress: float | None = None,
             origin: tuple[float, float] | None = None) -> np.ndarray:
        import cv2
        if self.cells is None:
            self.layout([frame])
        bh, bw = self.bank.block_shape
        blocks = np.stack([block_of(frame[y:y + h, x:x + w], bw, bh) for x, y, w, h in self.cells])
        need = [i for i, c in enumerate(self.cells) if c not in self.assigned or self.assigned[c][1] >= self.hold]
        if need:
            cand, dist = self.bank.candidates(blocks[need])
            rng = np.random.RandomState(self.seed + self._t)
            for n, i in enumerate(need):
                c = self.cells[i]
                current = self.assigned.get(c)
                if current is not None:
                    cur_d = float(self.bank.distance(np.array([current[0]]), blocks[i:i + 1])[0])
                    if cur_d - dist[n, 0] <= self.drift:
                        continue
                cx, cy = c[0] + c[2] / 2, c[1] + c[3] / 2
                reach = max(self.window_px, 4 * c[2])
                nearby = {self.bank.unit_of(int(t))[:2] for o, (t, _a) in self.assigned.items()
                          if abs(o[0] + o[2] / 2 - cx) < reach and abs(o[1] + o[3] / 2 - cy) < reach}
                good = cand[n][dist[n] <= dist[n, 0] * 1.25 + 1e-6]
                options = ([t for t in good if self.bank.unit_of(int(t))[:2] not in nearby]
                           or [t for t in cand[n] if self.bank.unit_of(int(t))[:2] not in nearby] or [cand[n, 0]])
                self.assigned[c] = (int(options[rng.randint(len(options))]), 0)
        out = frame.copy()
        h, w = frame.shape[:2]
        order = None
        top = 1.0
        if progress is not None:
            oy, ox = (origin[1] if origin else 0.5), (origin[0] if origin else 0.5)
            rs = np.random.RandomState(self.seed)
            order = {c: np.hypot((c[0] + c[2] / 2) / w - ox, (c[1] + c[3] / 2) / h - oy) * 0.82 + rs.uniform(0, 0.18) for c in self.cells}
            top = max(order.values()) or 1.0
        for c in self.cells:
            index, age = self.assigned[c]
            self.assigned[c] = (index, age + 1)
            x, y, cw, ch = c
            scale = 1.0
            if order is not None:
                start = order[c] / top * 0.82
                if progress < start:
                    continue
                scale = _ease((progress - start) / 0.06)
            tile = self._tile(index, (cw, ch))
            host = frame[y:y + ch, x:x + cw]
            flat = 1.0 - min(1.0, float((host.astype(np.float32) @ LUMA).std()) / max(self.threshold, 1e-3))
            tile = tone_transfer(tile, host, light=self.light, colour=self.colour + (0.9 - self.colour) * flat)
            if self.grout:
                g = self.grout
                tile = tile.copy()
                tile[:g] = frame[y:y + g, x:x + cw]
                tile[-g:] = frame[y + ch - g:y + ch, x:x + cw]
                tile[:, :g] = frame[y:y + ch, x:x + g]
                tile[:, -g:] = frame[y:y + ch, x + cw - g:x + cw]
            if scale >= 0.999:
                out[y:y + ch, x:x + cw] = tile
            else:
                sw, sh = max(1, int(round(cw * scale))), max(1, int(round(ch * scale)))
                oy0, ox0 = y + (ch - sh) // 2, x + (cw - sw) // 2
                out[oy0:oy0 + sh, ox0:ox0 + sw] = cv2.resize(tile, (sw, sh), interpolation=cv2.INTER_AREA)
        if keep is not None:
            k = keep[..., None].astype(np.float32)
            out = (out.astype(np.float32) * (1 - k) + frame.astype(np.float32) * k).astype(np.uint8)
        self._t += 1
        return out
