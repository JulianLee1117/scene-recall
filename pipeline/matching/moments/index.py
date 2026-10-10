"""The library moment index: every described instant, memory-mapped, plus coarse search vectors.

Built from each film's ``moments`` evidence (pictures) and ``measure`` evidence
(camera motion, hidden cuts, near-black stretches). The index is a derived
cache: ``python -m pipeline.matching.moments index`` rebuilds it from the
artifacts, and a build publishes a new directory atomically, so a reader never
sees a half-written index or mixes two builds.

Per moment it keeps what exact pair scoring needs (luma, edge field, colour,
brightness, camera motion, subject travel, instances) and whether the instant
is usable as a cut point. A coarse vector per moment (every other grid step)
drives retrieval: separately normalized parts (where things are, where the
light is, edge directions, main silhouette, movement), each reduced by PCA, so
a query can weight the parts by focus and the dot product stays a weighted
sum of part cosines.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
from pathlib import Path
import shutil
import threading
import time
from typing import Any, Callable
from uuid import uuid4

import numpy as np

from pipeline.evidence import store
from pipeline.matching.moments import score as scoring

CONTRACT = "match-moments-index-v1"
PARTS = {"layout": 16, "light": 12, "lines": 12, "shape": 12, "pose": 12, "color": 8, "motion": 4}
COARSE_STEP = 2                     # coarse vectors every other grid step (0.5 s); exact scoring expands to neighbours
CUT_GUARD_S = 0.3                   # stay this far from hidden cuts
DARK_LIT = 0.1                      # some part of the picture (a 1/576 cell) must reach this luma


def root(config: Any) -> Path:
    return Path(config.paths.assets_dir) / "matching" / "moments"


# ---------------------------------------------------------------------------
# Per-film assembly
# ---------------------------------------------------------------------------


def camera_at(times: np.ndarray, flow: list[list[float]], window: float = 0.25) -> np.ndarray:
    """Mean reliable camera motion (vx, vy, div, curl) per second around each time; NaN when unknown."""
    out = np.full((len(times), 4), np.nan, dtype=np.float32)
    if not flow:
        return out
    series = np.asarray(flow, dtype=np.float64)
    ok = series[:, 6] > 0
    series = series[ok]
    if not len(series):
        return out
    pair_times = series[:, 0] + 1 / 12            # a pair t -> t + 1/6 describes its midpoint
    order = np.argsort(pair_times)
    pair_times, values = pair_times[order], series[order, 1:5]
    low = np.searchsorted(pair_times, times - window)
    high = np.searchsorted(pair_times, times + window)
    cumulative = np.vstack([np.zeros((1, 4)), np.cumsum(values, axis=0)])
    counts = high - low
    sums = cumulative[high] - cumulative[low]
    has = counts > 0
    out[has] = (sums[has] / counts[has, None]).astype(np.float32)
    return out


def main_boxes(arrays: dict[str, np.ndarray]) -> tuple[np.ndarray, np.ndarray]:
    """Per moment: the main instance's box (NaN when none) and class code (0 when none)."""
    count = len(arrays["times"])
    boxes = np.full((count, 4), np.nan, dtype=np.float32)
    classes = np.zeros(count, dtype=np.uint8)
    if not len(arrays["inst_moment"]):
        return boxes, classes
    box = arrays["inst_box"].astype(np.float32) / 65535
    weight = scoring.box_area(box) * np.maximum(arrays["inst_score"].astype(np.float32) / 255, 0.3)
    order = np.lexsort((-weight, arrays["inst_moment"]))
    moments_sorted = arrays["inst_moment"][order]
    first = np.ones(len(order), dtype=bool)
    first[1:] = moments_sorted[1:] != moments_sorted[:-1]
    chosen = order[first]
    boxes[arrays["inst_moment"][chosen]] = box[chosen]
    classes[arrays["inst_moment"][chosen]] = arrays["inst_class"][chosen]
    return boxes, classes


