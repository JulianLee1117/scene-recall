"""Dedicated semantic-text embeddings for Scene Recall.

The visual encoder remains responsible for text-to-image retrieval.  This
module owns a separate, versioned text space for captions, dialogue, OCR, and
other textual evidence so those signals are not forced through the visual
model or combined into one document.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
import threading
from typing import Any

import numpy as np
import torch

from pipeline.config import Config


_LOGGER = logging.getLogger(__name__)

SEMANTIC_QUERY_INSTRUCTION = (
    "Retrieve film-shot evidence matching the user's remembered dialogue, "
    "visible content, cinematography, mood, or narrative moment."
)
SEMANTIC_QUERY_INSTRUCTION_VERSION = "scene-recall-semantic-query-v1"
TEXT_EMBEDDING_CONTRACT_VERSION = 1
_MAX_LENGTH = 8192
_BATCH_SIZE = 16


@dataclass(frozen=True)
class TextModelSpec:
    """Immutable model/profile facts that define a compatible vector space."""

    config_name: str
    model_id: str
    revision: str
    dimension: int

    @property
    def profile_id(self) -> str:
        """Stable local profile name used by derived indexes and manifests."""
        return (
            f"{self.config_name}-r{self.revision[:12]}-d{self.dimension}-"
            f"c{TEXT_EMBEDDING_CONTRACT_VERSION}"
        )


_TEXT_MODELS: dict[str, TextModelSpec] = {
    "qwen3-embedding-0.6b": TextModelSpec(
        config_name="qwen3-embedding-0.6b",
        model_id="Qwen/Qwen3-Embedding-0.6B",
        # Pin the upstream repository state so an index never silently mixes
        # vectors produced by different checkpoint revisions.
        revision="97b0c61",
        dimension=1024,
    ),
}


@dataclass
class _QwenTextEncoder:
    model: Any
    tokenizer: Any
    device: torch.device
    dimension: int

    @staticmethod
    def _last_token_pool(
        hidden: torch.Tensor,
        attention_mask: torch.Tensor,
    ) -> torch.Tensor:
        """Pool the final non-padding token, following Qwen's reference code."""
        left_padded = bool(
            attention_mask[:, -1].sum().item() == attention_mask.shape[0]
        )
        if left_padded:
            return hidden[:, -1]
        sequence_lengths = attention_mask.sum(dim=1) - 1
        batch_indices = torch.arange(hidden.shape[0], device=hidden.device)
        return hidden[batch_indices, sequence_lengths]

    def encode(self, texts: list[str]) -> torch.Tensor:
        inputs = self.tokenizer(
            texts,
            padding=True,
            truncation=True,
            max_length=_MAX_LENGTH,
            return_tensors="pt",
        )
        inputs = {key: value.to(self.device) for key, value in inputs.items()}
        outputs = self.model(**inputs, return_dict=True)
        pooled = self._last_token_pool(
            outputs.last_hidden_state,
            inputs["attention_mask"],
        )
        return pooled[:, : self.dimension]


_TEXT_MODEL_CACHE: dict[str, _QwenTextEncoder] = {}
# A failed weight/dependency/device load is not retried for every API request.
# CLI backfills naturally get a fresh attempt in their next process; a running
# API can be restarted after its model cache or environment is repaired.
_TEXT_MODEL_FAILURES: dict[str, str] = {}
_TEXT_MODEL_LOCK = threading.RLock()


def get_text_model_spec(config: Config) -> TextModelSpec:
    """Return the exact semantic-text profile selected by configuration."""
    name = config.models.text_encoder
    try:
        return _TEXT_MODELS[name]
    except KeyError as exc:
        raise ValueError(
            f"Unknown text_encoder {name!r}. Known models: "
            f"{sorted(_TEXT_MODELS)}"
        ) from exc


def get_text_vector_dim(config: Config) -> int:
    """Return the configured semantic-text dimension without loading weights."""
    return get_text_model_spec(config).dimension


def _load_text_model(config: Config) -> _QwenTextEncoder:
    spec = get_text_model_spec(config)
    with _TEXT_MODEL_LOCK:
        cached = _TEXT_MODEL_CACHE.get(spec.profile_id)
        if cached is not None:
            return cached
        previous_failure = _TEXT_MODEL_FAILURES.get(spec.profile_id)
        if previous_failure is not None:
            raise RuntimeError(previous_failure)

        try:
            from transformers import AutoModel, AutoTokenizer

            device = torch.device(
                "cuda" if torch.cuda.is_available() else "cpu"
            )
            kwargs: dict[str, Any] = {
                "revision": spec.revision,
                "trust_remote_code": True,
            }
            if device.type == "cuda":
                kwargs["torch_dtype"] = torch.float16
            tokenizer = AutoTokenizer.from_pretrained(
                spec.model_id,
                revision=spec.revision,
                trust_remote_code=True,
                padding_side="left",
            )
            model = AutoModel.from_pretrained(spec.model_id, **kwargs)
            model = model.to(device)
            model.eval()
        except Exception as exc:
            from pipeline.ingest.embed import _load_failure_hint

            message = _load_failure_hint(
                f"{spec.model_id}@{spec.revision}"
            )
            _TEXT_MODEL_FAILURES[spec.profile_id] = message
            _LOGGER.warning(
                "Semantic text model %s could not load; this process will use "
                "legacy text retrieval until restart: %s",
                spec.profile_id,
                exc,
            )
            raise RuntimeError(message) from exc

        encoder = _QwenTextEncoder(
            model=model,
            tokenizer=tokenizer,
            device=device,
            dimension=spec.dimension,
        )
        _TEXT_MODEL_CACHE[spec.profile_id] = encoder
        return encoder


def _normalize(matrix: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms = np.where(norms == 0.0, 1.0, norms)
    return (matrix / norms).astype(np.float32)


def _embed(texts: list[str], config: Config) -> np.ndarray:
    spec = get_text_model_spec(config)
    if not texts:
        return np.empty((0, spec.dimension), dtype=np.float32)
    if any(not isinstance(text, str) or not text.strip() for text in texts):
        raise ValueError("semantic embedding inputs must be non-empty strings")

    encoder = _load_text_model(config)
    batches: list[np.ndarray] = []
    for start in range(0, len(texts), _BATCH_SIZE):
        batch = texts[start : start + _BATCH_SIZE]
        with _TEXT_MODEL_LOCK, torch.inference_mode():
            features = encoder.encode(batch)
        batches.append(features.detach().cpu().float().numpy())
    return _normalize(np.concatenate(batches, axis=0))


def embed_semantic_documents(texts: list[str], config: Config) -> np.ndarray:
    """Embed stored text evidence without a query-side instruction prefix."""
    return _embed(texts, config)


def embed_semantic_queries(texts: list[str], config: Config) -> np.ndarray:
    """Embed retrieval queries using the versioned task instruction."""
    instructed = [
        f"Instruct: {SEMANTIC_QUERY_INSTRUCTION}\nQuery:{text.strip()}"
        for text in texts
    ]
    return _embed(instructed, config)


def embed_semantic_query(text: str, config: Config) -> np.ndarray:
    """Embed one retrieval query and return a one-dimensional vector."""
    return embed_semantic_queries([text], config)[0]
