"""Tests for resident exact vector search (CPU path; no GPU needed)."""

from __future__ import annotations

import numpy as np
import pyarrow as pa
import pytest

from pipeline.search import resident

_REAL_DEVICE_FOR = resident._device_for      # the autouse fixture pins tests to CPU


class _Table:
    def __init__(self, batch: pa.RecordBatch, version: int = 3):
        self._batch, self.version = batch, version
        self.schema = batch.schema

    def count_rows(self, where=None):
        return self._batch.num_rows

    def to_batches(self, *, columns, filter=None, batch_size=65_536):
        yield self._batch.select(columns)


class _Snapshot:
    is_index_snapshot = True
    uri = "memory://test"

    def __init__(self, tables):
        self._tables = tables

    def open_table(self, name):
        return self._tables[name]


def _vectors(rows):
    return pa.FixedSizeListArray.from_arrays(pa.array(np.asarray(rows, dtype=np.float32).ravel()), 3)


@pytest.fixture(autouse=True)
def _cpu(monkeypatch):
    monkeypatch.setattr(resident, "_device_for", lambda _bytes: __import__("torch").device("cpu"))
    resident._MATRICES.clear()


def test_per_view_top_rows_rank_each_view_independently():
    batch = pa.RecordBatch.from_pydict({
        "feature_id": ["a::caption", "a::story", "b::caption", "b::story"],
        "unit_id": ["a", "a", "b", "b"],
        "film_id": ["f1", "f1", "f2", "f2"],
        "view": ["caption", "story", "caption", "story"],
        "vector": _vectors([[1, 0, 0], [0, 1, 0], [0.9, 0.1, 0], [0.2, 0.9, 0]]),
    })
    db = _Snapshot({"text": _Table(batch)})
    matrix = resident.matrix(db, "text", vector_column="vector", key_column="feature_id", group_column="view")
    ranked = resident.top_rows_by_group(matrix, np.array([0, 1, 0]), ("story", "caption", "ocr"), limit=5)
    assert [matrix.row_keys[row] for row, _ in ranked["story"]] == ["a::story", "b::story"]
    assert ranked["ocr"] == []
    scoped = resident.top_rows_by_group(matrix, np.array([0, 1, 0]), ("story",), film_ids=("f2",), limit=5)
    assert [matrix.row_keys[row] for row, _ in scoped["story"]] == ["b::story"]
    # The same pinned version is reused; a new version reloads.
    assert resident.matrix(db, "text", vector_column="vector", key_column="feature_id", group_column="view") is matrix


def test_top_units_takes_each_shots_best_frame():
    batch = pa.RecordBatch.from_pydict({
        "frame_id": ["a0", "a1", "b0"], "unit_id": ["a", "a", "b"], "film_id": ["f", "f", "f"],
        "frame_index": [0, 1, 0], "timestamp": [1.0, 2.0, 9.0],
        "visual_vec": _vectors([[0, 1, 0], [1, 0, 0], [0.8, 0.6, 0]]),
    })
    db = _Snapshot({"frames": _Table(batch)})
    matrix = resident.matrix(db, "frames", vector_column="visual_vec", key_column="frame_id",
                             extra_columns=("frame_index", "timestamp"))
    units = resident.top_units(matrix, np.array([1, 0, 0]), limit=5)
    assert [(unit, matrix.row_keys[row]) for unit, _score, row in units] == [("a", "a1"), ("b", "b0")]
    assert matrix.row_extra["timestamp"][units[0][2]] == 2.0


def test_a_new_version_releases_the_previous_generation_before_loading(monkeypatch):
    import gc
    import weakref

    batch = pa.RecordBatch.from_pydict({
        "frame_id": ["a0", "b0"], "unit_id": ["a", "b"], "film_id": ["f", "f"],
        "visual_vec": _vectors([[1, 0, 0], [0, 1, 0]]),
    })
    table = _Table(batch, version=3)
    db = _Snapshot({"frames": table})
    previous = weakref.ref(resident.matrix(db, "frames", vector_column="visual_vec", key_column="frame_id"))
    alive_during_load = []
    real_load = resident._load

    def load(*args, **kwargs):
        gc.collect()
        alive_during_load.append(previous() is not None)
        return real_load(*args, **kwargs)

    monkeypatch.setattr(resident, "_load", load)
    table.version = 4
    assert resident.matrix(db, "frames", vector_column="visual_vec", key_column="frame_id").version == 4
    # Otherwise both generations occupy memory at once and the new one is pushed off the GPU.
    assert alive_during_load == [False]


def test_gpu_placement_counts_this_process_reusable_cache(monkeypatch):
    import torch

    gb = 1024 ** 3
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    monkeypatch.setattr(torch.cuda, "mem_get_info", lambda: (1 * gb, 16 * gb))
    monkeypatch.setattr(torch.cuda, "memory_allocated", lambda: 1 * gb)
    monkeypatch.setattr(torch.cuda, "memory_reserved", lambda: 6 * gb)
    # 1 GB free on the device plus 5 GB of this process's own released cache leaves room past the headroom.
    assert _REAL_DEVICE_FOR(1 * gb).type == "cuda"
    monkeypatch.setattr(torch.cuda, "memory_reserved", lambda: 1 * gb)
    assert _REAL_DEVICE_FOR(1 * gb).type == "cpu"


def test_only_pinned_snapshots_are_loaded():
    class Plain:
        def open_table(self, name):
            raise AssertionError("should not be opened")

    assert resident.matrix(Plain(), "frames", vector_column="v", key_column="k") is None
