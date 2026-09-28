"""Durable discovery and audition adapter. It never applies project documents."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import time

from pipeline.lab.media import JobCancelled, render_manifest
from pipeline.lab.models import ProjectDocument
from pipeline.lab.matching import render_candidate
from pipeline.matching import cohort, media
from pipeline.matching.contracts import same_pair


def preview_document(candidate):
    return {**ProjectDocument().model_dump(mode="json"),
            "clips": [candidate["outgoing"], candidate["incoming"]]}


def preview_path(config, job_id, candidate_id):
    return config.paths.assets_dir / "lab" / "renders" / f"{job_id}-{candidate_id}-proposed" / "output.mp4"


def preview_available(config, db, store, job_id, candidate):
    """Reusing a cached audition requires the same current render recipe."""
    if not candidate.get("preview_ready"):
        return False
    output = preview_path(config, job_id, candidate["id"])
    try:
        if not output.is_file():
            return False
        boundary = candidate.get("boundary_checks", {}).get("proposed", {})
        checks = boundary.get("checks", [])
        if (boundary.get("profile") != "boundary-image-mae-v1" or boundary.get("passed") is not True
                or len(checks) != 2 or any(check.get("within_tolerance") is not True
                    or abs(check.get("source_pts", float("inf")) - timestamp) > 1e-6
                    for check, timestamp in zip(checks, (candidate["reference_frame_pts"], candidate["candidate_frame_pts"])))):
            return False
        with output.open("rb") as handle:
            if hashlib.file_digest(handle, "sha256").hexdigest() != candidate.get("preview_sha256"):
                return False
        from pipeline.ingest.probe import _content_hash
        for clip in (candidate["outgoing"], candidate["incoming"]):
            if _content_hash(Path(cohort.resolve_film(db, clip["film_id"])["path"])) != clip["film_id"]:
                return False
        expected = render_manifest(preview_document(candidate), db, store, mode="preview",
                                   experiment_id="visual-rhymes")
        return json.loads(output.with_name("manifest.json").read_text(encoding="utf-8")) == expected
    except (OSError, ValueError, KeyError):
        return False


def _thumbnail(config, db, job_id, candidate, cancelled):
    source = cohort.unit(db, candidate["incoming"]["unit_id"])
    path = Path(cohort.resolve_film(db, source["film_id"])["path"])
    frame = media.at(path, candidate["candidate_frame_pts"], source["t_start"], source["t_end"], cancelled)
    directory = config.paths.assets_dir / "matching" / "results" / job_id
    directory.mkdir(parents=True, exist_ok=True)
    frame.image.thumbnail((640, 640))
    frame.image.save(directory / f"{candidate['id']}.jpg", quality=88)
    candidate["frame_url"] = f"/matching/searches/{job_id}/candidates/{candidate['id']}/frame"


def _render(config, db, store, job_id, candidate, progress, cancelled):
    if cancelled():
        raise JobCancelled("Match search cancelled")

    def render_progress(message):
        progress("Assembling the cut preview" if message == "Assembling music and picture" else message)

    render_candidate(job_id, preview_document(candidate), candidate, config, db, store, render_progress, cancelled)
    if candidate.get("preview_ready"):
        with preview_path(config, job_id, candidate["id"]).open("rb") as handle:
            candidate["preview_sha256"] = hashlib.file_digest(handle, "sha256").hexdigest()
        candidate["preview_url"] = f"/matching/searches/{job_id}/candidates/{candidate['id']}/preview"


def run(job, config, db, store, progress, cancelled):
    from pipeline.matching.search import find
    started = time.monotonic()
    proposal = job["snapshot"]["match_search"]
    cached, first_playable, render_count = {}, None, 0
    partial = {"candidates": [], "reference": proposal["reference"],
               "cohort_id": proposal["request"]["cohort_id"],
               "profile_id": proposal["request"]["profile_id"]}

    def render(candidate):
        nonlocal first_playable, render_count
        render_count += 1
        try:
            _render(config, db, store, job["id"], candidate, progress, cancelled)
        except JobCancelled:
            raise
        except (ValueError, OSError, RuntimeError) as exc:
            candidate.update(preview_ready=False, preview_warning=str(exc))
        if candidate.get("preview_ready") and first_playable is None:
            first_playable = time.monotonic() - started
            partial["first_playable_seconds"] = first_playable

    def discovered(candidate):
        if cancelled():
            raise JobCancelled("Match search cancelled")
        candidate = deepcopy(candidate)
        _thumbnail(config, db, job["id"], candidate, cancelled)
        cached[candidate["id"]] = candidate
        partial["candidates"] = sorted(cached.values(), key=lambda row: (-row["score"], row["id"]))
        store.publish_match_progress(job["id"], partial)
        # Spend at most one extra encode to make a verified cut available while
        # remaining model work continues. Final ranking may move this candidate.
        if len(cached) == 1:
            progress("Preparing the first cut while other matches are checked")
            render(candidate)
            store.publish_match_progress(job["id"], partial)

    result = find(config, db, proposal["request"], progress, cancelled, on_candidate=discovered)
    result.setdefault("reference", proposal["reference"])
    for candidate in result["candidates"]:
        previous = cached.get(candidate["id"])
        if previous and same_pair(previous, candidate):
            candidate.update({key: previous[key] for key in (
                "frame_url", "preview_ready", "preview_url", "preview_sha256", "preview_warning",
                "boundary_checks", "original_is_proposed") if key in previous})
    store.publish_match_progress(job["id"], result)
    for index, candidate in enumerate(result["candidates"]):
        if cancelled():
            raise JobCancelled("Match search cancelled")
        if not candidate.get("frame_url"):
            _thumbnail(config, db, job["id"], candidate, cancelled)
        if index < 3 and not candidate.get("preview_ready"):
            progress(f"Preparing cut {index + 1} of {min(3, len(result['candidates']))}")
            render(candidate)
        store.publish_match_progress(job["id"], result)
    result["elapsed_seconds"] = time.monotonic() - started
    result["first_playable_seconds"] = first_playable
    result["top_three_playable_seconds"] = result["elapsed_seconds"] if len(result["candidates"]) >= 3 and all(
        candidate.get("preview_ready") for candidate in result["candidates"][:3]) else None
    result["eager_preview_count"] = render_count
    return result


def preview_run(job, config, db, store, progress, cancelled):
    proposal = job["snapshot"]["match_search_preview"]

    def stopped():
        parent = store.get_job(proposal["search_id"], private=True)
        return cancelled() or parent["cancel_requested"] or parent["status"] != "completed"

    if stopped():
        raise JobCancelled("The parent match search is no longer available")
    candidate = deepcopy(proposal["candidate"])
    candidate.pop("preview_warning", None)
    candidate.pop("boundary_checks", None)
    candidate.pop("preview_url", None)
    candidate["preview_ready"] = False
    _render(config, db, store, job["id"], candidate, progress, stopped)
    if stopped():
        raise JobCancelled("Match search cancelled")
    return {"search_id": proposal["search_id"], "candidate": candidate}
