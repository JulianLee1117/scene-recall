"""Library recall stays bounded, scoped and separate from exact-frame evidence."""
from copy import deepcopy
import json
from types import SimpleNamespace

import lancedb
import numpy as np
import pyarrow as pa
import pytest

from pipeline.lab.media import JobCancelled
from pipeline.matching import library_retrieval as library


def unit(identity, film="b", start=0., end=10.):
    return {"unit_id": identity, "film_id": film, "t_start": start, "t_end": end,
            "caption": identity, "is_representative": True}


@pytest.fixture
def catalog(tmp_path):
    config = SimpleNamespace(models=SimpleNamespace(visual_encoder="pe_core_l14"))
    film = tmp_path / "film.mp4"
    film.write_bytes(b"retained source")
    image = tmp_path / "frame.webp"
    image.write_bytes(b"retained frame")
    db = lancedb.connect(str(tmp_path / "db"))
    db.create_table("films", data=[{"film_id": fid, "title": f"Film {fid}", "path": str(film)} for fid in "abc"])
    source = unit("reference", "a")
    db.create_table("units", data=[source, unit("first"), unit("second", "c"), unit("same", "a", 20., 30.)])

    def frame(identity, uid, film_id="b", timestamp=1., vector=(1., 0., 0.), **updates):
        vector = np.asarray(vector, dtype=np.float32)
        vector /= np.linalg.norm(vector)
        return {"frame_id": identity, "unit_id": uid, "film_id": film_id, "timestamp": timestamp,
                "timestamp_source": "legacy_seek", "path": str(image), "visual_encoder": "pe_core_l14",
                "visual_vec": vector.tolist(), "is_representative": True, **updates}

    rows = [frame("ref-early", "reference", "a", 1., (1., 0., 0.)),
            frame("ref-late", "reference", "a", 8., (0., 1., 0.)),
            frame("first-1", "first", timestamp=1., vector=(.99, .1, 0.)),
            frame("first-2", "first", timestamp=3., vector=(.95, .2, 0.)),
            frame("second-1", "second", "c", vector=(.1, .99, 0.)),
            frame("same-1", "same", "a", 21., (.5, .5, 0.))]
    schema = pa.schema([pa.field(key, pa.list_(pa.float32(), 3) if key == "visual_vec"
                                 else pa.bool_() if key == "is_representative"
                                 else pa.float64() if key == "timestamp" else pa.string()) for key in rows[0]])
    db.create_table("frames", data=pa.Table.from_pylist(rows, schema=schema))
    return config, db, source, frame


def options(**updates):
    return {"film_ids": [], "include_source_film": True, "min_incoming_seconds": 1., **updates}


def retrieve(catalog, time=1., **updates):
    config, db, source, _ = catalog
    return library.retrieve(config, db, source, time, options(**updates), library.library_identity(config, db))


def test_snapshot_is_json_safe_and_covers_available_library_not_prepared_cohort(catalog):
    config, db, _, _ = catalog
    identity = library.library_identity(config, db)
    assert json.loads(json.dumps(identity)) == identity
    assert identity["film_count"] == 3 and identity["frame_count"] == 6 and identity["unit_count"] == 4
    assert {row["film_id"] for row in identity["films"]} == set("abc")
    assert set(identity["tables"]) == {"films", "frames", "units"}


def test_nearest_indexed_source_supplies_only_a_proposal_and_unique_best_shot(catalog):
    early = retrieve(catalog, time=1.1)
    late = retrieve(catalog, time=7.7)
    assert early[0]["unit_id"] == "first"
    assert late[0]["unit_id"] == "second"
    assert early[0]["reference_frame"]["time"] == 1.
    assert late[0]["reference_frame"]["time"] == 8.
    assert len({row["unit_id"] for row in early}) == len(early) == 3
    assert early[0]["frame_id"] == "first-1"
    assert early[0]["film_title"] == "Film b"
    assert not {"score", "cues", "candidate_frame_pts"}.intersection(early[0])


def test_scope_applies_before_retrieval_and_unknown_films_fail(catalog):
    assert {row["film_id"] for row in retrieve(catalog, film_ids=["c"])} == {"c"}
    assert all(row["film_id"] != "a" for row in retrieve(catalog, include_source_film=False))
    assert retrieve(catalog, film_ids=["a"], include_source_film=False) == []
    with pytest.raises(ValueError, match="outside the available"):
        retrieve(catalog, film_ids=["b", "unknown"])


@pytest.mark.parametrize("timestamp", [-1., 10., float("nan"), float("inf")])
def test_source_moment_must_be_inside_authoritative_unit(catalog, timestamp):
    with pytest.raises(ValueError, match="reference moment"):
        retrieve(catalog, time=timestamp)


