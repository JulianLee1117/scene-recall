"""Match-cut continuity for assembly: how well one shot's last frame cuts into the next shot's first.

The beam search asks for thousands of cut pairs, so this is the identity-crop
(no reframing, full picture) form of the Match Cuts scorer
(``pipeline.matching.moments.score``), with every instant's features computed
once and cached: where the prominent subjects are, the eye-trace point, the
main person's pose, the light layout and the motion. Pair scores use the same
components, weights and library calibration as the Lab, so "a match" means
the same thing in both places.

Without a built moment index (``python -m pipeline.matching.moments index``)
the assembly keeps its shot-level eye-trace heuristic.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Iterable

import numpy as np

from pipeline.matching.moments import score as scoring

STRENGTH = {"off": 0.0, "some": 0.35, "many": 1.0}
_GRID = (9, 16)


@dataclass
class _Features:
    union: np.ndarray            # rows x 144 coverage of the prominent group
    eye: np.ndarray              # rows x 2 eye-trace point (NaN when none)
    person: np.ndarray           # rows: main subject is a person
    has_subject: np.ndarray      # rows: a prominent subject covers at least 1% of the picture
    pose_xy: np.ndarray          # rows x 17 x 2 main person's keypoints
    pose_seen: np.ndarray        # rows x 17
    light: np.ndarray            # rows x 144 blurred, centred and normalized luma
    camera: np.ndarray           # rows x 3 (vx, vy, div)
    velocity: np.ndarray         # rows x 2
    brightness: np.ndarray       # rows


_FIELDS = tuple(_Features.__dataclass_fields__)


class CutMatcher:
    """Scores cut pairs between library instants; features are cached per index row."""

    def __init__(self, index: Any, strength: float):
        self.index = index
        self.strength = strength
        self.calibration = {**scoring.DEFAULT_CALIBRATION, **(index.calibration or {})}
        self._rows: dict[str, tuple[np.ndarray, np.ndarray]] = {}
        self._row_memo: dict[tuple[str, float], int | None] = {}
        self._position: dict[int, int] = {}
        self._store: _Features | None = None
        self._size = 0
        self._pairs: dict[tuple[int, int], float] = {}

    @classmethod
    def load(cls, config: Any, setting: str | None) -> "CutMatcher | None":
        strength = STRENGTH.get(setting or "some", STRENGTH["some"])
        if strength <= 0:
            return None
        from pipeline.matching.moments import index as moment_index
        try:
            loaded = moment_index.load(config)
        except (OSError, ValueError, KeyError):
            return None
        return cls(loaded, strength) if loaded is not None else None

    # -- rows ---------------------------------------------------------------

    def row(self, unit_id: str, time: float) -> int | None:
        """The usable indexed instant of a shot nearest ``time`` (None when the shot is not indexed)."""
        key = (unit_id, round(float(time), 3))
        if key in self._row_memo:
            return self._row_memo[key]
        if unit_id not in self._rows:
            try:
                rows = self.index.unit_rows(self.index.unit_index(unit_id))
            except KeyError:
                rows = np.zeros(0, dtype=np.int64)
            usable = rows[self.index.columns["ok"][rows]] if len(rows) else rows
            self._rows[unit_id] = (usable, self.index.columns["time"][usable] if len(usable) else np.zeros(0))
        rows, times = self._rows[unit_id]
        found = None
        if len(rows):
            position = int(np.argmin(np.abs(times - time)))
            found = int(rows[position]) if abs(times[position] - time) <= 0.3 else None
        self._row_memo[key] = found
        return found

    def _positions(self, rows: list[int]) -> np.ndarray:
        """Positions of rows in the dense feature store, describing the missing ones in one batch."""
        missing = sorted({row for row in rows if row not in self._position})
        if missing:
            block = _describe(self.index, np.array(missing, dtype=np.int64))
            needed = self._size + len(missing)
            if self._store is None or needed > len(self._store.brightness):
                capacity = max(256, needed * 2)
                grown = {}
                for name in _FIELDS:
                    part = getattr(block, name)
                    array = np.zeros((capacity, *part.shape[1:]), dtype=part.dtype)
                    if self._store is not None:
                        array[:self._size] = getattr(self._store, name)[:self._size]
                    grown[name] = array
                self._store = _Features(**grown)
            for name in _FIELDS:
                getattr(self._store, name)[self._size:needed] = getattr(block, name)
            for offset, row in enumerate(missing):
                self._position[row] = self._size + offset
            self._size = needed
        return np.array([self._position[row] for row in rows], dtype=np.int64)

    def _features(self, rows: Iterable[int]) -> _Features:
        positions = self._positions([int(row) for row in rows])
        return _Features(*(getattr(self._store, name)[positions] for name in _FIELDS))

    # -- scoring ------------------------------------------------------------

    def scores(self, out_row: int | None, in_rows: list[int | None]) -> np.ndarray:
        """Match quality in [0, 1] of cutting from ``out_row`` into each of ``in_rows`` (0 when unknown).

        Pair scores are memoized: the beam search asks for the same cut many times.
        """
        result = np.zeros(len(in_rows))
        if out_row is None:
            return result
        missing = []
        for position, row in enumerate(in_rows):
            if row is None:
                continue
            cached = self._pairs.get((out_row, row))
            if cached is None:
                missing.append(position)
            else:
                result[position] = cached
        if missing:
            a = self._features([out_row])
            b = self._features([in_rows[position] for position in missing])
            values = _pair_scores(a, b, self.calibration)
            for position, value in zip(missing, values):
                result[position] = value
                self._pairs[(out_row, in_rows[position])] = float(value)
        return result


def _describe(index: Any, rows: np.ndarray) -> _Features:
    moments = index.moments(rows)
    n = len(rows)
    group, main = scoring.salient(moments)
    identity = np.tile(np.array([0.0, 0.0, 1.0, 1.0]), (n, 1))
    union = np.zeros((n, _GRID[0] * _GRID[1]), dtype=np.float32)
    ids = group[group >= 0]
    if len(ids):
        coverage = scoring.rasterize(moments, ids, identity, _GRID).reshape(len(ids), -1)
        owner = np.searchsorted(moments.inst_ptr, ids, side="right") - 1
        np.maximum.at(union, owner, coverage)
    eye, exists = scoring.eye_points(moments, main)
    person = np.zeros(n, dtype=bool)
    has = main >= 0
    person[has] = moments.classes[main[has]] == scoring.PERSON
    has_subject = np.zeros(n, dtype=bool)
    has_subject[has] = scoring.box_area(moments.boxes[main[has]]) >= 0.01
    pose_xy = np.zeros((n, 17, 2), dtype=np.float32)
    pose_seen = np.zeros((n, 17), dtype=bool)
    main_pose = moments.main_pose()
    with_pose = main_pose >= 0
    if with_pose.any():
        pose_xy[with_pose] = moments.pose_xy[main_pose[with_pose]]
        pose_seen[with_pose] = moments.pose_conf[main_pose[with_pose]] >= scoring.SEEN
    gray = moments.gray.reshape(n, _GRID[0], 2, _GRID[1], 2).mean(axis=(2, 4))
    light = scoring._blur(gray).reshape(n, -1)
    light = light - light.mean(axis=1, keepdims=True)
    light = light / np.maximum(np.linalg.norm(light, axis=1, keepdims=True), 1e-6)
    return _Features(union=union, eye=np.where(exists[:, None], eye, np.nan), person=person, has_subject=has_subject,
                     pose_xy=pose_xy, pose_seen=pose_seen, light=light.astype(np.float32),
                     camera=moments.camera[:, :3].astype(np.float32), velocity=moments.velocity.astype(np.float32),
                     brightness=moments.brightness.astype(np.float32))


def _continuity(a: np.ndarray, b: np.ndarray, still: float) -> np.ndarray:
    """``score._continuity`` for one outgoing and many incoming velocities."""
    value, _ = scoring._continuity(a, b, still)
    return value


def _pair_scores(a: _Features, b: _Features, calibration: dict[str, list[float]]) -> np.ndarray:
    """Weighted, calibrated match score of one outgoing instant ``a`` against incoming instants ``b``."""
    n = len(b.brightness)
    has_subject = bool(a.has_subject[0])
    weights = dict(scoring.weights_for("auto", has_subject))
    parts: dict[str, np.ndarray] = {}
    if has_subject:
        low = np.minimum(b.union, a.union).sum(axis=1)
        high = np.maximum(b.union, a.union).sum(axis=1)
        parts["subject"] = np.where(high > 0, low / np.maximum(high, 1e-9), 0.0)
        if np.isfinite(a.eye[0]).all():
            distance = np.hypot(b.eye[:, 0] - a.eye[0, 0], b.eye[:, 1] - a.eye[0, 1])
            parts["eyes"] = np.where(np.isfinite(distance) & (b.person == a.person[0]), np.exp(-(np.nan_to_num(distance, nan=9) / 0.1) ** 2), 0.0)
        else:
            weights["subject"] = weights.get("subject", 0.0) + weights.pop("eyes", 0.0)
        if a.pose_seen[0].any():
            scale = max(float(np.ptp(a.pose_xy[0][a.pose_seen[0]], axis=0).max()) if a.pose_seen[0].sum() >= 2 else 0.05, 0.05)
            distance2 = ((b.pose_xy - a.pose_xy[0]) ** 2).sum(axis=-1)
            similarity = np.exp(-distance2 / (2 * (scale * scoring.KEYPOINT_KAPPA) ** 2))
            both, either = b.pose_seen & a.pose_seen[0], b.pose_seen | a.pose_seen[0]
            parts["pose"] = (similarity * both).sum(axis=1) / np.maximum(either.sum(axis=1), 1)
        else:
            weights["subject"] = weights.get("subject", 0.0) + weights.pop("pose", 0.0)
    parts["light"] = np.clip(b.light @ a.light[0], 0.0, 1.0)
    pan = _continuity(a.camera[0, :2], b.camera[:, :2], scoring.STILL)
    travel, travel_known = scoring._continuity(a.velocity[0], b.velocity, 0.04)
    parts["motion"] = np.where(travel_known, 0.6 * pan + 0.4 * travel, pan)
    if has_subject:                      # a frame-filling subject's overlap says little (as in the Lab scorer)
        filled = float(np.clip((a.union[0].mean() - 0.55) / 0.35, 0.0, 0.8))
        moved = weights.get("subject", 0.0) * filled
        weights["subject"] = weights.get("subject", 0.0) - moved
        others = [name for name in ("pose", "eyes", "light") if name in parts and weights.get(name, 0.0) > 0]
        for name in others:
            weights[name] += moved / len(others)
    total = np.zeros(n)
    weight_sum = 0.0
    for name, weight in weights.items():
        if name in parts:
            total += weight * scoring.calibrate(parts[name], calibration[name])
            weight_sum += weight
    total = total / max(weight_sum, 1e-9)
    jump = np.abs(np.log((b.brightness + 0.05) / (a.brightness[0] + 0.05)))
    total -= scoring.TONE_PENALTY * np.clip(jump / math.log(3.0), 0.0, 1.0)
    return np.clip(total, 0.0, 1.0)
