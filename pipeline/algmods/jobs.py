"""Freeze one film window, render the dots treatment on the editor worker, keep a receipt."""
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
import uuid

from pipeline.algmods.contracts import FPS, MODS_VERSION, RenderRequest, render_params
from pipeline.ingest.probe import _content_hash
from pipeline.lab.media import JobCancelled, probe_media, resolve_film, run_process

_LOG = logging.getLogger(__name__)
KIND = "algmods-render"


def sha256(path):
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def freeze(request, db, *, resolve_tiles=None, tile_units=None):
    """Validate the request and pin the source film's identity, size and mtime.

    A search-sourced mosaic also pins its tile set: ``resolve_tiles(queries)`` returns the shots
    (film_id, unit) at request time, so the worker never searches and a re-run is exact."""
    request = RenderRequest.model_validate(request).model_dump(mode="json")
    window = request["source"]
    film = resolve_film(db, window["film_id"])
    path = Path(film["path"])
    if window["source_end"] > float(film["duration"]):
        raise ValueError("The selected window extends beyond the source film")
    if not path.is_file() or _content_hash(path) != window["film_id"]:
        raise ValueError("The source film is unavailable or changed; restore the original before rendering")
    stat = path.stat()
    source = {"film_id": window["film_id"], "path": str(path.resolve()), "size": stat.st_size,
              "mtime_ns": stat.st_mtime_ns, "title": film.get("title", "")}
    proposal = {"request": request, "version": MODS_VERSION, "source": source}
    treatment = request["treatment"]
    if treatment["kind"] == "mosaic" and treatment["source"] == "search":
        if tile_units is None:
            if resolve_tiles is None:
                raise ValueError("A search-sourced mosaic must be requested through the API")
            tile_units = [[str(f), int(u)] for f, u in resolve_tiles([q for q in treatment["queries"] if q.strip()])]
        if len(tile_units) < 12:
            raise ValueError("The search found too few shots to tile with; broaden or add queries")
        proposal["tile_units"] = tile_units
    return proposal


def output_root(config, job_id):
    if str(uuid.UUID(job_id)) != job_id:
        raise ValueError("Invalid Alg Mods render identity")
    return config.paths.assets_dir / "lab" / "renders" / job_id


def _verify(proposal, db):
    if freeze(proposal["request"], db, tile_units=proposal.get("tile_units")) != proposal:
        raise ValueError("The renderer or source changed after this request; submit a fresh render")


def enqueue(proposal, config, store):
    """Reuse exact completed bytes only after every artifact verifies."""
    reusable = None
    for candidate in store.completed_algmods_renders(proposal):
        try:
            for kind in ("manifest", "video"):
                artifact(config, candidate, kind)
        except (OSError, ValueError, KeyError, TypeError):
            continue
        reusable = candidate
        break
    return store.enqueue_algmods_render(proposal, reusable=reusable)


def _subject_detection_available():
    try:
        import rfdetr  # noqa: F401
    except ImportError:
        return False
    return True


