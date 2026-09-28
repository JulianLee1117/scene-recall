"""Bounded, offline framing representation pilot; never publishes search features.

Default invocation prepares a frozen, unjudged fixture. Explicit execution uses
only locally cached PE weights, in 32-frame resumable batches under the ingest
lock. Both descriptor arms share a forward pass, so timing is shared work.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import time
from typing import Any

import numpy as np
from filelock import FileLock, Timeout

CONTRACT = "framing-representation-pilot-v1"
MAX_FRAMES = 524
CHUNK_SIZE = 32
ARMS = ("pe_final", "pe_block17", "pe_global")
MAX_BYTES = 2 * 1024**3
MAX_SECONDS = 90 * 60


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def file_hash(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for data in iter(lambda: handle.read(4 * 1024**2), b""):
            hasher.update(data)
    return hasher.hexdigest()


def _write(path: Path, value: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, allow_nan=False)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _save_array(path: Path, value: np.ndarray) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("wb") as handle:
        np.save(handle, value, allow_pickle=False)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def select_fixture(rows: list[dict], film_ids: list[str]) -> list[dict]:
    """Timeline quantiles, one frame per unit; no model score or cache lookup."""
    if len(film_ids) != 8 or len(set(film_ids)) != 8:
        raise ValueError("The pilot requires exactly eight distinct films")
    selected = []
    for film_index, film_id in enumerate(film_ids):
        by_unit = {}
        for row in sorted((r for r in rows if r["film_id"] == film_id),
                          key=lambda r: (float(r["timestamp"]), r["frame_id"])):
            by_unit.setdefault(row["unit_id"], row)
        timeline = list(by_unit.values())
        if len(timeline) < 66:
            raise ValueError(f"Film {film_id} needs at least 66 distinct units")
        count = 66 if film_index < 4 else 65
        indices = [int((index + .5) * len(timeline) / count) for index in range(count)]
        references = {count // 3: "tune", 2 * count // 3: "heldout"} if film_index < 4 else {count // 2: "heldout"}
        for index, source in enumerate(indices):
            row = dict(timeline[source])
            row.update(role="reference" if index in references else "candidate",
                       split=references.get(index, "candidate"), judgment="unjudged")
            selected.append(row)
    if len(selected) != MAX_FRAMES or len({r["frame_id"] for r in selected}) != MAX_FRAMES:
        raise ValueError("Fixture identities are not unique")
    return selected


def source_bytes(row: dict, *, verify_hash: bool = True) -> bytes:
    path = Path(row["path"])
    before = path.stat()
    data = path.read_bytes()
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError(f"Source changed while reading {row['frame_id']}")
    if (after.st_size != row["source_size"] or after.st_mtime_ns != row["source_mtime_ns"]):
        raise ValueError(f"Source metadata drift: {row['frame_id']}")
    if verify_hash and hashlib.sha256(data).hexdigest() != row["sha256"]:
        raise ValueError(f"Source checksum drift: {row['frame_id']}")
    return data


def code_hashes() -> dict:
    from pipeline.ingest import embed
    result = {"runner": file_hash(Path(__file__)), "embed": file_hash(Path(embed.__file__))}
    optional_adapter = Path(__file__).with_name("framing_models.py")
    if optional_adapter.exists():
        result["framing_models"] = file_hash(optional_adapter)
    return result


def pe_profile(config) -> dict:
    from pipeline.index.framing_features import configured_framing_spatial_profile
    from pipeline.ingest.embed import resolve_visual_model_lineage
    profile = configured_framing_spatial_profile(config, ensure_weights=False)
    if profile is None:
        raise ValueError("Compatible PE weights must already exist locally; no download was attempted")
    lineage = resolve_visual_model_lineage(config.models.visual_encoder,
                                          model_revision=profile.model_revision, ensure_weights=False)
    if lineage is None:
        raise ValueError("Pinned PE checkpoint is not locally available")
    return {"spatial_profile": asdict(profile), "checkpoint_sha256": file_hash(lineage.snapshot_dir / "open_clip_model.safetensors"),
            "config_sha256": file_hash(lineage.snapshot_dir / "open_clip_config.json"),
            "blocks": {"pe_block17": 17, "pe_final": 23}, "block_indexing": "zero-based; 24 blocks",
            "preprocessing": "installed pinned OpenCLIP evaluation transform; full-image 336x336 squash",
            "extraction": "final LayerNorm applied to each selected block, exclude prefix tokens; 6x6 adaptive average; L2 per cell; float16 round trip",
            "global": "same forward final image_features, L2 normalized, float32",
            "precision": "float32 inference, no autocast", "numpy_version": np.__version__,
            "shared_forward_timing": True}


def prepare(out: Path, config, *, film_ids: list[str] | None = None, spatial: bool = False) -> dict:
    from lancedb.expr import col, lit
    from pipeline.index.snapshot import capture_snapshot
    from pipeline.index.writer import open_db, published_film_ids
    if out.exists() and any(out.iterdir()):
        raise ValueError("Use a new empty run directory")
    snapshot = capture_snapshot(config, open_db(config))
    published = published_film_ids(snapshot)
    if film_ids and (len(film_ids) != 8 or len(set(film_ids)) != 8 or not set(film_ids) <= published):
        raise ValueError("--film-id must specify exactly eight distinct published films")
    films = snapshot.open_table("films").search().select(["film_id", "title"]).limit(None).to_list()
    titles = {r["film_id"]: r["title"] for r in films}
    order = film_ids or sorted(published, key=lambda value: digest([CONTRACT, value]))
    columns = ["frame_id", "film_id", "unit_id", "shot_id", "timestamp", "timestamp_source",
               "path", "source_size", "source_mtime_ns"]
    chosen, rows = [], []
    for film_id in order:
        frames = snapshot.open_table("frames").search().select(columns).where(col("film_id") == lit(film_id)).limit(None).to_list()
        frames = [r for r in frames if r.get("unit_id") and r.get("path") and r.get("timestamp") is not None]
        if len({r["unit_id"] for r in frames}) < 66:
            if film_ids:
                raise ValueError(f"Insufficient frames for {film_id}")
            continue
        chosen.append(film_id)
        rows.extend(frames)
        if len(chosen) == 8:
            break
    fixture = select_fixture(rows, chosen)
    for row in fixture:
        row["path"] = str(Path(row["path"]).resolve(strict=True))
        stat = Path(row["path"]).stat()
        # An absent legacy metadata field is captured, never guessed.
        row["source_size"] = row.get("source_size") if row.get("source_size") is not None else stat.st_size
        row["source_mtime_ns"] = row.get("source_mtime_ns") if row.get("source_mtime_ns") is not None else stat.st_mtime_ns
        row["sha256"] = hashlib.sha256(source_bytes(row, verify_hash=False)).hexdigest()
        row["film_title"] = titles[row["film_id"]]
    payload = {"contract": CONTRACT, "created_at": datetime.now(timezone.utc).isoformat(),
               "snapshot_versions": snapshot.versions, "film_ids": chosen, "frames": fixture,
               "profile": pe_profile(config), "code_sha256": code_hashes(),
               "max_seconds": MAX_SECONDS, "max_bytes": MAX_BYTES,
               "selection": "timeline quantiles, one frame/unit, SHA-256 ordered films; independent of rankings",
               "quality_status": "unjudged; no known positives or negatives",
               "deferred_candidates": {"dinov3_vits16": "not run; local checkpoint/access feasibility pending",
                                       "pe_spatial": "not run; optional substitute pending feasibility",
                                       "eupe": "not run; optional efficiency follow-up"}}
    if spatial:
        from pipeline.experiments.framing_models import spatial_profile
        payload["spatial_profile"] = spatial_profile()
        payload["deferred_candidates"].pop("pe_spatial")
    manifest = {**payload, "manifest_sha256": digest(payload)}
    out.mkdir(parents=True, exist_ok=True)
    _write(out / "prepared.json", manifest)
    return manifest


def validate_manifest(out: Path, config) -> dict:
    manifest = _read(out / "prepared.json")
    payload = {key: value for key, value in manifest.items() if key != "manifest_sha256"}
    if manifest.get("contract") != CONTRACT or digest(payload) != manifest.get("manifest_sha256"):
        raise ValueError("Prepared manifest checksum mismatch")
    rows = manifest["frames"]
    if len(rows) != MAX_FRAMES or len({r["frame_id"] for r in rows}) != MAX_FRAMES:
        raise ValueError("Fixture frame identities are incomplete or duplicated")
    if sum(r["role"] == "candidate" for r in rows) != 512 or sum(r["split"] == "tune" for r in rows) != 4 or sum(r["split"] == "heldout" for r in rows) != 8:
        raise ValueError("Fixture roles or reference splits are invalid")
    if manifest["code_sha256"] != code_hashes() or manifest["profile"] != pe_profile(config):
        raise ValueError("Runner, model, extraction profile or dependencies changed; prepare a new run")
    if "spatial_profile" in manifest:
        from pipeline.experiments.framing_models import spatial_profile
        if manifest["spatial_profile"] != spatial_profile():
            raise ValueError("PE-Spatial profile changed; prepare a new run")
    for row in rows:
        source_bytes(row)
    return manifest


class PEExtractor:
    def __init__(self, profile: dict):
        from pipeline.ingest import embed
        # Recheck local artifacts before the loader's normal ensure_weights=True
        # path; offline guards also prevent accidental fallback network traffic.
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        self.encoder = embed._load_model("pe_core_l14", model_revision=profile["spatial_profile"]["model_revision"])
        if len(self.encoder.model.visual.trunk.blocks) != 24:
            raise ValueError("This profile requires exactly 24 PE transformer blocks")
        self.metadata = {"device": str(self.encoder.device), "preprocess": repr(self.encoder.preprocess)}
        self._checked = False
        import torch
        self.metadata["cuda_device"] = torch.cuda.get_device_name() if self.encoder.device.type == "cuda" else None
        if self.encoder.device.type == "cuda":
            torch.cuda.reset_peak_memory_stats()

    def __call__(self, rows: list[dict]) -> dict[str, np.ndarray]:
        import torch
        import torch.nn.functional as F
        from PIL import Image
        from pipeline.ingest import embed
        output_arrays = {arm: [] for arm in ARMS}
        for start in range(0, len(rows), 8):
            images = []
            for row in rows[start:start + 8]:
                with Image.open(BytesIO(source_bytes(row))) as image:
                    images.append(image.convert("RGB"))
            with embed._MODEL_LOCK, torch.inference_mode():
                batch = self.encoder._image_batch(images)
                if tuple(batch.shape[1:]) != (3, 336, 336):
                    raise ValueError("Pinned PE transform changed image geometry")
                output = self.encoder.model.visual.forward_intermediates(batch, indices=[17, 23],
                    normalize_intermediates=True, output_fmt="NCHW", output_extra_tokens=False)
                intermediates = output["image_intermediates"]
                if len(intermediates) != 2:
                    raise ValueError("PE did not return both selected blocks")
                if not self._checked:
                    baseline_global, baseline_grid = self.encoder.encode_spatial_images(images[:2], 6)
                    repeat = self.encoder.model.visual.forward_intermediates(batch[:2], indices=[17, 23],
                        normalize_intermediates=True, output_fmt="NCHW", output_extra_tokens=False)
                    def grid_values(tensor):
                        return F.normalize(F.adaptive_avg_pool2d(tensor, (6, 6)), p=2, dim=1).half().float()
                    if baseline_grid is None:
                        raise ValueError("Current baseline spatial extraction is unavailable")
                    differences = {
                        "baseline_final_grid_max_abs": float((grid_values(intermediates[1][:2]) - baseline_grid.half().float()).abs().max()),
                        "baseline_global_max_abs": float((F.normalize(output["image_features"][:2], dim=-1) - F.normalize(baseline_global, dim=-1)).abs().max()),
                        "repeat_final_grid_max_abs": float((grid_values(intermediates[1][:2]) - grid_values(repeat["image_intermediates"][1])).abs().max()),
                        "repeat_intermediate_grid_max_abs": float((grid_values(intermediates[0][:2]) - grid_values(repeat["image_intermediates"][0])).abs().max()),
                    }
                    if any(value > .001 for value in differences.values()):
                        raise ValueError(f"PE baseline parity/repeatability exceeds tolerance: {differences}")
                    self.metadata["feasibility_checks"] = {"frame_count": len(images[:2]), "tolerance": .001, **differences}
                    self._checked = True
                for arm, features in zip(("pe_block17", "pe_final"), intermediates, strict=True):
                    grid = F.normalize(F.adaptive_avg_pool2d(features, (6, 6)), p=2, dim=1)
                    output_arrays[arm].append(grid.permute(0, 2, 3, 1).cpu().float().numpy().astype(np.float16))
                global_features = F.normalize(output["image_features"], p=2, dim=-1)
                output_arrays["pe_global"].append(global_features.cpu().float().numpy())
        if self.encoder.device.type == "cuda":
            self.metadata["peak_gpu_allocated_bytes"] = int(torch.cuda.max_memory_allocated())
        return {arm: np.concatenate(arrays) for arm, arrays in output_arrays.items()}

    def close(self):
        import gc
        import torch
        from pipeline.ingest import embed
        for key in list(embed._MODEL_CACHE):
            if embed._MODEL_CACHE[key] is self.encoder:
                del embed._MODEL_CACHE[key]
        self.encoder = None
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()


def _validate_arrays(arrays: dict, count: int) -> None:
    if set(arrays) not in (set(ARMS), {*ARMS, "pe_spatial"}):
        raise ValueError("Descriptor arms incomplete")
    for arm, array in arrays.items():
        shape = (count, 1024) if arm == "pe_global" else (count, 6, 6, 384 if arm == "pe_spatial" else 1024)
        if array.shape != shape or not np.isfinite(array).all() or np.any(np.linalg.norm(array.astype(np.float32), axis=-1) < 1e-8):
            raise ValueError(f"Invalid descriptor shape/value in {arm}")
        if array.dtype != (np.float32 if arm == "pe_global" else np.float16):
            raise ValueError(f"Unexpected descriptor precision in {arm}")


def _load_chunk(out: Path, manifest: dict, start: int, count: int) -> dict | None:
    receipt_path = out / f"batch-{start:04d}.json"
    if not receipt_path.exists():
        return None
    receipt = _read(receipt_path)
    rows = manifest["frames"][start:start + count]
    if receipt.get("manifest_sha256") != manifest["manifest_sha256"] or receipt.get("frame_ids") != [r["frame_id"] for r in rows]:
        raise ValueError("Batch receipt does not match the frozen source order")
    arrays = {}
    for arm in ARMS:
        path = out / f"{arm}-{start:04d}.npy"
        if file_hash(path) != receipt["files"][arm]:
            raise ValueError("Descriptor checksum mismatch")
        arrays[arm] = np.load(path, allow_pickle=False)
    _validate_arrays(arrays, count)
    return arrays


def rank_results(manifest: dict, arrays: dict) -> dict:
    """Search every eligible candidate. No appearance shortlist can hide a frame."""
    frames = manifest["frames"]
    _validate_arrays(arrays, len(frames))
    results = []
    for reference_index, reference in enumerate(frames):
        if reference["role"] != "reference":
            continue
        eligible = [i for i, row in enumerate(frames) if row["role"] == "candidate"
                    and row["film_id"] != reference["film_id"]
                    and (not reference.get("sha256") or row.get("sha256") != reference["sha256"])]
        if not eligible:
            raise ValueError("No cross-film candidates remain")
        global_scores = np.clip(arrays["pe_global"][eligible] @ arrays["pe_global"][reference_index], -1, 1)
        scores = {"global_only": global_scores}
        for arm in ("pe_final", "pe_block17", *(["pe_spatial"] if "pe_spatial" in arrays else [])):
            candidate = arrays[arm][eligible].astype(np.float32)
            query = arrays[arm][reference_index].astype(np.float32)
            spatial = np.einsum("hwd,nhwd->n", query, candidate, optimize=True) / 36
            scores[arm] = spatial.clip(-1, 1)
            scores[arm + "_blend"] = .65 * global_scores + .35 * scores[arm]
        rankings = {}
        for arm, values in scores.items():
            order = sorted(range(len(eligible)), key=lambda i: (-float(values[i]), frames[eligible[i]]["frame_id"]))
            rankings[arm] = [{"frame_id": frames[eligible[i]]["frame_id"], "film_id": frames[eligible[i]]["film_id"],
                              "film_title": frames[eligible[i]].get("film_title"), "path": frames[eligible[i]]["path"],
                              "timestamp": frames[eligible[i]]["timestamp"], "score": float(values[i])} for i in order[:10]]
        baseline_ids = {row["frame_id"] for row in rankings["pe_final"]}
        overlap = {arm: len(baseline_ids & {row["frame_id"] for row in ranking}) for arm, ranking in rankings.items()}
        results.append({"reference_id": reference["frame_id"], "split": reference["split"], "path": reference["path"],
                        "eligible_candidate_count": len(eligible), "rankings": rankings,
                        "top10_overlap_with_final_grid": overlap})
    return {"manifest_sha256": manifest["manifest_sha256"], "quality_status": "unjudged",
            "search": "exact over all eligible frozen candidates; same-film and source-byte duplicates excluded; perceptual-duplicate review pending",
            "timing_caveat": "Both PE grids/global share one extraction forward; extraction timing is not per-arm production latency",
            "results": results}


def _spatial_pass(out, config, manifest, state, *, started, previous_elapsed, max_seconds, max_batches):
    """Distinct encoder in its own sequential pass, with independent receipts."""
    from pipeline.experiments.framing_models import SpatialExtractor
    from pipeline.ingest.locks import global_ingest_lock
    chunks, extractor, completed = [], None, 0
    for start in range(0, len(manifest["frames"]), CHUNK_SIZE):
        rows = manifest["frames"][start:start + CHUNK_SIZE]
        receipt_path = out / f"spatial-batch-{start:04d}.json"
        path = out / f"pe_spatial-{start:04d}.npy"
        if receipt_path.exists():
            receipt = _read(receipt_path)
            if (receipt.get("manifest_sha256") != manifest["manifest_sha256"]
                    or receipt.get("frame_ids") != [r["frame_id"] for r in rows]
                    or receipt.get("sha256") != file_hash(path)):
                raise ValueError("PE-Spatial batch identity or checksum mismatch")
            array = np.load(path, allow_pickle=False)
        else:
            if (out / "STOP").exists():
                return "stopped", None
            if previous_elapsed + time.monotonic() - started >= max_seconds:
                return "time_budget", None
            used = sum(p.stat().st_size for p in out.rglob("*") if p.is_file())
            if used + len(rows) * 36 * 384 * 2 + 1024**2 > min(manifest["max_bytes"], MAX_BYTES):
                return "storage_budget", None
            try:
                with global_ingest_lock(config.paths.assets_dir):
                    load_started = time.monotonic()
                    if extractor is None:
                        extractor = SpatialExtractor(manifest["spatial_profile"])
                        state["spatial_model_load_seconds"] = time.monotonic() - load_started
                    encoding_started = time.monotonic()
                    array = extractor(rows)
                    elapsed = time.monotonic() - encoding_started
            except Timeout:
                return "ingest_lock_busy", None
            _validate_spatial(array, len(rows))
            _save_array(path, array)
            _write(receipt_path, {"manifest_sha256": manifest["manifest_sha256"],
                    "frame_ids": [r["frame_id"] for r in rows], "sha256": file_hash(path),
                    "extraction_seconds": elapsed, "hardware": getattr(extractor, "metadata", {})})
            completed += 1
        _validate_spatial(array, len(rows))
        chunks.append(array)
        state.update(spatial_completed_frames=start + len(rows), status="running_spatial",
                     elapsed_seconds=previous_elapsed + time.monotonic() - started)
        _write(out / "progress.json", state)
        if max_batches is not None and completed >= max_batches and start + len(rows) < len(manifest["frames"]):
            return "batch_limit", None
    return "complete", np.concatenate(chunks)


def _validate_spatial(array, count):
    if (array.shape != (count, 6, 6, 384) or array.dtype != np.float16
            or not np.isfinite(array).all() or np.any(np.linalg.norm(array.astype(np.float32), axis=-1) < 1e-8)):
        raise ValueError("Invalid PE-Spatial descriptor shape, precision or values")


def run(out: Path, config, *, resume: bool = False, max_batches: int | None = None,
        max_seconds: float = MAX_SECONDS, extractor_factory=None, phase: str = "all") -> dict:
    from pipeline.ingest.locks import global_ingest_lock
    if not 0 < max_seconds <= MAX_SECONDS:
        raise ValueError("Execution budget must be at most 90 minutes")
    if phase not in ("all", "pe", "spatial"):
        raise ValueError("Unknown execution phase")
    with FileLock(out / ".pilot.lock", timeout=0):
        manifest = validate_manifest(out, config)
        state_path = out / "progress.json"
        if state_path.exists() and not resume:
            raise ValueError("Existing run requires --resume")
        state = _read(state_path) if state_path.exists() else {"manifest_sha256": manifest["manifest_sha256"], "elapsed_seconds": 0., "completed_frames": 0}
        if state.get("manifest_sha256") != manifest["manifest_sha256"]:
            raise ValueError("Progress belongs to a different manifest")
        previous_elapsed = float(state.get("elapsed_seconds", 0))
        started = time.monotonic()
        if phase == "spatial":
            if "spatial_profile" not in manifest:
                raise ValueError("PE-Spatial must be selected during preparation")
            status, _ = _spatial_pass(out, config, manifest, state,
                started=started, previous_elapsed=previous_elapsed, max_seconds=max_seconds, max_batches=max_batches)
            state.update(status="spatial_complete" if status == "complete" else status,
                         elapsed_seconds=previous_elapsed + time.monotonic() - started)
            _write(state_path, state)
            return state
        extractor, completed_batches, all_chunks = None, 0, {arm: [] for arm in ARMS}
        status = "complete"
        for start in range(0, len(manifest["frames"]), CHUNK_SIZE):
            rows = manifest["frames"][start:start + CHUNK_SIZE]
            arrays = _load_chunk(out, manifest, start, len(rows))
            if arrays is None:
                if (out / "STOP").exists():
                    status = "stopped"
                    break
                if previous_elapsed + time.monotonic() - started >= max_seconds:
                    status = "time_budget"
                    break
                used_bytes = sum(p.stat().st_size for p in out.rglob("*") if p.is_file())
                if used_bytes + len(rows) * (2 * 36 * 1024 * 2 + 1024 * 4) + 1024**2 > min(manifest["max_bytes"], MAX_BYTES):
                    status = "storage_budget"
                    break
                try:
                    with global_ingest_lock(config.paths.assets_dir):
                        load_started = time.monotonic()
                        if extractor is None:
                            extractor = (extractor_factory or PEExtractor)(manifest["profile"])
                            state["model_load_seconds"] = time.monotonic() - load_started
                        encoding_started = time.monotonic()
                        arrays = extractor(rows)
                        encoding_seconds = time.monotonic() - encoding_started
                except Timeout:
                    status = "ingest_lock_busy"
                    break
                _validate_arrays(arrays, len(rows))
                files = {}
                for arm in ARMS:
                    path = out / f"{arm}-{start:04d}.npy"
                    _save_array(path, arrays[arm])
                    files[arm] = file_hash(path)
                _write(out / f"batch-{start:04d}.json", {"manifest_sha256": manifest["manifest_sha256"],
                       "frame_ids": [r["frame_id"] for r in rows], "files": files,
                       "shared_extraction_seconds": encoding_seconds, "hardware": getattr(extractor, "metadata", {})})
                completed_batches += 1
            for arm in ARMS:
                all_chunks[arm].append(arrays[arm])
            state.update(completed_frames=start + len(rows), elapsed_seconds=previous_elapsed + time.monotonic() - started,
                         status="running")
            _write(state_path, state)
            if max_batches is not None and completed_batches >= max_batches and state["completed_frames"] < len(manifest["frames"]):
                status = "batch_limit"
                break
        spatial_array = None
        if status == "complete" and "spatial_profile" in manifest and phase != "pe":
            if isinstance(extractor, PEExtractor):
                extractor.close()
            if max_batches is not None and completed_batches >= max_batches:
                status = "batch_limit"
            else:
                status, spatial_array = _spatial_pass(out, config, manifest, state,
                    started=started, previous_elapsed=previous_elapsed, max_seconds=max_seconds,
                    max_batches=None if max_batches is None else max_batches - completed_batches)
        if status == "complete" and "spatial_profile" in manifest and phase == "pe":
            status = "pe_complete"
        if status == "complete":
            ranking_started = time.monotonic()
            final_arrays = {arm: np.concatenate(chunks) for arm, chunks in all_chunks.items()}
            if spatial_array is not None:
                final_arrays["pe_spatial"] = spatial_array
            report = rank_results(manifest, final_arrays)
            report["shared_ranking_seconds"] = time.monotonic() - ranking_started
            _write(out / "results.json", report)
        state.update(status=status, elapsed_seconds=previous_elapsed + time.monotonic() - started)
        _write(state_path, state)
        return state


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--film-id", action="append")
    parser.add_argument("--spatial", action="store_true", help="Include an already cached PE-Spatial checkpoint during preparation")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--max-batches", type=int)
    parser.add_argument("--max-minutes", type=float, default=90)
    parser.add_argument("--phase", choices=("all", "pe", "spatial"), default="all", help="Execution phase; separate 32-frame feasibility passes are resumable")
    args = parser.parse_args()
    if args.resume and not args.execute:
        parser.error("--resume requires --execute")
    if args.execute and args.film_id:
        parser.error("Film selection is frozen during preparation")
    if args.execute and args.spatial:
        parser.error("--spatial is selected during preparation; execution follows the frozen manifest")
    if args.max_batches is not None and args.max_batches < 1:
        parser.error("--max-batches must be positive")
    from pipeline.config import load_config
    config = load_config()
    result = run(args.out, config, resume=args.resume, max_batches=args.max_batches,
                 max_seconds=args.max_minutes * 60, phase=args.phase) if args.execute else prepare(args.out, config, film_ids=args.film_id, spatial=args.spatial)
    print(json.dumps({k: v for k, v in result.items() if k not in ("frames", "profile")}, indent=2))


if __name__ == "__main__":
    main()
