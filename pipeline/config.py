"""Config dataclass and loader for the cinema-search pipeline.

All pipeline stages call ``load_config()`` once at startup and receive a
``Config`` object.  No model names, paths, or thresholds are hardcoded
anywhere else in the pipeline — they all live in ``config.yaml``.

Resolution order (no explicit path given):
1. ``CINEMA_CONFIG`` environment variable
2. ``./config.yaml`` relative to the current working directory
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yaml


# ---------------------------------------------------------------------------
# Nested config sub-sections
# ---------------------------------------------------------------------------


@dataclass
class PathsConfig:
    films_dir: Path
    assets_dir: Path


@dataclass
class ModelsConfig:
    visual_encoder: str
    text_encoder: str
    annotator: str
    router: str
    whisper: str = "large-v3"


@dataclass
class ThresholdsConfig:
    shot_dedup_cosine: float
    scene_visual_sim: float
    scene_dialogue_gap: float
    scene_max_duration: int
    subsegment_min_duration: int
    flash_min_duration: float = 0.5
    keyframe_short_shot_s: float = 2.0


@dataclass
class RetrievalWeights:
    img: float
    txt: float
    lex: float


@dataclass
class DiversityConfig:
    max_per_scene: int
    max_per_film: int


@dataclass
class RetrievalConfig:
    weights: RetrievalWeights
    diversity: DiversityConfig
    rerank_enabled: bool


@dataclass
class ScoringConfig:
    duration_weight: float
    motion_weight: float
    frame_worthiness_weight: float


# ---------------------------------------------------------------------------
# Top-level Config
# ---------------------------------------------------------------------------


@dataclass
class Config:
    """Top-level configuration object.  All pipeline stages share one instance."""

    paths: PathsConfig
    models: ModelsConfig
    thresholds: ThresholdsConfig
    retrieval: RetrievalConfig
    scoring: ScoringConfig


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def load_config(path: Optional[Path | str] = None) -> Config:
    """Load ``config.yaml`` and return a :class:`Config` dataclass.

    Parameters
    ----------
    path:
        Explicit path to the YAML file.  If *None*, the function first checks
        the ``CINEMA_CONFIG`` environment variable, then falls back to
        ``./config.yaml``.

    Raises
    ------
    FileNotFoundError
        If the resolved path does not exist.
    """
    if path is None:
        env_path = os.environ.get("CINEMA_CONFIG")
        if env_path:
            path = Path(env_path)
        else:
            path = Path("config.yaml")
    else:
        path = Path(path)

    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    with path.open("r", encoding="utf-8") as fh:
        raw: dict = yaml.safe_load(fh)

    # --- paths ---
    p = raw["paths"]
    paths = PathsConfig(
        films_dir=Path(p["films_dir"]),
        assets_dir=Path(p["assets_dir"]),
    )

    # --- models ---
    m = raw["models"]
    models = ModelsConfig(
        visual_encoder=m["visual_encoder"],
        text_encoder=m["text_encoder"],
        annotator=m["annotator"],
        router=m["router"],
        whisper=m.get("whisper", "large-v3"),
    )

    # --- thresholds ---
    t = raw["thresholds"]
    thresholds = ThresholdsConfig(
        shot_dedup_cosine=float(t["shot_dedup_cosine"]),
        scene_visual_sim=float(t["scene_visual_sim"]),
        scene_dialogue_gap=float(t["scene_dialogue_gap"]),
        scene_max_duration=int(t["scene_max_duration"]),
        subsegment_min_duration=int(t["subsegment_min_duration"]),
        flash_min_duration=float(t.get("flash_min_duration", 0.5)),
        keyframe_short_shot_s=float(t.get("keyframe_short_shot_s", 2.0)),
    )

    # --- retrieval ---
    r = raw["retrieval"]
    weights = RetrievalWeights(
        img=float(r["weights"]["img"]),
        txt=float(r["weights"]["txt"]),
        lex=float(r["weights"]["lex"]),
    )
    diversity = DiversityConfig(
        max_per_scene=int(r["diversity"]["max_per_scene"]),
        max_per_film=int(r["diversity"]["max_per_film"]),
    )
    retrieval = RetrievalConfig(
        weights=weights,
        diversity=diversity,
        rerank_enabled=bool(r["rerank_enabled"]),
    )

    # --- scoring ---
    s = raw["scoring"]
    scoring = ScoringConfig(
        duration_weight=float(s["duration_weight"]),
        motion_weight=float(s["motion_weight"]),
        frame_worthiness_weight=float(s["frame_worthiness_weight"]),
    )

    return Config(
        paths=paths,
        models=models,
        thresholds=thresholds,
        retrieval=retrieval,
        scoring=scoring,
    )
