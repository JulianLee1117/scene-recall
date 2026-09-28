"""Local two-shot previews and bounded timestamped inspection frames."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import re
import time
from dataclasses import dataclass

import av
from PIL import Image

from pipeline.ingest.probe import _content_hash
from pipeline.lab.media import (
    DISPLAY_PROFILE,
    dialogue_manifest,
    JobCancelled,
    quantized_frame_counts,
    render_from_manifest,
    resolve_film,
    validate_sources,
)
from pipeline.lab.models import Crop, ProjectDocument
from pipeline.lab.audio_mix import AUDIO_MIX_PROFILE, music_manifest


PREVIEW_PROFILE = "next-scene-shared-voice-global-frame-excerpt-v3"
SAMPLING_PROFILE = "next-scene-three-pts-display-cropped-jpeg640-v2"
MAX_OFFERS = 6


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def _source(db, film_id):
    film = resolve_film(db, film_id)
    path = Path(film["path"])
    if not path.is_file():
        raise ValueError("Source footage is unavailable on disk")
    before = path.stat()
    content = _content_hash(path)
    after = path.stat()
    if content != film_id or (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError("Source footage changed; restore the indexed source before continuing")
    fingerprint = {"film_id": film_id, "content_hash_profile": "sha256-head-tail-4MiB",
                   "content_hash": content, "size": after.st_size, "mtime_ns": after.st_mtime_ns}
    return film, path, fingerprint


def preview_manifest(proposed_document, anchor_slot_id, db, store):
    """Resolve only the adjacent pair, retaining the full timeline's frame origin."""
    doc = ProjectDocument.model_validate(proposed_document).model_dump(mode="json")
    timeline = doc.get("music_timeline")
    if not timeline:
        raise ValueError("A next-scene preview requires a music timeline")
    slots = timeline["slots"]
    index = next((index for index, slot in enumerate(slots) if slot["id"] == anchor_slot_id), None)
    if index is None or index + 1 >= len(slots):
        raise ValueError("Choose an anchor with an adjacent next shot")
    pair = slots[index:index + 2]
    if any(not slot.get("clip_id") for slot in pair):
        raise ValueError("Both adjacent shots need a scene before previewing")
    selected_ids = {slot["clip_id"] for slot in pair}
    doc = validate_sources(doc, db, store, require_media=True, clip_ids=selected_ids)
    counts = quantized_frame_counts([slot["end"] - slot["start"] for slot in slots], doc["fps"])
    frame_start = sum(counts[:index])
    pair_counts = counts[index:index + 2]
    frame_end = frame_start + sum(pair_counts)
    clips = {clip["id"]: clip for clip in doc["clips"]}
    fingerprints = {}
    for slot in pair:
        film_id = clips[slot["clip_id"]]["film_id"]
        if film_id not in fingerprints:
            _, _, fingerprints[film_id] = _source(db, film_id)
    track = store.get_track(doc["track"]["id"])
    with Path(track["path"]).open("rb") as source:
        if hashlib.file_digest(source, "sha256").hexdigest() != track["id"]:
            raise ValueError("Imported music changed; restore the original track before previewing")
    offset = frame_start / doc["fps"]
    duration = (frame_end - frame_start) / doc["fps"]
    width, height = (1280, 720) if doc["aspect_ratio"] == "16:9" else (720, 1280)
    return {
        "schema_version": 1, "profile": PREVIEW_PROFILE, "fps": doc["fps"],
        "display_profile": DISPLAY_PROFILE,
        "width": width, "height": height, "duration": duration,
        "frame_start": frame_start, "frame_end": frame_end,
        "anchor_slot_id": anchor_slot_id, "next_slot_id": pair[1]["id"],
        "source_fingerprints": fingerprints,
        "clips": [{**clips[slot["clip_id"]], "gap": False, "slot_id": slot["id"],
                   "timeline_start": slot["start"], "timeline_end": slot["end"], "frame_count": count}
                  for slot, count in zip(pair, pair_counts)],
        "music": music_manifest(doc, offset=offset, duration=duration),
        "audio_mix_profile": AUDIO_MIX_PROFILE,
        "dialogue_clips": dialogue_manifest(doc, db),
    }


