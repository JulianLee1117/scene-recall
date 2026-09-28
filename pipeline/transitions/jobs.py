"""Freeze source identities, render locally, and retain an auditable job receipt."""
from __future__ import annotations

import hashlib
import json
import logging
import math
from pathlib import Path
import uuid

from pipeline.ingest.probe import _content_hash
from pipeline.lab.media import JobCancelled, probe_media, resolve_film, run_process
from pipeline.transitions.contracts import FPS, RENDERER_VERSION, RenderRequest, frame_count
from pipeline.transitions.compositor import render_clips
from pipeline.transitions.media import endpoint
from pipeline.transitions.storage import Guard

_LOG = logging.getLogger(__name__)


def sha256(path):
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def freeze(request, db):
    request = RenderRequest.model_validate(request).model_dump(mode="json")
    sources = []
    for clip in (request["outgoing"], request["incoming"]):
        film = resolve_film(db, clip["film_id"])
        path = Path(film["path"])
        if clip["source_end"] > float(film["duration"]):
            raise ValueError("A selected window extends beyond the source film")
        if clip["unit_id"]:
            from pipeline.matching.cohort import unit
            try:
                shot = unit(db, clip["unit_id"])
            except ValueError:
                # Shot rows are replaceable derivations. The immutable film
                # and timestamp anchors remain valid after a hint disappears.
                shot = None
            if shot is not None and shot["film_id"] != clip["film_id"]:
                raise ValueError("The selected shot belongs to a different source film")
        if not path.is_file() or _content_hash(path) != clip["film_id"]:
            raise ValueError("A source film is unavailable or changed; restore the original before rendering")
        stat = path.stat()
        sources.append({"film_id": clip["film_id"], "path": str(path.resolve()),
                        "size": stat.st_size, "mtime_ns": stat.st_mtime_ns,
                        "title": film.get("title", "")})
    return {"request": request, "renderer_version": RENDERER_VERSION, "sources": sources}


def output_root(config, job_id):
    if str(uuid.UUID(job_id)) != job_id:
        raise ValueError("Invalid transition render identity")
    return config.paths.assets_dir / "lab" / "renders" / job_id


def _verify(proposal, db):
    current = freeze(proposal["request"], db)
    if current != proposal:
        raise ValueError("The renderer or source changed after this request; submit a fresh render")


def enqueue(proposal, config, store):
    """Reuse exact completed local bytes only after validating every artifact.

    Source identity/profile are part of the freshly frozen proposal. File checks
    happen outside SQLite's write lock; enqueue rechecks terminal job ownership,
    cancellation and result identity under its existing serialization.
    """
    reusable = None
    for candidate in store.completed_transition_renders(proposal):
        try:
            for kind in ("manifest", "video", "a", "b"):
                artifact(config, candidate, kind)
        except (OSError, ValueError, KeyError, TypeError):
            continue
        reusable = candidate
        break
    return store.enqueue_transition_render(proposal, reusable=reusable)


def dimensions(output):
    from pipeline.transitions.encoding import dimensions as output_dimensions
    return output_dimensions(output)


