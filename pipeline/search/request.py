"""Bounded reuse and timings that live for exactly one search execution."""
from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import wraps
from inspect import signature
import logging
from time import perf_counter
from typing import Callable
from contextlib import contextmanager
from copy import deepcopy
import json

import numpy as np

_LOGGER = logging.getLogger("uvicorn.error")
_MAX_MEMO_BYTES = 4 * 1024 * 1024


@dataclass
class SearchContext:
    values: dict[tuple, np.ndarray] = field(default_factory=dict)
    bytes: int = 0
    hits: int = 0
    metadata: dict = field(default_factory=dict)
    stages: dict = field(default_factory=dict)


_CURRENT: ContextVar[SearchContext | None] = ContextVar("scene_search_request", default=None)


@contextmanager
def search_stage(name):
    started = perf_counter()
    try:
        yield
    finally:
        context = _CURRENT.get()
        if context is not None:
            context.stages[name] = context.stages.get(name, 0.) + (perf_counter() - started) * 1000


def reuse_rows(namespace, identities, fetch, *, identity_field="unit_id"):
    """Batch missing metadata once per pinned table/projection/scope."""
    context = _CURRENT.get()
    if context is None:
        return fetch(identities)
    cached = context.metadata.setdefault(namespace, {})
    missing = [identity for identity in dict.fromkeys(identities) if identity not in cached]
    with search_stage("metadata_hydration"):
        fresh = fetch(missing) if missing else []
    by_id = {row[identity_field]: row for row in fresh}
    for identity in missing:
        row = by_id.get(identity)
        size = len(json.dumps(row, default=str).encode())
        if context.bytes + size <= _MAX_MEMO_BYTES:
            cached[identity] = deepcopy(row)
            context.bytes += size
    return [deepcopy(cached.get(identity, by_id.get(identity))) for identity in dict.fromkeys(identities)
            if cached.get(identity, by_id.get(identity)) is not None]


def reuse_vector(key: tuple, compute: Callable[[], np.ndarray]) -> np.ndarray:
    context = _CURRENT.get()
    if context is None:
        return compute()
    if key in context.values:
        context.hits += 1
        return context.values[key].copy()
    with search_stage("query_embedding"):
        value = compute()
    if isinstance(value, np.ndarray) and context.bytes + value.nbytes <= _MAX_MEMO_BYTES:
        context.values[key] = value.copy()
        context.bytes += value.nbytes
    return value


def search_execution(function):
    """Nested clauses share work; concurrent requests and jobs never do."""
    parameters = signature(function)
    @wraps(function)
    def execute(*args, **kwargs):
        if _CURRENT.get() is not None:
            return function(*args, **kwargs)
        context = SearchContext()
        token = _CURRENT.set(context)
        started = perf_counter()
        try:
            if "db" in parameters.parameters and "config" in parameters.parameters:
                import lancedb
                from pipeline.index.snapshot import acquire_search_snapshot
                bound = parameters.bind(*args, **kwargs)
                db = bound.arguments["db"]
                if isinstance(db, lancedb.DBConnection):
                    bound.arguments["db"] = acquire_search_snapshot(bound.arguments["config"], db)
                    args, kwargs = bound.args, bound.kwargs
            return function(*args, **kwargs)
        finally:
            _CURRENT.reset(token)
            _LOGGER.info("search_execution route=%s elapsed_ms=%.1f embedding_reuse=%d memo_bytes=%d",
                         function.__name__, (perf_counter() - started) * 1000,
                         context.hits, context.bytes)
            _LOGGER.info("search_stages route=%s milliseconds=%s", function.__name__, context.stages)
    return execute
