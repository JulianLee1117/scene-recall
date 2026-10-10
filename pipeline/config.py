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

import math
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

#: Video containers the pipeline ingests; shared by the CLI and the API.
VIDEO_EXTENSIONS: frozenset[str] = frozenset({
    ".mkv", ".mp4", ".avi", ".mov", ".m4v", ".webm"
})

# Retrieval has three deliberately separate depths.  Candidate generation is
# broad enough to survive fusion/filtering, and the production window is what
# an interactive search returns initially. The hard ceiling bounds progressive
# deepening and explicit evaluation requests. Keeping these defaults here also
# lets older config files upgrade
# without silently retaining the former twelve-result retrieval ceiling.
DEFAULT_SEARCH_CANDIDATE_LIMIT = 200
DEFAULT_SEARCH_RESULT_WINDOW = 48
DEFAULT_SEARCH_MAX_RESULT_LIMIT = 200
DEFAULT_SEARCH_PAGE_SIZE = 12
DEFAULT_FILM_RESULTS_PER_PAGE_TARGET = 4
DEFAULT_FILM_REPEAT_RANK_STRENGTH = 32.0


# ---------------------------------------------------------------------------
# Nested config sub-sections
# ---------------------------------------------------------------------------


@dataclass
class PathsConfig:
    films_dir: Path
    assets_dir: Path
    incoming_dir: Path
    state_dir: Path
    # None keeps the existing per-film asset cache for older configurations.
    playback_dir: Path | None = None


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
    film_repeat_rank_strength: float


@dataclass
class RetrievalConfig:
    weights: RetrievalWeights
    diversity: DiversityConfig
    candidate_limit: int
    result_window: int
    max_result_limit: int
    rerank_shortlist: int = 0          # cross-encoder shortlist size; 0 keeps the fused order


@dataclass
class IngestConfig:
    annotation_concurrency: int = 8
    evidence: bool = True              # run the evidence passes after publication
    evidence_hosted: bool = True       # include hosted passes (metadata, subtitles, understanding)
    grounded_subjects: tuple[str, ...] = ("Animation",)   # genre families whose films use the grounded subject backend


