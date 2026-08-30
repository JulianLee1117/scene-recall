"""Focused tests for typed modular search recipes."""

from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from pipeline.config import Config
from pipeline.index.text_features import TextIndexProfile
from pipeline.search.recipe import (
    RecipeSourceNotFound,
    RecipeSourceUnavailable,
    SearchClause,
    SearchRecipeExecution,
    SemanticTextProfileUnavailable,
    SourceReference,
    _ClauseRanking,
    _ResolvedSourceEvidence,
    _fuse_rankings,
    _recipe_source_evidence,
    _run_clause,
    execute_search_recipe,
)


def _result(unit_id: str, *, frame_index: int = 0) -> dict:
    return {
        "unit_id": unit_id,
        "film_id": f"film-{unit_id}",
        "t_start": 1.0,
        "t_end": 2.0,
        "caption": f"caption {unit_id}",
        "keyframe_url": f"/media/keyframe/{unit_id}/{frame_index}",
        "keyframe_index": frame_index,
        "preview_url": f"/media/preview/{unit_id}",
        "rank": 1,
        "debug": {"final_score": 1.0},
    }


def _chain(rows: list[dict]) -> MagicMock:
    query = MagicMock()
    query.metric.return_value = query
    query.select.return_value = query
    query.where.return_value = query
    query.limit.return_value = query
    query.to_list.return_value = rows
    return query


def _png_bytes(color: str = "red") -> bytes:
    payload = BytesIO()
    Image.new("RGB", (8, 6), color).save(payload, format="PNG")
    return payload.getvalue()


def test_equal_rrf_promotes_cross_facet_agreement() -> None:
    all_clause = SearchClause("main", "text", "all", text="night")
    mood_clause = SearchClause("mood", "text", "mood", text="uneasy")
    rankings = [
        _ClauseRanking(all_clause, [_result("a"), _result("b")], "night"),
        _ClauseRanking(mood_clause, [_result("b"), _result("c")], "uneasy"),
    ]

    fused = _fuse_rankings(rankings, set())

    assert [result["unit_id"] for result in fused] == ["b", "a", "c"]
    assert [match["facet"] for match in fused[0]["matches"]] == ["all", "mood"]


def test_broad_recipe_clause_defers_product_preferences(config: Config) -> None:
    clause = SearchClause("main", "text", "all", text="night")

    with patch(
        "pipeline.search.recipe.search",
        return_value=[_result("match")],
    ) as broad_search:
        ranking = _run_clause(clause, MagicMock(), config, (), 100, {})

    assert ranking.results[0]["unit_id"] == "match"
    assert broad_search.call_args.kwargs == {
        "film_ids": (),
        "result_limit": 100,
        "_defer_result_preferences": True,
    }


def test_mood_text_clause_searches_only_the_narrow_mood_view(
    config: Config,
) -> None:
    clause = SearchClause("mood", "text", "mood", text="uneasy and calm")
    db = MagicMock()

    with patch(
        "pipeline.search.recipe.search_semantic_views",
        return_value=[_result("match")],
    ) as semantic_search:
        ranking = _run_clause(clause, db, config, ("film-a",), 100, {})

    assert ranking.results[0]["unit_id"] == "match"
    semantic_search.assert_called_once_with(
        "uneasy and calm",
        ("mood",),
        db,
        config,
        film_ids=("film-a",),
        result_limit=100,
    )


def test_mood_source_uses_the_same_mood_and_energy_contract(
    config: Config,
) -> None:
    clause = SearchClause(
        "mood",
        "source",
        "mood",
        source=SourceReference("unit-a"),
    )
    unit = {
        "unit_id": "unit-a",
        "film_id": "film-a",
        "shot_id": "unit-a",
        "caption": "A figure waits.",
        "dialogue": "[]",
        "on_screen_text": "",
        "mood": '["uneasy", "quiet"]',
        "energy": "calm",
    }
    units = MagicMock()
    units.search.return_value = _chain([unit])
    db = MagicMock()
    db.open_table.return_value = units

    with patch(
        "pipeline.search.recipe.search_semantic_views",
        return_value=[_result("match")],
    ) as semantic_search:
        ranking = _run_clause(clause, db, config, (), 100, {})

    assert ranking.query_text == "mood: uneasy, quiet; energy: calm"
    semantic_search.assert_called_once_with(
        ranking.query_text,
        ("mood",),
        db,
        config,
        film_ids=(),
        result_limit=100,
    )


