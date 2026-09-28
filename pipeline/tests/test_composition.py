from dataclasses import replace
import json

import numpy as np
import pytest

from pipeline.index.composition import fit_projection, save_profile, load_profile, project_grids, write_features, publish_coverage, ready_profile
from pipeline.index.framing_cache import read_input
from pipeline.index.snapshot import capture_snapshot
from pipeline.index.search_indexes import physical_index_change
from pipeline.search.composition import balanced_union, independent_candidates, selected_profile, shadow_profile
from pipeline.tests.test_framing_cache import setup_cache


def compact_fixture(config, tmp_path):
    db, source, rows, grids = setup_cache(config, tmp_path)
    source = replace(source, feature_dim=64)
    grids = np.random.default_rng(1).normal(size=(3, 6, 6, 64)).astype(np.float32)
    profile, matrix = fit_projection(grids.reshape(-1, 64), source, "a" * 64, 32)
    save_profile(config, profile, matrix)
    return db, source, rows, grids, profile, matrix


def test_profile_immutable_and_preserves_screen_positions(config, tmp_path):
    _, _, _, grids, profile, matrix = compact_fixture(config, tmp_path)
    loaded, projection = load_profile(config, profile.profile_id)
    assert loaded == profile
    np.testing.assert_array_equal(projection, matrix)
    vectors = project_grids(np.stack([grids[0], grids[0][::-1]]), profile, matrix)
    assert np.dot(vectors[0], vectors[1]) < .5
    path = config.paths.assets_dir / "search-profiles" / profile.profile_id / "projection.npy"
    np.save(path, matrix + .1)
    with pytest.raises(ValueError, match="corrupt"):
        load_profile(config, profile.profile_id)


def test_coverage_pinned_and_preserved_only_for_index_edits(config, tmp_path):
    db, source, rows, grids, profile, matrix = compact_fixture(config, tmp_path)
    inputs = [read_input(row) for row in rows]
    write_features(db, profile, matrix, inputs[:2], grids[:2])
    assert publish_coverage(config, db, profile) is False
    write_features(db, profile, matrix, inputs[2:], grids[2:])
    assert publish_coverage(config, db, profile)
    pinned = capture_snapshot(config, db)
    assert ready_profile(config, pinned, profile.profile_id)
    with physical_index_change(config, db):
        db.open_table("frames").create_scalar_index("film_id")
    assert ready_profile(config, db, profile.profile_id)
    db.open_table("frames").delete("frame_id = 'frame-2'")
    assert ready_profile(config, db, profile.profile_id) is None
    assert ready_profile(config, pinned, profile.profile_id)


def test_independent_composition_hits_and_film_scope(config, tmp_path):
    db, _, rows, grids, profile, matrix = compact_fixture(config, tmp_path)
    write_features(db, profile, matrix, [read_input(row) for row in rows], grids)
    result = independent_candidates(db, profile, matrix, rows[0]["visual_vec"], grids[2],
                                    (rows[0]["film_id"],), limit=2, reserve=0)
    assert "frame-2" in {row["frame_id"] for row in result}
    assert len(result) == 2


def test_balanced_overlap_fill_does_not_collapse_units_early():
    rows = [{"frame_id": str(i), "film_id": str(i // 4), "unit_id": "same"} for i in range(12)]
    result = balanced_union(rows[:8], rows[2:], limit=6, reserve=1)
    assert len({row["frame_id"] for row in result}) == 7
    assert len({row["unit_id"] for row in result}) == 1
    assert result == balanced_union(rows[:8], rows[2:], limit=6, reserve=1)


def test_configured_challenger_needs_review_but_shadow_does_not(config, tmp_path):
    db, _, rows, grids, profile, matrix = compact_fixture(config, tmp_path)
    write_features(db, profile, matrix, [read_input(row) for row in rows], grids)
    publish_coverage(config, db, profile)
    config.retrieval.composition_profile = profile.profile_id
    assert selected_profile(config, db) is None
    with shadow_profile(profile.profile_id):
        assert selected_profile(config, db)[0] == profile
    assert selected_profile(config, db) is None


def test_best_frame_is_selected_only_after_spatial_scoring(config, tmp_path, monkeypatch):
    from pipeline.search.composition import rank_candidates
    db, source, rows, grids, profile, matrix = compact_fixture(config, tmp_path)
    rows[0]["unit_id"] = rows[1]["unit_id"] = "same-scene"
    for row in rows:
        row["_distance"] = .1
    monkeypatch.setattr("pipeline.search.composition.independent_candidates", lambda *a, **kw: rows)
    monkeypatch.setattr("pipeline.search.composition.resolve_candidate_grids", lambda *a, **kw: (rows, grids, 0))
    ranked = rank_candidates(db, config, (profile, matrix), source, np.zeros(1024), grids[0], (),
                             limit=96, reserve=0, encode=None, score=lambda *a: np.array([.1, .9, .3]))
    assert ranked[0]["frame_id"] == "frame-1"
    assert len(ranked) == 2


def test_ann_benchmark_never_changes_production_table(config, tmp_path, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import Mock
    from pipeline.eval.search_benchmark import benchmark_frames
    db, _, rows, _ = setup_cache(config, tmp_path)
    table = db.open_table("frames")
    table.update(values={"visual_vec": [1.] + [0.] * 1023})
    before = table.version
    films = Mock()
    films.search.return_value.select.return_value.limit.return_value.to_list.return_value = [{"film_id": "film-a"}]
    snapshot = SimpleNamespace(versions={"frames": before}, open_table=lambda name: table if name == "frames" else films)
    monkeypatch.setattr("pipeline.eval.search_benchmark.capture_snapshot", lambda *args: snapshot)
    result = benchmark_frames(config, db, ann=True, count=1, repeats=2)
    assert result["promoted"] is False and len(result["cases"]) == 6
    assert db.open_table("frames").version == before
    assert list((config.paths.assets_dir / "search-builds").iterdir()) == []


def test_inactive_profile_retirement_preserves_raw_evidence(config, tmp_path):
    from pipeline.index.search_storage import retire_composition_profile
    db, _, rows, grids, profile, matrix = compact_fixture(config, tmp_path)
    write_features(db, profile, matrix, [read_input(row) for row in rows], grids)
    config.retrieval.composition_profile = profile.profile_id
    with pytest.raises(ValueError, match="Select another"):
        retire_composition_profile(config, profile.profile_id, apply=True)
    config.retrieval.composition_profile = None
    assert retire_composition_profile(config, profile.profile_id, apply=True)["removed_bytes"] > 0
    assert db.open_table("frames").count_rows() == 3
    assert all(read_input(row).data for row in rows)
