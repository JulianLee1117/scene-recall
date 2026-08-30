"""Tests for pipeline/search/retrieve.py and pipeline/api/main.py — TDD.

Tests:
  - search: returns list of dicts with required keys
  - search: keyframe_url and preview_url are correctly formatted
  - search: calls embed_text with the query
  - API GET /search?q=...: returns {"results": [...]}
  - API GET /unit/{unit_id}: returns unit row dict
  - API GET /media/keyframe/{shot_id}/{n}: FileResponse when file exists
  - API GET /media/keyframe/{shot_id}/{n}: 404 when file missing
  - API GET /media/preview/{shot_id}: FileResponse when file exists
  - API GET /media/preview/{shot_id}: 404 when file missing

All LanceDB and embed_text calls are mocked — no real DB or model in CI.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from PIL import Image

from pipeline.config import Config


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VEC_DIM = 1024
MEDIA_FILM_ID = "a" * 64
OTHER_FILM_ID = "b" * 64
MEDIA_SHOT_ID = f"{MEDIA_FILM_ID}_0001"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_vec() -> np.ndarray:
    """Return a random L2-normalised float32 row vector, shape (1, VEC_DIM)."""
    v = np.random.randn(VEC_DIM).astype(np.float32)
    return (v / np.linalg.norm(v)).reshape(1, -1)


def _make_unit_row(
    shot_id: str = "film_abc_0001",
    film_id: str = "film_abc",
    **overrides: object,
) -> dict:
    row = {
        "unit_id": shot_id,
        "film_id": film_id,
        "shot_id": shot_id,
        "t_start": 10.0,
        "t_end": 15.5,
        "is_representative": True,
        "caption": "A rainy night scene",
        "searchable_text": "rainy night alone",
        "dialogue": "[]",
        "keyframe_paths": json.dumps([f"/assets/keyframes/{shot_id}_0.webp"]),
        "mood": '["dark", "melancholic"]',
        "img_vec": [0.0] * VEC_DIM,
        "txt_vec": [0.0] * VEC_DIM,
        "_distance": 0.1,
    }
    row.update(overrides)
    return row


def _basis_vec(index: int) -> list[float]:
    """Return a deterministic unit vector for diversity tests."""
    vector = [0.0] * VEC_DIM
    vector[index] = 1.0
    return vector


def _cosine_vec(
    similarity: float,
    anchor_index: int = 0,
    orthogonal_index: int = 1,
) -> list[float]:
    """Return a unit vector with the requested cosine to an anchor basis."""
    vector = [0.0] * VEC_DIM
    vector[anchor_index] = similarity
    vector[orthogonal_index] = float(np.sqrt(1.0 - similarity**2))
    return vector


def _make_query_chain(rows: list[dict]) -> MagicMock:
    chain = MagicMock()
    chain.metric.return_value = chain
    chain.select.return_value = chain
    chain.where.return_value = chain
    chain.limit.return_value = chain
    chain.to_list.return_value = rows
    return chain


def test_any_of_balances_large_candidate_filters(monkeypatch: pytest.MonkeyPatch) -> None:
    """Large ID filters stay shallow enough for LanceDB's native stack."""
    import pipeline.search.retrieve as retrieve

    class FakeExpression:
        def __init__(self, values: frozenset[str], depth: int = 1) -> None:
            self.values = values
            self.depth = depth

        def __eq__(self, value: object) -> "FakeExpression":  # type: ignore[override]
            return FakeExpression(frozenset({str(value)}))

        def __or__(self, other: "FakeExpression") -> "FakeExpression":
            return FakeExpression(
                self.values | other.values,
                max(self.depth, other.depth) + 1,
            )

    monkeypatch.setattr(
        retrieve,
        "col",
        lambda column: FakeExpression(frozenset({column})),
    )
    monkeypatch.setattr(retrieve, "lit", lambda value: value)

    values = tuple(f"unit-{index}" for index in range(600))
    expression = retrieve._any_of("unit_id", values)

    assert expression is not None
    assert expression.values == frozenset(values)
    assert expression.depth <= 11


def _make_hybrid_mock_db(
    *,
    image_rows: list[dict],
    text_rows: list[dict],
    lexical_rows: list[dict],
) -> MagicMock:
    """Mock independent image, text, and unvectorised table searches."""
    table = MagicMock()

    def fake_search(
        _query: object = None,
        *,
        vector_column_name: str | None = None,
        **_kwargs: object,
    ) -> MagicMock:
        if vector_column_name == "img_vec":
            return _make_query_chain(image_rows)
        if vector_column_name == "txt_vec":
            return _make_query_chain(text_rows)
        assert vector_column_name is None
        return _make_query_chain(lexical_rows)

    table.search.side_effect = fake_search
    db = MagicMock()
    db.open_table.return_value = table
    return db


def _make_frame_hybrid_mock_db(
    *,
    frame_rows: list[dict],
    fallback_image_rows: list[dict],
    text_rows: list[dict],
    lexical_rows: list[dict],
    frame_unit_rows: list[dict] | None = None,
) -> tuple[MagicMock, MagicMock, MagicMock]:
    """Mock units plus an optional frame-level visual index."""
    units = MagicMock()
    scalar_query_chains: list[MagicMock] = []

    def fake_unit_search(
        _query: object = None,
        *,
        vector_column_name: str | None = None,
        **_kwargs: object,
    ) -> MagicMock:
        if vector_column_name == "img_vec":
            return _make_query_chain(fallback_image_rows)
        if vector_column_name == "txt_vec":
            return _make_query_chain(text_rows)
        assert vector_column_name is None
        rows = (
            lexical_rows
            if not scalar_query_chains or frame_unit_rows is None
            else frame_unit_rows
        )
        chain = _make_query_chain(rows)
        scalar_query_chains.append(chain)
        return chain

    units.search.side_effect = fake_unit_search
    units._scalar_query_chains = scalar_query_chains
    frames = MagicMock()
    frame_query_chain = _make_query_chain(frame_rows)
    _mark_frames_as_current_profile(frames, len(frame_rows))

    def fake_frame_search(
        _query: object = None,
        *,
        vector_column_name: str | None = None,
        **_kwargs: object,
    ) -> MagicMock:
        assert vector_column_name == "visual_vec"
        return frame_query_chain

    frames.search.side_effect = fake_frame_search
    frames._query_chain = frame_query_chain
    db = MagicMock()
    db.list_tables.return_value.tables = ["frames", "units"]
    db.open_table.side_effect = lambda name: {
        "frames": frames,
        "units": units,
    }[name]
    return db, units, frames


def _mark_frames_as_current_profile(frames: MagicMock, row_count: int) -> None:
    """Give a frame-table mock the lineage facts checked at query time."""
    frames.schema.names = ["visual_encoder"]
    frames.count_rows.side_effect = lambda _filter=None: row_count


def _make_search_mock_db(rows: list[dict]) -> MagicMock:
    """Mock DB for vector-search chain:
    open_table("units").search(vec, ...).metric(...).limit(...).where(...).to_list()
    """
    chain = MagicMock()
    chain.metric.return_value = chain
    chain.limit.return_value = chain
    chain.where.return_value = chain
    chain.to_list.return_value = rows

    tbl = MagicMock()
    tbl.search.return_value = chain

    db = MagicMock()
    db.open_table.return_value = tbl
    return db


def _make_filter_mock_db(rows: list[dict]) -> MagicMock:
    """Mock DB for scalar-filter chain:
    open_table("units").search().where(...).limit(...).to_list()
    """
    chain = MagicMock()
    chain.where.return_value = chain
    chain.limit.return_value = chain
    chain.to_list.return_value = rows

    tbl = MagicMock()
    tbl.search.return_value = chain

    db = MagicMock()
    db.open_table.return_value = tbl
    return db


# ---------------------------------------------------------------------------
# search()
# ---------------------------------------------------------------------------


def test_search_returns_nonempty_list(config: Config) -> None:
    """search('rainy night alone', db, config) returns a non-empty list."""
    from pipeline.search.retrieve import search

    rows = [_make_unit_row()]
    mock_db = _make_search_mock_db(rows)

    with patch("pipeline.search.retrieve.embed_text", return_value=_fake_vec()):
        results = search("rainy night alone", mock_db, config)

    assert isinstance(results, list)
    assert len(results) > 0


def test_search_result_has_required_keys(config: Config) -> None:
    """Each search result dict contains all required keys."""
    from pipeline.search.retrieve import search

    rows = [_make_unit_row()]
    mock_db = _make_search_mock_db(rows)

    with patch("pipeline.search.retrieve.embed_text", return_value=_fake_vec()):
        results = search("rainy night alone", mock_db, config)

    result = results[0]
    required = ("unit_id", "film_id", "t_start", "t_end", "caption", "keyframe_url", "preview_url")
    for key in required:
        assert key in result, f"Missing key: {key}"


def test_search_keyframe_url_format(config: Config) -> None:
    """keyframe_url is /media/keyframe/{shot_id}/0."""
    from pipeline.search.retrieve import search

    shot_id = "film_abc_0001"
    rows = [_make_unit_row(shot_id=shot_id)]
    mock_db = _make_search_mock_db(rows)

    with patch("pipeline.search.retrieve.embed_text", return_value=_fake_vec()):
        results = search("rain", mock_db, config)

    assert results[0]["keyframe_url"] == f"/media/keyframe/{shot_id}/0"


def test_search_preview_url_format(config: Config) -> None:
    """preview_url is /media/preview/{shot_id}."""
    from pipeline.search.retrieve import search

    shot_id = "film_abc_0001"
    rows = [_make_unit_row(shot_id=shot_id)]
    mock_db = _make_search_mock_db(rows)

    with patch("pipeline.search.retrieve.embed_text", return_value=_fake_vec()):
        results = search("rain", mock_db, config)

    assert results[0]["preview_url"] == f"/media/preview/{shot_id}"


def test_search_scopes_every_channel_to_selected_films(config: Config) -> None:
    """A selected film scope excludes candidates from every other film."""
    from pipeline.search.retrieve import search

    selected = _make_unit_row(
        shot_id="film_one_0001",
        film_id="film_one",
        caption="A woman walking through rain",
    )
    excluded = _make_unit_row(
        shot_id="film_two_0001",
        film_id="film_two",
        caption="A woman walking through rain",
    )
    db = _make_hybrid_mock_db(
        image_rows=[excluded, selected],
        text_rows=[excluded, selected],
        lexical_rows=[excluded, selected],
    )

    with patch("pipeline.search.retrieve.embed_text", return_value=_fake_vec()):
        results = search(
            "woman walking through rain",
            db,
            config,
            film_ids=["film_one"],
        )

    assert [result["film_id"] for result in results] == ["film_one"]


def test_search_calls_embed_text_with_query(config: Config) -> None:
    """search() calls embed_text([query], config) exactly once."""
    from pipeline.search.retrieve import search

    mock_db = _make_search_mock_db([])
    fake_vec = np.zeros((1, VEC_DIM), dtype=np.float32)

    with patch("pipeline.search.retrieve.embed_text", return_value=fake_vec) as mock_embed:
        search("rainy night alone", mock_db, config)

    mock_embed.assert_called_once_with(["rainy night alone"], config)


@pytest.mark.parametrize(
    ("weights", "expected_vector_column", "expects_embedding"),
    [
        ((1.0, 0.0, 0.0), "img_vec", True),
        ((0.0, 1.0, 0.0), "txt_vec", True),
        ((0.0, 0.0, 1.0), None, False),
    ],
    ids=("image-only", "text-only", "lexical-only"),
)
def test_zero_weight_channels_do_no_retrieval_work(
    config: Config,
    weights: tuple[float, float, float],
    expected_vector_column: str | None,
    expects_embedding: bool,
) -> None:
    """A channel ablation executes only the retrieval work it measures."""
    from pipeline.search.retrieve import search

    config.retrieval.weights.img = weights[0]
    config.retrieval.weights.txt = weights[1]
    config.retrieval.weights.lex = weights[2]
    row = _make_unit_row(searchable_text="rainy night alone")
    db = _make_hybrid_mock_db(
        image_rows=[row],
        text_rows=[row],
        lexical_rows=[row],
    )
    table = db.open_table.return_value

    with patch(
        "pipeline.search.retrieve.embed_text",
        return_value=_fake_vec(),
    ) as embed:
        results = search("rainy night", db, config)

    assert [result["unit_id"] for result in results] == [row["unit_id"]]
    if expects_embedding:
        embed.assert_called_once_with(["rainy night"], config)
    else:
        embed.assert_not_called()

    vector_columns = [
        call.kwargs.get("vector_column_name")
        for call in table.search.call_args_list
        if call.kwargs.get("vector_column_name") is not None
    ]
    if expected_vector_column is None:
        assert vector_columns == []
    else:
        assert vector_columns == [expected_vector_column]