def test_source_evidence_exposes_exact_text_without_describing_vectors() -> None:
    unit = {
        "unit_id": "unit-a",
        "film_id": "film-a",
        "shot_id": "unit-a",
        "caption": "A figure waits beneath a red light.",
        "dialogue": '["Who is there?", "Come in."]',
        "on_screen_text": "ROOM 237",
        "mood": '["uneasy", "quiet"]',
        "energy": "calm",
    }
    clauses = [
        SearchClause(
            "scene",
            "source",
            "scene",
            source=SourceReference("unit-a", 1),
        ),
        SearchClause(
            "words",
            "source",
            "words",
            source=SourceReference("unit-a", 1),
        ),
        SearchClause(
            "mood",
            "source",
            "mood",
            source=SourceReference("unit-a", 1),
        ),
        SearchClause(
            "look",
            "source",
            "look",
            source=SourceReference("unit-a", 1),
        ),
        SearchClause(
            "framing",
            "source",
            "composition",
            source=SourceReference("unit-a", 1),
        ),
    ]
    frame = {"frame_index": 1, "timestamp": 12.5}
    resolved_sources = {
        "scene": _ResolvedSourceEvidence(
            unit=unit,
            query_text=unit["caption"],
        ),
        "words": _ResolvedSourceEvidence(
            unit=unit,
            query_text="Who is there? Come in. ROOM 237",
        ),
        "mood": _ResolvedSourceEvidence(
            unit=unit,
            query_text="mood: uneasy, quiet; energy: calm",
        ),
        "look": _ResolvedSourceEvidence(
            unit=unit,
            frame=frame,
            visual_vector=np.ones(4, dtype=np.float32),
        ),
        "framing": _ResolvedSourceEvidence(
            unit=unit,
            frame=frame,
            image=Image.new("RGB", (4, 4), "red"),
        ),
    }

    evidence = _recipe_source_evidence(clauses, resolved_sources)

    by_facet = {item["facet"]: item for item in evidence}
    assert by_facet["scene"]["effective_text"] == unit["caption"]
    assert by_facet["words"]["effective_text"] == (
        "Who is there? Come in. ROOM 237"
    )
    assert by_facet["words"]["evidence"] == [
        {"type": "text", "view": "dialogue", "text": "Who is there?"},
        {"type": "text", "view": "dialogue", "text": "Come in."},
        {"type": "text", "view": "ocr", "text": "ROOM 237"},
    ]
    assert by_facet["mood"]["effective_text"] == (
        "mood: uneasy, quiet; energy: calm"
    )
    assert "effective_text" not in by_facet["look"]
    assert by_facet["look"]["adapter"] == "pe_global"
    assert "effective_text" not in by_facet["composition"]
    assert by_facet["composition"]["adapter"] == (
        "pe_global+spatial_6x6"
    )


def test_recipe_execution_reuses_one_source_snapshot_for_search_and_evidence(
    config: Config,
) -> None:
    clause = SearchClause(
        "scene",
        "source",
        "scene",
        source=SourceReference("unit-a"),
    )
    unit = {
        "unit_id": "unit-a",
        "film_id": "film-a",
        "shot_id": "unit-a",
        "caption": "A figure waits beneath a red light.",
        "dialogue": "[]",
        "on_screen_text": "",
        "mood": '["uneasy"]',
        "energy": "calm",
    }
    units = MagicMock()
    units.search.return_value = _chain([unit])
    db = MagicMock()
    db.open_table.return_value = units
    result = _result("match")

    with (
        patch(
            "pipeline.search.recipe.search_semantic_views",
            return_value=[result],
        ) as semantic_search,
        patch(
            "pipeline.search.recipe.apply_recipe_result_preferences",
            side_effect=lambda rows, *_args, **_kwargs: list(rows),
        ),
    ):
        execution = execute_search_recipe((clause,), db, config)

    semantic_search.assert_called_once_with(
        unit["caption"],
        ("caption",),
        db,
        config,
        film_ids=(),
        result_limit=int(config.retrieval.max_result_limit),
    )
    assert execution.results[0]["unit_id"] == "match"
    assert execution.source_evidence[0]["effective_text"] == unit["caption"]
    assert db.open_table.call_count == 1


