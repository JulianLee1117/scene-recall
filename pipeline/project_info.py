"""Public, read-only summary of the API's already-loaded configuration."""
from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException, Request, Response

if TYPE_CHECKING:
    from pipeline.config import Config


router = APIRouter(prefix="/project", tags=["project"])


def runtime_info(config: Config) -> dict:
    """Keep this allowlist explicit: new configuration fields stay private."""
    models, thresholds = config.models, config.thresholds
    retrieval, lab = config.retrieval, config.lab
    return {
        "schema_version": 1,
        "scope": "Loaded API configuration; not per-film provenance or feature readiness",
        "models": {
            "visual_encoder": models.visual_encoder,
            "text_encoder": models.text_encoder,
            "annotator": models.annotator,
            "annotator_provider": models.annotator_provider,
            "annotator_image_detail": models.annotator_image_detail,
            "annotator_reasoning_effort": models.annotator_reasoning_effort,
            "whisper": models.whisper,
        },
        "thresholds": {
            "subsegment_min_duration": thresholds.subsegment_min_duration,
            "flash_min_duration": thresholds.flash_min_duration,
            "keyframe_short_shot_s": thresholds.keyframe_short_shot_s,
        },
        "retrieval": {
            "weights": {
                "img": retrieval.weights.img,
                "txt": retrieval.weights.txt,
                "lex": retrieval.weights.lex,
            },
            "candidate_limit": retrieval.candidate_limit,
            "result_window": retrieval.result_window,
            "max_result_limit": retrieval.max_result_limit,
            "diversity": {
                "page_size": retrieval.diversity.page_size,
                "film_results_per_page_target": retrieval.diversity.film_results_per_page_target,
                "film_repeat_rank_strength": retrieval.diversity.film_repeat_rank_strength,
            },
        },
        "ingest": {"annotation_concurrency": config.ingest.annotation_concurrency},
        "lab": {
            "music_provider": lab.music_provider,
            "music_model": lab.music_model,
            "planner_model": lab.planner_model,
            "music_prompt_version": lab.music_prompt_version,
            "planner_prompt_version": lab.planner_prompt_version,
            "context_profile": lab.context_profile,
            "footage_inspection": lab.footage_inspection,
            "beat_checkpoint_configured": bool(lab.beat_checkpoint is not None and lab.beat_checkpoint_sha256),
            "beat_device": lab.beat_device,
        },
    }


@router.get("/info")
def project_info(request: Request, response: Response) -> dict:
    config = getattr(request.app.state, "config", None)
    if config is None:
        raise HTTPException(status_code=503, detail="Project configuration is not loaded.")
    response.headers["Cache-Control"] = "no-store"
    return runtime_info(config)
