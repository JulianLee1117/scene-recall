"""Match moments: what every instant of a shot looks like, so any instant can be a cut point.

A match cut joins two shots where the eye carries across: the subject sits in
the same place at the same size, the silhouette or the big light and dark
masses line up, the movement continues. Shot-level evidence (one hero frame,
a mean subject box) cannot say *when* inside a shot that happens, so this pass
describes the picture on the film's time grid (``t = k / FPS``) inside every
shot, at a size small enough to index the whole library:

* ``instances`` — class, score, box and a 16x16 silhouette inside the box, for the
  largest few objects, from the film's subject backend (``pipeline.evidence.subjects``):
  RF-DETR segmentation (COCO) for live action; for drawn films a grounder's boxes with
  the silhouette of RF-DETR's best-overlapping query and ViTPose keypoints;
* ``poses`` — RF-DETR keypoints (COCO 17) for the largest few people, on
  instants where segmentation found a person: where heads and eyes are (the
  eye trace), which body parts the frame shows (close-up or full figure) and
  the pose itself;
* ``gray`` — a 32x18 luma thumbnail (where the light is);
* ``structure`` — per 16x9 cell, the dominant edge orientation as a
  doubled-angle vector weighted by its coherent energy (a horizon, a doorway),
  and the total edge energy (busy or clean);
* ``color`` — 8x5 mean colour;
* ``sharpness`` and ``brightness``.

Everything is in *content* coordinates: the film's letterbox bars (from the
measurement pass, else detected here) are cropped away first. Motion is not
measured again; the measurement pass's 6 fps camera series is joined at index
time. Arrays live in a compressed ``.npz`` beside the JSON artifact, which
records the arrays' SHA-256 so a reader never pairs mismatched files.
"""

from __future__ import annotations

import functools
import hashlib
import io
import time
from pathlib import Path
from typing import Any, Callable

import numpy as np

from pipeline.evidence import store, subjects
from pipeline.evidence.library import FilmRef, film_units

FPS = 4.0
DECODE_WIDTH = 640
SEG_THRESHOLD = 0.35
MAX_INSTANCES = 6
MIN_INSTANCE_AREA = 0.002          # share of the picture; smaller detections are noise for matching
SILHOUETTE = 16                    # silhouette grid inside each instance box
GRAY = (18, 32)                    # rows, columns
GRID = (9, 16)                     # structure grid
COLOR = (5, 8)
EDGE_MARGIN_S = 0.04               # keep samples this far inside the shot's cuts
BATCH = 48
POSE_THRESHOLD = 0.5
MAX_POSES = 4
KEYPOINTS = 17
# COCO-17 keypoints, in the order the pose model returns them, and the bones that join them.
KEYPOINT_NAMES = ("nose", "left eye", "right eye", "left ear", "right ear", "left shoulder", "right shoulder",
                  "left elbow", "right elbow", "left wrist", "right wrist", "left hip", "right hip",
                  "left knee", "right knee", "left ankle", "right ankle")
SKELETON = ((0, 1), (0, 2), (1, 3), (2, 4), (5, 6), (5, 7), (7, 9), (6, 8), (8, 10), (5, 11), (6, 12), (11, 12),
            (11, 13), (13, 15), (12, 14), (14, 16))

PRODUCER = store.Producer(
    kind="moments",
    name="match",
    version=1,
    settings={
        "fps": FPS, "decode": {"width": DECODE_WIDTH, "path": "nvdec-noref-scale_cuda-v1"}, "grid": "film-time k/fps inside shots, 0.04 s from cuts",
        "segment": {"model": "rf-detr-seg-small-1.11-fp16", "threshold": SEG_THRESHOLD, "max": MAX_INSTANCES,
                    "min_area": MIN_INSTANCE_AREA, "silhouette": SILHOUETTE, "select": "area*score"},
        "gray": list(GRAY), "structure": {"grid": list(GRID), "gradient": "sobel@128x72", "field": "doubled-angle"},
        "color": list(COLOR), "content": "measure content_box else detected bars",
        "pose": {"model": "rf-detr-keypoint-preview-xlarge-1.11-fp16", "when": "segmented person >= 0.4",
                 "threshold": POSE_THRESHOLD, "score": "raw person probability", "max": MAX_POSES,
                 "select": "keypoint extent", "decode": "batched-gpu-v1"},
    },
)



