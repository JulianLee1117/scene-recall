"""Bounded SAM 2.1 tracks and ordered, mask-local movement evidence.

Checkpoint preparation is explicit. Inference is local-only and never downloads.
The descriptor deliberately retains spatial flow and velocity quantiles: a pair
of opposing hands must not disappear into a zero average velocity.
"""

from __future__ import annotations

from contextlib import nullcontext
from importlib.metadata import version
from pathlib import Path
import hashlib
import math

import numpy as np
from PIL import Image

from pipeline.matching.cohort import digest, read, write_new

CONTRACT = "sam2.1-small-mask-motion-v1"
CHECKPOINT = "facebook/sam2.1-hiera-small"
REVISION = "ee5bba1d82bb8749febdf90f45e84b687142ba03"
FILES = ("config.json", "model.safetensors", "preprocessor_config.json",
         "processor_config.json", "video_preprocessor_config.json")
OFFICIAL_SHA256 = {
    "config.json": "97ff9f65b76d107acda4247885f0a5555d0048850ae3c5f97183df289aaecde9",
    "model.safetensors": "0a4067b11ce1e23d5229203f11c718a823060d15a4b23fa2372a7d4b77cbbc60",
    "preprocessor_config.json": "6ebf229ee259368ce4a8d4f2fe893a72b053023710853e257253939e601f583d",
    "processor_config.json": "f8a68e865cfad115c1c2763f3d93eca7b1c622da06da2a9273eb437fb2389b6d",
    "video_preprocessor_config.json": "9fccfe5f464ec38c2f236d0e6a68e95511c80c22132fc2fa4b9f7b65f24fad95",
}


def download_checkpoint(directory: Path):
    """Operator-only download of a pinned official Transformers checkpoint."""
    from huggingface_hub import snapshot_download

    return Path(snapshot_download(CHECKPOINT, revision=REVISION,
                                 local_dir=directory, allow_patterns=list(FILES)))


def prepare_model(directory: Path, flow_profile: dict, device="cpu"):
    """Fingerprint existing local weights; keep independent motion lineage."""
    hashes = {name: hashlib.sha256((directory / name).read_bytes()).hexdigest() for name in FILES}
    if hashes != OFFICIAL_SHA256:
        raise ValueError("Subject checkpoint differs from the pinned official SAM 2.1 Small revision")
    payload = {
        "contract": CONTRACT, "checkpoint": CHECKPOINT, "revision": REVISION,
        "files": hashes,
        "flow_profile_id": flow_profile["id"],
        "versions": {name: version(name) for name in
                     ("torch", "torchvision", "transformers", "numpy", "av")},
        "precision": "cuda-bfloat16-autocast" if device == "cuda" else "cpu-float32",
        "sampling_fps": 6, "refinement_sampling_fps": 12, "max_seconds": 4, "max_frames": 25,
        "max_tracks": 3, "automatic_prompts": "3x3-grid-interior-area-stability-nms-v1",
        "descriptor": "mask-box-picture-aspect-4x4-flow-quantiles-8x8-silhouette-v1",
        "camera_fit": "outside-all-masks-seeded-affine-ransac64-v1",
        "flow_quality": "flow-photometric-support-v1-each-phase75-boundary-required",
    }
    profile = {**payload, "id": digest(payload)}
    manifest = directory / "subject-profile.json"
    if manifest.exists() and read(manifest) != profile:
        raise ValueError("Subject profile changed; prepare a separate checkpoint directory")
    if not manifest.exists():
        write_new(manifest, profile)
    return profile


def _cancel(cancelled):
    if cancelled():
        from pipeline.lab.media import JobCancelled
        raise JobCancelled("Subject tracking cancelled")


def _salience(mask, quality):
    summary = mask_summary(mask, quality=quality)
    if not summary["visible"]:
        return 0.0
    box = summary["box"]
    edge_span = .2 if max(box["width"], box["height"]) > .95 else 1.
    center = 1 - np.linalg.norm(np.asarray(summary["centroid"]) - .5)
    return float(quality * math.sqrt(summary["area"]) * edge_span * center)


def _resize_mask(mask, shape):
    return np.asarray(Image.fromarray(np.asarray(mask, dtype=np.uint8)).resize(
        (shape[1], shape[0]), Image.Resampling.NEAREST), dtype=bool)


