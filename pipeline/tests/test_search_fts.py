"""Integration tests for the native LanceDB full-text retrieval path."""

from __future__ import annotations

from pathlib import Path

import lancedb
from lancedb.expr import col, lit

from pipeline.index.writer import UNITS_FTS_FIELD, UNITS_FTS_INDEX
from pipeline.search.retrieve import _native_lexical_ranking


def _row(index: int, text: str) -> dict[str, object]:
    unit_id = f"film_{index:05d}"
    return {
        "unit_id": unit_id,
        "film_id": "film",
        "shot_id": unit_id,
        "t_start": float(index),
        "t_end": float(index + 1),
        "caption": text,
        "searchable_text": text,
        "dialogue": "[]",
        "keyframe_paths": "[]",
        "is_representative": True,
    }


def test_native_fts_finds_a_row_beyond_the_old_scan_limit(
    tmp_path: Path,
) -> None:
    """Lexical recall is independent of insertion position past row 10,000."""
    db = lancedb.connect(str(tmp_path / "db"))
    rows = [_row(index, "ordinary corridor") for index in range(10_025)]
    target = rows[-1]
    target["caption"] = "A singular heliotrope doorway"
    target["searchable_text"] = "A singular heliotrope doorway"
    table = db.create_table("units", data=rows)
    table.create_fts_index(
        UNITS_FTS_FIELD,
        name=UNITS_FTS_INDEX,
        replace=False,
        base_tokenizer="simple",
        language="English",
        max_token_length=40,
        lower_case=True,
        stem=True,
        remove_stop_words=True,
        ascii_folding=True,
        with_position=True,
    )

    ranked = _native_lexical_ranking(
        "heliotrope doorway",
        table,
        col("is_representative") == lit(True),
        (),
    )

    assert [row["unit_id"] for row, _score in ranked] == [target["unit_id"]]


def test_native_fts_uses_stemming_and_requires_compound_evidence(
    tmp_path: Path,
) -> None:
    """Native morphology works without admitting incidental one-term hits."""
    db = lancedb.connect(str(tmp_path / "db"))
    table = db.create_table(
        "units",
        data=[
            _row(1, "A woman running through rain"),
            _row(2, "A woman seated inside"),
            _row(3, "A man runs outside"),
        ],
    )
    table.create_fts_index(
        UNITS_FTS_FIELD,
        name=UNITS_FTS_INDEX,
        language="English",
        stem=True,
        with_position=True,
    )

    ranked = _native_lexical_ranking(
        "woman runs",
        table,
        col("is_representative") == lit(True),
        (),
    )

    assert [row["unit_id"] for row, _score in ranked] == ["film_00001"]
