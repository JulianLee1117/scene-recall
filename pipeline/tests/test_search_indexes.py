"""Physical index changes preserve only previously valid evidence manifests."""
from unittest.mock import patch

from pipeline.index.backfill_text import backfill_text_features
from pipeline.index.search_indexes import install_lookup_indexes, lookup_plan
from pipeline.index.text_features import resolve_ready_text_profile
from pipeline.index.writer import open_db
from pipeline.tests.test_text_features import _write_unit, _fake_embeddings


def test_lookup_migration_preserves_text_readiness_and_is_idempotent(config, tmp_path):
    _write_unit(config, tmp_path, "film_a")
    with patch("pipeline.index.backfill_text.embed_semantic_documents", side_effect=_fake_embeddings):
        backfill_text_features(config)
    db = open_db(config)
    before = resolve_ready_text_profile(config, db)
    assert before is not None
    assert install_lookup_indexes(config, db)
    assert install_lookup_indexes(config, db) == []
    assert resolve_ready_text_profile(config, db) == before
    assert all(item["ready"] for item in lookup_plan(db))


def test_index_creation_cannot_certify_stale_text_evidence(config, tmp_path):
    _write_unit(config, tmp_path, "film_a")
    with patch("pipeline.index.backfill_text.embed_semantic_documents", side_effect=_fake_embeddings):
        backfill_text_features(config)
    _write_unit(config, tmp_path, "film_b")
    db = open_db(config)
    assert resolve_ready_text_profile(config, db) is None
    install_lookup_indexes(config, db)
    assert resolve_ready_text_profile(config, db) is None