def mask_summary(mask, *, quality=1.0):
    """Normalized layout and silhouette, with explicit missing/occluded state."""
    mask = np.asarray(mask, dtype=bool)
    y, x = np.nonzero(mask)
    area = float(mask.mean())
    visible = bool(len(x) >= 16 and 0.002 <= area <= 0.85 and quality >= 0.5)
    if not len(x):
        return {"visible": False, "quality": float(quality), "area": 0.0,
                "centroid": None, "box": None, "silhouette": None}
    height, width = mask.shape
    x0, x1, y0, y1 = int(x.min()), int(x.max()) + 1, int(y.min()), int(y.max()) + 1
    silhouette = np.asarray(Image.fromarray(mask[y0:y1, x0:x1].astype(np.uint8) * 255)
                            .resize((8, 8), Image.Resampling.BOX), dtype=float) / 255
    return {"visible": visible, "quality": float(quality), "area": area,
            "centroid": [float((x.mean() + 0.5) / width), float((y.mean() + 0.5) / height)],
            "box": {"x": x0 / width, "y": y0 / height,
                    "width": (x1 - x0) / width, "height": (y1 - y0) / height},
            "silhouette": silhouette.ravel().tolist()}


def _camera(flow, excluded):
    """Fit on background only, keeping insufficient support explicitly unknown."""
    height, width, _ = flow.shape
    ys, xs = np.mgrid[4:height:8, 4:width:8]
    keep = ~excluded[ys, xs]
    ys, xs = ys[keep], xs[keep]
    normalized = flow / np.array([width, height])
    if len(xs) < 24 or len(xs) / max(1, math.ceil(height / 8) * math.ceil(width / 8)) < 0.2:
        return None, 0.0
    position = np.stack([xs / width, ys / height, np.ones(len(xs))], axis=1)
    vectors = normalized[ys, xs]
    rng, best = np.random.default_rng(817), np.zeros(len(xs), dtype=bool)
    for _ in range(64):
        selected = rng.choice(len(position), 3, replace=False)
        if np.linalg.matrix_rank(position[selected]) < 3:
            continue
        estimate = np.linalg.lstsq(position[selected], vectors[selected], rcond=None)[0]
        inliers = np.linalg.norm(position @ estimate - vectors, axis=1) < 2 / min(width, height)
        if inliers.sum() > best.sum():
            best = inliers
    confidence = float(best.mean())
    if confidence < 0.55 or np.linalg.matrix_rank(position[best]) < 3:
        return None, confidence
    affine = np.linalg.lstsq(position[best], vectors[best], rcond=None)[0]
    y, x = np.mgrid[:height, :width]
    coordinates = np.stack([x / width, y / height, np.ones_like(x)], axis=-1)
    return coordinates @ affine, confidence


def _local_flow(velocity, mask):
    y, x = np.nonzero(mask)
    if len(x) < 8:
        return None
    values = velocity[mask]
    grid = []
    # Cells move with the tracked subject instead of remaining fixed on screen.
    xedges = np.linspace(x.min(), x.max() + 1, 5).astype(int)
    yedges = np.linspace(y.min(), y.max() + 1, 5).astype(int)
    for gy in range(4):
        for gx in range(4):
            selected = mask[yedges[gy]:yedges[gy + 1], xedges[gx]:xedges[gx + 1]]
            patch = velocity[yedges[gy]:yedges[gy + 1], xedges[gx]:xedges[gx + 1]]
            grid.extend(np.mean(patch[selected], axis=0).tolist() if selected.any() else [0., 0.])
    return {"vector": grid + np.quantile(values, [0.1, 0.5, 0.9], axis=0).ravel().tolist(),
            "velocity": np.mean(values, axis=0).tolist(),
            "speed": float(np.mean(np.linalg.norm(values, axis=1)))}