def test_ready_semantic_profile_uses_qwen_and_collapses_views_per_unit(
    config: Config,
) -> None:
    """Independent text views produce one RRF vote and expose match evidence."""
    from pipeline.index.text_features import TextIndexProfile
    from pipeline.search.retrieve import search

    config.retrieval.weights.img = 0.0
    config.retrieval.weights.txt = 1.0
    config.retrieval.weights.lex = 0.0
    profile = TextIndexProfile(
        profile_id="qwen-v1",
        table_name="unit_text_qwen_v1",
        model_id="Qwen/test",
        model_revision="abc123",
        dimension=VEC_DIM,
    )
    first = _make_unit_row(shot_id="film_abc_0001")
    second = _make_unit_row(shot_id="film_abc_0002")
    feature_rows = [
        {
            "feature_id": "film_abc_0001::text::dialogue",
            "profile_id": profile.profile_id,
            "film_id": "film_abc",
            "unit_id": "film_abc_0001",
            "view": "dialogue",
            "text": "Meet me after midnight.",
            "_distance": 0.05,
        },
        {
            "feature_id": "film_abc_0001::text::caption",
            "profile_id": profile.profile_id,
            "film_id": "film_abc",
            "unit_id": "film_abc_0001",
            "view": "caption",
            "text": "A woman waits outside.",
            "_distance": 0.08,
        },
        {
            "feature_id": "film_abc_0002::text::caption",
            "profile_id": profile.profile_id,
            "film_id": "film_abc",
            "unit_id": "film_abc_0002",
            "view": "caption",
            "text": "A clock reads midnight.",
            "_distance": 0.10,
        },
    ]

    features = MagicMock()
    features.search.return_value = _make_query_chain(feature_rows)
    units = MagicMock()
    units.search.return_value = _make_query_chain([first, second])
    db = MagicMock()
    db.open_table.side_effect = lambda name: {
        "units": units,
        profile.table_name: features,
    }[name]

    with (
        patch(
            "pipeline.search.retrieve._ready_text_profile",
            return_value=profile,
        ),
        patch(
            "pipeline.search.retrieve.embed_semantic_query",
            return_value=_fake_vec()[0],
        ) as semantic_embed,
        patch("pipeline.search.retrieve.embed_text") as pe_embed,
    ):
        results = search("meet me after midnight", db, config)

    semantic_embed.assert_called_once_with("meet me after midnight", config)
    pe_embed.assert_not_called()
    assert [result["unit_id"] for result in results] == [
        "film_abc_0001",
        "film_abc_0002",
    ]
    assert results[0]["matched_text_view"] == "dialogue"
    assert results[0]["matched_text"] == "Meet me after midnight."
    assert results[0]["debug"]["channels"]["txt"]["source"] == "dialogue"
    assert features.search.call_count == 1
    features.search.return_value.select.assert_called_once_with(
        [
            "feature_id",
            "profile_id",
            "film_id",
            "unit_id",
            "view",
            "text",
            "_distance",
        ]
    )


def test_ready_semantic_profile_failure_falls_back_as_one_legacy_channel(
    config: Config,
) -> None:
    from pipeline.index.text_features import TextIndexProfile
    from pipeline.search.retrieve import search

    config.retrieval.weights.img = 0.0
    config.retrieval.weights.txt = 1.0
    config.retrieval.weights.lex = 0.0
    profile = TextIndexProfile(
        profile_id="qwen-v1",
        table_name="unit_text_qwen_v1",
        model_id="Qwen/test",
        model_revision="abc123",
        dimension=VEC_DIM,
    )
    row = _make_unit_row()
    db = _make_hybrid_mock_db(
        image_rows=[],
        text_rows=[row],
        lexical_rows=[],
    )

    with (
        patch(
            "pipeline.search.retrieve._ready_text_profile",
            return_value=profile,
        ),
        patch(
            "pipeline.search.retrieve.embed_semantic_query",
            side_effect=RuntimeError("weights unavailable"),
        ),
        patch(
            "pipeline.search.retrieve.embed_text",
            return_value=_fake_vec(),
        ) as legacy_embed,
    ):
        results = search("rainy night", db, config)

    legacy_embed.assert_called_once_with(["rainy night"], config)
    assert [result["unit_id"] for result in results] == [row["unit_id"]]
    assert results[0]["debug"]["channels"]["txt"]["source"] == (
        "legacy_combined_text"
    )


def test_search_rejects_no_enabled_channels(config: Config) -> None:
    """An invalid all-zero experiment cannot masquerade as empty retrieval."""
    from pipeline.search.retrieve import search

    config.retrieval.weights.img = 0.0
    config.retrieval.weights.txt = 0.0
    config.retrieval.weights.lex = 0.0

    with pytest.raises(ValueError, match="at least one retrieval channel"):
        search("rainy night", MagicMock(), config)


def test_lexical_query_terms_are_bounded_for_pasted_prose() -> None:
    """Compound native FTS work cannot grow without bound with query length."""
    from pipeline.search.retrieve import (
        _MAX_LEXICAL_QUERY_TERMS,
        _unique_query_terms,
    )

    terms = _unique_query_terms(" ".join(f"term{index}" for index in range(100)))

    assert len(terms) == _MAX_LEXICAL_QUERY_TERMS
    assert terms == [f"term{index}" for index in range(_MAX_LEXICAL_QUERY_TERMS)]


def test_search_empty_db_returns_empty_list(config: Config) -> None:
    """search() returns an empty list when the DB returns no rows."""
    from pipeline.search.retrieve import search

    mock_db = _make_search_mock_db([])

    with patch("pipeline.search.retrieve.embed_text", return_value=_fake_vec()):
        results = search("nothing matches", mock_db, config)

    assert results == []


def test_search_fuses_channel_ranks_with_config_weights(config: Config) -> None:
    """A candidate ranked in two channels beats raw-distance channel winners."""
    from pipeline.search.retrieve import search

    image_only = _make_unit_row(
        "image_only",
        "film_image",
        caption="A person outdoors",
        searchable_text="A person outdoors",
        t_start=0.0,
        t_end=2.0,
        img_vec=_basis_vec(0),
        _distance=0.01,
    )
    text_only = _make_unit_row(
        "text_only",
        "film_text",
        caption="A person indoors",
        searchable_text="A person indoors",
        t_start=50.0,
        t_end=52.0,
        img_vec=_basis_vec(1),
        _distance=0.02,
    )
    shared = _make_unit_row(
        "shared",
        "film_shared",
        caption="A night portrait",
        searchable_text="A night portrait",
        t_start=100.0,
        t_end=102.0,
        img_vec=_basis_vec(2),
        _distance=0.40,
    )
    lexical_only = _make_unit_row(
        "lexical_only",
        "film_lexical",
        caption="A cigarette in an ashtray",
        searchable_text="cigarette smoke ashtray",
        t_start=150.0,
        t_end=152.0,
        img_vec=_basis_vec(3),
        _distance=0.90,
    )
    db = _make_hybrid_mock_db(
        image_rows=[image_only, shared],
        text_rows=[text_only, shared],
        lexical_rows=[image_only, text_only, shared, lexical_only],
    )

    with patch("pipeline.search.retrieve.embed_text", return_value=_fake_vec()) as embed:
        results = search("cigarette", db, config)

    assert results[0]["unit_id"] == "shared"
    assert results[0]["debug"]["final_score"] == pytest.approx(
        config.retrieval.weights.img / 62
        + config.retrieval.weights.txt / 62
    )
    assert results[0]["debug"]["channels"]["img"]["rank"] == 2
    assert results[0]["debug"]["channels"]["txt"]["rank"] == 2
    embed.assert_called_once_with(["cigarette"], config)


def test_broad_single_term_does_not_get_an_incidental_lexical_vote(
    config: Config,
) -> None:
    """An unquoted concept relies on visual and semantic evidence, not FTS."""
    from pipeline.search.retrieve import search

    conceptual = _make_unit_row(
        "conceptual",
        "film_visual",
        caption="Soft golden light falls across a peaceful face",
        searchable_text="soft golden light peaceful face",
        img_vec=_basis_vec(0),
    )
    incidental_words = _make_unit_row(
        "incidental_words",
        "film_subtitle",
        caption="Two people argue in a dark corridor",
        searchable_text="two people argue dark corridor",
        dialogue='["It was beautiful and horrifying."]',
        img_vec=_basis_vec(1),
    )
    db = _make_hybrid_mock_db(
        image_rows=[conceptual],
        text_rows=[conceptual],
        lexical_rows=[incidental_words],
    )

    with patch("pipeline.search.retrieve.embed_text", return_value=_fake_vec()):
        results = search("beautiful", db, config)

    assert [result["unit_id"] for result in results] == ["conceptual"]
    assert set(results[0]["debug"]["channels"]) == {"img", "txt"}


def test_quoted_single_term_retains_explicit_lexical_retrieval(
    config: Config,
) -> None:
    """Quotes express word intent and keep native FTS as an independent vote."""
    from pipeline.search.retrieve import search

    lexical = _make_unit_row(
        "lexical",
        "film_subtitle",
        caption="Two people argue in a dark corridor",
        searchable_text="two people argue dark corridor beautiful",
        dialogue='["It was beautiful and horrifying."]',
        img_vec=_basis_vec(1),
    )
    db = _make_hybrid_mock_db(
        image_rows=[],
        text_rows=[],
        lexical_rows=[lexical],
    )

    with patch("pipeline.search.retrieve.embed_text", return_value=_fake_vec()):
        results = search('"beautiful"', db, config)

    assert [result["unit_id"] for result in results] == ["lexical"]
    assert set(results[0]["debug"]["channels"]) == {"lex"}


def test_single_term_lexical_only_ablation_still_retrieves(config: Config) -> None:
    """The broad-query policy must not turn a lexical-only eval into no-op."""
    from pipeline.search.retrieve import search

    config.retrieval.weights.img = 0.0
    config.retrieval.weights.txt = 0.0
    config.retrieval.weights.lex = 1.0
    lexical = _make_unit_row(
        "lexical",
        caption="A beautiful landscape",
        searchable_text="beautiful landscape",
    )
    db = _make_hybrid_mock_db(
        image_rows=[],
        text_rows=[],
        lexical_rows=[lexical],
    )

    results = search("beautiful", db, config)

    assert [result["unit_id"] for result in results] == ["lexical"]
    assert set(results[0]["debug"]["channels"]) == {"lex"}


def test_search_filters_junk_unless_query_explicitly_requests_it(
    config: Config,
) -> None:
    """Credits are suppressed normally but available for a credits query."""
    from pipeline.search.retrieve import search

    credits = _make_unit_row(
        "credits",
        "film_credits",
        caption="Rolling end credits over a black screen",
        searchable_text="closing credits cast and crew",
        img_vec=_basis_vec(0),
        _distance=0.01,
    )
    scene = _make_unit_row(
        "scene",
        "film_scene",
        caption="A woman smokes by a window",
        searchable_text="woman smoking cigarette at night",
        img_vec=_basis_vec(1),
        _distance=0.10,
    )
    db = _make_hybrid_mock_db(
        image_rows=[credits, scene],
        text_rows=[credits, scene],
        lexical_rows=[credits, scene],
    )

    with patch("pipeline.search.retrieve.embed_text", return_value=_fake_vec()):
        ordinary = search("woman", db, config)
        explicit = search("end credits", db, config)

    assert [result["unit_id"] for result in ordinary] == ["scene"]
    assert "credits" in [result["unit_id"] for result in explicit]


def test_lexical_ranking_ignores_glue_words_and_requires_compound_evidence() -> None:
    """Incidental “up”/“in” matches cannot influence compound-query fusion."""
    from pipeline.search.retrieve import _lexical_ranking

    close_up = _make_unit_row(
        "close_up",
        caption="A tight close-up portrait of a woman",
        searchable_text="tight close-up portrait woman",
    )
    walks_up = _make_unit_row(
        "walks_up",
        caption="A man walks up the stairs in a wide shot",
        searchable_text="man walks up stairs wide shot",
    )
    woman_only = _make_unit_row(
        "woman_only",
        caption="A woman waits in a restaurant",
        searchable_text="woman waits restaurant",
    )
    car_only = _make_unit_row(
        "car_only",
        caption="A car crosses a tunnel",
        searchable_text="car crosses tunnel",
    )
    woman_in_car = _make_unit_row(
        "woman_in_car",
        caption="A woman drives a car at night",
        searchable_text="woman drives car night",
    )
    rows = [close_up, walks_up, woman_only, car_only, woman_in_car]

    close_results = _lexical_ranking("close up", rows)
    compound_results = _lexical_ranking("woman in a car", rows)

    assert [row["unit_id"] for row, _score in close_results] == ["close_up"]
    assert [row["unit_id"] for row, _score in compound_results] == [
        "woman_in_car"
    ]


def test_junk_override_rejects_incidental_and_negated_credit_queries() -> None:
    """“Credit card” and “without credits” do not re-enable a credit roll."""
    from pipeline.search.retrieve import _is_unrequested_junk

    credits = _make_unit_row(
        "credits",
        caption="Rolling end credits over a black screen",
        searchable_text="closing credits cast and crew",
    )

    assert _is_unrequested_junk(credits, "credit card")
    assert _is_unrequested_junk(credits, "black screen without credits")
    assert not _is_unrequested_junk(credits, "end credits")


def test_hyphenated_end_credit_captions_are_filtered() -> None:
    """The annotator writes "end-credit cards", which must count as credits."""
    from pipeline.search.retrieve import _is_unrequested_junk

    for caption in (
        "Minimalist end-credit cards display centered white serif text",
        "Static black end-credit frame with centered white acknowledgments",
        "Black screen with white credit cards listing the production crew",
    ):
        row = _make_unit_row(
            "credits_row",
            caption=caption,
            searchable_text=caption,
        )
        assert _is_unrequested_junk(row, "medium shot"), caption


def test_search_collapses_same_subject_repeats_far_apart(
    config: Config,
) -> None:
    """0.92+ visual repeats collapse at any distance; 0.91 callbacks survive."""
    from pipeline.search.retrieve import search

    hero = _make_unit_row(
        "hero",
        "film_one",
        caption="Extreme close-up of a pale elf queen",
        searchable_text="elf queen close-up",
        t_start=10.0,
        t_end=12.0,
        img_vec=_basis_vec(0),
        _distance=0.01,
    )
    repeat = _make_unit_row(
        "repeat",
        "film_one",
        caption="Close-up of the same pale elf queen minutes later",
        searchable_text="elf queen close-up again",
        t_start=600.0,
        t_end=602.0,
        img_vec=_cosine_vec(0.93),
        _distance=0.02,
    )
    callback = _make_unit_row(
        "callback",
        "film_one",
        caption="The elf queen seen again in a different light",
        searchable_text="elf queen later",
        t_start=900.0,
        t_end=902.0,
        img_vec=_cosine_vec(0.91, orthogonal_index=2),
        _distance=0.03,
    )
    db = _make_hybrid_mock_db(
        image_rows=[hero, repeat, callback],
        text_rows=[hero, repeat, callback],
        lexical_rows=[],
    )

    with patch("pipeline.search.retrieve.embed_text", return_value=_fake_vec()):
        results = search("elf queen", db, config)

    assert [result["unit_id"] for result in results] == ["hero", "callback"]


