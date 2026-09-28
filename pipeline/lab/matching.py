"""Thin job and preview adapter for the independently callable matching engine."""

from __future__ import annotations

from pathlib import Path
import time

from pipeline.matching.service import find
from pipeline.lab.media import render_reel


def verify_boundaries(output, manifest, db, expected_pts, cancelled=lambda: False):
    """Compare the actual played boundary images to the selected decoded evidence.

    Compression/resize tolerance is explicit. This is an image fidelity check,
    not proof of editorial quality or distinguishability of duplicate frames.
    """
    import av
    import numpy as np
    from PIL import Image, ImageOps
    from pipeline.matching.media import at
    from pipeline.lab.media import resolve_film, JobCancelled

    cut = manifest["clips"][0]["frame_count"]
    actual = {}
    with av.open(str(output)) as container:
        for index, frame in enumerate(container.decode(video=0)):
            if cancelled():
                raise JobCancelled("Matching cancelled")
            if index in (cut - 1, cut):
                actual[index] = frame.to_image().resize((320, 180))
            if index >= cut:
                break
    checks = []
    for index, clip, timestamp in zip((cut - 1, cut), manifest["clips"], expected_pts):
        source = Path(resolve_film(db, clip["film_id"])["path"])
        sample = at(
            source, timestamp, clip["source_start"], clip["source_end"], cancelled
        )
        picture = sample.image
        crop = clip.get("crop")
        if crop:
            w, h = picture.size
            left, top = int(w * crop["x"]) // 2 * 2, int(h * crop["y"]) // 2 * 2
            width, height = (
                int(w * crop["width"]) // 2 * 2,
                int(h * crop["height"]) // 2 * 2,
            )
            picture = picture.crop((left, top, left + width, top + height))
        picture = ImageOps.pad(
            picture,
            (manifest["width"], manifest["height"]),
            method=Image.Resampling.LANCZOS,
        ).resize((320, 180))
        error = float(
            np.abs(
                np.asarray(picture, dtype=float)
                - np.asarray(actual[index], dtype=float)
            ).mean()
        )
        checks.append(
            {
                "source_pts": sample.time,
                "mean_pixel_error": error,
                "within_tolerance": error <= 12,
            }
        )
    return {
        "profile": "boundary-image-mae-v1",
        "checks": checks,
        "passed": all(row["within_tolerance"] for row in checks),
    }


def run(job, config, db, store, progress, cancelled):
    snapshot = job["snapshot"]
    started = time.monotonic()
    result = find(
        config, db, snapshot["document"], snapshot["match"], progress, cancelled
    )
    from pipeline.matching.media import at
    from pipeline.lab.media import resolve_film
    from pipeline.matching.cohort import unit

    directory = config.paths.assets_dir / "matching" / "results" / job["id"]
    directory.mkdir(parents=True, exist_ok=True)
    for candidate in result["candidates"]:
        selected = candidate["incoming"]
        source = unit(db, selected["unit_id"])
        frame = at(
            Path(resolve_film(db, selected["film_id"])["path"]),
            candidate["candidate_frame_pts"],
            source["t_start"],
            source["t_end"],
            cancelled,
        )
        frame.image.thumbnail((640, 640))
        frame.image.save(directory / (candidate["id"] + ".jpg"), quality=88)
    store.publish_match_progress(job["id"], result)
    for i, candidate in enumerate(result["candidates"][:3]):
        progress(
            f"Preparing transition preview {i + 1}/{min(3, len(result['candidates']))}"
        )
        render_candidate(
            job["id"],
            snapshot["document"],
            candidate,
            config,
            db,
            store,
            progress,
            cancelled,
        )
        store.publish_match_progress(job["id"], result)
    result["elapsed_seconds"] = time.monotonic() - started
    return result


