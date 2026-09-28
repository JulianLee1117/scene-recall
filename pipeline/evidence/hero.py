"""Hero frame per shot: the best still to show for a shot, picked from measured samples.

The measurement pass stores sharpness, brightness and detections for every
sampled frame (2 fps). This step scores those samples, prefers the moment the
understanding pass marked as the shot's peak, and extracts the chosen instant at
full quality. It is cheap (no GPU) and re-runs whenever either input changes.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Callable

import numpy as np

from pipeline.evidence import store
from pipeline.evidence.library import FilmRef


WIDTH = 1280
QUALITY = 82
WEBP_METHOD = 2             # 3x faster than the default with near-identical size
SEEK_AHEAD_S = 2.5          # decode forward instead of seeking when the next target is this close
PRODUCER = store.Producer(
    kind="hero",
    name="frame-pick",
    version=1,
    settings={"width": WIDTH, "quality": QUALITY, "format": "webp", "method": WEBP_METHOD,
              "score": "z(log sharpness)+0.3 people-2.0 bad exposure+peak(1.5,0.8s)|middle(0.6)+0.2 centrality-v1"},
)


def pick(record: dict[str, Any], t_start: float, t_end: float, peak: float | None) -> dict[str, Any] | None:
    """Choose one sampled instant: sharp, well exposed, with people, near the peak (else the middle)."""
    frames = record.get("frames") or []
    if not frames:
        return None
    log_sharp = np.log1p([max(0.0, float(frame[1])) for frame in frames])
    z = (log_sharp - log_sharp.mean()) / (log_sharp.std() + 1e-6)
    middle = (t_start + t_end) / 2
    half = max(0.5, (t_end - t_start) / 2)
    anchor = peak if peak is not None else middle
    best, best_score = None, -math.inf
    for position, (time_value, _sharpness, brightness, boxes) in enumerate(frames):
        score = float(z[position]) + (0.3 if any(box[0] == "person" for box in boxes) else 0.0)
        score -= 2.0 if brightness < 0.06 or brightness > 0.94 else 0.0
        score += (1.5 if peak is not None else 0.6) * math.exp(-((time_value - anchor) / 0.8) ** 2)
        score += 0.2 * (1 - min(1.0, abs(time_value - middle) / half))
        if score > best_score:
            best, best_score = time_value, score
    return {"time": round(float(best), 3), "score": round(best_score, 3), "basis": "peak" if peak is not None else "quality"}


def directory(config: Any, film_id: str, profile_id: str | None = None) -> Path:
    return Path(config.paths.assets_dir) / film_id / "evidence" / "hero" / (profile_id or PRODUCER.profile_id)


def extract(film: FilmRef, targets: dict[str, float], output: Path) -> dict[str, str]:
    """Decode each target instant and save ``<unit_id>.webp`` (atomically replacing older picks)."""
    from concurrent.futures import ThreadPoolExecutor

    import av
    from PIL import Image

    output.mkdir(parents=True, exist_ok=True)
    written: dict[str, str] = {}

    def save(unit_id: str, frame: Any, sar: float) -> None:
        image = frame.to_image().convert("RGB")
        size = (WIDTH, max(2, round(image.height * WIDTH / (image.width * sar))))
        image = image.resize(size, Image.Resampling.LANCZOS)
        destination = output / f"{unit_id}.webp"
        temporary = destination.with_name(f".tmp-{destination.name}")
        image.save(temporary, format="WEBP", quality=QUALITY, method=WEBP_METHOD)
        temporary.replace(destination)

    ordered = sorted(targets.items(), key=lambda item: item[1])
    with av.open(str(film.path)) as container, ThreadPoolExecutor(max_workers=3) as pool:
        stream = container.streams.video[0]
        stream.thread_type = "AUTO"
        sar = float(stream.sample_aspect_ratio or 1)
        origin = container.start_time / av.time_base if container.start_time is not None else 0.0
        frames = None
        position = -math.inf                      # time of the last decoded frame
        pending = None                            # decoded frame not yet consumed
        futures: dict[str, Any] = {}
        for unit_id, target in ordered:
            if frames is None or target < position or target - position > SEEK_AHEAD_S:
                container.seek(max(0, int((target - 1.0 + origin) / stream.time_base)), stream=stream, backward=True)
                frames = container.decode(stream)
                pending = None
            chosen = pending if pending is not None and pending[0] + 1e-3 >= target else None
            if chosen is None:
                for frame in frames:
                    if frame.pts is None:
                        continue
                    t = float(frame.pts * stream.time_base) - origin
                    position = t
                    if t + 1e-3 >= target:
                        chosen = (t, frame)
                        break
            if chosen is None:
                frames = None
                continue
            pending = chosen
            futures[unit_id] = pool.submit(save, unit_id, chosen[1], sar)
            in_flight = [future for future in futures.values() if not future.done()]
            if len(in_flight) > 12:            # bound decoded frames held in memory
                in_flight[0].result()
        for unit_id, future in futures.items():
            future.result()
            written[unit_id] = f"{unit_id}.webp"
    return written


def run(config: Any, db: Any, films: list[FilmRef], *, force: bool = False,
        progress: Callable[[str], None] = print) -> dict[str, int]:
    from pipeline.evidence import measure, understanding
    from pipeline.evidence.library import film_units

    counts = {"cached": 0, "done": 0, "skipped": 0, "failed": 0}
    und_producer = understanding.producer()
    for film in films:
        measured = store.read_artifact(config.paths.assets_dir, film.film_id, measure.PRODUCER)
        if measured is None:
            counts["skipped"] += 1
            continue
        story = store.read_artifact(config.paths.assets_dir, film.film_id, und_producer)
        inputs = {"measure": f"{measured['profile_id']}@{measured['created_at']}",
                  "understanding": f"{story['profile_id']}@{story['created_at']}" if story else ""}
        previous = store.read_artifact(config.paths.assets_dir, film.film_id, PRODUCER)
        if not force and previous is not None and previous.get("inputs") == inputs:
            counts["cached"] += 1
            continue
        peaks = {unit_id: record.get("peak_time") for unit_id, record in ((story or {}).get("data") or {}).get("shots", {}).items()}
        picks: dict[str, dict[str, Any]] = {}
        for unit in film_units(db, film.film_id, columns=["unit_id", "t_start", "t_end"]):
            record = measured["data"]["shots"].get(unit["unit_id"])
            if record is None:
                continue
            choice = pick(record, float(unit["t_start"]), float(unit["t_end"]), peaks.get(unit["unit_id"]))
            if choice is not None:
                picks[unit["unit_id"]] = choice
        output = directory(config, film.film_id)
        old = ((previous or {}).get("data") or {}).get("shots", {}) if not force else {}
        targets = {unit_id: choice["time"] for unit_id, choice in picks.items()
                   if (old.get(unit_id) or {}).get("time") != choice["time"] or not (output / f"{unit_id}.webp").is_file()}
        try:
            written = extract(film, targets, output)
        except Exception as exc:  # noqa: BLE001 - one film must not stop a library run
            counts["failed"] += 1
            progress(f"[hero] {film.title}: failed ({str(exc)[:300]})")
            continue
        for unit_id, choice in picks.items():
            if (output / f"{unit_id}.webp").is_file() and (unit_id in written or unit_id not in targets):
                choice["file"] = f"{unit_id}.webp"
        for stale in output.glob("*.webp"):
            if stale.stem not in picks:
                stale.unlink(missing_ok=True)
        store.write_artifact(config.paths.assets_dir, film.film_id, PRODUCER, {"shots": picks}, inputs=inputs)
        counts["done"] += 1
        progress(f"[hero] {film.title}: {len(written)} extracted, {sum('file' in c for c in picks.values())}/{len(picks)} ready")
    return counts
