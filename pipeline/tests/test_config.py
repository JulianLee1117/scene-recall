"""Tests for pipeline/config.py — written BEFORE implementation (TDD)."""
import os
import textwrap
from pathlib import Path

import pytest
import yaml


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_config(tmp_path: Path, content: str) -> Path:
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(textwrap.dedent(content))
    return cfg_file


MINIMAL_CONFIG = """
    paths:
      films_dir: /tmp/films
      assets_dir: /tmp/assets

    models:
      visual_encoder: pe_core_l14
      text_encoder: qwen3-embedding-0.6b
      annotator: gemini-3-flash
      router: qwen3:8b

    thresholds:
      shot_dedup_cosine: 0.12
      scene_visual_sim: 0.75
      scene_dialogue_gap: 2.5
      scene_max_duration: 300
      subsegment_min_duration: 20

    retrieval:
      weights:
        img: 0.4
        txt: 0.4
        lex: 0.2
      diversity:
        page_size: 12
        film_results_per_page_target: 4
      rerank_enabled: false

    scoring:
      duration_weight: 0.05
      motion_weight: 0.05
      frame_worthiness_weight: 0.10
"""


# ---------------------------------------------------------------------------
# load_config — basic loading
# ---------------------------------------------------------------------------

def test_load_config_returns_config_object(tmp_path):
    """load_config(path) returns a Config object (not a dict)."""
    from pipeline.config import load_config, Config

    cfg_file = _write_config(tmp_path, MINIMAL_CONFIG)
    cfg = load_config(cfg_file)

    assert isinstance(cfg, Config)


def test_load_config_paths(tmp_path):
    """Config.paths has films_dir and assets_dir as Path objects."""
    from pipeline.config import load_config

    cfg_file = _write_config(tmp_path, MINIMAL_CONFIG)
    cfg = load_config(cfg_file)

    assert isinstance(cfg.paths.films_dir, Path)
    assert isinstance(cfg.paths.assets_dir, Path)
    assert cfg.paths.films_dir == Path("/tmp/films")
    assert cfg.paths.assets_dir == Path("/tmp/assets")


def test_load_config_models(tmp_path):
    """Config.models carries all four model config strings."""
    from pipeline.config import load_config

    cfg_file = _write_config(tmp_path, MINIMAL_CONFIG)
    cfg = load_config(cfg_file)

    assert cfg.models.visual_encoder == "pe_core_l14"
    assert cfg.models.text_encoder == "qwen3-embedding-0.6b"
    assert cfg.models.annotator == "gemini-3-flash"
    assert cfg.models.annotator_provider == "gemini"
    assert cfg.models.annotator_image_detail == "low"
    assert cfg.models.annotator_reasoning_effort == "none"
    assert cfg.models.router == "qwen3:8b"


def test_load_config_explicit_openai_annotator(tmp_path):
    """OpenAI annotation settings are loaded and normalized."""
    raw = yaml.safe_load(textwrap.dedent(MINIMAL_CONFIG))
    raw["models"].update({
        "annotator_provider": "OPENAI",
        "annotator": "gpt-5.6-luna",
        "annotator_image_detail": "HIGH",
        "annotator_reasoning_effort": "NONE",
    })
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(yaml.safe_dump(raw), encoding="utf-8")

    from pipeline.config import load_config

    cfg = load_config(cfg_file)

    assert cfg.models.annotator_provider == "openai"
    assert cfg.models.annotator == "gpt-5.6-luna"
    assert cfg.models.annotator_image_detail == "high"
    assert cfg.models.annotator_reasoning_effort == "none"


def test_load_config_infers_openai_from_model(tmp_path):
    """Legacy configs without a provider infer OpenAI for non-Gemini models."""
    raw = yaml.safe_load(textwrap.dedent(MINIMAL_CONFIG))
    raw["models"]["annotator"] = "gpt-5.6-luna"
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(yaml.safe_dump(raw), encoding="utf-8")

    from pipeline.config import load_config

    assert load_config(cfg_file).models.annotator_provider == "openai"


def test_load_config_rejects_unknown_annotator_provider(tmp_path):
    """Provider typos fail before a long ingest can begin."""
    raw = yaml.safe_load(textwrap.dedent(MINIMAL_CONFIG))
    raw["models"]["annotator_provider"] = "unknown"
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(yaml.safe_dump(raw), encoding="utf-8")

    from pipeline.config import load_config

    with pytest.raises(ValueError, match="annotator_provider"):
        load_config(cfg_file)


def test_load_config_thresholds(tmp_path):
    """Config.thresholds carries all five threshold values."""
    from pipeline.config import load_config

    cfg_file = _write_config(tmp_path, MINIMAL_CONFIG)
    cfg = load_config(cfg_file)

    assert cfg.thresholds.shot_dedup_cosine == pytest.approx(0.12)
    assert cfg.thresholds.scene_visual_sim == pytest.approx(0.75)
    assert cfg.thresholds.scene_dialogue_gap == pytest.approx(2.5)
    assert cfg.thresholds.scene_max_duration == 300
    assert cfg.thresholds.subsegment_min_duration == 20


