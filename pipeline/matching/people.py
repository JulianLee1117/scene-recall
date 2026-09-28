"""Local, independently versioned person-mask evidence for scene Match search.

Preparation is an explicit operator action. Runtime never downloads a model.
Masks describe detected people in one source frame; they do not prove identity,
pose, dancing, or motion continuity.
"""
from __future__ import annotations

import hashlib
from importlib.metadata import version
from pathlib import Path

import numpy as np
from PIL import Image

from pipeline.matching.cohort import digest, read, write_new

CONTRACT = "coco-person-mask-full-source-v2"
CHECKPOINT = "maskrcnn_resnet50_fpn_v2_coco-73cbd019.pth"
URL = "https://download.pytorch.org/models/" + CHECKPOINT
# Full digest is pinned after verifying the official torchvision hash prefix.
SHA256 = "73cbd0190fcbe3ba339921fbce2c3a0b6bb9126c9a133c85e43a2a8e060a109e"
MIN_SCORE = .7
MIN_AREA = .001
MAX_PEOPLE = 3
MAX_DETECTIONS = 100
MIN_RELATIVE_AREA = .25


def model_directory(config):
    return config.paths.assets_dir / "matching" / "models" / "people-maskrcnn-v2"


def _directory(config_or_directory):
    return model_directory(config_or_directory) if hasattr(config_or_directory, "paths") else Path(config_or_directory)


def _hash(path):
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def download_checkpoint(config):
    """Explicitly download the official checkpoint, never called by inference."""
    from torch.hub import download_url_to_file
    from torchvision.models.detection import MaskRCNN_ResNet50_FPN_V2_Weights

    if MaskRCNN_ResNet50_FPN_V2_Weights.COCO_V1.url != URL:
        raise ValueError("The installed torchvision checkpoint URL changed")
    directory = model_directory(config)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / CHECKPOINT
    if not path.exists():
        download_url_to_file(URL, path, hash_prefix="73cbd019", progress=False)
    if _hash(path) != SHA256:
        raise ValueError("Person checkpoint differs from the pinned official weights")
    return path


def _device(device=None):
    import torch
    chosen = device or ("cuda" if torch.cuda.is_available() else "cpu")
    if chosen not in {"cpu", "cuda"}:
        raise ValueError("Person detector device must be cpu or cuda")
    return chosen


def _payload(device):
    return {
        "contract": CONTRACT, "architecture": "maskrcnn_resnet50_fpn_v2",
        "weights": "MaskRCNN_ResNet50_FPN_V2_Weights.COCO_V1",
        "checkpoint": CHECKPOINT, "checkpoint_url": URL, "checkpoint_sha256": SHA256,
        "versions": {name: version(name) for name in ("torch", "torchvision", "numpy", "Pillow")},
        "device": device, "precision": "float32-eval-inference",
        "preprocess": "PIL-RGB-to-float32-0-1-full-source-v1",
        "min_size": 800, "max_size": 1333, "detections_per_image": MAX_DETECTIONS,
        "label": 1, "label_name": "person", "min_score": MIN_SCORE,
        "mask_threshold": .5, "min_mask_area": MIN_AREA, "min_mask_pixels": 16,
        "descriptor": "full-source-tight-mask-box-centroid-area-8x8-occupancy-v1",
        "salience": "mask-area-descending-score-tie-v1", "max_supported_people": MAX_PEOPLE,
        "selection": "all-salient-detected-people-without-truncation-v2",
        "min_relative_area": MIN_RELATIVE_AREA,
    }


def prepare(config, device=None):
    """Prepare existing local weights; call download_checkpoint separately first."""
    directory, chosen = model_directory(config), _device(device)
    if _hash(directory / CHECKPOINT) != SHA256:
        raise ValueError("Person checkpoint differs from the pinned official weights")
    payload = _payload(chosen)
    profile = {**payload, "id": digest(payload)}
    manifest = directory / "profile.json"
    if manifest.exists() and read(manifest) != profile:
        raise ValueError("Person detector profile changed; prepare a separate model directory")
    if not manifest.exists():
        write_new(manifest, profile)
    return profile


def load_profile(config_or_directory):
    """Validate the current local derivation manifest, without network access."""
    directory = _directory(config_or_directory)
    profile = read(directory / "profile.json")
    if profile.get("device") not in {"cpu", "cuda"}:
        raise ValueError("Invalid person detector profile device")
    expected = _payload(profile["device"])
    if profile != {**expected, "id": digest(expected)}:
        raise ValueError("Person detector profile changed or is incompatible")
    if not (directory / CHECKPOINT).is_file():
        raise ValueError("Prepared person detector weights are unavailable")
    return profile


def _array(value):
    return value.detach().cpu().numpy() if hasattr(value, "detach") else np.asarray(value)