def subject_velocity(times: np.ndarray, units: np.ndarray, boxes: np.ndarray, classes: np.ndarray) -> np.ndarray:
    """Main-subject travel (content fractions per second) from the neighbouring grid instants of one shot."""
    count = len(times)
    velocity = np.full((count, 2), np.nan, dtype=np.float32)
    if count < 3:
        return velocity
    centers = np.stack([(boxes[:, 0] + boxes[:, 2]) / 2, (boxes[:, 1] + boxes[:, 3]) / 2], axis=1)
    heights = boxes[:, 3] - boxes[:, 1]
    previous, following = np.arange(count) - 1, np.arange(count) + 1
    previous[0], following[-1] = 0, count - 1
    same = (units[previous] == units) & (units[following] == units) & (previous != np.arange(count)) & (following != np.arange(count))
    same &= scoring._FAMILY[classes[previous]] == scoring._FAMILY[classes[following]]
    same &= (classes[previous] > 0) & (classes[following] > 0)
    jump = np.hypot(*(centers[following] - centers[previous]).T)
    size = np.maximum(np.nan_to_num(heights[previous]), np.nan_to_num(heights[following]))
    same &= np.nan_to_num(jump, nan=9.0) < np.maximum(0.25, size)       # the same thing, not a detector swap
    span = times[following] - times[previous]
    ok = same & (span > 0)
    velocity[ok] = ((centers[following] - centers[previous])[ok] / span[ok, None]).astype(np.float32)
    return velocity


def usable(times: np.ndarray, units: np.ndarray, gray: np.ndarray, described: np.ndarray,
           cuts: dict[int, list[float]], pictures: dict[int, list[list[float]]] | None = None) -> np.ndarray:
    """Whether each instant can be a cut point: described, not black, clear of hidden cuts, inside a picture.

    Black means nothing in the picture is lit (a fade or black frame); a dark
    picture with a lit subject (a satellite in space, a face in the dark) is
    a real cut point, so the mean brightness alone does not decide. A shot the
    hero pass split into pictures (ADR-0098) only offers instants inside one
    of them, never the dissolve or missed cut between two.
    """
    lit = gray.reshape(len(gray), -1).max(axis=1) / 255 >= DARK_LIT
    ok = described & lit
    pictures = pictures or {}
    for position, (time_value, unit) in enumerate(zip(times, units)):
        if not ok[position]:
            continue
        if any(abs(time_value - cut) < CUT_GUARD_S for cut in cuts.get(int(unit), ())):
            ok[position] = False
        elif int(unit) in pictures and not any(start <= time_value <= end for start, end in pictures[int(unit)]):
            ok[position] = False
    return ok


# ---------------------------------------------------------------------------
# Coarse vectors
# ---------------------------------------------------------------------------


def coarse_parts(arrays: dict[str, Any], aspect: float) -> dict[str, np.ndarray]:
    """Raw coarse part features for every moment of one film (before PCA)."""
    count = len(arrays["times"])
    gray = arrays["gray"].astype(np.float32) / 255
    light = gray.reshape(count, 6, 3, 8, 4).mean(axis=(2, 4)).reshape(count, -1)             # 6 x 8
    light = light - light.mean(axis=1, keepdims=True)
    field = arrays["field"].astype(np.float32) / 127
    lines = field.reshape(count, 2, 3, 3, 8, 2).mean(axis=(3, 5)).reshape(count, -1)          # 2 x 3 x 8
    moments = arrays["_moments"]
    group, main = scoring.salient(moments)
    ids = group[group >= 0]
    layout = np.zeros((count, 5 * 8), dtype=np.float32)
    if len(ids):
        crops = np.tile(np.array([0.0, 0.0, 1.0, 1.0]), (count, 1))
        coverage = scoring.rasterize(moments, ids, crops, (5, 8), supersample=3).reshape(len(ids), -1)
        owner = np.searchsorted(moments.inst_ptr, ids, side="right") - 1
        people = moments.classes[ids] == scoring.PERSON
        np.maximum.at(layout, owner, coverage * np.where(people, 1.0, 0.7)[:, None])
    shape = np.zeros((count, 65), dtype=np.float32)
    has = main >= 0
    if has.any():
        silhouettes = moments.masks[main[has]].astype(np.float32).reshape(-1, 8, 2, 8, 2).mean(axis=(2, 4)).reshape(-1, 64)
        boxes = moments.boxes[main[has]]
        box_aspect = (boxes[:, 2] - boxes[:, 0]) * aspect / np.maximum(boxes[:, 3] - boxes[:, 1], 1e-6)
        shape[has] = np.concatenate([silhouettes - silhouettes.mean(axis=1, keepdims=True),
                                     np.log(np.clip(box_aspect, 0.1, 10))[:, None] * 0.5], axis=1)
    camera = np.nan_to_num(arrays["camera"][:, :3]) / np.array([0.08, 0.08, 0.1])
    velocity = np.nan_to_num(arrays["velocity"]) / 0.2
    motion = np.concatenate([np.clip(camera[:, :2] + 0.5 * velocity, -3, 3), np.clip(camera[:, 2:3], -3, 3),
                             np.ones((count, 1)) * 0.5], axis=1).astype(np.float32)
    color = arrays["color"].astype(np.float32).reshape(count, -1) / 255
    pose = np.zeros((count, 17 * 3), dtype=np.float32)
    main_pose = moments.main_pose()
    with_pose = main_pose >= 0
    if with_pose.any():
        xy = moments.pose_xy[main_pose[with_pose]].astype(np.float32)
        seen = (moments.pose_conf[main_pose[with_pose]] >= scoring.SEEN).astype(np.float32)
        pose[with_pose] = np.concatenate([((xy - 0.5) * seen[..., None]).reshape(-1, 34), seen * 0.5], axis=1)
    return {"layout": layout, "light": light.astype(np.float32), "lines": lines.astype(np.float32), "color": color,
            "pose": pose,
            "shape": shape, "motion": motion}