def test_load_config_retrieval(tmp_path):
    """Config.retrieval has weights, diversity, and rerank_enabled."""
    from pipeline.config import load_config

    cfg_file = _write_config(tmp_path, MINIMAL_CONFIG)
    cfg = load_config(cfg_file)

    assert cfg.retrieval.weights.img == pytest.approx(0.4)
    assert cfg.retrieval.weights.txt == pytest.approx(0.4)
    assert cfg.retrieval.weights.lex == pytest.approx(0.2)
    assert cfg.retrieval.candidate_limit == 200
    assert cfg.retrieval.result_window == 48
    assert cfg.retrieval.max_result_limit == 100
    assert cfg.retrieval.diversity.page_size == 12
    assert cfg.retrieval.diversity.film_results_per_page_target == 4
    assert cfg.retrieval.rerank_enabled is False


def test_load_config_migrates_legacy_hard_film_cap(tmp_path):
    """Older personal configs become a soft page target, not a hard cap."""
    raw = yaml.safe_load(textwrap.dedent(MINIMAL_CONFIG))
    raw["retrieval"]["diversity"] = {
        "max_per_scene": 2,
        "max_per_film": 3,
    }
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(yaml.safe_dump(raw), encoding="utf-8")

    from pipeline.config import load_config

    cfg = load_config(cfg_file)

    assert cfg.retrieval.diversity.page_size == 12
    assert cfg.retrieval.diversity.film_results_per_page_target == 3


def test_load_config_scoring(tmp_path):
    """Config.scoring carries all three scoring weights."""
    from pipeline.config import load_config

    cfg_file = _write_config(tmp_path, MINIMAL_CONFIG)
    cfg = load_config(cfg_file)

    assert cfg.scoring.duration_weight == pytest.approx(0.05)
    assert cfg.scoring.motion_weight == pytest.approx(0.05)
    assert cfg.scoring.frame_worthiness_weight == pytest.approx(0.10)


# ---------------------------------------------------------------------------
# load_config — default path resolution
# ---------------------------------------------------------------------------

def test_load_config_uses_env_var(tmp_path, monkeypatch):
    """When no path is passed, load_config reads CINEMA_CONFIG env var."""
    from pipeline.config import load_config

    cfg_file = _write_config(tmp_path, MINIMAL_CONFIG)
    monkeypatch.setenv("CINEMA_CONFIG", str(cfg_file))

    cfg = load_config()  # no explicit path
    assert cfg.models.annotator == "gemini-3-flash"


def test_load_config_falls_back_to_cwd(tmp_path, monkeypatch):
    """Without CINEMA_CONFIG set, load_config looks for ./config.yaml."""
    from pipeline.config import load_config

    cfg_file = _write_config(tmp_path, MINIMAL_CONFIG)
    # We rename it to config.yaml inside tmp_path and cd there
    monkeypatch.delenv("CINEMA_CONFIG", raising=False)
    monkeypatch.chdir(tmp_path)

    cfg = load_config()  # should find tmp_path/config.yaml
    assert cfg.models.visual_encoder == "pe_core_l14"


def test_load_config_missing_file_raises(tmp_path, monkeypatch):
    """load_config raises FileNotFoundError for a non-existent path."""
    from pipeline.config import load_config

    monkeypatch.delenv("CINEMA_CONFIG", raising=False)

    with pytest.raises(FileNotFoundError):
        load_config(tmp_path / "nonexistent.yaml")


# ---------------------------------------------------------------------------
# Config is a proper dataclass (immutable-friendly, not a dict)
# ---------------------------------------------------------------------------

def test_config_not_a_dict(tmp_path):
    """Config should be a dataclass, not a plain dict."""
    from pipeline.config import load_config, Config

    cfg_file = _write_config(tmp_path, MINIMAL_CONFIG)
    cfg = load_config(cfg_file)

    assert not isinstance(cfg, dict)
    # Attribute access works
    _ = cfg.paths.films_dir
    _ = cfg.thresholds.shot_dedup_cosine


def test_config_repr_includes_class_name(tmp_path):
    """Config repr should identify itself as Config (dataclass default)."""
    from pipeline.config import load_config

    cfg_file = _write_config(tmp_path, MINIMAL_CONFIG)
    cfg = load_config(cfg_file)

    assert "Config" in repr(cfg)


# ---------------------------------------------------------------------------
# load_config — ingest section
# ---------------------------------------------------------------------------

def test_load_config_ingest_defaults(tmp_path):
    """A config without an ingest section gets the default concurrency."""
    from pipeline.config import load_config

    cfg = load_config(_write_config(tmp_path, MINIMAL_CONFIG))
    assert cfg.ingest.annotation_concurrency == 8


def test_load_config_ingest_section(tmp_path):
    """ingest.annotation_concurrency is read from the YAML when present."""
    from pipeline.config import load_config

    content = MINIMAL_CONFIG + (
        "\n"
        "    ingest:\n"
        "      annotation_concurrency: 3\n"
    )
    cfg = load_config(_write_config(tmp_path, content))
    assert cfg.ingest.annotation_concurrency == 3


def test_load_config_rejects_nonpositive_annotation_concurrency(tmp_path):
    """Zero or negative concurrency fails before an ingest can hang."""
    from pipeline.config import load_config

    content = MINIMAL_CONFIG + (
        "\n"
        "    ingest:\n"
        "      annotation_concurrency: 0\n"
    )
    with pytest.raises(ValueError, match="annotation_concurrency"):
        load_config(_write_config(tmp_path, content))