def describe_masks(rows, masks, flow_model, *, qualities=None, cancelled=lambda: False, measure_motion=True):
    """Describe supplied tracks; public seam for model-independent verification.

    ``masks`` has object, frame, height, width axes. Return JSON-safe evidence.
    """
    _cancel(cancelled)
    masks = np.asarray(masks, dtype=bool)
    if masks.ndim != 4 or masks.shape[1] != len(rows) or len(rows) < 2:
        raise ValueError("Expected one mask per object and decoded timestamp")
    qualities = np.ones(masks.shape[:2]) if qualities is None else np.asarray(qualities)
    tracks = [{"id": str(i + 1), "frames": [
        {"time": row.time, "end": row.end,
         "picture_aspect": row.image.width / row.image.height,
         **mask_summary(mask, quality=quality)}
        for row, mask, quality in zip(rows, track, qualities[i])], "motion": []}
        for i, track in enumerate(masks)]
    if not measure_motion:
        return tracks
    for index, (first, second) in enumerate(zip(rows, rows[1:])):
        _cancel(cancelled)
        seconds = second.time - first.time
        if seconds <= 0:
            raise ValueError("Subject timestamps must increase")
        flow = np.asarray(flow_model.pair(first.image, second.image))
        if flow.ndim != 3 or flow.shape[-1] != 2 or not np.isfinite(flow).all():
            raise ValueError("Invalid optical flow evidence")
        from pipeline.matching.motion import photometric_support
        photometric = photometric_support(first.image, second.image, flow)
        height, width = flow.shape[:2]
        frame_masks = [_resize_mask(track[index], (height, width)) for track in masks]
        background, confidence = _camera(flow, np.any(frame_masks, axis=0))
        screen = flow / np.array([width, height]) / seconds
        residual = None if background is None else screen - background / seconds
        for track, mask in zip(tracks, frame_masks):
            visible = track["frames"][index]["visible"] and track["frames"][index + 1]["visible"]
            track["motion"].append({
                "start": first.time, "end": second.time,
                "reliable": bool(visible and photometric["reliable"]),
                "photometric": photometric,
                "screen": _local_flow(screen, mask) if visible else None,
                "residual": _local_flow(residual, mask) if visible and residual is not None else None,
                "camera_confidence": confidence,
            })
    return tracks


class Tracker:
    def __init__(self, directory: Path, flow_model, device="cpu"):
        import torch
        from transformers import Sam2VideoModel, Sam2VideoProcessor

        self.profile = read(directory / "subject-profile.json")
        if self.profile != prepare_model(directory, flow_model.profile, device):
            raise ValueError("Subject checkpoint or runtime differs from its prepared profile")
        self.device, self.flow = device, flow_model
        self.model = Sam2VideoModel.from_pretrained(directory, local_files_only=True).to(device).eval()
        self.processor = Sam2VideoProcessor.from_pretrained(directory, local_files_only=True)
        self._torch = torch

    def describe(self, rows, *, region=None, point=None, seed_time=None, cancelled=lambda: False, measure_motion=True):
        torch = self._torch
        if not 2 <= len(rows) <= 25 or rows[-1].time - rows[0].time > 4.1:
            raise ValueError("Subject tracking requires two to 25 samples over at most four seconds")
        if any(b.time <= a.time for a, b in zip(rows, rows[1:])):
            raise ValueError("Subject timestamps must increase")
        if region is not None and point is not None:
            raise ValueError("Choose either a subject point or a region")
        width, height = rows[0].image.size
        if any(row.image.size != (width, height) for row in rows):
            raise ValueError("Tracking requires a constant source picture size")
        seed = 0 if seed_time is None else min(range(len(rows)), key=lambda i: abs(rows[i].time - seed_time))
        params, automatic = {}, region is None and point is None
        if region is not None:
            box = region if isinstance(region, dict) else vars(region)
            x, y, w, h = [float(box[k]) for k in ("x", "y", "width", "height")]
            if not all(math.isfinite(v) for v in (x, y, w, h)) or min(x, y) < 0 or min(w, h) <= 0 or x + w > 1 or y + h > 1:
                raise ValueError("Subject box must lie within the source picture")
            params["input_boxes"] = [[[x * width, y * height, (x + w) * width, (y + h) * height]]]
            ids = [1]
        else:
            points = [(x, y) for y in (0.25, 0.5, 0.75) for x in (0.25, 0.5, 0.75)] if automatic else [point]
            if any(len(p) != 2 or not all(math.isfinite(v) and 0 <= v <= 1 for v in p) for p in points):
                raise ValueError("Subject point must lie within the source picture")
            params = {"input_points": [[[[x * width, y * height]] for x, y in points]],
                      "input_labels": [[[1] for _ in points]]}
            ids = list(range(1, len(points) + 1))
        _cancel(cancelled)
        session = self.processor.init_video_session(
            video=[row.image for row in rows], inference_device=self.device,
            video_storage_device="cpu", processing_device="cpu", max_vision_features_cache_size=1,
        )
        # Transformers stores and clears the supplied object-id list internally.
        self.processor.add_inputs_to_inference_session(session, seed, ids.copy(), **params)
        context = torch.autocast("cuda", dtype=torch.bfloat16) if self.device == "cuda" else nullcontext()
        # Retain compact masks (long side 320); source images/PTS remain untouched.
        size = (max(1, round(height * min(1, 320 / max(width, height)))),
                max(1, round(width * min(1, 320 / max(width, height)))))

        def unpack(output):
            logits = torch.nn.functional.interpolate(output.pred_masks.float(), size=size,
                                                       mode="bilinear", align_corners=False)[:, 0]
            masks = (logits > 0).cpu().numpy()
            stable = ((logits > 1).sum((-1, -2)) / (logits > -1).sum((-1, -2)).clamp(min=1)).cpu().numpy()
            present = torch.sigmoid(output.object_score_logits.float()).flatten().cpu().numpy()
            return masks, np.minimum(stable, present)

        with torch.inference_mode(), context:
            output = self.model(inference_session=session, frame_idx=seed)
            first_masks, first_quality = unpack(output)
            selected = []
            for i in sorted(range(len(first_masks)), key=lambda i: _salience(first_masks[i], first_quality[i]), reverse=True):
                if not mask_summary(first_masks[i], quality=first_quality[i])["visible"]:
                    continue
                if any(np.logical_and(first_masks[i], first_masks[j]).sum() / max(1, np.logical_or(first_masks[i], first_masks[j]).sum()) > 0.65 for j in selected):
                    continue
                selected.append(i)
                if len(selected) >= 3:
                    break
            if not selected:
                return {"profile_id": self.profile["id"], "tracks": []}
            if automatic:
                # Reuse preprocessed video; only selected masks get temporal state.
                session.reset_tracking_data()
                ids = list(range(1, len(selected) + 1))
                input_masks = [torch.from_numpy(_resize_mask(first_masks[i], (height, width))) for i in selected]
                self.processor.add_inputs_to_inference_session(session, seed, ids, input_masks=input_masks)
            else:
                selected = [0]
            track_masks = np.zeros((len(selected), len(rows), *size), dtype=bool)
            qualities = np.zeros((len(selected), len(rows)))
            for reverse in (False, True):
                for output in self.model.propagate_in_video_iterator(session, start_frame_idx=seed, reverse=reverse):
                    _cancel(cancelled)
                    values, quality = unpack(output)
                    track_masks[:, output.frame_idx] = values
                    qualities[:, output.frame_idx] = quality
            del session
        tracks = describe_masks(rows, track_masks, self.flow, qualities=qualities, cancelled=cancelled,
                                measure_motion=measure_motion)
        for track in tracks:
            track["profile_id"] = self.profile["id"]
            track["prompt"] = "automatic" if automatic else "region" if region is not None else "point"
        return {"profile_id": self.profile["id"], "tracks": tracks}