def model_names() -> dict[str, str]:
    """The models behind this producer version, by role."""
    return {"objects": PRODUCER.settings["segment"]["model"], "pose": PRODUCER.settings["pose"]["model"]}


@functools.cache
def class_names() -> dict[int, str]:
    """Stored class codes (1..80) to names: COCO categories in id order, as the producer encodes them."""
    from rfdetr.assets.coco_classes import COCO_CLASSES
    names = {position + 1: COCO_CLASSES[coco_id] for position, coco_id in enumerate(sorted(COCO_CLASSES))}
    return {**names, **subjects.EXTRA_CLASS_NAMES}

# ---------------------------------------------------------------------------
# Pure helpers (numpy; unit tested without a GPU)
# ---------------------------------------------------------------------------


def sample_times(units: list[dict[str, Any]], fps: float = FPS) -> tuple[np.ndarray, np.ndarray]:
    """Grid times ``k / fps`` inside each shot (away from its cuts) and the shot index of each."""
    times, owners = [], []
    for index, unit in enumerate(units):
        low, high = float(unit["t_start"]) + EDGE_MARGIN_S, float(unit["t_end"]) - EDGE_MARGIN_S
        if high < low:
            continue
        first, last = int(np.ceil(low * fps - 1e-9)), int(np.floor(high * fps + 1e-9))
        ks = np.arange(first, last + 1)
        if ks.size == 0:                          # a shot shorter than one grid step keeps its nearest step
            ks = np.array([int(round((low + high) / 2 * fps))])
        times.append(ks / fps)
        owners.append(np.full(ks.size, index, dtype=np.int32))
    if not times:
        return np.zeros(0, dtype=np.float64), np.zeros(0, dtype=np.int32)
    return np.concatenate(times), np.concatenate(owners)


def pack_silhouette(mask: np.ndarray) -> np.ndarray:
    """A boolean SILHOUETTE x SILHOUETTE mask as 32 bytes."""
    return np.packbits(mask.astype(bool).reshape(-1))


def unpack_silhouettes(packed: np.ndarray) -> np.ndarray:
    """N x 32 packed bytes -> N x SILHOUETTE x SILHOUETTE boolean masks."""
    bits = np.unpackbits(np.asarray(packed, dtype=np.uint8).reshape(len(packed), -1), axis=1)
    return bits[:, :SILHOUETTE * SILHOUETTE].reshape(-1, SILHOUETTE, SILHOUETTE).astype(bool)


def select_instances(scores: np.ndarray, labels: np.ndarray, boxes: np.ndarray, *,
                     threshold: float = SEG_THRESHOLD, limit: int = MAX_INSTANCES,
                     min_area: float = MIN_INSTANCE_AREA) -> np.ndarray:
    """Indices of the detections to keep: confident, not tiny, largest (area x score) first.

    Near-duplicates (same class, box IoU over 0.8) keep only the stronger one.
    """
    area = np.clip(boxes[:, 2] - boxes[:, 0], 0, 1) * np.clip(boxes[:, 3] - boxes[:, 1], 0, 1)
    order = [i for i in np.argsort(-(area * scores), kind="stable") if scores[i] >= threshold and area[i] >= min_area]
    kept: list[int] = []
    for i in order:
        duplicate = False
        for j in kept:
            if labels[i] != labels[j]:
                continue
            x0, y0 = max(boxes[i, 0], boxes[j, 0]), max(boxes[i, 1], boxes[j, 1])
            x1, y1 = min(boxes[i, 2], boxes[j, 2]), min(boxes[i, 3], boxes[j, 3])
            inter = max(0.0, x1 - x0) * max(0.0, y1 - y0)
            if inter / max(area[i] + area[j] - inter, 1e-9) > 0.8:
                duplicate = True
                break
        if not duplicate:
            kept.append(int(i))
        if len(kept) >= limit:
            break
    return np.array(kept, dtype=np.int64)


