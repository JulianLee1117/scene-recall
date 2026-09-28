"""Source-resolved media imports and deterministic, decoded-frame reel rendering."""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import stat
import subprocess
from pathlib import Path
from typing import Callable

from lancedb.expr import col, lit

from pipeline.index.writer import table_names
from pipeline.lab.models import ProjectDocument
from pipeline.lab.audio_mix import AUDIO_MIX_PROFILE, audio_filter_graph, music_manifest


DISPLAY_PROFILE = "square-pixel-display-aspect-fit-v1"
MATCH_BOUNDARY_PROFILE = "decoded-match-absolute-pts-boundaries-v1"
_LOG = logging.getLogger(__name__)


class JobCancelled(RuntimeError):
    pass


def run_process(arguments, *, cancelled=lambda: False, timeout=600):
    """Bounded subprocess with cancellation and no shell or visible Windows window."""
    kwargs = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    process = subprocess.Popen(arguments, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kwargs)
    import time
    deadline = time.monotonic() + timeout
    try:
        while True:
            if cancelled():
                raise JobCancelled("Job cancelled")
            if time.monotonic() >= deadline:
                raise RuntimeError(f"Media command timed out after {timeout} seconds")
            try:
                out, err = process.communicate(timeout=0.25)
                break
            except subprocess.TimeoutExpired:
                continue
        if process.returncode:
            raise ValueError(f"Media command failed: {err.decode('utf-8', errors='replace')[-2500:]}")
        return out
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()


