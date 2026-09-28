"""Feature maintenance yields between batches, respects cancellation and budgets."""
from dataclasses import asdict
from unittest.mock import patch

import numpy as np
import pytest

from pipeline.index.search_features import preparation_request, prepare_batch
from pipeline.index.search_storage import SearchStorageFull, reserve_search_storage, storage_status
from pipeline.lab.store import LabStore
from pipeline.tests.test_framing_cache import setup_cache


def test_preparation_is_idempotent_and_source_generation_is_frozen(config, tmp_path):
    db, profile, rows, grids = setup_cache(config, tmp_path)
    with patch("pipeline.index.search_features.configured_framing_spatial_profile", return_value=profile):
        options = preparation_request(config, db, "film-a")
        with patch("pipeline.index.search_features.embed_spatial_images", return_value=(None, grids)) as encode:
            first = prepare_batch(config, db, options)
            assert first["written"] == 3 and first["done"]
            second = prepare_batch(config, db, options)
            assert second["written"] == 0 and second["cached"] == 3
            assert encode.call_count == 1
        db.open_table("frames").delete("frame_id = 'frame-0'")
        assert prepare_batch(config, db, options)["superseded"]


def test_feature_job_yields_without_creating_another_job_and_blocked_work_does_not_spin(config, tmp_path):
    db, profile, rows, grids = setup_cache(config, tmp_path)
    store = LabStore(config.paths.state_dir, config.paths.assets_dir)
    store.initialize()
    with patch("pipeline.index.search_features.configured_framing_spatial_profile", return_value=profile):
        options = preparation_request(config, db, "film-a")
    job = store.enqueue_search_features(options)
    assert store.enqueue_search_features(options)["id"] == job["id"]
    assert store.claim("editor") is None
    assert store.claim("ingest")["id"] == job["id"]
    store.continue_search_features(job["id"], {"cursor": "frame-0"})
    assert store.claim("ingest")["result"]["cursor"] == "frame-0"
    store.continue_search_features(job["id"], {"cursor": "frame-0"}, error="storage full")
    assert store.claim("ingest") is None
    assert store.enqueue_search_features(options)["id"] == job["id"]
    assert store.claim("ingest")["id"] == job["id"]
    store.cancel(job["id"])
    assert store.continue_search_features(job["id"], {})["status"] == "cancelled"


def test_storage_counts_retained_bytes_and_refuses_over_budget(config, tmp_path):
    folder = config.paths.assets_dir / "db" / "frame_framing_test.lance"
    folder.mkdir(parents=True)
    (folder / "retained.bin").write_bytes(b"x" * 30)
    assert storage_status(config)["used_bytes"] == 30
    with pytest.raises(SearchStorageFull):
        with reserve_search_storage(config, 65 * 1024**3):
            pytest.fail("Allocation must not start")
    assert (folder / "retained.bin").read_bytes() == b"x" * 30


def test_cancelled_preparation_does_not_infer_or_publish(config, tmp_path):
    from pipeline.lab.media import JobCancelled
    db, profile, rows, grids = setup_cache(config, tmp_path)
    with patch("pipeline.index.search_features.embed_spatial_images") as encode:
        with pytest.raises(JobCancelled):
            prepare_batch(config, db, {}, cancelled=lambda: True)
        encode.assert_not_called()


def test_fitting_waits_behind_preparation_and_interrupt_resume_keeps_cursor(config, tmp_path):
    db, profile, rows, grids = setup_cache(config, tmp_path)
    store = LabStore(config.paths.state_dir, config.paths.assets_dir)
    store.initialize()
    fit = store.enqueue_composition_fit()
    assert store.enqueue_composition_fit()["id"] == fit["id"]
    with patch("pipeline.index.search_features.configured_framing_spatial_profile", return_value=profile):
        options = preparation_request(config, db, "film-a")
    job = store.enqueue_search_features(options)
    assert store.claim("editor") is None
    assert store.claim("ingest")["id"] == job["id"]
    store.continue_search_features(job["id"], {"cursor": "frame-1"})
    store.claim("ingest")
    store.recover_interrupted("ingest")
    assert store.enqueue_search_features(options)["id"] == job["id"]
    resumed = store.claim("ingest")
    assert resumed["result"]["cursor"] == "frame-1"
    store.finish(job["id"])
    assert store.claim("ingest")["id"] == fit["id"]


def test_cache_discard_reclaims_only_optional_table(config, tmp_path):
    from pipeline.index.framing_cache import write_cache_rows, read_input, cache_table_name
    from pipeline.index.search_storage import discard_spatial_cache
    from pipeline.index.writer import table_names
    db, profile, rows, grids = setup_cache(config, tmp_path)
    write_cache_rows(db, profile, [read_input(row) for row in rows], grids)
    name = cache_table_name(profile)
    with pytest.raises(ValueError):
        discard_spatial_cache(config, "frames", apply=True)
    assert discard_spatial_cache(config, name)["present"]
    assert name in table_names(db)
    assert discard_spatial_cache(config, name, apply=True)["removed_bytes"] > 0
    assert db.open_table("frames").count_rows() == 3
    assert all(read_input(row).data for row in rows)


def test_background_fit_queues_profile_scoped_preparation_without_promotion(config, tmp_path):
    from types import SimpleNamespace
    from pipeline.lab.worker import execute_job
    db, profile, rows, grids = setup_cache(config, tmp_path)
    store = LabStore(config.paths.state_dir, config.paths.assets_dir)
    store.initialize()
    store.enqueue_composition_fit()
    job = store.claim("ingest")
    with patch("pipeline.index.search_features.configured_framing_spatial_profile", return_value=profile):
        options = preparation_request(config, db, "film-a")
    with patch("pipeline.index.composition_build.fit_profiles", return_value=[SimpleNamespace(profile_id="compact-test")]), \
         patch("pipeline.index.writer.published_film_ids", return_value={"film-a"}), \
         patch("pipeline.index.search_features.preparation_request", return_value=options) as request:
        finished = execute_job(job, config, db, store, role="ingest")
    assert finished["status"] == "completed"
    assert finished["result"]["promoted"] is False
    assert request.call_args.kwargs["composition_profile"] == "compact-test"
    assert store.claim("ingest")["kind"] == "prepare-search-features"
