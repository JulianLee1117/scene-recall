"""Grafts: a landmark-aligned piece of another shot set on the host, as a plan any agent can write.

A graft is four decisions, each plain data:

* **window** — where on the host: a feature box in landmark units (``eye-l``, ``mouth``, ...), the
  whole face, the segmented subject, or the whole frame; hard-edged or feathered.
* **donor** — which shot supplies the pixels, aligned to the host by shared keypoints (face:
  eyes and nose). Donors come from a pool (a search, pinned) and are chosen per graft by aligned
  structure and tone, or named explicitly.
* **light** — how much of the host's light and colour the graft takes (a sticker takes little, a
  graft takes most).
* **timing** — when it is there and how it enters and leaves: ``cut``, ``grow``, ``slide``,
  ``fade``; holds in frames; events placed by hand, on a beat list, or on onsets.

The same primitive renders hanajo's memory patches (one feature at a time, a sticker), a composite
face (many windows at once, grafted), a face-scoped cut-in (the whole donor face in the host's
framing) and anything an editor wants to schedule on a song. Faces now; the landmark kinds extend
to bodies (shoulders, hips) and hands (wrists) with the same code.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from pipeline.algmods.composite import PATCHES, DonorFace, FacePoints, _luma, _similarity, cell_polygon

Window = Literal["eye-l", "eye-r", "eyes", "nose", "mouth", "brow", "face", "subject", "frame"]
Enter = Literal["cut", "grow", "slide-l", "slide-r", "slide-u", "slide-d", "fade"]


class Graft(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    window: Window = "eye-l"
    donor: int | None = None                       # pool index; None = choose by fit for this window
    start: int = Field(ge=0)                       # frame
    hold: int = Field(default=8, ge=1, le=600)     # frames fully present
    enter: Enter = "cut"
    leave: Enter = "cut"
    ramp: int = Field(default=3, ge=0, le=30)      # frames for enter/leave
    light: float = Field(default=.5, ge=0, le=1)   # host light carried into the graft
    colour: float = Field(default=0, ge=0, le=1)   # host colour carried in
    feather: int = Field(default=0, ge=0, le=64)   # px soft edge; 0 = hard sticker edge
    grow: float = Field(default=1, ge=.5, le=2)    # window scale


class GraftPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    grafts: list[Graft] = Field(default_factory=list)
    seed: int = 7


def schedule_on_beats(beats: list[int], *, pattern: list[tuple[Window, float, Enter]], rest_every: int = 0,
                      frames: int | None = None) -> list[Graft]:
    """Grafts placed on given beat frames, cycling a pattern of (window, hold in beats, enter).

    ``pattern`` is the phrase: e.g. four quick eye swaps, then a long mouth, then a rest. Holds are in
    beats, so the rhythm follows the music, not a metronome. ``rest_every`` leaves every n-th beat empty."""
    out: list[Graft] = []
    k = 0
    for i, beat in enumerate(beats):
        if rest_every and (i + 1) % rest_every == 0:
            continue
        window, hold_beats, enter = pattern[k % len(pattern)]
        k += 1
        nxt = beats[i + 1] if i + 1 < len(beats) else (beats[i] + 15)
        hold = max(2, int(round((nxt - beat) * hold_beats)))
        if frames is not None and beat + hold > frames:
            break
        out.append(Graft(window=window, start=int(beat), hold=hold, enter=enter, leave="cut" if enter == "cut" else "fade",
                         ramp=0 if enter == "cut" else 4))
    return out


def beats_from_bpm(frames: int, fps: int, bpm: float, offset: int = 0) -> list[int]:
    step = fps * 60.0 / bpm
    return [int(round(offset + k * step)) for k in range(int((frames - offset) / step) + 1)]


def _ease(t: float) -> float:
    t = min(1.0, max(0.0, t))
    return t * t * (3 - 2 * t)


@dataclass
class GraftRenderer:
    """Renders a plan over host frames whose face landmarks are known per frame."""
    donors: list[DonorFace]
    seed: int = 7
    max_scale: float = 2.0
    tolerance: float = 0.1
    smooth: float = 0.6            # share of the previous landmarks kept each frame (jitter damping)
    _chosen: dict = field(default_factory=dict, repr=False)
    _used: set = field(default_factory=set, repr=False)
    _placed: dict = field(default_factory=dict, repr=False)     # graft index -> (host at placement, donor warped then)

    # ----- alignment
    def fit(self, donor: DonorFace, host: FacePoints) -> np.ndarray | None:
        import cv2
        if host.interocular / max(donor.points.interocular, 1e-3) > self.max_scale:
            return None
        M = _similarity(donor.points.triangle, host.triangle)
        moved = cv2.transform(donor.points.triangle.reshape(-1, 1, 2), M).reshape(-1, 2)
        if np.abs(moved - host.triangle).max() > self.tolerance * host.interocular:
            return None
        return M

    # ----- window
    def window_mask(self, window: Window, host: FacePoints, shape: tuple[int, int], *, grow: float = 1.0,
                    subject: np.ndarray | None = None, move: np.ndarray | None = None) -> np.ndarray:
        """``move`` (2x3) carries the window from ``host`` (where the graft was placed) to the current frame."""
        import cv2
        h, w = shape
        m = np.zeros((h, w), np.uint8)
        if move is not None:
            m0 = self.window_mask(window, host, shape, grow=grow, subject=subject)
            return cv2.warpAffine(m0.astype(np.uint8), move, (w, h), flags=cv2.INTER_NEAREST) > 0
        if window == "frame":
            m[:] = 1
        elif window == "subject":
            if subject is not None:
                m[subject] = 1
            else:
                m[:] = 1
        elif window == "face":
            # the face oval itself (cheek to cheek, brow to chin), not the generous face box: on a
            # close-up the box covers most of the frame and a cut-in reads as a full replacement
            from pipeline.algmods.composite import face_frame
            origin, across, down, d = face_frame(host)
            centre = origin + down * (0.55 * d)
            angle = float(np.degrees(np.arctan2(across[1], across[0])))
            axes = (int(1.3 * d * grow), int(1.7 * d * grow))
            cv2.ellipse(m, (int(centre[0]), int(centre[1])), axes, angle, 0, 360, 1, -1)
        else:
            x0, x1, y0, y1 = PATCHES[window]
            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
            hw, hh = (x1 - x0) / 2 * grow, (y1 - y0) / 2 * grow
            poly = cell_polygon(host, ("w", cx - hw, cx + hw, cy - hh, cy + hh))
            cv2.fillConvexPoly(m, np.round(poly).astype(np.int32), 1)
        return m.astype(bool)

    # ----- donor choice
    def choose(self, frame: np.ndarray, host: FacePoints, window: Window, *, grow: float = 1.0) -> int | None:
        import cv2
        h, w = frame.shape[:2]
        s = 480 / h
        small = cv2.resize(frame, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA)
        host_s = FacePoints(host.eyes * s, host.nose * s)
        m = self.window_mask(window if window not in ("frame", "subject") else "face", host_s, small.shape[:2], grow=grow)
        if m.sum() < 16:
            return None
        hl = cv2.GaussianBlur(_luma(small), (0, 0), 1.2)
        a = hl[m]; a = (a - a.mean()) / (a.std() + 1e-3)
        hc = small[m].reshape(-1, 3).mean(0); hc = hc / (hc.sum() + 1e-3)
        best, best_score = None, np.inf
        rng = np.random.RandomState(self.seed + len(self._used))
        for i in rng.permutation(len(self.donors))[:220]:
            if int(i) in self._used:
                continue
            donor = self.donors[int(i)]
            M = self.fit(donor, host_s)
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

    # ----- compositing
    def apply(self, frame: np.ndarray, host: FacePoints, graft: Graft, donor_index: int, phase: float,
              *, subject: np.ndarray | None = None) -> np.ndarray:
        """``phase`` 0..1 over the graft's life: enter over the ramp, hold, leave over the ramp."""
        import cv2
        from pipeline.algmods.mosaic import tone_transfer
        h, w = frame.shape[:2]
        donor = self.donors[donor_index]
        key = (id(graft), donor_index)
        if key not in self._placed:
            # A sticker: the donor is warped once, where the graft is placed; afterwards the whole patch,
            # content and edge together, rides the host's motion. Nothing swims inside the window.
            M = _similarity(donor.points.triangle, host.triangle)
            self._placed[key] = (host, cv2.warpAffine(donor.image(), M, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT))
        host0, placed = self._placed[key]
        move = _similarity(host0.triangle, host.triangle)
        warped = cv2.warpAffine(placed, move, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
        total = graft.hold + 2 * graft.ramp
        t = phase * total
        entering = t < graft.ramp
        leaving = t > graft.ramp + graft.hold
        amount = 1.0
        style = graft.enter if entering else (graft.leave if leaving else "cut")
        if entering and graft.ramp:
            amount = _ease(t / graft.ramp)
        elif leaving and graft.ramp:
            amount = _ease((total - t) / graft.ramp)
        grow = graft.grow * (amount if style == "grow" else 1.0)
        mask = self.window_mask(graft.window, host0, (h, w), grow=max(grow, 0.05), subject=subject, move=move)
        if not mask.any():
            return frame
        ys, xs = np.nonzero(mask)
        x0, x1, y0, y1 = xs.min(), xs.max() + 1, ys.min(), ys.max() + 1
        tile = tone_transfer(warped[y0:y1, x0:x1], frame[y0:y1, x0:x1], light=graft.light, colour=graft.colour)
        alpha = mask[y0:y1, x0:x1].astype(np.float32)
        if graft.feather:
            alpha = cv2.GaussianBlur(alpha, (0, 0), graft.feather)
        if style.startswith("slide") and amount < 1.0:
            # the window is fixed; the pixels slide in from one side
            dx, dy = {"slide-l": (-1, 0), "slide-r": (1, 0), "slide-u": (0, -1), "slide-d": (0, 1)}[style]
            shift = int(round((1 - amount) * (x1 - x0 if dx else y1 - y0)))
            tile = np.roll(tile, (dy * shift, dx * shift), axis=(0, 1))
        if style == "fade":
            alpha = alpha * amount
        out = frame.copy()
        region = out[y0:y1, x0:x1].astype(np.float32)
        out[y0:y1, x0:x1] = (region * (1 - alpha[..., None]) + tile.astype(np.float32) * alpha[..., None]).astype(np.uint8)
        return out

    def render(self, frames: list[np.ndarray], landmarks, plan: GraftPlan, *, subjects: list | None = None,
               progress=lambda _m: None) -> list[np.ndarray]:
        """``landmarks(i)`` -> FacePoints | None for frame i."""
        out = []
        last = None
        for i, frame in enumerate(frames):
            if i % 30 == 0:
                progress(f"grafts {i + 1}/{len(frames)}")
            raw = landmarks(i)
            if raw is None and last is None:
                out.append(frame.copy()); continue
            if raw is None:
                host = last
            elif last is None:
                host = raw
            else:                                                   # damp detector jitter frame to frame
                host = FacePoints(last.eyes * self.smooth + raw.eyes * (1 - self.smooth),
                                  last.nose * self.smooth + raw.nose * (1 - self.smooth))
            last = host
            result = frame
            for k, g in enumerate(plan.grafts):
                total = g.hold + 2 * g.ramp
                if not (g.start <= i < g.start + total):
                    continue
                if k not in self._chosen:
                    d = g.donor if g.donor is not None else self.choose(frame, host, g.window, grow=g.grow)
                    if d is None:
                        continue
                    self._chosen[k] = d
                    self._used.add(d)
                result = self.apply(result, host, g, self._chosen[k], (i - g.start) / max(total - 1, 1),
                                    subject=subjects[i] if subjects else None)
            out.append(result)
        return out
