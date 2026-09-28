"""Local measurement pass: camera motion, hidden cuts, subjects, look and per-sample series.

One streaming decode per film at ``ANALYSIS_FPS`` feeds groups of consecutive
shots to the GPU:

* camera motion — RAFT-small optical flow between consecutive frames, robust
  affine fit (``pipeline.matching.motion.summarize``) and a labelled time
  series (static / pan / tilt / push / pull / roll / handheld / unknown);
* hidden cuts — frame pairs the flow cannot explain and whose colour
  distribution jumps, away from the shot's own boundaries;
* subjects — RF-DETR detections at ``DETECT_FPS`` with a tracked main subject
  (position, size, screen direction);
* look — brightness, contrast, saturation, colourfulness, warmth, palette;
* per-sample series — camera vectors and residual subject motion at
  ``ANALYSIS_FPS``, and sharpness, brightness and boxes at the detection rate,
  so later steps (hero frames, editor timing, match cuts) never re-decode.

Measurements are facts about pixels; nothing here is guessed from stills and
nothing depends on other evidence, so the pass never goes stale when the
understanding pass changes. Hero frames are picked from the stored samples by
``pipeline.evidence.hero``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math
import queue
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable, Iterator

import numpy as np

from pipeline.evidence import store
from pipeline.evidence.library import FilmRef, film_units


ANALYSIS_WIDTH = 640
FLOW_WIDTH = 320
ANALYSIS_FPS = 6.0
DETECT_EVERY = 3                      # every 3rd analysis frame -> 2 fps
RAFT_ITERATIONS = 12
DETECT_THRESHOLD = 0.45
GROUP_PAIRS = 96                      # flow pairs per GPU group
QUEUE_FRAMES = 480                    # decoded frames buffered ahead of the GPU (~80 s of film, ~250 MB)
MOVE, ZOOM, ROLL, SHAKE = 0.02, 0.03, 0.05, 0.012
# Camera labels are re-derived from the stored flow series at compile time
# (camera_from_series), so these labeling constants are versioned there rather
# than in the producer identity: improving labels never needs a new GPU pass.
CAMERA_LABELS_VERSION = 2
DRIFT_MOVE, DRIFT_ZOOM = 0.15, 0.12      # accumulated over a shot: 15% of the frame, 12% scale change
MAX_BOXES = 6                         # largest detections kept per sample
LOOK_KEYS = ("brightness", "contrast", "saturation", "colorfulness", "warmth")
RAFT_CHECKPOINT = "matching/models/raft-small-ctv2/raft-small-ctv2.pth"

PRODUCER = store.Producer(
    kind="measure",
    name="local",
    version=1,
    settings={
        "analysis": {"fps": ANALYSIS_FPS, "width": ANALYSIS_WIDTH},
        "flow": {"model": "raft-small-c_t_v2", "width": FLOW_WIDTH, "iterations": RAFT_ITERATIONS,
                 "fit": "seeded-affine-ransac-64-2px-gpu-v1", "support": "flow-warp-0.1-gpu-v1"},
        "camera": {"move": MOVE, "zoom": ZOOM, "roll": ROLL, "shake": SHAKE, "min_run_s": 0.5},
        "detect": {"model": "rf-detr-small-1.11-fp16", "every": DETECT_EVERY, "threshold": DETECT_THRESHOLD,
                   "boxes_per_sample": MAX_BOXES},
        "series": "flow[t,vx,vy,div,curl,residual,ok]+frames[t,sharpness,brightness,boxes]-v1",
        "look": "content-box-weighted-gpu-v1",
    },
)


# ---------------------------------------------------------------------------
# Decoding
# ---------------------------------------------------------------------------


def display_size(path: Path, width: int) -> tuple[int, int]:
    import av
    with av.open(str(path)) as container:
        stream = container.streams.video[0]
        sar = float(stream.sample_aspect_ratio or 1)
        display_width = stream.codec_context.width * sar
        height = stream.codec_context.height
    return width, max(2, int(round(width * height / display_width / 2)) * 2)


def stream_frames(path: Path, *, fps: float = ANALYSIS_FPS, width: int = ANALYSIS_WIDTH) -> Iterator[tuple[float, np.ndarray]]:
    """Yield ``(time, rgb uint8 HxWx3)`` at a fixed rate from one GPU-decoded pass."""
    out_w, out_h = display_size(path, width)
    frame_bytes = out_w * out_h * 3
    video_filter = f"fps={fps},scale=w='max(2,trunc(iw*sar/2)*2)':h=ih,setsar=1,scale={out_w}:{out_h}"
    command = ["ffmpeg", "-nostdin", "-v", "error", "-hwaccel", "cuda", "-i", str(path), "-map", "0:v:0", "-an", "-sn",
               "-vf", video_filter, "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=frame_bytes * 4)
    frames: queue.Queue = queue.Queue(maxsize=QUEUE_FRAMES)

    def reader() -> None:
        index = 0
        try:
            while True:
                data = process.stdout.read(frame_bytes)
                if len(data) < frame_bytes:
                    break
                frames.put((index / fps, np.frombuffer(data, dtype=np.uint8).reshape(out_h, out_w, 3)))
                index += 1
        finally:
            frames.put(None)

    thread = threading.Thread(target=reader, daemon=True)
    thread.start()
    try:
        while (item := frames.get()) is not None:
            yield item
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()
        thread.join(timeout=5)
        process.stdout.close()
        process.stderr.close()


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class Models:
    """Lazily loaded GPU models shared across films."""

    def __init__(self, assets_dir: Path, device: str | None = None):
        import torch
        self.torch = torch
        self.device = torch.device(device or ("cuda" if torch.cuda.is_available() else "cpu"))
        self._assets_dir = Path(assets_dir)
        self._raft = None
        self._detector = None
        self._classes: dict[int, str] | None = None

    @property
    def raft(self):
        if self._raft is None:
            from torchvision.models.optical_flow import raft_small
            model = raft_small(weights=None).to(self.device).eval()
            state = self.torch.load(self._assets_dir / RAFT_CHECKPOINT, map_location=self.device, weights_only=True)
            model.load_state_dict(state)
            self._raft = model
        return self._raft

    @property
    def detector(self):
        if self._detector is None:
            from rfdetr import RFDETRSmall
            from rfdetr.assets.coco_classes import COCO_CLASSES
            self._detector = RFDETRSmall()
            if self.device.type == "cuda":
                self._detector.inference(compile=False, dtype=self.torch.float16)   # 1.6x throughput, same boxes
            self._classes = dict(COCO_CLASSES)
        return self._detector

    @property
    def classes(self) -> dict[int, str]:
        _ = self.detector
        return self._classes or {}

    def pairs(self, first: np.ndarray, second: np.ndarray, seconds: np.ndarray) -> dict[str, np.ndarray]:
        """Flow plus its camera/residual/support summary for N frame pairs, entirely on the GPU.

        Inputs are N×H×W×3 uint8 frames and N pair durations; outputs are
        per-pair arrays (see :func:`summarize_pairs`).
        """
        torch = self.torch
        a = torch.from_numpy(first).to(self.device).permute(0, 3, 1, 2).float()
        b = torch.from_numpy(second).to(self.device).permute(0, 3, 1, 2).float()
        height = max(64, int(round(a.shape[2] * FLOW_WIDTH / a.shape[3] / 8)) * 8)
        size = (height, FLOW_WIDTH)
        a = torch.nn.functional.interpolate(a, size=size, mode="bilinear", align_corners=False) / 127.5 - 1
        b = torch.nn.functional.interpolate(b, size=size, mode="bilinear", align_corners=False) / 127.5 - 1
        with torch.inference_mode():
            flow = self.raft(a, b, num_flow_updates=RAFT_ITERATIONS)[-1].permute(0, 2, 3, 1).float()
            summary = summarize_pairs(flow, (a + 1) / 2, (b + 1) / 2, torch.as_tensor(seconds, device=self.device))
        return {key: value.cpu().numpy() for key, value in summary.items()}

    def upload(self, frames: list[np.ndarray]) -> Any:
        """One host-to-device copy of N×H×W×3 uint8 frames."""
        return self.torch.from_numpy(np.stack(frames)).to(self.device, non_blocking=True)

    def detect(self, frames: Any) -> list[list[tuple[str, float, tuple[float, float, float, float]]]]:
        """Detections for uploaded N×H×W×3 uint8 frames (boxes as frame fractions)."""
        if len(frames) == 0:
            return []
        images = [frame.permute(2, 0, 1).float().div_(255) for frame in frames]
        results = self.detector.predict(images, threshold=DETECT_THRESHOLD, include_source_image=False)
        if not isinstance(results, list):
            results = [results]
        out = []
        height, width = frames.shape[1:3]
        for detections in results:
            rows = []
            for box, cls, conf in zip(detections.xyxy, detections.class_id, detections.confidence):
                x0, y0, x1, y1 = (float(box[0]) / width, float(box[1]) / height, float(box[2]) / width, float(box[3]) / height)
                rows.append((self.classes.get(int(cls), str(int(cls))), round(float(conf), 3),
                             (round(x0, 4), round(y0, 4), round(x1, 4), round(y1, 4))))
            out.append(rows)
        return out

    def frame_stats(self, frames: Any) -> dict[str, np.ndarray]:
        """Content box, look statistics and sharpness for uploaded N×H×W×3 uint8 frames.

        Statistics are weighted to the content box (letterbox/pillarbox bars
        excluded, see :func:`content_box`); sharpness is the Laplacian
        variance inside it, two pixels clear of the bar edge.
        """
        torch = self.torch
        with torch.inference_mode():
            brightest = frames[:, ::2, ::2].amax(dim=3) > 12
            rows_any = brightest.any(dim=2).cpu().numpy()
            cols_any = brightest.any(dim=1).cpu().numpy()
            count, height, width = frames.shape[:3]
            boxes = np.array([_content_span(rows, height) + _content_span(cols, width)
                              for rows, cols in zip(rows_any, cols_any)])
            ys = torch.arange(height, device=self.device).view(1, -1)
            xs = torch.arange(width, device=self.device).view(1, -1)
            box = torch.from_numpy(boxes).to(self.device)

            def weights(inset: int) -> Any:
                row = (ys >= box[:, 0:1] + inset) & (ys < box[:, 1:2] - inset)
                col = (xs >= box[:, 2:3] + inset) & (xs < box[:, 3:4] - inset)
                return (row.unsqueeze(2) & col.unsqueeze(1)).float()          # N×H×W

            def mean(values: Any, weight: Any) -> Any:
                return (values * weight).sum(dim=(1, 2)) / weight.sum(dim=(1, 2)).clamp(min=1)

            rgb = frames.float() / 255
            r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
            w = weights(0)
            luma = 0.2126 * r + 0.7152 * g + 0.0722 * b
            brightness = mean(luma, w)
            contrast = (mean(luma ** 2, w) - brightness ** 2).clamp(min=0).sqrt()
            high, low = rgb.amax(dim=3), rgb.amin(dim=3)
            saturation = mean(torch.where(high > 1e-6, (high - low) / high.clamp(min=1e-6), torch.zeros_like(high)), w)
            rg, yb = r - g, 0.5 * (r + g) - b
            rg_mean, yb_mean = mean(rg, w), mean(yb, w)
            rg_std = (mean(rg ** 2, w) - rg_mean ** 2).clamp(min=0).sqrt()
            yb_std = (mean(yb ** 2, w) - yb_mean ** 2).clamp(min=0).sqrt()
            colorfulness = (rg_std ** 2 + yb_std ** 2).sqrt() + 0.3 * (rg_mean ** 2 + yb_mean ** 2).sqrt()
            warmth = mean(r - b, w)
            gray = (rgb.mean(dim=3) * 255).unsqueeze(1)
            kernel = torch.tensor([[0, 1, 0], [1, -4, 1], [0, 1, 0]], dtype=torch.float32, device=self.device).view(1, 1, 3, 3)
            laplacian = torch.nn.functional.conv2d(gray, kernel, padding=1).squeeze(1)
            sharp_weight = weights(2)
            lap_mean = mean(laplacian, sharp_weight)
            sharpness = (mean(laplacian ** 2, sharp_weight) - lap_mean ** 2).clamp(min=0)
        values = {"brightness": brightness, "contrast": contrast, "saturation": saturation,
                  "colorfulness": colorfulness, "warmth": warmth, "sharpness": sharpness}
        out = {key: value.cpu().numpy() for key, value in values.items()}
        out["content_box"] = boxes
        return out


# ---------------------------------------------------------------------------
# Pure measurement helpers
# ---------------------------------------------------------------------------


_HYPOTHESES: dict[int, np.ndarray] = {}


def _ransac_hypotheses(count: int) -> np.ndarray:
    """The 64 seeded point triples of ``pipeline.matching.motion.summarize`` for *count* grid points."""
    if count not in _HYPOTHESES:
        rng = np.random.default_rng(817)
        _HYPOTHESES[count] = np.stack([rng.choice(count, 3, replace=False) for _ in range(64)])
    return _HYPOTHESES[count]


def summarize_pairs(flow: Any, first: Any, second: Any, seconds: Any) -> dict[str, Any]:
    """Batched dominant-affine camera fit, residual subject motion and photometric support.

    A tensor port of ``pipeline.matching.motion.summarize`` (same 8-px grid,
    seeded hypotheses, 2-px inlier tolerance, 55% support rule and
    least-squares refit), plus the flow-warp support check.

    ``flow`` is B×h×w×2 in pixels; ``first``/``second`` are B×3×h×w in [0, 1];
    ``seconds`` is B. Returns tensors: ``camera`` B×4 [vx, vy, div, curl] per
    second (frame fractions), ``confidence``, ``reliable``, ``residual`` (mean
    absolute 6×6 median residual, per second) and ``support`` (share of
    active pixels the flow explains).
    """
    import torch

    batch, height, width, _ = flow.shape
    device = flow.device
    scale = torch.tensor([width, height], dtype=torch.float64, device=device)
    ys = torch.arange(4, height, 8, device=device)
    xs = torch.arange(4, width, 8, device=device)
    grid_y, grid_x = torch.meshgrid(ys, xs, indexing="ij")
    position = torch.stack([grid_x.flatten() / width, grid_y.flatten() / height,
                            torch.ones(grid_x.numel(), device=device)], dim=1).double()        # N×3
    vectors = flow[:, grid_y.flatten(), grid_x.flatten(), :].double() / scale                   # B×N×2
    hypotheses = torch.from_numpy(_ransac_hypotheses(position.shape[0])).to(device)            # 64×3
    estimates = torch.linalg.pinv(position[hypotheses]).unsqueeze(0) @ vectors[:, hypotheses]  # B×64×3×2
    predicted = position.unsqueeze(0).unsqueeze(0) @ estimates                                 # B×64×N×2
    tolerance = 2 / min(width, height)
    inliers = torch.linalg.norm(predicted - vectors.unsqueeze(1), dim=-1) < tolerance          # B×64×N
    best = inliers[torch.arange(batch, device=device), inliers.sum(dim=-1).argmax(dim=1)]      # first maximum wins
    confidence = best.double().mean(dim=1)
    reliable = confidence >= 0.55
    weights = best.double().unsqueeze(-1)
    normal = position.T.unsqueeze(0) @ (weights * position.unsqueeze(0))                      # B×3×3
    target = position.T.unsqueeze(0) @ (weights * vectors)                                     # B×3×2
    ridge = 1e-9 * torch.eye(3, dtype=torch.float64, device=device)
    affine = torch.linalg.solve(normal + ridge, target) * reliable.view(-1, 1, 1)              # B×3×2
    per_second = seconds.double().view(-1, 1)
    center = torch.tensor([0.5, 0.5, 1.0], dtype=torch.float64, device=device)
    camera = torch.cat([
        (center @ affine) / per_second,
        (affine[:, 0, 0] + affine[:, 1, 1]).unsqueeze(1) / per_second,
        (affine[:, 0, 1] - affine[:, 1, 0]).unsqueeze(1) / per_second,
    ], dim=1)

    full_y, full_x = torch.meshgrid(torch.arange(height, device=device), torch.arange(width, device=device), indexing="ij")
    coordinates = torch.stack([full_x / width, full_y / height, torch.ones_like(full_x, dtype=torch.float32)],
                              dim=-1).double()                                                  # h×w×3
    residual = flow.double() / scale - coordinates.unsqueeze(0) @ affine.unsqueeze(1)          # B×h×w×2
    cells = []
    for gy in range(6):
        for gx in range(6):
            patch = residual[:, gy * height // 6:(gy + 1) * height // 6, gx * width // 6:(gx + 1) * width // 6]
            cells.append(patch.reshape(batch, -1, 2).median(dim=1).values)
    residual_energy = (torch.stack(cells, dim=1).abs() / per_second.unsqueeze(1)).mean(dim=(1, 2))

    sample_x = torch.clamp(torch.round(full_x.unsqueeze(0) + flow[..., 0]).long(), 0, width - 1)
    sample_y = torch.clamp(torch.round(full_y.unsqueeze(0) + flow[..., 1]).long(), 0, height - 1)
    index = (sample_y * width + sample_x).view(batch, 1, -1).expand(-1, 3, -1)
    warped = second.reshape(batch, 3, -1).gather(2, index).view(batch, 3, height, width)
    active = (first.amax(dim=1) > 0.04) | (warped.amax(dim=1) > 0.04)                         # B×h×w
    explained = ((first - warped).abs().mean(dim=1) < 0.1) & active
    active_share = active.float().mean(dim=(1, 2))
    support = explained.float().sum(dim=(1, 2)) / active.float().sum(dim=(1, 2)).clamp(min=1)
    support = torch.where(active_share < 0.2, torch.ones_like(support), support)             # mostly black: no evidence
    return {"camera": camera.float(), "confidence": confidence.float(), "reliable": reliable,
            "residual": residual_energy.float(), "support": support}


def color_histogram(image: np.ndarray) -> np.ndarray:
    small = image[::4, ::4].reshape(-1, 3) // 32
    index = small[:, 0].astype(np.int32) * 64 + small[:, 1] * 8 + small[:, 2]
    hist = np.bincount(index, minlength=512).astype(np.float32)
    return hist / max(1.0, hist.sum())


def histogram_distance(a: np.ndarray, b: np.ndarray) -> float:
    """Chi-square distance in [0, 1] between normalized histograms."""
    denominator = a + b
    mask = denominator > 0
    return float(0.5 * np.sum((a[mask] - b[mask]) ** 2 / denominator[mask]))


def content_box(image: np.ndarray, threshold: int = 12) -> tuple[int, int, int, int]:
    """``(y0, y1, x0, x1)`` inside letterbox/pillarbox bars; the full frame when bars are absent or unclear.

    Bars must be near-black, roughly symmetric and each under a quarter of the
    frame, so a dark sky or a black wall on one side is never cropped.
    """
    brightest = image[::2, ::2].max(axis=2)
    height, width = image.shape[:2]
    y0, y1 = _content_span(brightest.max(axis=1) > threshold, height)
    x0, x1 = _content_span(brightest.max(axis=0) > threshold, width)
    return y0, y1, x0, x1


def _content_span(mask: np.ndarray, size: int) -> tuple[int, int]:
    """Span of non-black lines (sampled every 2 px) unless the bars look one-sided or oversized."""
    inside = np.flatnonzero(mask)
    if inside.size == 0:
        return 0, size
    first, last = int(inside[0]) * 2, min(size, (int(inside[-1]) + 1) * 2)
    before, after = first, size - last
    if max(before, after) > 0.25 * size or abs(before - after) > 0.08 * size + 4:
        return 0, size
    return first, last


def palette(image: np.ndarray, colors: int = 4, iterations: int = 8) -> list[list[float]]:
    """Dominant colours as ``[r, g, b, share]`` via a few k-means iterations (bars excluded)."""
    y0, y1, x0, x1 = content_box(image)
    pixels = image[y0:y1:8, x0:x1:8].reshape(-1, 3).astype(np.float32)
    if len(pixels) < colors:
        return []
    rng = np.random.default_rng(7)
    centers = pixels[rng.choice(len(pixels), colors, replace=False)]
    for _ in range(iterations):
        labels = np.argmin(((pixels[:, None, :] - centers[None, :, :]) ** 2).sum(axis=2), axis=1)
        for k in range(colors):
            members = pixels[labels == k]
            if len(members):
                centers[k] = members.mean(axis=0)
    labels = np.argmin(((pixels[:, None, :] - centers[None, :, :]) ** 2).sum(axis=2), axis=1)
    shares = np.bincount(labels, minlength=colors) / len(labels)
    order = np.argsort(-shares)
    return [[int(centers[k][0]), int(centers[k][1]), int(centers[k][2]), round(float(shares[k]), 3)] for k in order]


def pair_label(camera: np.ndarray, reliable: bool) -> str:
    if not reliable:
        return "unknown"
    vx, vy, div, curl = (float(value) for value in camera)
    speed = math.hypot(vx, vy)
    if abs(div) >= ZOOM and abs(div) >= 0.7 * speed:
        return "push_in" if div > 0 else "pull_out"
    if speed >= MOVE:
        return _direction(vx, vy)
    if abs(curl) >= ROLL:
        return "roll"
    return "static"


def _direction(vx: float, vy: float) -> str:
    """Camera direction from image motion, which moves opposite to the camera."""
    if abs(vx) >= 1.5 * abs(vy):
        return "pan_right" if vx < 0 else "pan_left"
    if abs(vy) >= 1.5 * abs(vx):
        return "tilt_up" if vy > 0 else "tilt_down"
    return "diagonal"


def camera_segments(times: list[float], cameras: list[np.ndarray], reliable: list[bool], dt: float) -> dict[str, Any]:
    """Label each flow pair, smooth, detect handheld shake and merge into runs."""
    if not times:
        return {"segments": [], "dominant": "unknown", "moving": 0.0, "reliability": 0.0}
    labels = [pair_label(camera, ok) for camera, ok in zip(cameras, reliable)]
    ok_cameras = [camera for camera, ok in zip(cameras, reliable) if ok]
    shake = 0.0
    if len(ok_cameras) >= 4:
        velocities = np.array([camera[:2] for camera in ok_cameras])
        shake = float(np.median(np.linalg.norm(np.diff(velocities, axis=0), axis=1)))
    if shake >= SHAKE:
        labels = ["handheld" if label == "static" else label for label in labels]
    smoothed = [max(set(labels[max(0, i - 1): i + 2]), key=labels[max(0, i - 1): i + 2].count) for i in range(len(labels))]
    runs: list[list[Any]] = []
    for time_value, label in zip(times, smoothed):
        if runs and runs[-1][2] == label:
            runs[-1][1] = time_value + dt
        else:
            runs.append([time_value, time_value + dt, label])
    merged: list[list[Any]] = []
    for run in runs:
        if merged and run[1] - run[0] < 0.5 and merged[-1][2] != "unknown":
            merged[-1][1] = run[1]
        else:
            merged.append(run)
    durations: dict[str, float] = {}
    for start, end, label in merged:
        durations[label] = durations.get(label, 0.0) + end - start
    known = {label: seconds for label, seconds in durations.items() if label != "unknown"}
    total = sum(durations.values()) or 1.0
    dominant = max(known, key=known.get) if known and sum(known.values()) / total >= 0.5 else "unknown"
    moving = sum(seconds for label, seconds in known.items() if label not in {"static"}) / total
    mean = np.mean(np.array(ok_cameras), axis=0) if ok_cameras else np.zeros(4)
    drift = mean * dt * len(labels)                   # accumulated [dx, dy, dscale, droll] over the shot
    slow = False
    if dominant == "static" and len(ok_cameras) >= 6:
        # Sustained slow moves (a creeping push-in, a slow pan) stay under the
        # per-pair thresholds but add up; require most pairs to agree on sign.
        values = np.array(ok_cameras)

        def agrees(column: int) -> bool:
            return float(np.mean(np.sign(values[:, column]) == np.sign(drift[column]))) >= 0.7

        if abs(drift[2]) >= DRIFT_ZOOM and agrees(2):
            dominant, slow = ("push_in" if drift[2] > 0 else "pull_out"), True
        elif math.hypot(drift[0], drift[1]) >= DRIFT_MOVE:
            axis = 0 if abs(drift[0]) >= abs(drift[1]) else 1
            if agrees(axis):
                dominant, slow = _direction(float(mean[0]), float(mean[1])), True
        if slow:
            moving = max(moving, 0.5)
    return {"segments": [[round(s, 2), round(e, 2), label] for s, e, label in merged], "dominant": dominant,
            "slow": slow, "moving": round(moving, 3), "reliability": round(sum(reliable) / len(reliable), 3),
            "shake": round(shake, 4), "mean": [round(float(value), 4) for value in mean],
            "drift": [round(float(value), 4) for value in drift]}


def camera_from_series(series: list[list[float]]) -> dict[str, Any]:
    """Camera labels re-derived from a stored flow series ``[t, vx, vy, div, curl, residual, ok]``."""
    return camera_segments([row[0] for row in series], [np.array(row[1:5], dtype=np.float64) for row in series],
                           [bool(row[6]) for row in series], 1.0 / ANALYSIS_FPS)


def track_main_subject(detections: list[list[tuple[str, float, tuple[float, float, float, float]]]]) -> dict[str, Any] | None:
    """Follow the largest person (else largest object) across sampled frames."""
    def area(box):
        return max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])
    people = [[d for d in frame if d[0] == "person"] for frame in detections]
    pool = people if any(people) else detections
    track, previous = [], None
    for frame in pool:
        if not frame:
            continue
        if previous is None:
            choice = max(frame, key=lambda d: area(d[2]))
        else:
            px, py = (previous[0] + previous[2]) / 2, (previous[1] + previous[3]) / 2
            choice = min(frame, key=lambda d: math.hypot((d[2][0] + d[2][2]) / 2 - px, (d[2][1] + d[2][3]) / 2 - py) - area(d[2]))
        track.append(choice)
        previous = choice[2]
    if not track:
        return None
    first, last = track[0][2], track[-1][2]
    center = lambda box: ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)
    (x0, y0), (x1, y1) = center(first), center(last)
    a0, a1 = area(first), area(last)
    dx, dy = x1 - x0, y1 - y0
    if a0 > 0 and a1 / a0 > 1.35:
        direction = "toward"
    elif a0 > 0 and a1 / a0 < 0.74:
        direction = "away"
    elif abs(dx) >= 0.08 and abs(dx) >= abs(dy):
        direction = "right" if dx > 0 else "left"
    elif abs(dy) >= 0.08:
        direction = "down" if dy > 0 else "up"
    else:
        direction = "still"
    mean_box = np.mean(np.array([d[2] for d in track]), axis=0)
    return {"class": track[0][0], "box_start": list(first), "box_end": list(last),
            "center": [round(float((mean_box[0] + mean_box[2]) / 2), 3), round(float((mean_box[1] + mean_box[3]) / 2), 3)],
            "size": round(float(area(tuple(mean_box))), 4), "height": round(float(mean_box[3] - mean_box[1]), 3),
            "direction": direction, "dx": round(dx, 3), "dy": round(dy, 3)}


# ---------------------------------------------------------------------------
# Per-shot accumulation
# ---------------------------------------------------------------------------


@dataclass
class ShotState:
    unit_id: str
    t_start: float
    t_end: float
    frames: list[tuple[float, np.ndarray]] = field(default_factory=list)


def _shot_window(unit: dict[str, Any]) -> tuple[float, float]:
    start, end = float(unit["t_start"]), float(unit["t_end"])
    margin = min(0.08, (end - start) / 10)
    return start + margin, end - margin


def measure_group(models: Models, shots: list[ShotState]) -> dict[str, dict[str, Any]]:
    """Measure a group of consecutive shots with batched GPU work."""
    pairs = [(shot_index, k) for shot_index, shot in enumerate(shots) for k in range(len(shot.frames) - 1)]
    motion: dict[tuple[int, int], dict[str, Any]] = {}
    for start in range(0, len(pairs), 48):
        batch = pairs[start:start + 48]
        first = np.stack([shots[s].frames[k][1] for s, k in batch])
        second = np.stack([shots[s].frames[k + 1][1] for s, k in batch])
        seconds = np.array([shots[s].frames[k + 1][0] - shots[s].frames[k][0] for s, k in batch], dtype=np.float64)
        summary = models.pairs(first, second, seconds)
        for position, key in enumerate(batch):
            motion[key] = {name: values[position] for name, values in summary.items()}
    detect_refs = []
    for shot_index, shot in enumerate(shots):
        chosen = list(range(0, len(shot.frames), DETECT_EVERY)) or []
        if shot.frames and not chosen:
            chosen = [len(shot.frames) // 2]
        detect_refs.extend((shot_index, k) for k in chosen)
    detections: dict[tuple[int, int], list] = {}
    stats: dict[tuple[int, int], dict[str, Any]] = {}
    for start in range(0, len(detect_refs), 32):
        refs_batch = detect_refs[start:start + 32]
        uploaded = models.upload([shots[s].frames[k][1] for s, k in refs_batch])
        for ref, rows in zip(refs_batch, models.detect(uploaded)):
            detections[ref] = rows
        batch_stats = models.frame_stats(uploaded)
        for position, ref in enumerate(refs_batch):
            stats[ref] = {name: values[position] for name, values in batch_stats.items()}
    sharp = {ref: float(values["sharpness"]) for ref, values in stats.items()}

    results: dict[str, dict[str, Any]] = {}
    for shot_index, shot in enumerate(shots):
        record: dict[str, Any] = {"samples": len(shot.frames)}
        if len(shot.frames) >= 2:
            times, cameras, reliable, cuts, residual, series = [], [], [], [], [], []
            for k in range(len(shot.frames) - 1):
                t0, a = shot.frames[k]
                t1, b = shot.frames[k + 1]
                summary = motion[(shot_index, k)]
                support = float(summary["support"])
                reliable_pair = bool(summary["reliable"]) and support >= 0.45
                times.append(t0)
                cameras.append(summary["camera"])
                reliable.append(reliable_pair)
                pair_residual = float(summary["residual"])
                if reliable_pair:
                    residual.append(pair_residual)
                series.append([round(t0, 3), *(round(float(v), 4) for v in summary["camera"]),
                               round(pair_residual, 4), int(reliable_pair)])
                if support < 0.3 and histogram_distance(color_histogram(a), color_histogram(b)) > 0.45 \
                        and t0 - shot.t_start > 0.25 and shot.t_end - t1 > 0.25:
                    cuts.append(round((t0 + t1) / 2, 2))
            record["camera"] = camera_segments(times, cameras, reliable, 1.0 / ANALYSIS_FPS)
            record["flow"] = series
            record["cuts"] = cuts
            # Subject motion after removing the camera's dominant motion (frame fractions per second).
            record["motion_energy"] = round(float(np.mean(residual)), 4) if residual else None
        else:
            record["camera"] = {"segments": [], "dominant": "unknown", "moving": 0.0, "reliability": 0.0}
            record["flow"] = []
            record["cuts"] = []
            record["motion_energy"] = None
        refs = [ref for ref in detect_refs if ref[0] == shot_index]
        frame_detections = [detections.get(ref, []) for ref in refs]
        people = [sum(1 for d in rows if d[0] == "person") for rows in frame_detections]
        classes: dict[str, int] = {}
        for rows in frame_detections:
            for cls in {d[0] for d in rows}:
                classes[cls] = classes.get(cls, 0) + 1
        record["subjects"] = {"people_median": float(np.median(people)) if people else 0.0,
                              "people_max": int(max(people)) if people else 0,
                              "objects": dict(sorted(classes.items(), key=lambda kv: -kv[1])[:8]),
                              "main": track_main_subject(frame_detections)}
        looks = [{key: float(stats[ref][key]) for key in LOOK_KEYS} for ref in refs]
        record["_boxes"] = [tuple(int(v) for v in stats[ref]["content_box"]) + shots[ref[0]].frames[ref[1]][1].shape[:2]
                            for ref, look in zip(refs, looks) if look["brightness"] > 0.08]
        record["look"] = {key: round(float(np.mean([look[key] for look in looks])), 4) for key in LOOK_KEYS} if looks else {}
        if refs:
            sharp_values = [sharp.get(ref, 0.0) for ref in refs]
            record["sharpness"] = round(float(np.median(sharp_values)), 2)
            sharpest = refs[int(np.argmax(sharp_values))]
            record["palette"] = palette(shots[sharpest[0]].frames[sharpest[1]][1])
            record["frames"] = [
                [round(shots[s].frames[k][0], 3), round(value, 1), round(look["brightness"], 3),
                 [[cls, conf, *box] for cls, conf, box in sorted(rows, key=lambda d: -_box_area(d[2]))[:MAX_BOXES]]]
                for (s, k), value, look, rows in zip(refs, sharp_values, looks, frame_detections)]
        results[shot.unit_id] = record
    return results


def dark_spans(frames: list[Any] | None, *, threshold: float = 0.03, min_seconds: float = 0.4) -> list[list[float]]:
    """Near-black stretches (fades, black frames) inside a shot from its sampled brightness.

    ``frames`` rows are ``[t, sharpness, brightness, boxes]``. A stretch is a
    run of samples below ``threshold`` mean luma lasting at least
    ``min_seconds``, widened by half a sample spacing on each side.
    """
    samples = [(float(row[0]), float(row[2])) for row in frames or [] if len(row) >= 3]
    if len(samples) < 2:
        return []
    spacing = float(np.median(np.diff([t for t, _ in samples]))) if len(samples) > 1 else 0.0
    spans, run = [], []
    for time, brightness in samples + [(float("inf"), 1.0)]:
        if brightness < threshold:
            run.append(time)
            continue
        if run and run[-1] - run[0] + spacing >= min_seconds:
            spans.append([round(run[0] - spacing / 2, 3), round(run[-1] + spacing / 2, 3)])
        run = []
    return spans


def _box_area(box: tuple[float, float, float, float]) -> float:
    return max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])


# ---------------------------------------------------------------------------
# Producer run
# ---------------------------------------------------------------------------


def measure_film(config: Any, db: Any, film: FilmRef, models: Models, progress: Callable[[str], None] = print,
                 *, max_seconds: float | None = None) -> dict[str, Any]:
    started = time.perf_counter()
    units = film_units(db, film.film_id)
    if max_seconds is not None:  # diagnostic runs over the opening only
        units = [unit for unit in units if float(unit["t_end"]) <= max_seconds]
    windows = [(_shot_window(unit), unit) for unit in units]
    results: dict[str, dict[str, Any]] = {}
    group: list[ShotState] = []
    group_pairs = 0
    index = 0
    current: ShotState | None = None

    def flush() -> None:
        nonlocal group, group_pairs
        if group:
            results.update(measure_group(models, group))
        group, group_pairs = [], 0

    for t, frame in stream_frames(film.path):
        while index < len(windows) and t > windows[index][0][1]:
            unit = windows[index][1]
            state = current if current is not None and current.unit_id == unit["unit_id"] else \
                ShotState(unit["unit_id"], float(unit["t_start"]), float(unit["t_end"]))
            group.append(state)
            group_pairs += max(0, len(state.frames) - 1)
            current = None
            index += 1
            if group_pairs >= GROUP_PAIRS or len(group) >= 64:
                flush()
        if index >= len(windows):
            break
        (low, high), unit = windows[index]
        if low <= t <= high:
            if current is None or current.unit_id != unit["unit_id"]:
                current = ShotState(unit["unit_id"], float(unit["t_start"]), float(unit["t_end"]))
            current.frames.append((t, frame))
    while index < len(windows):
        unit = windows[index][1]
        state = current if current is not None and current.unit_id == unit["unit_id"] else \
            ShotState(unit["unit_id"], float(unit["t_start"]), float(unit["t_end"]))
        group.append(state)
        current = None
        index += 1
    flush()
    boxes = [box for record in results.values() for box in record.pop("_boxes", [])]
    content = None
    if boxes:
        y0, y1, x0, x1, height, width = np.median(np.array(boxes, dtype=np.float32), axis=0)
        content = [round(float(x0 / width), 4), round(float(y0 / height), 4), round(float(x1 / width), 4),
                   round(float(y1 / height), 4)]
    return {"shots": results, "units": len(units), "measured": len(results), "content_box": content,
            "elapsed_s": round(time.perf_counter() - started, 1)}


def run(config: Any, db: Any, films: list[FilmRef], *, force: bool = False, lock_films: bool = True,
        progress: Callable[[str], None] = print) -> dict[str, int]:
    """Measure films (skipping current artifacts). ``lock_films=False`` when the caller holds the film lock."""
    from pipeline.evidence.understanding import shots_digest
    from pipeline.ingest.locks import film_operation_lock
    models = Models(config.paths.assets_dir)
    counts = {"cached": 0, "done": 0, "failed": 0}
    for number, film in enumerate(films, start=1):
        units = film_units(db, film.film_id)
        inputs = {"shots": shots_digest(units)}
        if not force and store.read_artifact(config.paths.assets_dir, film.film_id, PRODUCER, inputs=inputs):
            counts["cached"] += 1
            continue
        try:
            if lock_films:
                with film_operation_lock(Path(config.paths.assets_dir) / film.film_id):
                    data = measure_film(config, db, film, models, progress)
            else:
                data = measure_film(config, db, film, models, progress)
        except Exception as exc:  # noqa: BLE001 - one film must not stop a library run
            counts["failed"] += 1
            progress(f"[measure] {film.title}: failed ({str(exc)[:300]})")
            continue
        store.write_artifact(config.paths.assets_dir, film.film_id, PRODUCER, data, inputs=inputs, compress=True)
        counts["done"] += 1
        progress(f"[measure] {number}/{len(films)} {film.title}: {data['measured']}/{data['units']} shots, "
                 f"{data['elapsed_s']}s")
    return counts
