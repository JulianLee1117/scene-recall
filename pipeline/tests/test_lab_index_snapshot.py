"""Editor reads retain a complete library while ingestion publishes its replacement."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import patch

import pytest

from pipeline.index.backfill_text import backfill_text_features
from pipeline.index.text_features import resolve_ready_text_profile
from pipeline.index.writer import _PUBLICATION_LOCK, _database_write_lock, open_db
from pipeline.lab import index_snapshot
from pipeline.lab.index_snapshot import acquire_editor_snapshot
from pipeline.lab.media import JobCancelled
from pipeline.tests.test_text_features import _write_unit, _fake_embeddings


@pytest.fixture(autouse=True)
def snapshots(monkeypatch):
    monkeypatch.setattr(index_snapshot, "_SNAPSHOTS", {})


def _ready(config, tmp_path):
    _write_unit(config, tmp_path, "film_a")
    with patch("pipeline.index.backfill_text.embed_semantic_documents", side_effect=_fake_embeddings):
        backfill_text_features(config)
    return open_db(config)


def test_editor_keeps_prior_ready_versions_and_manifest_until_new_generation_is_complete(config, tmp_path):
    db = _ready(config, tmp_path)
    progress = []
    first = acquire_editor_snapshot(config, db, progress.append, lambda: False)
    assert resolve_ready_text_profile(config, first) is not None
    _write_unit(config, tmp_path, "film_b")
    assert resolve_ready_text_profile(config, db) is None
    retained = acquire_editor_snapshot(config, db, progress.append, lambda: False)
    assert retained is first
    assert retained.open_table("units").count_rows() == 1
    assert resolve_ready_text_profile(config, retained) is not None
    from pipeline.search.retrieve import search_semantic_views
    from pipeline.tests.test_text_features import _vector
    with patch("pipeline.search.retrieve.embed_semantic_query", return_value=_vector()):
        rows = search_semantic_views("woman in neon", ["caption"], retained, config)
    assert rows and {row["film_id"] for row in rows} == {"film_a"}
    assert "last complete" in progress[-1]
    with patch("pipeline.index.backfill_text.embed_semantic_documents", side_effect=_fake_embeddings):
        backfill_text_features(config)
    current = acquire_editor_snapshot(config, db, progress.append, lambda: False)
    assert current.open_table("units").count_rows() == 2
    assert current.versions["units"] != first.versions["units"]
    assert resolve_ready_text_profile(config, current) is not None
    # A still-running job keeps its frozen coverage proof after manifest replacement.
    assert resolve_ready_text_profile(config, first) is not None
    assert first.open_table("units").count_rows() == 1
    with pytest.raises(AttributeError):
        first.open_table("units").delete("true")
    with pytest.raises(AttributeError):
        first.create_table("unexpected", [])


def test_concurrent_publication_cannot_expose_half_updated_tables_or_block_prior_snapshot(config, tmp_path):
    db = _ready(config, tmp_path)
    first = acquire_editor_snapshot(config, db, lambda _: None, lambda: False)
    entered, release = Event(), Event()

    def publishing():
        with _PUBLICATION_LOCK, _database_write_lock(db):
            entered.set()
            assert release.wait(5)

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(publishing)
        try:
            assert entered.wait(2)
            held = acquire_editor_snapshot(config, db, lambda _: None, lambda: False, timeout=.2)
            assert held is first
            assert held.open_table("units").count_rows() == 1
        finally:
            release.set()
        future.result(timeout=2)


def test_cross_process_publication_lock_also_excludes_capture(config, tmp_path):
    """The OS lock must work even without this process's threading lock."""
    db = _ready(config, tmp_path)
    first = acquire_editor_snapshot(config, db, lambda _: None, lambda: False)
    with _database_write_lock(db):
        retained = acquire_editor_snapshot(config, db, lambda _: None, lambda: False, timeout=.2)
        assert retained is first


def test_initial_partial_publication_wait_is_bounded_and_cancellable(config, tmp_path):
    db = _ready(config, tmp_path)
    _write_unit(config, tmp_path, "film_b")
    progress = []
    with pytest.raises(RuntimeError, match="No AI requests were made"):
        acquire_editor_snapshot(config, db, progress.append, lambda: False, timeout=0)
    assert "no AI requests started" in progress[0]
    with pytest.raises(JobCancelled):
        acquire_editor_snapshot(config, db, progress.append, lambda: True)


def test_library_without_a_semantic_manifest_preserves_existing_capability_behavior(config, tmp_path):
    _write_unit(config, tmp_path, "film_a")
    snapshot = acquire_editor_snapshot(config, open_db(config), lambda _: None, lambda: False)
    assert snapshot.open_table("units").count_rows() == 1
    assert resolve_ready_text_profile(config, snapshot) is None


@pytest.fixture
def search_snapshots(monkeypatch):
    from collections import OrderedDict
    from pipeline.index import snapshot
    monkeypatch.setattr(snapshot, "_RECENT_SEARCH", OrderedDict())
    return snapshot


def test_search_keeps_the_last_complete_library_until_semantic_text_catches_up(config, tmp_path, search_snapshots):
    db = _ready(config, tmp_path)
    first = search_snapshots.acquire_search_snapshot(config, db)
    _write_unit(config, tmp_path, "film_b")            # units published; their text features are not yet
    assert search_snapshots.acquire_search_snapshot(config, db) is first
    with patch("pipeline.index.backfill_text.embed_semantic_documents", side_effect=_fake_embeddings):
        backfill_text_features(config)
    current = search_snapshots.acquire_search_snapshot(config, db)
    assert current is not first and current.open_table("units").count_rows() == 2
    assert resolve_ready_text_profile(config, current) is not None


def test_fresh_search_process_serves_an_incomplete_library_without_retaining_it(config, tmp_path, search_snapshots):
    db = _ready(config, tmp_path)
    _write_unit(config, tmp_path, "film_b")
    partial = search_snapshots.acquire_search_snapshot(config, db)
    assert partial.open_table("units").count_rows() == 2
    assert resolve_ready_text_profile(config, partial) is None
    assert not search_snapshots._RECENT_SEARCH


def test_search_with_a_complete_view_never_waits_on_a_writer(config, tmp_path, search_snapshots):
    db = _ready(config, tmp_path)
    first = search_snapshots.acquire_search_snapshot(config, db)
    with _database_write_lock(db):
        assert search_snapshots.acquire_search_snapshot(config, db) is first
    search_snapshots._RECENT_SEARCH.clear()
    with _database_write_lock(db), pytest.raises(search_snapshots.SearchLibraryUnavailable):
        search_snapshots.acquire_search_snapshot(config, db)