def render_candidate(
    identity, source_document, candidate, config, db, store, progress, cancelled
):
    from pipeline.ingest.probe import _content_hash
    from pipeline.lab.media import resolve_film

    clips = [candidate["outgoing"], candidate["incoming"]]
    for film_id in {clip["film_id"] for clip in clips}:
        source = Path(resolve_film(db, film_id)["path"])
        if not source.is_file() or _content_hash(source) != film_id:
            raise ValueError(
                "Matched source footage changed; restore it before previewing"
            )
    # Full-picture proposals have an identical original: encode/check only once.
    cropped = any(clip.get("crop") for clip in clips)
    candidate["original_is_proposed"] = not cropped
    for original in ((False, True) if cropped else (False,)):
        chosen = [{**clip, "crop": None} for clip in clips] if original else clips
        document = {**source_document, "clips": chosen}
        suffix = "original" if original else "proposed"
        rendered = {
            "id": f"{identity}-{candidate['id']}-{suffix}",
            "snapshot": {
                "document": document,
                "mode": "preview",
                "experiment_id": "visual-rhymes",
            },
        }
        response = render_reel(rendered, config, db, store, progress, cancelled)
        output = (
            config.paths.assets_dir / "lab" / "renders" / rendered["id"] / "output.mp4"
        )
        check = verify_boundaries(
            output,
            response["manifest"],
            db,
            [candidate["reference_frame_pts"], candidate["candidate_frame_pts"]],
            cancelled,
        )
        candidate.setdefault("boundary_checks", {})[suffix] = check
    candidate["preview_ready"] = all(
        check["passed"] for check in candidate["boundary_checks"].values()
    )
    if not candidate["preview_ready"]:
        candidate["preview_warning"] = (
            "The rendered boundary differs from the matched image; inspect the source before using this suggestion."
        )
    else:
        candidate.pop("preview_warning", None)


def preview_run(job, config, db, store, progress, cancelled):
    proposal = job["snapshot"]["match"]
    candidate = proposal["candidate"]
    render_candidate(
        job["id"],
        proposal["document"],
        candidate,
        config,
        db,
        store,
        progress,
        cancelled,
    )
    candidate["preview_url"] = f"/lab/jobs/{job['id']}/matches/{candidate['id']}/preview"
    return {"match_job_id": proposal["match_job_id"], "candidate": candidate}


def adjusted_candidate(candidate, db, adjustments):
    """Resolve explicit boundary changes against real frames inside offered shots."""
    from copy import deepcopy
    from pipeline.matching import media, cohort
    from pipeline.lab.media import resolve_film

    result = deepcopy(candidate)
    for role, key, pts_key in (("outgoing", "outgoing_time", "reference_frame_pts"),
                               ("incoming", "incoming_time", "candidate_frame_pts")):
        timestamp = adjustments.get(key)
        if timestamp is None:
            continue
        clip = result[role]
        if clip.get("locked"):
            raise ValueError("Unlock this scene before adjusting its timing")
        source = cohort.unit(db, clip["unit_id"])
        if source["film_id"] != clip["film_id"] or not source["t_start"] <= timestamp < source["t_end"]:
            raise ValueError("Choose a cut point inside the offered shot")
        path = Path(resolve_film(db, clip["film_id"])["path"])
        frame = media.at(path, timestamp,
                         source["t_start"], source["t_end"])
        start, end = ((media.outgoing_start(path, frame, source["t_start"], source["t_end"]), frame.end) if role == "outgoing"
                      else (frame.time, min(source["t_end"], frame.time + 1)))
        if end - start < 0.5:
            raise ValueError("Leave at least half a second of footage on each side of the cut")
        clip.update(source_start=start, source_end=end, reference_time=frame.time,
                    window_start=None, window_end=None)
        result[pts_key] = frame.time
    # These measurements described the original proposed windows only.
    result.update(components={}, matched_channels=[], evidence="Timing adjusted by you; check the played cut",
                  preview_ready=False, adjusted=True)
    result.pop("boundary_checks", None)
    result.pop("preview_warning", None)
    result.pop("preview_url", None)
    return result
