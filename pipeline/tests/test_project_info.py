"""Project information reads only the loaded, explicitly public configuration."""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from pipeline.project_info import router, runtime_info


def test_runtime_fields_follow_loaded_configuration(config):
    config.models.visual_encoder = "custom-visual-profile"
    config.models.text_encoder = "custom-text-profile"
    config.models.annotator = "custom-annotation-model"
    config.models.annotator_provider = "openai"
    config.models.annotator_image_detail = "high"
    config.models.annotator_reasoning_effort = "low"
    config.models.whisper = "small"
    config.thresholds.subsegment_min_duration = 31
    config.thresholds.flash_min_duration = .3
    config.thresholds.keyframe_short_shot_s = 1.4
    config.retrieval.weights.img = .6
    config.retrieval.weights.txt = .3
    config.retrieval.weights.lex = .1
    config.retrieval.candidate_limit = 321
    config.retrieval.result_window = 64
    config.retrieval.max_result_limit = 250
    config.retrieval.diversity.page_size = 16
    config.retrieval.diversity.film_results_per_page_target = 5
    config.retrieval.diversity.film_repeat_rank_strength = 18
    config.ingest.annotation_concurrency = 3
    config.lab.music_provider = "gemini"
    config.lab.music_model = "gemini-listening"
    config.lab.planner_model = "gemini-planner"
    config.lab.music_prompt_version = "listening-custom"
    config.lab.planner_prompt_version = "planner-custom"
    config.lab.context_profile = "context-pilot"
    config.lab.footage_inspection = True
    config.lab.beat_device = "cuda"
    assert runtime_info(config) == {
        "schema_version": 1,
        "scope": "Loaded API configuration; not per-film provenance or feature readiness",
        "models": {
            "visual_encoder": "custom-visual-profile", "text_encoder": "custom-text-profile",
            "annotator": "custom-annotation-model", "annotator_provider": "openai",
            "annotator_image_detail": "high", "annotator_reasoning_effort": "low", "whisper": "small",
        },
        "thresholds": {"subsegment_min_duration": 31, "flash_min_duration": .3, "keyframe_short_shot_s": 1.4},
        "retrieval": {
            "weights": {"img": .6, "txt": .3, "lex": .1},
            "candidate_limit": 321, "result_window": 64, "max_result_limit": 250,
            "diversity": {"page_size": 16, "film_results_per_page_target": 5, "film_repeat_rank_strength": 18},
        },
        "ingest": {"annotation_concurrency": 3},
        "lab": {
            "music_provider": "gemini", "music_model": "gemini-listening", "planner_model": "gemini-planner",
            "music_prompt_version": "listening-custom", "planner_prompt_version": "planner-custom",
            "context_profile": "context-pilot", "footage_inspection": True,
            "beat_checkpoint_configured": False, "beat_device": "cuda",
        },
    }


def test_allowlist_excludes_paths_secrets_and_future_fields(config, monkeypatch):
    secret = "private-token-never-public"
    monkeypatch.setenv("OPENAI_API_KEY", secret)
    for group in (config, config.paths, config.models, config.thresholds,
                  config.retrieval, config.retrieval.weights, config.retrieval.diversity,
                  config.ingest, config.lab):
        group.future_secret = secret
    config.lab.beat_checkpoint = Path("Z:/private/offline-checkpoint.safetensors")
    config.lab.beat_checkpoint_sha256 = "e" * 64
    public = runtime_info(config)
    encoded = json.dumps(public)
    assert public["lab"]["beat_checkpoint_configured"] is True
    for private in (secret, "paths", "future_secret", "offline-checkpoint", "Z:",
                    str(config.paths.assets_dir), str(config.paths.films_dir), "e" * 64):
        assert private not in encoded


def test_route_needs_no_database_or_files_and_reads_current_app_config(config, monkeypatch):
    class NoPathAccess:
        def __getattribute__(self, name):
            raise AssertionError(f"Unexpected path access: {name}")

    def no_reload(*args, **kwargs):
        raise AssertionError("The endpoint must use loaded configuration")

    config.paths = NoPathAccess()
    config.lab.beat_checkpoint = Path("Z:/unmounted/not-present.safetensors")
    config.lab.beat_checkpoint_sha256 = "e" * 64
    monkeypatch.setattr("pipeline.config.load_config", no_reload)
    app = FastAPI()  # no database, lifecycle hooks, model loaders, or download clients
    app.state.config = config
    app.include_router(router)
    with TestClient(app) as client:
        response = client.get("/project/info")
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert response.json()["lab"]["beat_checkpoint_configured"] is True
        config.ingest.annotation_concurrency = 2
        config.lab.context_profile = "changed-profile"
        second = client.get("/project/info").json()
        assert second["ingest"]["annotation_concurrency"] == 2
        assert second["lab"]["context_profile"] == "changed-profile"
        assert client.post("/project/info", json={"ingest": {"annotation_concurrency": 99}}).status_code == 405
        assert config.ingest.annotation_concurrency == 2


@pytest.mark.parametrize("checkpoint,checksum,expected", [(None, None, False), ("missing-file", None, False), ("missing-file", "e" * 64, True)])
def test_checkpoint_flag_is_configuration_only(config, checkpoint, checksum, expected):
    config.lab.beat_checkpoint = Path(checkpoint) if checkpoint else None
    config.lab.beat_checkpoint_sha256 = checksum
    assert runtime_info(config)["lab"]["beat_checkpoint_configured"] is expected


def test_route_reports_missing_loaded_configuration_without_details():
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        response = client.get("/project/info")
    assert response.status_code == 503
    assert response.json() == {"detail": "Project configuration is not loaded."}