def test_semantic_view_retrieval_keeps_only_requested_views() -> None:
    from pipeline.search.retrieve import _semantic_text_search_rows

    profile = TextIndexProfile(
        profile_id="qwen-v1",
        table_name="unit_text_qwen_v1",
        model_id="Qwen/test",
        model_revision="abc",
        dimension=3,
    )
    features = MagicMock()
    features.search.return_value = _chain(
        [
            {
                "feature_id": "a::text::dialogue",
                "profile_id": profile.profile_id,
                "film_id": "film-a",
                "unit_id": "a",
                "view": "dialogue",
                "text": "Meet me outside.",
                "_distance": 0.1,
            },
            {
                "feature_id": "b::text::caption",
                "profile_id": profile.profile_id,
                "film_id": "film-a",
                "unit_id": "b",
                "view": "caption",
                "text": "A person waits outside.",
                "_distance": 0.01,
            },
        ]
    )
    units = MagicMock()
    units.search.return_value = _chain(
        [
            {
                **_result("a"),
                "shot_id": "a",
                "dialogue": '["Meet me outside."]',
                "keyframe_paths": '["a.webp"]',
                "img_vec": [1.0, 0.0, 0.0],
            }
        ]
    )
    db = MagicMock()
    db.open_table.side_effect = lambda name: {
        profile.table_name: features,
        "units": units,
    }[name]

    rows = _semantic_text_search_rows(
        np.array([1.0, 0.0, 0.0], dtype=np.float32),
        db,
        units,
        profile,
        (),
        candidate_limit=10,
        allowed_views=("dialogue", "ocr"),
    )

    assert [row["unit_id"] for row in rows] == ["a"]
    features.search.return_value.limit.assert_called_once_with(20)


def test_composition_is_mandatory_and_source_units_are_excluded() -> None:
    composition = SearchClause(
        "composition",
        "source",
        "composition",
        source=SourceReference("source", 1),
    )
    scene = SearchClause("scene", "text", "scene", text="running")
    composition_results = [_result("source"), _result("b"), _result("c")]
    for result in composition_results:
        result["matched_frame_index"] = 1
    rankings = [
        _ClauseRanking(composition, composition_results, ""),
        _ClauseRanking(scene, [_result("a"), _result("b")], "running"),
    ]

    fused = _fuse_rankings(rankings, {"source"})

    assert [result["unit_id"] for result in fused] == ["b", "c"]
    assert fused[0]["keyframe_index"] == 0
    assert [match["facet"] for match in fused[0]["matches"]] == [
        "composition",
        "scene",
    ]


def test_uploaded_visual_is_a_mandatory_correlated_candidate_gate() -> None:
    visual = SearchClause(
        "visual",
        "image",
        "visual",
        image=Image.new("RGB", (8, 6), "red"),
    )
    scene = SearchClause("scene", "text", "scene", text="running")
    visual_results = [_result("b"), _result("c")]
    for result in visual_results:
        result["matched_frame_index"] = 1
    rankings = [
        _ClauseRanking(visual, visual_results, ""),
        _ClauseRanking(scene, [_result("a"), _result("b")], "running"),
    ]

    fused = _fuse_rankings(rankings, set())

    assert [result["unit_id"] for result in fused] == ["b", "c"]
    assert [match["facet"] for match in fused[0]["matches"]] == [
        "visual",
        "scene",
    ]


def test_internal_visual_facet_requires_an_uploaded_image(
    config: Config,
) -> None:
    clause = SearchClause("visual", "text", "visual", text="red room")

    with pytest.raises(ValueError, match="requires an uploaded image"):
        execute_search_recipe([clause], MagicMock(), config)


@pytest.mark.parametrize("facet", ["look", "composition"])
def test_internal_uploaded_image_requires_visual_facet(
    facet: str,
    config: Config,
) -> None:
    clause = SearchClause(
        facet,
        "image",
        facet,
        image=Image.new("RGB", (8, 6), "red"),
    )

    with pytest.raises(ValueError, match="require the visual facet"):
        execute_search_recipe([clause], MagicMock(), config)


def test_cross_facet_agreement_wins_before_final_visual_dedupe(
    config: Config,
) -> None:
    from pipeline.search.retrieve import apply_recipe_result_preferences

    scene = SearchClause("scene", "text", "scene", text="figure")
    mood = SearchClause("mood", "text", "mood", text="lonely")
    rankings = [
        _ClauseRanking(
            scene,
            [_result("single"), _result("agreement"), _result("distinct")],
            "figure",
        ),
        _ClauseRanking(mood, [_result("agreement")], "lonely"),
    ]
    fused = _fuse_rankings(rankings, set())

    def preference_row(unit_id: str, vector: list[float]) -> dict:
        return {
            "unit_id": unit_id,
            "film_id": f"film-{unit_id}",
            "shot_id": unit_id,
            "t_start": 1.0,
            "t_end": 2.0,
            "is_representative": True,
            "caption": f"caption {unit_id}",
            "searchable_text": "figure",
            "dialogue": "[]",
            "keyframe_paths": f'["{unit_id}.webp"]',
            "img_vec": vector,
        }

    units = MagicMock()
    units.search.return_value = _chain(
        [
            preference_row("single", [1.0, 0.0]),
            preference_row("agreement", [1.0, 0.0]),
            preference_row("distinct", [0.0, 1.0]),
        ]
    )
    db = MagicMock()
    db.open_table.return_value = units

    final = apply_recipe_result_preferences(
        fused,
        db,
        config,
        film_ids=("film-single", "film-agreement", "film-distinct"),
        requested_text="figure lonely",
        result_limit=3,
    )

    assert [result["unit_id"] for result in final] == [
        "agreement",
        "distinct",
    ]