def test_search_soft_film_diversity_backfills_by_relevance(config: Config) -> None:
    """Film variety reorders a page but never suppresses available results."""
    from pipeline.search.retrieve import search

    config.retrieval.diversity.page_size = 4
    config.retrieval.diversity.film_results_per_page_target = 2
    same_film = [
        _make_unit_row(
            f"crowded_{index}",
            "film_crowded",
            caption=f"Distinct moment {index}",
            searchable_text=f"moment {index}",
            t_start=float(index * 100),
            t_end=float(index * 100 + 2),
            img_vec=_basis_vec(index),
            _distance=0.01 + index * 0.01,
        )
        for index in range(5)
    ]
    other = _make_unit_row(
        "other_film",
        "film_other",
        caption="A moment from another film",
        searchable_text="another film moment",
        t_start=50.0,
        t_end=52.0,
        img_vec=_basis_vec(9),
        _distance=0.30,
    )
    rows = [*same_film, other]
    db = _make_hybrid_mock_db(
        image_rows=rows,
        text_rows=rows,
        lexical_rows=[],
    )

    with patch("pipeline.search.retrieve.embed_text", return_value=_fake_vec()):
        results = search("moment", db, config, result_limit=6)

    assert [result["unit_id"] for result in results] == [
        "crowded_0",
        "crowded_1",
        "other_film",
        "crowded_2",
        "crowded_3",
        "crowded_4",
    ]
    assert len(results) == 6


def test_unscoped_broad_search_exposes_deeper_cross_film_candidates(
    config: Config,
) -> None:
    """Film diversity can see an agreed hit below the old per-channel depth."""
    from pipeline.search.retrieve import search

    config.retrieval.candidate_limit = 200
    config.retrieval.diversity.page_size = 12
    config.retrieval.diversity.film_results_per_page_target = 4
    crowded = [
        _make_unit_row(
            f"crowded_{index:03d}",
            "film_crowded",
            caption=f"Quiet moment {index}",
            searchable_text=f"quiet moment {index}",
            t_start=float(index * 100),
            t_end=float(index * 100 + 2),
            img_vec=[],
            _distance=0.001 + index * 0.001,
        )
        for index in range(205)
    ]
    deeper_other_film = _make_unit_row(
        "deeper_other_film",
        "film_other",
        caption="A quiet moment in another film",
        searchable_text="quiet moment another film",
        t_start=30_000.0,
        t_end=30_002.0,
        img_vec=[],
        _distance=0.9,
    )
    rows = [*crowded, deeper_other_film]
    db = _make_hybrid_mock_db(
        image_rows=rows,
        text_rows=rows,
        lexical_rows=[],
    )

    with patch("pipeline.search.retrieve.embed_text", return_value=_fake_vec()):
        results = search("quiet moment", db, config, result_limit=24)

    unit_ids = [result["unit_id"] for result in results]
    assert unit_ids[:4] == [f"crowded_{index:03d}" for index in range(4)]
    assert unit_ids[4] == "deeper_other_film"
    assert unit_ids[5:] == [
        f"crowded_{index:03d}" for index in range(4, 23)
    ]


def test_cross_film_reserve_is_bounded_and_keeps_one_best_missing_film() -> None:
    from pipeline.search.retrieve import _cross_film_candidate_reserve

    def candidate(unit_id: str, film_id: str) -> dict:
        return {"row": {"unit_id": unit_id, "film_id": film_id}}

    candidates = [
        candidate("a1", "film_a"),
        candidate("a2", "film_a"),
        candidate("b1", "film_b"),
        candidate("a3", "film_a"),
        candidate("c1", "film_c"),
        candidate("c2", "film_c"),
        candidate("d1", "film_d"),
        candidate("e1", "film_e"),
    ]

    reserved = _cross_film_candidate_reserve(
        candidates,
        candidate_limit=3,
        reserve_limit=2,
    )

    assert [item["row"]["unit_id"] for item in reserved] == [
        "a1",
        "a2",
        "b1",
        "c1",
        "d1",
    ]


def test_single_all_recipe_preserves_deep_normal_search_diversity(
    config: Config,
) -> None:
    """A lone broad clause cannot truncate a film hit below raw rank 100."""
    from pipeline.search.recipe import SearchClause, search_recipe
    from pipeline.search.retrieve import search

    config.retrieval.diversity.page_size = 12
    config.retrieval.diversity.film_results_per_page_target = 2
    crowded = [
        _make_unit_row(
            f"crowded_{index:03d}",
            "film_crowded",
            caption=f"Distinct moment {index}",
            searchable_text=f"moment {index}",
            t_start=float(index * 100),
            t_end=float(index * 100 + 2),
            img_vec=_basis_vec(index),
            _distance=0.001 + index * 0.001,
        )
        for index in range(102)
    ]
    deep_other_film = _make_unit_row(
        "deep_other_film",
        "film_other",
        caption="A lower-ranked moment from another film",
        searchable_text="another film moment",
        t_start=20_000.0,
        t_end=20_002.0,
        img_vec=_basis_vec(200),
        _distance=0.9,
    )
    rows = [*crowded, deep_other_film]
    db = _make_hybrid_mock_db(
        image_rows=rows,
        text_rows=rows,
        lexical_rows=[],
    )

    with patch("pipeline.search.retrieve.embed_text", return_value=_fake_vec()):
        normal_results = search("moment", db, config)
        recipe_results = search_recipe(
            [SearchClause("main", "text", "all", text="moment")],
            db,
            config,
        )

    assert "deep_other_film" in [
        result["unit_id"] for result in normal_results[:12]
    ]
    assert [result["unit_id"] for result in recipe_results] == [
        result["unit_id"] for result in normal_results
    ]
    for recipe_result, normal_result in zip(
        recipe_results,
        normal_results,
        strict=True,
    ):
        assert {
            key: value
            for key, value in recipe_result.items()
            if key != "matches"
        } == normal_result
        assert recipe_result["matches"][0]["rank"] == normal_result["rank"]


def test_search_disables_film_cap_for_explicit_scope(config: Config) -> None:
    """An explicit movie scope preserves relevance order without balancing."""
    from pipeline.search.retrieve import search

    config.retrieval.diversity.page_size = 4
    config.retrieval.diversity.film_results_per_page_target = 2
    rows = [
        _make_unit_row(
            f"scoped_{index}",
            "selected_film",
            caption=f"Distinct selected-film moment {index}",
            searchable_text=f"selected moment {index}",
            t_start=float(index * 100),
            t_end=float(index * 100 + 2),
            img_vec=_basis_vec(index),
            _distance=0.01 + index * 0.01,
        )
        for index in range(6)
    ]
    db = _make_hybrid_mock_db(
        image_rows=rows,
        text_rows=rows,
        lexical_rows=[],
    )

    with patch("pipeline.search.retrieve.embed_text", return_value=_fake_vec()):
        results = search(
            "moment",
            db,
            config,
            film_ids=["selected_film"],
            result_limit=6,
        )

    assert [result["unit_id"] for result in results] == [
        f"scoped_{index}" for index in range(6)
    ]


def test_search_deduplicates_channels_and_near_identical_images(
    config: Config,
) -> None:
    """One unit appears once, and a .95+ cosine visual duplicate is removed."""
    from pipeline.search.retrieve import search

    original = _make_unit_row(
        "original",
        "film_one",
        caption="A red car",
        searchable_text="red car road",
        t_start=0.0,
        t_end=2.0,
        img_vec=_basis_vec(0),
        _distance=0.01,
    )
    visual_duplicate = _make_unit_row(
        "visual_duplicate",
        "film_two",
        caption="The same red car",
        searchable_text="same red car road",
        t_start=100.0,
        t_end=102.0,
        img_vec=_basis_vec(0),
        _distance=0.02,
    )
    distinct = _make_unit_row(
        "distinct",
        "film_three",
        caption="A blue truck",
        searchable_text="blue truck road",
        t_start=200.0,
        t_end=202.0,
        img_vec=_basis_vec(1),
        _distance=0.03,
    )
    db = _make_hybrid_mock_db(
        image_rows=[original, visual_duplicate, distinct],
        text_rows=[original, visual_duplicate, distinct],
        lexical_rows=[original, visual_duplicate, distinct],
    )

    with patch("pipeline.search.retrieve.embed_text", return_value=_fake_vec()):
        results = search("road", db, config)
        raw_results = search(
            "road",
            db,
            config,
            _defer_result_preferences=True,
        )

    unit_ids = [result["unit_id"] for result in results]
    assert unit_ids.count("original") == 1
    assert "visual_duplicate" not in unit_ids
    assert "distinct" in unit_ids
    assert [result["unit_id"] for result in raw_results] == [
        "original",
        "visual_duplicate",
        "distinct",
    ]


def test_search_keeps_temporally_adjacent_visually_distinct_results(
    config: Config,
) -> None:
    """Nearby but visually different shots remain eligible in a one-film index."""
    from pipeline.search.retrieve import search

    near_a = _make_unit_row(
        "near_a",
        "same_film",
        caption="A car at night",
        searchable_text="car night",
        t_start=10.0,
        t_end=12.0,
        img_vec=_basis_vec(0),
        _distance=0.01,
    )
    near_b = _make_unit_row(
        "near_b",
        "same_film",
        caption="A car turns",
        searchable_text="car turn",
        t_start=35.0,
        t_end=37.0,
        img_vec=_basis_vec(1),
        _distance=0.02,
    )
    far = _make_unit_row(
        "far",
        "same_film",
        caption="A car on a bridge",
        searchable_text="car bridge",
        t_start=80.0,
        t_end=82.0,
        img_vec=_basis_vec(2),
        _distance=0.03,
    )
    db = _make_hybrid_mock_db(
        image_rows=[near_a, near_b, far],
        text_rows=[near_a, near_b, far],
        lexical_rows=[near_a, near_b, far],
    )

    with patch("pipeline.search.retrieve.embed_text", return_value=_fake_vec()):
        results = search("car", db, config)

    assert [result["unit_id"] for result in results] == ["near_a", "near_b", "far"]


def test_search_only_temporally_suppresses_visually_similar_results(
    config: Config,
) -> None:
    """A >.90-similar adjacent shot is removed, while a distant one remains."""
    from pipeline.search.retrieve import search

    original = _make_unit_row(
        "original",
        "same_film",
        caption="A car enters a tunnel",
        searchable_text="car tunnel",
        t_start=10.0,
        t_end=12.0,
        img_vec=_basis_vec(0),
        _distance=0.01,
    )
    adjacent_similar = _make_unit_row(
        "adjacent_similar",
        "same_film",
        caption="The car continues through the tunnel",
        searchable_text="car tunnel",
        t_start=35.0,
        t_end=37.0,
        img_vec=_cosine_vec(0.91),
        _distance=0.02,
    )
    distant_similar = _make_unit_row(
        "distant_similar",
        "same_film",
        caption="The car returns to the tunnel later",
        searchable_text="car tunnel",
        t_start=80.0,
        t_end=82.0,
        img_vec=_cosine_vec(0.91),
        _distance=0.03,
    )
    db = _make_hybrid_mock_db(
        image_rows=[original, adjacent_similar, distant_similar],
        text_rows=[original, adjacent_similar, distant_similar],
        lexical_rows=[],
    )

    with patch("pipeline.search.retrieve.embed_text", return_value=_fake_vec()):
        results = search("car", db, config)

    assert [result["unit_id"] for result in results] == [
        "original",
        "distant_similar",
    ]


def test_search_uses_middle_keyframe_and_serializable_debug(
    config: Config,
) -> None:
    """Three-frame shots display frame 1 and expose rank/channel diagnostics."""
    from pipeline.search.retrieve import search

    shot_id = "film_abc_0007"
    row = _make_unit_row(
        shot_id,
        keyframe_paths=json.dumps(
            [
                f"/assets/{shot_id}_0.webp",
                f"/assets/{shot_id}_1.webp",
                f"/assets/{shot_id}_2.webp",
            ]
        ),
        img_vec=_basis_vec(0),
    )
    db = _make_hybrid_mock_db(
        image_rows=[row],
        text_rows=[row],
        lexical_rows=[row],
    )

    with patch("pipeline.search.retrieve.embed_text", return_value=_fake_vec()):
        result = search("rainy night", db, config)[0]

    assert result["keyframe_url"] == f"/media/keyframe/{shot_id}/1"
    assert result["rank"] == 1
    assert set(result["debug"]) == {"final_score", "channels"}
    assert set(result["debug"]["channels"]) == {"img", "txt", "lex"}
    assert result["debug"]["channels"]["img"]["distance"] == pytest.approx(0.1)
    assert result["debug"]["channels"]["lex"]["distance"] is None
    json.dumps(result)


