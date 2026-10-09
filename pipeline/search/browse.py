"""Chronological, explicitly scoped browsing of published scene evidence.

No query, encoder, or similarity score is involved. The bounded result prefix
keeps selected-film order and source chronology, and uses the normal caption
junk policy. Metadata scans stay scoped and streaming; only the next candidate
chunk and its verified frame choices are retained in memory.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from contextlib import closing
import heapq
import json
import math
from typing import Any

from lancedb.expr import col, lit

from pipeline.config import Config
from pipeline.index.reads import filtered_rows, iter_filtered_rows
from pipeline.index.snapshot import acquire_search_snapshot
from pipeline.index.writer import table_names
from pipeline.search.retrieve import (
    _units_in_unrequested_junk_scenes,
    _clause_results_from_rows,
    _film_filter,
    _is_unrequested_junk,
    _unit_filter,
    resolve_result_limit,
)

_UNIT_COLUMNS = [
    "unit_id", "film_id", "shot_id", "t_start", "t_end", "caption",
    "keyframe_paths",
]
_FRAME_COLUMNS = [
    "frame_id", "film_id", "unit_id", "shot_id", "frame_index", "timestamp",
    "path",
]


def _scope(film_ids: Iterable[str] | None) -> tuple[str, ...]:
    if film_ids is None or isinstance(film_ids, str):
        raise ValueError("Select at least one movie to browse its scenes")
    values = tuple(film_ids)
    if not values or any(not isinstance(value, str) or not value.strip() for value in values):
        raise ValueError("Select at least one movie to browse its scenes")
    return tuple(dict.fromkeys(value.strip() for value in values))


def _key(row: dict[str, Any]) -> tuple[float, float, str]:
    return row["t_start"], row["t_end"], row["unit_id"]


def _eligible_unit(row: dict[str, Any]) -> dict[str, Any] | None:
    if not row.get("unit_id") or not row.get("shot_id"):
        return None
    try:
        start, end = float(row["t_start"]), float(row["t_end"])
        paths = row["keyframe_paths"]
        if isinstance(paths, str):
            paths = json.loads(paths)
    except (KeyError, TypeError, ValueError):
        return None
    if not (math.isfinite(start) and math.isfinite(end) and 0 <= start < end):
        return None
    if not isinstance(paths, list) or not paths or any(not isinstance(path, str) or not path for path in paths):
        return None
    if _is_unrequested_junk(row, "", requested=set()):
        return None
    return {**row, "t_start": start, "t_end": end, "keyframe_paths": paths}


def _next_units(table: Any, film_id: str, after: tuple | None, limit: int) -> list[dict]:
    where = (col("film_id") == lit(film_id)) & (col("is_representative") == lit(True))
    if after is not None:
        start, end, unit_id = after
        where = where & (
            (col("t_start") > lit(start))
            | ((col("t_start") == lit(start)) & (col("t_end") > lit(end)))
            | ((col("t_start") == lit(start)) & (col("t_end") == lit(end)) & (col("unit_id") > lit(unit_id)))
        )
    with closing(iter_filtered_rows(table, where=where, columns=_UNIT_COLUMNS)) as rows:
        eligible = (unit for row in rows if (unit := _eligible_unit(row)) is not None)
        return heapq.nsmallest(limit, eligible, key=_key)


def _frames_for_units(table: Any, film_id: str, units: list[dict]) -> dict[str, dict]:
    by_id = {unit["unit_id"]: unit for unit in units}
    chosen: dict[str, tuple[tuple, dict]] = {}
    where = (
        (col("film_id") == lit(film_id))
        & _unit_filter(tuple(by_id))
        & (col("is_representative") == lit(True))
    )
    with closing(iter_filtered_rows(table, where=where, columns=_FRAME_COLUMNS)) as rows:
        for frame in rows:
            unit = by_id.get(frame.get("unit_id"))
            if unit is None or frame.get("shot_id") != unit["shot_id"] or not frame.get("frame_id"):
                continue
            index = frame.get("frame_index")
            paths = unit["keyframe_paths"]
            if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(paths):
                continue
            if frame.get("path") != paths[index]:
                continue
            try:
                timestamp = float(frame["timestamp"])
            except (KeyError, TypeError, ValueError):
                continue
            if not math.isfinite(timestamp) or not unit["t_start"] <= timestamp < unit["t_end"]:
                continue
            # Prefer the normal middle keyframe; if unavailable, retain another
            # actual in-shot sample rather than inventing a midpoint timestamp.
            preference = (
                index != len(paths) // 2,
                abs(timestamp - (unit["t_start"] + unit["t_end"]) / 2),
                index, str(frame["frame_id"]),
            )
            prior = chosen.get(unit["unit_id"])
            if prior is None or preference < prior[0]:
                chosen[unit["unit_id"]] = (preference, {
                    "frame_id": frame["frame_id"], "frame_index": index,
                    "timestamp": timestamp,
                })
    return {unit_id: value[1] for unit_id, value in chosen.items()}


def browse_scenes(
    db: Any,
    config: Config,
    *,
    film_ids: Iterable[str] | None,
    result_limit: int | None = None,
    shot_filters: Mapping[str, Sequence[str]] | None = None,
) -> list[dict[str, Any]]:
    """Return a bounded chronological prefix from explicitly selected films.

    A film row plus representative units is the existing publication rule.
    Apply it only inside the requested scope, with all reads pinned together.
    Missing/unpublished IDs return no scenes; an absent scope is an error.
    """
    from pipeline.search import priors
    from pipeline.search.shot_facets import unit_scope

    scope = _scope(film_ids)
    limit = resolve_result_limit(config, result_limit)
    snapshot = db if getattr(db, "is_index_snapshot", False) is True else acquire_search_snapshot(config, db)
    if not {"films", "units", "frames"}.issubset(table_names(snapshot)):
        return []
    shots = unit_scope(snapshot, shot_filters)
    published = {
        row["film_id"] for row in filtered_rows(
            snapshot.open_table("films"), where=_film_filter(scope),
            columns=["film_id"], limit=len(scope),
        )
    }
    selected: list[dict] = []
    seen: set[str] = set()
    for film_id in scope:
        if film_id not in published:
            continue
        after = None
        while len(selected) < limit:
            chunk_size = max(64, limit - len(selected))
            units = _next_units(snapshot.open_table("units"), film_id, after, chunk_size)
            if not units:
                break
            frames = _frames_for_units(snapshot.open_table("frames"), film_id, units)
            junk_units = _units_in_unrequested_junk_scenes(
                snapshot, priors.load_evidence(snapshot, (unit["unit_id"] for unit in units)), set())
            for unit in units:
                unit_id = unit["unit_id"]
                if (unit_id not in seen and unit_id in frames and unit_id not in junk_units
                        and (shots is None or shots.allows(unit_id))):
                    selected.append({**unit, "_matched_frame": frames[unit_id]})
                    seen.add(unit_id)
                    if len(selected) == limit:
                        break
            if len(units) < chunk_size:
                break
            after = _key(units[-1])
        if len(selected) == limit:
            break
    results = _clause_results_from_rows(selected, channel="browse", mode="browse", result_limit=limit)
    for result in results:
        # This is source order, not a semantic relevance estimate.
        result["debug"] = {"mode": "browse", "final_score": 0.0, "channels": {}}
    return results


def browse_highlights(
    db: Any,
    config: Config,
    *,
    film_ids: Iterable[str] | None,
    preset: str,
    result_limit: int | None = None,
    shot_filters: Mapping[str, Sequence[str]] | None = None,
) -> list[dict[str, Any]]:
    """A film's best-known moments (``famous``) or its hidden gems (``gems``), one per dramatic scene.

    Films without evidence keep chronological browsing. Order comes from the
    synthesized priors (pipeline.evidence.synthesis), not from similarity.
    """
    from pipeline.evidence.tables import SHOT_EVIDENCE
    from pipeline.search import priors
    from pipeline.search.shot_facets import unit_scope

    scope = _scope(film_ids)
    limit = resolve_result_limit(config, result_limit)
    snapshot = db if getattr(db, "is_index_snapshot", False) is True else acquire_search_snapshot(config, db)
    if SHOT_EVIDENCE not in table_names(snapshot):
        return browse_scenes(snapshot, config, film_ids=scope, result_limit=limit, shot_filters=shot_filters)
    shots = unit_scope(snapshot, shot_filters)
    rows = filtered_rows(
        snapshot.open_table(SHOT_EVIDENCE), where=_film_filter(scope),
        columns=["unit_id", "film_id", "scene_id", "fame_library", "craft", "distinctiveness", "iconic", "gem"],
        limit=200_000,
    )
    if shots is not None:
        rows = [row for row in rows if shots.allows(row["unit_id"])]
    if not rows:
        return browse_scenes(snapshot, config, film_ids=scope, result_limit=limit, shot_filters=shot_filters)

    def famous(row: dict[str, Any]) -> float:
        return (1.0 if row.get("iconic") else 0.0) + float(row.get("fame_library") or 0) + 0.3 * float(row.get("craft") or 0)

    def gem(row: dict[str, Any]) -> float:
        craft, fame = float(row.get("craft") or 0), float(row.get("fame_library") or 0)
        return (1.0 if row.get("gem") else 0.0) + craft * (1 - fame) * (0.5 + 0.5 * float(row.get("distinctiveness") or 0))

    ranked = sorted(rows, key=famous if preset == "famous" else gem, reverse=True)
    if preset == "gems":
        ranked = [row for row in ranked if not row.get("iconic")]
    junk_units = _units_in_unrequested_junk_scenes(snapshot, {row["unit_id"]: row for row in ranked}, set())
    ranked = [row for row in ranked if row["unit_id"] not in junk_units]
    picked, scenes = [], set()
    for row in ranked:
        scene = row.get("scene_id") or row["unit_id"]
        if scene in scenes:
            continue
        scenes.add(scene)
        picked.append(row["unit_id"])
        if len(picked) >= limit:
            break
    units = {row["unit_id"]: row for row in filtered_rows(
        snapshot.open_table("units"), where=_unit_filter(tuple(picked)), columns=_UNIT_COLUMNS, limit=len(picked))}
    ordered = [units[unit_id] for unit_id in picked if unit_id in units and not _is_unrequested_junk(units[unit_id], "", set())]
    results = _clause_results_from_rows(ordered, channel="browse", mode=f"browse-{preset}", result_limit=limit)
    evidence = priors.load_evidence(snapshot, (result["unit_id"] for result in results))
    scene_rows = priors.load_scenes(snapshot, (row.get("scene_id") for row in evidence.values()))
    for rank, result in enumerate(results, start=1):
        result["rank"] = rank
        result["debug"] = {"mode": f"browse-{preset}", "final_score": 0.0, "channels": {}}
        shot = evidence.get(result["unit_id"])
        if shot:
            priors.decorate(result, shot, scene_rows.get(shot.get("scene_id") or ""))
    return results
