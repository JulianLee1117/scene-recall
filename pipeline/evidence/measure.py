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
MOVE, ZOOM, ROLL, SHAKE = 0.02, 0.03, 0.05, 0.012
MAX_BOXES = 6                         # largest detections kept per sample
RAFT_CHECKPOINT = "matching/models/raft-small-ctv2/raft-small-ctv2.pth"

PRODUCER = store.Producer(
    kind="measure",
    name="local",
    version=1,
    settings={
        "analysis": {"fps": ANALYSIS_FPS, "width": ANALYSIS_WIDTH},
        "flow": {"model": "raft-small-c_t_v2", "width": FLOW_WIDTH, "iterations": RAFT_ITERATIONS,
                 "fit": "seeded-affine-ransac-64-2px-v1"},
        "camera": {"move": MOVE, "zoom": ZOOM, "roll": ROLL, "shake": SHAKE, "min_run_s": 0.5},
        "detect": {"model": "rf-detr-small-1.11-fp16", "every": DETECT_EVERY, "threshold": DETECT_THRESHOLD,
                   "boxes_per_sample": MAX_BOXES},
        "series": "flow[t,vx,vy,div,curl,residual,ok]+frames[t,sharpness,brightness,boxes]-v1",
        "look": "content-box-cropped-v1",
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
    frames: queue.Queue = queue.Queue(maxsize=64)

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

    def flow(self, first: np.ndarray, second: np.ndarray) -> np.ndarray:
        """Batched optical flow; inputs N×H×W×3 uint8, output N×h×w×2 in flow-resolution pixels."""
        torch = self.torch
        a = torch.from_numpy(first).to(self.device).permute(0, 3, 1, 2).float()
        b = torch.from_numpy(second).to(self.device).permute(0, 3, 1, 2).float()
        height = max(64, int(round(a.shape[2] * FLOW_WIDTH / a.shape[3] / 8)) * 8)
        size = (height, FLOW_WIDTH)
        a = torch.nn.functional.interpolate(a, size=size, mode="bilinear", align_corners=False) / 127.5 - 1
        b = torch.nn.functional.interpolate(b, size=size, mode="bilinear", align_corners=False) / 127.5 - 1
        with torch.inference_mode():
            flow = self.raft(a, b, num_flow_updates=RAFT_ITERATIONS)[-1]
        return flow.permute(0, 2, 3, 1).float().cpu().numpy()

    def detect(self, frames: list[np.ndarray]) -> list[list[tuple[str, float, tuple[float, float, float, float]]]]:
        if not frames:
            return []
        results = self.detector.predict(frames, threshold=DETECT_THRESHOLD, include_source_image=False)
        if not isinstance(results, list):
            results = [results]
        out = []
        for image, detections in zip(frames, results):
            height, width = image.shape[:2]
            rows = []
            for box, cls, conf in zip(detections.xyxy, detections.class_id, detections.confidence):
                x0, y0, x1, y1 = (float(box[0]) / width, float(box[1]) / height, float(box[2]) / width, float(box[3]) / height)
                rows.append((self.classes.get(int(cls), str(int(cls))), round(float(conf), 3),
                             (round(x0, 4), round(y0, 4), round(x1, 4), round(y1, 4))))
            out.append(rows)
        return out

    def sharpness(self, frames: np.ndarray) -> np.ndarray:
        """Variance of the Laplacian per frame (N×H×W×3 uint8)."""
        torch = self.torch
        gray = torch.from_numpy(frames).to(self.device).float().mean(dim=3, keepdim=True).permute(0, 3, 1, 2)
        kernel = torch.tensor([[0, 1, 0], [1, -4, 1], [0, 1, 0]], dtype=torch.float32, device=self.device).view(1, 1, 3, 3)
        with torch.inference_mode():
            lap = torch.nn.functional.conv2d(gray, kernel)
        return lap.flatten(1).var(dim=1).cpu().numpy()


# ---------------------------------------------------------------------------
# Pure measurement helpers
# ---------------------------------------------------------------------------


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

    def span(mask: np.ndarray, size: int) -> tuple[int, int]:
        inside = np.flatnonzero(mask)
        if inside.size == 0:
            return 0, size
        first, last = int(inside[0]) * 2, min(size, (int(inside[-1]) + 1) * 2)
        before, after = first, size - last
        if max(before, after) > 0.25 * size or abs(before - after) > 0.08 * size + 4:
            return 0, size
        return first, last

    y0, y1 = span(brightest.max(axis=1) > threshold, height)
    x0, x1 = span(brightest.max(axis=0) > threshold, width)
    return y0, y1, x0, x1


def look_stats(image: np.ndarray) -> dict[str, float]:
    y0, y1, x0, x1 = content_box(image)
    rgb = image[y0:y1:4, x0:x1:4].astype(np.float32) / 255.0
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    luma = 0.2126 * r + 0.7152 * g + 0.0722 * b
    high, low = rgb.max(axis=2), rgb.min(axis=2)
    saturation = np.where(high > 1e-6, (high - low) / np.maximum(high, 1e-6), 0.0)
    rg, yb = r - g, 0.5 * (r + g) - b
    colorfulness = math.sqrt(float(rg.std()) ** 2 + float(yb.std()) ** 2) + 0.3 * math.sqrt(float(rg.mean()) ** 2 + float(yb.mean()) ** 2)
    return {"brightness": float(luma.mean()), "contrast": float(luma.std()), "saturation": float(saturation.mean()),
            "colorfulness": colorfulness, "warmth": float((r - b).mean())}


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
        if abs(vx) >= 1.5 * abs(vy):
            return "pan_right" if vx < 0 else "pan_left"      # image content moves opposite to the camera
        if abs(vy) >= 1.5 * abs(vx):
            return "tilt_up" if vy > 0 else "tilt_down"
        return "diagonal"
    if abs(curl) >= ROLL:
        return "roll"
    return "static"


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
    return {"segments": [[round(s, 2), round(e, 2), label] for s, e, label in merged], "dominant": dominant,
            "moving": round(moving, 3), "reliability": round(sum(reliable) / len(reliable), 3), "shake": round(shake, 4),
            "mean": [round(float(value), 4) for value in mean]}


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
    from pipeline.matching.motion import summarize

    pairs = [(shot_index, k) for shot_index, shot in enumerate(shots) for k in range(len(shot.frames) - 1)]
    flows: dict[tuple[int, int], np.ndarray] = {}
    for start in range(0, len(pairs), 48):
        batch = pairs[start:start + 48]
        first = np.stack([shots[s].frames[k][1] for s, k in batch])
        second = np.stack([shots[s].frames[k + 1][1] for s, k in batch])
        for key, flow in zip(batch, models.flow(first, second)):
            flows[key] = flow
    detect_refs = []
    for shot_index, shot in enumerate(shots):
        chosen = list(range(0, len(shot.frames), DETECT_EVERY)) or []
        if shot.frames and not chosen:
            chosen = [len(shot.frames) // 2]
        detect_refs.extend((shot_index, k) for k in chosen)
    detect_images = [shots[s].frames[k][1] for s, k in detect_refs]
    detections: dict[tuple[int, int], list] = {}
    for start in range(0, len(detect_images), 16):
        for ref, rows in zip(detect_refs[start:start + 16], models.detect(detect_images[start:start + 16])):
            detections[ref] = rows
    sharp: dict[tuple[int, int], float] = {}
    if detect_images:
        values = models.sharpness(np.stack(detect_images))
        sharp = {ref: float(value) for ref, value in zip(detect_refs, values)}

    results: dict[str, dict[str, Any]] = {}
    for shot_index, shot in enumerate(shots):
        record: dict[str, Any] = {"samples": len(shot.frames)}
        if len(shot.frames) >= 2:
            times, cameras, reliable, cuts, residual, series = [], [], [], [], [], []
            for k in range(len(shot.frames) - 1):
                t0, a = shot.frames[k]
                t1, b = shot.frames[k + 1]
                summary = summarize(flows[(shot_index, k)], t1 - t0)
                support = _photometric_support(a, b, flows[(shot_index, k)])
                reliable_pair = bool(summary["reliable"]) and support >= 0.45
                times.append(t0)
                cameras.append(summary["camera"])
                reliable.append(reliable_pair)
                pair_residual = float(np.abs(summary["residual"]).mean())
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
        looks = [look_stats(shots[s].frames[k][1]) for s, k in refs]
        record["_boxes"] = [content_box(shots[s].frames[k][1]) + shots[s].frames[k][1].shape[:2]
                            for (s, k), look in zip(refs, looks) if look["brightness"] > 0.08]
        record["look"] = {key: round(float(np.mean([look[key] for look in looks])), 4) for key in looks[0]} if looks else {}
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


def _box_area(box: tuple[float, float, float, float]) -> float:
    return max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])


def _photometric_support(first: np.ndarray, second: np.ndarray, flow: np.ndarray) -> float:
    """Share of pixels explained by the flow (letterbox-safe), in [0, 1]."""
    height, width = flow.shape[:2]
    step_y = first.shape[0] / height
    step_x = first.shape[1] / width
    a = first[(np.arange(height) * step_y).astype(int)][:, (np.arange(width) * step_x).astype(int)].astype(np.float32) / 255
    b = second[(np.arange(height) * step_y).astype(int)][:, (np.arange(width) * step_x).astype(int)].astype(np.float32) / 255
    y, x = np.mgrid[:height, :width]
    sx = np.clip(np.rint(x + flow[..., 0]).astype(int), 0, width - 1)
    sy = np.clip(np.rint(y + flow[..., 1]).astype(int), 0, height - 1)
    warped = b[sy, sx]
    active = (a.max(axis=2) > 0.04) | (warped.max(axis=2) > 0.04)
    if active.mean() < 0.2:
        return 1.0  # mostly black: no evidence either way
    error = np.abs(a - warped).mean(axis=2)[active]
    return float((error < 0.1).mean())


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


def run(config: Any, db: Any, films: list[FilmRef], *, force: bool = False,
        progress: Callable[[str], None] = print) -> dict[str, int]:
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
            with film_operation_lock(Path(config.paths.assets_dir) / film.film_id):
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
