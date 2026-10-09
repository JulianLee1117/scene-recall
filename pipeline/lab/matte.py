"""Person matting for a shot: a soft alpha per frame, a lab primitive like regions and depth.

Robust Video Matting (MobileNetV3, TorchScript, MIT) gives a temporally stable alpha for people
with hair-level edges, where a detector's instance mask is a blob. The model file lives at
``<assets>/models/rvm_mobilenetv3_fp32.torchscript`` and is downloaded on first use (15 MB).
Regions use it for ``subject`` and ``background`` when ``matte`` is on and the class is a person.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

MODEL_FILE = "rvm_mobilenetv3_fp32.torchscript"
MODEL_URL = "https://github.com/PeterL1n/RobustVideoMatting/releases/download/v1.0.0/" + MODEL_FILE


def model_path(assets_dir: Path | None = None) -> Path:
    if assets_dir is None:
        from pipeline.config import load_config
        assets_dir = load_config().paths.assets_dir
    return Path(assets_dir) / "models" / MODEL_FILE


def available(assets_dir: Path | None = None) -> bool:
    try:
        import torch  # noqa: F401
    except ImportError:
        return False
    return True


@dataclass
class PersonMatte:
    assets_dir: Path | None = None
    _model: object = field(default=None, repr=False)

    def _load(self):
        if self._model is None:
            import torch
            import urllib.request
            path = model_path(self.assets_dir)
            if not path.is_file():
                path.parent.mkdir(parents=True, exist_ok=True)
                urllib.request.urlretrieve(MODEL_URL, path)
            device = "cuda" if torch.cuda.is_available() else "cpu"
            self._model = torch.jit.load(str(path), map_location=device).to(device).eval()
            self._device = device
        return self._model

    def alphas(self, frames: list[np.ndarray], *, progress=None) -> list[np.ndarray]:
        """Alpha 0..1 (float32, frame size) for every frame, run as one sequence so edges stay steady."""
        import torch
        model = self._load()
        h = frames[0].shape[0]
        ratio = 0.375 if h >= 1000 else (0.5 if h >= 600 else 1.0)
        rec = [None] * 4
        out = []
        with torch.no_grad():
            for i, f in enumerate(frames):
                x = torch.from_numpy(np.ascontiguousarray(f)).to(self._device).permute(2, 0, 1)[None].float() / 255.0
                _fgr, pha, *rec = model(x, *rec, ratio)
                out.append(pha[0, 0].float().cpu().numpy().astype(np.float32))
                if progress and i % 30 == 0:
                    progress(i / len(frames))
        return out