def test_search_uses_best_frame_per_shot_and_returns_match_evidence(
    config: Config,
) -> None:
    """Frame retrieval collapses to shots and displays each shot's argmax."""
    from pipeline.search.retrieve import search

    first = _make_unit_row(
        "first",
        "film_one",
        caption="A person makes a subtle motion",
        searchable_text="person subtle motion",
        keyframe_paths=json.dumps(["a.webp", "b.webp", "c.webp"]),
        img_vec=_basis_vec(0),
    )
    second = _make_unit_row(
        "second",
        "film_two",
        caption="Another person moves",
        searchable_text="another person moves",
        keyframe_paths=json.dumps(["d.webp"]),
        img_vec=_basis_vec(1),
    )
    frame_rows = [
        {
            "frame_id": "first_0",
            "unit_id": "first",
            "shot_id": "first",
            "film_id": "film_one",
            "frame_index": 0,
            "timestamp": 11.0,
            "path": "a.webp",
            "_distance": 0.20,
        },
        {
            "frame_id": "second_0",
            "unit_id": "second",
            "shot_id": "second",
            "film_id": "film_two",
            "frame_index": 0,
            "timestamp": 20.0,
            "path": "d.webp",
            "_distance": 0.02,
        },
        {
            "frame_id": "first_1",
            "unit_id": "first",
            "shot_id": "first",
            "film_id": "film_one",
            "frame_index": 1,
            "timestamp": 12.5,
            "path": "b.webp",
            "_distance": 0.01,
        },
        {
            "frame_id": "first_2",
            "unit_id": "first",
            "shot_id": "first",
            "film_id": "film_one",
            "frame_index": 2,
            "timestamp": 14.0,
            "path": "c.webp",
            "_distance": 0.30,
        },
    ]
    db, units, frames = _make_frame_hybrid_mock_db(
        frame_rows=frame_rows,
        fallback_image_rows=[second, first],
        text_rows=[],
        lexical_rows=[first, second],
    )

    with patch("pipeline.search.retrieve.embed_text", return_value=_fake_vec()):
        results = search("distinctive gesture", db, config)

    assert [result["unit_id"] for result in results] == ["first", "second"]
    assert len(results) == 2
    first_result = results[0]
    assert first_result["keyframe_url"] == "/media/keyframe/first/1"
    assert first_result["matched_frame_url"] == "/media/keyframe/first/1"
    assert first_result["matched_frame_index"] == 1
    assert first_result["matched_frame_timestamp"] == pytest.approx(12.5)
    image_debug = first_result["debug"]["channels"]["img"]
    assert image_debug["source"] == "frame"
    assert image_debug["rank"] == 1
    assert image_debug["matched_frame"]["frame_id"] == "first_1"
    assert "path" not in image_debug["matched_frame"]
    assert not any(
        call.kwargs.get("vector_column_name") == "img_vec"
        for call in units.search.call_args_list
    )
    assert frames.search.call_args.kwargs["vector_column_name"] == "visual_vec"
    json.dumps(results)


def test_search_fetches_frame_hit_units_outside_lexical_candidates(
    config: Config,
) -> None:
    """Frame hits are joined directly when absent from the FTS candidate set."""
    from pipeline.search.retrieve import search

    scanned = _make_unit_row(
        "inside_scan",
        "selected_film",
        caption="An unrelated quiet room",
        searchable_text="unrelated quiet room",
    )
    frame_hit = _make_unit_row(
        "outside_scan",
        "selected_film",
        caption="A distinctive gesture",
        searchable_text="distinctive gesture",
        img_vec=_basis_vec(1),
    )
    out_of_scope = _make_unit_row(
        "other_film_hit",
        "other_film",
        caption="The same distinctive gesture",
        searchable_text="distinctive gesture",
        img_vec=_basis_vec(2),
    )
    frame_rows = [
        {
            "frame_id": "outside_scan_0",
            "unit_id": "outside_scan",
            "shot_id": "outside_scan",
            "film_id": "selected_film",
            "frame_index": 0,
            "timestamp": 42.0,
            "path": "outside.webp",
            "_distance": 0.01,
        },
        {
            "frame_id": "other_film_hit_0",
            "unit_id": "other_film_hit",
            "shot_id": "other_film_hit",
            "film_id": "other_film",
            "frame_index": 0,
            "timestamp": 24.0,
            "path": "other.webp",
            "_distance": 0.001,
        },
    ]
    db, units, frames = _make_frame_hybrid_mock_db(
        frame_rows=frame_rows,
        fallback_image_rows=[],
        text_rows=[],
        lexical_rows=[scanned],
        frame_unit_rows=[frame_hit, out_of_scope],
    )

    with patch("pipeline.search.retrieve.embed_text", return_value=_fake_vec()):
        results = search(
            "distinctive gesture",
            db,
            config,
            film_ids=["selected_film"],
        )

    assert [result["unit_id"] for result in results] == ["outside_scan"]
    assert results[0]["matched_frame_url"] == "/media/keyframe/outside_scan/0"
    assert len(units._scalar_query_chains) == 2
    lexical_query, frame_unit_query = units._scalar_query_chains
    lexical_query.limit.assert_called_once_with(
        config.retrieval.candidate_limit * 3
    )
    frame_unit_query.limit.assert_called_once_with(1)
    frame_unit_query.where.assert_called_once()
    frames._query_chain.where.assert_called_once()


def test_search_falls_back_to_unit_image_vector_when_frames_empty(
    config: Config,
) -> None:
    """An existing but empty frames table preserves the old image channel."""
    from pipeline.search.retrieve import search

    row = _make_unit_row(
        "fallback",
        keyframe_paths=json.dumps(["a.webp", "b.webp", "c.webp"]),
        img_vec=_basis_vec(0),
    )
    db, units, frames = _make_frame_hybrid_mock_db(
        frame_rows=[],
        fallback_image_rows=[row],
        text_rows=[],
        lexical_rows=[row],
    )

    with patch("pipeline.search.retrieve.embed_text", return_value=_fake_vec()):
        result = search("rain", db, config)[0]

    assert result["keyframe_url"] == "/media/keyframe/fallback/1"
    assert "matched_frame_index" not in result
    assert result["debug"]["channels"]["img"]["source"] == "unit"
    assert frames.search.called
    assert any(
        call.kwargs.get("vector_column_name") == "img_vec"
        for call in units.search.call_args_list
    )


def test_search_honors_an_explicit_smaller_result_limit(config: Config) -> None:
    """A caller can request the legacy twelve-result presentation prefix."""
    from pipeline.search.retrieve import search

    rows = [
        _make_unit_row(
            f"shot_{index}",
            f"film_{index}",
            caption=f"Car number {index}",
            searchable_text=f"car number {index}",
            t_start=float(index * 100),
            t_end=float(index * 100 + 2),
            img_vec=_basis_vec(index),
            _distance=index / 100,
        )
        for index in range(20)
    ]
    db = _make_hybrid_mock_db(
        image_rows=rows,
        text_rows=rows,
        lexical_rows=rows,
    )

    with patch("pipeline.search.retrieve.embed_text", return_value=_fake_vec()):
        results = search("car", db, config, result_limit=12)

    assert len(results) == 12
    assert [result["rank"] for result in results] == list(range(1, 13))


def test_search_result_window_has_stable_smaller_prefix(config: Config) -> None:
    """Expanding the window cannot reshuffle results already shown."""
    from pipeline.search.retrieve import search

    rows = [
        _make_unit_row(
            f"stable_{index}",
            f"film_{index}",
            caption=f"Stable moment {index}",
            searchable_text=f"stable moment {index}",
            t_start=float(index * 100),
            t_end=float(index * 100 + 2),
            img_vec=_basis_vec(index),
            _distance=index / 1000,
        )
        for index in range(60)
    ]
    db = _make_hybrid_mock_db(
        image_rows=rows,
        text_rows=rows,
        lexical_rows=rows,
    )

    with patch("pipeline.search.retrieve.embed_text", return_value=_fake_vec()):
        first_page = search("stable", db, config, result_limit=12)
        full_window = search("stable", db, config)
        evaluation_window = search("stable", db, config, result_limit=100)

    assert len(full_window) == 48
    assert [row["unit_id"] for row in first_page] == [
        row["unit_id"] for row in full_window[:12]
    ]
    assert [row["unit_id"] for row in full_window] == [
        row["unit_id"] for row in evaluation_window[:48]
    ]


def test_search_supports_bounded_top_100_for_evaluation(config: Config) -> None:
    from pipeline.search.retrieve import search

    rows = [
        _make_unit_row(
            f"eval_{index}",
            f"film_{index}",
            img_vec=_basis_vec(index),
            _distance=index / 1000,
        )
        for index in range(110)
    ]
    db = _make_hybrid_mock_db(
        image_rows=rows,
        text_rows=rows,
        lexical_rows=[],
    )

    with patch("pipeline.search.retrieve.embed_text", return_value=_fake_vec()):
        assert len(search("moment", db, config, result_limit=100)) == 100


def test_search_rejects_invalid_result_limit_before_work(config: Config) -> None:
    from pipeline.search.retrieve import search

    db = MagicMock()
    with (
        patch("pipeline.search.retrieve.embed_text") as embed,
        pytest.raises(ValueError, match="between 1 and 100"),
    ):
        search("moment", db, config, result_limit=101)

    db.open_table.assert_not_called()
    embed.assert_not_called()


# ---------------------------------------------------------------------------
# FastAPI app endpoints
# ---------------------------------------------------------------------------


def test_reference_and_text_fusion_rewards_agreement_and_preserves_evidence() -> None:
    from pipeline.search.retrieve import _fuse_reference_and_text_results

    reference_results = [
        {
            "unit_id": "reference_only",
            "matched_frame_url": "/media/keyframe/reference_only/0",
            "debug": {"channels": {"spatial": {"rank": 1}}},
        },
        {
            "unit_id": "both",
            "matched_frame_url": "/media/keyframe/both/2",
            "debug": {"channels": {"spatial": {"rank": 2}}},
        },
    ]
    text_results = [
        {
            "unit_id": "both",
            "matched_text_view": "dialogue",
            "matched_text": "Come out into the rain.",
            "debug": {"channels": {"txt": {"rank": 1}}},
        },
        {
            "unit_id": "text_only",
            "debug": {"channels": {"lex": {"rank": 2}}},
        },
    ]

    results = _fuse_reference_and_text_results(
        reference_results,
        text_results,
        result_limit=3,
    )

    assert [result["unit_id"] for result in results] == [
        "both",
        "reference_only",
    ]
    assert results[0]["matched_frame_url"] == "/media/keyframe/both/2"
    assert results[0]["matched_text_view"] == "dialogue"
    assert results[0]["matched_text"] == "Come out into the rain."
    assert set(results[0]["debug"]["channels"]) == {"spatial"}
    assert set(results[0]["debug"]["clauses"]) == {"reference", "text"}
    assert set(
        results[0]["debug"]["clauses"]["text"]["channels"]
    ) == {"txt"}
    assert results[0]["debug"]["query_ranks"] == {
        "reference": 2,
        "text": 1,
    }
    assert results[0]["debug"]["mode"] == "reference_image_text"


def test_search_by_image_with_text_runs_both_replaceable_retrievers(
    config: Config,
) -> None:
    from pipeline.search.retrieve import search_by_image

    reference_results = [{"unit_id": "both", "debug": {"channels": {}}}]
    text_results = [{"unit_id": "both", "debug": {"channels": {}}}]
    image = Image.new("RGB", (32, 18), "black")
    db = MagicMock()

    with (
        patch(
            "pipeline.search.retrieve._search_by_image_only",
            return_value=reference_results,
        ) as image_search,
        patch(
            "pipeline.search.retrieve.search",
            return_value=text_results,
        ) as text_search,
    ):
        results = search_by_image(
            image,
            db,
            config,
            film_ids=["film_one", "film_two"],
            exclude_unit_id="source",
            exclude_film_id="film_one",
            result_limit=7,
            text_query="  neon rain  ",
        )

    assert [result["unit_id"] for result in results] == ["both"]
    image_search.assert_called_once_with(
        image,
        db,
        config,
        film_ids=("film_two",),
        exclude_unit_id="source",
        result_limit=100,
        requested_text="neon rain",
        deduplicate_visual=False,
        apply_film_diversity=False,
    )
    text_search.assert_called_once_with(
        "neon rain",
        db,
        config,
        film_ids=("film_two",),
        result_limit=100,
    )


def test_search_by_image_excludes_source_film_before_unscoped_candidates(
    config: Config,
) -> None:
    """Cross-film matching frees ANN slots before either retriever runs."""
    from pipeline.search.retrieve import search_by_image

    films = MagicMock()
    films.search.return_value = _make_query_chain(
        [
            {"film_id": "source_film"},
            {"film_id": "other_b"},
            {"film_id": "other_a"},
        ]
    )
    db = MagicMock()
    db.list_tables.return_value.tables = ["films"]
    db.open_table.return_value = films
    image = Image.new("RGB", (32, 18), "black")

    with (
        patch("pipeline.search.retrieve.require_visual_encoder_profile"),
        patch(
            "pipeline.search.retrieve._search_by_image_only",
            return_value=[],
        ) as image_search,
    ):
        results = search_by_image(
            image,
            db,
            config,
            exclude_film_id="source_film",
        )

    assert results == []
    image_search.assert_called_once_with(
        image,
        db,
        config,
        film_ids=("other_a", "other_b"),
        exclude_unit_id=None,
        result_limit=48,
        requested_text="",
        deduplicate_visual=True,
        apply_film_diversity=True,
    )


def test_search_by_image_returns_empty_when_source_is_only_published_film(
    config: Config,
) -> None:
    """Cross-film matching never lets the empty-scope sentinel include source."""
    from pipeline.search.retrieve import search_by_image

    films = MagicMock()
    films.search.return_value = _make_query_chain([{"film_id": "source_film"}])
    db = MagicMock()
    db.list_tables.return_value.tables = ["films"]
    db.open_table.return_value = films

    with (
        patch("pipeline.search.retrieve.require_visual_encoder_profile"),
        patch("pipeline.search.retrieve._search_by_image_only") as image_search,
    ):
        results = search_by_image(
            Image.new("RGB", (32, 18), "black"),
            db,
            config,
            exclude_film_id="source_film",
        )

    assert results == []
    image_search.assert_not_called()


