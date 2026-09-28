"""Exact semantic scans keep ranked evidence and pinned table generations."""
from types import SimpleNamespace
from unittest.mock import MagicMock

import lancedb
from lancedb.expr import col, lit
import numpy as np
import pyarrow as pa
import pytest

from pipeline.index.snapshot import IndexSnapshot, _ReadTable
from pipeline.search import retrieve


@pytest.fixture
def semantic_table(tmp_path):
    db = lancedb.connect(str(tmp_path / "db"))
    rng = np.random.default_rng(12)
    rows = [dict(feature_id=f"feature-{index:04d}", profile_id="test",
                 film_id=f"film-{index % 3}", unit_id=f"unit-{index}",
                 view=retrieve.TEXT_VIEWS[index % len(retrieve.TEXT_VIEWS)],
                 text=f"Evidence {index}", is_representative=index % 7 != 0,
                 vector=rng.normal(size=4).tolist()) for index in range(180)]
    arrow = pa.Table.from_pylist(rows)
    vector = pa.array([row["vector"] for row in rows], type=pa.list_(pa.float32(), 4))
    arrow = arrow.set_column(arrow.schema.get_field_index("vector"), "vector", vector)
    table = db.create_table("features", arrow)
    table.create_scalar_index("view", index_type="BITMAP")
    table.create_scalar_index("is_representative", index_type="BITMAP")
    table.create_scalar_index("film_id", index_type="BTREE")
    pinned = db.open_table("features")
    pinned.checkout(pinned.version)
    return table, pinned


@pytest.mark.parametrize("views,scope,use_scan", [
    (("caption",), (), True),
    (("mood", "dialogue"), (), True),
    (retrieve.TEXT_VIEWS, (), False),
    (("caption",), ("film-1",), False),
])
def test_scan_matches_existing_evidence_ranks_and_only_routes_broad_view_filters(
        semantic_table, monkeypatch, views, scope, use_scan):
    pytest.importorskip("lance")
    _live, pinned = semantic_table
    snapshot = IndexSnapshot("test", {"features": _ReadTable(pinned)}, {})
    original = SimpleNamespace(open_table=lambda _name: pinned)
    units = [{"unit_id": f"unit-{i}", "film_id": f"film-{i % 3}"} for i in range(180)]
    monkeypatch.setattr(retrieve, "_hydrate_units", lambda *_: units)
    profile = SimpleNamespace(table_name="features")
    vector = np.array([1., 2., 3., 4.], dtype=np.float32)
    expected = retrieve._semantic_text_search_rows(vector, original, None, profile, scope,
                                                  candidate_limit=8, allowed_views=views)
    scan = _ReadTable.scan_vector_rows
    calls = []

    def observed(self, *args, **kwargs):
        calls.append(kwargs)
        return scan(self, *args, **kwargs)

    monkeypatch.setattr(_ReadTable, "scan_vector_rows", observed)
    actual = retrieve._semantic_text_search_rows(vector, snapshot, None, profile, scope,
                                                candidate_limit=8, allowed_views=views)
    assert actual == expected
    assert bool(calls) is use_scan
    assert all(row["_matched_text"]["view"] in views for row in actual)


def test_native_scan_stays_on_pinned_generation(semantic_table):
    pytest.importorskip("lance")
    live, pinned = semantic_table
    vector = np.array([1., 2., 3., 4.], dtype=np.float32)
    live.add([dict(feature_id="new-nearest", profile_id="test", film_id="new-film",
                   unit_id="new-unit", view="caption", text="new", is_representative=True,
                   vector=vector.tolist())])
    rows = _ReadTable(pinned).scan_vector_rows(
        vector, column="vector", columns=retrieve._TEXT_FEATURE_COLUMNS,
        where=(col("view") == lit("caption")) & (col("is_representative") == lit(True)), limit=10)
    assert "new-nearest" not in {row["feature_id"] for row in rows}
    assert pinned.version < live.version


@pytest.mark.parametrize("reason", ["missing_native_dependency", "vector_index"])
def test_native_scan_defers_when_existing_vector_index_or_optional_dependency_unavailable(reason):
    table = MagicMock()
    table.list_indices.return_value = []
    if reason == "vector_index":
        table.list_indices.return_value = [SimpleNamespace(columns=["vector"])]
    else:
        table.to_lance.side_effect = ImportError("Optional dependency absent")
    assert _ReadTable(table).scan_vector_rows(
        np.ones(4), column="vector", columns=["feature_id"],
        where=col("view") == lit("caption"), limit=10) is None
    if reason == "vector_index":
        table.to_lance.assert_not_called()


def test_retrieval_uses_existing_query_when_native_scanner_defers(semantic_table, monkeypatch):
    _live, pinned = semantic_table
    snapshot = IndexSnapshot("test", {"features": _ReadTable(pinned)}, {})
    monkeypatch.setattr(_ReadTable, "scan_vector_rows", lambda *args, **kwargs: None)
    monkeypatch.setattr(retrieve, "_hydrate_units", lambda table, ids, scope:
                        [{"unit_id": identity, "film_id": "film"} for identity in ids])
    rows = retrieve._semantic_text_search_rows(np.ones(4, dtype=np.float32), snapshot,
                                               None, SimpleNamespace(table_name="features"), (),
                                               candidate_limit=5, allowed_views=("mood",))
    assert len(rows) == 5
    assert all(row["_matched_text"]["view"] == "mood" for row in rows)
