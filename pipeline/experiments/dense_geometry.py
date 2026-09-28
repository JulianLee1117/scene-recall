"""Bounded, local-only DINOv3 dense-feature challenger. Never product-active."""

from __future__ import annotations

import argparse
from contextlib import nullcontext
from dataclasses import asdict
import hashlib
from importlib.metadata import version
from io import BytesIO
import json
from pathlib import Path
import sys
from typing import Any

import numpy as np
from PIL import Image

from pipeline.experiments.region_geometry import Box

CONTRACT = "dinov3-region-grid-shadow-v1"
MAX_FRAMES = 200
MAX_QUERIES = 200
MAX_COMPARISONS = 5000
MAX_IMAGE_BYTES = 64 * 1024 * 1024
MAX_IMAGE_PIXELS = 20_000_000
GRID_SIZE = 8


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _hash_file(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be nonempty text")
    return value


def _write_new(path: Path, value: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, allow_nan=False)
        handle.write("\n")


def checkpoint_profile(checkpoint: Path, model_id: str, image_size: int = 448) -> dict:
    """Fingerprint actual safe weight shards, configs, preprocessing and runtime."""
    checkpoint = checkpoint.resolve(strict=True)
    if not checkpoint.is_dir():
        raise ValueError("Checkpoint must be an existing local directory")
    if (checkpoint / "adapter_config.json").exists():
        raise ValueError("Adapter checkpoints could redirect base weights and are unsupported")
    _text(model_id, "model_id")
    config = _read(checkpoint / "config.json")
    if config.get("model_type") != "dinov3_vit":
        raise ValueError("Only a local Hugging Face DINOv3ViT checkpoint is supported")
    if config.get("auto_map") or config.get("quantization_config"):
        raise ValueError("Custom code and quantized checkpoints are outside this profile")
    processor_config = _read(checkpoint / "preprocessor_config.json")
    if processor_config.get("image_processor_type", "DINOv3ViTImageProcessor") != "DINOv3ViTImageProcessor":
        raise ValueError("Checkpoint preprocessing must declare the DINOv3ViT image processor")
    patch = config.get("patch_size", 16)
    if isinstance(patch, (list, tuple)) and len(patch) == 2 and patch[0] == patch[1]:
        patch = patch[0]
    if isinstance(patch, bool) or not isinstance(patch, int) or patch <= 0:
        raise ValueError("Checkpoint requires a square integer patch size")
    if isinstance(image_size, bool) or not isinstance(image_size, int) or not 224 <= image_size <= 672 or image_size % patch:
        raise ValueError("Image size must be 224–672 pixels and divisible by patch size")
    dimensions, registers = config.get("hidden_size"), config.get("num_register_tokens", 0)
    if isinstance(dimensions, bool) or not isinstance(dimensions, int) or not 1 <= dimensions <= 8192:
        raise ValueError("Unsupported descriptor dimensions")
    if isinstance(registers, bool) or not isinstance(registers, int) or not 0 <= registers <= 32:
        raise ValueError("Invalid register token count")
    files = ["config.json", "preprocessor_config.json"]
    index = checkpoint / "model.safetensors.index.json"
    if index.is_file():
        if (checkpoint / "model.safetensors").exists():
            raise ValueError("Checkpoint contains ambiguous single-file and sharded weights")
        weight_map = _read(index).get("weight_map")
        if not isinstance(weight_map, dict) or not weight_map:
            raise ValueError("Safetensors index needs a nonempty weight map")
        shards = sorted(set(weight_map.values()))
        if len(shards) > 100 or any(not isinstance(name, str) or Path(name).name != name or not name.endswith(".safetensors") or "/" in name or "\\" in name for name in shards):
            raise ValueError("Weight shards must be local safetensors basenames")
        files += ["model.safetensors.index.json", *shards]
    else:
        files.append("model.safetensors")
    file_hashes = {name: _hash_file(checkpoint / name) for name in sorted(files)}
    payload = {
        "schema_version": 1, "kind": "dense_geometry_profile", "contract": CONTRACT,
        "model_id": model_id, "weight_revision": _digest(file_hashes), "files": file_hashes,
        "model_type": "dinov3_vit", "dimensions": dimensions, "patch_size": patch,
        "register_tokens": registers,
        "preprocessing": {"image_size": image_size, "picture": "full decoded RGB source; EXIF rotation rejected",
            "resize": "Pillow bilinear aspect-preserving resize with centered black padding",
            "processor_overrides": {"do_resize": False, "do_center_crop": False},
            "region_space": "original-image-normalized-v1"},
        "extraction": "float32 eager evaluation; last hidden state without CLS/register tokens; L2 per patch",
        "comparison": {"grid_size": GRID_SIZE, "resampling": "bilinear patch centers; border clamp; L2 after interpolation",
            "ranking": "descending mean cosine of corresponding region-resampled cells; stable identity tie break"},
        "versions": {name: version(name) for name in ("transformers", "torch", "torchvision", "Pillow", "numpy")},
        "shadow_only": True,
    }
    return {**payload, "profile_id": f"{CONTRACT}-{_digest(payload)}"}


def validate_profile(profile: dict):
    payload = {key: value for key, value in profile.items() if key != "profile_id"}
    if profile.get("kind") != "dense_geometry_profile" or profile.get("schema_version") != 1 or profile.get("contract") != CONTRACT:
        raise ValueError("Unsupported dense geometry profile")
    if profile.get("profile_id") != f"{CONTRACT}-{_digest(payload)}":
        raise ValueError("Profile content fingerprint does not match its identity")


def _load_checkpoint(checkpoint: Path, device: str):
    # Direct built-in classes plus a verified local directory prevent custom
    # repository code or implicit Hub model resolution from entering this run.
    import torch
    from transformers import DINOv3ViTImageProcessor, DINOv3ViTModel
    processor = DINOv3ViTImageProcessor.from_pretrained(
        str(checkpoint), local_files_only=True, trust_remote_code=False,
    )
    model = DINOv3ViTModel.from_pretrained(
        str(checkpoint), local_files_only=True, trust_remote_code=False,
        use_safetensors=True, dtype=torch.float32, attn_implementation="eager",
    ).to(device)
    model.eval()
    return processor, model


class DinoV3LocalAdapter:
    def __init__(self, checkpoint: Path, profile: dict, *, device: str = "cpu"):
        if device not in ("cpu", "cuda"):
            raise ValueError("Device must be cpu or cuda")
        validate_profile(profile)
        actual = checkpoint_profile(checkpoint, profile["model_id"], profile["preprocessing"]["image_size"])
        if actual != profile:
            raise ValueError("Checkpoint or runtime differs from the pinned profile; create a new profile")
        self.profile, self.device = profile, device
        self.processor, self.model = _load_checkpoint(checkpoint.resolve(strict=True), device)

    def extract(self, image: Image.Image) -> tuple[np.ndarray, Box]:
        import torch
        size = self.profile["preprocessing"]["image_size"]
        scale = min(size / image.width, size / image.height)
        width, height = max(1, round(image.width * scale)), max(1, round(image.height * scale))
        left, top = (size - width) // 2, (size - height) // 2
        canvas = Image.new("RGB", (size, size), (0, 0, 0))
        canvas.paste(image.resize((width, height), Image.Resampling.BILINEAR), (left, top))
        inputs = self.processor(images=canvas, return_tensors="pt", do_resize=False, do_center_crop=False)
        pixels = inputs["pixel_values"]
        if tuple(pixels.shape) != (1, 3, size, size):
            raise ValueError("Processor changed the pinned full-image geometry")
        with torch.inference_mode():
            output = self.model(pixel_values=pixels.to(device=self.device, dtype=torch.float32))
        grid = size // self.profile["patch_size"]
        prefix = 1 + self.profile["register_tokens"]
        tokens = output.last_hidden_state
        if tuple(tokens.shape) != (1, prefix + grid * grid, self.profile["dimensions"]):
            raise ValueError("DINOv3 patch token shape does not match the pinned profile")
        features = tokens[0, prefix:].detach().float().cpu().numpy().reshape(grid, grid, -1)
        features = normalize_features(features)
        return features, Box(left / size, top / size, width / size, height / size)


def normalize_features(features: np.ndarray) -> np.ndarray:
    values = np.asarray(features, dtype=np.float32)
    if values.ndim != 3 or any(size <= 0 for size in values.shape) or not np.isfinite(values).all():
        raise ValueError("Dense descriptors must be finite nonempty height×width×dimension arrays")
    norm = np.linalg.norm(values.astype(np.float64), axis=-1, keepdims=True)
    if np.any(norm < 1e-12):
        raise ValueError("Zero-norm dense descriptors cannot provide cosine evidence")
    return (values / norm).astype(np.float32)


def region_descriptor(features: np.ndarray, viewport: Box, region: Box = Box(0, 0, 1, 1)) -> np.ndarray:
    """Resample one source region to corresponding cells in a small dense grid."""
    values = normalize_features(features)
    height, width, _ = values.shape
    fractions = (np.arange(GRID_SIZE, dtype=np.float32) + .5) / GRID_SIZE
    xs = (viewport.x + (region.x + fractions * region.width) * viewport.width) * width - .5
    ys = (viewport.y + (region.y + fractions * region.height) * viewport.height) * height - .5
    xs, ys = np.clip(xs, 0, width - 1), np.clip(ys, 0, height - 1)
    x0, y0 = np.floor(xs).astype(int), np.floor(ys).astype(int)
    x1, y1 = np.minimum(x0 + 1, width - 1), np.minimum(y0 + 1, height - 1)
    wx, wy = (xs - x0)[None, :, None], (ys - y0)[:, None, None]
    top = values[y0[:, None], x0[None, :]] * (1 - wx) + values[y0[:, None], x1[None, :]] * wx
    bottom = values[y1[:, None], x0[None, :]] * (1 - wx) + values[y1[:, None], x1[None, :]] * wx
    return normalize_features(top * (1 - wy) + bottom * wy)


def dense_similarity(reference: np.ndarray, candidate: np.ndarray) -> float:
    if reference.shape != candidate.shape:
        raise ValueError("Only matching profile dimensions and comparison grids can be compared")
    first, second = normalize_features(reference), normalize_features(candidate)
    return float(np.clip(np.mean(np.sum(first * second, axis=-1)), -1, 1))


def extract_bundle(adapter: DinoV3LocalAdapter, document: dict, output: Path, *, image_base: Path) -> dict:
    if document.get("kind") != "dense_geometry_images" or document.get("schema_version") != 1:
        raise ValueError("Expected dense_geometry_images schema_version 1")
    frames = document.get("frames")
    if not isinstance(frames, list) or not 1 <= len(frames) <= MAX_FRAMES:
        raise ValueError("Select one to 200 explicitly identified operator images")
    identities = [_text(frame.get("id"), "frame id") for frame in frames]
    if len(set(identities)) != len(identities):
        raise ValueError("Image identities must be unique")
    output.mkdir(parents=True, exist_ok=False)
    rows = []
    for frame in frames:
        path = Path(_text(frame.get("path"), "operator image path"))
        if not path.is_absolute():
            path = image_base / path
        if not path.is_file() or path.stat().st_size > MAX_IMAGE_BYTES:
            raise ValueError("Operator image is unavailable or exceeds 64 MiB")
        content = path.read_bytes()
        if len(content) > MAX_IMAGE_BYTES:
            raise ValueError("Operator image exceeds 64 MiB")
        with Image.open(BytesIO(content)) as opened:
            if opened.width * opened.height > MAX_IMAGE_PIXELS:
                raise ValueError("Operator image exceeds 20 million pixels")
            if opened.getexif().get(274, 1) != 1:
                raise ValueError("Normalize EXIF orientation explicitly before supplying regions")
            source_size = {"width": opened.width, "height": opened.height}
            image = opened.convert("RGB")
        features, viewport = adapter.extract(image)
        features = normalize_features(features)
        filename = hashlib.sha256(frame["id"].encode()).hexdigest() + ".npz"
        destination = output / filename
        np.savez_compressed(destination, features=features)
        rows.append({
            "id": frame["id"], "source": frame.get("source"), "image_sha256": hashlib.sha256(content).hexdigest(),
            "source_picture": source_size, "viewport": asdict(viewport), "shape": list(features.shape),
            "descriptor_file": filename, "descriptor_sha256": _hash_file(destination),
        })
    result = {
        "schema_version": 1, "kind": "dense_geometry_bundle", "shadow_only": True,
        "profile": adapter.profile, "expected_rows": len(frames), "completed_rows": len(rows),
        "complete": True, "coverage_digest": _digest(rows), "frames": rows,
    }
    _write_new(output / "manifest.json", result)
    return result


def rank_bundle(bundle: Path, queries: dict) -> dict:
    manifest = _read(bundle / "manifest.json")
    if manifest.get("kind") != "dense_geometry_bundle" or manifest.get("schema_version") != 1 or manifest.get("complete") is not True:
        raise ValueError("A complete shadow descriptor bundle is required")
    validate_profile(manifest["profile"])
    rows = manifest["frames"]
    if not 1 <= len(rows) <= MAX_FRAMES or manifest["expected_rows"] != len(rows) or manifest["completed_rows"] != len(rows) or manifest["coverage_digest"] != _digest(rows):
        raise ValueError("Descriptor bundle is partial or has incompatible coverage")
    by_id = {row["id"]: row for row in rows}
    if len(by_id) != len(rows):
        raise ValueError("Descriptor identities must be unique")
    if queries.get("kind") != "dense_geometry_queries" or queries.get("schema_version") != 1:
        raise ValueError("Expected dense_geometry_queries schema_version 1")
    cases = queries.get("cases")
    if not isinstance(cases, list) or not 1 <= len(cases) <= MAX_QUERIES:
        raise ValueError("Provide one to 200 query cases")
    if sum(len(case.get("candidates", [])) for case in cases) > MAX_COMPARISONS:
        raise ValueError("One run is limited to 5000 independent comparisons")

    def load_selection(selection):
        identity = _text(selection.get("frame_id"), "frame_id")
        if identity not in by_id:
            raise ValueError(f"Frame {identity!r} is absent from this complete bundle")
        row = by_id[identity]
        filename = row["descriptor_file"]
        if Path(filename).name != filename or "/" in filename or "\\" in filename:
            raise ValueError("Descriptor filename must remain inside its bundle")
        path = bundle / filename
        if _hash_file(path) != row["descriptor_sha256"]:
            raise ValueError("Descriptor content changed after bundle completion")
        with np.load(path, allow_pickle=False) as archive:
            features = archive["features"]
        expected_grid = manifest["profile"]["preprocessing"]["image_size"] // manifest["profile"]["patch_size"]
        if list(features.shape) != row["shape"] or features.shape != (expected_grid, expected_grid, manifest["profile"]["dimensions"]):
            raise ValueError("Descriptor dimensions do not match the declared profile")
        region = Box(**selection.get("region", {"x": 0, "y": 0, "width": 1, "height": 1}))
        return region_descriptor(features, Box(**row["viewport"]), region)

    reports, seen = [], set()
    for case in cases:
        case_id = _text(case.get("id"), "case id")
        if case_id in seen:
            raise ValueError("Query case identities must be unique")
        seen.add(case_id)
        reference = load_selection(case["reference"])
        candidates = case.get("candidates")
        if not isinstance(candidates, list) or not 1 <= len(candidates) <= MAX_FRAMES:
            raise ValueError("Select one to 200 candidate regions")
        candidate_ids = [_text(item.get("frame_id"), "candidate frame_id") for item in candidates]
        if len(candidate_ids) != len(set(candidate_ids)) or case["reference"]["frame_id"] in candidate_ids:
            raise ValueError("Candidates must be unique and exclude the reference identity")
        ranking = [{"frame_id": item["frame_id"], "region": item.get("region"), "dense_region_cosine": dense_similarity(reference, load_selection(item))} for item in candidates]
        ranking.sort(key=lambda row: (-row["dense_region_cosine"], row["frame_id"]))
        reports.append({"id": case_id, "reference": case["reference"], "ranking": [{"rank": index + 1, **row} for index, row in enumerate(ranking)]})
    return {
        "schema_version": 1, "kind": "dense_geometry_rankings", "shadow_only": True, "production_activation": False,
        "profile_id": manifest["profile"]["profile_id"], "bundle_sha256": _digest(manifest), "queries_sha256": _digest(queries), "cases": reports,
        "note": "Independent DINOv3 dense region correspondence, not shape-only invariance or motion evidence. No PE scores or production rankings are mixed.",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    profile = commands.add_parser("profile")
    profile.add_argument("--checkpoint", type=Path, required=True)
    profile.add_argument("--model-id", required=True)
    profile.add_argument("--image-size", type=int, default=448)
    extract = commands.add_parser("extract")
    extract.add_argument("--checkpoint", type=Path, required=True)
    extract.add_argument("--profile", type=Path, required=True)
    extract.add_argument("--images", type=Path, required=True)
    extract.add_argument("--device", choices=["cpu", "cuda"], default="cpu")
    rank = commands.add_parser("rank")
    rank.add_argument("--bundle", type=Path, required=True)
    rank.add_argument("--queries", type=Path, required=True)
    for command in (profile, extract, rank):
        command.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.output.exists():
            raise FileExistsError("Output already exists; preserve prior profiles and runs")
        if args.command == "profile":
            _write_new(args.output, checkpoint_profile(args.checkpoint, args.model_id, args.image_size))
        elif args.command == "rank":
            _write_new(args.output, rank_bundle(args.bundle, _read(args.queries)))
        else:
            lock = nullcontext()
            if args.device == "cuda":
                from pipeline.config import load_config
                from pipeline.ingest.locks import global_ingest_lock
                lock = global_ingest_lock(load_config().paths.assets_dir)
            with lock:
                adapter = DinoV3LocalAdapter(args.checkpoint, _read(args.profile), device=args.device)
                extract_bundle(adapter, _read(args.images), args.output, image_base=args.images.parent)
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