def probe_media(path: Path):
    raw = run_process(["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)], timeout=30)
    return json.loads(raw)


def import_track(store, source: Path, name: str):
    probe = probe_media(source)
    audio = next((stream for stream in probe.get("streams", []) if stream.get("codec_type") == "audio"), None)
    if audio is None:
        raise ValueError("The imported file must contain audio")
    duration = float(probe.get("format", {}).get("duration") or audio.get("duration") or 0)
    if not math.isfinite(duration) or not 0 < duration <= 4 * 60 * 60:
        raise ValueError("Audio must have a readable duration of at most four hours")
    with source.open("rb") as evidence:
        digest = hashlib.file_digest(evidence, "sha256").hexdigest()
    suffix = Path(name).suffix.lower()
    if suffix not in {".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".opus", ".mp4", ".webm", ".aiff"}:
        suffix = ".audio"
    destination = store.root / "tracks" / f"{digest}{suffix}"
    # Content identity makes duplicate uploads safe; never overwrite evidence.
    try:
        existing = store.get_track(digest)
    except KeyError:
        existing = None
    if existing and Path(existing["path"]).is_file():
        with Path(existing["path"]).open("rb") as evidence:
            if hashlib.file_digest(evidence, "sha256").hexdigest() == digest:
                return existing
        raise ValueError("An imported track with this identity changed on disk; preserve it and restore the original source before retrying")
    if existing:
        # Restore the exact registered source path after a missing-file reimport.
        destination = Path(existing["path"])
    if not destination.exists():
        source.replace(destination)
    return store.add_track(digest, Path(name).name[:200] or "Imported track", duration, destination)


def resolve_film(db, film_id):
    if "films" not in table_names(db):
        raise ValueError("No indexed source films are available")
    rows = db.open_table("films").search().where(col("film_id") == lit(film_id)).limit(1).to_list()
    if not rows or rows[0].get("film_id") != film_id:
        raise ValueError(f"Source film {film_id} is unavailable")
    return dict(rows[0])


def validate_sources(document, db, store, *, require_media=False, clip_ids=None, dialogue_ids=None):
    """Unit IDs remain hints; durable film/time anchors are authoritative."""
    doc = ProjectDocument.model_validate(document).model_dump(mode="json")
    if doc["track"]:
        track = store.get_track(doc["track"]["id"])
        if abs(track["duration"] - doc["track"]["duration"]) > 0.001:
            raise ValueError("Track duration differs from the imported source")
        if require_media and not Path(track["path"]).is_file():
            raise ValueError("Imported music is unavailable on disk")
    films = {}
    sources = [(clip, clip_ids) for clip in doc["clips"]]
    sources += [(clip, dialogue_ids) for clip in doc["dialogue_clips"]]
    for clip, selected_ids in sources:
        if selected_ids is not None and clip["id"] not in selected_ids:
            continue
        film = films.setdefault(clip["film_id"], None)
        if film is None:
            film = films[clip["film_id"]] = resolve_film(db, clip["film_id"])
        if clip["source_end"] > float(film["duration"]) + 0.001:
            raise ValueError(f"Clip {clip['id']} extends beyond its source film")
        if require_media and not Path(film["path"]).is_file():
            raise ValueError(f"Source film {film.get('title', clip['film_id'])} is unavailable on disk")
    return doc


def dialogue_manifest(document, db):
    """Resolve original audio by indexed identity, never a document-supplied path."""
    from pipeline.lab.dialogue_assets import describe_clip, source_info

    sources, clips = {}, []
    for clip in document.get("dialogue_clips", []):
        film_id = clip["film_id"]
        if film_id not in sources:
            sources[film_id] = source_info(db, film_id)
        clips.append({**clip, **describe_clip(clip, sources[film_id])})
    return clips


def _check_dialogue_fingerprints(clips, db):
    checked = set()
    for clip in clips:
        if clip["film_id"] in checked:
            continue
        checked.add(clip["film_id"])
        source = Path(resolve_film(db, clip["film_id"])["path"])
        current = source.stat()
        expected = clip["source_fingerprint"]
        if (current.st_size, current.st_mtime_ns) != (expected["size"], expected["mtime_ns"]):
            raise ValueError("Dialogue source changed after the render was prepared")
        if "inode" in expected and (current.st_ino, current.st_dev) != (expected["inode"], expected["device"]):
            raise ValueError("Dialogue source changed after the render was prepared")


def quantized_frame_counts(durations, fps):
    """Quantize cumulative boundaries from the original reel frame origin."""
    elapsed, previous, counts = 0.0, 0, []
    for duration in durations:
        elapsed += duration
        boundary = round(elapsed * fps)
        counts.append(boundary - previous)
        previous = boundary
    if any(count < 1 for count in counts):
        raise ValueError("Each clip must contain at least one output frame")
    return counts


def render_manifest(document, db, store, *, mode="preview", experiment_id="music-sketch"):
    timeline = document.get("music_timeline") if experiment_id == "music-sketch" else None
    selected_ids = {slot["clip_id"] for slot in timeline["slots"] if slot.get("clip_id")} if timeline else None
    doc = validate_sources(document, db, store, require_media=True, clip_ids=selected_ids)
    if experiment_id != "music-sketch" and doc["dialogue_clips"]:
        raise ValueError("Dialogue clips belong to AI Music Video")
    if not doc["clips"] and not timeline:
        raise ValueError("Add clips before rendering")
    fps = doc["fps"]
    render_clips = doc["clips"]
    if timeline:
        if mode == "export" and any(not slot.get("clip_id") for slot in timeline["slots"]):
            raise ValueError("Fill every music slot before exporting; preview keeps empty slots as black gaps")
        by_id = {clip["id"]: clip for clip in doc["clips"]}
        render_clips = []
        for slot in timeline["slots"]:
            clip = by_id.get(slot.get("clip_id"))
            entry = dict(clip) if clip else {"id": slot["id"], "source_start": 0,
                                            "source_end": slot["end"] - slot["start"], "crop": None}
            render_clips.append({**entry, "gap": clip is None, "slot_id": slot["id"],
                                 "timeline_start": slot["start"], "timeline_end": slot["end"]})
        durations = [slot["end"] - slot["start"] for slot in timeline["slots"]]
    else:
        durations = [clip["source_end"] - clip["source_start"] for clip in render_clips]
    counts = quantized_frame_counts(durations, fps)
    elapsed = sum(durations)
    track = None
    if experiment_id == "music-sketch":
        if not doc["track"]:
            raise ValueError("Import a music track before rendering AI Music Video")
        passage_duration = doc["passage"]["end"] - doc["passage"]["start"]
        if abs(elapsed - passage_duration) > 1 / fps + 0.000001:
            raise ValueError(f"Clips total {elapsed:.2f}s; adjust them to fill the {passage_duration:.2f}s music passage")
        track = store.get_track(doc["track"]["id"])
    landscape = (1280, 720) if mode == "preview" else (1920, 1080)
    width, height = landscape if doc["aspect_ratio"] == "16:9" else landscape[::-1]
    return {
        "schema_version": 1, "profile": MATCH_BOUNDARY_PROFILE if experiment_id == "visual-rhymes" else "decoded-reel-shared-voice-mix-v6", "fps": fps,
        "display_profile": DISPLAY_PROFILE,
        "width": width, "height": height, "duration": sum(counts) / fps,
        "clips": [{**clip, "frame_count": count} for clip, count in zip(render_clips, counts)],
        "music": music_manifest(doc) if track else None,
        "audio_mix_profile": AUDIO_MIX_PROFILE if track else None,
        "dialogue_clips": dialogue_manifest(doc, db) if track else [],
    }


def render_reel(job, config, db, store, progress: Callable, cancelled=lambda: False):
    snapshot = job["snapshot"]
    manifest = render_manifest(snapshot["document"], db, store, mode=snapshot["mode"], experiment_id=snapshot["experiment_id"])
    result = render_from_manifest(job["id"], manifest, config, db, store, progress, cancelled)
    return {**result, "output_url": f"/lab/jobs/{job['id']}/output"}


def render_from_manifest(identity, manifest, config, db, store, progress: Callable, cancelled=lambda: False):
    """Shared decoded renderer for a complete reel or a frame-aligned excerpt."""
    if not isinstance(identity, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", identity) is None:
        raise ValueError("Invalid render identity")
    directory = (config.paths.assets_dir / "lab" / "renders" / identity).absolute()
    intermediates = [directory / f"clip-{index:03}.mp4" for index in range(len(manifest["clips"]))]
    intermediates += [directory / "clips.txt", directory / "audio-filters.txt", directory / "output.partial.mp4"]
    _check_render_directory(directory)
    for path in [*intermediates, directory / "manifest.json", directory / "dialogue-assets.json", directory / "output.mp4"]:
        _check_render_file(path)
    try:
        return _render_in_directory(directory, manifest, db, store, progress, cancelled, config)
    finally:
        # run_process kills and waits for its subprocess before returning or
        # raising, so no encoder can still be writing these known temporary files.
        _cleanup_render_intermediates(directory, intermediates)


def _check_render_directory(directory):
    for path in [*reversed(directory.parents), directory]:
        try:
            info = path.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise ValueError(f"Render directory contains a link: {path}")
        if not stat.S_ISDIR(info.st_mode):
            raise ValueError(f"Render directory is not a directory: {path}")


def _check_render_file(path):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    if (not stat.S_ISREG(info.st_mode)
            or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
        raise ValueError(f"Render artifact is not an ordinary file: {path}")


def _cleanup_render_intermediates(directory, paths):
    try:
        _check_render_directory(directory)
        resolved = directory.resolve(strict=True)
    except FileNotFoundError:
        return
    except Exception:
        _LOG.warning("Could not safely clean render intermediates in %s", directory, exc_info=True)
        return
    for path in paths:
        try:
            _check_render_file(path)
            if path.parent != directory or path.resolve().parent != resolved:
                raise ValueError("Render intermediate escaped its job directory")
            path.unlink(missing_ok=True)
        except Exception:
            # Retain a locked or suspicious file for later maintenance, without
            # replacing an encoder failure/cancellation or rejecting a good output.
            _LOG.warning("Could not remove render intermediate %s", path, exc_info=True)


def _render_in_directory(directory, manifest, db, store, progress, cancelled, config):
    _check_dialogue_fingerprints(manifest.get("dialogue_clips", []), db)
    from pipeline.lab.dialogue_assets import prepare_dialogue_audio, audio_asset_path
    dialogue_assets = []
    for clip in manifest.get("dialogue_clips", []):
        progress("Preparing shared source dialogue audio")
        dialogue_assets.append(prepare_dialogue_audio(config, db, clip, cancelled=cancelled))
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    fps, width, height = manifest["fps"], manifest["width"], manifest["height"]
    match_boundaries = manifest.get("profile") == MATCH_BOUNDARY_PROFILE
    for index, clip in enumerate(manifest["clips"]):
        if clip.get("gap"):
            progress(f"Keeping empty slot {index + 1} at its music position")
            run_process([
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-f", "lavfi",
                "-i", f"color=c=black:s={width}x{height}:r={fps}", "-an", "-frames:v", str(clip["frame_count"]),
                "-c:v", "libx264", "-preset", "fast", "-crf", "20", "-pix_fmt", "yuv420p",
                "-video_track_timescale", "24000", str(directory / f"clip-{index:03}.mp4"),
            ], cancelled=cancelled)
            continue
        progress(f"Rendering clip {index + 1} of {len(manifest['clips'])}")
        film = resolve_film(db, clip["film_id"])
        filters = ["setpts=PTS-STARTPTS"]
        # Match Cuts stores actual decoded PTS. Resampling toward the outgoing
        # end and incoming start preserves the selected boundary frame even
        # when 50/60 fps footage becomes 24 fps. Original source timing stays
        # unchanged. Legacy/music manifests retain their existing sampling.
        fps_filter = f"fps={fps}:round={'down' if index == 0 else 'up'}" if match_boundaries else f"fps={fps}"
        crop = clip["crop"]
        if crop:
            filters.append(f"crop=trunc(iw*{crop['width']}/2)*2:trunc(ih*{crop['height']}/2)*2:iw*{crop['x']}:ih*{crop['y']}")
        filters.extend([
            # Fit the display aspect ratio, not the encoded pixel dimensions.
            # Setting SAR alone after an ordinary fit distorts anamorphic film.
            f"scale=w='max(2,trunc(min({width},{height}*dar)/2)*2)':h='max(2,trunc(min({height},{width}/dar)/2)*2)'",
            "setsar=1", f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black", fps_filter,
            "tpad=stop_mode=clone:stop_duration=0.05",
        ])
        run_process([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
            # Limit source decoding, not the quantized output: an output-side
            # -t can discard the final frame required by a cumulative boundary.
            # Ordinary -ss is relative to a container's start_time; matches
            # use decoded absolute PTS, including sources with nonzero starts.
            *(["-seek_timestamp", "1"] if match_boundaries else []),
            "-ss", str(clip["source_start"]), "-t", str(clip["source_end"] - clip["source_start"]),
            "-i", str(film["path"]), "-map", "0:v:0", "-an",
            "-vf", ",".join(filters), "-frames:v", str(clip["frame_count"]),
            "-c:v", "libx264", "-preset", "fast", "-crf", "20", "-pix_fmt", "yuv420p",
            "-video_track_timescale", "24000", str(directory / f"clip-{index:03}.mp4"),
        ], cancelled=cancelled)
    progress("Assembling music and picture")
    concat = directory / "clips.txt"
    concat.write_text("\n".join(f"file 'clip-{index:03}.mp4'" for index in range(len(manifest["clips"]))) + "\n", encoding="utf-8")
    temporary = directory / "output.partial.mp4"
    args = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-f", "concat", "-safe", "1", "-i", str(concat)]
    music = manifest["music"]
    if music:
        track = store.get_track(music["track_id"])
        passage_start = music.get("envelope_start", music["start"])
        args += ["-ss", str(passage_start), "-t", str(music.get("envelope_end", music["end"]) - passage_start), "-i", track["path"]]
        for asset in dialogue_assets:
            args += ["-i", str(audio_asset_path(config, asset["asset_id"]))]
        # A script avoids Windows command-line limits for 32 independent clips.
        graph = directory / "audio-filters.txt"
        graph.write_text(audio_filter_graph(manifest), encoding="utf-8")
        args += ["-filter_complex_script", str(graph), "-map", "0:v:0", "-map", "[mixed]", "-c:a", "aac", "-b:a", "192k"]
    else:
        args += ["-map", "0:v:0", "-an"]
    args += ["-c:v", "copy", "-t", str(manifest["duration"]), "-movflags", "+faststart", str(temporary)]
    run_process(args, cancelled=cancelled)
    _check_dialogue_fingerprints(manifest.get("dialogue_clips", []), db)
    actual = probe_media(temporary)
    video = next(stream for stream in actual["streams"] if stream["codec_type"] == "video")
    if int(video.get("nb_frames") or 0) != sum(clip["frame_count"] for clip in manifest["clips"]):
        raise ValueError("Rendered frame count did not match the editable reel")
    if abs(float(video["duration"]) - manifest["duration"]) > 1 / fps + 0.001:
        raise ValueError("Rendered duration did not match the editable reel")
    output = directory / "output.mp4"
    if dialogue_assets:
        (directory / "dialogue-assets.json").write_text(json.dumps(dialogue_assets, indent=2), encoding="utf-8")
    temporary.replace(output)
    return {"manifest": manifest}
