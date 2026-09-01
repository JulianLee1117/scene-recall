"""Typed modular search recipes over existing Scene Recall evidence.

This module owns workflow-level clause composition.  Channel mechanics stay
in :mod:`pipeline.search.retrieve`, and every source reference is resolved
from stable unit/frame identities already published in LanceDB.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Literal, Sequence

import lancedb
from lancedb.expr import col, lit
import numpy as np
from PIL import Image, UnidentifiedImageError

from pipeline.config import Config
from pipeline.index.text_features import build_mood_view_text
from pipeline.search.retrieve import (
    SemanticTextProfileUnavailable,
    apply_recipe_result_preferences,
    resolve_result_limit,
    search,
    search_by_image,
    search_look_by_image,
    search_look_by_text,
    search_look_by_vector,
    search_semantic_views,
    resolve_reference_result_scope,
)


SearchFacet = Literal[
    "all",
    "scene",
    "words",
    "look",
    "composition",
    "mood",
]
ClauseKind = Literal["text", "source", "image"]
_RRF_K = 60


class RecipeSourceNotFound(LookupError):
    """A stable unit/frame reference no longer resolves in the active index."""


class RecipeSourceUnavailable(ValueError):
    """A source exists but lacks evidence required by the selected facet."""


@dataclass(frozen=True)
class SourceReference:
    unit_id: str
    frame_index: int | None = None


@dataclass(frozen=True)
class SearchClause:
    clause_id: str
    kind: ClauseKind
    facet: SearchFacet
    text: str | None = None
    source: SourceReference | None = None
    image: Image.Image | None = None


def _is_mandatory_visual_clause(clause: SearchClause) -> bool:
    """Return whether a clause defines the recipe's candidate set.

    Uploaded images are authoritative visual references whether interpreted
    as Look or Framing. Indexed composition sources retain the same gate.
    Ordinary Look text and indexed-frame clauses remain independent fusion
    signals instead of becoming mandatory merely because they share a facet.
    """
    return clause.kind == "image" or clause.facet == "composition"


@dataclass(frozen=True)
class SearchRecipeExecution:
    """One recipe's results and the exact source inputs used to produce them."""

    results: list[dict[str, Any]]
    source_evidence: list[dict[str, Any]]


@dataclass(frozen=True)
class _ClauseRanking:
    clause: SearchClause
    results: list[dict[str, Any]]
    query_text: str


@dataclass(frozen=True)
class _ResolvedSourceEvidence:
    unit: dict[str, Any]
    query_text: str = ""
    frame: dict[str, Any] | None = None
    visual_vector: np.ndarray | None = None
    image: Image.Image | None = None


def _one_row(
    table: Any,
    column: str,
    value: str,
    columns: Sequence[str],
) -> dict[str, Any] | None:
    rows = (
        table.search()
        .select(list(columns))
        .where(col(column) == lit(value))
        .limit(2)
        .to_list()
    )
    matches = [row for row in rows if str(row.get(column) or "") == value]
    if len(matches) != 1:
        return None
    return dict(matches[0])


def _resolve_unit(
    db: lancedb.DBConnection,
    unit_id: str,
) -> dict[str, Any]:
    row = _one_row(
        db.open_table("units"),
        "unit_id",
        unit_id,
        (
            "unit_id",
            "film_id",
            "shot_id",
            "caption",
            "dialogue",
            "on_screen_text",
            "mood",
            "energy",
        ),
    )
    if row is None:
        raise RecipeSourceNotFound(f"Source scene {unit_id!r} was not found")
    return row


def _resolve_frame(
    db: lancedb.DBConnection,
    unit: dict[str, Any],
    frame_index: int,
) -> dict[str, Any]:
    frame_id = f"{unit['unit_id']}::frame::{frame_index}"
    row = _one_row(
        db.open_table("frames"),
        "frame_id",
        frame_id,
        (
            "frame_id",
            "film_id",
            "unit_id",
            "frame_index",
            "timestamp",
            "path",
            "visual_vec",
        ),
    )
    try:
        resolved_frame_index = int(row.get("frame_index", -1)) if row else -1
    except (TypeError, ValueError):
        resolved_frame_index = -1
    if (
        row is None
        or str(row.get("unit_id") or "") != str(unit["unit_id"])
        or str(row.get("film_id") or "") != str(unit["film_id"])
        or resolved_frame_index != frame_index
    ):
        raise RecipeSourceNotFound(
            f"Frame {frame_index} for source scene {unit['unit_id']!r} "
            "was not found"
        )
    return row