def fit_pca(samples: np.ndarray, dims: int, seed: int = 7) -> tuple[np.ndarray, np.ndarray]:
    """``(mean, components)`` for projecting a part to ``dims`` dimensions."""
    mean = samples.mean(axis=0)
    centered = samples - mean
    _, _, vt = np.linalg.svd(centered[np.random.default_rng(seed).permutation(len(centered))[:200_000]],
                             full_matrices=False)
    return mean.astype(np.float32), vt[:dims].astype(np.float32)


def project(parts: dict[str, np.ndarray], pca: dict[str, tuple[np.ndarray, np.ndarray]]) -> np.ndarray:
    """Concatenated, per-part L2-normalized projections."""
    pieces = []
    for name, dims in PARTS.items():
        values = parts[name]
        if name != "motion":
            mean, components = pca[name]
            values = (values - mean) @ components.T
        norm = np.linalg.norm(values, axis=1, keepdims=True)
        pieces.append(np.where(norm > 1e-6, values / np.maximum(norm, 1e-9), 0.0).astype(np.float32))
    return np.concatenate(pieces, axis=1)


def part_slices() -> dict[str, slice]:
    out, start = {}, 0
    for name, dims in PARTS.items():
        out[name] = slice(start, start + dims)
        start += dims
    return out


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------


def film_aspect(path: Path, content: list[float] | None) -> float:
    from pipeline.evidence.measure import display_size
    width, height = display_size(path, 640)
    x0, y0, x1, y1 = content or (0.0, 0.0, 1.0, 1.0)
    return float(width * (x1 - x0)) / float(max(height * (y1 - y0), 1e-6))


def _film_moments(arrays: dict[str, np.ndarray], aspect: float) -> scoring.Moments:
    count = len(arrays["times"])
    ptr = np.zeros(count + 1, dtype=np.int64)
    np.add.at(ptr, arrays["inst_moment"].astype(np.int64) + 1, 1)
    ptr = np.cumsum(ptr)
    from pipeline.evidence.moments import unpack_silhouettes
    pose_ptr = np.zeros(count + 1, dtype=np.int64)
    pose_moment = arrays.get("pose_moment", np.zeros(0, np.int32))
    np.add.at(pose_ptr, pose_moment.astype(np.int64) + 1, 1)
    return scoring.Moments(
        pose_ptr=np.cumsum(pose_ptr),
        pose_xy=arrays.get("pose_xy", np.zeros((0, 17, 2), np.uint16)).astype(np.float32) / 65535,
        pose_conf=arrays.get("pose_conf", np.zeros((0, 17), np.uint8)).astype(np.float32) / 255,
        gray=np.zeros((count, 1, 1), np.float32), field=np.zeros((count, 1, 1, 1), np.float32),
        color=np.zeros((count, 1, 1, 1), np.float32), brightness=np.zeros(count, np.float32),
        camera=np.zeros((count, 4), np.float32), velocity=np.zeros((count, 2), np.float32),
        aspect=np.full(count, aspect, np.float32), inst_ptr=ptr,
        boxes=arrays["inst_box"].astype(np.float32) / 65535, masks=unpack_silhouettes(arrays["inst_mask"]),
        classes=arrays["inst_class"].astype(np.int64), scores=arrays["inst_score"].astype(np.float32) / 255)


