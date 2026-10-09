"""Monocular depth for a shot: a lab primitive, like regions and grafts.

Depth Anything V2 (small, ~25M parameters, ~0.35 GB of GPU) gives relative inverse depth per
frame: larger is nearer. A shot's maps are normalised together (2nd to 98th percentile of the
whole shot) and smoothed in time, so a band of distance means the same thing on every frame.
Regions use it for the ``near`` and ``far`` kinds (the nearest share of the frame, or the rest).
The model downloads from the Hugging Face hub on first use.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

MODEL = "depth-anything/Depth-Anything-V2-Small-hf"


@dataclass
class DepthEstimator:
    model: str = MODEL
    width: int = 960                      # inference width; maps are resized back to the frame
    smooth: float = 0.5                   # temporal EMA weight on the previous map
    _pipe: object = field(default=None, repr=False)

    def _load(self):
        if self._pipe is None:
            import torch
            from transformers import pipeline
            self._pipe = pipeline("depth-estimation", model=self.model, device=0 if torch.cuda.is_available() else -1)
        return self._pipe

    def raw(self, frame: np.ndarray) -> np.ndarray:
        """Relative inverse depth (nearer is larger) at frame size, float32."""
        import cv2
        from PIL import Image
        h, w = frame.shape[:2]
        s = min(1.0, self.width / w)
        img = Image.fromarray(frame if s == 1.0 else cv2.resize(frame, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA))
        d = np.asarray(self._load()(img)["predicted_depth"], np.float32)
        return d if d.shape == (h, w) else cv2.resize(d, (w, h), interpolation=cv2.INTER_LINEAR)

    def shot(self, frames: list[np.ndarray], *, progress=None) -> list[np.ndarray]:
        """Nearness maps in 0..1 for every frame, normalised across the shot and smoothed in time."""
        maps = []
        prev = None
        for i, f in enumerate(frames):
            d = self.raw(f)
            if prev is not None and self.smooth:
                d = self.smooth * prev + (1 - self.smooth) * d
            prev = d
            maps.append(d)
            if progress and i % 10 == 0:
                progress(i / len(frames))
        sample = np.concatenate([m[::8, ::8].ravel() for m in maps])
        lo, hi = np.percentile(sample, [2, 98])
        return [np.clip((m - lo) / max(hi - lo, 1e-6), 0, 1).astype(np.float32) for m in maps]


def depth_visual(near: np.ndarray) -> np.ndarray:
    """A grey picture of a nearness map (near is bright)."""
    return np.repeat((near * 255).astype(np.uint8)[..., None], 3, axis=2)