def content_crop(width: int, height: int, box: list[float] | None) -> tuple[int, int, int, int]:
    """Pixel ``(x0, y0, x1, y1)`` of the content box (the full frame when unknown)."""
    if not box:
        return 0, 0, width, height
    x0, y0, x1, y1 = box
    left, top = int(round(x0 * width)), int(round(y0 * height))
    right, bottom = int(round(x1 * width)), int(round(y1 * height))
    if right - left < width * 0.5 or bottom - top < height * 0.5:
        return 0, 0, width, height
    return left, top, right, bottom


# ---------------------------------------------------------------------------
# GPU work
# ---------------------------------------------------------------------------


class Models:
    """RF-DETR segmentation plus the pixel descriptors, batched on the GPU."""

    def __init__(self, device: str | None = None):
        import torch
        self.torch = torch
        self.device = torch.device(device or ("cuda" if torch.cuda.is_available() else "cpu"))
        self._rf = None
        self._pose = None
        self._classes: dict[int, int] = {}
        self.subjects = subjects.COCO          # the current film's subject backend
        self._grounded = None

    @property
    def grounded(self):
        if self._grounded is None:
            self._grounded = subjects.GroundedSubjects(self.device)
        return self._grounded

    def _images(self, rgb: Any) -> list[np.ndarray]:
        """The content-cropped frames back as H x W x 3 uint8 arrays for the grounder."""
        return list((rgb * 255).round().clamp(0, 255).to(self.torch.uint8).permute(0, 2, 3, 1).cpu().numpy())

    def _grounded_poses(self, rgb: Any, instances: list[list[tuple[int, float, np.ndarray, np.ndarray]]]) -> dict[int, list]:
        boxes = [[box for code, score, box, _ in rows if code == 1 and score >= 0.4] for rows in instances]
        if not any(boxes):
            return {}
        found = self.grounded.poses(self._images(rgb), boxes)
        return {index: people for index, people in enumerate(found) if people}

    @property
    def rf(self):
        if self._rf is None:
            from rfdetr import RFDETRSegSmall
            from rfdetr.assets.coco_classes import COCO_CLASSES
            self._rf = RFDETRSegSmall()
            if self.device.type == "cuda":
                self._rf.inference(compile=False, dtype=self.torch.float16)
            # COCO category ids (1..90, with gaps) -> dense 1..80 class codes; 0 means none.
            self._classes = {coco_id: position + 1 for position, coco_id in enumerate(sorted(COCO_CLASSES))}
        return self._rf

    @property
    def pose(self):
        if self._pose is None:
            from rfdetr import RFDETRKeypointPreview
            self._pose = RFDETRKeypointPreview()
            if self.device.type == "cuda":
                self._pose.inference(compile=False, dtype=self.torch.float16)
        return self._pose

    def _poses(self, rgb: Any, frames_with_people: list[int]) -> dict[int, list[tuple[float, np.ndarray, np.ndarray]]]:
        """Per frame: ``(score, xy 17 x 2 content fractions, confidence 17)`` for the largest people.

        Decoded on the GPU from the model's raw outputs (the library's
        per-image post-processing costs as much as the model): the person
        probability of each query, its 17 keypoints in input fractions (the
        content picture, since the whole picture is resized) and their
        visibility. People are ranked by the extent of their shown keypoints.
        """
        if not frames_with_people:
            return {}
        torch = self.torch
        functional = torch.nn.functional
        pose = self.pose
        resolution = pose.model.resolution
        batch = functional.interpolate(rgb[frames_with_people], size=(resolution, resolution), mode="bilinear",
                                       align_corners=False, antialias=False)
        mean = torch.tensor(pose.means, device=self.device).view(1, 3, 1, 1)
        std = torch.tensor(pose.stds, device=self.device).view(1, 3, 1, 1)
        model = pose.model.inference_model if pose.model.inference_model is not None else pose.model.model
        outputs = model(((batch - mean) / std).to(next(model.parameters()).dtype))
        logits, raw = (outputs["pred_logits"], outputs["pred_keypoints"]) if isinstance(outputs, dict) else (outputs[1], outputs[2])
        slots = list(pose.model.postprocess.num_keypoints_per_class)
        person = next(index for index, count in enumerate(slots) if count > 0)
        frames_count, queries = logits.shape[:2]
        score = logits.float().sigmoid()[..., person]                                            # F x Q
        keypoints = raw.float().view(frames_count, queries, len(slots), max(slots), raw.shape[-1])[:, :, person, :KEYPOINTS]
        xy = keypoints[..., :2].clamp(0, 1)
        confidence = keypoints[..., 2].sigmoid()
        seen = confidence >= 0.3
        big = torch.tensor(9.0, device=self.device)
        low = torch.where(seen[..., None], xy, big).amin(dim=2)
        high = torch.where(seen[..., None], xy, -big).amax(dim=2)
        extent = (high - low).clamp(min=0)
        rank = extent[..., 0] * extent[..., 1] + extent[..., 1] * 0.01
        valid = (score >= POSE_THRESHOLD) & (seen.sum(dim=2) >= 2)
        rank = torch.where(valid, rank, torch.full_like(rank, -1.0))
        top_rank, top = rank.topk(min(MAX_POSES, queries), dim=1)
        gather = top[..., None, None].expand(-1, -1, KEYPOINTS, 2)
        chosen_xy = xy.gather(1, gather).cpu().numpy()
        chosen_conf = confidence.gather(1, top[..., None].expand(-1, -1, KEYPOINTS)).cpu().numpy()
        chosen_score = score.gather(1, top).cpu().numpy()
        keep = (top_rank >= 0).cpu().numpy()
        out: dict[int, list[tuple[float, np.ndarray, np.ndarray]]] = {}
        for row, index in enumerate(frames_with_people):
            people = [(float(chosen_score[row, k]), chosen_xy[row, k], chosen_conf[row, k]) for k in range(keep.shape[1]) if keep[row, k]]
            if people:
                out[index] = people
        return out

    def describe(self, frames: Any) -> dict[str, np.ndarray]:
        """Descriptors for N x H x W x 3 uint8 content-cropped frames (a torch tensor on the device)."""
        torch = self.torch
        functional = torch.nn.functional
        with torch.inference_mode():
            rgb = frames.permute(0, 3, 1, 2).float().div_(255)                        # N x 3 x H x W
            n = rgb.shape[0]
            luma = (0.2126 * rgb[:, 0] + 0.7152 * rgb[:, 1] + 0.0722 * rgb[:, 2]).unsqueeze(1)
            gray = functional.adaptive_avg_pool2d(luma, GRAY).squeeze(1)
            color = functional.adaptive_avg_pool2d(rgb, COLOR).permute(0, 2, 3, 1)    # N x 5 x 8 x 3
            small = functional.interpolate(luma, size=(72, 128), mode="area")
            sobel_x = torch.tensor([[-1, 0, 1], [-2, 0, 2], [-1, 0, 1]], dtype=torch.float32, device=self.device).view(1, 1, 3, 3) / 8
            gx = functional.conv2d(functional.pad(small, (1, 1, 1, 1), mode="replicate"), sobel_x)
            gy = functional.conv2d(functional.pad(small, (1, 1, 1, 1), mode="replicate"), sobel_x.transpose(2, 3))
            jxx = functional.adaptive_avg_pool2d(gx * gx, GRID)
            jyy = functional.adaptive_avg_pool2d(gy * gy, GRID)
            jxy = functional.adaptive_avg_pool2d(gx * gy, GRID)
            field = torch.cat([jxx - jyy, 2 * jxy], dim=1)                              # N x 2 x 9 x 16
            energy = (jxx + jyy).squeeze(1)
            lap = functional.conv2d(small * 255, torch.tensor([[0, 1, 0], [1, -4, 1], [0, 1, 0]], dtype=torch.float32,
                                                              device=self.device).view(1, 1, 3, 3))
            sharpness = lap.flatten(1).var(dim=1)
            brightness = luma.mean(dim=(1, 2, 3))
            instances = self._segment(rgb)
            if self.subjects == subjects.GROUNDED:
                poses = self._grounded_poses(rgb, instances)
            else:
                people = [index for index, rows in enumerate(instances) if any(code == 1 and score >= 0.4
                                                                                for code, score, _, _ in rows)]
                poses = self._poses(rgb, people)
        # The orientation field is scaled per frame (its direction and relative strength
        # matter); edge energy keeps an absolute log scale (busy versus clean pictures).
        field_np = field.cpu().numpy()
        scale = np.maximum(np.abs(field_np).reshape(n, -1).max(axis=1), 1e-6).reshape(n, 1, 1, 1)
        return {
            "gray": np.clip(np.round(gray.cpu().numpy() * 255), 0, 255).astype(np.uint8),
            "color": np.clip(np.round(color.cpu().numpy() * 255), 0, 255).astype(np.uint8),
            "field": np.clip(np.round(field_np / scale * 127), -127, 127).astype(np.int8),
            "energy": np.clip(np.round(np.log1p(energy.cpu().numpy() * 4000) * 32), 0, 255).astype(np.uint8),
            "sharpness": sharpness.cpu().numpy().astype(np.float32),
            "brightness": brightness.cpu().numpy().astype(np.float32),
            "instances": instances,
            "poses": poses,
        }

    def _segment(self, rgb: Any) -> list[list[tuple[int, float, np.ndarray, np.ndarray]]]:
        """Per frame: ``(class code, score, box [x0,y0,x1,y1], packed silhouette)`` in content fractions."""
        torch = self.torch
        functional = torch.nn.functional
        rf = self.rf
        resolution = rf.model.resolution
        batch = functional.interpolate(rgb, size=(resolution, resolution), mode="bilinear", align_corners=False)
        mean = torch.tensor(rf.means, device=self.device).view(1, 3, 1, 1)
        std = torch.tensor(rf.stds, device=self.device).view(1, 3, 1, 1)
        batch = (batch - mean) / std
        model = rf.model.inference_model if rf.model.inference_model is not None else rf.model.model
        dtype = next(model.parameters()).dtype
        outputs = model(batch.to(dtype))
        if isinstance(outputs, dict):
            boxes, logits, masks = outputs["pred_boxes"], outputs["pred_logits"], outputs["pred_masks"]
        else:
            boxes, logits, masks = outputs[0], outputs[1], outputs[2]
        prob = logits.float().sigmoid()
        score, label = prob.max(dim=2)                                                   # one class per query
        cx, cy, w, h = boxes.float().unbind(-1)
        xyxy = torch.stack([cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2], dim=-1).clamp(0, 1)
        score_np, label_np, box_np = score.cpu().numpy(), label.cpu().numpy(), xyxy.cpu().numpy()
        results: list[list[tuple[int, float, np.ndarray, np.ndarray]]] = [[] for _ in range(len(score_np))]
        # (frame, query or -1, code, score, box): which query lends its mask and what the subject is
        kept: list[tuple[int, int, int, float, np.ndarray]] = []
        if self.subjects == subjects.GROUNDED:
            for frame, found in enumerate(self.grounded.detect(self._images(rgb))):
                area = np.array([(d.box[2] - d.box[0]) * (d.box[3] - d.box[1]) for d in found], np.float32)
                order = [i for i in np.argsort(-(area * np.array([d.score for d in found], np.float32)), kind="stable")
                         if area[i] >= MIN_INSTANCE_AREA][:MAX_INSTANCES]
                chosen = [found[i] for i in order]
                for det, query in zip(chosen, subjects.match_masks(chosen, box_np[frame])):
                    kept.append((frame, query, det.code, det.score, det.box.astype(np.float32)))
        else:
            for frame in range(len(score_np)):
                for query in select_instances(score_np[frame], label_np[frame], box_np[frame]):
                    kept.append((frame, int(query), self._classes.get(int(label_np[frame, query]), 0),
                                 float(score_np[frame, query]), box_np[frame, query].astype(np.float32)))
        if not kept:
            return results
        from torchvision.ops import roi_align
        mh, mw = masks.shape[-2:]
        with_mask = [k for k, (_f, query, *_rest) in enumerate(kept) if query >= 0]
        silhouettes = {}
        if with_mask:
            frames_index = torch.tensor([kept[k][0] for k in with_mask], device=self.device)
            queries_index = torch.tensor([kept[k][1] for k in with_mask], device=self.device)
            selected = masks[frames_index, queries_index].float().unsqueeze(1)                # K x 1 x h x w
            boxes_px = torch.from_numpy(np.stack([kept[k][4] for k in with_mask])).to(self.device) \
                * torch.tensor([mw, mh, mw, mh], dtype=torch.float32, device=self.device)
            rois = torch.cat([torch.arange(len(with_mask), device=self.device, dtype=torch.float32).unsqueeze(1), boxes_px], dim=1)
            cut = (roi_align(selected, rois, output_size=(SILHOUETTE, SILHOUETTE), spatial_scale=1.0,
                             sampling_ratio=2, aligned=True)[:, 0] > 0).cpu().numpy()
            silhouettes = {k: cut[row] for row, k in enumerate(with_mask)}
        empty = np.zeros((SILHOUETTE, SILHOUETTE), bool)
        for k, (frame, _query, code, score, box) in enumerate(kept):
            results[frame].append((code, score, box, pack_silhouette(silhouettes.get(k, empty))))
        return results
        from torchvision.ops import roi_align
        mh, mw = masks.shape[-2:]
        frames_index = torch.tensor([frame for frame, _ in kept], device=self.device)
        queries_index = torch.tensor([query for _, query in kept], device=self.device)
        selected = masks[frames_index, queries_index].float().unsqueeze(1)                    # K x 1 x h x w
        boxes_px = torch.from_numpy(np.stack([box_np[frame, query] for frame, query in kept])).to(self.device) \
            * torch.tensor([mw, mh, mw, mh], dtype=torch.float32, device=self.device)
        rois = torch.cat([torch.arange(len(kept), device=self.device, dtype=torch.float32).unsqueeze(1), boxes_px], dim=1)
        silhouettes = (roi_align(selected, rois, output_size=(SILHOUETTE, SILHOUETTE), spatial_scale=1.0,
                                 sampling_ratio=2, aligned=True)[:, 0] > 0).cpu().numpy()
        for (frame, query), silhouette in zip(kept, silhouettes):
            results[frame].append((self._classes.get(int(label_np[frame, query]), 0), float(score_np[frame, query]),
                                   box_np[frame, query].astype(np.float32), pack_silhouette(silhouette)))
        return results