def _load_film(assets: Path, db: Any, film: Any, progress: Callable[[str], None]) -> tuple[dict, dict] | None:
    """One film's moments with the joined measurements, or ``None`` when absent or stale."""
    from pipeline.evidence import measure, moments as producer
    from pipeline.evidence.library import film_units
    loaded = producer.read(assets, film.film_id)
    if loaded is None:
        return None
    document, arrays = loaded
    units = film_units(db, film.film_id)
    if [unit["unit_id"] for unit in units] != document["data"]["units"]:
        progress(f"[moments-index] {film.title}: shots changed since its moments pass; skipped")
        return None
    measured = store.serving_artifact(assets, film.film_id, measure.PRODUCER)
    shots = ((measured or {}).get("data") or {}).get("shots") or {}
    content = document["data"].get("content_box")
    aspect = film_aspect(film.path, content)
    times, owner = arrays["times"], arrays["unit"].astype(np.int64)
    camera = np.full((len(times), 4), np.nan, dtype=np.float32)
    for index in np.unique(owner):
        rows = np.flatnonzero(owner == index)
        camera[rows] = camera_at(times[rows], (shots.get(units[int(index)]["unit_id"]) or {}).get("flow") or [])
    boxes, classes = main_boxes(arrays)
    velocity = subject_velocity(times, owner, boxes, classes)
    cuts = {index: (shots.get(unit["unit_id"]) or {}).get("cuts") or [] for index, unit in enumerate(units)}
    from pipeline.evidence import hero
    hero_document = store.serving_artifact(assets, film.film_id, hero.PRODUCER) or {}
    shown = (hero_document.get("data") or {}).get("shots") or {}
    pictures = {index: (shown.get(unit["unit_id"]) or {}).get("pictures") for index, unit in enumerate(units)}
    ok = usable(times, owner, arrays["gray"], arrays["described"].astype(bool), cuts,
                {index: spans for index, spans in pictures.items() if spans})
    arrays = {**arrays, "camera": camera, "velocity": velocity, "ok": ok}
    arrays["_moments"] = _film_moments(arrays, aspect)
    film_row = {"film_id": film.film_id, "title": film.title, "aspect": aspect, "content_box": content,
                "moments_profile": document["profile_id"], "measure_profile": (measured or {}).get("profile_id"),
                "evidence": [document["data"].get("arrays_sha256"), (measured or {}).get("created_at"),
                             hero_document.get("created_at")],
                "count": int(len(times)), "instances": int(len(arrays["inst_moment"])),
                "poses": int(len(arrays.get("pose_moment", ()))),
                "subjects": (document["data"].get("subjects") or {}).get("backend", "coco"),
                "units": [unit["unit_id"] for unit in units],
                "unit_bounds": [[float(unit["t_start"]), float(unit["t_end"])] for unit in units]}
    return film_row, arrays


def build_identity(films: list[dict[str, Any]]) -> str:
    """What a build is made of: every film's moments and measurement profiles and the evidence
    artifacts themselves (the moments arrays' digest, the measurement and hero artifacts' creation
    times). A re-described film changes the identity, so the build lands in a new directory and
    a serving API (which maps the current one) picks it up on its next request; Windows refuses
    to delete mapped files, so the live directory is never rewritten in place."""
    rows = [[f["film_id"], f["moments_profile"], f["measure_profile"], list(f.get("evidence") or [])] for f in films]
    return store.digest({"contract": CONTRACT, "films": rows})[:16]


