"""Bounded, source-timed dialogue for inspecting a published shot.

This is a compiled-evidence read, independent of search ranking and inference.
Read by time overlap, since a subtitle can belong to the adjacent shot in the
quote index while still being audible in the selected shot.
"""
from __future__ import annotations

from contextlib import closing
import heapq
import math
from typing import Any

from lancedb.expr import col, lit

from pipeline.evidence.tables import DIALOGUE_LINES
from pipeline.index.reads import filtered_rows, iter_filtered_rows
from pipeline.index.snapshot import acquire_search_snapshot
from pipeline.index.writer import table_names


MAX_SHOT_DIALOGUE_LINES = 200
_LINE_COLUMNS = ["line_id", "t_start", "t_end", "text", "source"]


def _range(row: dict[str, Any]) -> tuple[float, float] | None:
    try:
        start, end = float(row["t_start"]), float(row["t_end"])
    except (KeyError, TypeError, ValueError):
        return None
    return (start, end) if math.isfinite(start) and math.isfinite(end) and 0 <= start < end else None


def _line(row: dict[str, Any]) -> dict[str, Any] | None:
    span = _range(row)
    identity, text = row.get("line_id"), row.get("text")
    if span is None or not isinstance(identity, str) or not identity or not isinstance(text, str) or not text.strip():
        return None
    source = row.get("source")
    return {
        "line_id": identity, "t_start": span[0], "t_end": span[1], "text": text.strip(),
        "source": source if isinstance(source, str) and source else None,
    }


def shot_dialogue(db: Any, config: Any, *, unit_id: str) -> dict[str, Any] | None:
    """Return timed lines for one canonical, published shot, or None if absent.

    The complete shot and dialogue reads share one pinned library generation.
    Unknown coverage stays unavailable: an absent per-film dialogue index is
    not evidence that the film or selected shot is silent.
    """
    snapshot = db if getattr(db, "is_index_snapshot", False) is True else acquire_search_snapshot(config, db)
    names = table_names(snapshot)
    if not {"films", "units"}.issubset(names):
        return None
    units = filtered_rows(
        snapshot.open_table("units"),
        where=(col("unit_id") == lit(unit_id)) & (col("is_representative") == lit(True)),
        columns=["unit_id", "film_id", "t_start", "t_end"], limit=1,
    )
    if not units or (span := _range(units[0])) is None:
        return None
    film_id = units[0]["film_id"]
    if not filtered_rows(snapshot.open_table("films"), where=col("film_id") == lit(film_id), columns=["film_id"], limit=1):
        return None
    response = {
        "unit_id": unit_id, "film_id": film_id, "t_start": span[0], "t_end": span[1],
        "status": "unavailable", "lines": [], "truncated": False,
    }
    if DIALOGUE_LINES not in names:
        return response
    table = snapshot.open_table(DIALOGUE_LINES)
    # The compiled schema has no separate per-film coverage marker. A film
    # with no rows therefore remains unknown, not an asserted absence of speech.
    film_filter = col("film_id") == lit(film_id)
    if not filtered_rows(table, where=film_filter, columns=["line_id"], limit=1):
        return response
    where = film_filter & (col("t_start") < lit(span[1])) & (col("t_end") > lit(span[0]))
    with closing(iter_filtered_rows(table, where=where, columns=_LINE_COLUMNS)) as rows:
        lines = heapq.nsmallest(
            MAX_SHOT_DIALOGUE_LINES + 1,
            (line for row in rows if (line := _line(row)) is not None),
            key=lambda line: (line["t_start"], line["t_end"], line["line_id"]),
        )
    return {
        **response, "status": "available", "lines": lines[:MAX_SHOT_DIALOGUE_LINES],
        "truncated": len(lines) > MAX_SHOT_DIALOGUE_LINES,
    }
