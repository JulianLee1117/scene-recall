"""One bounded scene-selection interface for fixed and provisional timelines.

The model chooses one offered source alias with its own trim control. Required
shot keys and per-shot constraints keep source/cut authority in the server's
offers; committed projects still contain ordinary source-backed clips.
"""
from __future__ import annotations

from copy import deepcopy
import math
from typing import Annotated, Literal

from pydantic import Field, ValidationError

from pipeline.lab.models import LabModel
from pipeline.lab.limits import MAX_TIMELINE_SLOTS
from pipeline.lab.source_timing import fit_cut_preferences, frame_time


SELECTION_CONTRACT = "scoped-scene-choices-v3"
TIMED_SELECTION_CONTRACT = "scoped-source-timing-preferences-v1"


def response_contract(scope=None, *, inspection=False):
    base = TIMED_SELECTION_CONTRACT if scope is not None else SELECTION_CONTRACT
    return base + "+inspection-hints-v1" if inspection else base


class SceneChoice(LabModel):
    source: dict[str, Annotated[float, Field(ge=0, strict=True)]] | None = Field(min_length=1, max_length=1)
    reason: str = Field(min_length=1, max_length=600)


class TimedSceneChoice(SceneChoice):
    source: dict[str, Annotated[float, Field(ge=0, le=1, strict=True)]] | None = Field(min_length=1, max_length=1)
    preferred_end_frame: int = Field(ge=1, strict=True)


class InspectedSceneChoice(SceneChoice):
    inspection_hint: Literal["action_timing", "visual_fit", "none"]


class InspectedTimedSceneChoice(TimedSceneChoice):
    inspection_hint: Literal["action_timing", "visual_fit", "none"]


def _choice_model(scope, inspection):
    if inspection:
        return InspectedTimedSceneChoice if scope is not None else InspectedSceneChoice
    return TimedSceneChoice if scope is not None else SceneChoice


def _shot_offers(offers, scope):
    maximum = 32 if scope is not None else 100
    if not 1 <= len(offers) <= maximum:
        raise ValueError(f"Scene selection requires between 1 and {maximum} offered shots")
    result = {}
    for offer in offers:
        slot = offer["slot"]
        if isinstance(slot, bool) or not isinstance(slot, int) or not 0 <= slot < MAX_TIMELINE_SLOTS:
            raise ValueError("Scene selection needs valid timeline slot indices")
        key = f"shot_{slot}"
        if key in result:
            raise ValueError("Scene selection cannot offer a shot twice")
        result[key] = offer
    return result


def _object(properties):
    return {"type": "object", "properties": properties,
            "required": list(properties), "additionalProperties": False}


def source_aliases(sources):
    """Share the prompt catalog's stable, request-local names."""
    return {identity: f"c{index}" for index, identity in enumerate(sources)}


def _offered_aliases(offers):
    # Retrieval may retain additional rows; only the offered union determines
    # aliases, in the same order as the catalog assembled for the prompt.
    return source_aliases(dict.fromkeys(identity for offer in offers for identity in offer["candidate_ids"]))


def _schema_limits(schema):
    """Fail locally if future scope changes exceed hosted strict-schema limits."""
    properties = enums = strings = maximum_depth = 0

    def visit(node, depth=0):
        nonlocal properties, enums, strings, maximum_depth
        if isinstance(node, dict):
            if node.get("type") in ("object", "array"):
                depth += 1
                maximum_depth = max(maximum_depth, depth)
            properties += len(node.get("properties", {}))
            enums += len(node.get("enum", []))
            strings += sum(len(name) for name in node.get("properties", {}))
            strings += sum(len(name) for name in node.get("$defs", {}))
            strings += sum(len(value) for value in node.get("enum", []) if isinstance(value, str))
            if isinstance(node.get("const"), str):
                strings += len(node["const"])
            for value in node.values():
                visit(value, depth)
        elif isinstance(node, list):
            for value in node:
                visit(value, depth)

    visit(schema)
    if properties > 5000 or enums > 1000 or strings > 120000 or maximum_depth > 10:
        raise ValueError("Scene selection exceeds the supported response schema limits")


