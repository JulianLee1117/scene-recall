"""Opt-in relevance checks using the real, pinned local Qwen text encoder.

Run with SCENE_RECALL_LOCAL_TEXT_EVAL=1 and HF_HUB_OFFLINE=1 after preparing
the model cache. Normal test runs skip model inference. These pairwise checks
cover instruction-induced topic bias in indexed text evidence; they do not
measure visual truth, full-library recall, or the final hybrid ranking.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from pipeline.config import Config
from pipeline.ingest.text_embed import (
    embed_semantic_documents,
    embed_semantic_queries,
)


_EVIDENCE = json.loads(
    (Path(__file__).parent / "data" / "semantic_query_relevance.json").read_text(
        encoding="utf-8"
    )
)

pytestmark = pytest.mark.skipif(
    os.environ.get("SCENE_RECALL_LOCAL_TEXT_EVAL") != "1",
    reason="Set SCENE_RECALL_LOCAL_TEXT_EVAL=1 to evaluate the cached Qwen model",
)


@pytest.mark.parametrize("case", _EVIDENCE["cases"], ids=lambda case: case["id"])
def test_query_evidence_outranks_unrelated_descriptions(
    case: dict,
    config: Config,
) -> None:
    """Every relevant passage must beat the strongest unrelated passage."""
    config.models.text_encoder = "qwen3-embedding-0.6b"
    positive_ids = case["positive_ids"]
    negative_ids = case["negative_ids"]
    evidence_ids = positive_ids + negative_ids
    documents = embed_semantic_documents(
        [_EVIDENCE["documents"][key]["text"] for key in evidence_ids],
        config,
    )
    query = embed_semantic_queries([case["query"]], config)[0]
    similarities = dict(zip(evidence_ids, documents @ query, strict=True))
    strongest_negative = max(negative_ids, key=similarities.__getitem__)

    for positive_id in positive_ids:
        assert similarities[positive_id] > similarities[strongest_negative], (
            f"Query {case['query']!r}: relevant {positive_id} "
            f"({similarities[positive_id]:.4f}) did not outrank unrelated "
            f"{strongest_negative} ({similarities[strongest_negative]:.4f})"
        )