def render_preview(identity, proposed_document, anchor_slot_id, config, db, store, progress, cancelled=lambda: False):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,240}", identity):
        raise ValueError("Invalid next-scene preview identity")
    if cancelled():
        raise JobCancelled("Next-scene preview cancelled")
    manifest = preview_manifest(proposed_document, anchor_slot_id, db, store)
    result = render_from_manifest(identity, manifest, config, db, store, progress, cancelled)
    return {**result, "preview_ready": True}


@dataclass
class _DisplaySample:
    time: float
    end: float
    image: Image.Image


def _sample_at(path, requested, lower, upper, crop, cancelled):
    """Decode a bounded neighborhood and normalize its displayed pixel shape.

    This is a Lab display derivation; Match Cuts retains its strict raw-frame
    profile. PTS and the next decoded PTS bound every retained frame.
    """
    start, end = max(lower, requested - .15), min(upper, requested + .15)
    deadline = time.monotonic() + 45
    best, pending = None, None
    with av.open(str(path)) as container:
        stream = container.streams.video[0]
        sar = float(stream.sample_aspect_ratio or 1)
        if not math.isfinite(sar) or sar <= 0:
            raise ValueError("Source footage has an invalid pixel aspect ratio")
        if float(stream.metadata.get("rotate", "0")) % 360:
            raise ValueError("Rotated inspection footage requires display normalization")
        container.seek(max(0, int(start / stream.time_base)), stream=stream, backward=True)

        def retain(frame, timestamp, frame_end):
            nonlocal best
            if not lower <= timestamp < frame_end <= upper + 1e-7:
                return
            if best is None or (abs(timestamp - requested), timestamp) < (abs(best[0] - requested), best[0]):
                best = timestamp, frame_end, frame

        for frame in container.decode(stream):
            if cancelled():
                raise JobCancelled("Next-scene inspection cancelled")
            if time.monotonic() > deadline:
                raise TimeoutError("Source decoding exceeded its bounded time budget")
            if frame.pts is None:
                continue
            timestamp = float(frame.pts * stream.time_base)
            if pending is not None:
                retain(pending[1], pending[0], timestamp)
                pending = None
            if timestamp >= end:
                break
            if timestamp + 1e-7 < start:
                continue
            if any(str(side.type).endswith("DISPLAYMATRIX") for side in frame.side_data):
                raise ValueError("Display-matrix inspection footage requires explicit normalization")
            pending = timestamp, frame
        else:
            # At EOF, codec-provided duration can establish the last frame end.
            if pending is not None and pending[1].duration:
                retain(pending[1], pending[0], pending[0] + float(pending[1].duration * stream.time_base))
        if best is None:
            raise ValueError("No decoded frame inside the selected inspection window")
        timestamp, frame_end, frame = best
        image = frame.to_image()
        if crop:
            width, height = image.size
            left, top = int(width * crop["x"]) // 2 * 2, int(height * crop["y"]) // 2 * 2
            crop_width, crop_height = int(width * crop["width"]) // 2 * 2, int(height * crop["height"]) // 2 * 2
            if crop_width < 2 or crop_height < 2:
                raise ValueError("Inspection crop contains too few source pixels")
            image = image.crop((left, top, left + crop_width, top + crop_height))
        display_width, display_height = image.width * sar, image.height
        factor = min(1., 640 / max(display_width, display_height))
        size = max(1, round(display_width * factor)), max(1, round(display_height * factor))
        if image.size != size:
            image = image.resize(size, Image.Resampling.LANCZOS)
        return _DisplaySample(timestamp, frame_end, image)