def test_search_by_image_explicit_source_only_scope_takes_precedence(
    config: Config,
) -> None:
    """A deliberate one-film scope remains usable with cross-film defaults."""
    from pipeline.search.retrieve import search_by_image

    image = Image.new("RGB", (32, 18), "black")
    db = MagicMock()
    with (
        patch("pipeline.search.retrieve.require_visual_encoder_profile"),
        patch(
            "pipeline.search.retrieve._search_by_image_only",
            return_value=[],
        ) as image_search,
    ):
        results = search_by_image(
            image,
            db,
            config,
            film_ids=["source_film"],
            exclude_film_id="source_film",
        )

    assert results == []
    image_search.assert_called_once_with(
        image,
        db,
        config,
        film_ids=("source_film",),
        exclude_unit_id=None,
        result_limit=48,
        requested_text="",
        deduplicate_visual=True,
        apply_film_diversity=False,
    )


def test_blank_text_preserves_reference_search_exactly(config: Config) -> None:
    from pipeline.search.retrieve import search_by_image

    reference_results = [
        {
            "unit_id": "first",
            "rank": 1,
            "debug": {"mode": "reference_image", "channels": {}},
        }
    ]
    image = Image.new("RGB", (32, 18), "black")

    with (
        patch(
            "pipeline.search.retrieve._search_by_image_only",
            return_value=reference_results,
        ) as image_search,
        patch("pipeline.search.retrieve.search") as text_search,
    ):
        results = search_by_image(
            image,
            MagicMock(),
            config,
            text_query="   ",
        )

    assert results is reference_results
    assert image_search.call_args.kwargs["requested_text"] == ""
    assert image_search.call_args.kwargs["deduplicate_visual"] is True
    text_search.assert_not_called()


def test_text_constraint_can_retain_visually_similar_repeated_shots(
    config: Config,
) -> None:
    from pipeline.search.retrieve import _search_by_image_only

    first = _make_unit_row("first", "film_one", img_vec=_basis_vec(0))
    second = _make_unit_row("second", "film_one", img_vec=_basis_vec(0))
    frame_rows = [
        {
            "frame_id": f"{unit_id}_0",
            "unit_id": unit_id,
            "shot_id": unit_id,
            "film_id": "film_one",
            "frame_index": 0,
            "timestamp": timestamp,
            "path": f"{unit_id}.webp",
            "_distance": distance,
            "_semantic_rank": rank,
            "_semantic_score": 1.0 - distance,
            "_spatial_rank": rank,
            "_spatial_score": 1.0 - distance,
            "_reference_score": 1.0 - distance,
        }
        for unit_id, timestamp, distance, rank in (
            ("first", 10.0, 0.01, 1),
            ("second", 200.0, 0.02, 2),
        )
    ]
    units = MagicMock()
    units.search.return_value = _make_query_chain([first, second])
    db = MagicMock()
    db.open_table.return_value = units

    with patch(
        "pipeline.search.retrieve._reference_frame_candidates",
        return_value=frame_rows,
    ):
        image_only = _search_by_image_only(
            Image.new("RGB", (32, 18), "black"),
            db,
            config,
            result_limit=2,
        )
        combined_candidates = _search_by_image_only(
            Image.new("RGB", (32, 18), "black"),
            db,
            config,
            result_limit=2,
            requested_text="the second conversation",
            deduplicate_visual=False,
        )
        recipe_candidates = _search_by_image_only(
            Image.new("RGB", (32, 18), "black"),
            db,
            config,
            result_limit=2,
            _defer_result_preferences=True,
        )

    assert [result["unit_id"] for result in image_only] == ["first"]
    assert [result["unit_id"] for result in combined_candidates] == [
        "first",
        "second",
    ]
    assert [result["unit_id"] for result in recipe_candidates] == [
        "first",
        "second",
    ]


def test_spatial_grid_scores_reward_matching_screen_cells() -> None:
    """Equal global content scores still distinguish aligned from swapped layout."""
    from pipeline.search.retrieve import _spatial_grid_scores

    query = np.zeros((2, 2, 2), dtype=np.float32)
    query[:, 0, 0] = 1.0
    query[:, 1, 1] = 1.0
    aligned = query.copy()
    horizontally_swapped = query[:, ::-1, :].copy()

    scores = _spatial_grid_scores(
        query,
        np.stack([aligned, horizontally_swapped]),
    )

    assert scores[0] == pytest.approx(1.0)
    assert scores[1] == pytest.approx(0.0)


def test_search_by_image_blends_semantics_with_aligned_spatial_cells(
    tmp_path: Path,
    config: Config,
) -> None:
    """A strong aligned-grid match can improve a close semantic candidate."""
    from pipeline.search.retrieve import search_by_image

    first_path = tmp_path / "first.webp"
    second_path = tmp_path / "second.webp"
    Image.new("RGB", (64, 36), "navy").save(first_path)
    Image.new("RGB", (64, 36), "navy").save(second_path)

    first = _make_unit_row(
        "first",
        "film_one",
        caption="Semantic favorite with a different layout",
    )
    second = _make_unit_row(
        "second",
        "film_one",
        caption="Slightly weaker semantics with aligned layout",
    )
    frame_rows = [
        {
            "frame_id": "first_0",
            "unit_id": "first",
            "shot_id": "first",
            "film_id": "film_one",
            "frame_index": 0,
            "timestamp": 10.0,
            "path": str(first_path),
            "_distance": 0.10,
        },
        {
            "frame_id": "second_0",
            "unit_id": "second",
            "shot_id": "second",
            "film_id": "film_one",
            "frame_index": 0,
            "timestamp": 130.0,
            "path": str(second_path),
            "_distance": 0.15,
        },
    ]

    frames = MagicMock()
    frames.search.return_value = _make_query_chain(frame_rows)
    _mark_frames_as_current_profile(frames, len(frame_rows))
    units = MagicMock()
    units.search.return_value = _make_query_chain([first, second])
    db = MagicMock()
    db.list_tables.return_value.tables = ["frames", "units"]
    db.open_table.side_effect = lambda name: {
        "frames": frames,
        "units": units,
    }[name]

    query_grid = np.zeros((1, 6, 6, 2), dtype=np.float32)
    query_grid[..., 0] = 1.0
    candidate_grids = np.zeros((2, 6, 6, 2), dtype=np.float32)
    candidate_grids[0, ..., 0] = -1.0
    candidate_grids[1, ..., 0] = 1.0
    global_query = np.zeros((1, VEC_DIM), dtype=np.float32)
    global_query[0, 0] = 1.0
    global_candidates = np.repeat(global_query, 2, axis=0)

    with patch(
        "pipeline.search.retrieve.embed_spatial_images",
        side_effect=[
            (global_query, query_grid),
            (global_candidates, candidate_grids),
        ],
    ):
        results = search_by_image(
            Image.new("RGB", (64, 36), "navy"),
            db,
            config,
        )

    assert [result["unit_id"] for result in results] == ["second", "first"]
    assert results[0]["debug"]["mode"] == "reference_image"
    assert results[0]["debug"]["channels"]["spatial"]["rank"] == 1
    assert results[0]["matched_frame_url"] == "/media/keyframe/second/0"


