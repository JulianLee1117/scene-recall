"""How a shot is shown: its focus span, hero frame and hover preview.

A shot can hold more than one picture: a dissolve or fade the shot detector
kept inside it, or a cut it missed. Comparing the shot's stored keyframe
embeddings splits it into pictures (:func:`pictures`); the focus span is the
picture holding the action peak (:func:`focus`). Thumbnails, hover previews and
the editor stay inside it, so they show what the evidence describes.

The measurement pass stores sharpness, brightness and detections for every
sampled frame (2 fps). This step scores the samples inside the focus span,
prefers the moment the understanding pass marked as the shot's peak, and
extracts the chosen instant at full quality. Where the ingest hover preview
(4 s around the shot's midpoint) strays outside the focus span, it renders a
replacement around the peak. It re-runs whenever an input changes.
"""

from __future__ import annotations

import hashlib
import math
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

import numpy as np

from pipeline.evidence import store
from pipeline.evidence.library import FilmRef


WIDTH = 1280
QUALITY = 82
WEBP_METHOD = 2             # 3x faster than the default with near-identical size
SEEK_AHEAD_S = 2.5          # decode forward instead of seeking when the next target is this close
FOCUS_SIMILARITY = 0.7      # neighbouring keyframes of one picture stay above this cosine
PREVIEW_SECONDS = 4.0       # as the ingest hover preview (pipeline.ingest.media)
PREVIEW_HEIGHT = 480
PREVIEW_SLACK_S = 0.25      # an ingest preview this close to the focus span is kept
PRODUCER = store.Producer(
    kind="hero",
    name="frame-pick",
    version=2,
    settings={"width": WIDTH, "quality": QUALITY, "format": "webp", "method": WEBP_METHOD,
              "score": "z(log sharpness)+0.3 people-2.0 bad exposure+peak(1.5,0.8s)|middle(0.6)+0.2 centrality-v1",
              "pictures": f"split at hidden cuts and where neighbouring keyframes fall below cosine {FOCUS_SIMILARITY}; "
                          "dark spans removed; focus = the picture holding the peak",
              "preview": {"seconds": PREVIEW_SECONDS, "height": PREVIEW_HEIGHT, "codec": "h264",
                          "when": "the ingest preview leaves the focus span"}},
)


def pictures(t_start: float, t_end: float, keyframes: list[tuple[float, np.ndarray]],
             cuts: list[float], dark: list[list[float]]) -> list[tuple[float, float]]:
    """The shot split into stretches that each show one picture.

    Hidden cuts split it exactly. Where two neighbouring keyframes (unit
    vectors) stop looking alike, cosine below ``FOCUS_SIMILARITY``, the picture
    changed at an unknown moment between them (a dissolve, a fade, a missed
    cut), so that gap belongs to neither side. A slow camera move drifts by
    small steps and stays one picture. Near-black stretches are removed.
    """
    bounds = [t_start, *sorted(cut for cut in cuts if t_start < cut < t_end), t_end]
    spans: list[tuple[float, float]] = []
    for low, high in zip(bounds, bounds[1:]):
        inside = sorted((time, vector) for time, vector in keyframes if low <= time <= high)
        start = low
        for (left_time, left), (right_time, right) in zip(inside, inside[1:]):
            if float(left @ right) < FOCUS_SIMILARITY:
                spans.append((start, left_time))
                start = right_time
        spans.append((start, high))
    for dark_start, dark_end in dark:
        kept = []
        for low, high in spans:
            if dark_end <= low or dark_start >= high:
                kept.append((low, high))
                continue
            if dark_start > low:
                kept.append((low, float(dark_start)))
            if dark_end < high:
                kept.append((float(dark_end), high))
        spans = kept
    return [(round(low, 3), round(high, 3)) for low, high in spans if high > low]


def focus(spans: list[tuple[float, float]], t_start: float, t_end: float, peak: float | None) -> tuple[float, float]:
    """The picture holding the action peak (else the shot's middle); the nearest one if it falls in a gap."""
    anchor = peak if peak is not None and t_start <= peak <= t_end else (t_start + t_end) / 2
    if not spans:
        return round(t_start, 3), round(t_end, 3)
    return min(spans, key=lambda span: 0.0 if span[0] <= anchor <= span[1]
               else min(abs(anchor - span[0]), abs(anchor - span[1])))