def run(job, config, db, store, progress, cancelled):
    proposal = job["snapshot"]["algmods_render"]
    _verify(proposal, db)
    request = proposal["request"]
    window, treatment, output = request["source"], request["treatment"], request["output"]
    mod, params = render_params(treatment)
    if mod == "mosaic":
        params["host_film"] = window["film_id"]
        params["assets_dir"] = str(config.paths.assets_dir)
        params["tile_units"] = proposal.get("tile_units")
    try:
        from pipeline.algmods import render as renderer
    except ImportError:
        raise ValueError("Alg Mods needs OpenCV: run `uv sync --extra algmods --extra measure` and restart the editor worker") from None
    subject = _subject_detection_available()
    if treatment["kind"] == "dots" and treatment["protect_subject"] and not subject:
        raise ValueError("Keeping the subject real needs subject detection: run `uv sync --extra measure` and restart the editor worker")
    root = output_root(config, job["id"])
    root.mkdir(parents=True, exist_ok=False)
    partial = root / "output.partial.mp4"
    final = root / "output.mp4"
    try:
        if cancelled():
            raise JobCancelled("Alg Mods render cancelled")
        progress(f"Rendering {mod}")
        summary = renderer.render(proposal["source"]["path"], window["source_start"], window["source_end"], mod, partial,
                                  params=params, progress=progress, side_by_side=output["side_by_side"],
                                  subject=subject, cancelled=cancelled)
        if cancelled():
            raise JobCancelled("Alg Mods render cancelled")
        progress("Checking the preview and preserving its receipt")
        probe = probe_media(partial)
        video = next((row for row in probe.get("streams", []) if row.get("codec_type") == "video"), {})
        width, height = int(video.get("width", 0)), int(video.get("height", 0))
        expected_h = summary["crop"][3] * (2 if (output["side_by_side"] and summary["crop"][0] == 0 and summary["crop"][2] != renderer.OUT_W) else 1) if mod == "mosaic" and params.get("aspect") == "native" else renderer.OUT_H
        if int(video.get("nb_frames", 0)) != summary["frames"] or height != expected_h:
            raise ValueError("Rendered timing or dimensions differ from the requested preview")
        _verify(proposal, db)
        output_sha = sha256(partial)
        base = f"/lab/alg-mods/renders/{job['id']}"
        result = {"preview_url": f"{base}/video", "manifest_url": f"{base}/manifest",
                  "duration": summary["frames"] / FPS, "frames": summary["frames"], "fps": FPS,
                  "width": width, "height": height, "version": MODS_VERSION, "treatment": treatment,
                  "side_by_side": output["side_by_side"], "subject_detection": subject,
                  "crop": summary["crop"], "output_sha256": output_sha}
        manifest = {"schema_version": 1, "job_id": job["id"], "version": MODS_VERSION, "request": request,
                    "source": {key: proposal["source"][key] for key in ("film_id", "size", "mtime_ns", "title")},
                    "source_identity_policy": "indexed-first-last-4MiB-sha256-plus-size-and-mtime-v1",
                    "audio": "muted", "fps": FPS, "width": width, "height": height,
                    "frame_count": summary["frames"], "duration": summary["frames"] / FPS,
                    "crop": summary["crop"], "subject_detection": subject,
                    "mask_coverage": summary.get("mask_coverage"), "output_sha256": output_sha,
                    "ffmpeg_version": run_process(["ffmpeg", "-version"], timeout=10).decode().splitlines()[0]}
        manifest_path = root / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2, allow_nan=False), encoding="utf-8")
        result["manifest_sha256"] = sha256(manifest_path)
        partial.replace(final)
        return result
    except InterruptedError:
        raise JobCancelled("Alg Mods render cancelled") from None
    finally:
        try:
            partial.unlink(missing_ok=True)
        except OSError:
            _LOG.warning("Could not remove Alg Mods intermediate %s", partial)


def artifact(config, job, kind):
    """A download belongs to a completed immutable receipt, never a partial job."""
    if job["status"] != "completed" or job["cancel_requested"] or not job.get("result"):
        raise ValueError("This Alg Mods render is not ready")
    root = output_root(config, job["id"])
    result = job["result"]
    manifest_path = root / "manifest.json"
    if sha256(manifest_path) != result["manifest_sha256"]:
        raise ValueError("The saved receipt changed; render a fresh variant")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest["request"] != job["snapshot"]["algmods_render"]["request"] or manifest["job_id"] != job["id"]:
        raise ValueError("The preview does not belong to this request")
    if kind == "manifest":
        return manifest_path
    if kind != "video":
        raise KeyError(kind)
    path = root / "output.mp4"
    if sha256(path) != manifest["output_sha256"]:
        raise ValueError("The saved preview changed; render a fresh variant")
    return path
