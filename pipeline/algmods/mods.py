"""The treatments. Frames are H x W x 3 uint8 RGB; masks are H x W bool.

* ``time_stripes`` — stripes of the subject taken from earlier frames
  (alg.comp.mod's train), stripes radiating from a vanishing point or running
  along a direction; the background is untouched.
* ``DotTracker`` — feature dots: Shi-Tomasi corners carried by Lucas-Kanade
  flow and drawn as soft pastel discs, the look of his 645K "deresolution via
  feature detection" reel. Needs OpenCV (the ``algmods`` extra).
* ``quadtree`` — recursive subdivision where the picture varies; big flat cells
  elsewhere. ``quadtree_reveal`` drives the threshold with a 0..1 progress.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


# -- time stripes --------------------------------------------------------------

def stripe_ids(height: int, width: int, *, count: int, vanish: tuple[float, float] | None = None,
               direction: tuple[float, float] = (1.0, 0.0)) -> np.ndarray:
    """Integer stripe index per pixel: fan around ``vanish`` (output fractions), else bands along ``direction``."""
    ys, xs = np.mgrid[0:height, 0:width].astype(np.float32)
    if vanish is not None:
        # Bands are slices of the subject along its direction of travel, so each band is a line
        # through the vanishing point: index by the angle of that line, but only over the half-turn
        # the picture occupies, so the count means "slices across the subject", not around a point.
        vx, vy = vanish[0] * width, vanish[1] * height
        angle = np.arctan2(ys - vy, xs - vx)
        angle = np.where(angle < 0, angle + np.pi, angle)         # a line and its opposite are one band
        unit = angle / np.pi
    else:
        dx, dy = direction
        norm = float(np.hypot(dx, dy)) or 1.0
        proj = (xs * dx + ys * dy) / norm
        unit = (proj - proj.min()) / max(float(proj.max() - proj.min()), 1.0)
    return np.minimum((unit * count).astype(np.int32), count - 1)


def block_lags(height: int, width: int, *, vanish: tuple[float, float] | None, direction: tuple[float, float],
               bands: int, blocks: int, max_lag: int, seed: int = 7, mask: np.ndarray | None = None) -> np.ndarray:
    """A per-pixel lag field in blocks: ``bands`` slices along the direction of travel (lines through the
    vanishing point when there is one), each cut into ``blocks`` pieces along its length, every piece with
    its own lag. This is his train: a carriage shredded into pieces that each arrive at a different time.
    Along-band distance is measured from the vanishing point (perspective) or along ``direction``; the
    block length is set so blocks are roughly square where the subject is."""
    ys, xs = np.mgrid[0:height, 0:width].astype(np.float32)
    rng = np.random.RandomState(seed)
    if vanish is not None:
        vx, vy = vanish[0] * width, vanish[1] * height
        angle = np.arctan2(ys - vy, xs - vx)
        angle = np.where(angle < 0, angle + np.pi, angle)
        band = np.minimum((angle / np.pi * bands).astype(np.int32), bands - 1)
        along = np.hypot(xs - vx, ys - vy)
    else:
        dx, dy = direction
        norm = float(np.hypot(dx, dy)) or 1.0
        perp = (xs * dy - ys * dx) / norm                          # across the travel
        along = (xs * dx + ys * dy) / norm                         # along the travel
        band = np.minimum(((perp - perp.min()) / max(float(perp.max() - perp.min()), 1.0) * bands).astype(np.int32), bands - 1)
    ref = along[mask] if (mask is not None and mask.any()) else along
    lo, hi = float(np.percentile(ref, 2)), float(np.percentile(ref, 98))
    piece = np.minimum(((along - lo) / max(hi - lo, 1.0) * blocks).astype(np.int32), blocks - 1)
    piece = np.maximum(piece, 0)
    # each (band, piece) gets a lag; neighbouring bands are decorrelated, which is the shredded look
    table = rng.randint(0, max_lag + 1, (bands, blocks)).astype(np.int32)
    table[rng.uniform(size=table.shape) < 0.3] = 0                 # some pieces are on time
    return table[band, piece]


def stripe_lags(ids: np.ndarray, *, count: int, max_lag: int, pattern: str = "interleave") -> np.ndarray:
    """Frames of delay per stripe. ``interleave`` alternates near/far lags like a shuffled deck;
    ``ramp`` increases across the fan; ``random`` fixes a seeded shuffle."""
    if pattern == "ramp":
        table = np.linspace(0, max_lag, count).round().astype(np.int32)
    elif pattern == "segments":
        # a few wide runs with one lag each, like carriages arriving out of step, with jitter between runs
        rng = np.random.RandomState(7)
        runs = max(4, count // 8)
        edges = np.sort(rng.choice(np.arange(1, count), runs - 1, replace=False))
        table = np.zeros(count, np.int32)
        start = 0
        for k, edge in enumerate(list(edges) + [count]):
            table[start:edge] = int(round(max_lag * (0.15 + 0.85 * rng.uniform()))) if k % 2 else 0
            start = edge
    elif pattern == "random":
        table = np.random.RandomState(7).randint(0, max_lag + 1, count).astype(np.int32)
    else:
        table = np.array([(i * 7919) % (max_lag + 1) for i in range(count)], np.int32)
    return table[ids]


def box_blur(image: np.ndarray, radius: int) -> np.ndarray:
    """Separable box blur of a float32 H x W array (edge-replicated), NumPy only."""
    if radius <= 0:
        return image
    k = 2 * radius + 1
    padded = np.pad(image, radius, mode="edge")
    csum = np.cumsum(padded, axis=0)
    csum = np.concatenate([np.zeros((1, csum.shape[1]), csum.dtype), csum], axis=0)
    rows = (csum[k:] - csum[:-k]) / k
    csum = np.cumsum(rows, axis=1)
    csum = np.concatenate([np.zeros((csum.shape[0], 1), csum.dtype), csum], axis=1)
    return ((csum[:, k:] - csum[:, :-k]) / k).astype(np.float32)


def time_stripes(history: list[np.ndarray], mask: np.ndarray | None, lags: np.ndarray,
                 *, feather: int = 0) -> np.ndarray:
    """Compose the current frame (``history[-1]``) with each stripe of the subject taken ``lag`` frames back.

    ``history`` is oldest-first; a lag beyond it clamps to the oldest frame. Where
    ``mask`` is false (or None) the current frame is kept, so only the subject
    comes apart in time. ``feather`` blurs the mask edge in pixels.
    """
    current = history[-1]
    depth = len(history)
    lag = np.minimum(lags, depth - 1)
    rows, cols = np.indices(current.shape[:2])
    stack = np.stack(history, axis=0)                              # depth x H x W x 3
    remixed = stack[depth - 1 - lag, rows, cols]
    if mask is None:
        return remixed
    alpha = mask.astype(np.float32)
    if feather > 0:
        alpha = box_blur(alpha, feather)
    alpha = alpha[..., None]
    out = remixed.astype(np.float32) * alpha + current.astype(np.float32) * (1 - alpha)
    return np.clip(out + 0.5, 0, 255).astype(np.uint8)


# -- feature dots --------------------------------------------------------------

def pastel(rgb: np.ndarray, *, lift: float = 0.30, keep: float = 0.50, sat_floor: float = 0.12,
           sat_gain: float = 0.45) -> np.ndarray:
    """His "liberties with the colors": lift shadows, cap saturation, no pure whites. ``rgb`` float 0..1, N x 3."""
    mx = rgb.max(axis=1)
    mn = rgb.min(axis=1)
    sat = np.where(mx > 1e-6, (mx - mn) / np.maximum(mx, 1e-6), 0.0)
    new_value = lift + keep * mx
    new_sat = np.clip(sat_floor + sat_gain * sat, 0.0, 0.5)
    span = np.maximum(mx - mn, 1e-6)[:, None]
    unit = (mx[:, None] - rgb) / span                      # 0 at the max channel, 1 at the min channel
    chroma = (new_value * new_sat)[:, None]
    return np.clip(new_value[:, None] - chroma * unit, 0.0, 1.0)


#: Yoon Hyup (@ynhp): flat acrylic colours clustered from eight paintings' painted rows, plus the
#: pink, lavender, teal and red accents he uses sparingly (k-means merged them into the greys).
YNHP_PALETTE: tuple[tuple[int, int, int], ...] = (
    (233, 234, 231), (209, 206, 196), (227, 211, 154), (231, 193, 95), (234, 158, 31), (197, 151, 88),
    (170, 96, 37), (105, 74, 53), (137, 121, 107), (173, 157, 170), (104, 140, 186), (52, 79, 141),
    (92, 88, 129), (52, 64, 88), (226, 120, 140), (168, 140, 210), (90, 180, 170), (190, 58, 65),
    (70, 120, 90), (125, 165, 95),
)
YNHP_GROUND = (14, 16, 28)


def vivid(rgb: np.ndarray, palette: np.ndarray, *, mix: float = 0.65, min_value: float = 0.55,
          sat_gain: float = 1.25, sat_cap: float = 0.75, energy: np.ndarray | None = None,
          hot: np.ndarray | None = None, spread: float = 0.0, rng: np.random.RandomState | None = None) -> np.ndarray:
    """Opaque, flat colour: lift value to a floor (``min_value``, or ``0.22 + 0.5 * energy`` per dot when
    ``energy`` 0..1 is given, so lit spots glow and dim ones stay dim), boost saturation, then pull
    toward the nearest palette colour by ``mix``. ``hot`` 0..1 marks a light's core (bright against its
    surroundings): its value floor climbs to 0.9 and its saturation falls toward white. A bright pink
    wall in daylight is not hot. ``rgb`` and ``palette`` float 0..1."""
    mx = rgb.max(axis=1)
    mn = rgb.min(axis=1)
    sat = np.where(mx > 1e-6, (mx - mn) / np.maximum(mx, 1e-6), 0.0)
    hot = np.zeros(len(rgb), np.float32) if hot is None else np.clip(hot, 0, 1)
    if energy is None:
        floor = np.full(len(rgb), min_value, np.float32)
    else:
        floor = 0.12 + 0.6 * np.clip(energy, 0, 1)
    floor = np.maximum(floor, 0.9 * hot)
    new_value = np.maximum(mx, floor + (1 - floor) * mx * 0.6)
    new_sat = np.clip(sat * sat_gain, 0.0, sat_cap) * (1 - 0.7 * hot)
    span = np.maximum(mx - mn, 1e-6)[:, None]
    unit = (mx[:, None] - rgb) / span
    bright = np.clip(new_value[:, None] - (new_value * new_sat)[:, None] * unit, 0.0, 1.0)
    # nearest palette colour by chromaticity and value: a grey goes to a grey of the right value,
    # a dark green to the dark green, never a near-grey to whichever hue happens to be closest in RGB
    bc = bright / np.maximum(bright.sum(axis=1, keepdims=True), 1e-6)
    pc = palette / np.maximum(palette.sum(axis=1, keepdims=True), 1e-6)
    d = ((bc[:, None, :] - pc[None, :, :]) ** 2).sum(-1) * 3.0 + (bright.max(axis=1)[:, None] - palette.max(axis=1)[None, :]) ** 2
    if spread > 0 and len(palette) >= 3:
        # Not one palette colour per region but a draw among the nearest three, so a sky is cobalt,
        # lavender and sky blue interleaved (his skies), never a field of one blue.
        order = np.argsort(d, axis=1)[:, :3]
        dk = np.take_along_axis(d, order, axis=1)
        gap = dk - dk[:, :1]
        w = np.exp(-gap / (0.02 * spread))
        w = np.where(gap > 0.03, 0.0, w)                   # a neighbour in hue, never a green in a sunset sky
        w /= w.sum(axis=1, keepdims=True)
        rng = rng or np.random.RandomState(0)
        u = rng.uniform(size=(len(rgb), 1))
        pick = (u > np.cumsum(w, axis=1)).sum(axis=1)
        snapped = palette[np.take_along_axis(order, np.minimum(pick, 2)[:, None], axis=1)[:, 0]]
    else:
        snapped = palette[d.argmin(axis=1)]
    return np.clip(bright * (1 - mix) + snapped * mix, 0.0, 1.0)


def scene_ground(frame: np.ndarray, *, value: float = 0.09) -> tuple[int, int, int]:
    """A near-black ground in the scene's own shadow colour: the median of the darkest quarter of the
    frame, lifted to ``value`` with its hue kept and a little saturation added. A warm film gets a
    warm ground, a teal night a teal one; never one navy for everything."""
    f = frame.reshape(-1, 3).astype(np.float32) / 255.0
    luma = f @ np.array([0.299, 0.587, 0.114], np.float32)
    dark = f[luma <= np.percentile(luma, 25)]
    if not len(dark):
        return YNHP_GROUND
    c = np.median(dark, axis=0)
    mx = float(c.max())
    if mx < 1e-3:
        return (int(value * 255 * 0.9), int(value * 255 * 0.9), int(value * 255))
    chroma = c / mx
    chroma = 1.0 - (1.0 - chroma) * 0.8                   # a hint of the shadows' hue, not a coloured ground
    chroma = np.clip(chroma, 0.0, 1.0)
    rgb = np.clip(chroma * value, 0.0, 1.0)
    return tuple(int(round(float(v) * 255)) for v in rgb)


@dataclass
class DotTracker:
    """Painted dots that move with the surfaces under them.

    Seeded on texture (corners plus a jittered grid, never overlapping) and carried by a dense
    optical-flow field sampled at each dot, so neighbours move together. Where the local flow is
    unreliable (the frame border, flat or occluded areas) a dot follows the frame's global motion
    instead, so nothing ever freezes. A dot that crosses the frame edge leaves at once; one born
    at the edge arrives full-size (it was painted off-screen); one born inside grows into place;
    one whose spot loses its texture shrinks away; a hard cut clears the field and repaints it.
    Colour is sampled once at birth (pastel after alg.comp.mod, or palette-snapped after Yoon Hyup).
    """

    density: float = 3000.0            # target dots per megapixel (an upper bound; spacing usually binds first)
    radius_frac: float = 0.0065        # disc radius as a share of frame width
    quality: float = 0.0015
    fade_in: int = 5                   # frames a new dot takes to grow to full size
    fade_out: int = 4                  # frames a lost dot takes to shrink away
    grace: int = 4                     # frames a dot may sit on an untextured spot before it shrinks away
    flow_scale: float = 0.5            # flow is computed on the frame scaled by this
    flow_tolerance: float = 1.0        # forward-backward agreement (px at flow scale) for a trusted local flow
    edge_band: float = 0.03            # share of width near each edge where the global motion is used
    smooth: float = 0.25               # share of a dot's previous motion kept each frame (jitter damping)
    cut_floor: float = 0.15            # trusted-flow share below which the frame is a cut: clear and repaint
    cut_residual: float = 40.0         # mean |warped previous - current| (0..255) on trusted pixels above which it is a cut
    refresh_every: int = 4
    spacing: float = 0.0               # gap between dots as a share of their combined radii (0 = may touch)
    fill: bool = True                  # seed from a jittered grid on texture too, so the field is full from frame one
    fill_jitter: float = 0.18          # share of the lattice pitch each fill candidate is jittered by
    clahe: bool = True
    contrast_floor: float = 4.0        # raw-gray local std (0..255) a new dot's neighbourhood must reach;
                                       # equalisation must not turn sensor noise on flat dark areas into dots
    paint_floor: float = 0.3           # paint energy (light or colour, 0..1) below which a spot gets no mark
    subject_energy: float = 0.55       # paint energy guaranteed inside the segmented subject: it is what the picture is of
    texture_density: float = 0.25      # share of candidates kept on dark textured spots (building masses)
    thick_boost: float = 0.6           # extra radius inside large lit areas (a big sign gets fat marks)
    mass_blur: float = 10.0            # px blur that mass colours are sampled from (coherent colour per mass)
    dim_power: float = 1.75            # how hard dim areas are thinned (1 = linear in paint energy)
    subject_blur: float = 5.0          # px blur, confined to the subject mask, that subject colours come from
    dim_floor: float = 0.15            # natural energy below which a spot is void: no mark, even on texture or a subject
    parallax_floor: float = 1.0        # px: local flow is used only where it departs from the global motion by more
    background: tuple[int, int, int] = (0, 0, 0)
    soften: float = 0.9                # Gaussian sigma in px after drawing
    lift: float = 0.30
    keep: float = 0.50
    sat_floor: float = 0.12
    sat_gain: float = 0.32
    # "vivid" (Yoon Hyup) colour and size variety; palette None keeps the pastel (alg.comp.mod) colour
    palette: tuple[tuple[int, int, int], ...] | None = None
    palette_mix: float = 0.65
    size_jitter: float = 0.0           # log-normal sigma on each dot's radius (0 = all equal)
    bright_boost: float = 0.0          # extra radius share for a dot born on a bright pixel (lights grow)
    palette_spread: float = 0.0        # draw among the nearest palette colours (0 = nearest only)
    flat_boost: float = 0.0            # extra radius share where the picture is flat (big marks on a sky)
    flat_thin: float = 0.0             # share of lattice sites dropped where the picture is flat
    flat_gap: float = 0.0              # extra spacing (share of combined radii) between marks where the picture is flat
    dash: float = 0.0                  # extra elongation along structure where it is coherent (0 = round)
    seed: int = 7
    points: np.ndarray | None = field(default=None, repr=False)      # N x 2 float32
    colours: np.ndarray | None = field(default=None, repr=False)     # N x 3 float 0..1
    radii: np.ndarray | None = field(default=None, repr=False)       # N float, px
    vel: np.ndarray | None = field(default=None, repr=False)         # N x 2, last frame's motion
    misses: np.ndarray | None = field(default=None, repr=False)      # consecutive frames on the global motion
    bare: np.ndarray | None = field(default=None, repr=False)        # consecutive frames on an untextured spot
    ids: np.ndarray | None = field(default=None, repr=False)         # stable identity per dot
    crowded: np.ndarray | None = field(default=None, repr=False)     # consecutive frames squeezed by a neighbour
    _next_id: int = 0
    age: np.ndarray | None = field(default=None, repr=False)
    dying: np.ndarray | None = field(default=None, repr=False)       # 0 alive, else frames since lost
    _prev: np.ndarray | None = field(default=None, repr=False)        # previous equalised gray (flow)
    _raw: np.ndarray | None = field(default=None, repr=False)         # current raw gray (texture, photometry)
    _prev_raw: np.ndarray | None = field(default=None, repr=False)
    _paint: np.ndarray | None = field(default=None, repr=False)       # placement energy 0..1 (subject raised)
    _natural: np.ndarray | None = field(default=None, repr=False)     # the spot's own energy 0..1: colour follows this
    _subject: np.ndarray | None = field(default=None, repr=False)     # this frame's subject mask
    _subject_rgb: np.ndarray | None = field(default=None, repr=False) # mask-confined blurred colour, float 0..1
    _hot: np.ndarray | None = field(default=None, repr=False)         # 0..1 light cores: bright against surroundings
    _lattice_offset: tuple[float, float] = (0.0, 0.0)                 # fill lattice origin, carried by the global motion
    _thick: np.ndarray | None = field(default=None, repr=False)       # 0..1 depth inside lit areas
    _detail: np.ndarray | None = field(default=None, repr=False)      # 0..1 local detail (gradient), for flat_boost
    _coh: np.ndarray | None = field(default=None, repr=False)         # 0..1 structure coherence, for dashes
    _orient: np.ndarray | None = field(default=None, repr=False)      # degrees, the structure's tangent
    elong: np.ndarray | None = field(default=None, repr=False)        # N, 1 = round, more = a dash
    angle: np.ndarray | None = field(default=None, repr=False)        # N degrees
    _frame_index: int = 0
    _clahe: object = field(default=None, repr=False)
    _dis: object = field(default=None, repr=False)
    last_motion: dict = field(default_factory=dict, repr=False)      # diagnostics of the latest frame

    def _gray(self, frame: np.ndarray) -> np.ndarray:
        import cv2
        gray = cv2.cvtColor(frame, cv2.COLOR_RGB2GRAY)
        self._raw = gray
        # Where the paint goes: light (luma) or colour (saturation times value), smoothed so a neon
        # tube's glow counts as a region, not a line. Thickness is the depth inside lit areas.
        f = frame.astype(np.float32) / 255.0
        mx, mn = f.max(axis=2), f.min(axis=2)
        sat = np.where(mx > 1e-3, (mx - mn) / np.maximum(mx, 1e-3), 0.0)
        paint = np.maximum((gray.astype(np.float32) / 255.0) ** 0.8, sat * mx)
        self._paint = cv2.GaussianBlur(paint, (0, 0), 2.0)
        self._natural = self._paint
        # A light's core is bright against its surroundings; a lit wall in daylight is bright everywhere
        # and therefore not hot.
        local = self._paint - cv2.GaussianBlur(self._paint, (0, 0), 25.0)
        self._hot = (np.clip(local / 0.25, 0, 1) * np.clip((self._paint - 0.5) / 0.3, 0, 1)).astype(np.float32)
        lit = (self._paint > 0.45).astype(np.uint8)
        dist = cv2.distanceTransform(lit, cv2.DIST_L2, 3) if lit.any() else np.zeros_like(paint)
        self._thick = np.clip(dist / (4.0 * self._radius(frame.shape[1])), 0.0, 1.0).astype(np.float32)
        if self.flat_boost > 0 or self.dash > 0 or self.flat_thin > 0 or self.flat_gap > 0:
            g = cv2.GaussianBlur(gray.astype(np.float32) / 255.0, (0, 0), 2.0)      # grain is not detail
            gx = cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3)
            gy = cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3)
            mag = cv2.GaussianBlur(np.hypot(gx, gy), (0, 0), 4.0)
            # Absolute scale (luma gradient per px): a smooth sunset sky is flat whether or not the rest of
            # the frame has edges; a scene-relative scale called Lawrence's sky detailed.
            self._detail = np.clip(mag / 0.04, 0.0, 1.0).astype(np.float32)
            jxx = cv2.GaussianBlur(gx * gx, (0, 0), 5.0)
            jyy = cv2.GaussianBlur(gy * gy, (0, 0), 5.0)
            jxy = cv2.GaussianBlur(gx * gy, (0, 0), 5.0)
            self._coh = (np.sqrt((jxx - jyy) ** 2 + 4 * jxy ** 2) / np.maximum(jxx + jyy, 1e-6)).astype(np.float32)
            self._coh *= (np.clip(mag / max(float(np.percentile(mag, 80)), 1e-4), 0, 1)).astype(np.float32)   # flat areas have no structure
            grad_angle = 0.5 * np.arctan2(2 * jxy, jxx - jyy)
            self._orient = np.degrees(grad_angle + np.pi / 2).astype(np.float32)                              # along the edge
        if self.clahe:
            if self._clahe is None:
                self._clahe = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8))
            gray = self._clahe.apply(gray)
        return gray

    def _textured(self, pts: np.ndarray, floor: float | None = None) -> np.ndarray:
        """Which points sit on real local contrast in the raw gray (7x7 std at or above ``floor``)."""
        import cv2
        raw = self._raw.astype(np.float32)
        mean = cv2.blur(raw, (7, 7))
        sq = cv2.blur(raw * raw, (7, 7))
        std = np.sqrt(np.maximum(sq - mean * mean, 0.0))
        h, w = raw.shape
        xs = np.clip(pts[:, 0].round().astype(int), 0, w - 1)
        ys = np.clip(pts[:, 1].round().astype(int), 0, h - 1)
        return std[ys, xs] >= (self.contrast_floor if floor is None else floor)

    def _at(self, image: np.ndarray, pts: np.ndarray) -> np.ndarray:
        h, w = image.shape[:2]
        xs = np.clip(pts[:, 0].round().astype(int), 0, w - 1)
        ys = np.clip(pts[:, 1].round().astype(int), 0, h - 1)
        return image[ys, xs]

    def _on_paint(self, pts: np.ndarray, *, floor: float | None = None) -> np.ndarray:
        """A spot is paintable when it is lit or coloured, or sits on texture (a dark mass's edge)."""
        return (self._at(self._paint, pts) >= self.paint_floor) | self._textured(pts, floor=floor)

    def _target(self, h: int, w: int) -> int:
        return int(self.density * h * w / 1e6)

    def _radius(self, w: int) -> float:
        return max(1.5, self.radius_frac * w)

    def _sample_colours(self, frame: np.ndarray, pts: np.ndarray) -> np.ndarray:
        """A mass shares one colour (sampled coarse, so neighbours agree); a light's core keeps its own."""
        import cv2
        fine = self._at(cv2.GaussianBlur(frame, (0, 0), 1.5), pts).astype(np.float32) / 255.0
        if self.palette is None:
            return pastel(fine, lift=self.lift, keep=self.keep, sat_floor=self.sat_floor, sat_gain=self.sat_gain)
        coarse = self._at(cv2.GaussianBlur(frame, (0, 0), self.mass_blur), pts).astype(np.float32) / 255.0
        energy = self._at(self._natural, pts)                   # colour follows the spot's own light, never a boost
        hot = self._at(self._hot, pts)
        rgb = fine * hot[:, None] + coarse * (1 - hot[:, None])
        dim = (energy < self.paint_floor)[:, None]               # a dark structure keeps its own ink, no sky bleeding in
        rgb = np.where(dim, fine, rgb)
        if self._subject is not None and self._subject_rgb is not None:
            on_subject = self._at(self._subject, pts)[:, None] & (hot[:, None] < 0.5)
            rgb = np.where(on_subject, self._at(self._subject_rgb, pts), rgb)
        return vivid(rgb, np.array(self.palette, np.float32) / 255.0, mix=self.palette_mix, energy=energy, hot=hot,
                     spread=self.palette_spread, rng=np.random.RandomState(self.seed + 7 + self._frame_index))
        return pastel(rgb, lift=self.lift, keep=self.keep, sat_floor=self.sat_floor, sat_gain=self.sat_gain)  # pragma: no cover

    def _sample_radii(self, frame: np.ndarray, pts: np.ndarray) -> np.ndarray:
        """Per-dot radius: the base radius, log-normal jitter, and a boost on bright source pixels."""
        base = self._radius(frame.shape[1])
        rng = np.random.RandomState(self.seed + self._frame_index)
        r = np.full(len(pts), base, np.float32)
        if self.size_jitter > 0:
            r *= np.exp(rng.normal(0.0, self.size_jitter, len(pts))).astype(np.float32)
        if self.bright_boost > 0 or self.thick_boost > 0:
            r *= 1.0 + self.bright_boost * self._at(self._paint, pts) + self.thick_boost * self._at(self._thick, pts)
        if self.flat_boost > 0 and self._detail is not None:
            r *= 1.0 + self.flat_boost * (1.0 - self._at(self._detail, pts))
        return np.clip(r, 1.0, base * 4.0)

    def _spawn(self, frame: np.ndarray, gray: np.ndarray, detect_mask: np.ndarray | None) -> None:
        """Detect corners; keep those whose grid cell holds no live dot, up to the target count."""
        import cv2
        h, w = gray.shape
        target = self._target(h, w)
        alive = 0 if self.points is None else int((self.dying == 0).sum())
        if alive >= target:
            return
        radius = self._radius(w)
        cell = max(2.0, radius * 1.6)
        allowed = None if detect_mask is None else detect_mask.astype(np.uint8) * 255
        fresh = cv2.goodFeaturesToTrack(gray, maxCorners=target * 2, qualityLevel=self.quality,
                                        minDistance=max(2, int(radius * 1.2)), mask=allowed, blockSize=5)
        if fresh is None:
            return
        fresh = fresh.reshape(-1, 2).astype(np.float32)
        fresh = fresh[self._on_paint(fresh) & (self._at(self._natural, fresh) >= self.dim_floor)]
        if self.fill:
            # Corners alone fill a field slowly (a few hundred per frame). Painted density comes from
            # every textured spot, so add a jittered grid of candidates after the corners; the
            # non-overlap check below is what stops a grid pattern from showing.
            rng = np.random.RandomState(self.seed + 1000 + self._frame_index)
            pitch = max(2.0, radius * 1.6)
            # A hex lattice with a small jitter: a flat wall reads as rows of marks, not scatter. The
            # lattice drifts with the global motion so a still surface keeps the same rows frame to frame.
            ox, oy = self._lattice_offset
            row_h = pitch * 0.866
            gy, gx = np.mgrid[(oy % row_h):h:row_h, (ox % pitch):w:pitch]
            gx = gx + (np.round((gy - oy % row_h) / row_h) % 2) * (pitch / 2)
            grid = np.stack([gx.ravel(), gy.ravel()], axis=1).astype(np.float32)
            # Each lattice site owns a stable random number (hashed from its indices, which ride the
            # drifting lattice), so a thinned flat area keeps the same sparse subset of sites every frame
            # rather than re-rolling until the target count fills it anyway.
            ix = np.round((grid[:, 0] - ox % pitch) / pitch).astype(np.int64)
            iy = np.round((grid[:, 1] - oy % row_h) / row_h).astype(np.int64)
            site_u = (((ix * 73856093) ^ (iy * 19349663) ^ (self.seed * 83492791)) & 0xFFFF).astype(np.float32) / 65535.0
            grid += rng.uniform(-self.fill_jitter * pitch, self.fill_jitter * pitch, grid.shape).astype(np.float32)
            grid[:, 0] = np.clip(grid[:, 0], 0, w - 1)
            grid[:, 1] = np.clip(grid[:, 1], 0, h - 1)
            if detect_mask is not None:
                inside = detect_mask[grid[:, 1].astype(int), grid[:, 0].astype(int)]
                grid, site_u = grid[inside], site_u[inside]
            energy = self._at(self._paint, grid)
            natural = self._at(self._natural, grid)
            dark_texture = self._textured(grid) & (energy < self.paint_floor)
            chance = np.clip((energy - self.paint_floor) / (1 - self.paint_floor), 0, 1) ** self.dim_power
            chance = np.where(dark_texture, self.texture_density, chance)
            chance = np.where(natural < self.dim_floor, 0.0, chance)        # black is void
            if self.flat_thin > 0 and self._detail is not None:
                # A flat sky is left mostly to the ground, marks cluster where there is something to
                # describe; the eye fills the rest in, which is the illusion.
                detail = self._at(self._detail, grid)
                flat_keep = 1.0 - self.flat_thin * (1.0 - detail)
                grid, chance, site_u = grid[site_u < flat_keep], chance[site_u < flat_keep], site_u[site_u < flat_keep]
            grid = grid[rng.uniform(size=len(grid)) < chance]
            rng.shuffle(grid)
            fresh = np.concatenate([fresh, grid]) if len(fresh) else grid
        if not len(fresh):
            return
        # Non-overlap: a new dot must clear every live dot by their combined radii plus the gap.
        # Candidates are hashed on a grid of the largest possible diameter and checked against neighbours.
        fresh_radii = self._sample_radii(frame, fresh)
        max_r = float(max(radius * 4.0, fresh_radii.max()))
        cell = max(2.0, 2 * max_r * (1 + self.spacing + self.flat_gap))
        cols, rows = int(np.ceil(w / cell)) + 1, int(np.ceil(h / cell)) + 1
        buckets: dict[tuple[int, int], list[tuple[float, float, float]]] = {}

        def key(x: float, y: float) -> tuple[int, int]:
            return (min(rows - 1, int(y / cell)), min(cols - 1, int(x / cell)))

        def clear(x: float, y: float, r: float, gap: float = 0.0) -> bool:
            kr, kc = key(x, y)
            for dr in (-1, 0, 1):
                for dc in (-1, 0, 1):
                    for ox, oy, orad in buckets.get((kr + dr, kc + dc), ()):
                        if (ox - x) ** 2 + (oy - y) ** 2 < ((r + orad) * (1 + self.spacing + gap)) ** 2:
                            return False
            return True

        # Marks on a flat surface keep their distance: a sky is a few marks on the ground, not a pavement.
        gaps = (self.flat_gap * (1.0 - self._at(self._detail, fresh))) if (self.flat_gap > 0 and self._detail is not None) else np.zeros(len(fresh), np.float32)

        if self.points is not None and len(self.points):
            live = self.dying == 0
            for (x, y), r in zip(self.points[live], self.radii[live]):
                buckets.setdefault(key(float(x), float(y)), []).append((float(x), float(y), float(r)))
        keep: list[int] = []
        room = target - alive
        for i, (p, r) in enumerate(zip(fresh, fresh_radii)):
            x, y, r = float(p[0]), float(p[1]), float(r)
            if not clear(x, y, r, float(gaps[i])):
                continue
            buckets.setdefault(key(x, y), []).append((x, y, r))
            keep.append(i)
            if len(keep) >= room:
                break
        if not keep:
            return
        born = fresh[keep]
        colours = self._sample_colours(frame, born)
        radii = fresh_radii[keep]
        n = len(born)
        if self.dash > 0 and self._coh is not None:
            coh = self._at(self._coh, born)
            elong = np.where(coh > 0.45, 1.0 + self.dash * np.clip((coh - 0.45) / 0.55, 0, 1), 1.0).astype(np.float32)
            angle = self._at(self._orient, born).astype(np.float32)
        else:
            elong, angle = np.ones(n, np.float32), np.zeros(n, np.float32)
        ids = np.arange(self._next_id, self._next_id + n)
        self._next_id += n
        if self.points is None or not len(self.points):
            # The opening population appears at full strength: a cut must land on dots, not on black.
            self.points, self.colours, self.radii = born, colours, radii
            self.age, self.dying = np.full(n, self.fade_in, int), np.zeros(n, int)
            self.vel, self.misses, self.bare = np.zeros((n, 2), np.float32), np.zeros(n, int), np.zeros(n, int)
            self.ids, self.crowded = ids, np.zeros(n, int)
            self.elong, self.angle = elong, angle
        else:
            # A dot born in the edge band is a mark arriving from off-screen: already full-size.
            band = self.edge_band * w
            arriving = (born[:, 0] < band) | (born[:, 0] > w - band) | (born[:, 1] < band) | (born[:, 1] > h - band)
            self.points = np.concatenate([self.points, born])
            self.colours = np.concatenate([self.colours, colours])
            self.radii = np.concatenate([self.radii, radii])
            self.age = np.concatenate([self.age, np.where(arriving, self.fade_in, 0)])
            self.dying = np.concatenate([self.dying, np.zeros(n, int)])
            self.vel = np.concatenate([self.vel, np.zeros((n, 2), np.float32)])
            self.misses = np.concatenate([self.misses, np.zeros(n, int)])
            self.bare = np.concatenate([self.bare, np.zeros(n, int)])
            self.ids = np.concatenate([self.ids, ids])
            self.crowded = np.concatenate([self.crowded, np.zeros(n, int)])
            self.elong = np.concatenate([self.elong, elong])
            self.angle = np.concatenate([self.angle, angle])

    def _keep(self, mask: np.ndarray) -> None:
        """Drop every dot where ``mask`` is False."""
        if mask.all():
            return
        for name in ("points", "colours", "radii", "age", "dying", "vel", "misses", "bare", "ids", "crowded", "elong", "angle"):
            setattr(self, name, getattr(self, name)[mask])

    def _motion(self, gray: np.ndarray):
        """Dense flow from the previous frame (DIS, at ``flow_scale``), a trust map from forward-backward
        agreement, and a global similarity fit of the trusted flow. Returns (flow, trusted, affine, scale)."""
        import cv2
        s = self.flow_scale
        prev = cv2.resize(self._prev, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
        cur = cv2.resize(gray, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
        if self._dis is None:
            self._dis = cv2.DISOpticalFlow_create(cv2.DISOPTICAL_FLOW_PRESET_MEDIUM)
        fwd = self._dis.calc(prev, cur, None)
        bwd = self._dis.calc(cur, prev, None)
        hs, ws = fwd.shape[:2]
        gy, gx = np.mgrid[0:hs, 0:ws].astype(np.float32)
        back_at = cv2.remap(bwd, gx + fwd[..., 0], gy + fwd[..., 1], cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
        err = np.linalg.norm(fwd + back_at, axis=2)
        trusted = err < self.flow_tolerance + 0.05 * np.linalg.norm(fwd, axis=2)
        # Photometric check on the raw gray (equalisation is tile-dependent, so it would mismatch on a
        # plain shift): where the flow is trusted, the previous frame carried by it must look like the
        # current one. Self-similar texture can fool the agreement test alone.
        # The current frame is the previous one carried by the backward flow: cur(x) = prev(x + bwd(x)).
        raw_prev = cv2.resize(self._prev_raw, None, fx=s, fy=s, interpolation=cv2.INTER_AREA).astype(np.float32)
        raw_cur = cv2.resize(self._raw, None, fx=s, fy=s, interpolation=cv2.INTER_AREA).astype(np.float32)
        fwd_at = cv2.remap(fwd, gx + bwd[..., 0], gy + bwd[..., 1], cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
        trusted_cur = np.linalg.norm(bwd + fwd_at, axis=2) < self.flow_tolerance + 0.05 * np.linalg.norm(bwd, axis=2)
        warped = cv2.remap(raw_prev, gx + bwd[..., 0], gy + bwd[..., 1], cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
        residual = float(np.abs(warped - raw_cur)[trusted_cur].mean()) if trusted_cur.any() else 255.0
        step = max(4, min(hs, ws) // 40)
        ys, xs = np.mgrid[step // 2:hs:step, step // 2:ws:step]
        sample = trusted[ys, xs]
        src = np.stack([xs[sample], ys[sample]], axis=1).astype(np.float32)
        affine = None
        if len(src) >= 12:
            dst = src + fwd[ys[sample], xs[sample]]
            affine, _inliers = cv2.estimateAffinePartial2D(src, dst, method=cv2.RANSAC, ransacReprojThreshold=1.5)
        trusted_share = float(trusted.mean())
        self.last_motion = {"trusted": trusted_share, "residual": residual, "global": affine is not None,
                            "scale": None if affine is None else float(np.hypot(affine[0, 0], affine[1, 0])),
                            "cut": trusted_share < self.cut_floor or residual > self.cut_residual,
                            "shift": None if affine is None else (float(affine[0, 2]) / s, float(affine[1, 2]) / s)}
        return fwd, trusted, affine, s

    def _track(self, gray: np.ndarray) -> bool:
        """Move every dot with the surface under it. Returns True when the frame is a cut."""
        import cv2
        if self.points is None or not len(self.points) or self._prev is None:
            return False
        fwd, trusted, affine, s = self._motion(gray)
        if self.last_motion["cut"]:
            self.points = None                              # a new picture: clear and repaint
            return True
        h, w = gray.shape
        pts = self.points
        mx = (pts[:, 0] * s).astype(np.float32)[None, :]
        my = (pts[:, 1] * s).astype(np.float32)[None, :]
        local = cv2.remap(fwd, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)[0] / s
        trust = cv2.remap(trusted.astype(np.float32), mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)[0] > 0.5
        band = self.edge_band * w
        in_band = (pts[:, 0] < band) | (pts[:, 0] > w - band) | (pts[:, 1] < band) | (pts[:, 1] > h - band)
        use_local = trust & ~in_band
        if affine is not None:
            moved = (np.c_[pts * s, np.ones(len(pts), np.float32)] @ affine.T) / s
            global_motion = (moved - pts).astype(np.float32)
            # A rigid surface moves as one. Per-dot flow noise would otherwise random-walk neighbours
            # into each other, so local flow is used only where it truly departs from the global motion.
            use_local &= np.linalg.norm(local - global_motion, axis=1) > self.parallax_floor
        else:
            global_motion = self.vel                        # no fit this frame: keep going as before
        motion = np.where(use_local[:, None], local, global_motion).astype(np.float32)
        if affine is not None:
            shift = (affine[:, 2] / s)
            self._lattice_offset = (self._lattice_offset[0] + float(shift[0]), self._lattice_offset[1] + float(shift[1]))
        self.vel = motion * (1 - self.smooth) + self.vel * self.smooth
        self.points = pts + self.vel
        self.misses = np.where(use_local, 0, self.misses + 1)
        # Marks that crossed the frame edge have left the picture.
        r = self.radii
        inside = ((self.points[:, 0] > -r) & (self.points[:, 0] < w + r) &
                  (self.points[:, 1] > -r) & (self.points[:, 1] < h + r))
        self._keep(inside)
        if not len(self.points):
            return False
        # When the surface shrinks (a zoom out, a receding plane) marks crowd; the ones squeezed below
        # 60% of their size for longer than the grace are dropped rather than drawn ever smaller.
        if len(self.points) > 1:
            from scipy.spatial import cKDTree
            dist, nn = cKDTree(self.points).query(self.points, k=2)
            share = dist[:, 1] * self.radii / np.maximum(self.radii + self.radii[nn[:, 1]], 1e-6)
            squeezed = share < 0.6 * self.radii
            self.crowded = np.where(squeezed, self.crowded + 1, 0)
            # of a crowded pair, only the younger yields
            younger = self.age <= self.age[nn[:, 1]]
            self.dying[(self.crowded > self.grace) & younger & (self.dying == 0)] = 1
        # A mark is on something as long as either the local flow still finds it or its spot still
        # has texture (at half the birth floor, so a mark does not flicker at the threshold). When both
        # have been lost for longer than the grace, the thing it sat on is gone: it shrinks away.
        textured = self._on_paint(self.points, floor=self.contrast_floor / 2)
        self.bare = np.where(textured, 0, self.bare + 1)
        gone = (self.bare > self.grace) & (self.misses > self.grace) & (self.dying == 0)
        self.dying[gone] = 1
        return False

    def _cull(self) -> None:
        if self.points is None:
            return
        self._keep(~(self.dying > self.fade_out))

    def step(self, frame: np.ndarray, protect: np.ndarray | None = None, *,
             detect_mask: np.ndarray | None = None, subject: np.ndarray | None = None) -> np.ndarray:
        gray = self._gray(frame)
        self._subject, self._subject_rgb = None, None
        if subject is not None and subject.any():
            import cv2
            self._paint = np.where(subject, np.maximum(self._paint, self.subject_energy), self._paint).astype(np.float32)
            # Subject colour is coherent locally and confined to the subject: a blur that never crosses the
            # mask edge, so the ground does not bleed in and a face keeps its own colour next to a red dress.
            weight = subject.astype(np.float32)
            num = cv2.GaussianBlur(frame.astype(np.float32) * weight[..., None], (0, 0), self.subject_blur)
            den = cv2.GaussianBlur(weight, (0, 0), self.subject_blur)[..., None]
            self._subject = subject
            self._subject_rgb = (num / np.maximum(den, 1e-3) / 255.0).astype(np.float32)
        self._track(gray)
        h, w = gray.shape
        alive = 0 if self.points is None else int((self.dying == 0).sum())
        # Top up on the cadence, and at once after a mass loss (a blurred frame, a whip), so the
        # field never empties for more than one frame.
        if self._frame_index % self.refresh_every == 0 or alive < 0.7 * self._target(h, w):
            self._spawn(frame, gray, detect_mask)
        if self.points is not None and len(self.points):
            self.age[self.dying == 0] += 1
            self.dying[self.dying > 0] += 1
        self._cull()
        self._prev, self._prev_raw = gray, self._raw
        self._frame_index += 1
        return self.draw(frame, protect)

    def draw(self, frame: np.ndarray, protect: np.ndarray | None) -> np.ndarray:
        import cv2
        h, w = frame.shape[:2]
        canvas = np.zeros((h, w, 3), np.float32)
        canvas[:] = np.array(self.background, np.float32) / 255.0
        if self.points is not None and len(self.points):
            # Dots grow into place (ease-out) and shrink away, at full colour throughout: a painted
            # mark being set down, never a ghost fading in.
            t_in = np.clip(self.age / max(self.fade_in, 1), 0.0, 1.0)
            grow = 1.0 - (1.0 - t_in) ** 2
            t_out = np.where(self.dying > 0, np.clip(self.dying / max(self.fade_out, 1), 0.0, 1.0), 0.0)
            shrink = 1.0 - t_out ** 2
            scale = grow * shrink
            drawn = self.radii * scale
            if len(self.points) > 1:
                # Marks never overlap: when motion has crowded two neighbours, each yields its share.
                from scipy.spatial import cKDTree
                dist, nn = cKDTree(self.points).query(self.points, k=2)
                gap, other = dist[:, 1], nn[:, 1]
                allowed = gap * self.radii / np.maximum(self.radii + self.radii[other], 1e-6)
                drawn = np.minimum(drawn, np.maximum(allowed, 0.4 * drawn))
            # small dots on top so they stay visible inside the packed areas
            for i in np.argsort(-drawn):
                r = int(round(float(drawn[i])))
                if r < 1:
                    continue
                colour = tuple(float(c) for c in self.colours[i])
                centre = (int(round(self.points[i, 0])), int(round(self.points[i, 1])))
                e = float(self.elong[i]) if self.elong is not None else 1.0
                if e > 1.05:
                    # a dash: the same area as the round mark, stretched along the structure under it
                    a, b = max(1, int(round(r * np.sqrt(e)))), max(1, int(round(r / np.sqrt(e))))
                    cv2.ellipse(canvas, centre, (a, b), float(self.angle[i]), 0, 360, colour, -1, lineType=cv2.LINE_AA)
                else:
                    cv2.circle(canvas, centre, r, colour, -1, lineType=cv2.LINE_AA)
            if self.soften > 0:
                canvas = cv2.GaussianBlur(canvas, (0, 0), self.soften)
        out = np.clip(canvas * 255 + 0.5, 0, 255).astype(np.uint8)
        if protect is not None:
            out[protect] = frame[protect]
        return out


# -- quadtree ------------------------------------------------------------------

def _integral(image: np.ndarray) -> np.ndarray:
    """(H+1) x (W+1) x C summed-area table."""
    table = np.cumsum(np.cumsum(image, axis=0), axis=1)
    return np.pad(table, ((1, 0), (1, 0), (0, 0)))


def quadtree(frame: np.ndarray, *, threshold: float, min_size: int = 8, max_size: int | None = None,
             line: tuple[int, int, int] | None = None) -> np.ndarray:
    """Fill each leaf cell with its mean colour; split while the cell's colour spread exceeds ``threshold``."""
    height, width = frame.shape[:2]
    out = np.empty_like(frame)
    pic = frame.astype(np.float64)
    integral, integral_sq = _integral(pic), _integral(pic ** 2)

    def stats(y0: int, x0: int, y1: int, x1: int) -> tuple[np.ndarray, float]:
        area = float((y1 - y0) * (x1 - x0))
        s = integral[y1, x1] - integral[y0, x1] - integral[y1, x0] + integral[y0, x0]
        q = integral_sq[y1, x1] - integral_sq[y0, x1] - integral_sq[y1, x0] + integral_sq[y0, x0]
        mean = s / area
        var = np.maximum(q / area - mean ** 2, 0.0)
        return mean, float(np.sqrt(var).mean())

    stack = [(0, 0, height, width)]
    while stack:
        y0, x0, y1, x1 = stack.pop()
        h, w = y1 - y0, x1 - x0
        mean, spread = stats(y0, x0, y1, x1)
        big = max_size is not None and max(h, w) > max_size
        if (big or spread > threshold) and min(h, w) >= min_size * 2:
            ym, xm = y0 + h // 2, x0 + w // 2
            stack += [(y0, x0, ym, xm), (y0, xm, ym, x1), (ym, x0, y1, xm), (ym, xm, y1, x1)]
            continue
        out[y0:y1, x0:x1] = np.clip(mean + 0.5, 0, 255).astype(np.uint8)
        if line is not None:
            out[y0:y1, x0:x0 + 1] = line
            out[y0:y0 + 1, x0:x1] = line
    return out


def quadtree_reveal(frame: np.ndarray, progress: float, *, coarse: float = 64.0, fine: float = 2.0,
                    min_size: int = 6) -> np.ndarray:
    """Quadtree whose threshold falls from ``coarse`` to ``fine`` as ``progress`` goes 0..1; at 1 the frame is returned."""
    p = float(np.clip(progress, 0.0, 1.0))
    if p >= 1.0:
        return frame
    threshold = coarse * (1 - p) ** 2 + fine * p
    return quadtree(frame, threshold=threshold, min_size=min_size)


# -- helpers --------------------------------------------------------------------