def _vector_similarity(first, second):
    first, second = np.asarray(first, dtype=float), np.asarray(second, dtype=float)
    if not np.isfinite(first).all() or not np.isfinite(second).all():
        raise ValueError("Non-finite subject descriptor")
    a, b = np.linalg.norm(first), np.linalg.norm(second)
    if max(a, b) < 1e-8:
        return 1.0
    if min(a, b) < 1e-8:
        return 0.0
    return float(max(0, np.dot(first, second) / (a * b)) * min(a, b) / max(a, b))


def silhouette_overlap(first, second):
    """Soft foreground IoU: empty cells cannot agree on a subject's shape."""
    first, second = np.asarray(first, dtype=float), np.asarray(second, dtype=float)
    if first.shape != (64,) or second.shape != (64,) or any(
            not np.isfinite(values).all() or (values < 0).any() or (values > 1).any()
            for values in (first, second)):
        raise ValueError("Invalid normalized subject silhouette")
    union = float(np.maximum(first, second).sum())
    return float(np.minimum(first, second).sum() / union) if union > 0 else 0.0


def _phases(sequence, channel):
    edges = np.linspace(sequence[0]["start"], sequence[-1]["end"], 4)
    result = []
    for low, high in zip(edges, edges[1:]):
        weights = np.asarray([max(0., min(high, x["end"]) - max(low, x["start"])) for x in sequence])
        if weights.sum() <= 0:
            return None
        result.append(np.average([x[channel]["vector"] for x in sequence], axis=0, weights=weights))
    return np.asarray(result)


def _supported_phases(sequence):
    edges = np.linspace(sequence[0]["start"], sequence[-1]["end"], 4)
    for low, high in zip(edges, edges[1:]):
        durations = [max(0., min(high, x["end"]) - max(low, x["start"])) for x in sequence]
        if sum(durations) <= 0 or sum(d for d, x in zip(durations, sequence) if x["reliable"]) / sum(durations) < .75:
            return False
    return True


