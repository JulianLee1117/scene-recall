"""Opt-in retrieval comparisons, separate from ordinary search and recipes."""
from __future__ import annotations

import asyncio
from dataclasses import asdict
from functools import partial
import hashlib
from pathlib import Path
import threading
import time

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

router = APIRouter(prefix="/search/intent", tags=["search-comparison"])
_SLOT = threading.BoundedSemaphore(1)
_LOADED_CODE = {}
_INTERPRETATION_TIMEOUT_SECONDS = 2.0
_COMPARISON_TIMEOUT_SECONDS = 45.0


class ComparisonRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    q: str = Field(min_length=1, max_length=500)
    film_ids: list[str] = Field(default_factory=list, max_length=100)
    limit: int = Field(default=200, ge=1, le=200, strict=True)

    @field_validator("q")
    @classmethod
    def query_not_blank(cls, value):
        if not value.strip():
            raise ValueError("Query must not be blank")
        return value

    @field_validator("film_ids")
    @classmethod
    def normalize_scope(cls, value):
        if len(set(value)) != len(value) or any(not film.strip() or len(film) > 200 for film in value):
            raise ValueError("Use unique nonempty film IDs of at most 200 characters")
        return value


class FrozenComparisonRequest(ComparisonRequest):
    intent: dict | None = None
    supplemental_budget: int = Field(default=120, ge=5, le=300, strict=True)


class FrozenBenchmarkRequest(FrozenComparisonRequest):
    limit: int = Field(default=48, ge=1, le=48, strict=True)
    rounds: int = Field(default=2, ge=1, le=2, strict=True)
    rotation: int = Field(default=0, ge=0, le=2, strict=True)


def _execute(payload, request, intent, *, slot_reserved=False, check_cancelled=None):
    from pipeline.api.main import _with_film_titles, _search_response
    from pipeline.search.intent_retrieval import run_comparison

    if not slot_reserved and not _SLOT.acquire(blocking=False):
        raise HTTPException(429, "A search comparison is running; try again shortly")
    try:
        started = time.perf_counter()
        execution = run_comparison(payload.q, request.app.state.db, request.app.state.config,
                                   intent=intent, film_ids=payload.film_ids, result_limit=payload.limit,
                                   supplemental_budget=getattr(payload, "supplemental_budget", 120),
                                   check_cancelled=check_cancelled)
        if check_cancelled is not None:
            check_cancelled()
        value = asdict(execution)
        for variant in value["variants"].values():
            # Each frozen list is complete for this bounded comparison. Switching
            # lists never retrieves a different snapshot or submits another call.
            variant.update(_search_response(_with_film_titles(request, variant["results"]),
                                           request.app.state.config, payload.limit))
        value.update(query=payload.q, film_ids=payload.film_ids,
                     elapsed_seconds=time.perf_counter() - started,
                     loaded_code_sha256=_LOADED_CODE)
        return value
    finally:
        if not slot_reserved:
            _SLOT.release()


@router.post("/compare")
def compare(payload: FrozenComparisonRequest, request: Request):
    from pipeline.api.main import _require_search_ready
    from pipeline.search.intent import intent_from_dict
    _require_search_ready(request)
    try:
        intent = intent_from_dict(payload.intent, query=payload.q, film_ids=payload.film_ids) if payload.intent is not None else None
    except (ValueError, TypeError, KeyError) as exc:
        raise HTTPException(422, "Invalid frozen intent") from exc
    return _execute(payload, request, intent)