@dataclass
class LabConfig:
    music_provider: str = "openai"
    music_model: str = "gpt-audio-1.5"
    planner_model: str = "gpt-5.6-terra"
    music_prompt_version: str = "music-feeling-v3"
    planner_prompt_version: str = "source-timeline-v2"
    beat_checkpoint: Path | None = None
    beat_checkpoint_sha256: str | None = None
    beat_device: str = "cpu"
    footage_inspection: bool = False
    context_profile: str | None = None
    harness: str = "v2"              # v2: measured music + beat-lattice assembly; v1: LLM timing and selection
    harness_critique: bool = False   # v2: watch a rough cut (Gemini) and re-assemble once around flagged issues


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
    lab: LabConfig = field(default_factory=LabConfig)


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def _harness(value: object) -> str:
    harness = str(value).strip().lower()
    if harness not in {"v1", "v2"}:
        raise ValueError("lab.harness must be v1 or v2")
    return harness


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
        # User-authored state must not live in the replaceable asset/index
        # directory.  Keep older configs working by placing it beside the
        # source library unless an explicit location is configured.
        state_dir=Path(p.get("state_dir", films_dir.parent / "state")),
        playback_dir=Path(p["playback_dir"]) if p.get("playback_dir") else None,
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
    film_repeat_rank_strength = float(
        diversity_raw.get(
            "film_repeat_rank_strength",
            DEFAULT_FILM_REPEAT_RANK_STRENGTH,
        )
    )
    if not math.isfinite(film_repeat_rank_strength) or film_repeat_rank_strength < 0:
        raise ValueError(
            "retrieval.diversity.film_repeat_rank_strength must be a finite "
            "non-negative number"
        )
    diversity = DiversityConfig(
        page_size=page_size,
        film_results_per_page_target=film_results_per_page_target,
        film_repeat_rank_strength=film_repeat_rank_strength,
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
    rerank_shortlist = r.get("rerank_shortlist", 0)
    if type(rerank_shortlist) is not int or not 0 <= rerank_shortlist <= 200:
        raise ValueError("retrieval.rerank_shortlist must be an integer between 0 and 200")
    retrieval = RetrievalConfig(
        weights=weights,
        diversity=diversity,
        candidate_limit=candidate_limit,
        result_window=result_window,
        max_result_limit=max_result_limit,
        rerank_shortlist=rerank_shortlist,
    )

    # --- ingest (optional section) ---
    ingest_raw = raw.get("ingest") or {}
    annotation_concurrency = int(ingest_raw.get("annotation_concurrency", 8))
    if annotation_concurrency < 1:
        raise ValueError("ingest.annotation_concurrency must be at least 1")
    evidence = ingest_raw.get("evidence", True)
    evidence_hosted = ingest_raw.get("evidence_hosted", True)
    if not isinstance(evidence, bool) or not isinstance(evidence_hosted, bool):
        raise ValueError("ingest.evidence and ingest.evidence_hosted must be booleans")
    grounded = ingest_raw.get("grounded_subjects", ["Animation"])
    if not isinstance(grounded, list) or not all(isinstance(item, str) for item in grounded):
        raise ValueError("ingest.grounded_subjects must be a list of genre family names")
    ingest = IngestConfig(annotation_concurrency=annotation_concurrency, evidence=evidence,
                          evidence_hosted=evidence_hosted, grounded_subjects=tuple(grounded))

    lab_raw = raw.get("lab") or {}
    footage_inspection = lab_raw.get("footage_inspection", False)
    if not isinstance(footage_inspection, bool):
        raise ValueError("lab.footage_inspection must be a boolean")
    context_profile = lab_raw.get("context_profile")
    if context_profile is not None and (not isinstance(context_profile, str)
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}", context_profile) is None):
        raise ValueError("lab.context_profile must be null or a simple context profile identifier")
    beat_path = lab_raw.get("beat_checkpoint")
    beat_hash = lab_raw.get("beat_checkpoint_sha256")
    if bool(beat_path) != bool(beat_hash):
        raise ValueError("lab.beat_checkpoint and beat_checkpoint_sha256 must be configured together")
    if beat_hash and (len(str(beat_hash)) != 64 or any(c not in "0123456789abcdef" for c in str(beat_hash))):
        raise ValueError("lab.beat_checkpoint_sha256 must be a lowercase SHA-256 digest")
    beat_device = str(lab_raw.get("beat_device", "cpu"))
    if beat_device not in {"cpu", "cuda"}:
        raise ValueError("lab.beat_device must be cpu or cuda")
    music_model = str(lab_raw.get("music_model", "gpt-audio-1.5")).strip()
    # Preserve older explicit Gemini configurations; never switch providers
    # because a request failed or a different API key happens to be available.
    music_provider = str(lab_raw.get("music_provider", "gemini" if music_model.startswith("gemini-") else "openai")).lower()
    if music_provider not in {"openai", "gemini"}:
        raise ValueError("lab.music_provider must be openai or gemini")
    if not music_model or (music_provider == "gemini") != music_model.startswith("gemini-"):
        raise ValueError("lab.music_model must match the selected music_provider")
    if music_provider == "openai" and not music_model.startswith(("gpt-audio", "gpt-4o-audio")):
        raise ValueError("lab.music_model must accept audio input through Chat Completions, for example gpt-audio-1.5")
    planner_model = str(lab_raw.get("planner_model", music_model if music_provider == "gemini" else "gpt-5.6-terra")).strip()
    if not planner_model or (music_provider == "gemini") != planner_model.startswith("gemini-"):
        raise ValueError("lab.planner_model must match the selected music_provider")
    if music_provider == "openai" and planner_model.startswith(("gpt-audio", "gpt-4o-audio")):
        raise ValueError("lab.planner_model requires a text model with Structured Outputs, for example gpt-5.6-terra")
    lab = LabConfig(
        music_provider=music_provider,
        music_model=music_model,
        planner_model=planner_model,
        music_prompt_version=str(lab_raw.get("music_prompt_version", "music-feeling-v3")),
        planner_prompt_version=str(lab_raw.get("planner_prompt_version", "source-timeline-v2")),
        beat_checkpoint=Path(beat_path) if beat_path else None,
        beat_checkpoint_sha256=beat_hash,
        beat_device=beat_device,
        footage_inspection=footage_inspection,
        context_profile=context_profile,
        harness=_harness(lab_raw.get("harness", "v2")),
        harness_critique=bool(lab_raw.get("harness_critique", False)),
    )

    return Config(
        paths=paths,
        models=models,
        thresholds=thresholds,
        retrieval=retrieval,
        ingest=ingest,
        lab=lab,
    )