def test_composition_scope_applies_to_every_bounded_clause(
    tmp_path: Path,
    config: Config,
) -> None:
    from pipeline.search.recipe import search_recipe

    composition = SearchClause(
        "composition",
        "source",
        "composition",
        source=SourceReference("source", 0),
    )
    scene = SearchClause("scene", "text", "scene", text="running")
    observed_scopes: list[tuple[str, ...]] = []
    image_path = tmp_path / "source.webp"
    Image.new("RGB", (32, 18), "red").save(image_path)
    frame = {
        "frame_id": "source::frame::0",
        "film_id": "film-source",
        "unit_id": "source",
        "frame_index": 0,
        "timestamp": 1.0,
        "path": str(image_path),
        "visual_vec": [1.0, 0.0],
    }

    def run_clause(
        clause: SearchClause,
        _db: MagicMock,
        _config: Config,
        film_ids: tuple[str, ...],
        _result_limit: int,
        _source_units: dict,
    ) -> _ClauseRanking:
        observed_scopes.append(tuple(film_ids))
        return _ClauseRanking(clause, [_result("match")], clause.text or "")

    with (
        patch(
            "pipeline.search.recipe._resolve_unit",
            return_value={"unit_id": "source", "film_id": "film-source"},
        ),
        patch("pipeline.search.recipe._resolve_frame", return_value=frame),
        patch(
            "pipeline.search.recipe.resolve_reference_result_scope",
            return_value=(("film-other",), True),
        ) as resolve_scope,
        patch(
            "pipeline.search.recipe._run_clause",
            side_effect=run_clause,
        ),
        patch(
            "pipeline.search.recipe.apply_recipe_result_preferences",
            return_value=[],
        ) as final_preferences,
    ):
        search_recipe([composition, scene], MagicMock(), config)

    assert observed_scopes == [("film-other",), ("film-other",)]
    resolve_scope.assert_called_once()
    assert final_preferences.call_args.kwargs["film_ids"] == ("film-other",)
    assert final_preferences.call_args.kwargs["apply_film_diversity"] is True


def test_uploaded_visual_applies_temporal_spread_after_recipe_fusion(
    config: Config,
) -> None:
    visual = SearchClause(
        "visual",
        "image",
        "visual",
        image=Image.new("RGB", (8, 6), "red"),
    )
    scene = SearchClause("scene", "text", "scene", text="running")

    with (
        patch(
            "pipeline.search.recipe._run_clause",
            side_effect=lambda clause, *_args, **_kwargs: _ClauseRanking(
                clause,
                [_result("match")],
                clause.text or "",
            ),
        ),
        patch(
            "pipeline.search.recipe.apply_recipe_result_preferences",
            return_value=[],
        ) as final_preferences,
    ):
        execute_search_recipe([visual, scene], MagicMock(), config)

    assert (
        final_preferences.call_args.kwargs[
            "apply_reference_temporal_spread"
        ]
        is True
    )


def test_source_words_require_dialogue_or_ocr(config: Config) -> None:
    clause = SearchClause(
        "words",
        "source",
        "words",
        source=SourceReference("unit-a"),
    )
    units = MagicMock()
    units.search.return_value = _chain(
        [
            {
                "unit_id": "unit-a",
                "film_id": "film-a",
                "shot_id": "unit-a",
                "caption": "A silent room",
                "dialogue": "[]",
                "on_screen_text": "",
                "mood": '["quiet", "still"]',
            }
        ]
    )
    db = MagicMock()
    db.open_table.return_value = units

    with pytest.raises(RecipeSourceUnavailable, match="no dialogue"):
        _run_clause(clause, db, config, (), 100, {})


