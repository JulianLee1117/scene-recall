"""Translate bounded editorial recipes into existing search operations only."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path

from lancedb.expr import col, lit

from pipeline.index.reads import filtered_rows
from pipeline.index.writer import table_names
from pipeline.lab.models import Crop, FrozenSearchReference, GeneratedSearchPlan, MusicDirection, MusicSearchPlan
from pipeline.search.capabilities import CAPABILITY_VERSION
from pipeline.search.recipe import SearchClause, SourceReference, execute_search_recipe


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def offered_references(document, db, capabilities, *, exclude_slot_ids=()):
    """Offer one real indexed frame inside each placed clip, never remap later.

    Its ID hashes the full selection and exact frame authority. Trimming or
    replacing the anchor makes a saved recipe stale instead of changing meaning.
    """
    try:
        if not {"units", "frames"}.issubset(table_names(db)):
            return []
        available = {row["facet"] for row in capabilities["facets"] if row["source_available"] is True}
        if not available:
            return []
        clips = {row["id"]: row for row in document["clips"]}
        excluded = set(exclude_slot_ids)
        placed = list(dict.fromkeys(slot["clip_id"] for slot in (document.get("music_timeline") or {}).get("slots", [])
                                    if slot.get("clip_id") and slot["id"] not in excluded))
        result = []
        for identity in placed[:100]:
            clip = clips[identity]
            cropped = clip.get("crop") is not None and Crop.model_validate(clip["crop"]) != Crop()
            frames = filtered_rows(db.open_table("frames"),
                      where=((col("film_id") == lit(clip["film_id"]))
                      & (col("timestamp") >= lit(clip["source_start"])) & (col("timestamp") < lit(clip["source_end"]))),
                      columns=["unit_id", "frame_index", "timestamp", "path", "film_id"], limit=300)
            frames = [row for row in frames if row.get("film_id") == clip["film_id"] and isinstance(row.get("frame_index"), int)
                      and not isinstance(row["frame_index"], bool) and row["frame_index"] >= 0
                      and isinstance(row.get("timestamp"), (int, float)) and math.isfinite(row["timestamp"])
                      and clip["source_start"] <= row["timestamp"] < clip["source_end"]]
            if not frames:
                continue
            frame = min(frames, key=lambda row: (abs(row["timestamp"] - (clip["source_start"] + clip["source_end"]) / 2), row["timestamp"]))
            units = (db.open_table("units").search().where(col("unit_id") == lit(frame["unit_id"]))
                     .select(["unit_id", "film_id", "t_start", "t_end", "caption", "dialogue", "on_screen_text", "mood", "energy"]).limit(2).to_list())
            if len(units) != 1:
                continue
            unit = units[0]
            if unit["unit_id"] != frame["unit_id"] or unit["film_id"] != clip["film_id"] or not unit["t_start"] <= frame["timestamp"] < unit["t_end"]:
                continue
            facets = []
            from pipeline.search.recipe import _source_text
            for facet in sorted(available):
                if cropped and facet in {"look", "composition"}:
                    # The indexed frame represents the original image, not the
                    # manually cropped display. No crop encoder is implied.
                    continue
                if facet in {"scene", "words", "mood"}:
                    try:
                        _source_text(facet, unit)
                    except ValueError:
                        continue
                if facet == "composition" and not Path(str(frame.get("path") or "")).is_file():
                    continue
                facets.append(facet)
            if not facets:
                continue
            authority = {"clip_id": clip["id"], "film_id": clip["film_id"], "unit_id": frame["unit_id"],
                         "frame_index": frame["frame_index"], "timestamp": frame["timestamp"],
                         "source_start": clip["source_start"], "source_end": clip["source_end"]}
            reference = {"reference_id": "ref-" + _hash(authority)[:24], **authority, "available_facets": facets}
            if cropped:
                reference["context_note"] = "Semantic references describe the whole source shot. Indexed Look and Framing do not represent this manual crop."
            result.append(reference)
        return result
    except (AttributeError, TypeError, ValueError, KeyError, OSError, RuntimeError):
        return []


def _authority(reference):
    return FrozenSearchReference.model_validate({key: reference[key] for key in FrozenSearchReference.model_fields}).model_dump(mode="json")


def bind_generated_direction(direction, references, capabilities):
    """Validate the model's requested adapters and add server-owned authority."""
    result = deepcopy(direction)
    if result.get("search_plan") is None:
        available = next(row for row in capabilities["facets"] if row["facet"] == result["search_facet"])
        if available["text_available"] is False:
            raise ValueError(f"The {result['search_facet']} text search adapter is unavailable; choose an available search")
        return MusicDirection.model_validate(result).model_dump(mode="json")
    plan = GeneratedSearchPlan.model_validate(result["search_plan"]).model_dump(mode="json")
    offered = {row["reference_id"]: row for row in references}
    available = {row["facet"]: row for row in capabilities["facets"]}
    frozen = {}
    for clause in plan["clauses"]:
        if available[clause["facet"]][clause["kind"] + "_available"] is not True:
            raise ValueError(f"The {clause['facet']} {clause['kind']} search adapter is not verified ready; choose an available search")
        if clause["kind"] == "source":
            source = offered.get(clause["reference_id"])
            if not source or clause["facet"] not in source["available_facets"]:
                raise ValueError("Search recipe used an unavailable reference; regenerate this direction from current selected clips")
            frozen[source["reference_id"]] = _authority(source)
    result["search_plan"] = {**plan, "references": list(frozen.values())}
    return MusicDirection.model_validate(result).model_dump(mode="json")