def _json_strings(raw: Any, *, field: str, unit_id: str) -> list[str]:
    if raw in (None, ""):
        return []
    if isinstance(raw, list):
        values = raw
    else:
        try:
            values = json.loads(str(raw))
        except json.JSONDecodeError as exc:
            raise RecipeSourceUnavailable(
                f"Source scene {unit_id!r} has invalid {field} evidence"
            ) from exc
    if not isinstance(values, list):
        raise RecipeSourceUnavailable(
            f"Source scene {unit_id!r} has invalid {field} evidence"
        )
    return [
        value.strip()
        for value in values
        if isinstance(value, str) and value.strip()
    ]


def _source_text(facet: SearchFacet, unit: dict[str, Any]) -> str:
    unit_id = str(unit["unit_id"])
    if facet == "scene":
        text = str(unit.get("caption") or "").strip()
        if text:
            return text
        raise RecipeSourceUnavailable(
            f"Source scene {unit_id!r} has no visual description"
        )
    if facet == "words":
        dialogue = _json_strings(
            unit.get("dialogue"),
            field="dialogue",
            unit_id=unit_id,
        )
        on_screen_text = str(unit.get("on_screen_text") or "").strip()
        text = " ".join([*dialogue, on_screen_text]).strip()
        if text:
            return text
        raise RecipeSourceUnavailable(
            "This scene has no dialogue or on-screen text to match"
        )
    if facet == "mood":
        try:
            text = build_mood_view_text(unit)
        except ValueError as exc:
            raise RecipeSourceUnavailable(
                f"Source scene {unit_id!r} has invalid mood evidence"
            ) from exc
        if text:
            return text
        raise RecipeSourceUnavailable(
            f"Source scene {unit_id!r} has no mood or energy evidence"
        )
    raise ValueError(f"facet {facet!r} does not derive a text source")


def _resolve_source_clauses(
    clauses: Sequence[SearchClause],
    db: lancedb.DBConnection,
) -> dict[str, _ResolvedSourceEvidence]:
    """Resolve every source clause before any retrieval can short-circuit."""
    resolved: dict[str, _ResolvedSourceEvidence] = {}
    units: dict[str, dict[str, Any]] = {}
    frames: dict[tuple[str, int], dict[str, Any]] = {}
    for clause in clauses:
        if clause.kind != "source":
            continue
        if clause.source is None:
            raise ValueError(f"source clause {clause.clause_id!r} has no source")
        source = clause.source
        unit = units.get(source.unit_id)
        if unit is None:
            unit = _resolve_unit(db, source.unit_id)
            units[source.unit_id] = unit

        if clause.facet in {"scene", "words", "mood"}:
            resolved[clause.clause_id] = _ResolvedSourceEvidence(
                unit=unit,
                query_text=_source_text(clause.facet, unit),
            )
            continue
        if clause.facet not in {"look", "composition"}:
            raise ValueError(f"facet {clause.facet!r} does not accept a source")
        if source.frame_index is None:
            raise ValueError(f"{clause.facet} source requires frame_index")

        frame_key = (source.unit_id, source.frame_index)
        frame = frames.get(frame_key)
        if frame is None:
            frame = _resolve_frame(db, unit, source.frame_index)
            frames[frame_key] = frame
        if clause.facet == "look":
            vector = np.asarray(frame.get("visual_vec"), dtype=np.float32)
            if vector.ndim != 1 or vector.size == 0:
                raise RecipeSourceUnavailable(
                    f"Source frame {frame['frame_id']!r} has no visual vector"
                )
            resolved[clause.clause_id] = _ResolvedSourceEvidence(
                unit=unit,
                frame=frame,
                visual_vector=vector,
            )
            continue

        path = Path(str(frame.get("path") or ""))
        try:
            with Image.open(path) as source_image:
                image = source_image.convert("RGB")
        except (OSError, ValueError, UnidentifiedImageError) as exc:
            raise RecipeSourceUnavailable(
                f"Source frame {frame['frame_id']!r} is unavailable"
            ) from exc
        resolved[clause.clause_id] = _ResolvedSourceEvidence(
            unit=unit,
            frame=frame,
            image=image,
        )
    return resolved