def selection_schema(offers, sources, scope=None, *, inspection=False):
    """Bind aliases to exact starts, or normalized positions for flexible cuts.

    A source-keyed number needs only one property per candidate. Shared shot
    fields stay outside the union, preserving all 24 candidates for 100 fixed
    shots without exceeding the provider's 5000-property limit.
    """
    requested = _shot_offers(offers, scope)
    aliases = _offered_aliases(offers)
    base = _choice_model(scope, inspection).model_json_schema()
    shots = {}
    for key, offer in requested.items():
        shot = deepcopy(base)
        branches = []
        minimum_duration = offer["timing"]["min_duration"] if scope is not None else offer["duration"]
        for identity in offer["candidate_ids"]:
            source = sources.get(identity)
            if source is None:
                raise ValueError("An offered scene is unavailable")
            lower, upper = source["t_start"], source["t_end"] - minimum_duration
            if not math.isfinite(lower) or not math.isfinite(upper) or lower < 0 or upper < lower - 1e-6:
                raise ValueError("An offered scene cannot fit the minimum shot duration")
            bounds = {"minimum": 0., "maximum": 1.} if scope is not None else {"minimum": lower, "maximum": max(lower, upper)}
            branches.append(_object({aliases[identity]: {"type": "number", **bounds}}))
        shot["properties"]["source"] = {"anyOf": [*branches, {"type": "null"}]} if branches else {"type": "null"}
        if scope is not None:
            shot["properties"]["preferred_end_frame"]["enum"] = list(offer["timing"]["end_frames"])
        shots[key] = shot
    schema = _object({"choices": _object(shots)})
    _schema_limits(schema)
    return schema


def selection_manifest(offers, sources, scope=None, *, inspection=False):
    """Keep the private alias-to-source map beside the hosted receipt on failure."""
    shots = {}
    for key, offer in _shot_offers(offers, scope).items():
        shots[key] = {field: deepcopy(offer[field]) for field in ("slot", "start", "duration", "candidate_ids")}
        if scope is not None:
            shots[key]["timing"] = deepcopy(offer["timing"])
    return {"contract": response_contract(scope, inspection=inspection), "shots": shots,
            "alias_to_source": {alias: identity for identity, alias in _offered_aliases(offers).items()},
            "timing_scope": deepcopy(scope),
            "sources": {identity: {key: source[key] for key in ("film_id", "t_start", "t_end")}
                        for identity, source in sources.items()}}


def _invalid(message, slot=None):
    shot = f" for shot {slot + 1}" if slot is not None else ""
    return ValueError(f"The AI {message}{shot}. Your saved edit is unchanged. Try generating again.")


def selection_choices(output, offers, sources, scope=None, *, inspection=False):
    """Resolve offered aliases and validate the complete edit before applying."""
    requested = _shot_offers(offers, scope)
    aliases = _offered_aliases(offers)
    if (not isinstance(output, dict) or set(output) != {"choices"}
            or not isinstance(output["choices"], dict) or set(output["choices"]) != set(requested)):
        raise _invalid("returned an incomplete scene selection")
    model = _choice_model(scope, inspection)
    parsed = []
    for key, offer in requested.items():
        slot = offer["slot"]
        try:
            choice = model.model_validate(output["choices"][key])
        except ValidationError as exc:
            raise _invalid("returned an invalid scene choice", slot) from exc
        if scope is not None:
            if choice.preferred_end_frame not in offer["timing"]["end_frames"]:
                raise _invalid("chose an unavailable cut", slot)
        identity, source_value, candidate = None, None, None
        if choice.source is not None:
            alias, source_value = next(iter(choice.source.items()))
            allowed = {aliases[identity]: identity for identity in offer["candidate_ids"]}
            if alias not in allowed:
                raise _invalid("chose an unavailable scene", slot)
            identity = allowed[alias]
            candidate = sources.get(identity)
            if candidate is None:
                raise ValueError("An offered scene is unavailable. Your saved edit is unchanged. Try generating again.")
            if not math.isfinite(source_value):
                raise _invalid("returned an invalid source position", slot)
        parsed.append((offer, choice, identity, source_value, candidate))

    fitted = fit_cut_preferences(
        scope, list(requested.values()), [row[1].preferred_end_frame for row in parsed],
        [row[4]["t_end"] - row[4]["t_start"] if row[4] is not None else None for row in parsed],
    ) if scope is not None else None
    cursor = scope["passage"]["start"] if scope is not None else None
    result = []
    for index, (offer, choice, identity, source_value, candidate) in enumerate(parsed):
        slot = offer["slot"]
        start, duration = offer["start"], offer["duration"]
        if fitted is not None:
            start = cursor
            cursor = frame_time(scope, fitted[index])
            duration = cursor - start
        if duration < 1 / 24 - 1e-6:
            raise _invalid("returned a shot shorter than one output frame", slot)
        source_start = None
        if candidate is not None:
            if fitted is None:
                source_start = source_value
            else:
                span = candidate["t_end"] - candidate["t_start"] - duration
                # The solver permits only the existing microsecond float tolerance.
                # Zero handles absorb roundoff, never an infeasible chosen duration.
                source_start = candidate["t_start"] + source_value * max(0., span)
            if (source_start < candidate["t_start"] - 1e-6
                    or source_start + duration > candidate["t_end"] + 1e-6):
                raise _invalid("chose timing outside the available footage", slot)
        row = choice.model_dump(mode="json", exclude={"source"})
        if fitted is not None:
            row.update(end_frame=fitted[index], source_position=source_value)
        result.append({**row, "slot": slot, "candidate_id": identity, "source_start": source_start,
                       "start": start, "duration": duration})
    return result