def resolve_search(direction, references, capabilities, duration):
    """Old directions remain a single text search. New references fail closed."""
    plan = direction.get("search_plan")
    if plan is None:
        availability = next(row for row in capabilities["facets"] if row["facet"] == direction["search_facet"])["text_available"]
        plan = {"clauses": [{"kind": "text", "facet": direction["search_facet"], "text": direction["query"], "reference_id": None}],
                "references": [], "unverified_requirements": ["Search index readiness could not be verified"] if availability is None else []}
    else:
        parsed = MusicSearchPlan.model_validate(plan)
        original = parsed.model_dump(mode="json")
        bound = bind_generated_direction({**direction, "search_plan": {key: original[key] for key in ("clauses", "unverified_requirements")}}, references, capabilities)["search_plan"]
        stored = {row["reference_id"]: row for row in original["references"]}
        actual = {row["reference_id"]: row for row in bound["references"]}
        if stored != actual:
            raise ValueError("A search reference changed or is no longer selected; regenerate the direction before searching")
        plan = bound
    return {**deepcopy(plan), "capability_version": CAPABILITY_VERSION, "min_duration": duration}


def recipe_key(resolved):
    return _hash({key: resolved[key] for key in ("clauses", "references")})


def execute_search(resolved, document, config, db):
    """Do not silently relax gates, invent motion adapters or alter film scope."""
    from pipeline.lab import music
    clauses = resolved["clauses"]
    preset = (document.get("planner_settings") or {}).get("footage") or "balanced"   # recognizable <-> fresh
    ranking = {} if preset == "balanced" else {"preset": preset}                      # balanced is the default
    if len(clauses) == 1 and clauses[0]["kind"] == "text":
        clause = clauses[0]
        args = (clause["text"], db, config, document.get("film_ids", []))
        rows = (music.retrieve_edit_candidates(*args, **ranking) if clause["facet"] == "all"
                else music.retrieve_edit_candidates(*args, clause["facet"], **ranking))
        return rows
    references = {row["reference_id"]: row for row in resolved["references"]}
    recipe = []
    for index, clause in enumerate(clauses):
        ref = references.get(clause["reference_id"])
        recipe.append(SearchClause(f"clause-{index + 1}", clause["kind"], clause["facet"], text=clause["text"],
                                   source=SourceReference(ref["unit_id"], ref["frame_index"]) if ref else None))
    result = execute_search_recipe(recipe, db, config, film_ids=document.get("film_ids", []),
                                   result_limit=min(48, config.retrieval.max_result_limit), preset=preset,
                                   _preserve_visual_alternatives=True)
    return result.results