def _recipe_source_evidence(
    clauses: Sequence[SearchClause],
    resolved_sources: dict[str, _ResolvedSourceEvidence],
) -> list[dict[str, Any]]:
    """Describe the authoritative input contributed by each source clause.

    This is product-facing explainability, not ranking evidence for a result.
    Text-backed adapters expose the exact effective text. Visual adapters stay
    explicitly visual instead of inventing an English description for a frame
    vector or spatial grid.
    """
    described: list[dict[str, Any]] = []
    for clause in clauses:
        if clause.kind == "image":
            if clause.image is None:
                raise RecipeSourceUnavailable(
                    "uploaded image source is unavailable"
                )
            if clause.facet not in {"look", "composition"}:
                raise ValueError(
                    "uploaded images support only look or composition"
                )
            spatial = clause.facet == "composition"
            described.append(
                {
                    "clause_id": clause.clause_id,
                    "facet": clause.facet,
                    "source": {"kind": "uploaded_image"},
                    "adapter": (
                        "pe_global+spatial_6x6" if spatial else "pe_global"
                    ),
                    "evidence": [
                        {
                            "type": "image",
                            "mode": (
                                "global_spatial_visual"
                                if spatial
                                else "global_visual"
                            ),
                        }
                    ],
                }
            )
            continue
        if clause.kind != "source" or clause.source is None:
            continue
        source = clause.source
        resolved = resolved_sources[clause.clause_id]
        unit = resolved.unit

        source_payload: dict[str, Any] = {"unit_id": source.unit_id}
        if source.frame_index is not None:
            source_payload["frame_index"] = source.frame_index
        payload: dict[str, Any] = {
            "clause_id": clause.clause_id,
            "facet": clause.facet,
            "source": source_payload,
        }

        if clause.facet == "scene":
            effective_text = resolved.query_text
            payload.update(
                {
                    "adapter": "caption",
                    "effective_text": effective_text,
                    "evidence": [
                        {
                            "type": "text",
                            "view": "caption",
                            "text": effective_text,
                        }
                    ],
                }
            )
        elif clause.facet == "words":
            effective_text = resolved.query_text
            dialogue = _json_strings(
                unit.get("dialogue"),
                field="dialogue",
                unit_id=str(unit["unit_id"]),
            )
            evidence = [
                {"type": "text", "view": "dialogue", "text": line}
                for line in dialogue
            ]
            on_screen_text = str(unit.get("on_screen_text") or "").strip()
            if on_screen_text:
                evidence.append(
                    {
                        "type": "text",
                        "view": "ocr",
                        "text": on_screen_text,
                    }
                )
            payload.update(
                {
                    "adapter": "dialogue+ocr",
                    "effective_text": effective_text,
                    "evidence": evidence,
                }
            )
        elif clause.facet == "mood":
            effective_text = resolved.query_text
            payload.update(
                {
                    "adapter": "mood",
                    "effective_text": effective_text,
                    "evidence": [
                        {
                            "type": "text",
                            "view": "mood",
                            "text": effective_text,
                        }
                    ],
                }
            )
        elif clause.facet in {"look", "composition"}:
            if source.frame_index is None or resolved.frame is None:
                raise RecipeSourceUnavailable(
                    f"{clause.facet} source requires an exact frame"
                )
            frame_index = int(resolved.frame["frame_index"])
            spatial = clause.facet == "composition"
            payload.update(
                {
                    "adapter": (
                        "pe_global+spatial_6x6" if spatial else "pe_global"
                    ),
                    "evidence": [
                        {
                            "type": "frame",
                            "frame_index": frame_index,
                            "mode": (
                                "spatial_visual" if spatial else "global_visual"
                            ),
                        }
                    ],
                }
            )
        else:
            raise ValueError(
                f"facet {clause.facet!r} does not accept a source"
            )
        described.append(payload)
    return described


def _without_sources(
    results: Sequence[dict[str, Any]],
    source_unit_ids: set[str],
) -> list[dict[str, Any]]:
    return [
        result
        for result in results
        if str(result.get("unit_id") or "") not in source_unit_ids
    ]


