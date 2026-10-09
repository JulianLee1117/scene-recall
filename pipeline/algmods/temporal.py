"""Temporal remix treatments: the picture made from other moments of the same shot.

Coherence is free here because every pixel is the shot's own. What the library adds that
alg.comp.mod did not have: a subject mask per frame (regions), dense flow, and a music map.

* ``beat_time``   — two clocks. ``world``: the background is frozen between beats and jumps
                    forward on each beat while the subject moves live (no holes: the live subject is
                    pasted over the frozen world). ``subject``: the inverse, the subject frozen and
                    jumping while the world runs; the hole it leaves is filled from the frozen frame.
* ``motion_echo`` — only the fast parts of the subject leave a trail: subject pixels whose flow
                    exceeds a threshold are kept in a decaying buffer behind the live subject.
                    Choreography, not smear.
* ``time_slice``  — a boundary sweeps the frame; on one side the shot is ``offset`` frames
                    later. The sweep can follow a direction, a wedge, or beats.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np


# ----------------------------------------------------------------------------------------------- beats

def beats_from_rhythm_cache(assets_dir: Path, track_name_contains: str, *, passage_start: float | None = None) -> tuple[list[float], list[float], dict]:
    """Beat and downbeat times (seconds into the passage) from the Lab's cached rhythm analysis of a track."""
    import glob
    from pipeline.config import load_config
    from pipeline.lab.store import LabStore
    cfg = load_config()
    store = LabStore(cfg.paths.state_dir, cfg.paths.assets_dir)
    with store.connection() as con:
        names = {r["id"]: r["name"] for r in con.execute("SELECT id, name FROM tracks").fetchall()}
    best = None
    for f in glob.glob(str(Path(assets_dir) / "lab" / "rhythm" / "*.json")):
        d = json.load(open(f, encoding="utf-8"))
        prov = d.get("provenance") or {}
        ident = prov.get("identity") or prov
        name = names.get(ident.get("track") or prov.get("track"), "")
        if track_name_contains.lower() not in name.lower():
            continue
        passage = ident.get("passage") or prov.get("passage") or {}
        if passage_start is not None and abs(float(passage.get("start", -1)) - passage_start) > 0.5:
            continue
        if best is None or len(d.get("beats") or []) > len(best[0].get("beats") or []):
            best = (d, name, passage)
    if best is None:
        raise ValueError(f"No rhythm cache for a track containing {track_name_contains!r}")
    d, name, passage = best
    start = float(passage.get("start", 0.0))
    beats = [float(b) - start for b in d.get("beats") or []]
    downs = [float(b) - start for b in d.get("downbeats") or []]
    return beats, downs, {"track": name, "passage": passage}


def beat_frames(beats: list[float], *, fps: int, frames: int, offset: float = 0.0) -> list[int]:
    """Beats as frame indices inside a clip of ``frames`` frames, with the music starting at ``offset`` s into the clip."""
    out = sorted({int(round((b + offset) * fps)) for b in beats if 0 <= (b + offset) * fps < frames})
    return out


# ------------------------------------------------------------------------------------------- beat time

def beat_time(frames: list[np.ndarray], masks: list[np.ndarray | None], beats: list[int], *, mode: str = "world",
              feather: int = 4, sub_beats: int = 1) -> list[np.ndarray]:
    """Two clocks: one half of the picture only advances on beats."""
    import cv2
    n = len(frames)
    steps = sorted(set(beats))
    if sub_beats > 1 and len(steps) > 1:
        extra = []
        for a, b in zip(steps, steps[1:]):
            extra += [int(round(a + (b - a) * k / sub_beats)) for k in range(1, sub_beats)]
        steps = sorted(set(steps + extra))
    held_index = 0
    out = []
    for i, frame in enumerate(frames):
        if steps and i >= steps[0]:
            while len(steps) > 1 and i >= steps[1]:
                steps.pop(0)
            held_index = steps[0]
        held = frames[held_index]
        mask = masks[i] if masks[i] is not None else np.zeros(frame.shape[:2], bool)
        alpha = cv2.GaussianBlur(mask.astype(np.float32), (0, 0), feather)[..., None] if feather else mask.astype(np.float32)[..., None]
        if mode == "world":
            # frozen world, live subject on top
            out.append((held.astype(np.float32) * (1 - alpha) + frame.astype(np.float32) * alpha).astype(np.uint8))
        else:
            # live world, the subject frozen where it was on the last beat
            hm = masks[held_index] if masks[held_index] is not None else np.zeros(frame.shape[:2], bool)
            ha = cv2.GaussianBlur(hm.astype(np.float32), (0, 0), feather)[..., None] if feather else hm.astype(np.float32)[..., None]
            out.append((frame.astype(np.float32) * (1 - ha) + held.astype(np.float32) * ha).astype(np.uint8))
    return out


# ----------------------------------------------------------------------------------------- motion echo