def test_search_by_image_uses_active_spatial_cache_without_candidate_encoding(
    tmp_path: Path,
    config: Config,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A complete compatible cache leaves only the reference image on GPU."""
    from pipeline.index.framing_features import FramingSpatialProfile
    from pipeline.search.retrieve import search_by_image

    paths = [tmp_path / "first.webp", tmp_path / "second.webp"]
    for path in paths:
        Image.new("RGB", (64, 36), "navy").save(path)
    units_rows = [
        _make_unit_row("first", "film_one"),
        _make_unit_row("second", "film_one"),
    ]
    frame_rows = [
        {
            "frame_id": f"{unit_id}_0",
            "unit_id": unit_id,
            "shot_id": unit_id,
            "film_id": "film_one",
            "frame_index": 0,
            "timestamp": timestamp,
            "path": str(path),
            "_distance": distance,
        }
        for unit_id, timestamp, path, distance in (
            ("first", 10.0, paths[0], 0.10),
            ("second", 130.0, paths[1], 0.15),
        )
    ]
    frames = MagicMock()
    frames.search.return_value = _make_query_chain(frame_rows)
    _mark_frames_as_current_profile(frames, len(frame_rows))
    units = MagicMock()
    units.search.return_value = _make_query_chain(units_rows)
    db = MagicMock()
    db.list_tables.return_value.tables = ["frames", "units"]
    db.open_table.side_effect = lambda name: {
        "frames": frames,
        "units": units,
    }[name]
    profile = FramingSpatialProfile(
        profile_id="test",
        table_name="frame_framing_test",
        encoder_name="pe_core_l14",
        model_id="test",
        model_revision="a" * 40,
        open_clip_version="test",
        timm_version="test",
        torch_version="test",
        torchvision_version="test",
        pillow_version="test",
        row_schema_version=1,
        grid_size=6,
        feature_dim=2,
        extraction_contract_version=1,
        storage_dtype="float16-le",
    )
    global_query = np.zeros((1, VEC_DIM), dtype=np.float32)
    query_grid = np.zeros((1, 6, 6, 2), dtype=np.float32)
    query_grid[..., 0] = 1.0
    cached = np.zeros((2, 6, 6, 2), dtype=np.float32)
    cached[0, ..., 0] = -1.0
    cached[1, ..., 0] = 1.0
    spatial_embed = MagicMock(return_value=(global_query, query_grid))

    with (
        caplog.at_level(logging.INFO, logger="uvicorn.error"),
        patch(
            "pipeline.search.retrieve.resolve_ready_framing_profile",
            return_value=profile,
        ),
        patch(
            "pipeline.search.retrieve.load_framing_grids",
            return_value=cached,
        ) as load_cache,
        patch(
            "pipeline.search.retrieve.embed_spatial_images",
            spatial_embed,
        ),
    ):
        results = search_by_image(
            Image.new("RGB", (64, 36), "navy"),
            db,
            config,
        )

    assert [result["unit_id"] for result in results] == ["second", "first"]
    spatial_embed.assert_called_once()
    assert spatial_embed.call_args.kwargs["model_revision"] == "a" * 40
    load_cache.assert_called_once_with(db, profile, ["first_0", "second_0"])
    assert any(
        "framing_search cache=hit reason=profile_ready" in message
        and "candidates=2 spatial_candidates=2" in message
        for message in caplog.messages
    )


def test_unreadable_spatial_cache_falls_back_for_whole_shortlist(
    tmp_path: Path,
    config: Config,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """No candidate receives cached evidence when the active lookup fails."""
    from pipeline.index.framing_features import FramingSpatialProfile
    from pipeline.search.retrieve import _reference_frame_candidates

    paths = [tmp_path / "first.webp", tmp_path / "second.webp"]
    for path in paths:
        Image.new("RGB", (64, 36), "navy").save(path)
    rows = [
        {
            "frame_id": f"{unit_id}_0",
            "unit_id": unit_id,
            "shot_id": unit_id,
            "film_id": "film_one",
            "path": str(path),
            "_distance": distance,
        }
        for unit_id, path, distance in (
            ("first", paths[0], 0.1),
            ("second", paths[1], 0.2),
        )
    ]
    frames = MagicMock()
    frames.search.return_value = _make_query_chain(rows)
    db = MagicMock()
    db.list_tables.return_value.tables = ["frames"]
    db.open_table.return_value = frames
    profile = FramingSpatialProfile(
        profile_id="test",
        table_name="frame_framing_test",
        encoder_name="pe_core_l14",
        model_id="test",
        model_revision="a" * 40,
        open_clip_version="test",
        timm_version="test",
        torch_version="test",
        torchvision_version="test",
        pillow_version="test",
        row_schema_version=1,
        grid_size=6,
        feature_dim=2,
        extraction_contract_version=1,
        storage_dtype="float16-le",
    )
    global_query = np.zeros((1, VEC_DIM), dtype=np.float32)
    query_grid = np.zeros((1, 6, 6, 2), dtype=np.float32)
    query_grid[..., 0] = 1.0
    live_grids = np.repeat(query_grid, 2, axis=0)
    spatial_embed = MagicMock(
        side_effect=[
            (global_query, query_grid),
            (np.repeat(global_query, 2, axis=0), live_grids),
        ]
    )

    with (
        caplog.at_level(logging.INFO, logger="uvicorn.error"),
        patch(
            "pipeline.search.retrieve.resolve_ready_framing_profile",
            return_value=profile,
        ),
        patch(
            "pipeline.search.retrieve.load_framing_grids",
            return_value=None,
        ),
        patch(
            "pipeline.search.retrieve.embed_spatial_images",
            spatial_embed,
        ),
    ):
        candidates = _reference_frame_candidates(
            Image.new("RGB", (64, 36), "navy"),
            db,
            config,
            (),
            candidate_limit=2,
        )

    assert len(candidates) == 2
    assert all(row["_spatial_score"] is not None for row in candidates)
    assert len(spatial_embed.call_args_list) == 2
    assert len(spatial_embed.call_args_list[1].args[0]) == 2
    assert all(
        call.kwargs["model_revision"] == "a" * 40
        for call in spatial_embed.call_args_list
    )
    assert any(
        "framing_search cache=live reason=cache_read_failed" in message
        and "candidates=2 spatial_candidates=2" in message
        for message in caplog.messages
    )


def test_frames_generation_change_after_candidates_disables_cache(
    tmp_path: Path,
    config: Config,
) -> None:
    """Manifest readiness is rechecked after the frame ANN snapshot is read."""
    from pipeline.index.framing_features import FramingSpatialProfile
    from pipeline.search.retrieve import _reference_frame_candidates

    path = tmp_path / "candidate.webp"
    Image.new("RGB", (64, 36), "navy").save(path)
    rows = [
        {
            "frame_id": "candidate_0",
            "unit_id": "candidate",
            "shot_id": "candidate",
            "film_id": "film_one",
            "path": str(path),
            "_distance": 0.1,
        }
    ]
    frames = MagicMock()
    frames.search.return_value = _make_query_chain(rows)
    db = MagicMock()
    db.list_tables.return_value.tables = ["frames"]
    db.open_table.return_value = frames
    profile = FramingSpatialProfile(
        profile_id="test",
        table_name="frame_framing_test",
        encoder_name="pe_core_l14",
        model_id="test",
        model_revision="a" * 40,
        open_clip_version="test",
        timm_version="test",
        torch_version="test",
        torchvision_version="test",
        pillow_version="test",
        row_schema_version=1,
        grid_size=6,
        feature_dim=2,
        extraction_contract_version=1,
        storage_dtype="float16-le",
    )
    global_query = np.zeros((1, VEC_DIM), dtype=np.float32)
    query_grid = np.zeros((1, 6, 6, 2), dtype=np.float32)
    query_grid[..., 0] = 1.0
    spatial_embed = MagicMock(
        side_effect=[
            (global_query, query_grid),
            (global_query, query_grid),
        ]
    )

    with (
        patch(
            "pipeline.search.retrieve.resolve_ready_framing_profile",
            side_effect=[profile, None],
        ) as resolve_profile,
        patch(
            "pipeline.search.retrieve.load_framing_grids",
        ) as load_cache,
        patch(
            "pipeline.search.retrieve.embed_spatial_images",
            spatial_embed,
        ),
    ):
        candidates = _reference_frame_candidates(
            Image.new("RGB", (64, 36), "navy"),
            db,
            config,
            (),
            candidate_limit=1,
        )

    assert len(candidates) == 1
    assert resolve_profile.call_count == 2
    assert resolve_profile.call_args_list[0].kwargs == {
        "validate_frame_ids": False
    }
    load_cache.assert_not_called()
    assert len(spatial_embed.call_args_list) == 2


def test_reference_image_semantic_backfill_follows_spatial_shortlist(
    tmp_path: Path,
    config: Config,
) -> None:
    """A missing spatial score is never an accidental ranking advantage."""
    from pipeline.search.retrieve import _reference_frame_candidates

    shortlist_path = tmp_path / "shortlist.webp"
    Image.new("RGB", (64, 36), "navy").save(shortlist_path)
    frame_rows = [
        {
            "frame_id": "shortlist_0",
            "unit_id": "shortlist",
            "shot_id": "shortlist",
            "film_id": "film_one",
            "frame_index": 0,
            "timestamp": 10.0,
            "path": str(shortlist_path),
            "_distance": 0.05,
        },
        {
            "frame_id": "backfill_0",
            "unit_id": "backfill",
            "shot_id": "backfill",
            "film_id": "film_one",
            "frame_index": 0,
            "timestamp": 130.0,
            "path": str(tmp_path / "unused.webp"),
            "_distance": 0.15,
        },
    ]
    frames = MagicMock()
    frames.search.return_value = _make_query_chain(frame_rows)
    db = MagicMock()
    db.list_tables.return_value.tables = ["frames"]
    db.open_table.return_value = frames

    global_query = np.zeros((1, VEC_DIM), dtype=np.float32)
    global_query[0, 0] = 1.0
    query_grid = np.zeros((1, 6, 6, 2), dtype=np.float32)
    query_grid[..., 0] = 1.0
    opposite_grid = -query_grid.copy()

    with (
        patch(
            "pipeline.search.retrieve._REFERENCE_SPATIAL_CANDIDATE_LIMIT",
            1,
        ),
        patch(
            "pipeline.search.retrieve.embed_spatial_images",
            side_effect=[
                (global_query, query_grid),
                (global_query, opposite_grid),
            ],
        ),
    ):
        candidates = _reference_frame_candidates(
            Image.new("RGB", (64, 36), "navy"),
            db,
            config,
            (),
            candidate_limit=2,
        )

    assert [row["unit_id"] for row in candidates] == [
        "shortlist",
        "backfill",
    ]
    assert candidates[0]["_reference_score"] < candidates[1]["_reference_score"]
    assert candidates[0]["_spatial_score"] is not None
    assert candidates[1]["_spatial_score"] is None


def test_search_by_image_reranks_unique_shots_not_duplicate_frames(
    tmp_path: Path,
    config: Config,
) -> None:
    """Multiple global hits from one shot consume one spatial-rerank slot."""
    from pipeline.search.retrieve import search_by_image

    paths = [
        tmp_path / "first_0.webp",
        tmp_path / "first_1.webp",
        tmp_path / "second_0.webp",
    ]
    for path in paths:
        Image.new("RGB", (64, 36), "navy").save(path)

    first = _make_unit_row("first", "film_one")
    second = _make_unit_row("second", "film_one")
    frame_rows = [
        {
            "frame_id": frame_id,
            "unit_id": unit_id,
            "shot_id": unit_id,
            "film_id": "film_one",
            "frame_index": frame_index,
            "timestamp": timestamp,
            "path": str(path),
            "_distance": distance,
        }
        for frame_id, unit_id, frame_index, timestamp, path, distance in (
            ("first_0", "first", 0, 10.0, paths[0], 0.05),
            ("first_1", "first", 1, 11.0, paths[1], 0.06),
            ("second_0", "second", 0, 130.0, paths[2], 0.10),
        )
    ]
    frame_query = _make_query_chain(frame_rows)
    frames = MagicMock()
    frames.search.return_value = frame_query
    _mark_frames_as_current_profile(frames, len(frame_rows))
    units = MagicMock()
    units.search.return_value = _make_query_chain([first, second])
    db = MagicMock()
    db.list_tables.return_value.tables = ["frames", "units"]
    db.open_table.side_effect = lambda name: {
        "frames": frames,
        "units": units,
    }[name]

    global_query = np.zeros((1, VEC_DIM), dtype=np.float32)
    global_query[0, 0] = 1.0
    grid = np.zeros((1, 6, 6, 2), dtype=np.float32)
    grid[..., 0] = 1.0
    spatial_embed = MagicMock(
        side_effect=[
            (global_query, grid),
            (
                np.repeat(global_query, 2, axis=0),
                np.repeat(grid, 2, axis=0),
            ),
        ]
    )

    with patch(
        "pipeline.search.retrieve.embed_spatial_images",
        spatial_embed,
    ):
        results = search_by_image(
            Image.new("RGB", (64, 36), "navy"),
            db,
            config,
        )

    assert [result["unit_id"] for result in results] == ["first", "second"]
    assert len(spatial_embed.call_args_list[1].args[0]) == 2
    frame_query.limit.assert_called_once_with(
        config.retrieval.candidate_limit * 3
    )


def test_search_by_image_excludes_source_unit(
    tmp_path: Path,
    config: Config,
) -> None:
    """The in-app Composition action does not return its source shot."""
    from pipeline.search.retrieve import search_by_image

    paths = [tmp_path / "source.webp", tmp_path / "other.webp"]
    for path in paths:
        Image.new("RGB", (64, 36), "black").save(path)
    source = _make_unit_row("source", "film_one")
    other = _make_unit_row("other", "film_one")
    frame_rows = [
        {
            "frame_id": f"{unit_id}_0",
            "unit_id": unit_id,
            "shot_id": unit_id,
            "film_id": "film_one",
            "frame_index": 0,
            "timestamp": timestamp,
            "path": str(path),
            "_distance": distance,
        }
        for unit_id, timestamp, path, distance in (
            ("source", 10.0, paths[0], 0.0),
            ("other", 60.0, paths[1], 0.1),
        )
    ]
    frames = MagicMock()
    frames.search.return_value = _make_query_chain(frame_rows)
    _mark_frames_as_current_profile(frames, len(frame_rows))
    units = MagicMock()
    units.search.return_value = _make_query_chain([source, other])
    db = MagicMock()
    db.list_tables.return_value.tables = ["frames", "units"]
    db.open_table.side_effect = lambda name: {
        "frames": frames,
        "units": units,
    }[name]
    grid = np.zeros((1, 6, 6, 2), dtype=np.float32)
    grid[..., 0] = 1.0
    candidate_grids = np.repeat(grid, 2, axis=0)
    global_query = np.zeros((1, VEC_DIM), dtype=np.float32)
    global_query[0, 0] = 1.0

    with patch(
        "pipeline.search.retrieve.embed_spatial_images",
        side_effect=[
            (global_query, grid),
            (np.repeat(global_query, 2, axis=0), candidate_grids),
        ],
    ):
        results = search_by_image(
            Image.new("RGB", (64, 36), "black"),
            db,
            config,
            exclude_unit_id="source",
        )

    assert [result["unit_id"] for result in results] == ["other"]


def test_reference_image_temporal_diversity_keeps_other_moments() -> None:
    """One nearby match is allowed, but it cannot fill the result page."""
    from pipeline.search.retrieve import _is_reference_temporal_duplicate

    selected = [{"film_id": "film_one", "timestamp": 100.0}]

    assert _is_reference_temporal_duplicate(
        {"film_id": "film_one", "timestamp": 180.0},
        selected,
    )
    assert not _is_reference_temporal_duplicate(
        {"film_id": "film_one", "timestamp": 200.1},
        selected,
    )
    assert not _is_reference_temporal_duplicate(
        {"film_id": "film_two", "timestamp": 120.0},
        selected,
    )


def test_reference_image_temporal_diversity_backfills_adjacent_matches(
    tmp_path: Path,
    config: Config,
) -> None:
    """Strong neighboring match cuts remain eligible when diversity underfills."""
    from pipeline.search.retrieve import search_by_image

    paths = [tmp_path / f"near_{index}.webp" for index in range(3)]
    for path in paths:
        Image.new("RGB", (64, 36), "black").save(path)
    units_rows = [
        _make_unit_row(f"near_{index}", "film_one")
        for index in range(3)
    ]
    frame_rows = [
        {
            "frame_id": f"near_{index}_0",
            "unit_id": f"near_{index}",
            "shot_id": f"near_{index}",
            "film_id": "film_one",
            "frame_index": 0,
            "timestamp": 10.0 + index * 10.0,
            "path": str(paths[index]),
            "_distance": 0.05 + index * 0.01,
        }
        for index in range(3)
    ]
    frames = MagicMock()
    frames.search.return_value = _make_query_chain(frame_rows)
    _mark_frames_as_current_profile(frames, len(frame_rows))
    units = MagicMock()
    units.search.return_value = _make_query_chain(units_rows)
    db = MagicMock()
    db.list_tables.return_value.tables = ["frames", "units"]
    db.open_table.side_effect = lambda name: {
        "frames": frames,
        "units": units,
    }[name]

    global_query = np.zeros((1, VEC_DIM), dtype=np.float32)
    global_query[0, 0] = 1.0
    grid = np.zeros((1, 6, 6, 2), dtype=np.float32)
    grid[..., 0] = 1.0
    with patch(
        "pipeline.search.retrieve.embed_spatial_images",
        side_effect=[
            (global_query, grid),
            (
                np.repeat(global_query, 3, axis=0),
                np.repeat(grid, 3, axis=0),
            ),
        ],
    ):
        results = search_by_image(
            Image.new("RGB", (64, 36), "black"),
            db,
            config,
        )

    assert [result["unit_id"] for result in results] == [
        "near_0",
        "near_1",
        "near_2",
    ]


def test_api_search_returns_results_dict(config: Config) -> None:
    """GET /search?q=rain returns {"results": [...]} with expected keys."""
    from fastapi.testclient import TestClient

    rows = [_make_unit_row()]
    mock_db = _make_search_mock_db(rows)
    fake_vec = _fake_vec()

    with (
        patch("pipeline.api.main.load_config", return_value=config),
        patch("pipeline.api.main.open_db", return_value=mock_db),
        patch("pipeline.search.retrieve.embed_text", return_value=fake_vec),
    ):
        import pipeline.api.main as api_mod  # noqa: PLC0415
        with TestClient(api_mod.app) as client:
            response = client.get("/search?q=rain")

    assert response.status_code == 200
    data = response.json()
    assert "results" in data
    assert data["display_batch_size"] == 12
    assert isinstance(data["results"], list)
    assert len(data["results"]) > 0
    result = data["results"][0]
    for key in (
        "unit_id",
        "film_id",
        "t_start",
        "t_end",
        "caption",
        "keyframe_url",
        "keyframe_index",
        "preview_url",
    ):
        assert key in result, f"API result missing key: {key}"


def test_api_image_search_accepts_raw_image_and_forwards_scope(
    config: Config,
) -> None:
    """POST /search/image decodes one still and preserves repeated filters."""
    from fastapi.testclient import TestClient
    from io import BytesIO

    buffer = BytesIO()
    Image.new("RGB", (32, 18), "red").save(buffer, format="PNG")
    mock_db = MagicMock()

    with (
        patch("pipeline.api.main.load_config", return_value=config),
        patch("pipeline.api.main.open_db", return_value=mock_db),
        patch(
            "pipeline.api.main._search_by_image",
            return_value=[],
        ) as image_search,
    ):
        import pipeline.api.main as api_mod  # noqa: PLC0415
        with TestClient(api_mod.app) as client:
            response = client.post(
                "/search/image",
                params=[
                    ("film_id", "film_one"),
                    ("film_id", "film_two"),
                    ("exclude_unit_id", "source"),
                    ("exclude_film_id", "source_film"),
                    ("q", "neon rain"),
                ],
                content=buffer.getvalue(),
                headers={"Content-Type": "image/png"},
            )

    assert response.status_code == 200
    assert response.json() == {"results": [], "display_batch_size": 12}
    args = image_search.call_args.args
    assert isinstance(args[0], Image.Image)
    assert args[0].mode == "RGB"
    assert args[0].size == (32, 18)
    assert args[1:] == (mock_db, config)
    assert image_search.call_args.kwargs == {
        "film_ids": ["film_one", "film_two"],
        "exclude_unit_id": "source",
        "exclude_film_id": "source_film",
        "result_limit": 48,
        "text_query": "neon rain",
    }


@pytest.mark.parametrize(
    ("content", "content_type", "expected_status"),
    [
        (b"", "image/png", 400),
        (b"not an image", "image/png", 400),
        (b"not an image", "text/plain", 415),
    ],
)
def test_api_image_search_rejects_bad_payloads(
    content: bytes,
    content_type: str,
    expected_status: int,
    config: Config,
) -> None:
    from fastapi.testclient import TestClient

    with (
        patch("pipeline.api.main.load_config", return_value=config),
        patch("pipeline.api.main.open_db", return_value=MagicMock()),
    ):
        import pipeline.api.main as api_mod  # noqa: PLC0415
        with TestClient(api_mod.app) as client:
            response = client.post(
                "/search/image",
                content=content,
                headers={"Content-Type": content_type},
            )

    assert response.status_code == expected_status


def test_api_image_search_rejects_oversized_payload(config: Config) -> None:
    from fastapi.testclient import TestClient

    with (
        patch("pipeline.api.main.load_config", return_value=config),
        patch("pipeline.api.main.open_db", return_value=MagicMock()),
        patch("pipeline.api.main._MAX_REFERENCE_IMAGE_BYTES", 4),
    ):
        import pipeline.api.main as api_mod  # noqa: PLC0415
        with TestClient(api_mod.app) as client:
            response = client.post(
                "/search/image",
                content=b"12345",
                headers={"Content-Type": "image/png"},
            )

    assert response.status_code == 413


def test_api_image_search_rejects_text_constraint_over_500_chars(
    config: Config,
) -> None:
    from fastapi.testclient import TestClient
    from io import BytesIO

    buffer = BytesIO()
    Image.new("RGB", (32, 18), "red").save(buffer, format="PNG")

    with (
        patch("pipeline.api.main.load_config", return_value=config),
        patch("pipeline.api.main.open_db", return_value=MagicMock()),
        patch("pipeline.api.main._search_by_image") as image_search,
    ):
        import pipeline.api.main as api_mod  # noqa: PLC0415
        with TestClient(api_mod.app) as client:
            response = client.post(
                "/search/image",
                params={"q": "x" * 501},
                content=buffer.getvalue(),
                headers={"Content-Type": "image/png"},
            )

    assert response.status_code == 422
    image_search.assert_not_called()


def test_api_image_search_stream_cap_does_not_require_content_length(
    config: Config,
) -> None:
    """The byte cap is enforced while consuming a chunked request body."""
    from fastapi.testclient import TestClient

    def chunks():
        yield b"123"
        yield b"45"

    with (
        patch("pipeline.api.main.load_config", return_value=config),
        patch("pipeline.api.main.open_db", return_value=MagicMock()),
        patch("pipeline.api.main._MAX_REFERENCE_IMAGE_BYTES", 4),
    ):
        import pipeline.api.main as api_mod  # noqa: PLC0415
        with TestClient(api_mod.app) as client:
            response = client.post(
                "/search/image",
                content=chunks(),
                headers={"Content-Type": "image/png"},
            )

    assert response.status_code == 413


def test_api_image_search_rejects_disguised_unsupported_format(
    config: Config,
) -> None:
    """Declared MIME type cannot make a GIF pass the JPEG/PNG/WebP allowlist."""
    from fastapi.testclient import TestClient
    from io import BytesIO

    buffer = BytesIO()
    Image.new("RGB", (8, 8), "red").save(buffer, format="GIF")
    with (
        patch("pipeline.api.main.load_config", return_value=config),
        patch("pipeline.api.main.open_db", return_value=MagicMock()),
    ):
        import pipeline.api.main as api_mod  # noqa: PLC0415
        with TestClient(api_mod.app) as client:
            response = client.post(
                "/search/image",
                content=buffer.getvalue(),
                headers={"Content-Type": "image/png"},
            )

    assert response.status_code == 415


def test_api_search_forwards_repeated_film_scope(config: Config) -> None:
    """Repeated film_id parameters become one explicit backend search scope."""
    from fastapi.testclient import TestClient

    mock_db = MagicMock()
    with (
        patch("pipeline.api.main.load_config", return_value=config),
        patch("pipeline.api.main.open_db", return_value=mock_db),
        patch("pipeline.api.main._search", return_value=[]) as scoped_search,
    ):
        import pipeline.api.main as api_mod  # noqa: PLC0415
        with TestClient(api_mod.app) as client:
            response = client.get(
                "/search",
                params=[
                    ("q", "rain"),
                    ("film_id", "film_one"),
                    ("film_id", "film_two"),
                ],
            )

    assert response.status_code == 200
    scoped_search.assert_called_once_with(
        "rain",
        mock_db,
        config,
        film_ids=["film_one", "film_two"],
        result_limit=48,
    )


def test_api_search_attaches_human_readable_film_title(config: Config) -> None:
    """Search responses join display titles without changing retrieval rows."""
    from fastapi.testclient import TestClient

    result = {
        "unit_id": "unit_one",
        "film_id": "film_one",
        "t_start": 10.0,
        "t_end": 12.0,
        "caption": "A room at night",
        "keyframe_url": "/media/keyframe/unit_one/0",
        "preview_url": "/media/preview/unit_one",
    }
    films = MagicMock()
    films.version = 7
    films.search.return_value = _make_query_chain(
        [{"film_id": "film_one", "title": "Fallen Angels"}]
    )
    mock_db = MagicMock()
    mock_db.list_tables.return_value.tables = ["films"]
    mock_db.open_table.return_value = films

    with (
        patch("pipeline.api.main.load_config", return_value=config),
        patch("pipeline.api.main.open_db", return_value=mock_db),
        patch("pipeline.api.main._search", return_value=[result]),
    ):
        import pipeline.api.main as api_mod  # noqa: PLC0415
        with TestClient(api_mod.app) as client:
            response = client.get("/search?q=night")

    assert response.status_code == 200
    assert response.json()["results"][0]["film_title"] == "Fallen Angels"


def test_api_search_survives_optional_film_title_lookup_failure(
    config: Config,
) -> None:
    """A display-metadata failure cannot discard valid retrieval results."""
    from fastapi.testclient import TestClient

    result = {
        "unit_id": "unit_one",
        "film_id": "film_one",
        "t_start": 10.0,
        "t_end": 12.0,
        "caption": "A room at night",
        "keyframe_url": "/media/keyframe/unit_one/0",
        "preview_url": "/media/preview/unit_one",
    }
    films = MagicMock()
    films.version = 7
    films.search.side_effect = RuntimeError("metadata temporarily unavailable")
    mock_db = MagicMock()
    mock_db.list_tables.return_value.tables = ["films"]
    mock_db.open_table.return_value = films

    with (
        patch("pipeline.api.main.load_config", return_value=config),
        patch("pipeline.api.main.open_db", return_value=mock_db),
        patch("pipeline.api.main._search", return_value=[result]),
    ):
        import pipeline.api.main as api_mod  # noqa: PLC0415
        with TestClient(api_mod.app) as client:
            response = client.get("/search?q=night")

    assert response.status_code == 200
    assert response.json() == {
        "results": [result],
        "display_batch_size": 12,
    }


def test_api_search_rejects_unbounded_query_text(config: Config) -> None:
    """Pasted documents are rejected before model or database search work."""
    from fastapi.testclient import TestClient

    mock_db = MagicMock()
    mock_db.list_tables.return_value.tables = []
    with (
        patch("pipeline.api.main.load_config", return_value=config),
        patch("pipeline.api.main.open_db", return_value=mock_db),
        patch("pipeline.api.main._search", return_value=[]) as search_mock,
    ):
        import pipeline.api.main as api_mod  # noqa: PLC0415
        with TestClient(api_mod.app) as client:
            response = client.get("/search", params={"q": "x" * 501})

    assert response.status_code == 422
    search_mock.assert_not_called()


def test_api_unit_endpoint_returns_row(config: Config) -> None:
    """GET /unit/{unit_id} returns the matching unit row."""
    from fastapi.testclient import TestClient

    row = _make_unit_row()
    mock_db = _make_filter_mock_db([row])

    with (
        patch("pipeline.api.main.load_config", return_value=config),
        patch("pipeline.api.main.open_db", return_value=mock_db),
    ):
        import pipeline.api.main as api_mod  # noqa: PLC0415
        with TestClient(api_mod.app) as client:
            response = client.get(f"/unit/{row['unit_id']}")

    assert response.status_code == 200
    data = response.json()
    assert data["unit_id"] == row["unit_id"]
    assert data["film_id"] == row["film_id"]


def test_api_unit_endpoint_404_when_not_found(config: Config) -> None:
    """GET /unit/{unit_id} returns 404 when unit does not exist."""
    from fastapi.testclient import TestClient

    mock_db = _make_filter_mock_db([])  # empty result

    with (
        patch("pipeline.api.main.load_config", return_value=config),
        patch("pipeline.api.main.open_db", return_value=mock_db),
    ):
        import pipeline.api.main as api_mod  # noqa: PLC0415
        with TestClient(api_mod.app, raise_server_exceptions=False) as client:
            response = client.get("/unit/nonexistent_unit")

    assert response.status_code == 404


def test_api_keyframe_returns_file(tmp_path: Path, config: Config) -> None:
    """GET /media/keyframe/{shot_id}/{n} returns 200 when file exists."""
    from fastapi.testclient import TestClient

    film_id = MEDIA_FILM_ID
    shot_id = MEDIA_SHOT_ID
    keyframe_dir = config.paths.assets_dir / film_id / "keyframes"
    keyframe_dir.mkdir(parents=True, exist_ok=True)
    (keyframe_dir / f"{shot_id}_0.webp").write_bytes(b"RIFF fake webp")

    mock_db = _make_filter_mock_db([
        _make_unit_row(shot_id=shot_id, film_id=film_id)
    ])

    with (
        patch("pipeline.api.main.load_config", return_value=config),
        patch("pipeline.api.main.open_db", return_value=mock_db),
    ):
        import pipeline.api.main as api_mod  # noqa: PLC0415
        with TestClient(api_mod.app) as client:
            response = client.get(f"/media/keyframe/{shot_id}/0")

    assert response.status_code == 200


def test_api_keyframe_404_when_missing(config: Config) -> None:
    """GET /media/keyframe/{shot_id}/{n} returns 404 when file is absent."""
    from fastapi.testclient import TestClient

    shot_id = MEDIA_SHOT_ID
    mock_db = _make_filter_mock_db([
        _make_unit_row(shot_id=shot_id, film_id=MEDIA_FILM_ID)
    ])

    with (
        patch("pipeline.api.main.load_config", return_value=config),
        patch("pipeline.api.main.open_db", return_value=mock_db),
    ):
        import pipeline.api.main as api_mod  # noqa: PLC0415
        with TestClient(api_mod.app, raise_server_exceptions=False) as client:
            response = client.get("/media/keyframe/no_such_shot/0")

    assert response.status_code == 404


@pytest.mark.parametrize("n", [-1, 1])
def test_api_keyframe_404_when_index_out_of_range(
    config: Config,
    n: int,
) -> None:
    """Only keyframe indexes recorded on the unit may be served."""
    from fastapi.testclient import TestClient

    film_id = MEDIA_FILM_ID
    shot_id = MEDIA_SHOT_ID
    keyframe_dir = config.paths.assets_dir / film_id / "keyframes"
    keyframe_dir.mkdir(parents=True, exist_ok=True)
    (keyframe_dir / f"{shot_id}_{n}.webp").write_bytes(b"unindexed keyframe")

    mock_db = _make_filter_mock_db([
        _make_unit_row(shot_id=shot_id, film_id=film_id)
    ])

    with (
        patch("pipeline.api.main.load_config", return_value=config),
        patch("pipeline.api.main.open_db", return_value=mock_db),
    ):
        import pipeline.api.main as api_mod  # noqa: PLC0415
        with TestClient(api_mod.app, raise_server_exceptions=False) as client:
            response = client.get(f"/media/keyframe/{shot_id}/{n}")

    assert response.status_code == 404


def test_api_preview_returns_file(tmp_path: Path, config: Config) -> None:
    """GET /media/preview/{shot_id} returns 200 when file exists."""
    from fastapi.testclient import TestClient

    film_id = MEDIA_FILM_ID
    shot_id = MEDIA_SHOT_ID
    preview_dir = config.paths.assets_dir / film_id / "previews"
    preview_dir.mkdir(parents=True, exist_ok=True)
    (preview_dir / f"{shot_id}.webm").write_bytes(b"fake webm bytes")

    mock_db = _make_filter_mock_db([
        _make_unit_row(shot_id=shot_id, film_id=film_id)
    ])

    with (
        patch("pipeline.api.main.load_config", return_value=config),
        patch("pipeline.api.main.open_db", return_value=mock_db),
    ):
        import pipeline.api.main as api_mod  # noqa: PLC0415
        with TestClient(api_mod.app) as client:
            response = client.get(f"/media/preview/{shot_id}")

    assert response.status_code == 200


def test_api_preview_404_when_missing(config: Config) -> None:
    """GET /media/preview/{shot_id} returns 404 when file is absent."""
    from fastapi.testclient import TestClient

    shot_id = MEDIA_SHOT_ID
    mock_db = _make_filter_mock_db([
        _make_unit_row(shot_id=shot_id, film_id=MEDIA_FILM_ID)
    ])

    with (
        patch("pipeline.api.main.load_config", return_value=config),
        patch("pipeline.api.main.open_db", return_value=mock_db),
    ):
        import pipeline.api.main as api_mod  # noqa: PLC0415
        with TestClient(api_mod.app, raise_server_exceptions=False) as client:
            response = client.get("/media/preview/no_such_shot")

    assert response.status_code == 404


def test_api_preview_rejects_path_outside_assets(
    tmp_path: Path,
    config: Config,
) -> None:
    """A compromised film_id cannot cross into another film's asset tree."""
    from fastapi.testclient import TestClient

    shot_id = MEDIA_SHOT_ID
    other_film_dir = config.paths.assets_dir / OTHER_FILM_ID / "previews"
    other_film_dir.mkdir(parents=True, exist_ok=True)
    (other_film_dir / f"{shot_id}.webm").write_bytes(b"must not be served")

    mock_db = _make_filter_mock_db([
        _make_unit_row(
            shot_id=shot_id,
            film_id=f"{MEDIA_FILM_ID}\\..\\{OTHER_FILM_ID}",
        )
    ])

    with (
        patch("pipeline.api.main.load_config", return_value=config),
        patch("pipeline.api.main.open_db", return_value=mock_db),
    ):
        import pipeline.api.main as api_mod  # noqa: PLC0415
        with TestClient(api_mod.app, raise_server_exceptions=False) as client:
            response = client.get(f"/media/preview/{shot_id}")

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Film mock DB helper
# ---------------------------------------------------------------------------


def _make_film_mock_db(
    rows: list[dict],
    *,
    ready_film_ids: set[str] | None = None,
) -> MagicMock:
    """Mock DB for film lookup:
    open_table("films").search().where(...).to_list()
    """
    film_chain = MagicMock()
    film_chain.select.return_value = film_chain
    film_chain.where.return_value = film_chain
    film_chain.limit.return_value = film_chain
    film_chain.to_list.return_value = rows
    films_table = MagicMock()
    films_table.search.return_value = film_chain

    ready = (
        {str(row["film_id"]) for row in rows}
        if ready_film_ids is None
        else ready_film_ids
    )
    unit_chain = MagicMock()
    unit_chain.select.return_value = unit_chain
    unit_chain.where.return_value = unit_chain
    unit_chain.limit.return_value = unit_chain
    unit_chain.to_list.return_value = [
        {"film_id": film_id} for film_id in sorted(ready)
    ]
    units_table = MagicMock()
    units_table.search.return_value = unit_chain
    units_table.version = 1
    fts_index = MagicMock()
    fts_index.name = "units_searchable_text_fts_v1"
    fts_index.index_type = "FTS"
    fts_index.columns = ["searchable_text"]
    units_table.list_indices.return_value = [fts_index]
    fts_stats = MagicMock()
    fts_stats.num_unindexed_rows = 0
    units_table.index_stats.return_value = fts_stats

    db = MagicMock()
    db.open_table.side_effect = lambda name: (
        films_table if name == "films" else units_table
    )
    db.list_tables.return_value.tables = ["films", "units"]
    return db


def test_api_library_includes_searchable_film_metadata(
    config: Config,
) -> None:
    """Indexed library rows expose the stable ID and display metadata."""
    from fastapi.testclient import TestClient

    config.paths.films_dir.mkdir(parents=True)
    indexed_path = config.paths.films_dir / "fallen-angels.mkv"
    indexed_path.write_bytes(b"film")
    (config.paths.films_dir / "unindexed.mp4").write_bytes(b"new")
    film_row = {
        "film_id": "film_one",
        "title": "Fallen Angels",
        "path": str(indexed_path),
        "duration": 5940.0,
    }
    mock_db = _make_film_mock_db([film_row])

    with (
        patch("pipeline.api.main.load_config", return_value=config),
        patch("pipeline.api.main.open_db", return_value=mock_db),
    ):
        import pipeline.api.main as api_mod  # noqa: PLC0415
        with TestClient(api_mod.app) as client:
            response = client.get("/library")

    assert response.status_code == 200
    by_filename = {row["filename"]: row for row in response.json()}
    indexed = by_filename[indexed_path.name]
    assert indexed["status"] == "indexed"
    assert indexed["film_id"] == "film_one"
    assert indexed["title"] == "Fallen Angels"
    assert indexed["duration"] == 5940.0
    assert by_filename["unindexed.mp4"]["film_id"] is None


def test_api_library_does_not_mark_metadata_only_film_indexed(
    config: Config,
) -> None:
    """A crash before the final unit publication cannot fake readiness."""
    from fastapi.testclient import TestClient

    config.paths.films_dir.mkdir(parents=True)
    source = config.paths.films_dir / "interrupted.mkv"
    source.write_bytes(b"film")
    mock_db = _make_film_mock_db(
        [
            {
                "film_id": "interrupted",
                "title": "Interrupted",
                "path": str(source),
                "duration": 100.0,
            }
        ],
        ready_film_ids=set(),
    )

    with (
        patch("pipeline.api.main.load_config", return_value=config),
        patch("pipeline.api.main.open_db", return_value=mock_db),
    ):
        import pipeline.api.main as api_mod

        with TestClient(api_mod.app) as client:
            response = client.get("/library")

    assert response.status_code == 200
    assert response.json() == [
        {
            "filename": "interrupted.mkv",
            "path": str(source),
            "size_gb": 0.0,
            "status": "not_indexed",
            "film_id": None,
            "title": "interrupted",
            "duration": None,
        }
    ]


def test_api_library_caches_ready_ids_for_unchanged_units_version(
    config: Config,
) -> None:
    """Frequent UI polling does not rescan every representative unit."""
    from fastapi.testclient import TestClient

    config.paths.films_dir.mkdir(parents=True)
    source = config.paths.films_dir / "ready.mkv"
    source.write_bytes(b"film")
    mock_db = _make_film_mock_db(
        [
            {
                "film_id": "ready",
                "title": "Ready",
                "path": str(source),
                "duration": 100.0,
            }
        ]
    )
    units_table = mock_db.open_table("units")
    ready_scan = units_table.search.return_value

    with (
        patch("pipeline.api.main.load_config", return_value=config),
        patch("pipeline.api.main.open_db", return_value=mock_db),
    ):
        import pipeline.api.main as api_mod

        with TestClient(api_mod.app) as client:
            first = client.get("/library")
            second = client.get("/library")

    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    ready_scan.to_list.assert_called_once()


def test_api_library_refreshes_ready_ids_when_units_version_changes(
    config: Config,
) -> None:
    """A completed unit publication invalidates readiness immediately."""
    from fastapi.testclient import TestClient

    config.paths.films_dir.mkdir(parents=True)
    sources = []
    film_rows = []
    for film_id in ("first", "second"):
        source = config.paths.films_dir / f"{film_id}.mkv"
        source.write_bytes(b"film")
        sources.append(source)
        film_rows.append(
            {
                "film_id": film_id,
                "title": film_id.title(),
                "path": str(source),
                "duration": 100.0,
            }
        )
    mock_db = _make_film_mock_db(
        film_rows,
        ready_film_ids={"first"},
    )
    units_table = mock_db.open_table("units")
    ready_scan = units_table.search.return_value

    with (
        patch("pipeline.api.main.load_config", return_value=config),
        patch("pipeline.api.main.open_db", return_value=mock_db),
    ):
        import pipeline.api.main as api_mod

        with TestClient(api_mod.app) as client:
            first = client.get("/library")
            units_table.version = 2
            ready_scan.to_list.return_value = [
                {"film_id": "first"},
                {"film_id": "second"},
            ]
            second = client.get("/library")

    first_status = {row["filename"]: row["status"] for row in first.json()}
    second_status = {row["filename"]: row["status"] for row in second.json()}
    assert first_status == {
        "first.mkv": "indexed",
        "second.mkv": "not_indexed",
    }
    assert second_status == {
        "first.mkv": "indexed",
        "second.mkv": "indexed",
    }
    assert ready_scan.to_list.call_count == 2


def test_api_library_unions_external_indexed_and_source_directory_films(
    config: Config,
) -> None:
    """Indexed sources outside films_dir remain available to search filters."""
    from fastapi.testclient import TestClient

    config.paths.films_dir.mkdir(parents=True)
    unindexed_path = config.paths.films_dir / "waiting-to-ingest.mp4"
    unindexed_path.write_bytes(b"new")
    external_path = config.paths.films_dir.parent / "archive" / "indexed.mkv"
    external_path.parent.mkdir()
    external_path.write_bytes(b"indexed")
    mock_db = _make_film_mock_db([
        {
            "film_id": "external_film",
            "title": "External Film",
            "path": str(external_path),
            "duration": 7200.0,
        },
    ])

    with (
        patch("pipeline.api.main.load_config", return_value=config),
        patch("pipeline.api.main.open_db", return_value=mock_db),
    ):
        import pipeline.api.main as api_mod  # noqa: PLC0415
        with TestClient(api_mod.app) as client:
            response = client.get("/library")

    assert response.status_code == 200
    by_filename = {row["filename"]: row for row in response.json()}
    assert by_filename["indexed.mkv"] == {
        "filename": "indexed.mkv",
        "path": str(external_path),
        "size_gb": 0.0,
        "status": "indexed",
        "film_id": "external_film",
        "title": "External Film",
        "duration": 7200.0,
    }
    assert by_filename["waiting-to-ingest.mp4"]["status"] == "not_indexed"
    assert by_filename["waiting-to-ingest.mp4"]["film_id"] is None


def test_api_library_reports_index_metadata_failure(config: Config) -> None:
    """A DB failure is an explicit retryable error, not a fake empty catalog."""
    from fastapi.testclient import TestClient

    config.paths.films_dir.mkdir(parents=True)
    mock_db = MagicMock()
    mock_db.list_tables.side_effect = RuntimeError("database offline")

    with (
        patch("pipeline.api.main.load_config", return_value=config),
        patch("pipeline.api.main.open_db", return_value=mock_db),
        patch("pipeline.api.main.ensure_search_indexes"),
    ):
        import pipeline.api.main as api_mod  # noqa: PLC0415
        with TestClient(api_mod.app, raise_server_exceptions=False) as client:
            response = client.get("/library")

    assert response.status_code == 503
    assert response.json() == {
        "detail": "Indexed film metadata is temporarily unavailable",
    }


# ---------------------------------------------------------------------------
# _parse_range
# ---------------------------------------------------------------------------


def test_parse_range_normal() -> None:
    """bytes=0-999 on a 1000-byte file returns (0, 999)."""
    from pipeline.api.main import _parse_range

    start, end = _parse_range("bytes=0-999", 1000)
    assert start == 0
    assert end == 999


def test_parse_range_open_end() -> None:
    """bytes=100- returns start=100, end=file_size-1."""
    from pipeline.api.main import _parse_range

    start, end = _parse_range("bytes=100-", 1000)
    assert start == 100
    assert end == 999


def test_parse_range_suffix() -> None:
    """bytes=-500 means last 500 bytes: start=file_size-500, end=file_size-1."""
    from pipeline.api.main import _parse_range

    start, end = _parse_range("bytes=-500", 1000)
    assert start == 500
    assert end == 999


def test_parse_range_invalid_returns_416() -> None:
    """A malformed Range header raises HTTPException(416)."""
    from fastapi import HTTPException

    from pipeline.api.main import _parse_range

    with pytest.raises(HTTPException) as exc_info:
        _parse_range("totally-bogus", 1000)
    assert exc_info.value.status_code == 416


# ---------------------------------------------------------------------------
# GET /video/{film_id}
# ---------------------------------------------------------------------------


def test_api_video_returns_200_with_accept_ranges(tmp_path: Path, config: Config) -> None:
    """GET /video/{film_id} returns 200, Accept-Ranges header, and file content."""
    from fastapi.testclient import TestClient

    video_file = tmp_path / "test.mp4"
    video_file.write_bytes(b"fake video content")

    row = {"film_id": "film_test", "path": str(video_file)}
    mock_db = _make_film_mock_db([row])

    with (
        patch("pipeline.api.main.load_config", return_value=config),
        patch("pipeline.api.main.open_db", return_value=mock_db),
    ):
        import pipeline.api.main as api_mod  # noqa: PLC0415
        with TestClient(api_mod.app) as client:
            response = client.get("/video/film_test")

    assert response.status_code == 200
    assert response.headers.get("Accept-Ranges") == "bytes"
    assert response.content == b"fake video content"


def test_api_video_range_request_returns_206(tmp_path: Path, config: Config) -> None:
    """GET /video/{film_id} with Range header returns 206 and Content-Range."""
    from fastapi.testclient import TestClient

    content = b"0123456789"  # 10 bytes
    video_file = tmp_path / "test.mp4"
    video_file.write_bytes(content)

    row = {"film_id": "film_test", "path": str(video_file)}
    mock_db = _make_film_mock_db([row])

    with (
        patch("pipeline.api.main.load_config", return_value=config),
        patch("pipeline.api.main.open_db", return_value=mock_db),
    ):
        import pipeline.api.main as api_mod  # noqa: PLC0415
        with TestClient(api_mod.app) as client:
            response = client.get("/video/film_test", headers={"Range": "bytes=0-4"})

    assert response.status_code == 206
    assert response.headers.get("Content-Range") == "bytes 0-4/10"
    assert response.content == b"01234"


def test_api_video_film_not_in_db_returns_404(config: Config) -> None:
    """GET /video/{film_id} returns 404 when film is absent from DB."""
    from fastapi.testclient import TestClient

    mock_db = _make_film_mock_db([])

    with (
        patch("pipeline.api.main.load_config", return_value=config),
        patch("pipeline.api.main.open_db", return_value=mock_db),
    ):
        import pipeline.api.main as api_mod  # noqa: PLC0415
        with TestClient(api_mod.app, raise_server_exceptions=False) as client:
            response = client.get("/video/nonexistent_film")

    assert response.status_code == 404


def test_api_video_file_missing_on_disk_returns_404(tmp_path: Path, config: Config) -> None:
    """GET /video/{film_id} returns 404 when the video file doesn't exist on disk."""
    from fastapi.testclient import TestClient

    row = {"film_id": "film_test", "path": str(tmp_path / "missing.mp4")}
    mock_db = _make_film_mock_db([row])

    with (
        patch("pipeline.api.main.load_config", return_value=config),
        patch("pipeline.api.main.open_db", return_value=mock_db),
    ):
        import pipeline.api.main as api_mod  # noqa: PLC0415
        with TestClient(api_mod.app, raise_server_exceptions=False) as client:
            response = client.get("/video/film_test")

    assert response.status_code == 404