def _run_text_clause(
    clause: SearchClause,
    query: str,
    db: lancedb.DBConnection,
    config: Config,
    film_ids: Sequence[str],
    result_limit: int,
) -> list[dict[str, Any]]:
    if clause.facet == "all":
        return search(
            query,
            db,
            config,
            film_ids=film_ids,
            result_limit=result_limit,
            _defer_result_preferences=True,
        )
    if clause.facet == "scene":
        return search_semantic_views(
            query,
            ("caption",),
            db,
            config,
            film_ids=film_ids,
            result_limit=result_limit,
        )
    if clause.facet == "words":
        return search_semantic_views(
            query,
            ("dialogue", "ocr"),
            db,
            config,
            film_ids=film_ids,
            result_limit=result_limit,
        )
    if clause.facet == "look":
        return search_look_by_text(
            query,
            db,
            config,
            film_ids=film_ids,
            result_limit=result_limit,
        )
    if clause.facet == "mood":
        return search_semantic_views(
            query,
            ("mood",),
            db,
            config,
            film_ids=film_ids,
            result_limit=result_limit,
        )
    raise ValueError(f"facet {clause.facet!r} does not accept text")


def _run_clause(
    clause: SearchClause,
    db: lancedb.DBConnection,
    config: Config,
    film_ids: Sequence[str],
    result_limit: int,
    resolved_sources: dict[str, _ResolvedSourceEvidence],
) -> _ClauseRanking:
    if clause.kind == "text":
        query = str(clause.text or "").strip()
        return _ClauseRanking(
            clause,
            _run_text_clause(
                clause,
                query,
                db,
                config,
                film_ids,
                result_limit,
            ),
            query,
        )

    if clause.kind == "image":
        if clause.image is None:
            raise RecipeSourceUnavailable("uploaded image source is unavailable")
        if clause.facet == "look":
            return _ClauseRanking(
                clause,
                search_look_by_image(
                    clause.image,
                    db,
                    config,
                    film_ids=film_ids,
                    result_limit=result_limit,
                    _return_candidate_reserve=True,
                ),
                "",
            )
        if clause.facet != "composition":
            raise ValueError(
                "uploaded images support only look or composition"
            )
        return _ClauseRanking(
            clause,
            search_by_image(
                clause.image,
                db,
                config,
                film_ids=film_ids,
                result_limit=result_limit,
                _defer_result_preferences=True,
            ),
            "",
        )

    if clause.source is None:
        raise ValueError(f"source clause {clause.clause_id!r} has no source")
    resolved = resolved_sources.get(clause.clause_id)
    if resolved is None:
        resolved = _resolve_source_clauses((clause,), db)[clause.clause_id]
        resolved_sources[clause.clause_id] = resolved
    unit = resolved.unit
    if clause.facet in {"scene", "words", "mood"}:
        query = resolved.query_text
        return _ClauseRanking(
            clause,
            _run_text_clause(
                clause,
                query,
                db,
                config,
                film_ids,
                result_limit,
            ),
            query,
        )

    if clause.facet == "look":
        if resolved.visual_vector is None:
            raise AssertionError("resolved look source has no visual vector")
        return _ClauseRanking(
            clause,
            search_look_by_vector(
                resolved.visual_vector,
                db,
                config,
                film_ids=film_ids,
                result_limit=result_limit,
            ),
            "",
        )
    if clause.facet == "composition":
        if resolved.image is None:
            raise AssertionError("resolved composition source has no image")
        return _ClauseRanking(
            clause,
            search_by_image(
                resolved.image,
                db,
                config,
                film_ids=film_ids,
                exclude_unit_id=str(unit["unit_id"]),
                # Preserve ADR-0005: result-card composition discovery starts
                # outside the source film unless explicit one-film scope wins.
                exclude_film_id=str(unit["film_id"]),
                result_limit=result_limit,
                _defer_result_preferences=True,
            ),
            "",
        )
    raise ValueError(f"facet {clause.facet!r} does not accept a source")


