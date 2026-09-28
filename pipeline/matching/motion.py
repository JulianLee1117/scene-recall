"""Small local flow baseline; camera and residual movement remain distinct evidence."""

from __future__ import annotations

from importlib.metadata import version
from pathlib import Path
import hashlib

import numpy as np

from pipeline.matching.cohort import digest, read, write_new

CONTRACT = "raft-small-ctv2-camera-residual-v1"


def prepare_model(directory: Path):
    import torch
    from torchvision.models.optical_flow import Raft_Small_Weights

    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "raft-small-ctv2.pth"
    if not path.exists():
        torch.hub.download_url_to_file(
            Raft_Small_Weights.C_T_V2.url, str(path), hash_prefix="01064c6d"
        )
    content_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    payload = {
        "contract": CONTRACT,
        "weight_sha256": content_hash,
        "versions": {
            name: version(name) for name in ("torch", "torchvision", "numpy", "av")
        },
        "sampling_fps": 6,
        "long_side": 320,
        "grid": 6,
        "camera_fit": "seeded-affine-ransac-64-2px-v1",
    }
    profile = {**payload, "id": digest(payload)}
    manifest = directory / "profile.json"
    if manifest.exists() and read(manifest) != profile:
        raise ValueError(
            "Motion model profile changed; prepare a separate model directory"
        )
    if not manifest.exists():
        write_new(manifest, profile)
    return profile


class Flow:
    def __init__(self, directory: Path, device="cpu"):
        import torch
        from torchvision.models.optical_flow import raft_small

        self.profile = read(directory / "profile.json")
        path = directory / "raft-small-ctv2.pth"
        if (
            self.profile["contract"] != CONTRACT
            or hashlib.sha256(path.read_bytes()).hexdigest()
            != self.profile["weight_sha256"]
        ):
            raise ValueError("Motion checkpoint differs from its prepared profile")
        if self.profile["versions"] != {
            name: version(name) for name in self.profile["versions"]
        }:
            raise ValueError("Motion runtime changed; prepare a new profile")
        if self.profile["id"] != digest(
            {k: v for k, v in self.profile.items() if k != "id"}
        ):
            raise ValueError("Motion profile fingerprint changed")
        self.device = device
        self.model = raft_small(weights=None).to(device).eval()
        self.model.load_state_dict(
            torch.load(path, map_location=device, weights_only=True)
        )

    def pair(self, first, second):
        import torch
        from PIL import Image

        width = max(128, round(first.width * min(1, 320 / max(first.size)) / 8) * 8)
        height = max(128, round(first.height * min(1, 320 / max(first.size)) / 8) * 8)

        def tensor(image):
            values = np.asarray(
                image.resize((width, height), Image.Resampling.BILINEAR)
            ).copy()
            return (
                torch.from_numpy(values)
                .permute(2, 0, 1)
                .float()
                .unsqueeze(0)
                .to(self.device)
                / 127.5
                - 1
            )

        with torch.inference_mode():
            flow = (
                self.model(tensor(first), tensor(second), num_flow_updates=12)[-1][0]
                .permute(1, 2, 0)
                .cpu()
                .numpy()
            )
        return flow


def summarize(flow: np.ndarray, seconds: float):
    """Robust dominant affine flow; inadequate support stays unknown, never static."""
    if seconds <= 0 or not np.isfinite(flow).all():
        raise ValueError("Invalid temporal flow evidence")
    height, width, _ = flow.shape
    ys, xs = np.mgrid[4:height:8, 4:width:8]
    position = np.stack(
        [xs.ravel() / width, ys.ravel() / height, np.ones(xs.size)], axis=1
    )
    vectors = flow[ys, xs].reshape(-1, 2) / np.array([width, height])
    rng = np.random.default_rng(817)
    best = np.zeros(len(position), dtype=bool)
    tolerance = 2 / min(width, height)
    for _ in range(64):
        selected = rng.choice(len(position), 3, replace=False)
        estimate = np.linalg.lstsq(position[selected], vectors[selected], rcond=None)[0]
        inliers = np.linalg.norm(position @ estimate - vectors, axis=1) < tolerance
        if inliers.sum() > best.sum():
            best = inliers
    reliable = float(best.mean()) >= 0.55
    affine = (
        np.linalg.lstsq(position[best], vectors[best], rcond=None)[0]
        if reliable
        else np.zeros((3, 2))
    )
    y, x = np.mgrid[:height, :width]
    coordinates = np.stack([x / width, y / height, np.ones_like(x)], axis=-1)
    normalized = flow / np.array([width, height])
    residual = normalized - coordinates @ affine
    grid = np.zeros((6, 6, 2), dtype=np.float32)
    for gy in range(6):
        for gx in range(6):
            patch = residual[
                gy * height // 6 : (gy + 1) * height // 6,
                gx * width // 6 : (gx + 1) * width // 6,
            ]
            grid[gy, gx] = np.median(patch.reshape(-1, 2), axis=0) / seconds
    center_velocity = np.array([0.5, 0.5, 1]) @ affine / seconds
    camera = np.r_[
        center_velocity,
        np.trace(affine[:2]) / seconds,
        (affine[0, 1] - affine[1, 0]) / seconds,
    ].astype(np.float32)
    return {
        "camera": camera,
        "residual": grid,
        "confidence": float(best.mean()),
        "reliable": reliable,
    }