def test_look_source_uses_stored_frame_vector(
    config: Config,
) -> None:
    clause = SearchClause(
        "look",
        "source",
        "look",
        source=SourceReference("unit-a", 1),
    )
    unit = {
        "unit_id": "unit-a",
        "film_id": "film-a",
        "shot_id": "unit-a",
        "caption": "A red room",
        "dialogue": "[]",
        "on_screen_text": "",
        "mood": '["tense", "warm"]',
    }
    vector = [1.0, 0.0, 0.0]
    frame = {
        "frame_id": "unit-a::frame::1",
        "film_id": "film-a",
        "unit_id": "unit-a",
        "frame_index": 1,
        "timestamp": 3.0,
        "path": "unused.webp",
        "visual_vec": vector,
    }
    units = MagicMock()
    units.search.return_value = _chain([unit])
    frames = MagicMock()
    frames.search.return_value = _chain([frame])
    db = MagicMock()
    db.open_table.side_effect = lambda name: {"units": units, "frames": frames}[name]

    with patch(
        "pipeline.search.recipe.search_look_by_vector",
        return_value=[_result("match")],
    ) as look_search:
        ranking = _run_clause(clause, db, config, ("film-b",), 100, {})

    np.testing.assert_array_equal(look_search.call_args.args[0], vector)
    assert look_search.call_args.kwargs == {
        "film_ids": ("film-b",),
        "result_limit": 100,
    }
    assert ranking.results[0]["unit_id"] == "match"


def test_uploaded_visual_uses_one_global_spatial_reference_ranking(
    config: Config,
) -> None:
    image = Image.new("RGB", (8, 6), "red")
    clause = SearchClause("visual", "image", "visual", image=image)

    with patch(
        "pipeline.search.recipe.search_by_image",
        return_value=[_result("match")],
    ) as visual_search:
        ranking = _run_clause(
            clause,
            MagicMock(),
            config,
            ("film-a",),
            100,
            {},
        )

    assert visual_search.call_args.args[0] is image
    assert visual_search.call_args.kwargs == {
        "film_ids": ("film-a",),
        "result_limit": 100,
        "_defer_result_preferences": True,
    }
    assert ranking.results[0]["unit_id"] == "match"


def test_uploaded_image_source_evidence_is_explicit() -> None:
    clauses = [
        SearchClause(
            "visual",
            "image",
            "visual",
            image=Image.new("RGB", (4, 4), "green"),
        ),
    ]

    evidence = _recipe_source_evidence(clauses, {})

    assert evidence == [
        {
            "clause_id": "visual",
            "facet": "visual",
            "source": {"kind": "uploaded_image"},
            "adapter": "pe_global+spatial_6x6",
            "evidence": [
                {"type": "image", "mode": "global_spatial_visual"}
            ],
        },
    ]


def test_composition_source_preserves_cross_film_policy(
    tmp_path: Path,
    config: Config,
) -> None:
    image_path = tmp_path / "source.webp"
    Image.new("RGB", (32, 18), "red").save(image_path)
    clause = SearchClause(
        "composition",
        "source",
        "composition",
        source=SourceReference("unit-a", 0),
    )
    unit = {
        "unit_id": "unit-a",
        "film_id": "film-a",
        "shot_id": "unit-a",
        "caption": "A red room",
        "dialogue": "[]",
        "on_screen_text": "",
        "mood": '["tense", "warm"]',
    }
    frame = {
        "frame_id": "unit-a::frame::0",
        "film_id": "film-a",
        "unit_id": "unit-a",
        "frame_index": 0,
        "timestamp": 3.0,
        "path": str(image_path),
        "visual_vec": [1.0, 0.0],
    }
    units = MagicMock()
    units.search.return_value = _chain([unit])
    frames = MagicMock()
    frames.search.return_value = _chain([frame])
    db = MagicMock()
    db.open_table.side_effect = lambda name: {"units": units, "frames": frames}[name]

    with patch(
        "pipeline.search.recipe.search_by_image",
        return_value=[],
    ) as composition_search:
        _run_clause(clause, db, config, (), 100, {})

    assert isinstance(composition_search.call_args.args[0], Image.Image)
    assert composition_search.call_args.kwargs == {
        "film_ids": (),
        "exclude_unit_id": "unit-a",
        "exclude_film_id": "film-a",
        "result_limit": 100,
        "_defer_result_preferences": True,
    }