def _match_evidence(
    clause: SearchClause,
    rank: int,
    result: dict[str, Any],
) -> dict[str, Any]:
    match: dict[str, Any] = {
        "clause_id": clause.clause_id,
        "facet": clause.facet,
        "rank": rank,
    }
    if clause.facet in {"look", "composition"}:
        try:
            frame_index = int(
                result.get("matched_frame_index", result["keyframe_index"])
            )
        except (KeyError, TypeError, ValueError):
            return match
        evidence: dict[str, Any] = {
            "type": "frame",
            "frame_index": frame_index,
        }
        timestamp = result.get("matched_frame_timestamp")
        if isinstance(timestamp, (int, float)):
            evidence["timestamp"] = float(timestamp)
        match["evidence"] = evidence
        return match
    matched_text = result.get("matched_text")
    matched_view = result.get("matched_text_view")
    if isinstance(matched_text, str) and matched_text:
        match["evidence"] = {
            "type": "text",
            "view": str(matched_view or "text"),
            "text": matched_text,
        }
    elif isinstance(result.get("matched_frame_index"), int):
        evidence = {
            "type": "frame",
            "frame_index": int(result["matched_frame_index"]),
        }
        timestamp = result.get("matched_frame_timestamp")
        if isinstance(timestamp, (int, float)):
            evidence["timestamp"] = float(timestamp)
        match["evidence"] = evidence
    return match


def _fuse_rankings(
    rankings: Sequence[_ClauseRanking],
    source_unit_ids: set[str],
) -> list[dict[str, Any]]:
    by_clause: dict[str, dict[str, tuple[int, dict[str, Any]]]] = {}
    for ranking in rankings:
        seen: set[str] = set()
        hits: dict[str, tuple[int, dict[str, Any]]] = {}
        for fallback_rank, result in enumerate(ranking.results, start=1):
            unit_id = str(result.get("unit_id") or "")
            if not unit_id or unit_id in seen or unit_id in source_unit_ids:
                continue
            try:
                rank = int(result.get("rank", fallback_rank))
            except (TypeError, ValueError):
                rank = fallback_rank
            if rank < 1:
                rank = fallback_rank
            seen.add(unit_id)
            hits[unit_id] = (rank, result)
        by_clause[ranking.clause.clause_id] = hits

    mandatory_visual_rankings = [
        ranking
        for ranking in rankings
        if _is_mandatory_visual_clause(ranking.clause)
    ]
    if mandatory_visual_rankings:
        eligible = set.intersection(
            *(
                set(by_clause[ranking.clause.clause_id])
                for ranking in mandatory_visual_rankings
            )
        )
    else:
        eligible = {
            unit_id
            for hits in by_clause.values()
            for unit_id in hits
        }

    fused: list[tuple[float, int, str, dict[str, Any]]] = []
    for unit_id in eligible:
        contributors: list[
            tuple[int, _ClauseRanking, int, dict[str, Any]]
        ] = []
        score = 0.0
        for clause_index, ranking in enumerate(rankings):
            hit = by_clause[ranking.clause.clause_id].get(unit_id)
            if hit is None:
                continue
            rank, result = hit
            score += 1.0 / (_RRF_K + rank)
            contributors.append((clause_index, ranking, rank, result))
        if not contributors:
            continue

        def display_priority(
            contributor: tuple[int, _ClauseRanking, int, dict[str, Any]],
        ) -> tuple[int, int, int]:
            clause_index, ranking, rank, _result = contributor
            facet_priority = (
                0
                if _is_mandatory_visual_clause(ranking.clause)
                else 1
                if ranking.clause.facet == "look"
                else 2
            )
            return facet_priority, rank, clause_index

        base_contributor = min(contributors, key=display_priority)
        base = dict(base_contributor[3])
        base["matches"] = [
            _match_evidence(ranking.clause, rank, result)
            for _index, ranking, rank, result in contributors
        ]
        if not base.get("matched_text"):
            text_contributor = next(
                (
                    result
                    for _index, _ranking, _rank, result in contributors
                    if result.get("matched_text")
                ),
                None,
            )
            if text_contributor is not None:
                base["matched_text_view"] = text_contributor.get(
                    "matched_text_view"
                )
                base["matched_text"] = text_contributor.get("matched_text")
        debug = dict(base.get("debug") or {})
        debug["final_score"] = score
        base["debug"] = debug
        fused.append(
            (
                score,
                min(rank for _index, _ranking, rank, _result in contributors),
                unit_id,
                base,
            )
        )

    fused.sort(key=lambda item: (-item[0], item[1], item[2]))
    return [result for _score, _best_rank, _unit_id, result in fused]