def test_source_bounds_film_and_reference_identity_cannot_be_forged(catalog):
    config, db, source, _ = catalog
    pinned = library.library_identity(config, db)
    with pytest.raises(ValueError, match="reference shot changed"):
        library.retrieve(config, db, {**source, "t_end": 100.}, 20., options(), pinned)
    with pytest.raises(ValueError, match="reference shot changed"):
        library.retrieve(config, db, {**source, "film_id": "b"}, 1., options(), pinned)
    with pytest.raises(ValueError, match="reference selection"):
        library.retrieve(config, db, source, 1., options(reference={"unit_id": "first"}), pinned)


def test_mixed_visual_spaces_rejected_even_outside_requested_scope(catalog):
    config, db, _, _ = catalog
    db.open_table("frames").update(where="frame_id = 'second-1'", values={"visual_encoder": "different"})
    with pytest.raises(ValueError, match="not use configured encoder"):
        library.library_identity(config, db)


def test_queued_snapshot_rejects_changed_index_or_encoder(catalog):
    config, db, source, _ = catalog
    pinned = library.library_identity(config, db)
    db.open_table("units").update(where="unit_id = 'first'", values={"caption": "changed"})
    with pytest.raises(ValueError, match="changed after this search was queued"):
        library.retrieve(config, db, source, 1., options(), pinned)
    pinned = library.library_identity(config, db)
    corrupted = deepcopy(pinned)
    corrupted["visual_encoder"] = "different"
    with pytest.raises(ValueError, match="changed after this search was queued"):
        library.retrieve(config, db, source, 1., options(), corrupted)


def test_index_change_during_candidate_work_never_publishes_partial_results(catalog):
    config, db, source, _ = catalog
    pinned = library.library_identity(config, db)
    calls = 0

    def concurrent_change():
        nonlocal calls
        calls += 1
        if calls == 3:
            db.open_table("units").update(where="unit_id = 'first'", values={"caption": "concurrent publication"})
        return False

    with pytest.raises(ValueError, match="changed after this search was queued"):
        library.retrieve(config, db, source, 1., options(), pinned, concurrent_change)


def test_handles_are_checked_at_seed_and_best_legal_frame_can_replace_illegal_best(catalog):
    _, db, _, _ = catalog
    db.open_table("frames").update(where="frame_id = 'first-1'", values={"timestamp": 9.8})
    rows = retrieve(catalog, film_ids=["b"])
    assert len(rows) == 1 and rows[0]["frame_id"] == "first-2"
    assert retrieve(catalog, film_ids=["b"], min_incoming_seconds=9.) == []


def test_non_thumbnail_retained_frames_remain_eligible(catalog):
    _, db, _, _ = catalog
    db.open_table("frames").update(where="frame_id = 'first-1'", values={"is_representative": False})
    assert retrieve(catalog, film_ids=["b"])[0]["frame_id"] == "first-1"


def test_overlapping_derived_same_film_shot_and_missing_images_are_excluded(catalog, tmp_path):
    _, db, _, frame = catalog
    db.open_table("units").add([unit("overlap", "a", 8., 15.)])
    db.open_table("frames").add([frame("overlap-1", "overlap", "a", 12.)])
    db.open_table("frames").update(where="unit_id = 'first'", values={"path": str(tmp_path / "missing.webp")})
    assert {row["unit_id"] for row in retrieve(catalog)} == {"second", "same"}


def test_unpublished_missing_source_and_nonrepresentative_candidates_are_excluded(catalog, tmp_path):
    config, db, _, _ = catalog
    db.open_table("units").update(where="unit_id = 'second'", values={"is_representative": False})
    db.open_table("films").update(where="film_id = 'b'", values={"path": str(tmp_path / "missing.mp4")})
    assert {film["film_id"] for film in library.library_identity(config, db)["films"]} == {"a"}
    assert {row["unit_id"] for row in retrieve(catalog)} == {"same"}


def test_cancellation_never_returns_results(catalog):
    config, db, source, _ = catalog
    with pytest.raises(JobCancelled):
        library.retrieve(config, db, source, 1., options(), library.library_identity(config, db), lambda: True)


def test_query_never_deepens_beyond_six_hundred_frames(catalog):
    config, db, source, frame = catalog
    db.open_table("units").add([unit(f"extra-{i:04d}") for i in range(601)])
    db.open_table("frames").add([frame(f"extra-frame-{i:04d}", f"extra-{i:04d}", timestamp=9.9 if i < 600 else 1.,
                                       vector=(1., (i + 1) / 10000., 0.)) for i in range(601)])
    # The valid 601st shot and original legal candidates sit beyond the fixed
    # frame projection. Bad handles do not silently expand the work budget.
    assert retrieve(catalog, film_ids=["b"]) == []


def test_output_never_exceeds_two_hundred_unique_shots(catalog):
    _, db, _, frame = catalog
    db.open_table("units").add([unit(f"extra-{i:04d}") for i in range(230)])
    db.open_table("frames").add([frame(f"extra-frame-{i:04d}", f"extra-{i:04d}",
                                       vector=(1., (i + 1) / 10000., 0.)) for i in range(230)])
    rows = retrieve(catalog, film_ids=["b"])
    assert len(rows) == len({row["unit_id"] for row in rows}) == 200