@router.post("/benchmark")
def benchmark_frozen(payload: FrozenBenchmarkRequest, request: Request):
    """Explicit frozen diagnostic; never contacts an interpreter."""
    from pipeline.api.main import _require_search_ready
    from pipeline.search.intent import intent_from_dict
    from pipeline.experiments.intent_latency import benchmark
    _require_search_ready(request)
    try:
        intent = intent_from_dict(payload.intent, query=payload.q, film_ids=payload.film_ids)
    except (ValueError, TypeError, KeyError) as exc:
        raise HTTPException(422, "A valid frozen intent is required") from exc
    if not _SLOT.acquire(blocking=False):
        raise HTTPException(429, "A search comparison is running; try again shortly")
    try:
        value = benchmark(payload.q, request.app.state.db, request.app.state.config,
            intent=intent, film_ids=payload.film_ids, result_limit=payload.limit,
            rounds=payload.rounds, rotation=payload.rotation,
            supplemental_budget=payload.supplemental_budget)
        value["loaded_code_sha256"] = _LOADED_CODE
        return value
    except TimeoutError as exc:
        raise HTTPException(504, "Frozen benchmark exceeded its stage deadline") from exc
    finally:
        _SLOT.release()


def _execute_reserved(payload, request, intent, check_cancelled):
    """The submitted worker owns admission until actual retrieval finishes.

    Cancelling an HTTP request cannot cancel a running encoder thread. Releasing
    its slot in the coroutine's finally block would admit concurrent retrieval.
    """
    try:
        return _execute(payload, request, intent, slot_reserved=True, check_cancelled=check_cancelled)
    finally:
        _SLOT.release()


@router.post("/compare-hosted")
async def compare_hosted(payload: ComparisonRequest, request: Request):
    from pipeline.api.main import _require_search_ready
    from pipeline.experiments.intent_service import interpret
    _require_search_ready(request)
    if not _SLOT.acquire(blocking=False):
        raise HTTPException(429, "A search comparison is running; try again shortly")
    started = time.perf_counter()
    deadline = started + _COMPARISON_TIMEOUT_SECONDS
    stopped = threading.Event()

    def check_cancelled():
        if stopped.is_set() or time.perf_counter() >= deadline:
            raise TimeoutError("Comparison stopped")

    slot_transferred = False
    try:
        try:
            intent, provider = await asyncio.wait_for(
                asyncio.to_thread(interpret, payload.q, request.app.state.config.paths.state_dir,
                                  film_ids=payload.film_ids), timeout=_INTERPRETATION_TIMEOUT_SECONDS)
        except TimeoutError:
            intent, provider = None, {"status": "fallback", "reason": "interpretation_deadline"}
        except (OSError, ValueError):
            intent, provider = None, {"status": "fallback", "reason": "interpretation_unavailable"}
        # Submit synchronously before transferring ownership, so cancellation
        # cannot strand a reserved slot before the worker has been scheduled.
        future = asyncio.get_running_loop().run_in_executor(
            None, partial(_execute_reserved, payload, request, intent, check_cancelled))
        slot_transferred = True
        # Consume any exception even if the client has gone away. The active
        # request still receives it normally through its shielded await.
        future.add_done_callback(lambda completed: completed.exception() if not completed.cancelled() else None)
        try:
            while not future.done():
                check_cancelled()
                if await request.is_disconnected():
                    raise HTTPException(499, "Search comparison cancelled")
                # asyncio.wait never cancels the worker on timeout or disconnect.
                # Poll only this admitted request; no durable job or global task.
                await asyncio.wait({future}, timeout=min(.25, max(0, deadline - time.perf_counter())))
            value = future.result()
        except TimeoutError as exc:
            # A deadline ends the HTTP wait, not a running native encoder or
            # database call. Its worker retains admission until it really exits.
            raise HTTPException(504, "Search comparison timed out; ordinary results remain available") from exc
        value["provider"] = provider
        value["elapsed_seconds"] = time.perf_counter() - started
        return value
    finally:
        stopped.set()
        if not slot_transferred:
            _SLOT.release()


# Capture identities at import, so a changed file cannot mislabel an old server.
for _name in ("search/intent.py", "search/intent_retrieval.py", "search/retrieve.py", "api/search_intent.py",
              "experiments/intent_latency.py"):
    _path = Path(__file__).resolve().parents[1] / _name
    if _path.exists():
        _LOADED_CODE[_name] = hashlib.sha256(_path.read_bytes()).hexdigest()