@dataclass
class MotionEcho:
    """Trails only where the subject moves fast; the still parts of the body stay solid."""
    threshold: float = 2.5         # px/frame of flow for a pixel to leave a trail
    decay: float = 0.88            # per-frame retention of the trail
    tint: tuple[float, float, float] | None = None    # optional colour cast for the trail (0..1)
    scale: float = 0.5
    _trail: np.ndarray | None = field(default=None, repr=False)
    _prev: np.ndarray | None = field(default=None, repr=False)
    _dis: object = field(default=None, repr=False)

    def step(self, frame: np.ndarray, mask: np.ndarray | None) -> np.ndarray:
        import cv2
        h, w = frame.shape[:2]
        gray = cv2.cvtColor(frame, cv2.COLOR_RGB2GRAY)
        s = self.scale
        small = cv2.resize(gray, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
        if self._trail is None:
            self._trail = np.zeros((h, w, 4), np.float32)              # rgb premultiplied + alpha
        if self._prev is not None:
            if self._dis is None:
                self._dis = cv2.DISOpticalFlow_create(cv2.DISOPTICAL_FLOW_PRESET_MEDIUM)
            flow = self._dis.calc(self._prev, small, None)
            speed = cv2.resize(np.linalg.norm(flow, axis=2) / s, (w, h), interpolation=cv2.INTER_LINEAR)
            moving = speed > self.threshold
            if mask is not None:
                moving &= mask
            # the trail fades, then this frame's fast subject pixels are stamped onto it
            self._trail *= self.decay
            rgb = frame.astype(np.float32) / 255.0
            if self.tint is not None:
                rgb = rgb * 0.4 + np.array(self.tint, np.float32) * 0.6
            self._trail[moving, :3] = rgb[moving]
            self._trail[moving, 3] = 1.0
        self._prev = small
        a = self._trail[..., 3:4]
        base = frame.astype(np.float32) / 255.0
        composed = base * (1 - a) + self._trail[..., :3] * a
        if mask is not None:                                           # the live subject is always on top
            m = mask[..., None].astype(np.float32)
            composed = composed * (1 - m) + base * m
        return np.clip(composed * 255, 0, 255).astype(np.uint8)


# ------------------------------------------------------------------------------------------ time slice

def time_slice(frames: list[np.ndarray], *, offset: int = 30, sweeps: list[tuple[int, int]] | None = None,
               direction: str = "left-to-right", feather: int = 2) -> list[np.ndarray]:
    """A boundary sweeps the frame over each (start, end) sweep; behind it the shot is ``offset`` frames later.

    Between sweeps the picture is wholly 'later' (after a sweep completes) or wholly 'now' (before the
    next starts), so each sweep is a visible seam of time crossing the picture."""
    import cv2
    n = len(frames)
    h, w = frames[0].shape[:2]
    sweeps = sweeps or [(0, n)]
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    axis = {"left-to-right": xx / w, "right-to-left": 1 - xx / w, "top-to-bottom": yy / h, "bottom-to-top": 1 - yy / h,
            "centre-out": np.hypot(xx / w - 0.5, yy / h - 0.5) / 0.71}[direction]
    out = []
    state_later = False
    for i, frame in enumerate(frames):
        later = frames[min(n - 1, i + offset)]
        pos = None
        for (a, b) in sweeps:
            if a <= i < b:
                pos = (i - a) / max(b - a, 1)
        if pos is None:
            out.append(later.copy() if state_later else frame.copy())
            continue
        edge = np.clip((pos - axis) * (w / max(feather, 1)) + 0.5, 0, 1)[..., None]      # 1 behind the boundary
        if state_later:
            edge = 1 - edge                                                            # the next sweep brings 'now' back
        out.append((frame.astype(np.float32) * (1 - edge) + later.astype(np.float32) * edge).astype(np.uint8))
        if pos >= 1 - 1 / max(b - a, 1):
            state_later = not state_later
    return out


# ------------------------------------------------------------------------------------ vanishing point

def vanishing_point(frames: list[np.ndarray], masks: list[np.ndarray | None], *, scale: float = 0.5,
                    min_speed: float = 1.0) -> tuple[tuple[float, float] | None, float]:
    """Where the subject's motion lines converge, in frame fractions, and how well they converge (0..1).

    For a train running along perspective lines every flow vector inside the mask points at the
    vanishing point; the least-squares intersection of those lines finds it. For lateral motion
    the lines are parallel, the fit is poor, and the caller should use bands across the motion
    instead (``None`` is returned)."""
    import cv2
    dis = cv2.DISOpticalFlow_create(cv2.DISOPTICAL_FLOW_PRESET_MEDIUM)
    h, w = frames[0].shape[:2]
    A = np.zeros((2, 2)); b = np.zeros(2); n = 0
    prev = None
    dirs = []
    for i in range(0, len(frames), max(1, len(frames) // 12)):
        gray = cv2.resize(cv2.cvtColor(frames[i], cv2.COLOR_RGB2GRAY), None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        if prev is not None:
            flow = dis.calc(prev, gray, None)
            m = masks[i]
            if m is not None:
                m = cv2.resize(m.astype(np.uint8), (gray.shape[1], gray.shape[0]), interpolation=cv2.INTER_NEAREST) > 0
            else:
                m = np.ones(gray.shape, bool)
            speed = np.linalg.norm(flow, axis=2)
            ok = m & (speed > min_speed * scale)
            ys, xs = np.nonzero(ok)
            if len(xs) > 50:
                sel = np.random.RandomState(i).choice(len(xs), min(2000, len(xs)), replace=False)
                for y, x in zip(ys[sel], xs[sel]):
                    d = flow[y, x] / max(speed[y, x], 1e-6)
                    dirs.append(d)
                    nrm = np.array([-d[1], d[0]])                      # normal to the motion line
                    p = np.array([x, y], float) / scale
                    A += np.outer(nrm, nrm); b += nrm * (nrm @ p); n += 1
        prev = gray
    if n < 100:
        return None, 0.0
    dirs = np.array(dirs)
    spread = 1.0 - float(np.linalg.norm(dirs.mean(axis=0)))          # 0 = all parallel, 1 = all directions
    if spread < 0.03:
        return None, spread
    try:
        vp = np.linalg.solve(A, b)
    except np.linalg.LinAlgError:
        return None, spread
    return (float(vp[0] / w), float(vp[1] / h)), spread
