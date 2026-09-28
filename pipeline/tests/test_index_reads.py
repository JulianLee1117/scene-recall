"""Real scalar indexes must not hide matches after the scan's old limit."""
from types import SimpleNamespace
from unittest.mock import MagicMock

import lancedb
from lancedb.expr import col, lit
import pyarrow as pa
import pytest

from pipeline.index.reads import filtered_rows
from pipeline.index.writer import create_tables


@pytest.fixture
def indexed_library(tmp_path):
    db = lancedb.connect(str(tmp_path / "db"))
    create_tables(db, vector_dim=4)
    film = tmp_path / "film.mp4"
    film.touch()
    db.open_table("films").add([{"film_id": "film", "title": "Film", "path": str(film),
                                 "duration": 8000., "fps": 24.}])
    units = [{"unit_id": f"unit-{i}", "film_id": "film", "shot_id": f"unit-{i}",
              "t_start": i * 10., "t_end": i * 10. + 10., "caption": f"Scene {i}",
              "is_representative": True} for i in range(700)]
    frames = [{"frame_id": f"unit-{i}::frame::{j}", "unit_id": f"unit-{i}", "film_id": "film",
               "frame_index": j, "timestamp": i * 10. + j + 1., "path": str(film)}
              for i in range(700) for j in range(3)]
    db.open_table("units").add(units)
    db.open_table("frames").add(frames)
    for name in ("units", "frames"):
        for column in ("film_id", "unit_id"):
            db.open_table(name).create_scalar_index(column, index_type="BTREE")
    return db


@pytest.mark.parametrize("expression", [False, True])
def test_late_matches_equal_unlimited_filtered_scan(indexed_library, expression):
    table = indexed_library.open_table("units")
    where = ((col("film_id") == lit("film")) & (col("t_start") >= lit(6000.))) if expression else (
        "film_id = 'film' AND t_start >= 6000")
    columns = ["unit_id", "t_start"]
    expected = table.search().where(where).select(columns).limit(None).to_list()
    assert len(expected) == 100
    for limit in (1, 2, 60, 256):
        assert filtered_rows(table, where=where, columns=columns, limit=limit) == expected[:limit]


def test_unindexed_rows_and_missing_matches_remain_visible(indexed_library):
    table = indexed_library.open_table("units")
    table.add([{"unit_id": "new", "film_id": "film", "t_start": 7200., "t_end": 7210.}])
    where = (col("film_id") == lit("film")) & (col("t_start") >= lit(7200.))
    assert filtered_rows(table, where=where, columns=["unit_id"], limit=2) == [{"unit_id": "new"}]
    assert filtered_rows(table, where=where & (col("t_end") < lit(1.)), limit=2) == []


def test_third_frame_bookmark_and_editor_reference_validation(indexed_library):
    from pipeline.api.main import _bookmark_frame_timestamp
    from pipeline.lab.next_scene import _validate_references
    db = indexed_library
    assert _bookmark_frame_timestamp(db, {"unit_id": "unit-650"}, 2) == 6503.
    clip = {"id": "clip", "film_id": "film", "source_start": 6500., "source_end": 6510.}
    reference = {"clip_id": "clip", **{k: clip[k] for k in ("film_id", "source_start", "source_end")},
                 "unit_id": "unit-650", "frame_index": 2, "timestamp": 6503.}
    _validate_references({"resolved_search": {"references": [reference]}}, {"clips": [clip]}, db)


def test_late_editor_caption_handles_and_reference_offer(indexed_library):
    from pipeline.lab.direction_planner import _source_context
    from pipeline.lab.next_scene import _anchor_handles
    from pipeline.lab.search_plan import offered_references
    clip = {"id": "clip", "film_id": "film", "unit_id": "unit-650", "source_start": 6502., "source_end": 6508.}
    assert _source_context(clip, indexed_library)["caption_evidence"]["text"] == "Scene 650"
    scope = {"anchor": clip, "cut_min": 1., "cut_max": 6., "current_cut": 4.}
    assert _anchor_handles(scope, indexed_library)["anchor_offer"]["unit_id"] == "unit-650"
    document = {"clips": [clip], "music_timeline": {"slots": [{"id": "slot", "clip_id": "clip"}]}}
    capabilities = {"facets": [{"facet": "scene", "source_available": True}]}
    offered = offered_references(document, indexed_library, capabilities)
    assert len(offered) == 1 and offered[0]["unit_id"] == "unit-650"


def test_stream_stops_at_bound_and_closes_without_materializing_rest():
    query = MagicMock()
    query.where.return_value = query.select.return_value = query.limit.return_value = query
    table = SimpleNamespace(search=lambda: query)
    read = []
    def batches():
        for start in (0, 3, 6):
            read.append(start)
            yield pa.record_batch({"id": list(range(start, start + 3))})
    reader = MagicMock()
    reader.__iter__.side_effect = batches
    query.to_batches.return_value = reader
    assert filtered_rows(table, where="anything", columns=["id"], limit=4) == [{"id": i} for i in range(4)]
    assert read == [0, 3]
    query.limit.assert_called_once_with(None)
    reader.close.assert_called_once()


def test_invalid_and_zero_limit_do_not_open_a_scan():
    table = MagicMock()
    assert filtered_rows(table, where="anything", limit=0) == []
    for limit in (-1, True, 1.5, None):
        with pytest.raises(ValueError):
            filtered_rows(table, where="anything", limit=limit)
    table.search.assert_not_called()
