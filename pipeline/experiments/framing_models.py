"""Isolated PE-Spatial pilot adapter; downloads require the explicit CLI flag.

No product loader imports this module. The official model code and checkpoint
are pinned, kept in one disposable directory, and hashed before every run.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
from importlib.metadata import version
from io import BytesIO
import json
from pathlib import Path
import sys
import time

import numpy as np

MODEL = "PE-Spatial-S16-512"
REVISION = "3a1d1419e8b19ba1c473a6e9b095e78699ddd7a7"
CODE_REVISION = "3e352cca660658d4b5c90f42a7808b11469e4c66"
ROOT = Path(__file__).resolve().parents[2] / ".tmp" / "framing-models" / MODEL
CODE_FILES = ("core/vision_encoder/pe.py", "core/vision_encoder/rope.py",
              "core/vision_encoder/config.py", "LICENSE.PE")
MAX_DOWNLOAD_BYTES = 1024**3


def file_hash(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(4 * 1024**2), b""):
            result.update(block)
    return result.hexdigest()


def spatial_profile() -> dict:
    """Require a completed local download; never resolves a moving hub ref."""
    receipt = json.loads((ROOT / "download.json").read_text(encoding="utf-8"))
    if receipt["model_revision"] != REVISION or receipt["code_revision"] != CODE_REVISION:
        raise ValueError("Spatial model download revision changed")
    expected = {*CODE_FILES, "core/__init__.py", "core/vision_encoder/__init__.py", MODEL + ".pt"}
    if set(receipt["files"]) != expected:
        raise ValueError("Incomplete spatial model download receipt")
    for name, sha in receipt["files"].items():
        if file_hash(ROOT / name) != sha:
            raise ValueError(f"Spatial model artifact changed: {name}")
    return {"model_id": "facebook/" + MODEL, "model_revision": REVISION,
            "code_repository": "facebookresearch/perception_models", "code_revision": CODE_REVISION,
            "files": receipt["files"], "dimensions": 384, "grid_size": 6,
            "adapter_sha256": file_hash(Path(__file__)),
            "preprocessing": "full RGB picture; torchvision PIL bilinear squash 512x512; mean/std=0.5; no crop",
            "extraction": "float32 no autocast; final patch tokens, exclude CLS; 6x6 adaptive average; L2 per cell; float16",
            "versions": {name: version(name) for name in ("torch", "torchvision", "timm", "einops", "numpy", "Pillow")}}


def download() -> dict:
    """Stage only this public model and its minimal official code, under 1 GiB."""
    import httpx
    ROOT.mkdir(parents=True, exist_ok=True)
    if (ROOT / "download.json").exists():
        return spatial_profile()
    # This host advertises IPv6 destinations without a usable route. Bind this
    # isolated download to IPv4 instead of spending a timeout per IPv6 address.
    transport = httpx.HTTPTransport(local_address="0.0.0.0", trust_env=False)
    with httpx.Client(timeout=30, follow_redirects=True, trust_env=False, transport=transport) as client:
        metadata = client.get(f"https://huggingface.co/api/models/facebook/{MODEL}/revision/{REVISION}?blobs=true")
        metadata.raise_for_status()
        info = metadata.json()
        weight = next(f for f in info["siblings"] if f["rfilename"] == MODEL + ".pt")
        size = weight["size"]
        if not 0 < size < MAX_DOWNLOAD_BYTES - 1024**2:
            raise ValueError("Checkpoint exceeds the admitted download budget")
        expected_sha = weight["lfs"]["sha256"]

        def fetch(pair):
            name, url, cap = pair
            path = ROOT / name
            path.parent.mkdir(parents=True, exist_ok=True)
            if name.endswith(".pt") and path.exists() and path.stat().st_size == size and file_hash(path) == expected_sha:
                return name, expected_sha
            temporary = path.with_suffix(path.suffix + ".part")
            total = 0
            with client.stream("GET", url) as response:
                response.raise_for_status()
                with temporary.open("wb") as handle:
                    for block in response.iter_bytes(1024**2):
                        total += len(block)
                        if total > cap:
                            raise ValueError("Download exceeds expected size")
                        handle.write(block)
            if name.endswith(".pt") and (total != size or file_hash(temporary) != expected_sha):
                raise ValueError("Checkpoint checksum or length mismatch")
            temporary.replace(path)
            return name, file_hash(path)

        requests = [(name, f"https://raw.githubusercontent.com/facebookresearch/perception_models/{CODE_REVISION}/{name}", 256 * 1024)
                    for name in CODE_FILES]
        requests.append((MODEL + ".pt", f"https://huggingface.co/facebook/{MODEL}/resolve/{REVISION}/{MODEL}.pt", size))
        with ThreadPoolExecutor(max_workers=5) as executor:
            files = dict(executor.map(fetch, requests))
    for name in ("core/__init__.py", "core/vision_encoder/__init__.py"):
        (ROOT / name).write_text("", encoding="utf-8")
        files[name] = file_hash(ROOT / name)
    receipt = {"model_revision": REVISION, "code_revision": CODE_REVISION, "files": files,
               "download_bytes": sum((ROOT / name).stat().st_size for name in files)}
    temporary = ROOT / "download.json.tmp"
    temporary.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    temporary.replace(ROOT / "download.json")
    return spatial_profile()


class SpatialExtractor:
    """Called only under the pilot's existing ingestion/GPU coordination."""
    def __init__(self, profile: dict):
        import torch
        from torchvision import transforms as T
        if profile != spatial_profile():
            raise ValueError("Spatial profile no longer matches local artifacts")
        for name, module in tuple(sys.modules.items()):
            if name == "core" or name.startswith("core."):
                path = getattr(module, "__file__", None)
                if path is None or not Path(path).resolve().is_relative_to(ROOT.resolve()):
                    raise ValueError("An unrelated core package is already loaded")
        sys.path.insert(0, str(ROOT))
        try:
            from core.vision_encoder.pe import VisionTransformer
        finally:
            sys.path.remove(str(ROOT))
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        tick = time.perf_counter()
        self.model = VisionTransformer.from_config(MODEL, pretrained=False)
        state = torch.load(ROOT / (MODEL + ".pt"), map_location="cpu", weights_only=True)
        state = state.get("state_dict", state.get("weights", state))
        state = {key.replace("module.", ""): value for key, value in state.items()}
        if any(key.startswith("visual.") for key in state):
            state = {key.removeprefix("visual."): value for key, value in state.items() if key.startswith("visual.")}
        self.model.load_state_dict(state, strict=True)
        self.model.eval().to(self.device)
        if self.device.type == "cuda":
            torch.cuda.reset_peak_memory_stats()
        self.preprocess = T.Compose([T.Resize((512, 512), interpolation=T.InterpolationMode.BILINEAR),
                                     T.ToTensor(), T.Normalize([.5] * 3, [.5] * 3)])
        self.metadata = {"device": str(self.device), "model_load_seconds": time.perf_counter() - tick,
                         "parameter_count": sum(p.numel() for p in self.model.parameters()),
                         "inference_batch_size": 8, "precision": "float32"}
        self._repeat_checked = False

    def __call__(self, rows):
        import torch
        import torch.nn.functional as F
        from PIL import Image
        from pipeline.experiments.framing_representation import source_bytes
        values = []
        with torch.inference_mode():
            for offset in range(0, len(rows), 8):
                images = []
                for row in rows[offset:offset + 8]:
                    with Image.open(BytesIO(source_bytes(row))) as source:
                        images.append(self.preprocess(source.convert("RGB")))
                batch = torch.stack(images).to(self.device)
                patches = self.model.forward_features(batch, norm=False, strip_cls_token=True)
                if tuple(patches.shape[1:]) != (1024, 384):
                    raise ValueError("Unexpected PE-Spatial patch layout")
                grid = patches.transpose(1, 2).reshape(len(images), 384, 32, 32)
                grid = F.normalize(F.adaptive_avg_pool2d(grid, (6, 6)), dim=1)
                values.append(grid.permute(0, 2, 3, 1).cpu().numpy().astype(np.float16))
        result = np.concatenate(values)
        if not self._repeat_checked:
            self._repeat_checked = True
            repeated = self(rows[:2])
            difference = float(np.max(np.abs(result[:2].astype(np.float32) - repeated.astype(np.float32))))
            self.metadata["repeat_max_abs_difference"] = difference
            if difference > .001:
                raise ValueError("PE-Spatial extraction failed repeatability check")
        if self.device.type == "cuda":
            self.metadata["peak_gpu_allocated_bytes"] = int(torch.cuda.max_memory_allocated())
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download", action="store_true")
    args = parser.parse_args()
    print(json.dumps(download() if args.download else spatial_profile(), indent=2))


if __name__ == "__main__":
    main()