def test_every_source_evidence_is_validated_before_empty_composition_scope(
    tmp_path: Path,
    config: Config,
) -> None:
    from pipeline.search.recipe import search_recipe

    image_path = tmp_path / "source.webp"
    Image.new("RGB", (32, 18), "red").save(image_path)
    clauses = [
        SearchClause(
            "composition",
            "source",
            "composition",
            source=SourceReference("visual-source", 0),
        ),
        SearchClause(
            "words",
            "source",
            "words",
            source=SourceReference("silent-source"),
        ),
    ]
    units = {
        "visual-source": {
            "unit_id": "visual-source",
            "film_id": "film-source",
        },
        "silent-source": {
            "unit_id": "silent-source",
            "film_id": "film-source",
            "dialogue": "[]",
            "on_screen_text": "",
        },
    }
    frame = {
        "frame_id": "visual-source::frame::0",
        "film_id": "film-source",
        "unit_id": "visual-source",
        "frame_index": 0,
        "timestamp": 1.0,
        "path": str(image_path),
        "visual_vec": [1.0, 0.0],
    }
    with (
        patch(
            "pipeline.search.recipe._resolve_unit",
            side_effect=lambda _db, unit_id: units[unit_id],
        ),
        patch("pipeline.search.recipe._resolve_frame", return_value=frame),
        patch(
            "pipeline.search.recipe.resolve_reference_result_scope",
            return_value=None,
        ) as resolve_scope,
    ):
        with pytest.raises(RecipeSourceUnavailable, match="no dialogue"):
            search_recipe(clauses, MagicMock(), config)

    resolve_scope.assert_not_called()


def test_composition_file_is_validated_before_empty_target_scope(
    tmp_path: Path,
    config: Config,
) -> None:
    from pipeline.search.recipe import search_recipe

    clause = SearchClause(
        "composition",
        "source",
        "composition",
        source=SourceReference("source", 0),
    )
    unit = {"unit_id": "source", "film_id": "film-source"}
    frame = {
        "frame_id": "source::frame::0",
        "film_id": "film-source",
        "unit_id": "source",
        "frame_index": 0,
        "timestamp": 1.0,
        "path": str(tmp_path / "missing.webp"),
        "visual_vec": [1.0, 0.0],
    }
    with (
        patch("pipeline.search.recipe._resolve_unit", return_value=unit),
        patch("pipeline.search.recipe._resolve_frame", return_value=frame),
        patch(
            "pipeline.search.recipe.resolve_reference_result_scope",
            return_value=None,
        ) as resolve_scope,
    ):
        with pytest.raises(RecipeSourceUnavailable, match="unavailable"):
            search_recipe([clause], MagicMock(), config)

    resolve_scope.assert_not_called()


def test_stale_composition_frame_precedes_empty_target_scope(
    config: Config,
) -> None:
    from pipeline.search.recipe import search_recipe

    clause = SearchClause(
        "composition",
        "source",
        "composition",
        source=SourceReference("source", 7),
    )
    with (
        patch(
            "pipeline.search.recipe._resolve_unit",
            return_value={"unit_id": "source", "film_id": "film-source"},
        ),
        patch(
            "pipeline.search.recipe._resolve_frame",
            side_effect=RecipeSourceNotFound("stale frame"),
        ),
        patch(
            "pipeline.search.recipe.resolve_reference_result_scope",
            return_value=None,
        ) as resolve_scope,
    ):
        with pytest.raises(RecipeSourceNotFound, match="stale frame"):
            search_recipe([clause], MagicMock(), config)

    resolve_scope.assert_not_called()


def test_semantic_view_operational_value_error_becomes_unavailable(
    config: Config,
) -> None:
    from pipeline.search.retrieve import search_semantic_views

    profile = TextIndexProfile(
        profile_id="qwen-v1",
        table_name="unit_text_qwen_v1",
        model_id="Qwen/test",
        model_revision="abc",
        dimension=3,
    )
    with (
        patch(
            "pipeline.search.retrieve._ready_text_profile",
            return_value=profile,
        ),
        patch(
            "pipeline.search.retrieve.embed_semantic_query",
            side_effect=ValueError("runtime embedding failure"),
        ),
    ):
        with pytest.raises(SemanticTextProfileUnavailable):
            search_semantic_views(
                "night",
                ("caption",),
                MagicMock(),
                config,
            )


@pytest.mark.parametrize(
    "payload",
    [
        {"clauses": []},
        {
            "clauses": [
                {"id": "one", "kind": "text", "facet": "scene", "text": "a"},
                {"id": "two", "kind": "text", "facet": "scene", "text": "b"},
            ]
        },
        {
            "clauses": [
                {
                    "id": "composition",
                    "kind": "source",
                    "facet": "composition",
                    "source": {"unit_id": "unit-a"},
                }
            ]
        },
        {
            "clauses": [
                {"id": "visual", "kind": "image", "facet": "visual"}
            ]
        },
        {
            "clauses": [
                {"id": str(index), "kind": "text", "facet": facet, "text": "x"}
                for index, facet in enumerate(("all", "scene", "words", "look"))
            ]
        },
    ],
)
def test_recipe_api_rejects_invalid_clause_shapes(
    payload: dict,
    config: Config,
) -> None:
    import pipeline.api.main as api_mod

    with (
        patch.object(api_mod, "load_config", return_value=config),
        patch.object(api_mod, "open_db", return_value=MagicMock()),
        patch.object(api_mod, "ensure_search_indexes"),
        patch.object(api_mod, "_execute_search_recipe") as recipe_search,
        TestClient(api_mod.app) as client,
    ):
        response = client.post("/search/recipe", json=payload)

    assert response.status_code == 422
    recipe_search.assert_not_called()


