"""Shot filters (ADR-0114): facet derivation, scopes, and enforcement inside retrieval."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from pipeline.search import shot_facets
from pipeline.search.request import bind_unit_scope, search_execution, unit_scope
from pipeline.search.shot_facets import UnitScope, _build_index, validate_shot_filters


def _unit(unit_id, film="talkie", **fields):
    return {"unit_id": unit_id, "film_id": film, "framing": "medium", "setting": "interior",
            "time_of_day": "unknown", "dialogue": "[]", **fields}


def _index():
    units = [
        _unit("a", framing="close_up", dialogue=json.dumps(["Hello."]), time_of_day="night"),
        _unit("b", framing="extreme_wide", setting="exterior"),
        _unit("c", framing="unknown", dialogue=json.dumps(["Bye."])),
        # A film with no dialogue track: its quiet shots are unknown, not "no dialogue".
        _unit("d", film="silent"),
    ]
    evidence = {
        "a": {"colorfulness": 0.31, "camera": "static", "camera_reliability": 0.9, "people": 1.0},
        "b": {"colorfulness": 0.004, "camera": "pan_left", "camera_reliability": 0.95, "people": 4.0},
        "c": {"colorfulness": 0.2, "camera": "push_in", "camera_reliability": 0.2, "people": 0.0},
    }
    return _build_index(units, evidence)


def _values(index, key):
    facet = next(item for item in shot_facets.SHOT_FACETS if item.key == key)
    return [facet.values[code][0] if code >= 0 else None for code in index.codes[key]]


def test_facets_come_from_stored_evidence_and_stay_unknown_without_it() -> None:
    index = _index()
    assert _values(index, "dialogue") == ["spoken", "none", "spoken", None]
    assert _values(index, "size") == ["close", "wide", None, "medium"]
    assert _values(index, "color") == ["color", "bw", "color", None]
    # Unreliable or unmeasured camera movement is unknown.
    assert _values(index, "camera") == ["still", "moving", None, None]
    assert _values(index, "people") == ["one", "group", "none", None]
    assert _values(index, "time") == ["night", None, None, None]
    assert _values(index, "place") == ["interior", "exterior", "interior", "interior"]


def test_values_within_a_facet_are_alternatives_and_unknown_never_passes() -> None:
    index = _index()
    assert index.passing({"size": ["close", "wide"]}).tolist() == [True, True, False, False]
    assert index.passing({"size": ["close", "wide"], "color": ["bw"]}).tolist() == [False, True, False, False]
    assert index.passing({"dialogue": ["none"]}).tolist() == [False, True, False, False]


def test_a_scope_masks_any_unit_order_and_reads_deeper_when_narrow() -> None:
    index = _index()
    scope = UnitScope(index, index.passing({"dialogue": ["spoken"]}), (("dialogue", ("spoken",)),))
    assert scope.allows("a") and not scope.allows("b") and not scope.allows("missing")
    assert scope.mask_for(["c", "zzz", "a", "b"], ("matrix", 1)).tolist() == [True, False, True, False]
    assert scope.fraction == 0.5
    assert scope.depth(600) == 1200
    narrow = UnitScope(index, np.zeros(4, dtype=bool), ())
    assert narrow.depth(600) == 600 * 25


def test_unknown_facets_and_values_are_rejected_and_empty_ones_dropped() -> None:
    assert validate_shot_filters({"size": ["close", "close"], "color": []}) == {"size": ("close",)}
    with pytest.raises(ValueError, match="unknown shot filter"):
        validate_shot_filters({"lens": ["wide"]})
    with pytest.raises(ValueError, match="unknown size value"):
        validate_shot_filters({"size": ["huge"]})


def test_resident_channels_mask_shots_before_ranking() -> None:
    torch = pytest.importorskip("torch")
    from pipeline.search.resident import VectorMatrix

    index = _index()
    scope = UnitScope(index, index.passing({"size": ["close", "wide"]}), (("size", ("close", "wide")),))
    matrix = VectorMatrix(
        name="frames", version=3, device=torch.device("cpu"),
        vectors=torch.zeros((4, 2)), row_unit=torch.tensor([0, 1, 1, 2]), row_group=torch.zeros(4, dtype=torch.int16),
        groups=["frame"], unit_ids=["a", "b", "c"], unit_film=torch.tensor([0, 0, 1]), films=["talkie", "other"],
        row_keys=np.array(["f0", "f1", "f2", "f3"], dtype=object), row_extra={},
    )

    @search_execution
    def masked(film_ids):
        bind_unit_scope(scope)
        return matrix.allowed_rows(film_ids).tolist()

    assert masked(()) == [True, True, True, False]
    assert masked(("other",)) == [False, False, False, False]
    assert matrix.allowed_rows(()) is None, "outside a filtered search nothing is masked"


def test_database_paths_drop_shots_outside_the_scope() -> None:
    from pipeline.search.retrieve import _rows_in_scope

    index = _index()
    scope = UnitScope(index, index.passing({"place": ["exterior"]}), (("place", ("exterior",)),))
    rows = [{"unit_id": "a", "film_id": "talkie"}, {"unit_id": "b", "film_id": "talkie"}]

    @search_execution
    def filtered():
        bind_unit_scope(scope)
        return [row["unit_id"] for row in _rows_in_scope(rows, ())]

    assert filtered() == ["b"]
    assert [row["unit_id"] for row in _rows_in_scope(rows, ())] == ["a", "b"]


def test_a_recipe_binds_its_shot_filters_for_every_clause() -> None:
    from pipeline.search import recipe

    seen = []
    sentinel = object()

    def fake_search(*_args, **_kwargs):
        seen.append(unit_scope())
        return []

    with (
        patch.object(recipe, "unit_scope", return_value=sentinel) as build,
        patch.object(recipe, "search", side_effect=fake_search),
    ):
        recipe.execute_search_recipe(
            [recipe.SearchClause(clause_id="main", kind="text", facet="all", text="rain")],
            MagicMock(), MagicMock(), shot_filters={"dialogue": ["none"]},
        )
    assert build.call_args.args[1] == {"dialogue": ("none",)}
    assert seen == [sentinel]


def test_api_rejects_unknown_shot_filters() -> None:
    from pydantic import ValidationError

    from pipeline.api.main import SearchRecipeRequest

    clause = {"id": "main", "kind": "text", "facet": "all", "text": "rain"}
    request = SearchRecipeRequest(clauses=[clause], shot_filters={"color": ["bw"], "time": []})
    assert request.shot_filters == {"color": ["bw"]}
    with pytest.raises(ValidationError):
        SearchRecipeRequest(clauses=[clause], shot_filters={"lens": ["wide"]})