def _sample_window(window, config, db, sources, cancelled, deadline):

    start, end = window["source_start"], window["source_end"]
    if not all(isinstance(value, (int, float)) and math.isfinite(value) for value in (start, end)) or start < 0 or end <= start:
        raise ValueError("Inspection requires a finite positive source window")
    film_id = window["film_id"]
    if film_id not in sources:
        sources[film_id] = _source(db, film_id)
    film, path, fingerprint = sources[film_id]
    if end > float(film["duration"]) + 0.001:
        raise ValueError("Inspection window extends beyond its source film")
    crop = Crop.model_validate(window["crop"]).model_dump() if window.get("crop") else None
    identity = {"profile": SAMPLING_PROFILE, "source": fingerprint,
                "source_start": start, "source_end": end, "crop": crop}
    directory = config.paths.assets_dir / "lab" / "next-scene-frames" / _digest(identity)
    metadata = directory / "samples.json"
    if metadata.is_file():
        cached = json.loads(metadata.read_text(encoding="utf-8"))
        if cached.get("provenance") == identity and len(cached.get("frames", [])) == 3:
            valid = all(item.get("file") == f"frame-{index}.jpg" and
                        isinstance(item.get("timestamp"), (int, float)) and
                        isinstance(item.get("frame_end"), (int, float)) and
                        start <= item["timestamp"] < item["frame_end"] <= end + 1e-7 and
                        (directory / item["file"]).is_file() and
                        hashlib.sha256((directory / item["file"]).read_bytes()).hexdigest() == item.get("sha256")
                        for index, item in enumerate(cached["frames"]))
            if valid:
                return {**cached, "frames": [{**item, "path": str(directory / item["file"])} for item in cached["frames"]]}
        raise ValueError("Cached inspection frames changed; remove the derived cache before retrying")
    directory.mkdir(parents=True, exist_ok=True)
    frames = []
    for index, requested in enumerate((start, (start + end) / 2, math.nextafter(end, start))):
        if cancelled():
            raise JobCancelled("Next-scene inspection cancelled")
        if time.monotonic() > deadline:
            raise TimeoutError("Next-scene inspection exceeded its bounded time budget")
        sample = _sample_at(path, requested, start, end, crop, cancelled)
        image = sample.image
        image_path = directory / f"frame-{index}.jpg"
        image.convert("RGB").save(image_path, quality=84)
        frames.append({"file": image_path.name, "requested_time": requested,
                       "timestamp": sample.time, "frame_end": sample.end,
                       "sha256": hashlib.sha256(image_path.read_bytes()).hexdigest()})
    result = {"provenance": identity, "frames": frames}
    metadata.write_text(json.dumps(result, indent=2, allow_nan=False), encoding="utf-8")
    return {**result, "frames": [{**item, "path": str(directory / item["file"])} for item in frames]}


def sample_frames(anchor, offers, config, db, cancelled=lambda: False, progress=lambda _: None):
    """Sample three actual PTS images per window; sparse frames never verify motion."""
    if len(offers) > MAX_OFFERS:
        raise ValueError("Inspection is limited to six offered scenes")
    if cancelled():
        raise JobCancelled("Next-scene inspection cancelled")
    sources, windows, images = {}, [], []
    deadline = time.monotonic() + 120
    for index, window in enumerate([anchor, *offers]):
        role = "anchor" if index == 0 else "offer"
        identity = str(window.get("id") or window.get("unit_id") or role)
        progress(f"Sampling {role} {index if index else ''}".strip())
        sampled = _sample_window(window, config, db, sources, cancelled, deadline)
        windows.append({"id": identity, "role": role, **sampled})
        for frame in sampled["frames"]:
            images.append({"path": frame["path"],
                           "label": f"{role} {identity}; source time {frame['timestamp']:.6f}s; cropped view as offered"})
    return {"profile": SAMPLING_PROFILE, "windows": windows, "images": images,
            "limitation": "Three timestamped stills per source window. They can support appearance comparisons, not verified motion or action completion."}
