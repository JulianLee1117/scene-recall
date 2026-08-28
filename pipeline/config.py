"""Config dataclass and loader for Scene Recall.

All pipeline stages call ``load_config()`` once at startup and receive a
``Config`` object. User-selectable paths, models, thresholds, and retrieval
weights live in ``config.yaml``; exact supported model revisions and embedding
contracts are intentionally code-owned registries.

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

#: Video containers the pipeline ingests; shared by the CLI and the API.
VIDEO_EXTENSIONS: frozenset[str] = frozenset({
    ".mkv", ".mp4", ".avi", ".mov", ".m4v", ".webm"
})

# Retrieval has three deliberately separate depths.  Candidate generation is
# broad enough to survive fusion/filtering, the production window is what an
# interactive search returns, and the hard ceiling bounds explicit evaluation
# requests.  Keeping these defaults here also lets older config files upgrade
# without silently retaining the former twelve-result retrieval ceiling.
DEFAULT_SEARCH_CANDIDATE_LIMIT = 200
DEFAULT_SEARCH_RESULT_WINDOW = 48
DEFAULT_SEARCH_MAX_RESULT_LIMIT = 100
DEFAULT_SEARCH_PAGE_SIZE = 12
DEFAULT_FILM_RESULTS_PER_PAGE_TARGET = 4


# ---------------------------------------------------------------------------
# Nested config sub-sections
# ---------------------------------------------------------------------------


@dataclass
class PathsConfig:
    films_dir: Path
    assets_dir: Path
    incoming_dir: Path


@dataclass
class ModelsConfig:
    visual_encoder: str
    text_encoder: str
    annotator: str
    annotator_provider: str = "gemini"
    annotator_image_detail: str = "low"
    annotator_reasoning_effort: str = "none"
    whisper: str = "large-v3"


@dataclass
class ThresholdsConfig:
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
    page_size: int
    film_results_per_page_target: int


@dataclass
class RetrievalConfig:
    weights: RetrievalWeights
    diversity: DiversityConfig
    candidate_limit: int
    result_window: int
    max_result_limit: int


@dataclass
class IngestConfig:
    annotation_concurrency: int = 8


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
    ingest: IngestConfig


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
    films_dir = Path(p["films_dir"])
    paths = PathsConfig(
        films_dir=films_dir,
        assets_dir=Path(p["assets_dir"]),
        # Existing configs continue to work: by convention incoming sits next
        # to the immutable film library on the same volume.
        incoming_dir=Path(p.get("incoming_dir", films_dir.parent / "incoming")),
    )

    # --- models ---
    m = raw["models"]
    annotator = str(m["annotator"])
    annotator_provider = str(
        m.get(
            "annotator_provider",
            "gemini" if annotator.startswith("gemini-") else "openai",
        )
    ).lower()
    if annotator_provider not in {"openai", "gemini"}:
        raise ValueError(
            "models.annotator_provider must be 'openai' or 'gemini', "
            f"got {annotator_provider!r}"
        )

    annotator_image_detail = str(
        m.get("annotator_image_detail", "low")
    ).lower()
    if annotator_image_detail not in {"low", "high", "original", "auto"}:
        raise ValueError(
            "models.annotator_image_detail must be low, high, original, or auto"
        )

    annotator_reasoning_effort = str(
        m.get("annotator_reasoning_effort", "none")
    ).lower()
    if annotator_reasoning_effort not in {
        "none", "low", "medium", "high", "xhigh", "max"
    }:
        raise ValueError(
            "models.annotator_reasoning_effort must be none, low, medium, "
            "high, xhigh, or max"
        )

    models = ModelsConfig(
        visual_encoder=m["visual_encoder"],
        text_encoder=m["text_encoder"],
        annotator=annotator,
        annotator_provider=annotator_provider,
        annotator_image_detail=annotator_image_detail,
        annotator_reasoning_effort=annotator_reasoning_effort,
        whisper=m.get("whisper", "large-v3"),
    )

    # --- thresholds ---
    t = raw["thresholds"]
    thresholds = ThresholdsConfig(
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
    diversity_raw = r["diversity"]
    # ``max_per_film`` was a hard global cap.  Accept it only as a legacy
    # spelling for the new per-page preference so existing personal configs
    # migrate to non-destructive, relevance-backfilled diversity.
    film_results_per_page_target = int(
        diversity_raw.get(
            "film_results_per_page_target",
            diversity_raw.get(
                "max_per_film",
                DEFAULT_FILM_RESULTS_PER_PAGE_TARGET,
            ),
        )
    )
    page_size = int(
        diversity_raw.get("page_size", DEFAULT_SEARCH_PAGE_SIZE)
    )
    if page_size < 1:
        raise ValueError("retrieval.diversity.page_size must be at least 1")
    if film_results_per_page_target < 0:
        raise ValueError(
            "retrieval.diversity.film_results_per_page_target cannot be negative"
        )
    diversity = DiversityConfig(
        page_size=page_size,
        film_results_per_page_target=film_results_per_page_target,
    )

    candidate_limit = int(
        r.get("candidate_limit", DEFAULT_SEARCH_CANDIDATE_LIMIT)
    )
    result_window = int(
        r.get("result_window", DEFAULT_SEARCH_RESULT_WINDOW)
    )
    max_result_limit = int(
        r.get("max_result_limit", DEFAULT_SEARCH_MAX_RESULT_LIMIT)
    )
    if max_result_limit < 1 or max_result_limit > 1000:
        raise ValueError(
            "retrieval.max_result_limit must be between 1 and 1000"
        )
    if result_window < 1 or result_window > max_result_limit:
        raise ValueError(
            "retrieval.result_window must be between 1 and max_result_limit"
        )
    if candidate_limit < max_result_limit:
        raise ValueError(
            "retrieval.candidate_limit must be at least max_result_limit"
        )
    retrieval = RetrievalConfig(
        weights=weights,
        diversity=diversity,
        candidate_limit=candidate_limit,
        result_window=result_window,
        max_result_limit=max_result_limit,
    )

    # --- ingest (optional section) ---
    ingest_raw = raw.get("ingest") or {}
    annotation_concurrency = int(ingest_raw.get("annotation_concurrency", 8))
    if annotation_concurrency < 1:
        raise ValueError("ingest.annotation_concurrency must be at least 1")
    ingest = IngestConfig(annotation_concurrency=annotation_concurrency)

    return Config(
        paths=paths,
        models=models,
        thresholds=thresholds,
        retrieval=retrieval,
        ingest=ingest,
    )