def photometric_support(first, second, flow):
    """How much of a frame is explained by the measured displacement?

    Forward warping samples the second image at first-image positions plus flow.
    Letterbox borders and out-of-picture samples cannot vote for reliable motion.
    This is a conservative dissolve/corrupt-flow check, not a semantic cut model.
    """
    from PIL import Image
    height, width, _ = flow.shape
    a, b = [np.asarray(image.resize((width, height), Image.Resampling.BILINEAR), dtype=float) / 255
            for image in (first, second)]
    y, x = np.mgrid[:height, :width]
    sx, sy = x + flow[..., 0], y + flow[..., 1]
    valid = (sx >= 0) & (sx < width - 1) & (sy >= 0) & (sy < height - 1)
    x0, y0 = np.clip(sx.astype(int), 0, width - 2), np.clip(sy.astype(int), 0, height - 2)
    wx, wy = np.clip(sx - x0, 0, 1)[..., None], np.clip(sy - y0, 0, 1)[..., None]
    warped = (b[y0, x0] * (1 - wx) * (1 - wy) + b[y0, x0 + 1] * wx * (1 - wy)
              + b[y0 + 1, x0] * (1 - wx) * wy + b[y0 + 1, x0 + 1] * wx * wy)
    active = (a.max(axis=2) > .03) | (warped.max(axis=2) > .03)
    valid &= active
    if valid.mean() < .2:
        return {"reliable": False, "support": 0., "median_error": 1.}
    error = np.abs(a - warped).mean(axis=2)[valid]
    support, median = float((error < .10).mean()), float(np.median(error))
    return {"reliable": support >= .55, "support": support, "median_error": median,
            "contract": "flow-photometric-support-v1"}


def sequence(model, rows, cancelled=lambda: False, *, verify_photometric=False):
    result = []
    for first, second in zip(rows, rows[1:]):
        if cancelled():
            from pipeline.lab.media import JobCancelled

            raise JobCancelled("Matching cancelled")
        flow = model.pair(first.image, second.image)
        item = summarize(flow, second.time - first.time)
        if verify_photometric:
            check = photometric_support(first.image, second.image, flow)
            item["photometric"] = check
            item["reliable"] = item["reliable"] and check["reliable"]
        result.append({**item, "start": first.time, "end": second.time})
    return result


def descriptor(sequence, mode="camera", region=None):
    if (
        len(sequence) < 3
        or sum(item["reliable"] for item in sequence) / len(sequence) < 0.75
    ):
        return None
    verified = any("photometric" in item for item in sequence)
    if verified:
        # A dissolve concentrated at entry must not be hidden by good frames
        # later in the window. Every ordered phase needs actual supported flow.
        if any(sum(item["reliable"] for item in phase) / len(phase) < .75
               for phase in np.array_split(sequence, 3)):
            return None
    values = []
    for item in sequence:
        if mode == "camera":
            values.append(item["camera"])
        else:
            if region is None:
                raise ValueError("Select the moving subject's region")
            x0, y0 = int(region.x * 6), int(region.y * 6)
            x1, y1 = (
                max(x0 + 1, int(np.ceil((region.x + region.width) * 6))),
                max(y0 + 1, int(np.ceil((region.y + region.height) * 6))),
            )
            values.append(item["residual"][y0:y1, x0:x1].mean(axis=(0, 1)))
    values = np.asarray(values)
    if np.linalg.norm(values[:, :2], axis=1).mean() < 0.003 and (
        mode != "camera" or np.abs(values[:, 2:]).mean() < 0.003
    ):
        return None
    phases = np.array_split(np.arange(len(values)), 3)
    return np.asarray([values[[index for index in phase if not verified or sequence[index]["reliable"]]].mean(axis=0)
                       for phase in phases], dtype=np.float32)


def similarity(first, second):
    first, second = np.asarray(first).ravel(), np.asarray(second).ravel()
    length_a, length_b = np.linalg.norm(first), np.linalg.norm(second)
    if min(length_a, length_b) < 1e-8:
        return -1.0
    direction = float(np.dot(first, second) / (length_a * length_b))
    speed = float(np.exp(-abs(np.log(length_a / length_b))))
    return direction * speed
