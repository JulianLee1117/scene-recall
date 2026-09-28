"""Choose a reference from retained masks without changing their derivation."""
from __future__ import annotations

import math

import numpy as np


def reference_salience(frame):
    """Prefer a visible contained subject over a large piece of the background.

    Border contact is uncertainty, not a rejection: hands, cropped close-ups and
    off-center objects remain useful. The model's mask stability is not semantic
    object confidence. This is a search selection policy over its retained tracks.
    """
    if not frame or not frame.get("visible"):
        return 0.
    center = np.asarray(frame["centroid"], dtype=float)
    box = frame["box"]
    x, y, width, height = (float(box[name]) for name in ("x", "y", "width", "height"))
    quality, area = float(frame.get("quality", 1.)), float(frame["area"])
    if (center.shape != (2,) or not np.isfinite(center).all()
            or not np.isfinite([x, y, width, height, quality, area]).all()
            or not 0 < area <= 1 or min(width, height) <= 0):
        return 0.
    border = min(x, y, 1 - x - width, 1 - y - height) <= .01
    extent = .2 if max(width, height) > .95 else 1.
    return quality * math.sqrt(area) * max(0., 1 - float(np.linalg.norm(center - .5))) * extent * (.65 if border else 1.)


def select_reference_track(tracks, timestamp, *, explicit=False):
    if explicit:
        return tracks[0] if tracks else None
    def score(track):
        frame = next((row for row in track["frames"] if abs(row["time"] - timestamp) < 1e-7), None)
        return reference_salience(frame)
    selected = max(tracks, key=score, default=None)
    return selected if selected is not None and score(selected) > 0 else None