# ---------------------------------------------------------------------------
# Producer run
# ---------------------------------------------------------------------------


def _film_content_box(assets_dir: Path, film_id: str) -> list[float] | None:
    from pipeline.evidence import measure
    measured = store.serving_artifact(assets_dir, film_id, measure.PRODUCER)
    return ((measured or {}).get("data") or {}).get("content_box")


def stream_frames(path: Path, *, fps: float = FPS, width: int = DECODE_WIDTH):
    """``(time, rgb)`` at ``fps``: NVDEC skipping non-reference frames, scaled on the GPU.

    Skipping non-reference frames makes decoding about 1.5x cheaper; the fps
    filter then takes the nearest decoded frame, at most a frame or two from
    the grid instant. Falls back to the measurement pass's full decode when
    this path yields nothing (a codec NVDEC cannot skip in).
    """
    from pipeline.evidence import measure
    out_w, out_h = measure.display_size(path, width)
    command = ["ffmpeg", "-nostdin", "-v", "error", "-hwaccel", "cuda", "-hwaccel_output_format", "cuda",
               "-skip_frame:v", "noref", "-i", str(path), "-map", "0:v:0", "-an", "-sn",
               "-vf", f"fps={fps},scale_cuda={out_w}:{out_h},hwdownload,format=nv12",
               "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"]
    yielded = False
    for item in _pipe_frames(command, out_w, out_h, fps):
        yielded = True
        yield item
    if not yielded:
        yield from measure.stream_frames(path, fps=fps, width=width)