def _summary(mask, score, identity, width, height):
    y, x = np.nonzero(mask)
    area = len(x) / (width * height)
    if len(x) < 16 or area < MIN_AREA:
        return None
    x0, x1, y0, y1 = int(x.min()), int(x.max()) + 1, int(y.min()), int(y.max()) + 1
    silhouette = np.asarray(Image.fromarray(mask[y0:y1, x0:x1].astype(np.uint8) * 255)
                            .resize((8, 8), Image.Resampling.BOX), dtype=float) / 255
    return {"id": str(identity), "score": float(score), "visible": True,
            "box": {"x": x0 / width, "y": y0 / height,
                    "width": (x1 - x0) / width, "height": (y1 - y0) / height},
            "centroid": [float((x.mean() + .5) / width), float((y.mean() + .5) / height)],
            "area": float(area), "silhouette": silhouette.ravel().tolist(),
            "picture_aspect": width / height}


def select_salient(detections):
    """Preserve the complete salient group, even when matching cannot support it.

    The detector output is bounded to 100 instances. The layout matcher supports
    at most three people, but truncating here would turn a crowd into a false
    three-person match. Unsupported counts must remain visible to the caller.
    """
    ordered = sorted(detections, key=lambda row: (-row["area"], -row["score"], row["id"]))
    if not ordered:
        return []
    return [row for row in ordered if row["area"] >= ordered[0]["area"] * MIN_RELATIVE_AREA]


def describe_prediction(prediction, image_size):
    """Validate detector output and reduce masks without changing source geometry."""
    width, height = image_size
    if (not isinstance(width, int) or not isinstance(height, int) or min(width, height) <= 0):
        raise ValueError("Person evidence requires positive integer picture dimensions")
    boxes, labels, scores = (_array(prediction[name]) for name in ("boxes", "labels", "scores"))
    masks = prediction["masks"]
    if (scores.ndim != 1 or boxes.shape != (len(scores), 4) or labels.shape != scores.shape
            or tuple(masks.shape) != (len(scores), 1, height, width)):
        raise ValueError("Malformed person detector output")
    if len(scores) > MAX_DETECTIONS:
        raise ValueError("Person detector exceeded its bounded output")
    detections = []
    for index, (box, label, score) in enumerate(zip(boxes, labels, scores)):
        if label != 1 or not np.isfinite(score) or not MIN_SCORE <= score <= 1:
            continue
        if (not np.isfinite(box).all() or not 0 <= box[0] < box[2] <= width
                or not 0 <= box[1] < box[3] <= height):
            continue
        mask = _array(masks[index, 0])
        if not np.isfinite(mask).all() or (mask < 0).any() or (mask > 1).any():
            continue
        found = _summary(mask >= .5, score, index, width, height)
        if found:
            detections.append(found)
    selected = select_salient(detections)
    return {"picture_aspect": width / height, "detections": detections,
            "selected": selected, "selected_complete_count": len(selected)}


class Detector:
    def __init__(self, config_or_directory, device=None):
        import torch
        from torchvision.models.detection import maskrcnn_resnet50_fpn_v2

        directory = _directory(config_or_directory)
        self.profile = load_profile(directory)
        self.device = _device(device or self.profile["device"])
        if self.device != self.profile["device"]:
            raise ValueError("Person detector device differs from its prepared profile")
        path = directory / CHECKPOINT
        if _hash(path) != SHA256:
            raise ValueError("Person checkpoint differs from the pinned official weights")
        # Both weight arguments must be None: torchvision may otherwise download
        # a backbone even when full-model weights are absent.
        self.model = maskrcnn_resnet50_fpn_v2(
            weights=None, weights_backbone=None, num_classes=91,
            min_size=self.profile["min_size"], max_size=self.profile["max_size"],
            box_detections_per_img=self.profile["detections_per_image"],
        )
        self.model.load_state_dict(torch.load(path, map_location="cpu", weights_only=True))
        self.model.to(self.device).eval()

    def describe(self, image):
        import torch
        from torchvision.transforms.functional import pil_to_tensor

        if not isinstance(image, Image.Image) or min(image.size) <= 0:
            raise ValueError("Person detection requires a source image")
        rgb = image.convert("RGB")
        tensor = pil_to_tensor(rgb).to(device=self.device, dtype=torch.float32) / 255
        with torch.inference_mode():
            prediction = self.model([tensor])[0]
        return {"profile_id": self.profile["id"], **describe_prediction(prediction, rgb.size)}


def main():
    import argparse
    import json
    from pipeline.config import load_config

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["prepare"])
    parser.add_argument("--device", choices=["cpu", "cuda"])
    args = parser.parse_args()
    config = load_config()
    download_checkpoint(config)
    print(json.dumps(prepare(config, args.device), indent=2))


if __name__ == "__main__":
    main()
