"""embed.py — visual and text embedding via PE Core L/14 (primary) or SigLIP-2 (fallback).

Models are loaded once per process (singleton keyed by model name) and cached
in ``_MODEL_CACHE``.  All outputs are L2-normalised (cosine-similarity ready).

Supported ``config.models.visual_encoder`` values
--------------------------------------------------
``pe_core_l14``
    facebook/PE-Core-L14-336 — CLIP-style, 1024-dim, 336 px input.
``siglip2_so400m``
    google/siglip2-so400m-patch14-384 — 1152-dim, 384 px input.

Keyframe naming convention used by ``shot_embedding``
------------------------------------------------------
    ``{asset_dir}/keyframes/{shot.shot_id}_{i}.webp``

where ``i`` runs from 0 to ``len(shot.keyframe_times) - 1``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import torch
from PIL import Image

from pipeline.config import Config
from pipeline.ingest.shots import Shot

# ---------------------------------------------------------------------------
# Model registry
# ---------------------------------------------------------------------------

_HF_IDS: dict[str, str] = {
    "pe_core_l14": "facebook/PE-Core-L14-336",
    "siglip2_so400m": "google/siglip2-so400m-patch14-384",
}

# Nominal embedding dimensions (for documentation / validation)
_DIMS: dict[str, int] = {
    "pe_core_l14": 1024,
    "siglip2_so400m": 1152,
}

_BATCH_SIZE: int = 32

# ---------------------------------------------------------------------------
# Module-level singleton cache   {model_name: (model, processor, device)}
# ---------------------------------------------------------------------------

_MODEL_CACHE: dict[str, tuple[Any, Any, torch.device]] = {}


def _load_model(model_name: str) -> tuple[Any, Any, torch.device]:
    """Load *model_name* and return ``(model, processor, device)``, cached.

    Transformers are imported lazily so that the module can be imported
    without triggering a heavy HuggingFace load at import time.

    Parameters
    ----------
    model_name:
        Must be a key in ``_HF_IDS`` (e.g. ``"pe_core_l14"``).

    Raises
    ------
    ValueError
        If *model_name* is not in ``_HF_IDS``.
    """
    if model_name not in _MODEL_CACHE:
        hf_id = _HF_IDS.get(model_name)
        if hf_id is None:
            raise ValueError(
                f"Unknown visual_encoder {model_name!r}. "
                f"Known models: {sorted(_HF_IDS)}"
            )

        from transformers import (
            AutoModel,
            AutoProcessor,
            CLIPModel,
            CLIPProcessor,
        )

        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

        if model_name == "pe_core_l14":
            processor = CLIPProcessor.from_pretrained(hf_id)
            model = CLIPModel.from_pretrained(hf_id)
        else:
            processor = AutoProcessor.from_pretrained(hf_id)
            model = AutoModel.from_pretrained(hf_id)

        model = model.to(device)
        model.eval()
        _MODEL_CACHE[model_name] = (model, processor, device)

    return _MODEL_CACHE[model_name]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _l2_normalize(arr: np.ndarray) -> np.ndarray:
    """Row-wise L2 normalisation; zero vectors are left as-is."""
    norms = np.linalg.norm(arr, axis=1, keepdims=True)
    norms = np.where(norms == 0.0, 1.0, norms)
    return (arr / norms).astype(np.float32)


def _get_dim(config: Config) -> int:
    """Return embedding dimension for the configured model without loading weights.

    Uses the ``_DIMS`` lookup table so callers can obtain the dimension without
    triggering a heavyweight model load.

    Raises
    ------
    ValueError
        If ``config.models.visual_encoder`` is not in ``_DIMS``.
    """
    model_name = config.models.visual_encoder
    dim = _DIMS.get(model_name)
    if dim is None:
        raise ValueError(
            f"Unknown visual_encoder {model_name!r}. Known models: {sorted(_DIMS)}"
        )
    return dim


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def embed_images(paths: list[Path], config: Config) -> np.ndarray:
    """Embed image files and return an L2-normalised feature matrix.

    Parameters
    ----------
    paths:
        Paths to image files (any format supported by Pillow).
    config:
        Pipeline configuration; ``config.models.visual_encoder`` selects the
        model.

    Returns
    -------
    np.ndarray
        Shape ``(N, D)``, dtype ``float32``, each row L2-normalised.
        Returns shape ``(0, D)`` when *paths* is empty.
    """
    if not paths:
        return np.empty((0, _get_dim(config)), dtype=np.float32)

    model, processor, device = _load_model(config.models.visual_encoder)
    batches: list[np.ndarray] = []

    for start in range(0, len(paths), _BATCH_SIZE):
        batch_paths = paths[start : start + _BATCH_SIZE]
        images = [Image.open(p).convert("RGB") for p in batch_paths]

        inputs = processor(images=images, return_tensors="pt")
        inputs = {k: v.to(device) for k, v in inputs.items()}

        with torch.no_grad():
            features = model.get_image_features(**inputs)

        batches.append(features.cpu().float().numpy())

    arr = np.concatenate(batches, axis=0)
    return _l2_normalize(arr)


def embed_text(texts: list[str], config: Config) -> np.ndarray:
    """Embed text strings and return an L2-normalised feature matrix.

    Parameters
    ----------
    texts:
        Text strings to embed (search queries, scene descriptions, etc.).
    config:
        Pipeline configuration; ``config.models.visual_encoder`` selects the
        model.

    Returns
    -------
    np.ndarray
        Shape ``(N, D)``, dtype ``float32``, each row L2-normalised.
        Returns shape ``(0, D)`` when *texts* is empty.
    """
    if not texts:
        return np.empty((0, _get_dim(config)), dtype=np.float32)

    model, processor, device = _load_model(config.models.visual_encoder)
    batches: list[np.ndarray] = []

    for start in range(0, len(texts), _BATCH_SIZE):
        batch_texts = texts[start : start + _BATCH_SIZE]

        inputs = processor(
            text=batch_texts,
            return_tensors="pt",
            padding=True,
            truncation=True,
        )
        inputs = {k: v.to(device) for k, v in inputs.items()}

        with torch.no_grad():
            features = model.get_text_features(**inputs)

        batches.append(features.cpu().float().numpy())

    arr = np.concatenate(batches, axis=0)
    return _l2_normalize(arr)


def shot_embedding(shot: Shot, asset_dir: Path, config: Config) -> np.ndarray:
    """Embed all keyframes for *shot* and return the mean, re-normalised.

    Keyframe paths are expected at::

        {asset_dir}/keyframes/{shot.shot_id}_{i}.webp

    where ``i`` is 0-indexed (matching the output of ``media.py``).

    Parameters
    ----------
    shot:
        Shot dataclass with ``shot_id`` and ``keyframe_times``.
    asset_dir:
        Root asset directory for the film (``film.asset_dir``).
    config:
        Pipeline configuration.

    Returns
    -------
    np.ndarray
        Shape ``(D,)``, dtype ``float32``, L2-normalised.

    Raises
    ------
    ValueError
        If ``shot.keyframe_times`` is empty (no keyframes to embed).
    """
    if not shot.keyframe_times:
        raise ValueError(
            f"Shot {shot.shot_id!r} has no keyframe_times; cannot compute embedding."
        )

    paths = [
        asset_dir / "keyframes" / f"{shot.shot_id}_{i}.webp"
        for i in range(len(shot.keyframe_times))
    ]

    embeddings = embed_images(paths, config)  # (K, D)
    mean_vec = embeddings.mean(axis=0).astype(np.float32)  # (D,)

    return _l2_normalize(mean_vec.reshape(1, -1))[0]