def spatial_color_filter(width, height, framing=None, video=None):
    """Shared display framing and explicit source-to-Rec.709 color normalization.

    Return a filter fragment plus the assumptions that must be kept in a receipt.
    Known PQ/HLG footage is tone mapped into this deliberately SDR lab profile.
    """
    from pipeline.transitions.contracts import Framing
    framing = Framing.model_validate(framing or {}).model_dump()
    video = video or {}
    rgb = str(video.get("pix_fmt", "")).startswith(("rgb", "bgr", "gbr"))
    matrix = video.get("color_space") or ("gbr" if rgb else "bt709")
    transfer = video.get("color_transfer") or "bt709"
    primaries = video.get("color_primaries") or "bt709"
    known = lambda value: value not in {None, "unknown", "unspecified", "reserved"}
    matrix = matrix if known(matrix) else ("gbr" if rgb else "bt709")
    transfer = transfer if known(transfer) else "bt709"
    primaries = primaries if known(primaries) else "bt709"
    matrix = {"bt2020nc": "2020_ncl", "bt2020c": "2020_cl"}.get(matrix, matrix)
    if matrix not in {"gbr", "bt709", "fcc", "bt470bg", "smpte170m", "smpte240m", "2020_ncl", "2020_cl", "ycgco"}:
        raise ValueError("The source color matrix is not supported by this Rec.709 preview profile")
    if transfer not in {"bt709", "smpte170m", "smpte240m", "bt470m", "bt470bg", "linear", "iec61966-2-1", "iec61966-2-4", "bt2020-10", "bt2020-12", "smpte2084", "arib-std-b67"}:
        raise ValueError("The source transfer function is not supported by this Rec.709 preview profile")
    if primaries not in {"bt709", "bt470m", "bt470bg", "smpte170m", "smpte240m", "bt2020", "smpte431", "smpte432", "film"}:
        raise ValueError("The source color primaries are not supported by this Rec.709 preview profile")
    source_range = "full" if video.get("color_range") == "pc" or rgb or str(video.get("pix_fmt", "")).startswith("yuvj") else "limited"
    color = f"zscale=matrixin={matrix}:transferin={transfer}:primariesin={primaries}:rangein={source_range}"
    hdr = transfer in {"smpte2084", "arib-std-b67"}
    if hdr:
        color += (":transfer=linear:npl=100,format=gbrpf32le,"
                  "zscale=primaries=bt709,tonemap=mobius:param=0.3:desat=2,"
                  "zscale=matrix=bt709:transfer=bt709:primaries=bt709:range=limited,format=yuv444p")
    else:
        color += ":matrix=bt709:transfer=bt709:primaries=bt709:range=limited,format=yuv444p"
    fit = framing["fit"]
    x, y, zoom = framing["anchor_x"], framing["anchor_y"], framing["zoom"]
    # Normalize display pixels before fitting/cropping. Anchor positions describe
    # the amount of excess removed from each side, including the optional zoom.
    geometry = "scale=w='max(2,trunc(iw*sar/2)*2)':h=ih,setsar=1,"
    geometry += f"scale={width}:{height}:force_original_aspect_ratio={'increase' if fit == 'fill' else 'decrease'}:force_divisible_by=2,"
    if fit == "fill":
        geometry += f"crop={width}:{height}:(iw-ow)*{x:.9f}:(ih-oh)*{y:.9f},"
    else:
        geometry += f"pad={width}:{height}:(ow-iw)*{x:.9f}:(oh-ih)*{y:.9f}:color=black,"
    if zoom > 1:
        geometry += (f"scale={math.ceil(width * zoom / 2) * 2}:{math.ceil(height * zoom / 2) * 2},"
                     f"crop={width}:{height}:(iw-ow)*{x:.9f}:(ih-oh)*{y:.9f},")
    geometry += "setsar=1,format=yuv444p"
    record = {"policy": "rec709-linear-light-sdr-v1", "source_matrix": matrix, "source_transfer": transfer,
              "source_primaries": primaries, "source_range": source_range,
              "missing_tag_assumptions": [key for key in ("color_space", "color_transfer", "color_primaries", "color_range") if not known(video.get(key))],
              "hdr_tonemap": "mobius-0.3-npl100-desat2" if hdr else None,
              "output": "bt709-primaries-transfer-matrix-limited", "framing": framing}
    return color + "," + geometry, record


def _normal_filter(width, height, duration, framing=None, video=None):
    spatial, _ = spatial_color_filter(width, height, framing, video)
    return (f"trim=duration={duration:.9f},setpts=PTS-STARTPTS,fps={FPS},"
            f"{spatial},tpad=stop_mode=clone:stop_duration={1 / FPS:.9f},settb=1/{FPS}")