def _pipe_frames(command: list[str], width: int, height: int, fps: float):
    import queue
    import subprocess
    import threading
    frame_bytes = width * height * 3
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=frame_bytes * 4)
    frames: queue.Queue = queue.Queue(maxsize=256)

    def reader() -> None:
        index = 0
        try:
            while True:
                data = process.stdout.read(frame_bytes)
                if len(data) < frame_bytes:
                    break
                frames.put((index / fps, np.frombuffer(data, dtype=np.uint8).reshape(height, width, 3)))
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


def describe_film(film: FilmRef, units: list[dict[str, Any]], models: Models, content: list[float] | None,
                  progress: Callable[[str], None] = print) -> dict[str, np.ndarray]:
    """Decode the film once at FPS and describe every grid instant inside a shot."""
    times, owners = sample_times(units)
    wanted = {int(round(t * FPS)): position for position, t in enumerate(times)}
    count = len(times)
    out: dict[str, Any] = {
        "gray": np.zeros((count, *GRAY), np.uint8), "color": np.zeros((count, *COLOR, 3), np.uint8),
        "field": np.zeros((count, 2, *GRID), np.int8), "energy": np.zeros((count, *GRID), np.uint8),
        "sharpness": np.zeros(count, np.float32), "brightness": np.zeros(count, np.float32),
        "described": np.zeros(count, bool),
    }
    inst_moment, inst_class, inst_score, inst_box, inst_mask = [], [], [], [], []
    pose_moment, pose_score, pose_xy, pose_conf = [], [], [], []
    pending: list[tuple[int, np.ndarray]] = []
    crop: tuple[int, int, int, int] | None = None
    last_report = time.perf_counter()

    def flush() -> None:
        if not pending:
            return
        uploaded = models.torch.from_numpy(np.stack([frame for _, frame in pending])).to(models.device, non_blocking=True)
        result = models.describe(uploaded)
        for row, (position, _frame) in enumerate(pending):
            for key in ("gray", "color", "field", "energy", "sharpness", "brightness"):
                out[key][position] = result[key][row]
            out["described"][position] = True
            for code, score, box, mask in result["instances"][row]:
                inst_moment.append(position)
                inst_class.append(code)
                inst_score.append(int(round(score * 255)))
                inst_box.append(np.round(box * 65535).astype(np.uint16))
                inst_mask.append(mask)
            for score, xy, confidence in result["poses"].get(row, []):
                pose_moment.append(position)
                pose_score.append(int(round(score * 255)))
                pose_xy.append(np.round(xy * 65535).astype(np.uint16))
                pose_conf.append(np.round(np.clip(confidence, 0, 1) * 255).astype(np.uint8))
        pending.clear()

    last_needed = int(round(times[-1] * FPS)) if count else -1
    for t, frame in stream_frames(film.path, fps=FPS, width=DECODE_WIDTH):
        k = int(round(t * FPS))
        if k > last_needed:
            break
        position = wanted.get(k)
        if position is None:
            continue
        if crop is None:
            crop = content_crop(frame.shape[1], frame.shape[0], content)
        x0, y0, x1, y1 = crop
        pending.append((position, np.ascontiguousarray(frame[y0:y1, x0:x1])))
        if len(pending) >= BATCH:
            flush()
            if time.perf_counter() - last_report > 60:
                progress(f"[moments] {film.title}: {t / 60:.0f} min")
                last_report = time.perf_counter()
    flush()
    out.update({
        "times": times.astype(np.float64), "unit": owners,
        "inst_moment": np.array(inst_moment, np.int32), "inst_class": np.array(inst_class, np.uint8),
        "inst_score": np.array(inst_score, np.uint8),
        "inst_box": np.array(inst_box, np.uint16).reshape(-1, 4),
        "inst_mask": np.array(inst_mask, np.uint8).reshape(-1, SILHOUETTE * SILHOUETTE // 8),
        "pose_moment": np.array(pose_moment, np.int32), "pose_score": np.array(pose_score, np.uint8),
        "pose_xy": np.array(pose_xy, np.uint16).reshape(-1, KEYPOINTS, 2),
        "pose_conf": np.array(pose_conf, np.uint8).reshape(-1, KEYPOINTS),
    })
    return out


def arrays_path(assets_dir: Path, film_id: str) -> Path:
    return store.artifact_path(assets_dir, film_id, PRODUCER, suffix=".npz")


def write(assets_dir: Path, film: FilmRef, units: list[dict[str, Any]], arrays: dict[str, np.ndarray],
          inputs: dict[str, str], content: list[float] | None, elapsed: float, *, backend: str = subjects.COCO) -> None:
    buffer = io.BytesIO()
    np.savez_compressed(buffer, **arrays)
    payload = buffer.getvalue()
    path = arrays_path(assets_dir, film.film_id)
    store._atomic_write_bytes(path, payload)
    data = {"units": [unit["unit_id"] for unit in units], "moments": int(len(arrays["times"])),
            "described": int(arrays["described"].sum()), "instances": int(len(arrays["inst_moment"])),
            "poses": int(len(arrays["pose_moment"])),
            "content_box": content, "arrays": path.name, "arrays_sha256": hashlib.sha256(payload).hexdigest(),
            "elapsed_s": round(elapsed, 1), "subjects": subjects.record(backend)}
    store.write_artifact(assets_dir, film.film_id, PRODUCER, data, inputs=inputs)


def read(assets_dir: Path, film_id: str, *, profile: store.Producer = PRODUCER) -> tuple[dict[str, Any], dict[str, np.ndarray]] | None:
    """The current artifact and its arrays, else the newest earlier profile's (``serving_artifact``).

    ``None`` when there is none or the arrays do not belong to the document.
    The document's ``profile_id`` says which profile served.
    """
    document = store.serving_artifact(assets_dir, film_id, profile)
    if document is None:
        return None
    path = Path(assets_dir) / film_id / store.EVIDENCE_DIRNAME / profile.kind / f"{document['profile_id']}.npz"
    try:
        payload = path.read_bytes()
    except OSError:
        return None
    if hashlib.sha256(payload).hexdigest() != document["data"].get("arrays_sha256"):
        return None
    with np.load(io.BytesIO(payload)) as archive:
        arrays = {name: archive[name] for name in archive.files}
    return document, arrays


def run(config: Any, db: Any, films: list[FilmRef], *, force: bool = False, lock_films: bool = True,
        progress: Callable[[str], None] = print) -> dict[str, int]:
    """Describe films (skipping current artifacts). ``lock_films=False`` when the caller holds the film lock."""
    from pipeline.evidence.understanding import shots_digest
    from pipeline.ingest.locks import film_operation_lock
    assets = Path(config.paths.assets_dir)
    models: Models | None = None
    counts = {"cached": 0, "done": 0, "failed": 0}
    for number, film in enumerate(films, start=1):
        units = film_units(db, film.film_id)
        backend = subjects.backend_for_film(db, film.film_id, subjects.configured_families(config))
        inputs = subjects.cache_inputs(shots_digest(units), backend)
        if not force and store.read_artifact(assets, film.film_id, PRODUCER, inputs=inputs) is not None \
                and arrays_path(assets, film.film_id).is_file():
            counts["cached"] += 1
            continue
        models = models or Models()
        models.subjects = backend
        started = time.perf_counter()
        try:
            content = _film_content_box(assets, film.film_id)
            if lock_films:
                with film_operation_lock(assets / film.film_id):
                    arrays = describe_film(film, units, models, content, progress)
            else:
                arrays = describe_film(film, units, models, content, progress)
            write(assets, film, units, arrays, inputs, content, time.perf_counter() - started, backend=backend)
        except Exception as exc:  # noqa: BLE001 - one film must not stop a library run
            counts["failed"] += 1
            progress(f"[moments] {film.title}: failed ({str(exc)[:300]})")
            continue
        counts["done"] += 1
        progress(f"[moments] {number}/{len(films)} {film.title}: {int(arrays['described'].sum())}/{len(arrays['times'])} "
                 f"moments, {len(arrays['inst_moment'])} instances, {len(arrays['pose_moment'])} poses, "
                 f"{time.perf_counter() - started:.0f}s")
    return counts