def _boundary_window(track, timestamp, outgoing, cache):
    """One request's immutable track/cut descriptor, reused across pairings."""
    key = (id(track), timestamp, outgoing)
    if cache is not None and key in cache:
        # Retaining the track prevents Python object-id reuse across refinements.
        return cache[key][1]
    result = _describe_boundary(track, timestamp, outgoing)
    if cache is not None:
        cache[key] = (track, result)
    return result


def _describe_boundary(track, timestamp, outgoing):
    if outgoing:
        sequence = [x for x in track["motion"] if timestamp - 1.05 <= x["start"] and x["end"] <= timestamp + 1e-6]
        frames = [x for x in track["frames"] if x["time"] < timestamp - 1e-7]
    else:
        sequence = [x for x in track["motion"] if x["start"] >= timestamp - 1e-6 and x["end"] <= timestamp + 1.05]
        frames = [x for x in track["frames"] if x["time"] >= timestamp - 1e-7]
    if (len(sequence) < 3 or not frames or sum(x["reliable"] for x in sequence) / len(sequence) < .75
            or not sequence[-1 if outgoing else 0]["reliable"] or not _supported_phases(sequence)):
        return None
    frame = frames[-1 if outgoing else 0]
    if not frame["visible"]:
        return None
    sequence = [x for x in sequence if x["reliable"]]
    channels = {}
    for channel in ("screen", "residual"):
        if any(x[channel] is None for x in sequence):
            continue
        speed = float(np.mean([x[channel]["speed"] for x in sequence]))
        phases = _phases(sequence, channel) if speed >= .003 else None
        channels[channel] = {"speed": speed, "phases": None if phases is None else phases.ravel(),
                             "boundary": sequence[-1 if outgoing else 0][channel]["vector"]}
    return {"frame": frame, "channels": channels}


def similarity(outgoing, incoming, outgoing_time, incoming_time, *, cache=None):
    """Compare boundaries; optional cache is scoped to one immutable request."""
    if not outgoing.get("profile_id") or outgoing.get("profile_id") != incoming.get("profile_id"):
        raise ValueError("Cannot compare incompatible tracked-subject profiles")
    rejected = {"score": -1.0, "components": {}, "reliable": False}
    left = _boundary_window(outgoing, outgoing_time, True, cache)
    if left is None:
        return rejected
    right = _boundary_window(incoming, incoming_time, False, cache)
    if right is None:
        return rejected
    a, b = left["frame"], right["frame"]
    channels = {}
    for channel in ("screen", "residual"):
        if channel not in left["channels"] or channel not in right["channels"]:
            continue
        first, second = left["channels"][channel], right["channels"][channel]
        speeds = [first["speed"], second["speed"]]
        if max(speeds) < .003:
            continue
        if min(speeds) < .003:
            # A following camera on only one side changes visible continuity.
            # This is mismatched movement evidence, not a missing channel.
            channels[channel] = 0.0
            continue
        # Time order is retained with three consecutive phases, never frame-order sorting.
        phases = [first["phases"], second["phases"]]
        if any(x is None for x in phases):
            continue
        ordered = _vector_similarity(*phases)
        boundary = _vector_similarity(first["boundary"], second["boundary"])
        channels[channel] = .4 * ordered + .6 * boundary
    if "screen" not in channels and "residual" not in channels:
        return rejected
    position = float(np.exp(-4 * np.linalg.norm(np.asarray(a["centroid"]) - b["centroid"])))
    scale = float(np.exp(-abs(np.log(a["area"] / b["area"]))))
    shape = silhouette_overlap(a["silhouette"], b["silhouette"])
    ratios = [frame["box"]["width"] / frame["box"]["height"] * frame.get("picture_aspect", 1.)
              for frame in (a, b)]
    aspect = float(np.exp(-abs(np.log(ratios[0] / ratios[1]))))
    movement = float(np.mean(list(channels.values())))
    components = {"movement": movement, "position": position, "scale": scale, "silhouette": shape,
                  "aspect": aspect,
                  **{f"{k}_movement": v for k, v in channels.items()}}
    score = .6 * movement + .2 * position + .1 * scale + .1 * shape * aspect
    return {"score": float(score), "components": components, "reliable": movement >= .2,
            "outgoing_region": a["box"], "incoming_region": b["box"]}