def test_recipe_api_reports_semantic_profile_failure_as_503(
    config: Config,
) -> None:
    import pipeline.api.main as api_mod

    with (
        patch.object(api_mod, "load_config", return_value=config),
        patch.object(api_mod, "open_db", return_value=MagicMock()),
        patch.object(api_mod, "ensure_search_indexes"),
        patch.object(
            api_mod,
            "_execute_search_recipe",
            side_effect=SemanticTextProfileUnavailable("profile unavailable"),
        ),
        TestClient(api_mod.app) as client,
    ):
        response = client.post(
            "/search/recipe",
            json={
                "clauses": [
                    {
                        "id": "scene",
                        "kind": "text",
                        "facet": "scene",
                        "text": "night",
                    }
                ]
            },
        )

    assert response.status_code == 503
    assert response.json()["detail"] == "profile unavailable"


@pytest.mark.parametrize(
    ("failure", "status_code"),
    [
        (RecipeSourceNotFound("stale source"), 404),
        (RecipeSourceUnavailable("source evidence unavailable"), 422),
    ],
)
def test_recipe_api_preserves_source_failure_status(
    failure: Exception,
    status_code: int,
    config: Config,
) -> None:
    import pipeline.api.main as api_mod

    with (
        patch.object(api_mod, "load_config", return_value=config),
        patch.object(api_mod, "open_db", return_value=MagicMock()),
        patch.object(api_mod, "ensure_search_indexes"),
        patch.object(api_mod, "_execute_search_recipe", side_effect=failure),
        TestClient(api_mod.app) as client,
    ):
        response = client.post(
            "/search/recipe",
            json={
                "clauses": [
                    {
                        "id": "source",
                        "kind": "source",
                        "facet": "scene",
                        "source": {"unit_id": "stale"},
                    }
                ]
            },
        )

    assert response.status_code == status_code
    assert response.json()["detail"] == str(failure)


def test_recipe_api_forwards_typed_clauses_and_returns_matches(
    config: Config,
) -> None:
    import pipeline.api.main as api_mod

    result = _result("match", frame_index=1)
    result["matches"] = [
        {"clause_id": "main", "facet": "all", "rank": 1}
    ]
    with (
        patch.object(api_mod, "load_config", return_value=config),
        patch.object(api_mod, "open_db", return_value=MagicMock()),
        patch.object(api_mod, "ensure_search_indexes"),
        patch.object(
            api_mod,
            "_with_film_titles",
            side_effect=lambda _request, rows: rows,
        ),
        patch.object(
            api_mod,
            "_execute_search_recipe",
            return_value=SearchRecipeExecution(
                results=[result],
                source_evidence=[
                    {
                        "clause_id": "composition",
                        "facet": "composition",
                        "source": {
                            "unit_id": "unit-a",
                            "frame_index": 1,
                        },
                        "adapter": "pe_global+spatial_6x6",
                        "evidence": [
                            {
                                "type": "frame",
                                "frame_index": 1,
                                "mode": "spatial_visual",
                            }
                        ],
                    }
                ],
            ),
        ) as recipe_search,
        TestClient(api_mod.app) as client,
    ):
        response = client.post(
            "/search/recipe",
            json={
                "clauses": [
                    {
                        "id": "main",
                        "kind": "text",
                        "facet": "all",
                        "text": "  lonely night  ",
                    },
                    {
                        "id": "composition",
                        "kind": "source",
                        "facet": "composition",
                        "source": {"unit_id": "unit-a", "frame_index": 1},
                    },
                ],
                "film_ids": ["film-a", "film-a", "film-b"],
            },
        )
        available_slots = client.app.state.image_search_slots.qsize()

    assert response.status_code == 200
    assert response.json()["results"][0]["keyframe_index"] == 1
    assert response.json()["results"][0]["matches"][0]["facet"] == "all"
    assert response.json()["source_evidence"][0]["adapter"] == (
        "pe_global+spatial_6x6"
    )
    clauses = recipe_search.call_args.args[0]
    assert clauses[0].text == "lonely night"
    assert clauses[1].source == SourceReference("unit-a", 1)
    assert recipe_search.call_args.kwargs == {"film_ids": ["film-a", "film-b"]}
    assert available_slots == 2


