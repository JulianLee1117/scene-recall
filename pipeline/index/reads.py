"""Bounded scalar reads whose limit applies after the complete filter.

LanceDB 0.33 can push a scan limit ahead of residual predicates when part of
the filter uses a scalar index. A valid late match then disappears. Keep the
engine scan unlimited and consume only the requested filtered Arrow rows.
Vector and full-text ranked queries deliberately do not use this helper.
"""
from __future__ import annotations

from collections.abc import Iterator, Sequence
from itertools import islice
from typing import Any


def iter_filtered_rows(
    table: Any,
    *,
    where: Any,
    columns: Sequence[str] | None = None,
    batch_size: int = 256,
) -> Iterator[dict[str, Any]]:
    """Stream complete-filter matches in native scan order, in bounded batches.

    Callers that stop early must close this iterator (for example with
    ``contextlib.closing``). Exhaustion and failures close the Arrow reader.
    A table pinned by ``IndexSnapshot`` keeps its existing version throughout.
    """
    if isinstance(batch_size, bool) or not isinstance(batch_size, int) or not 1 <= batch_size <= 1024:
        raise ValueError("Scalar batch size must be an integer from 1 to 1024")
    query = table.search().where(where)
    if columns is not None:
        query = query.select(list(columns))
    reader = query.limit(None).to_batches(batch_size=batch_size)
    try:
        for batch in reader:
            yield from batch.to_pylist()
    finally:
        reader.close()


def filtered_rows(
    table: Any,
    *,
    where: Any,
    columns: Sequence[str] | None = None,
    limit: int,
) -> list[dict[str, Any]]:
    """Read at most ``limit`` matches without materializing the remaining rows.

    The scanner may inspect many candidate rows to satisfy the full predicate;
    the native batch size and returned list bound the materialized result.
    """
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
        raise ValueError("Scalar read limit must be a nonnegative integer")
    if limit == 0:
        return []
    rows = iter_filtered_rows(table, where=where, columns=columns, batch_size=min(limit, 256))
    try:
        return list(islice(rows, limit))
    finally:
        rows.close()
