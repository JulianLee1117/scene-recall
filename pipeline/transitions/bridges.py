"""Durable, explicitly imported transition media and source-backed seam auditions.

Imported provenance is user supplied, never a verified provider generation receipt.
Only this module writes bridge inputs; requests cannot supply filesystem paths.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
import time
import uuid

from pydantic import Field, model_validator

from pipeline.lab.media import (
    JobCancelled, _check_render_directory, _check_render_file, run_process,
    _cleanup_render_intermediates,
)
from pipeline.transitions import jobs
from pipeline.transitions.contracts import FPS, StrictModel, frame_count
from pipeline.transitions.storage import Guard
from pipeline.transitions.encoding import ffmpeg_args, record as encoding_record

BRIDGE_VERSION = "imported-transition-bridge-v2"
MAX_UPLOAD_BYTES = 128 * 1024 * 1024


class BridgeProvenance(StrictModel):
    provider: str | None = Field(default=None, max_length=100)
    model: str | None = Field(default=None, max_length=200)
    prompt: str | None = Field(default=None, max_length=20000)
    seed: int | None = Field(default=None, ge=0, le=4294967295)


class BridgeImportRequest(StrictModel):
    parent_render_id: str
    trim_start: float = Field(default=0, ge=0, lt=30)
    trim_end: float | None = Field(default=None, gt=0, le=30)
    playback_duration: float | None = Field(default=None, ge=.08, le=30)
    provenance: BridgeProvenance = Field(default_factory=BridgeProvenance)

    @model_validator(mode="after")
    def bounded(self):
        if str(uuid.UUID(self.parent_render_id)) != self.parent_render_id:
            raise ValueError("Invalid parent render identity")
        if self.trim_end is not None and self.trim_end - self.trim_start < .08 - 1e-8:
            raise ValueError("Select at least 0.08 seconds of bridge video")
        return self


def _write_json(path, value):
    temporary = path.with_suffix(path.suffix + ".partial")
    with temporary.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, allow_nan=False)
    temporary.replace(path)


def _probe(path):
    # A uploaded container must not resolve remote resources or nested protocols.
    return json.loads(run_process([
        "ffprobe", "-v", "error", "-protocol_whitelist", "file,pipe",
        "-show_format", "-show_streams", "-of", "json", str(path),
    ], timeout=30))


def _media_info(path):
    probe = _probe(path)
    stream = next((row for row in probe.get("streams", []) if row.get("codec_type") == "video"), None)
    if stream is None:
        raise ValueError("The bridge file must contain video")
    duration = float(probe.get("format", {}).get("duration") or stream.get("duration") or 0)
    width, height = int(stream.get("width", 0)), int(stream.get("height", 0))
    if not math.isfinite(duration) or not .08 <= duration <= 30.05:
        raise ValueError("The imported bridge must have a readable duration between 0.08 and 30 seconds")
    if not 16 <= width <= 4096 or not 16 <= height <= 4096 or width * height > 9_000_000:
        raise ValueError("The imported bridge must be at most 4096 pixels per side and 9 megapixels")
    return {"duration": duration, "width": width, "height": height,
            "codec": stream.get("codec_name"), "has_audio": any(
                row.get("codec_type") == "audio" for row in probe.get("streams", []))}


def _check_source_timing(request):
    if request.get("retime", {}).get("mode", "off") != "off":
        raise ValueError("Render a version with Speed off before importing or generating an AI bridge")


def _check_parent(parent, config, db):
    if parent.get("kind") != "transition-render":
        raise ValueError("Choose a completed local transition render as the source pair")
    proposal = parent["snapshot"]["transition_render"]
    _check_source_timing(proposal["request"])
    manifest_path = jobs.artifact(config, parent, "manifest")
    for kind in ("a", "b"):
        jobs.artifact(config, parent, kind)
    current = jobs.freeze(proposal["request"], db)
    if current["sources"] != proposal["sources"]:
        raise ValueError("The source pair changed since the parent render")
    return current, json.loads(manifest_path.read_text(encoding="utf-8"))


def prepare_import(body, stream, filename, config, db, store):
    """Copy a bounded upload, then return a private snapshot to enqueue at job_id.

    Fresh UUID roots retain the asset ledger's orphan grace until enqueue.
    A failed import removes only files created by this call. The input remains
    beside the live UUID job and is then protected by the normal render ledger.
    """
    body = BridgeImportRequest.model_validate(body).model_dump(mode="json")
    parent = store.get_job(body["parent_render_id"], private=True)
    source, parent_manifest = _check_parent(parent, config, db)
    identity = str(uuid.uuid4())
    root = jobs.output_root(config, identity)
    _check_render_directory(root)
    root.mkdir(parents=True, exist_ok=False)
    storage_check = Guard(root, kind="transition-bridge")
    temporary, original = root / "original.partial", root / "original.media"
    created = [temporary, original, root / "input.json", root / "input.json.partial",
               root / "parent-manifest.json", root / "frame-a.jpg", root / "frame-b.jpg"]
    try:
        storage_check()
        size = 0
        deadline = time.monotonic() + 120
        with temporary.open("xb") as output:
            while chunk := stream.read(1024 * 1024):
                storage_check()
                if time.monotonic() > deadline:
                    raise ValueError("Bridge upload exceeded its two-minute copy budget")
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise ValueError("Bridge uploads are limited to 128 MiB")
                output.write(chunk)
        with temporary.open("rb") as input_stream:
            header = input_stream.read(16)
        if not (header.startswith(b"\x1aE\xdf\xa3") or header[4:8] == b"ftyp"):
            raise ValueError("Import an MP4, MOV, or WebM video container")
        media = _media_info(temporary)
        end = body["trim_end"] if body["trim_end"] is not None else min(30., media["duration"])
        if end > media["duration"] + 1e-6 or end - body["trim_start"] < .08 - 1e-8:
            raise ValueError("The selected bridge trim falls outside the imported video")
        body["trim_end"] = end
        body["playback_duration"] = body["playback_duration"] or end - body["trim_start"]
        temporary.replace(original)
        for side in ("a", "b"):
            (root / f"frame-{side}.jpg").write_bytes(jobs.artifact(config, parent, side).read_bytes())
        (root / "parent-manifest.json").write_bytes(jobs.artifact(config, parent, "manifest").read_bytes())
        receipt = {"schema_version": 1, "job_id": identity, "bridge_version": BRIDGE_VERSION,
                   "name": str(filename or "Imported bridge").replace("\\", "/").rsplit("/", 1)[-1][:200],
                   "sha256": jobs.sha256(original), "size": size, "media": media,
                   "provenance_status": "user-supplied-unverified", "request": body,
                   "parent_manifest_sha256": parent["result"]["manifest_sha256"],
                   "frame_a_sha256": jobs.sha256(root / "frame-a.jpg"),
                   "frame_b_sha256": jobs.sha256(root / "frame-b.jpg"),
                   "source_frame_times": parent_manifest["source_frame_times"]}
        _write_json(root / "input.json", receipt)
        return {"job_id": identity, "bridge_version": BRIDGE_VERSION, "request": body,
                "source_snapshot": source, "input": receipt,
                "input_manifest_sha256": jobs.sha256(root / "input.json")}
    except BaseException:
        _check_render_directory(root)
        for path in created:
            _check_render_file(path)
            path.unlink(missing_ok=True)
        root.rmdir()
        raise


def _input(config, proposal):
    root = jobs.output_root(config, proposal["job_id"])
    _check_render_directory(root)
    for name in ("original.media", "input.json", "parent-manifest.json", "frame-a.jpg", "frame-b.jpg"):
        _check_render_file(root / name)
    if (jobs.sha256(root / "input.json") != proposal["input_manifest_sha256"]
            or json.loads((root / "input.json").read_text(encoding="utf-8")) != proposal["input"]
            or jobs.sha256(root / "original.media") != proposal["input"]["sha256"]):
        raise ValueError("The imported bridge or its receipt changed")
    if proposal["input"]["request"] != proposal["request"]:
        raise ValueError("The bridge input does not belong to this request")
    for name, digest in (("parent-manifest.json", "parent_manifest_sha256"),
                         ("frame-a.jpg", "frame_a_sha256"), ("frame-b.jpg", "frame_b_sha256")):
        if jobs.sha256(root / name) != proposal["input"][digest]:
            raise ValueError("The frozen source endpoint or parent receipt changed")
    return root


def _verify(proposal, config, db, store):
    if proposal["bridge_version"] != BRIDGE_VERSION:
        raise ValueError("The bridge renderer changed; import a fresh variant")
    _check_source_timing(proposal["source_snapshot"]["request"])
    root = _input(config, proposal)
    source = jobs.freeze(proposal["source_snapshot"]["request"], db)
    if source["sources"] != proposal["source_snapshot"]["sources"]:
        raise ValueError("The source pair changed")
    return root


def _filter(spatial, duration, count, speed=1):
    return (f"trim=duration={duration:.9f},setpts=(PTS-STARTPTS)*{speed:.12f},fps={FPS},"
            f"{spatial},format=yuv420p,"
            f"tpad=stop_mode=clone:stop_duration=1,trim=end_frame={count},settb=1/{FPS}")


def _bridge_framing(width, height, outgoing, incoming, stream):
    """Correct small native-grid differences only for an explicitly filled pair.

    H3 can return a 1344x768 image for a 16:9 pair. Fitting that image adds
    transient sidebars at both joins. A centered crop is bounded to a 2% aspect
    difference; wider mismatches and ambiguous display geometry keep Fit.
    """
    threshold = .02
    sar = stream.get("sample_aspect_ratio")
    assumed_square = sar in (None, "", "N/A")
    square = assumed_square
    if not assumed_square:
        try:
            numerator, denominator = (int(value) for value in str(sar).split(":"))
            square = numerator == denominator and numerator > 0
        except (ValueError, TypeError):
            square = False
    rotations = []
    if "rotate" in stream.get("tags", {}):
        rotations.append(stream["tags"]["rotate"])
    for item in stream.get("side_data_list", []):
        if "rotation" in item or item.get("side_data_type") == "Display Matrix":
            rotations.append(item.get("rotation"))
    try:
        unrotated = all(math.isfinite(float(value)) and float(value) % 360 == 0 for value in rotations)
    except (TypeError, ValueError):
        unrotated = False
    aspect = int(stream["width"]) / int(stream["height"])
    target = width / height
    mismatch = max(aspect / target, target / aspect) - 1
    both_fill = all(side.get("framing", {}).get("fit") == "fill" for side in (outgoing, incoming))
    reason = ("source-pair-does-not-both-fill" if not both_fill else
              "non-square-or-invalid-sample-aspect" if not square else
              "rotated-or-ambiguous-display-geometry" if not unrotated else
              "aspect-difference-exceeds-bound" if mismatch > threshold + 1e-12 else
              "near-canvas-grid-with-filled-source-pair")
    applied = both_fill and square and unrotated and mismatch <= threshold + 1e-12
    framing = {"fit": "fill" if applied else "fit", "anchor_x": .5, "anchor_y": .5, "zoom": 1.}
    return framing, {"policy": "near-canvas-native-grid-correction-v1", "applied": applied,
                     "reason": reason, "maximum_aspect_ratio_mismatch": threshold,
                     "coded_aspect_ratio_mismatch": mismatch,
                     "missing_sample_aspect_assumed_square": assumed_square,
                     "selected_framing": framing}


def _endpoint_delta(reference, actual):
    """Descriptive image differences only; not a quality or continuity score."""
    import numpy as np
    from PIL import Image, ImageOps
    with Image.open(reference) as image:
        a = np.asarray(ImageOps.pad(image.convert("RGB"), (160, 90)), dtype=np.float32)
    with Image.open(actual) as image:
        b = np.asarray(ImageOps.pad(image.convert("RGB"), (160, 90)), dtype=np.float32)
    return {"mean_absolute_rgb_difference": round(float(np.abs(a - b).mean()) / 255, 5),
            "interpretation": "display-fit-image-difference-not-a-motion-or-quality-score"}


def run(job, config, db, store, progress, cancelled, *, generation=None):
    proposal = job["snapshot"]["transition_bridge"]
    if proposal["job_id"] != job["id"]:
        raise ValueError("The bridge input belongs to a different job")
    root = _verify(proposal, config, db, store)
    cancelled = Guard(root, cancelled, kind=job.get("kind", "transition-bridge"))
    request = proposal["request"]
    source = proposal["source_snapshot"]
    width, height = jobs.dimensions(source["request"]["output"])
    a, b = source["request"]["outgoing"], source["request"]["incoming"]
    clips = [(Path(source["sources"][0]["path"]), a["source_start"], a["source_end"] - a["source_start"], 1.),
             (root / "original.media", request["trim_start"], request["trim_end"] - request["trim_start"],
              request["playback_duration"] / (request["trim_end"] - request["trim_start"])),
             (Path(source["sources"][1]["path"]), b["source_start"], b["source_end"] - b["source_start"], 1.)]
    counts = [frame_count(duration * speed) for _path, _start, duration, speed in clips]
    normalization = []
    temporary = [root / f"clip-{index:03}.mp4" for index in range(3)] + [root / "output.partial.mp4"]
    for path in [*temporary, *(root / name for name in (
        "output.mp4", "manifest.json", "bridge-first.jpg", "bridge-last.jpg", "normalized-a.jpg", "normalized-b.jpg"))]:
        _check_render_file(path)
    try:
        if cancelled():
            raise JobCancelled("Bridge assembly cancelled")
        for index, (path, start, duration, speed) in enumerate(clips):
            progress(["Preparing outgoing clip", "Preparing imported bridge", "Preparing incoming clip"][index])
            stream = next(row for row in _probe(path)["streams"] if row["codec_type"] == "video")
            bridge_framing = None
            if index == 1:
                framing, bridge_framing = _bridge_framing(width, height, a, b, stream)
            else:
                framing = (a if index == 0 else b).get("framing")
            spatial, normalized = jobs.spatial_color_filter(width, height, framing, stream)
            if bridge_framing is not None:
                normalized["bridge_framing"] = bridge_framing
            normalization.append(normalized)
            run_process(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                         "-protocol_whitelist", "file,pipe", "-ss", str(start), "-i", str(path),
                         "-map", "0:v:0", "-an", "-vf", _filter(spatial, duration, counts[index], speed),
                         "-frames:v", str(counts[index]), "-c:v", "libx264", "-preset", "fast",
                         "-crf", "18", "-threads", "2", "-color_primaries", "bt709", "-color_trc", "bt709",
                         "-colorspace", "bt709", "-color_range", "tv", str(temporary[index])], cancelled=cancelled, timeout=180)
        progress("Assembling A, bridge, and B")
        graph = "[0:v][1:v][2:v]concat=n=3:v=1:a=0,format=yuv420p[v]"
        arguments = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y"]
        for path in temporary[:3]:
            arguments += ["-i", str(path)]
        run_process([*arguments, "-filter_complex_threads", "2", "-filter_complex", graph,
                     "-map", "[v]", "-an", "-frames:v", str(sum(counts)), "-r", str(FPS),
                     *ffmpeg_args(source["request"]["output"]["quality"]),
                     "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709", "-color_range", "tv",
                     "-movflags", "+faststart", str(temporary[3])], cancelled=cancelled, timeout=300)
        video = next(row for row in _probe(temporary[3])["streams"] if row["codec_type"] == "video")
        if (int(video.get("nb_frames", 0)) != sum(counts) or video["width"] != width or video["height"] != height
                or video.get("sample_aspect_ratio") != "1:1"):
            raise ValueError("The assembled bridge does not match its requested frame timing or dimensions")
        for name, segment, timestamp in (("bridge-first.jpg", 1, 0),
                                          ("bridge-last.jpg", 1, (counts[1] - 1) / FPS),
                                          ("normalized-a.jpg", 0, (counts[0] - 1) / FPS),
                                          ("normalized-b.jpg", 2, 0)):
            run_process(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                         "-ss", str(timestamp), "-i", str(temporary[segment]), "-frames:v", "1", str(root / name)],
                        cancelled=cancelled, timeout=30)
        _verify(proposal, config, db, store)
        if cancelled():
            raise JobCancelled("Bridge assembly cancelled")
        start, end = counts[0] / FPS, sum(counts[:2]) / FPS
        digest = jobs.sha256(temporary[3])
        manifest = {"schema_version": 1, "job_id": job["id"], "bridge_version": BRIDGE_VERSION,
                    "request": request, "source_request": source["request"],
                    "sources": [{key: row[key] for key in ("film_id", "size", "mtime_ns", "title")}
                                for row in source["sources"]],
                    "input": proposal["input"], "input_manifest_sha256": proposal["input_manifest_sha256"],
                    "audio": "muted", "fps": FPS, "width": width, "height": height,
                    "normalization": normalization,
                    "encoding": encoding_record(source["request"]["output"]["quality"]),
                    "frame_count": sum(counts), "segment_frame_counts": counts,
                    "duration": sum(counts) / FPS, "transition_start": start, "transition_end": end,
                    "assembly_policy": "full-source-A-then-trimmed-retimed-bridge-then-full-source-B-v1",
                    "timing_policy": "nearest-30fps-half-frame-ties-up-v1",
                    "retime_factor": clips[1][3], "synthesized_range": {"start": start, "end": end},
                    "endpoint_diagnostics": {
                        "outgoing": _endpoint_delta(root / "normalized-a.jpg", root / "bridge-first.jpg"),
                        "incoming": _endpoint_delta(root / "normalized-b.jpg", root / "bridge-last.jpg")},
                    "output_sha256": digest,
                    "artifacts": {name: jobs.sha256(root / name) for name in
                                  ("original.media", "frame-a.jpg", "frame-b.jpg", "bridge-first.jpg", "bridge-last.jpg",
                                   "normalized-a.jpg", "normalized-b.jpg")},
                    "ffmpeg_version": run_process(["ffmpeg", "-version"], timeout=10).decode().splitlines()[0]}
        if generation is not None:
            manifest["generation"] = generation
        _write_json(root / "manifest.json", manifest)
        temporary[3].replace(root / "output.mp4")
        family = "generations" if generation is not None else "bridges"
        base = f"/lab/transitions/{family}/{job['id']}"
        return {"preview_url": f"{base}/video", "manifest_url": f"{base}/manifest",
                "original_url": f"{base}/original", "duration": manifest["duration"], "fps": FPS,
                "width": width, "height": height, "transition_start": start, "transition_end": end,
                "bridge_version": BRIDGE_VERSION, "output_sha256": digest,
                "manifest_sha256": jobs.sha256(root / "manifest.json"),
                "endpoint_diagnostics": manifest["endpoint_diagnostics"],
                "source_frame_times": proposal["input"]["source_frame_times"],
                "provenance_status": proposal["input"]["provenance_status"]}
    finally:
        _cleanup_render_intermediates(root, [*temporary, root / "manifest.json.partial"])


def artifact(config, job, kind):
    if (job.get("kind") != "transition-bridge" or job["status"] != "completed"
            or job["cancel_requested"] or not job.get("result")):
        raise ValueError("This bridge audition is not ready")
    proposal = job["snapshot"]["transition_bridge"]
    if proposal["job_id"] != job["id"]:
        raise ValueError("The bridge input belongs to a different job")
    root = _input(config, proposal)
    manifest_path = root / "manifest.json"
    _check_render_file(manifest_path)
    if jobs.sha256(manifest_path) != job["result"]["manifest_sha256"]:
        raise ValueError("The bridge manifest changed")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (manifest["job_id"] != job["id"] or manifest["request"] != proposal["request"]
            or manifest["source_request"] != proposal["source_snapshot"]["request"]
            or manifest["input_manifest_sha256"] != proposal["input_manifest_sha256"]):
        raise ValueError("The bridge manifest does not belong to this request")
    name = {"video": "output.mp4", "manifest": "manifest.json", "original": "original.media",
            "a": "frame-a.jpg", "b": "frame-b.jpg", "first": "bridge-first.jpg", "last": "bridge-last.jpg"}[kind]
    path = root / name
    _check_render_file(path)
    expected = manifest["output_sha256"] if kind == "video" else manifest["artifacts"].get(name)
    if expected and jobs.sha256(path) != expected:
        raise ValueError("The bridge artifact changed")
    return path