def published(final: Path) -> str | None:
    """The identity a finished build directory claims, else ``None``."""
    try:
        return json.loads((final / "manifest.json").read_text(encoding="utf-8")).get("id")
    except (OSError, ValueError):
        return None


def total_rows(films: list[dict[str, Any]]) -> int:
    return int(sum(film["count"] for film in films))


def _coarse_rows(arrays: dict[str, Any]) -> np.ndarray:
    return np.flatnonzero((np.round(arrays["times"] * 4).astype(np.int64) % COARSE_STEP == 0) & arrays["ok"])


def build(config: Any, db: Any, *, progress: Callable[[str], None] = print) -> Path:
    """Build and publish a new index from every film with current moments evidence.

    Two streaming passes keep memory to one film at a time: the first samples
    coarse parts for PCA and counts rows, the second writes the columns.
    """
    from pipeline.evidence.library import list_films

    started = time.perf_counter()
    assets = Path(config.paths.assets_dir)
    films, samples = [], {name: [] for name in PARTS if name != "motion"}
    rng = np.random.default_rng(11)
    by_id = {film.film_id: film for film in list_films(db)}
    for film in by_id.values():
        loaded = _load_film(assets, db, film, progress)
        if loaded is None:
            continue
        film_row, arrays = loaded
        parts = coarse_parts(arrays, film_row["aspect"])
        rows = _coarse_rows(arrays)
        chosen = rows if len(rows) <= 4000 else np.sort(rng.choice(rows, 4000, replace=False))
        for name in samples:
            samples[name].append(parts[name][chosen])
        films.append(film_row)
        progress(f"[moments-index] {film.title}: {film_row['count']} moments, {int(arrays['ok'].sum())} usable")
    if not films:
        raise ValueError("No film has moments evidence yet. Run `python -m pipeline.evidence moments` first.")
    pca = {name: fit_pca(np.concatenate(values), PARTS[name]) for name, values in samples.items()}
    del samples

    scene_of = _scenes(db)
    identity = build_identity(films)
    directory = root(config)
    final = directory / identity
    if published(final) == identity:
        progress(f"[moments-index] {len(films)} films, {total_rows(films)} moments: unchanged, {identity} stays published")
        (directory / "current.json").write_text(json.dumps({"id": identity}), encoding="utf-8")
        return final
    temporary = directory / f".build-{uuid4().hex[:8]}"
    temporary.mkdir(parents=True, exist_ok=True)
    try:
        total = sum(film["count"] for film in films)
        total_instances = sum(film["instances"] for film in films)
        total_poses = sum(film["poses"] for film in films)
        columns = {
            "film": ((total,), np.int16), "unit": ((total,), np.int32), "time": ((total,), np.float64),
            "ok": ((total,), np.bool_), "gray": ((total, 18, 32), np.uint8), "field": ((total, 2, 9, 16), np.int8),
            "color": ((total, 5, 8, 3), np.uint8), "brightness": ((total,), np.float32),
            "sharpness": ((total,), np.float32), "camera": ((total, 4), np.float32), "velocity": ((total, 2), np.float32),
            "inst_ptr": ((total + 1,), np.int64), "inst_box": ((total_instances, 4), np.float32),
            "inst_mask": ((total_instances, 32), np.uint8), "inst_class": ((total_instances,), np.uint8),
            "inst_score": ((total_instances,), np.uint8),
            "pose_ptr": ((total + 1,), np.int64), "pose_xy": ((total_poses, 17, 2), np.float16),
            "pose_conf": ((total_poses, 17), np.uint8),
        }
        out = {name: np.lib.format.open_memmap(temporary / f"{name}.npy", mode="w+", dtype=dtype, shape=shape)
               for name, (shape, dtype) in columns.items()}
        out["inst_ptr"][0] = 0
        out["pose_ptr"][0] = 0
        pose_offset = 0
        coarse_index, coarse_vectors, units_table = [], [], []
        unit_offset = moment_offset = instance_offset = 0
        for film_index, film_row in enumerate(films):
            loaded = _load_film(assets, db, by_id[film_row["film_id"]], progress)
            if loaded is None or loaded[0]["count"] != film_row["count"]:
                raise RuntimeError(f"{film_row['title']}: moments changed during the index build; build again")
            _, arrays = loaded
            count, instances = film_row["count"], film_row["instances"]
            span = slice(moment_offset, moment_offset + count)
            out["film"][span] = film_index
            out["unit"][span] = arrays["unit"] + unit_offset
            out["time"][span] = arrays["times"]
            out["ok"][span] = arrays["ok"]
            for name in ("gray", "field", "color", "brightness", "sharpness", "camera", "velocity"):
                out[name][span] = arrays[name]
            per_moment = np.bincount(arrays["inst_moment"], minlength=count)
            out["inst_ptr"][moment_offset + 1:moment_offset + count + 1] = instance_offset + np.cumsum(per_moment)
            ispan = slice(instance_offset, instance_offset + instances)
            out["inst_box"][ispan] = arrays["inst_box"].astype(np.float32) / 65535
            out["inst_mask"][ispan] = arrays["inst_mask"]
            out["inst_class"][ispan] = arrays["inst_class"]
            out["inst_score"][ispan] = arrays["inst_score"]
            poses = film_row["poses"]
            per_moment_poses = np.bincount(arrays.get("pose_moment", np.zeros(0, np.int32)), minlength=count)
            out["pose_ptr"][moment_offset + 1:moment_offset + count + 1] = pose_offset + np.cumsum(per_moment_poses)
            if poses:
                pspan = slice(pose_offset, pose_offset + poses)
                out["pose_xy"][pspan] = (arrays["pose_xy"].astype(np.float32) / 65535).astype(np.float16)
                out["pose_conf"][pspan] = arrays["pose_conf"]
            pose_offset += poses
            rows = _coarse_rows(arrays)
            parts = coarse_parts(arrays, film_row["aspect"])
            coarse_index.append(rows + moment_offset)
            coarse_vectors.append(project({name: parts[name][rows] for name in PARTS}, pca).astype(np.float16))
            for unit_id, bounds in zip(film_row["units"], film_row["unit_bounds"]):
                units_table.append([unit_id, film_index, bounds[0], bounds[1], scene_of.get(unit_id)])
            unit_offset += len(film_row["units"])
            moment_offset += count
            instance_offset += instances
        _close(out)
        del out
        np.save(temporary / "coarse_index.npy", np.concatenate(coarse_index).astype(np.int64))
        np.save(temporary / "coarse.npy", np.concatenate(coarse_vectors))
        np.savez(temporary / "pca.npz", **{f"{name}_mean": value[0] for name, value in pca.items()},
                 **{f"{name}_components": value[1] for name, value in pca.items()})
        manifest = {"contract": CONTRACT, "id": identity, "created_at": datetime.now(UTC).isoformat(),
                    "moments": total, "instances": total_instances, "coarse": int(sum(len(rows) for rows in coarse_index)),
                    "parts": PARTS, "fps": 4.0,
                    "films": [{key: film[key] for key in ("film_id", "title", "aspect", "content_box", "moments_profile",
                                                          "measure_profile", "evidence", "count")} for film in films],
                    "units": units_table, "elapsed_s": round(time.perf_counter() - started, 1)}
        (temporary / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        if final.exists():
            shutil.rmtree(final)
        temporary.replace(final)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    (directory / "current.json").write_text(json.dumps({"id": identity}), encoding="utf-8")
    _prune(directory, keep={identity})
    loaded = load(config)
    if loaded is not None:
        loaded.calibration = calibrate(loaded)
        (final / "calibration.json").write_text(json.dumps(loaded.calibration), encoding="utf-8")
        progress(f"[moments-index] calibration {loaded.calibration}")
    progress(f"[moments-index] {len(films)} films, {total} moments, published {identity} "
             f"in {time.perf_counter() - started:.0f}s")
    return final


def _close(arrays: dict[str, np.ndarray]) -> None:
    """Flush and release written memory maps (Windows cannot rename a directory with open maps)."""
    import gc
    for name in list(arrays):
        arrays[name].flush()
        del arrays[name]
    gc.collect()


def _prune(directory: Path, keep: set[str]) -> None:
    """Remove older builds (a loaded index keeps its files open; Windows refuses and they stay until next build)."""
    for child in directory.iterdir():
        if child.is_dir() and child.name not in keep:
            shutil.rmtree(child, ignore_errors=True)


def _scenes(db: Any) -> dict[str, str]:
    from pipeline.index.writer import table_names
    if "shot_evidence" not in table_names(db):
        return {}
    table = db.open_table("shot_evidence").to_lance().to_table(columns=["unit_id", "scene_id"])
    return {unit: scene for unit, scene in zip(table.column("unit_id").to_pylist(), table.column("scene_id").to_pylist()) if scene}


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


@dataclass
class Index:
    """A published index, memory-mapped; coarse vectors held in memory."""

    id: str
    directory: Path
    manifest: dict[str, Any]
    columns: dict[str, np.ndarray]
    coarse: np.ndarray
    coarse_rows: np.ndarray
    pca: dict[str, tuple[np.ndarray, np.ndarray]]
    unit_ids: list[str]
    unit_film: np.ndarray
    unit_start: np.ndarray
    unit_end: np.ndarray
    unit_scene: list[str | None]
    film_ids: list[str]
    film_titles: list[str]
    film_aspect: np.ndarray
    unit_first: np.ndarray                 # first moment row of each unit (plus the total at the end)
    calibration: dict[str, list[float]] | None = None
    _unit_lookup: dict[str, int] | None = None

    def unit_index(self, unit_id: str) -> int:
        if self._unit_lookup is None:
            self._unit_lookup = {unit: index for index, unit in enumerate(self.unit_ids)}
        if unit_id not in self._unit_lookup:
            raise KeyError(f"Shot {unit_id} is not in the match index")
        return self._unit_lookup[unit_id]

    def unit_rows(self, unit: int) -> np.ndarray:
        """Moment rows of one shot (they are contiguous and time-ordered)."""
        return np.arange(self.unit_first[unit], self.unit_first[unit + 1])

    def moments(self, rows: np.ndarray) -> scoring.Moments:
        from pipeline.evidence.moments import unpack_silhouettes
        rows = np.asarray(rows, dtype=np.int64)
        c = self.columns
        starts, ends = c["inst_ptr"][rows], c["inst_ptr"][rows + 1]
        counts = ends - starts
        instance_rows = np.concatenate([np.arange(s, e) for s, e in zip(starts, ends)]).astype(np.int64) \
            if counts.sum() else np.zeros(0, np.int64)
        pose_starts, pose_ends = c["pose_ptr"][rows], c["pose_ptr"][rows + 1]
        pose_counts = pose_ends - pose_starts
        pose_rows = np.concatenate([np.arange(s, e) for s, e in zip(pose_starts, pose_ends)]).astype(np.int64) \
            if pose_counts.sum() else np.zeros(0, np.int64)
        return scoring.Moments(
            pose_ptr=np.concatenate([[0], np.cumsum(pose_counts)]).astype(np.int64),
            pose_xy=c["pose_xy"][pose_rows].astype(np.float32),
            pose_conf=c["pose_conf"][pose_rows].astype(np.float32) / 255,
            gray=c["gray"][rows].astype(np.float32) / 255,
            field=c["field"][rows].astype(np.float32) / 127,
            color=c["color"][rows].astype(np.float32) / 255,
            brightness=c["brightness"][rows].astype(np.float32),
            camera=c["camera"][rows].astype(np.float32),
            velocity=c["velocity"][rows].astype(np.float32),
            aspect=self.film_aspect[c["film"][rows]].astype(np.float32),
            inst_ptr=np.concatenate([[0], np.cumsum(counts)]).astype(np.int64),
            boxes=c["inst_box"][instance_rows].astype(np.float32),
            masks=unpack_silhouettes(c["inst_mask"][instance_rows]) if len(instance_rows) else np.zeros((0, 16, 16), bool),
            classes=c["inst_class"][instance_rows].astype(np.int64),
            scores=c["inst_score"][instance_rows].astype(np.float32) / 255)

    def query_vector(self, row: int) -> np.ndarray:
        """The coarse vector of any moment (projected on demand when it is not a coarse row)."""
        position = int(np.searchsorted(self.coarse_rows, row))
        if position < len(self.coarse_rows) and self.coarse_rows[position] == row:
            return self.coarse[position].astype(np.float32)
        film = int(self.columns["film"][row])
        moments = self.moments(np.array([row]))
        arrays = {"times": np.array([self.columns["time"][row]]),
                  "gray": self.columns["gray"][[row]], "field": self.columns["field"][[row]], "color": self.columns["color"][[row]],
                  "camera": self.columns["camera"][[row]], "velocity": self.columns["velocity"][[row]],
                  "_moments": moments}
        return project(coarse_parts(arrays, float(self.film_aspect[film])), self.pca)[0]


_LOCK = threading.Lock()
_LOADED: dict[str, Index] = {}


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def calibrate(index: "Index", *, references: int = 160, candidates: int = 400, seed: int = 3) -> dict[str, list[float]]:
    """Quantiles of every raw reward over random pairs of usable instants (p50, p90, p99, p99.9).

    Subject, eye and shape parts only count references that have a subject.
    Stored beside the index so scores mean "better than chance" for this library.
    """
    rng = np.random.default_rng(seed)
    usable_rows = np.flatnonzero(index.columns["ok"])
    values: dict[str, list[np.ndarray]] = {name: [] for name in scoring.REWARDS}
    for _ in range(references):
        row = int(rng.choice(usable_rows))
        reference = index.moments(np.array([row]))
        rows = np.sort(rng.choice(usable_rows, min(candidates, len(usable_rows)), replace=False))
        scored = scoring.score(reference, index.moments(rows), scoring.Options())
        _, main = scoring.salient(reference)
        for name in scoring.REWARDS:
            if name in ("subject", "eyes", "shape") and main[0] < 0:
                continue
            values[name].append(scored.parts[name])
    out = {}
    for name, chunks in values.items():
        joined = np.concatenate(chunks) if chunks else np.zeros(1)
        out[name] = [round(float(np.quantile(joined, q)), 4) for q in (0.5, 0.9, 0.99, 0.999)]
    return out


def current_id(config: Any) -> str | None:
    pointer = root(config) / "current.json"
    try:
        return json.loads(pointer.read_text(encoding="utf-8"))["id"]
    except (OSError, ValueError, KeyError):
        return None


def load(config: Any) -> Index | None:
    """The current published index (cached per build); ``None`` when none is built."""
    identity = current_id(config)
    if identity is None:
        return None
    with _LOCK:
        if identity in _LOADED:
            return _LOADED[identity]
        directory = root(config) / identity
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        if manifest.get("contract") != CONTRACT:
            return None
        names = ("film", "unit", "time", "ok", "gray", "field", "color", "brightness", "sharpness", "camera", "velocity",
                 "inst_ptr", "inst_box", "inst_mask", "inst_class", "inst_score", "pose_ptr", "pose_xy", "pose_conf")
        columns = {name: np.load(directory / f"{name}.npy", mmap_mode="r") for name in names}
        for name in ("film", "unit", "time", "ok", "brightness", "sharpness", "inst_ptr", "pose_ptr"):
            columns[name] = np.array(columns[name])                     # small and hot: keep in memory
        with np.load(directory / "pca.npz") as archive:
            pca = {name: (archive[f"{name}_mean"], archive[f"{name}_components"]) for name in PARTS if name != "motion"}
        units = manifest["units"]
        index = Index(
            id=identity, directory=directory, manifest=manifest, columns=columns,
            coarse=np.load(directory / "coarse.npy", mmap_mode="r"),        # float16, paged in on first search
            coarse_rows=np.load(directory / "coarse_index.npy"), pca=pca,
            unit_ids=[row[0] for row in units], unit_film=np.array([row[1] for row in units], dtype=np.int32),
            unit_start=np.array([row[2] for row in units]), unit_end=np.array([row[3] for row in units]),
            unit_scene=[row[4] for row in units],
            film_ids=[film["film_id"] for film in manifest["films"]],
            film_titles=[film["title"] for film in manifest["films"]],
            film_aspect=np.array([film["aspect"] for film in manifest["films"]], dtype=np.float64),
            unit_first=np.searchsorted(columns["unit"], np.arange(len(units) + 1), side="left"),
            calibration=_read_json(directory / "calibration.json"))
        _LOADED.clear()
        _LOADED[identity] = index
        return index