def _annotate_single_clause_results(
    clause: SearchClause,
    results: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Add recipe evidence without changing a normal search's ordering."""
    annotated: list[dict[str, Any]] = []
    for rank, result in enumerate(results, start=1):
        product_result = dict(result)
        product_result["matches"] = [_match_evidence(clause, rank, result)]
        annotated.append(product_result)
    return annotated


def execute_search_recipe(
    clauses: Sequence[SearchClause],
    db: lancedb.DBConnection,
    config: Config,
    *,
    film_ids: Sequence[str] = (),
    result_limit: int | None = None,
) -> SearchRecipeExecution:
    """Run a recipe and return results with its resolved source snapshot."""
    resolved_result_limit = resolve_result_limit(config, result_limit)
    if not 1 <= len(clauses) <= 3:
        raise ValueError("a search recipe requires one to three clauses")
    if len({clause.clause_id for clause in clauses}) != len(clauses):
        raise ValueError("search recipe clause IDs must be unique")
    if len({clause.facet for clause in clauses}) != len(clauses):
        raise ValueError("search recipe facets must be unique")
    image_clauses = [clause for clause in clauses if clause.kind == "image"]
    if len(image_clauses) > 1:
        raise ValueError("a search recipe accepts at most one uploaded image")
    for clause in image_clauses:
        if clause.facet not in {"look", "composition"}:
            raise ValueError(
                "uploaded images support only look or composition"
            )
        if clause.image is None:
            raise RecipeSourceUnavailable("uploaded image source is unavailable")

    normalized_film_ids = tuple(
        dict.fromkeys(
            str(film_id).strip()
            for film_id in film_ids
            if str(film_id).strip()
        )
    )
    only_clause = clauses[0] if len(clauses) == 1 else None
    if (
        only_clause is not None
        and only_clause.kind == "text"
        and only_clause.facet == "all"
    ):
        normal_results = search(
            str(only_clause.text or "").strip(),
            db,
            config,
            film_ids=normalized_film_ids,
            result_limit=resolved_result_limit,
        )
        return SearchRecipeExecution(
            results=_annotate_single_clause_results(only_clause, normal_results),
            source_evidence=[],
        )

    resolved_sources = _resolve_source_clauses(clauses, db)
    source_evidence = _recipe_source_evidence(clauses, resolved_sources)
    result_film_ids = normalized_film_ids
    apply_film_diversity: bool | None = None
    composition_source_clause = next(
        (
            clause
            for clause in clauses
            if clause.facet == "composition" and clause.kind == "source"
        ),
        None,
    )
    if composition_source_clause is not None:
        if composition_source_clause.source is None:
            raise ValueError("composition source requires a source scene")
        composition_unit = resolved_sources[
            composition_source_clause.clause_id
        ].unit
        scope = resolve_reference_result_scope(
            db,
            normalized_film_ids,
            str(composition_unit["film_id"]),
        )
        if scope is None:
            return SearchRecipeExecution(
                results=[],
                source_evidence=source_evidence,
            )
        result_film_ids, apply_film_diversity = scope

    rankings = [
        _run_clause(
            clause,
            db,
            config,
            result_film_ids,
            int(config.retrieval.max_result_limit),
            resolved_sources,
        )
        for clause in clauses
    ]
    source_unit_ids = {
        clause.source.unit_id
        for clause in clauses
        if clause.source is not None
    }
    fused = _fuse_rankings(rankings, source_unit_ids)
    requested_text = " ".join(
        ranking.query_text for ranking in rankings if ranking.query_text
    )
    return SearchRecipeExecution(
        results=apply_recipe_result_preferences(
            _without_sources(fused, source_unit_ids),
            db,
            config,
            film_ids=result_film_ids,
            requested_text=requested_text,
            result_limit=resolved_result_limit,
            apply_reference_temporal_spread=any(
                _is_mandatory_visual_clause(clause)
                for clause in clauses
            ),
            apply_film_diversity=apply_film_diversity,
        ),
        source_evidence=source_evidence,
    )


def search_recipe(
    clauses: Sequence[SearchClause],
    db: lancedb.DBConnection,
    config: Config,
    *,
    film_ids: Sequence[str] = (),
    result_limit: int | None = None,
) -> list[dict[str, Any]]:
    """Run one to three typed clauses and return one final ranked window."""
    return execute_search_recipe(
        clauses,
        db,
        config,
        film_ids=film_ids,
        result_limit=result_limit,
    ).results


__all__ = [
    "RecipeSourceNotFound",
    "RecipeSourceUnavailable",
    "SearchClause",
    "SearchFacet",
    "SearchRecipeExecution",
    "SemanticTextProfileUnavailable",
    "SourceReference",
    "execute_search_recipe",
    "search_recipe",
]
