"""Partial cache reuse has source identity and numerical equivalence guarantees."""
from pathlib import Path
import os
from unittest.mock import Mock

import numpy as np
from PIL import Image

from pipeline.index.framing_cache import (
    cache_table_name, canonical_grid, load_partial_grids, read_input,
    resolve_candidate_grids, write_cache_rows,
)
from pipeline.index.writer import create_tables, open_db
from pipeline.tests.test_framing_features import _profile, _frame_row


def setup_cache(config, tmp_path):
    profile = _profile()
    db = open_db(config)
    create_tables(db, vector_dim=1024)
    rows = []
    for index in range(3):
        path = tmp_path / f"{index}.bmp"
        Image.new("RGB", (8, 8), (index * 60, 20, 40)).save(path)
        row = _frame_row(path, frame_id=f"frame-{index}")
        row["unit_id"] = f"unit-{index}"
        row["shot_id"] = row["unit_id"]
        rows.append(row)
    db.open_table("frames").add(rows)
    rng = np.random.default_rng(5)
    grids = rng.normal(size=(3, 6, 6, 4)).astype(np.float32)
    grids /= np.linalg.norm(grids, axis=-1, keepdims=True)
    return db, profile, rows, grids


def test_partial_cache_only_encodes_missing_rows_and_matches_uncached_scores(config, tmp_path):
    db, profile, rows, grids = setup_cache(config, tmp_path)
    assert write_cache_rows(db, profile, [read_input(rows[1])], grids[1:2]) == 1
    encoder = Mock(return_value=(None, grids[[0, 2]]))
    selected, matrix, hits = resolve_candidate_grids(rows, 3, db, config, profile, encoder)
    assert hits == 1
    assert len(encoder.call_args.args[0]) == 2
    assert [row["frame_id"] for row in selected] == [row["frame_id"] for row in rows]
    np.testing.assert_array_equal(matrix, np.stack([canonical_grid(grid, profile) for grid in grids]))


def test_same_size_and_mtime_content_replacement_is_not_a_cache_hit(config, tmp_path):
    db, profile, rows, grids = setup_cache(config, tmp_path)
    before = read_input(rows[0])
    write_cache_rows(db, profile, [before], grids[:1])
    path = Path(rows[0]["path"])
    stat = path.stat()
    Image.new("RGB", (8, 8), (99, 20, 40)).save(path)
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert path.stat().st_size == stat.st_size
    after = read_input(rows[0])
    assert after.sha256 != before.sha256
    assert load_partial_grids(db, profile, [after]) == {}
    assert write_cache_rows(db, profile, [before], grids[:1]) == 0


def test_corrupt_row_does_not_discard_other_valid_rows(config, tmp_path):
    db, profile, rows, grids = setup_cache(config, tmp_path)
    inputs = [read_input(row) for row in rows]
    assert write_cache_rows(db, profile, inputs, grids) == 3
    table = db.open_table(cache_table_name(profile))
    table.update(where="frame_id = 'frame-1'", values={"descriptor_sha256": "0" * 64})
    assert set(load_partial_grids(db, profile, inputs)) == {"frame-0", "frame-2"}


def test_removed_frame_cannot_be_reintroduced_by_late_cache_write(config, tmp_path):
    db, profile, rows, grids = setup_cache(config, tmp_path)
    inputs = [read_input(row) for row in rows]
    db.open_table("frames").delete("frame_id = 'frame-1'")
    assert write_cache_rows(db, profile, inputs, grids) == 2
    assert set(load_partial_grids(db, profile, inputs)) == {"frame-0", "frame-2"}