def ingest_preview_window(t_start: float, t_end: float) -> tuple[float, float]:
    """The window of the ingest hover preview: up to four seconds around the shot's midpoint."""
    length = min(PREVIEW_SECONDS, t_end - t_start)
    start = max(t_start, (t_start + t_end) / 2 - length / 2)
    return start, min(t_end, start + length)


def preview_window(t_start: float, t_end: float, span: tuple[float, float], peak: float | None) -> tuple[float, float] | None:
    """A replacement hover-preview window around the peak inside the focus span, or None if the ingest one fits."""
    ingest_start, ingest_end = ingest_preview_window(t_start, t_end)
    if ingest_start >= span[0] - PREVIEW_SLACK_S and ingest_end <= span[1] + PREVIEW_SLACK_S:
        return None
    anchor = peak if peak is not None and span[0] <= peak <= span[1] else (span[0] + span[1]) / 2
    length = min(PREVIEW_SECONDS, span[1] - span[0])
    start = min(max(anchor - length / 2, span[0]), span[1] - length)
    return round(start, 3), round(start + length, 3)


def pick(record: dict[str, Any], t_start: float, t_end: float, peak: float | None,
         span: tuple[float, float] | None = None) -> dict[str, Any] | None:
    """Choose one sampled instant inside the focus span: sharp, well exposed, with people, near the peak."""
    frames = record.get("frames") or []
    if span is not None:
        frames = [frame for frame in frames if span[0] <= float(frame[0]) <= span[1]] or frames
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


def render_previews(film: FilmRef, windows: dict[str, tuple[float, float]], output: Path) -> dict[str, str]:
    """Render silent 480p H.264 hover previews ``<unit_id>.mp4`` for the given source windows."""
    output.mkdir(parents=True, exist_ok=True)
    encoders = [["-c:v", "h264_nvenc", "-preset", "p4", "-cq", "30"],
                ["-c:v", "libx264", "-preset", "veryfast", "-crf", "28"]]
    chosen = {"encoder": 0}                     # NVENC unless it is unavailable, then x264 for the rest

    def render(unit_id: str, window: tuple[float, float]) -> str:
        destination = output / f"{unit_id}.mp4"
        temporary = destination.with_name(f".tmp-{destination.name}")
        while True:
            encoder = chosen["encoder"]
            command = ["ffmpeg", "-y", "-nostdin", "-v", "error", "-ss", f"{window[0]:.3f}", "-i", str(film.path),
                       "-t", f"{window[1] - window[0]:.3f}", "-map", "0:v:0", "-an", "-sn", "-dn",
                       "-vf", f"scale=w='max(2,trunc(iw*sar/2)*2)':h=ih,setsar=1,scale=-2:{PREVIEW_HEIGHT}",
                       *encoders[encoder], "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-f", "mp4", str(temporary)]
            result = subprocess.run(command, capture_output=True)
            if result.returncode == 0:
                break
            temporary.unlink(missing_ok=True)
            if encoder + 1 >= len(encoders):
                raise RuntimeError(result.stderr.decode(errors="replace")[-300:])
            chosen["encoder"] = max(chosen["encoder"], encoder + 1)
        temporary.replace(destination)
        return unit_id

    with ThreadPoolExecutor(max_workers=3) as pool:
        return {unit_id: f"{unit_id}.mp4" for unit_id in pool.map(lambda item: render(*item), windows.items())}


def _keyframes(db: Any, film_id: str) -> dict[str, list[tuple[float, np.ndarray]]]:
    """Each unit's stored keyframe embeddings as unit vectors, by timestamp."""
    from lancedb.expr import col, lit

    from pipeline.index.reads import iter_filtered_rows

    keyframes: dict[str, list[tuple[float, np.ndarray]]] = {}
    for row in iter_filtered_rows(db.open_table("frames"), columns=["unit_id", "timestamp", "visual_vec"],
                                  where=col("film_id") == lit(film_id), batch_size=1024):
        vector = np.asarray(row["visual_vec"], dtype=np.float32)
        norm = float(np.linalg.norm(vector))
        if norm > 0 and row.get("timestamp") is not None:
            keyframes.setdefault(str(row["unit_id"]), []).append((float(row["timestamp"]), vector / norm))
    return keyframes


def _fingerprint(keyframes: dict[str, list[tuple[float, np.ndarray]]]) -> str:
    """Identity of a film's keyframe set (it changes only when the film is re-indexed)."""
    listing = sorted((unit_id, round(time, 3)) for unit_id, frames in keyframes.items() for time, _ in frames)
    return hashlib.sha1(repr(listing).encode()).hexdigest()[:16]


