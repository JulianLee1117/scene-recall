"""embed.py — visual and text embedding via PE Core L/14 (primary) or SigLIP-2 (fallback).

Models are cached once per process by ``(model name, immutable revision)`` in
``_MODEL_CACHE``. Calls without an explicit revision pin their first resolved
revision as that process's default. All outputs are L2-normalised
(cosine-similarity ready).

Supported ``config.models.visual_encoder`` values
--------------------------------------------------
``pe_core_l14``
    timm/PE-Core-L-14-336 — OpenCLIP, 1024-dim, 336 px input.
``siglip2_so400m``
    google/siglip2-so400m-patch14-384 — 1152-dim, 384 px input.

Keyframe naming convention used by ``shot_embedding``
------------------------------------------------------
    ``{asset_dir}/keyframes/{shot.shot_id}_{i}.webp``

where ``i`` runs from 0 to ``len(shot.keyframe_times) - 1``.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any, Protocol

import numpy as np
import torch
import torch.nn.functional as F
from PIL import Image

from pipeline.config import Config
from pipeline.ingest.shots import Shot

# ---------------------------------------------------------------------------
# Model registry
# ---------------------------------------------------------------------------

_MODEL_REPOS: dict[str, str] = {
    "pe_core_l14": "timm/PE-Core-L-14-336",
    "siglip2_so400m": "google/siglip2-so400m-patch14-384",
}

_MODEL_CONFIG_FILES: dict[str, str] = {
    "pe_core_l14": "open_clip_config.json",
    "siglip2_so400m": "config.json",
}

_MODEL_WEIGHT_FILES: dict[str, str | None] = {
    "pe_core_l14": "open_clip_model.safetensors",
    # Transformers resolves a possibly sharded checkpoint from this immutable
    # revision when the model is constructed.
    "siglip2_so400m": None,
}

# Nominal embedding dimensions (for documentation / validation)
_DIMS: dict[str, int] = {
    "pe_core_l14": 1024,
    "siglip2_so400m": 1152,
}

_BATCH_SIZE: int = 32
_SPATIAL_BATCH_SIZE: int = 32

# ---------------------------------------------------------------------------
# Uniform encoder adapters
# ---------------------------------------------------------------------------


class _Encoder(Protocol):
    """Uniform interface over OpenCLIP and Transformers encoders."""

    def encode_images(self, images: list[Image.Image]) -> torch.Tensor: ...

    def encode_spatial_images(
        self,
        images: list[Image.Image],
        grid_size: int,
    ) -> tuple[torch.Tensor, torch.Tensor | None]: ...

    def encode_texts(self, texts: list[str]) -> torch.Tensor: ...


@dataclass(frozen=True)
class VisualModelLineage:
    """One locally resolved immutable visual-model snapshot."""

    config_name: str
    model_id: str
    model_revision: str
    snapshot_dir: Path


@dataclass
class _OpenClipEncoder:
    """Adapter for the OpenCLIP-remapped PE Core checkpoint."""

    model: Any
    preprocess: Any
    tokenizer: Any
    device: torch.device

    def _image_batch(self, images: list[Image.Image]) -> torch.Tensor:
        return torch.stack([self.preprocess(image) for image in images]).to(
            self.device
        )

    def encode_images(self, images: list[Image.Image]) -> torch.Tensor:
        batch = self._image_batch(images)
        return self.model.encode_image(batch, normalize=False)

    def encode_spatial_images(
        self,
        images: list[Image.Image],
        grid_size: int,
    ) -> tuple[torch.Tensor, torch.Tensor | None]:
        """Return global features plus a position-preserving patch grid.

        PE Core's timm visual trunk exposes the final transformer feature map.
        Pooling that 24x24 map to a small fixed grid keeps normalized screen
        position while making transient reference-image reranking affordable.
        """
        visual = getattr(self.model, "visual", None)
        forward_intermediates = getattr(visual, "forward_intermediates", None)
        if not callable(forward_intermediates):
            return self.encode_images(images), None

        batch = self._image_batch(images)
        output = forward_intermediates(
            batch,
            indices=[-1],
            normalize_intermediates=True,
            output_fmt="NCHW",
            output_extra_tokens=False,
        )
        intermediates = output.get("image_intermediates")
        image_features = output.get("image_features")
        if (
            not isinstance(intermediates, list)
            or not intermediates
            or not isinstance(intermediates[-1], torch.Tensor)
            or not isinstance(image_features, torch.Tensor)
        ):
            return self.model.encode_image(batch, normalize=False), None

        spatial = F.adaptive_avg_pool2d(
            intermediates[-1],
            (grid_size, grid_size),
        )
        spatial = F.normalize(spatial, p=2, dim=1)
        return image_features, spatial

    def encode_texts(self, texts: list[str]) -> torch.Tensor:
        tokens = self.tokenizer(
            texts,
            context_length=self.model.context_length,
        ).to(self.device)
        return self.model.encode_text(tokens, normalize=False)


@dataclass
class _TransformersEncoder:
    """Adapter for encoders exposed through Hugging Face Transformers."""

    model: Any
    processor: Any
    device: torch.device

    def encode_images(self, images: list[Image.Image]) -> torch.Tensor:
        inputs = self.processor(images=images, return_tensors="pt")
        inputs = {key: value.to(self.device) for key, value in inputs.items()}
        features = self.model.get_image_features(**inputs)
        return getattr(features, "pooler_output", features)

    def encode_spatial_images(
        self,
        images: list[Image.Image],
        grid_size: int,
    ) -> tuple[torch.Tensor, torch.Tensor | None]:
        del grid_size
        return self.encode_images(images), None

    def encode_texts(self, texts: list[str]) -> torch.Tensor:
        inputs = self.processor(
            text=texts,
            return_tensors="pt",
            padding=True,
            truncation=True,
        )
        inputs = {key: value.to(self.device) for key, value in inputs.items()}
        features = self.model.get_text_features(**inputs)
        return getattr(features, "pooler_output", features)


# Module-level singleton cache   {(model_name, immutable_revision): encoder}
_MODEL_CACHE: dict[tuple[str, str], _Encoder] = {}
# Calls that do not request an exact revision retain the original process-wide
# singleton behavior. Once ``main`` resolves for a model, that immutable
# revision remains the default for the process so two halves of one operation
# cannot cross an upstream ref update.
_DEFAULT_MODEL_REVISIONS: dict[str, str] = {}
# Torch model construction and inference share one process-wide gate.  This
# prevents concurrent API requests from loading the same large model twice or
# overlapping GPU work on a singleton encoder.
_MODEL_LOCK = threading.RLock()


def _snapshot_revision_from_cache_path(path: Path) -> str:
    """Read a HF snapshot revision lexically without dereferencing its file.

    Hugging Face snapshot files are commonly symlinks into ``blobs`` on Linux.
    Resolving the symlink loses the ``snapshots/<commit>`` parent and therefore
    the immutable revision. Only the lexical cache path carries that identity.
    """
    snapshot_dir = path.parent
    if snapshot_dir.parent.name != "snapshots":
        raise RuntimeError(
            "visual encoder artifact is not beneath an immutable HF snapshot: "
            f"{path}"
        )
    revision = snapshot_dir.name
    if not re.fullmatch(r"[0-9a-f]{40,64}", revision):
        raise RuntimeError(
            f"visual encoder snapshot has invalid revision {revision!r}"
        )
    return revision


def _cached_or_downloaded_file(
    model_id: str,
    filename: str,
    *,
    revision: str,
    ensure: bool,
) -> Path | None:
    from huggingface_hub import hf_hub_download, try_to_load_from_cache

    cached = try_to_load_from_cache(
        model_id,
        filename,
        revision=revision,
    )
    if isinstance(cached, str):
        return Path(cached)
    if not ensure:
        return None
    try:
        return Path(
            hf_hub_download(
                repo_id=model_id,
                filename=filename,
                revision=revision,
            )
        )
    except Exception as exc:
        raise RuntimeError(
            f"could not resolve {filename!r} for visual encoder "
            f"{model_id!r} at revision {revision!r}"
        ) from exc


def resolve_visual_model_lineage(
    model_name: str,
    *,
    model_revision: str | None = None,
    ensure_weights: bool = False,
) -> VisualModelLineage | None:
    """Resolve one exact HF snapshot without mixing mutable ``main`` files."""
    model_id = _MODEL_REPOS.get(model_name)
    config_filename = _MODEL_CONFIG_FILES.get(model_name)
    if model_id is None or config_filename is None:
        raise ValueError(
            f"Unknown visual_encoder {model_name!r}. "
            f"Known models: {sorted(_MODEL_REPOS)}"
        )

    requested_revision = model_revision or "main"
    config_path = _cached_or_downloaded_file(
        model_id,
        config_filename,
        revision=requested_revision,
        ensure=ensure_weights,
    )
    if config_path is None:
        return None
    resolved_revision = _snapshot_revision_from_cache_path(config_path)
    if model_revision is not None and resolved_revision != model_revision:
        raise RuntimeError(
            f"visual encoder resolved revision {resolved_revision!r}, "
            f"expected {model_revision!r}"
        )

    weight_filename = _MODEL_WEIGHT_FILES[model_name]
    if weight_filename is not None:
        # Pin the weight lookup to the config's already resolved commit. This
        # closes a mutable-main race between two hub downloads.
        weight_path = _cached_or_downloaded_file(
            model_id,
            weight_filename,
            revision=resolved_revision,
            ensure=ensure_weights,
        )
        if weight_path is None:
            return None
        if _snapshot_revision_from_cache_path(weight_path) != resolved_revision:
            raise RuntimeError(
                "visual encoder config and weights crossed HF revisions"
            )

    return VisualModelLineage(
        config_name=model_name,
        model_id=model_id,
        model_revision=resolved_revision,
        snapshot_dir=config_path.parent,
    )


def _load_model(
    model_name: str,
    model_revision: str | None = None,
) -> _Encoder:
    """Load *model_name* and return a cached encoder adapter.

    PE Core uses its official OpenCLIP-remapped checkpoint. SigLIP 2 uses
    Hugging Face Transformers. Imports stay lazy so importing this module
    does not trigger heavyweight model initialization.

    Parameters
    ----------
    model_name:
        Must be a key in ``_MODEL_REPOS`` (e.g. ``"pe_core_l14"``).

    Raises
    ------
    ValueError
        If *model_name* is not in ``_MODEL_REPOS``.
    """
    with _MODEL_LOCK:
        if model_revision is None:
            default_revision = _DEFAULT_MODEL_REVISIONS.get(model_name)
            if default_revision is not None:
                cached = _MODEL_CACHE.get((model_name, default_revision))
                if cached is not None:
                    return cached
                # Tests and controlled process maintenance may clear the model
                # objects without clearing the selected default revision.
                _DEFAULT_MODEL_REVISIONS.pop(model_name, None)
        if model_revision is not None:
            cached = _MODEL_CACHE.get((model_name, model_revision))
            if cached is not None:
                return cached
        lineage = resolve_visual_model_lineage(
            model_name,
            model_revision=model_revision,
            ensure_weights=True,
        )
        if lineage is None:  # pragma: no cover - ensure_weights raises
            raise RuntimeError(f"visual encoder {model_name!r} is unavailable")
        cache_key = (model_name, lineage.model_revision)
        cached = _MODEL_CACHE.get(cache_key)
        if cached is not None:
            if model_revision is None:
                _DEFAULT_MODEL_REVISIONS[model_name] = (
                    lineage.model_revision
                )
            return cached

        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

        if model_name == "pe_core_l14":
            import open_clip

            exact_model_id = f"local-dir:{lineage.snapshot_dir}"
            try:
                model, _preprocess_train, preprocess = (
                    open_clip.create_model_and_transforms(exact_model_id)
                )
            except Exception as exc:
                raise RuntimeError(
                    _load_failure_hint(
                        lineage.model_id,
                        lineage.model_revision,
                    )
                ) from exc
            tokenizer = open_clip.get_tokenizer(exact_model_id)
            model = model.to(device)
            model.eval()
            encoder: _Encoder = _OpenClipEncoder(
                model=model,
                preprocess=preprocess,
                tokenizer=tokenizer,
                device=device,
            )
        else:
            from transformers import AutoModel, AutoProcessor

            try:
                processor = AutoProcessor.from_pretrained(
                    lineage.model_id,
                    revision=lineage.model_revision,
                )
                model = AutoModel.from_pretrained(
                    lineage.model_id,
                    revision=lineage.model_revision,
                )
            except Exception as exc:
                raise RuntimeError(
                    _load_failure_hint(
                        lineage.model_id,
                        lineage.model_revision,
                    )
                ) from exc
            model = model.to(device)
            model.eval()
            encoder = _TransformersEncoder(
                model=model,
                processor=processor,
                device=device,
            )

        _MODEL_CACHE[cache_key] = encoder
        if model_revision is None:
            _DEFAULT_MODEL_REVISIONS[model_name] = lineage.model_revision
        return encoder


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _load_failure_hint(
    model_id: str,
    model_revision: str | None = None,
) -> str:
    """Explain the usual cause of a model-load failure in offline mode."""
    import os

    hint = ""
    if os.environ.get("HF_HUB_OFFLINE"):
        hint = (
            " HF_HUB_OFFLINE is set (see .env); if this model has not been "
            "downloaded yet, unset it once to download, then re-enable."
        )
    revision_detail = (
        f" at immutable revision {model_revision!r}"
        if model_revision is not None
        else ""
    )
    return f"Could not load encoder {model_id!r}{revision_detail}.{hint}"


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


def get_vector_dim(config: Config) -> int:
    """Return the embedding dimension for the configured visual encoder.

    Public alias for :func:`_get_dim`.  Use this to obtain the vector
    dimension before opening a LanceDB connection so that
    :func:`pipeline.index.writer.create_tables` receives the correct dim.

    Parameters
    ----------
    config:
        Pipeline configuration.  ``config.models.visual_encoder`` selects
        the model (e.g. ``"pe_core_l14"`` → 1024, ``"siglip2_so400m"`` → 1152).

    Returns
    -------
    int
        Embedding dimension for the configured model.

    Raises
    ------
    ValueError
        If ``config.models.visual_encoder`` is not a recognised model name.
    """
    return _get_dim(config)


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

    encoder = _load_model(config.models.visual_encoder)
    batches: list[np.ndarray] = []

    for start in range(0, len(paths), _BATCH_SIZE):
        batch_paths = paths[start : start + _BATCH_SIZE]
        images: list[Image.Image] = []
        for path in batch_paths:
            with Image.open(path) as image:
                images.append(image.convert("RGB"))

        with _MODEL_LOCK, torch.no_grad():
            features = encoder.encode_images(images)

        batches.append(features.cpu().float().numpy())

    arr = np.concatenate(batches, axis=0)
    return _l2_normalize(arr)


def _as_model_image(image: Image.Image) -> Image.Image:
    """Return an RGB model input without copying an image already in RGB."""
    return image if image.mode == "RGB" else image.convert("RGB")


def embed_pil_images(
    images: list[Image.Image],
    config: Config,
) -> np.ndarray:
    """Embed in-memory Pillow images with the configured visual encoder."""
    if not images:
        return np.empty((0, _get_dim(config)), dtype=np.float32)

    encoder = _load_model(config.models.visual_encoder)
    batches: list[np.ndarray] = []
    for start in range(0, len(images), _BATCH_SIZE):
        batch = [
            _as_model_image(image)
            for image in images[start : start + _BATCH_SIZE]
        ]
        with _MODEL_LOCK, torch.no_grad():
            features = encoder.encode_images(batch)
        batches.append(features.cpu().float().numpy())

    return _l2_normalize(np.concatenate(batches, axis=0))


def embed_spatial_images(
    images: list[Image.Image],
    config: Config,
    *,
    grid_size: int = 6,
    model_revision: str | None = None,
) -> tuple[np.ndarray, np.ndarray | None]:
    """Embed images globally and, when supported, as a spatial patch grid.

    The global matrix is shaped ``(N, D)``.  PE Core additionally returns an
    L2-normalized ``(N, grid_size, grid_size, D)`` grid whose cells retain
    normalized screen position.  Encoders without a compatible intermediate
    feature API return ``None`` for the grid so callers can fall back cleanly.
    """
    if grid_size < 2:
        raise ValueError("grid_size must be at least 2")
    if not images:
        return (
            np.empty((0, _get_dim(config)), dtype=np.float32),
            None,
        )

    encoder = _load_model(
        config.models.visual_encoder,
        model_revision=model_revision,
    )
    global_batches: list[np.ndarray] = []
    spatial_batches: list[np.ndarray] = []
    spatial_supported = True

    for start in range(0, len(images), _SPATIAL_BATCH_SIZE):
        batch = [
            _as_model_image(image)
            for image in images[start : start + _SPATIAL_BATCH_SIZE]
        ]
        with _MODEL_LOCK, torch.no_grad():
            global_features, spatial_features = encoder.encode_spatial_images(
                batch,
                grid_size,
            )
        global_batches.append(global_features.cpu().float().numpy())
        if spatial_features is None:
            spatial_supported = False
        elif spatial_supported:
            spatial_batches.append(
                spatial_features.permute(0, 2, 3, 1)
                .contiguous()
                .cpu()
                .float()
                .numpy()
            )

    global_matrix = _l2_normalize(
        np.concatenate(global_batches, axis=0)
    )
    if not spatial_supported:
        return global_matrix, None
    return global_matrix, np.concatenate(spatial_batches, axis=0).astype(
        np.float32
    )


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

    encoder = _load_model(config.models.visual_encoder)
    batches: list[np.ndarray] = []

    for start in range(0, len(texts), _BATCH_SIZE):
        batch_texts = texts[start : start + _BATCH_SIZE]

        with _MODEL_LOCK, torch.no_grad():
            features = encoder.encode_texts(batch_texts)

        batches.append(features.cpu().float().numpy())

    arr = np.concatenate(batches, axis=0)
    return _l2_normalize(arr)


def pool_image_embeddings(embeddings: np.ndarray) -> np.ndarray:
    """Mean-pool per-frame embeddings into one L2-normalized shot vector."""
    if embeddings.ndim != 2 or embeddings.shape[0] == 0:
        raise ValueError(
            "frame embeddings must be a nonempty 2D matrix; "
            f"got shape {embeddings.shape}"
        )
    mean_vec = embeddings.mean(axis=0).astype(np.float32)
    return _l2_normalize(mean_vec.reshape(1, -1))[0]


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
    return pool_image_embeddings(embeddings)
