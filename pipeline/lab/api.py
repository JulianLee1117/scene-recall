"""Lab HTTP boundaries; imports no optional model implementation."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse

from pipeline.lab.media import import_track, render_manifest, validate_sources
from pipeline.lab.models import JobRequest, NextSceneAdjust, NextSceneApply, ProjectCreate, ProjectDocument, ProjectUpdate, RestoreRequest
from pipeline.lab.registry import EXPERIMENTS
from pipeline.lab.store import ActiveProjectJobs, DuplicateJob, RevisionConflict

router = APIRouter(prefix="/lab", tags=["lab"])
MAX_TRACK_BYTES = 250 * 1024 * 1024


@router.get("/workers")
def workers(request: Request):
    from pipeline.lab.worker_control import worker_status
    return worker_status(_store(request))


def _store(request):
    return request.app.state.lab


def _call(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except KeyError as exc:
        raise HTTPException(404, str(exc).strip("'")) from None
    except (ActiveProjectJobs, RevisionConflict, DuplicateJob) as exc:
        raise HTTPException(409, str(exc)) from None
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None


@router.get("/experiments")
def experiments():
    return {"experiments": EXPERIMENTS}


@router.get("/experiments/{experiment_id}/draft")
def experiment_draft(experiment_id: str):
    """Canonical editor defaults without creating a durable project or job."""
    if experiment_id not in {item["id"] for item in EXPERIMENTS}:
        raise HTTPException(404, "Experiment not found")
    return {"id": "", "revision": 0, "experiment_id": experiment_id,
            "name": "Untitled music edit" if experiment_id == "music-sketch" else "Untitled Match Cuts",
            "document": ProjectDocument().model_dump(mode="json"), "created_at": 0, "updated_at": 0}


@router.get("/music/search-capabilities")
def music_search_capabilities(request: Request):
    from pipeline.search.capabilities import search_capabilities

    return search_capabilities(request.app.state.config, request.app.state.db)


@router.get("/projects")
def projects(request: Request):
    return {"projects": _store(request).list_projects()}


@router.post("/projects")
def create_project(body: ProjectCreate, request: Request):
    store = _store(request)
    document = body.document.model_dump(mode="json") if body.document is not None else None
    if document is not None:
        # The first save has no prior durable selections to grandfather in.
        _call(validate_sources, document, request.app.state.db, store)
    return _call(store.create_project, body.name, body.experiment_id, document)


@router.get("/projects/{project_id}")
def get_project(project_id: str, request: Request):
    return _call(_store(request).get_project, project_id)


@router.get("/projects/{project_id}/timeline.otio")
def export_project_timeline(project_id: str, request: Request):
    """The saved edit as an OpenTimelineIO timeline on the original media (for DaVinci Resolve)."""
    import json
    import re

    from fastapi.responses import Response

    from pipeline.lab.otio_export import export_timeline

    project = _call(_store(request).get_project, project_id)
    timeline = _call(export_timeline, project["document"], request.app.state.db, _store(request), name=project["name"])
    filename = (re.sub(r"[^A-Za-z0-9._-]+", "-", project["name"]).strip("-") or "scene-recall-edit") + ".otio"
    return Response(json.dumps(timeline, indent=1), media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.delete("/projects/{project_id}")
def delete_project(project_id: str, request: Request, base_revision: int = Query(..., ge=1)):
    return _call(_store(request).delete_project, project_id, base_revision)


@router.put("/projects/{project_id}")
def update_project(project_id: str, body: ProjectUpdate, request: Request):
    store = _store(request)
    current = _call(store.get_project, project_id)
    if current["revision"] != body.base_revision:
        raise HTTPException(409, "Project changed; reload before saving")
    document = body.document.model_dump(mode="json")
    # Unchanged durable selections survive a temporarily missing index/source.
    # Newly supplied or changed anchors must resolve against the actual film.
    existing = {(clip["film_id"], clip["source_start"], clip["source_end"])
                for clip in current["document"]["clips"]}
    changed = {clip["id"] for clip in document["clips"] if (clip["film_id"], clip["source_start"], clip["source_end"]) not in existing}
    existing_dialogue = {(clip["film_id"], clip["source_start"], clip["source_end"])
                         for clip in current["document"].get("dialogue_clips", [])}
    changed_dialogue = {clip["id"] for clip in document["dialogue_clips"]
                        if (clip["film_id"], clip["source_start"], clip["source_end"]) not in existing_dialogue}
    _call(validate_sources, document, request.app.state.db, store, clip_ids=changed, dialogue_ids=changed_dialogue)
    return _call(store.update_project, project_id, body.base_revision, document, body.name)


@router.get("/projects/{project_id}/revisions")
def revisions(project_id: str, request: Request):
    return {"revisions": _call(_store(request).revisions, project_id)}


@router.post("/projects/{project_id}/restore")
def restore(project_id: str, body: RestoreRequest, request: Request):
    return _call(_store(request).restore, project_id, body.base_revision, body.revision)


def _uploaded_track(store, file):
    """Import original audio independently of whether an edit has been saved."""
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=store.root / "tracks", suffix=".upload", delete=False) as target:
            temporary = Path(target.name)
            total = 0
            while chunk := file.file.read(1024 * 1024):
                total += len(chunk)
                if total > MAX_TRACK_BYTES:
                    raise HTTPException(413, "Track must be at most 250 MiB")
                target.write(chunk)
        if not total:
            raise HTTPException(422, "Track is empty")
        return _call(import_track, store, temporary, file.filename or "track")
    finally:
        if temporary and temporary.exists():
            temporary.unlink()
        file.file.close()


@router.post("/tracks")
def import_audio(request: Request, file: UploadFile = File(...)):
    track = _uploaded_track(_store(request), file)
    return {key: track[key] for key in ("id", "name", "duration")}


@router.post("/projects/{project_id}/track")
def upload_track(project_id: str, request: Request, base_revision: int = Form(...), file: UploadFile = File(...)):
    store = _store(request)
    current = _call(store.get_project, project_id)
    if current["revision"] != base_revision:
        raise HTTPException(409, "Project changed; reload before importing music")
    if current["experiment_id"] != "music-sketch":
        raise HTTPException(422, "Music import belongs to AI Music Video")
    track = _uploaded_track(store, file)
    passage_end = min(30, track["duration"])
    from pipeline.lab.editorial_context import reset_track_ranges
    document = {**reset_track_ranges(current["document"]), "track": {key: track[key] for key in ("id", "name", "duration")},
                "passage": {"start": 0, "end": passage_end}, "analysis": None, "rhythm": None, "music_timeline": None, "direction_plan": None,
                "song_context": None, "visual_plan": None,
                # A deliberate music import starts a new arrangement. Old
                # sources, locks and timing remain in the prior revision.
                "clips": [], "dialogue_clips": [],
                "audio_fade_in_seconds": min(current["document"].get("audio_fade_in_seconds", 0), passage_end),
                "audio_fade_out_seconds": min(current["document"].get("audio_fade_out_seconds", 0), passage_end)}
    return _call(store.update_project, project_id, base_revision, document)


@router.get("/tracks/{track_id}/audio")
def track_audio(track_id: str, request: Request):
    track = _call(_store(request).get_track, track_id)
    path = Path(track["path"])
    if not path.is_file():
        raise HTTPException(404, "Imported music is unavailable on disk")
    return FileResponse(path, filename=track["name"], content_disposition_type="inline")


@router.get("/dialogue-audio")
def dialogue_audio(request: Request, film_id: str, source_start: float, source_end: float,
                   source_audio_mode: Literal["original", "voice_focus"] = "original"):
    """Prepare once, then redirect byte ranges to an immutable PCM representation."""
    from pipeline.lab.dialogue_assets import DialogueAudioBusy, prepare_dialogue_audio

    try:
        asset = prepare_dialogue_audio(request.app.state.config, request.app.state.db,
                                       {"film_id": film_id, "source_start": source_start, "source_end": source_end,
                                        "source_audio_mode": source_audio_mode})
    except DialogueAudioBusy as exc:
        raise HTTPException(503, str(exc), headers={"Retry-After": "2"}) from None
    except (ValueError, KeyError) as exc:
        raise HTTPException(422, str(exc).strip("'")) from None
    # Relative, so a path prefix the browser reached the API through (the web app's /api proxy) is kept.
    return RedirectResponse(f"dialogue-audio/assets/{asset['asset_id']}.wav", status_code=307,
                            headers={"Cache-Control": "no-store", "X-Dialogue-Channel-Method": asset["channel_method"]})


@router.get("/dialogue-audio/assets/{asset_id}.wav")
def dialogue_audio_asset(asset_id: str, request: Request):
    from pipeline.lab.dialogue_assets import DialogueAudioBusy, audio_asset_path

    try:
        path = audio_asset_path(request.app.state.config, asset_id)
    except DialogueAudioBusy as exc:
        raise HTTPException(503, str(exc), headers={"Retry-After": "2"}) from None
    except KeyError as exc:
        raise HTTPException(404, str(exc).strip("'")) from None
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    return FileResponse(path, media_type="audio/wav",
                        headers={"Cache-Control": "private, max-age=31536000, immutable", "ETag": f'"{asset_id}"'})


@router.post("/projects/{project_id}/jobs")
def start_job(project_id: str, body: JobRequest, request: Request):
    store = _store(request)
    project = _call(store.get_project, project_id)
    if project["revision"] != body.base_revision:
        raise HTTPException(409, "Project changed; save before starting the job")
    if body.kind == "next-scene":
        if project["experiment_id"] != "music-sketch":
            raise HTTPException(422, "Next-scene suggestions belong to AI Music Video")
        from pipeline.lab.next_scene import validate_request
        options = body.next_scene.model_dump(mode="json")
        scope = _call(validate_request, project["document"], options)
        if options["inspect_frames"] and request.app.state.config.lab.music_provider != "openai":
            raise HTTPException(422, "Sampled-frame inspection currently requires the OpenAI music provider")
        _call(validate_sources, project["document"], request.app.state.db, store, require_media=True,
              clip_ids={scope["anchor"]["id"]})
        return _call(store.enqueue, body.kind, project_id, body.base_revision, next_scene=options)
    if body.kind == "generate":
        if project["experiment_id"] != "music-sketch":
            raise HTTPException(422, "Music edit generation belongs to AI Music Video")
        from pipeline.lab.generation import validate_generate_request
        options = body.generate.model_dump() if body.generate else {}
        generation_mode, targets = _call(validate_generate_request, project["document"], options, body.slot_ids)
        timeline = project["document"].get("music_timeline")
        if generation_mode != "fill" or not timeline or targets:
            placed = {slot["clip_id"] for slot in timeline["slots"] if slot.get("clip_id")} if timeline else None
            _call(validate_sources, project["document"], request.app.state.db, store, require_media=True, clip_ids=placed)
    elif body.kind == "plan":
        if project["experiment_id"] != "music-sketch":
            raise HTTPException(422, "Shot direction planning belongs to AI Music Video")
        from pipeline.lab.timeline import plan_targets
        _call(plan_targets, project["document"], body.slot_ids)
    elif body.kind in {"rhythm", "analyze", "draft"}:
        if project["experiment_id"] != "music-sketch":
            raise HTTPException(422, "Automatic matching remains a gated research experiment")
        if not project["document"]["track"]:
            raise HTTPException(422, "Import music before starting analysis or drafting")
        if body.replan_timing:
            from pipeline.lab.timeline import require_replan_unlocked
            _call(require_replan_unlocked, project["document"])
        timeline = project["document"].get("music_timeline")
        placed = {slot["clip_id"] for slot in timeline["slots"] if slot.get("clip_id")} if timeline else None
        if body.replan_timing:
            placed = set()  # Old selections become unplaced evidence; only the music is needed.
        _call(validate_sources, project["document"], request.app.state.db, store, require_media=True, clip_ids=placed)
        if body.slot_ids is not None:
            from pipeline.lab.timeline import draft_targets
            _call(draft_targets, project["document"], body.slot_ids)
    elif body.kind == "render":
        _call(render_manifest, project["document"], request.app.state.db, store, mode=body.mode, experiment_id=project["experiment_id"])
    return _call(store.enqueue, body.kind, project_id, body.base_revision, mode=body.mode, slot_ids=body.slot_ids,
                 replan_timing=body.replan_timing, generate=body.generate.model_dump() if body.generate else None,
                 suggest_only=body.suggest_only)


def _next_scene_candidate(request, job_id, candidate_id):
    job = _call(_store(request).get_job, job_id, private=True)
    if job["kind"] not in {"next-scene", "next-scene-preview"} or job["status"] != "completed":
        raise HTTPException(409, "Next-scene suggestions have not completed")
    result = job["result"] or {}
    candidates = result.get("candidates", []) if job["kind"] == "next-scene" else [result.get("candidate")]
    candidate = next((item for item in candidates if item and item["id"] == candidate_id), None)
    if candidate is None:
        raise HTTPException(404, "Next-scene suggestion not found")
    return job, candidate


def _next_scene_preview_path(request, job_id, candidate_id):
    # Both identities come from the server's saved ledger, never user paths.
    return request.app.state.config.paths.assets_dir / "lab" / "renders" / f"{job_id}-{candidate_id}" / "output.mp4"


@router.get("/jobs/{job_id}/next-scenes/{candidate_id}/preview")
def next_scene_preview(job_id: str, candidate_id: str, request: Request):
    job, candidate = _next_scene_candidate(request, job_id, candidate_id)
    path = _next_scene_preview_path(request, job["id"], candidate["id"])
    if not candidate.get("preview_ready") or not path.is_file():
        raise HTTPException(409, "Prepare this transition preview before playing it")
    return FileResponse(path, media_type="video/mp4")


@router.post("/jobs/{job_id}/next-scenes/{candidate_id}/preview")
def prepare_next_scene_preview(job_id: str, candidate_id: str, body: NextSceneAdjust, request: Request):
    job, candidate = _next_scene_candidate(request, job_id, candidate_id)
    if job["kind"] != "next-scene":
        raise HTTPException(422, "Adjust a suggestion from its original next-scene search")
    from pipeline.lab.next_scene import adjusted_candidate
    adjustments = body.adjustment_payload()
    _call(adjusted_candidate, job["document"], job["result"]["scope"], candidate, request.app.state.db, adjustments)
    store = _store(request)
    current = _call(store.get_project, job["project_id"])
    return _call(store.enqueue, "next-scene-preview", current["id"], current["revision"],
                 next_scene_preview={"job_id": job["id"], "candidate_id": candidate["id"], "adjustments": adjustments})


@router.post("/jobs/{job_id}/apply-next-scene")
def apply_next_scene(job_id: str, body: NextSceneApply, request: Request):
    job, candidate = _next_scene_candidate(request, job_id, body.candidate_id)
    if job["kind"] != "next-scene":
        raise HTTPException(422, "Apply a suggestion from its original next-scene search")
    store = _store(request)
    current = _call(store.get_project, job["project_id"])
    if current["revision"] != body.base_revision or current["revision"] != job["base_revision"]:
        raise HTTPException(409, "The edit changed after this search; find next scenes again")
    from pipeline.lab.next_scene import adjusted_candidate, proposal_document
    adjustments = body.adjustment_payload()
    scope = job["result"]["scope"]
    adjusted = _call(adjusted_candidate, current["document"], scope, candidate, request.app.state.db, adjustments)
    preview_job, preview_candidate = job, candidate
    if body.preview_job_id:
        preview_job, preview_candidate = _next_scene_candidate(request, body.preview_job_id, candidate["id"])
        if (preview_job["kind"] != "next-scene-preview" or preview_job["project_id"] != current["id"]
                or (preview_job["result"] or {}).get("next_scene_job_id") != job["id"]):
            raise HTTPException(409, "This preview belongs to another suggestion")
    if any(adjusted[key] != preview_candidate[key] for key in ("cut", "outgoing", "incoming")):
        raise HTTPException(409, "Prepare the adjusted preview before using this scene")
    if not preview_candidate.get("preview_ready") or not _next_scene_preview_path(request, preview_job["id"], candidate["id"]).is_file():
        raise HTTPException(409, "Prepare a playable preview before using this scene")
    document = _call(proposal_document, current["document"], scope, candidate, request.app.state.db, adjustments)
    pair_ids = {adjusted["outgoing"]["id"], adjusted["incoming"]["id"]}
    _call(validate_sources, document, request.app.state.db, store, require_media=True, clip_ids=pair_ids)
    from pipeline.lab.next_scene_media import preview_manifest
    current_manifest = _call(preview_manifest, document, scope["anchor_slot_id"], request.app.state.db, store)
    if current_manifest != preview_candidate.get("manifest"):
        raise HTTPException(409, "The source or preview settings changed; prepare the transition preview again")
    return _call(store.update_project, current["id"], body.base_revision, document, current["name"])


@router.get("/jobs/{job_id}")
def get_job(job_id: str, request: Request):
    return _call(_store(request).get_job, job_id)


@router.get("/projects/{project_id}/jobs")
def project_jobs(project_id: str, request: Request):
    return {"jobs": _call(_store(request).project_jobs, project_id)}


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str, request: Request):
    return _call(_store(request).cancel, job_id)


@router.get("/jobs/{job_id}/output")
def job_output(job_id: str, request: Request, download: bool = False):
    job = _call(_store(request).get_job, job_id)
    if job["kind"] != "render" or job["status"] != "completed":
        raise HTTPException(409, "The render is not complete")
    # Use the database identity, never any client-supplied path.
    path = request.app.state.config.paths.assets_dir / "lab" / "renders" / job["id"] / "output.mp4"
    if not path.is_file():
        raise HTTPException(404, "Render cache is unavailable; render this revision again")
    return FileResponse(path, media_type="video/mp4", filename="scene-recall-sketch.mp4",
                        content_disposition_type="attachment" if download else "inline")