def _reuse(source: Path, destination: Path) -> None:
    """Carry an unchanged pick into this profile: a hard link costs no space, a copy works anywhere."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.unlink(missing_ok=True)
    try:
        destination.hardlink_to(source)
    except OSError:
        shutil.copy2(source, destination)


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
        keyframes = _keyframes(db, film.film_id)
        inputs = {"measure": f"{measured['profile_id']}@{measured['created_at']}",
                  "understanding": f"{story['profile_id']}@{story['created_at']}" if story else "",
                  "keyframes": _fingerprint(keyframes)}
        previous = store.read_artifact(config.paths.assets_dir, film.film_id, PRODUCER)
        if not force and previous is not None and previous.get("inputs") == inputs:
            counts["cached"] += 1
            continue
        peaks = {unit_id: record.get("peak_time") for unit_id, record in ((story or {}).get("data") or {}).get("shots", {}).items()}
        picks: dict[str, dict[str, Any]] = {}
        for unit in film_units(db, film.film_id, columns=["unit_id", "t_start", "t_end"]):
            unit_id = unit["unit_id"]
            record = measured["data"]["shots"].get(unit_id)
            if record is None:
                continue
            t_start, t_end, peak = float(unit["t_start"]), float(unit["t_end"]), peaks.get(unit_id)
            spans = pictures(t_start, t_end, keyframes.get(unit_id, []), record.get("cuts") or [],
                             measure.dark_spans(record.get("frames")))
            span = focus(spans, t_start, t_end, peak)
            choice = pick(record, t_start, t_end, peak, span)
            if choice is None:
                continue
            choice["focus"] = list(span)
            if len(spans) > 1:
                choice["pictures"] = [list(item) for item in spans]
            window = preview_window(t_start, t_end, span, peak)
            if window is not None:
                choice["preview"] = {"start": window[0], "end": window[1]}
            picks[unit_id] = choice
        output = directory(config, film.film_id)
        old = ((previous or {}).get("data") or {}).get("shots", {}) if not force else {}
        earlier_dir, earlier = None, {}
        if previous is None:                    # a new profile: carry unchanged picks over from the one serving
            served = store.serving_artifact(config.paths.assets_dir, film.film_id, PRODUCER)
            if served is not None and served.get("profile_id") != PRODUCER.profile_id:
                earlier_dir = directory(config, film.film_id, served["profile_id"])
                earlier = ((served.get("data") or {}).get("shots") or {})
        targets, windows = {}, {}
        for unit_id, choice in picks.items():
            image = output / f"{unit_id}.webp"
            if (old.get(unit_id) or {}).get("time") == choice["time"] and image.is_file():
                pass
            elif (earlier_dir is not None and (earlier.get(unit_id) or {}).get("time") == choice["time"]
                  and (earlier_dir / image.name).is_file()):
                _reuse(earlier_dir / image.name, image)
            else:
                targets[unit_id] = choice["time"]
            wanted = choice.get("preview")
            kept = (old.get(unit_id) or {}).get("preview") or {}
            if wanted and not ((kept.get("start"), kept.get("end")) == (wanted["start"], wanted["end"])
                               and (output / f"{unit_id}.mp4").is_file()):
                windows[unit_id] = (wanted["start"], wanted["end"])
        try:
            written = extract(film, targets, output)
            rendered = render_previews(film, windows, output) if windows else {}
        except Exception as exc:  # noqa: BLE001 - one film must not stop a library run
            counts["failed"] += 1
            progress(f"[hero] {film.title}: failed ({str(exc)[:300]})")
            continue
        for unit_id, choice in picks.items():
            if (output / f"{unit_id}.webp").is_file() and (unit_id in written or unit_id not in targets):
                choice["file"] = f"{unit_id}.webp"
            if choice.get("preview") and (output / f"{unit_id}.mp4").is_file() and (unit_id in rendered or unit_id not in windows):
                choice["preview"]["file"] = f"{unit_id}.mp4"
        for stale in [*output.glob("*.webp"), *output.glob("*.mp4")]:
            if stale.stem not in picks or (stale.suffix == ".mp4" and "preview" not in picks[stale.stem]):
                stale.unlink(missing_ok=True)
        store.write_artifact(config.paths.assets_dir, film.film_id, PRODUCER, {"shots": picks}, inputs=inputs)
        counts["done"] += 1
        progress(f"[hero] {film.title}: {len(written)} extracted, {len(rendered)} previews, "
                 f"{sum('file' in c for c in picks.values())}/{len(picks)} ready")
    return counts