def run(job, config, db, store, progress, cancelled):
    proposal = job["snapshot"]["transition_render"]
    _verify(proposal, db)
    request = proposal["request"]
    recipe = request["recipe"]
    width, height = dimensions(request["output"])
    from pipeline.transitions.retiming import plan, prepare_clip, cut_receipt
    retime = request["retime"]
    plans = [plan(clip, retime, outgoing=index == 0)
             for index, clip in enumerate((request["outgoing"], request["incoming"]))]
    counts = [row["frame_count"] for row in plans]
    overlap = frame_count(recipe["duration"])
    total = sum(counts) - overlap
    offset = (counts[0] - overlap) / FPS
    root = output_root(config, job["id"])
    root.mkdir(parents=True, exist_ok=False)
    cancelled = Guard(root, cancelled)
    intermediate = [root / "clip-0.mp4", root / "clip-1.mp4", root / "output.partial.mp4"]
    final = root / "output.mp4"
    frame_times = []
    filters = []
    colors = []
    retimed = []
    try:
        if cancelled():
            raise JobCancelled("Transition render cancelled")
        for index, (clip, source) in enumerate(zip((request["outgoing"], request["incoming"]), proposal["sources"])):
            progress(f"Preparing clip {'A' if index == 0 else 'B'}")
            raw = probe_media(Path(source["path"]))
            video = next((row for row in raw.get("streams", []) if row.get("codec_type") == "video"), None)
            if video is None or clip["source_end"] > float(raw.get("format", {}).get("duration", 0)) + .001:
                raise ValueError("A clip has no readable video or extends beyond its actual media")
            vf = _normal_filter(width, height, clip["source_end"] - clip["source_start"], clip.get("framing"), video)
            colors.append(spatial_color_filter(width, height, clip.get("framing"), video)[1])
            filters.append(vf)
            if retime["mode"] != "off":
                spatial, _ = spatial_color_filter(width, height, clip.get("framing"), video)
                prepared = prepare_clip(source["path"], intermediate[index], clip, retime,
                                        outgoing=index == 0, width=width, height=height, spatial=spatial,
                                        video=video, origin=float(raw.get("format", {}).get("start_time") or 0),
                                        cancelled=cancelled, progress=progress)
                retimed.append(prepared)
                filters[-1] = {"spatial": spatial, "temporal_policy": prepared["policy"]}
            else:
                run_process(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                         "-ss", str(clip["source_start"]), "-i", source["path"],
                         "-t", str(clip["source_end"] - clip["source_start"]), "-map", "0:v:0", "-an",
                         "-vf", vf, "-frames:v", str(counts[index]), "-c:v", "libx264",
                         "-preset", "fast", "-crf", "14", "-threads", "2",
                         "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709", "-color_range", "tv", str(intermediate[index])],
                            cancelled=cancelled, timeout=180)
            frame_times.append(endpoint(Path(source["path"]), clip, outgoing=index == 0,
                                        destination=root / f"frame-{'a' if index == 0 else 'b'}.jpg", cancelled=cancelled))
        progress("Rendering the transition")
        graph = render_clips(intermediate[:2], intermediate[2], counts, overlap, recipe,
                             width=width, height=height, quality=request["output"]["quality"],
                             cancelled=cancelled, progress=progress)
        progress("Checking the preview and preserving its recipe")
        probe = probe_media(intermediate[2])
        video = next((row for row in probe.get("streams", []) if row.get("codec_type") == "video"), {})
        if (int(video.get("nb_frames", 0)) != total or video.get("width") != width or video.get("height") != height
                or video.get("sample_aspect_ratio") != "1:1"
                or abs(float(probe["format"]["duration"]) - total / FPS) > 1 / FPS):
            raise ValueError("Rendered timing, dimensions or pixel aspect differ from the requested preview")
        _verify(proposal, db)
        if cancelled():
            raise JobCancelled("Transition render cancelled")
        output_sha = sha256(intermediate[2])
        base = f"/lab/transitions/renders/{job['id']}"
        result = {"preview_url": f"{base}/video", "manifest_url": f"{base}/manifest",
                  "frame_a_url": f"{base}/frames/a", "frame_b_url": f"{base}/frames/b",
                  "frame_a_time": frame_times[0], "frame_b_time": frame_times[1],
                  "duration": total / FPS, "transition_start": offset, "transition_end": counts[0] / FPS,
                  "fps": FPS, "width": width, "height": height, "renderer_version": RENDERER_VERSION,
                  "recipe": recipe, "retime": retime, "output_sha256": output_sha}
        timing_receipt = {"request": retime, "sources": retimed,
                          "visible_cut": cut_receipt(plans, retime, request, overlap, sampled_sources=retimed),
                          "source_windows_unchanged": True}
        result["retiming"] = {"source_durations": [row["source_duration"] for row in plans],
                              "output_durations": [row["output_duration"] for row in plans],
                              "visible_cut": timing_receipt["visible_cut"]}
        manifest = {"schema_version": 2, "job_id": job["id"], "renderer_version": RENDERER_VERSION,
                    "request": request, "sources": [{key: row[key] for key in ("film_id", "size", "mtime_ns", "title")}
                                                     for row in proposal["sources"]],
                    "source_identity_policy": "indexed-first-last-4MiB-sha256-plus-size-and-mtime-v1",
                    "audio": "muted", "fps": FPS, "width": width, "height": height, "sample_aspect_ratio": "1:1",
                    "timing_policy": "nearest-30fps-half-frame-ties-up-v1",
                    "frame_count": total, "duration": total / FPS, "transition_start": offset,
                    "transition_end": counts[0] / FPS, "source_frame_times": frame_times,
                    "source_frame_policy": "native-last-A-first-B-within-source-window-display-normalized-v1",
                    "source_frame_use": "manual-external-bridge-endpoints-independent-of-local-overlap",
                    "frame_a_sha256": sha256(root / "frame-a.jpg"), "frame_b_sha256": sha256(root / "frame-b.jpg"),
                    "output_sha256": output_sha, "normalization_filters": filters, "color_normalization": colors,
                    "retiming": timing_receipt,
                    "compositor": graph, "transition_filter": None,
                    "ffmpeg_version": run_process(["ffmpeg", "-version"], timeout=10).decode().splitlines()[0]}
        manifest_path = root / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2, allow_nan=False), encoding="utf-8")
        result["manifest_sha256"] = sha256(manifest_path)
        intermediate[2].replace(final)
        return result
    finally:
        for path in intermediate:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                _LOG.warning("Could not remove transition intermediate %s", path)


def artifact(config, job, kind):
    """A download belongs to a completed immutable receipt, never a partial job."""
    if job["status"] != "completed" or job["cancel_requested"] or not job.get("result"):
        raise ValueError("This transition render is not ready")
    root = output_root(config, job["id"])
    result = job["result"]
    manifest_path = root / "manifest.json"
    if sha256(manifest_path) != result["manifest_sha256"]:
        raise ValueError("The saved transition receipt changed; render a fresh variant")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest["request"] != job["snapshot"]["transition_render"]["request"] or manifest["job_id"] != job["id"]:
        raise ValueError("The preview does not belong to this transition request")
    name, digest = {"video": ("output.mp4", "output_sha256"), "a": ("frame-a.jpg", "frame_a_sha256"),
                    "b": ("frame-b.jpg", "frame_b_sha256"), "manifest": ("manifest.json", None)}[kind]
    path = root / name
    if digest and sha256(path) != manifest[digest]:
        raise ValueError("The saved transition artifact changed; render a fresh variant")
    return path