def test_uploaded_visual_recipe_api_decodes_and_forwards_one_image_clause(
    config: Config,
) -> None:
    import pipeline.api.main as api_mod

    source_evidence = [
        {
            "clause_id": "visual",
            "facet": "visual",
            "source": {"kind": "uploaded_image"},
            "adapter": "pe_global+spatial_6x6",
            "evidence": [
                {"type": "image", "mode": "global_spatial_visual"}
            ],
        }
    ]
    with (
        patch.object(api_mod, "load_config", return_value=config),
        patch.object(api_mod, "open_db", return_value=MagicMock()),
        patch.object(api_mod, "ensure_search_indexes"),
        patch.object(
            api_mod,
            "_with_film_titles",
            side_effect=lambda _request, rows: rows,
        ),
        patch.object(
            api_mod,
            "_execute_search_recipe",
            return_value=SearchRecipeExecution(
                results=[_result("match")],
                source_evidence=source_evidence,
            ),
        ) as recipe_search,
        TestClient(api_mod.app) as client,
    ):
        response = client.post(
            "/search/recipe/image",
            data={
                "recipe": json.dumps(
                    {
                        "clauses": [
                            {
                                "id": "main",
                                "kind": "text",
                                "facet": "all",
                                "text": "  rainy night  ",
                            },
                            {
                                "id": "visual",
                                "kind": "image",
                                "facet": "visual",
                            },
                        ],
                        "film_ids": ["film-a", "film-a", "film-b"],
                    }
                )
            },
            files={"image": ("frame.png", _png_bytes(), "image/png")},
        )
        available_slots = client.app.state.image_search_slots.qsize()

    assert response.status_code == 200
    assert response.json()["source_evidence"] == source_evidence
    clauses = recipe_search.call_args.args[0]
    assert clauses[0].text == "rainy night"
    assert clauses[1].kind == "image"
    assert clauses[1].facet == "visual"
    assert isinstance(clauses[1].image, Image.Image)
    assert clauses[1].image.mode == "RGB"
    assert recipe_search.call_args.kwargs == {
        "film_ids": ["film-a", "film-b"]
    }
    assert available_slots == 2


@pytest.mark.parametrize(
    "clauses",
    [
        [{"id": "main", "kind": "text", "facet": "all", "text": "x"}],
        [
            {"id": "visual-a", "kind": "image", "facet": "visual"},
            {
                "id": "visual-b",
                "kind": "image",
                "facet": "visual",
            },
        ],
        [{"id": "look", "kind": "image", "facet": "look"}],
        [
            {
                "id": "framing",
                "kind": "image",
                "facet": "composition",
            }
        ],
        [{"id": "scene", "kind": "image", "facet": "scene"}],
        [{"id": "visual", "kind": "text", "facet": "visual", "text": "x"}],
    ],
)
def test_uploaded_image_recipe_api_requires_one_supported_image_clause(
    clauses: list[dict],
    config: Config,
) -> None:
    import pipeline.api.main as api_mod

    with (
        patch.object(api_mod, "load_config", return_value=config),
        patch.object(api_mod, "open_db", return_value=MagicMock()),
        patch.object(api_mod, "ensure_search_indexes"),
        patch.object(api_mod, "_execute_search_recipe") as recipe_search,
        TestClient(api_mod.app) as client,
    ):
        response = client.post(
            "/search/recipe/image",
            data={"recipe": json.dumps({"clauses": clauses})},
            files={"image": ("frame.png", _png_bytes(), "image/png")},
        )

    assert response.status_code == 422
    recipe_search.assert_not_called()


def test_uploaded_image_recipe_api_reuses_reference_media_validation(
    config: Config,
) -> None:
    import pipeline.api.main as api_mod

    recipe = json.dumps(
        {
            "clauses": [
                {"id": "visual", "kind": "image", "facet": "visual"}
            ]
        }
    )
    with (
        patch.object(api_mod, "load_config", return_value=config),
        patch.object(api_mod, "open_db", return_value=MagicMock()),
        patch.object(api_mod, "ensure_search_indexes"),
        patch.object(api_mod, "_execute_search_recipe") as recipe_search,
        TestClient(api_mod.app) as client,
    ):
        response = client.post(
            "/search/recipe/image",
            data={"recipe": recipe},
            files={"image": ("frame.txt", b"not an image", "text/plain")},
        )

    assert response.status_code == 415
    assert response.json()["detail"] == (
        "Use a JPEG, PNG, or WebP reference image"
    )
    recipe_search.assert_not_called()
