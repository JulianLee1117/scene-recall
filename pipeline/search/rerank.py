"""Cross-encoder rerank of the fused shortlist, reading each shot's text evidence.

Retrieval channels vote by rank, so a shot that one precise document describes
exactly (a story line such as "a face slowly rises from the dark basement
stairs") can lose to shots that several loose channels half-match. A
cross-encoder reads the query and each candidate's evidence together and judges
relevance directly. It runs only on the fused shortlist and its judgement is
blended with the fused rank, never substituted for it, so visual matches that
text cannot describe keep their place.

Model: Qwen3-Reranker-0.6B (Apache-2.0), scored as p("yes") following the
model card. Enabled by ``retrieval.rerank_shortlist`` (0 disables it).

The rerank is an optional refinement with a time budget. When another process
fills the GPU (ingest measurement, embedding backfills), VRAM oversubscription
makes every kernel crawl: a 40-shot rerank that takes 0.25 s alone took 30 s.
So it is skipped while the GPU has little free memory, abandoned mid-shortlist
once it exceeds its budget, and then rested for a minute, keeping the fused
order meanwhile.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any

_LOGGER = logging.getLogger("uvicorn.error")
MODEL_ID = "Qwen/Qwen3-Reranker-0.6B"
INSTRUCTION = ("Given a description, quote or memory of a film moment, judge whether this film shot shows "
               "that moment or matches that description.")
_PREFIX = ("<|im_start|>system\nJudge whether the Document meets the requirements based on the Query and the "
           "Instruct provided. Note that the answer can only be \"yes\" or \"no\".<|im_end|>\n<|im_start|>user\n")
_SUFFIX = "<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n"
_MAX_TOKENS = 384
_BLEND = 0.6                 # weight of the cross-encoder vs the fused rank inside the shortlist
_BUDGET_S = 1.0              # abandon a rerank that runs longer (the fused order stands)
_REST_S = 60.0               # after an overrun or a full GPU, skip reranking this long
_MIN_FREE_BYTES = 768 << 20  # below this much free VRAM, kernels risk paging
_LOCK = threading.Lock()
_MODEL: dict[str, Any] = {}
_RESTING = {"until": 0.0, "overruns": 0}
_OVERRUNS_TO_REST = 2       # one slow query is a blip; consecutive ones mean the GPU is busy


def _load() -> dict[str, Any] | None:
    with _LOCK:
        if "model" in _MODEL or "failed" in _MODEL:
            return _MODEL if "model" in _MODEL else None
        try:
            import torch
            from transformers import AutoModelForCausalLM, AutoTokenizer

            device = "cuda" if torch.cuda.is_available() else "cpu"
            tokenizer = AutoTokenizer.from_pretrained(MODEL_ID, padding_side="left")
            model = AutoModelForCausalLM.from_pretrained(
                MODEL_ID, dtype=torch.float16 if device == "cuda" else torch.float32).to(device).eval()
            state = dict(model=model, tokenizer=tokenizer, device=device, torch=torch,
                         yes=tokenizer.convert_tokens_to_ids("yes"), no=tokenizer.convert_tokens_to_ids("no"),
                         prefix=tokenizer.encode(_PREFIX, add_special_tokens=False),
                         suffix=tokenizer.encode(_SUFFIX, add_special_tokens=False))
            # The first inference pays one-time CUDA setup (seconds). Pay it here, so the time
            # budget only ever measures real queries; otherwise a cold start rests the reranker.
            _score(state, "warm up", ["warm up"] * 2, 2, deadline=float("inf"))
            _MODEL.update(state)
            return _MODEL
        except Exception as exc:  # noqa: BLE001 - optional: search continues without it
            _MODEL["failed"] = True
            _LOGGER.warning("reranker unavailable; keeping fused order: %s", exc)
            return None


def score(query: str, documents: list[str], *, batch_size: int = 16) -> list[float] | None:
    """p(relevant) for each document, or None when unavailable, resting, over budget or failing."""
    if not documents or time.monotonic() < _RESTING["until"]:
        return None
    state = _load()
    if state is None:
        return None
    if state["device"] == "cuda" and _free_bytes(state) < _MIN_FREE_BYTES:
        _rest("GPU memory is nearly full")
        return None
    try:
        budget = _BUDGET_S if _BUDGET_S is not None else float("inf")
        scores = _score(state, query, documents, batch_size, deadline=time.monotonic() + budget)
    except Exception as exc:  # noqa: BLE001 - e.g. CUDA out of memory: keep the fused order
        _LOGGER.warning("rerank skipped for this query: %s", str(exc)[:200])
        try:
            state["torch"].cuda.empty_cache()
        except Exception:  # noqa: BLE001
            pass
        return None
    if scores is None:
        _RESTING["overruns"] += 1
        if _RESTING["overruns"] >= _OVERRUNS_TO_REST:
            _rest(f"{_RESTING['overruns']} queries in a row over its {_BUDGET_S:.1f} s budget")
    else:
        _RESTING["overruns"] = 0
    return scores


def _free_bytes(state: dict[str, Any]) -> int:
    """Device memory this process can still use: free on the device plus its own reusable cache."""
    try:
        cuda = state["torch"].cuda
        return int(cuda.mem_get_info()[0]) + int(cuda.memory_reserved() - cuda.memory_allocated())
    except Exception:  # noqa: BLE001 - unknown: assume there is room
        return _MIN_FREE_BYTES


def set_budget(seconds: float | None) -> None:
    """Change the per-query time budget; None disables it (quality evaluations, not serving)."""
    global _BUDGET_S
    _BUDGET_S = seconds


def _rest(reason: str) -> None:
    _RESTING["overruns"] = 0
    _RESTING["until"] = time.monotonic() + _REST_S
    _LOGGER.warning("rerank resting for %.0f s (%s); keeping the fused order", _REST_S, reason)


def _score(state: dict[str, Any], query: str, documents: list[str], batch_size: int, *,
           deadline: float) -> list[float] | None:
    torch, tokenizer, model = state["torch"], state["tokenizer"], state["model"]
    scores: list[float] = []
    budget = _MAX_TOKENS - len(state["prefix"]) - len(state["suffix"])
    with torch.inference_mode():
        for start in range(0, len(documents), batch_size):
            if start and time.monotonic() > deadline:
                return None
            texts = [f"<Instruct>: {INSTRUCTION}\n<Query>: {query}\n<Document>: {document}"
                     for document in documents[start:start + batch_size]]
            encoded = tokenizer(texts, padding=False, truncation="longest_first", max_length=budget,
                                return_attention_mask=False, add_special_tokens=False)
            ids = [state["prefix"] + item + state["suffix"] for item in encoded["input_ids"]]
            batch = tokenizer.pad({"input_ids": ids}, padding=True, return_tensors="pt").to(state["device"])
            logits = model(**batch).logits[:, -1, :]
            pair = torch.stack([logits[:, state["no"]], logits[:, state["yes"]]], dim=1).float()
            scores.extend(torch.softmax(pair, dim=1)[:, 1].tolist())
    return scores


def document(row: dict[str, Any], evidence: dict[str, Any] | None, scene: dict[str, Any] | None,
             film_title: str | None) -> str:
    """One shot's evidence as the reranker reads it: film, scene, story, look and words."""
    parts = []
    if film_title:
        parts.append(f"Film: {film_title}.")
    if scene and scene.get("title"):
        parts.append(f"Scene: {scene['title']}. {scene.get('summary') or ''}".strip())
    if evidence and evidence.get("action"):
        characters = ""
        try:
            names = json.loads(evidence.get("characters") or "[]")
            characters = f" ({', '.join(names)})" if names else ""
        except (TypeError, ValueError):
            pass
        parts.append(f"Action: {evidence['action']}{characters}")
    if evidence and evidence.get("iconic_note"):
        parts.append(f"Known moment: {evidence['iconic_note']}")
    if row.get("caption"):
        parts.append(f"Visual: {row['caption']}")
    line = (row.get("_matched_line") or {}).get("text")
    if not line:
        try:
            lines = json.loads(row.get("dialogue") or "[]")
            line = " ".join(lines)[:240] if isinstance(lines, list) else ""
        except (TypeError, ValueError):
            line = ""
    if line:
        parts.append(f'Dialogue: "{line}"')
    return "\n".join(parts)


def blend(order: list[Any], relevance: list[float]) -> list[Any]:
    """Reorder a shortlist by the cross-encoder, tempered by the fused rank."""
    count = len(order)
    scored = []
    for position, (item, value) in enumerate(zip(order, relevance)):
        fused = 1.0 - position / max(1, count)
        scored.append((-(_BLEND * value + (1 - _BLEND) * fused), position, item))
    scored.sort(key=lambda entry: (entry[0], entry[1]))
    return [item for _score, _position, item in scored]
