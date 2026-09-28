"""Targeted temporal refresh: retained evidence and bounded publication."""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
from unittest.mock import Mock

import numpy as np
import pytest

from pipeline.config import Config
from pipeline.index.backfill_text import TextBackfillResult
from pipeline.index.writer import (
    FrameWrite, UnitWrite, create_tables, open_db, publish_film_index,
)
from pipeline.ingest import backfill_temporal as temporal
from pipeline.ingest.media import keyframe_paths, keyframe_timestamp_source
from pipeline.ingest.probe import FilmRecord
from pipeline.ingest.shots import SHORT_SHOT_SAMPLING_PROFILE, Shot


DIM = 8
VECTOR = np.ones(DIM, dtype=np.float32) / np.sqrt(DIM)


def _film(config: Config, film_id: str) -> FilmRecord:
    path = config.paths.films_dir / f"{film_id}.mkv"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(f"source media {film_id}".encode())
    asset_dir = config.paths.assets_dir / film_id
    asset_dir.mkdir(parents=True, exist_ok=True)
    return FilmRecord(film_id, path, asset_dir, 20.0, 24.0, False, film_id)


def _shot(film: FilmRecord, index: int, *, duration: float = 1.25, frames: int = 1, profile: str = "") -> Shot:
    start = float(index * 3)
    times = [start + duration * (i + 1) / (frames + 1) for i in range(frames)]
    return Shot(f"{film.film_id}_{index:04d}", start, start + duration, None, times, sampling_profile=profile)


def _publish(db, config: Config, film: FilmRecord, shots: list[Shot]) -> None:
    units, frames = [], []
    for shot in shots:
        annotation = {"caption": "An empty tree.", "mood": ["calm", "still"], "searchable_text": "An empty tree. Hello."}
        units.append(UnitWrite(shot, annotation, VECTOR, VECTOR, ["Hello.", "Stay here."]))
        for index, path in enumerate(keyframe_paths(film, shot)):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"retained original image")
            frames.append(FrameWrite(shot.shot_id, shot.shot_id, index, shot.keyframe_times[index], path, config.models.visual_encoder, VECTOR, index == len(shot.keyframe_times) // 2))
    publish_film_index(db, film, units, frames)


def _rows(db, table: str) -> list[dict]:
    return sorted(db.open_table(table).search().limit(None).to_list(), key=lambda row: row.get("unit_id", row.get("film_id", "")) + str(row.get("frame_index", "")))


@pytest.fixture
def library(config: Config):
    db = open_db(config)
    create_tables(db, vector_dim=DIM)
    film = _film(config, "film_a")
    other = _film(config, "film_b")
    short = _shot(film, 0)
    second = _shot(film, 1)
    long = _shot(film, 2, duration=3, frames=3)
    other_shot = _shot(other, 0)
    _publish(db, config, film, [short, second, long])
    _publish(db, config, other, [other_shot])
    return db, film, other, short, second, long, other_shot


@pytest.fixture
def stages(monkeypatch: pytest.MonkeyPatch, library):
    _db, film, *_rest = library
    probe = Mock(return_value=film)
    source_hash = Mock(return_value=film.film_id)
    captures: list[tuple] = []

    def extract(film, shots, _config, *, extract_previews=True):
        assert extract_previews is False
        for shot in shots:
            assert shot.sampling_profile == SHORT_SHOT_SAMPLING_PROFILE
            for path in keyframe_paths(film, shot):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"new timestamped image")

    def annotate(shot, paths, dialogue, _config, *, cache_dir):
        captures.append((shot, paths, dialogue, cache_dir))
        return {"caption": "A man fades away beside a tree.", "mood": ["strange", "quiet"], "searchable_text": "A man fades away beside a tree. " + " ".join(line.text for line in dialogue)}

    extract_mock = Mock(side_effect=extract)
    annotate_mock = Mock(side_effect=annotate)
    image_mock = Mock(side_effect=lambda paths, _config: np.array([VECTOR] * len(paths)))
    text_mock = Mock(side_effect=lambda strings, _config: np.array([VECTOR] * len(strings)))
    semantic = Mock(return_value=TextBackfillResult("profile", "table", 3, 12, 4, 12, 8, True))
    monkeypatch.setattr(temporal, "probe_film", probe)
    monkeypatch.setattr(temporal, "_content_hash", source_hash)
    monkeypatch.setattr(temporal, "extract_media", extract_mock)
    monkeypatch.setattr(temporal, "annotate_shot", annotate_mock)
    monkeypatch.setattr(temporal, "embed_images", image_mock)
    monkeypatch.setattr(temporal, "embed_text", text_mock)
    monkeypatch.setattr(temporal, "keyframe_timestamp", lambda _film, shot, index: shot.keyframe_times[index])
    monkeypatch.setattr(temporal, "backfill_text_features_during_ingest", semantic)
    # Make forbidden whole-pipeline stages fail loudly if a refactor calls one.
    monkeypatch.setattr("pipeline.ingest.shots.detect_shots", Mock(side_effect=AssertionError("detector must not run")))
    monkeypatch.setattr("pipeline.ingest.dialogue.extract_dialogue", Mock(side_effect=AssertionError("dialogue extraction must not run")))
    return {"probe": probe, "hash": source_hash, "extract": extract_mock, "annotate": annotate_mock, "images": image_mock, "text": text_mock, "semantic": semantic, "captures": captures}


def test_empty_plan_is_read_only(config: Config):
    assert not config.paths.assets_dir.exists()
    result = temporal.plan_temporal_backfill(config)
    assert result["eligible_units"] == 0
    assert not config.paths.assets_dir.exists()


def test_plan_scopes_exact_legacy_short_units_without_opening_media(config: Config, library, monkeypatch):
    db, film, other, short, second, long, other_shot = library
    original_versions = {name: db.open_table(name).version for name in ("films", "units", "frames")}
    monkeypatch.setattr(temporal, "probe_film", Mock(side_effect=AssertionError("dry run opened source")))
    result = temporal.plan_temporal_backfill(config, film_ids=[film.film_id])
    assert result["film_count"] == 1
    assert result["eligible_units"] == 2
    assert result["skipped_ineligible"] == 1
    assert result["films"][0]["unit_ids"] == [short.shot_id, second.shot_id]
    assert {name: db.open_table(name).version for name in original_versions} == original_versions
    json.dumps(result)


def test_plan_rejects_unknown_cross_film_and_ineligible_explicit_ids(config: Config, library):
    _db, film, other, short, _second, long, other_shot = library
    for kwargs, message in [
        ({"film_ids": ["missing"]}, "unknown or unpublished film"),
        ({"unit_ids": ["missing"]}, "unknown unit"),
        ({"film_ids": [film.film_id], "unit_ids": [other_shot.shot_id]}, "outside"),
        ({"unit_ids": [long.shot_id]}, "not a legacy single-image"),
    ]:
        with pytest.raises(ValueError, match=message):
            temporal.plan_temporal_backfill(config, **kwargs)
    assert temporal.plan_temporal_backfill(config, unit_ids=[])["eligible_units"] == 0
    assert temporal.plan_temporal_backfill(config, film_ids=[])["eligible_units"] == 0


@pytest.mark.parametrize("frames", [1, 2, 3])
def test_current_native_profile_is_skipped_even_when_original_files_remain(config: Config, library, frames: int):
    db, film, _other, short, second, long, _other_shot = library
    original = keyframe_paths(film, short)[0]
    current = _shot(film, 0, frames=frames, profile=SHORT_SHOT_SAMPLING_PROFILE)
    _publish(db, config, film, [current, second, long])
    assert original.is_file()
    result = temporal.plan_temporal_backfill(config, unit_ids=[short.shot_id])
    assert result["eligible_units"] == 0
    assert result["skipped_current"] == 1


def test_unknown_sampling_profile_is_not_overwritten(config: Config, library):
    db, film, _other, short, *_rest = library
    table = db.open_table("units")
    table.update(where=f"unit_id = '{short.shot_id}'", values={"keyframe_paths": json.dumps([str(film.asset_dir / "keyframes" / "future-policy" / f"{short.shot_id}_0.webp")])})
    result = temporal.plan_temporal_backfill(config, film_ids=[film.film_id])
    assert result["eligible_units"] == 1
    with pytest.raises(ValueError, match="not a legacy single-image"):
        temporal.plan_temporal_backfill(config, unit_ids=[short.shot_id])


def test_targeted_refresh_preserves_other_rows_sources_dialogue_and_timings(config: Config, library, stages):
    db, film, _other, short, second, _long, _other_shot = library
    before_units, before_frames = _rows(db, "units"), _rows(db, "frames")
    before_films = _rows(db, "films")
    old_image = keyframe_paths(film, short)[0]
    retained = [film.path, old_image, film.asset_dir / "shots.json", film.asset_dir / "dialogue.json", film.asset_dir / "previews" / f"{short.shot_id}.mp4"]
    for path in retained[2:]:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"retained evidence must not change")
    before_files = {path: path.read_bytes() for path in retained}
    progress = []
    result = temporal.backfill_temporal(config, film_id=film.film_id, unit_ids=[short.shot_id], progress=progress.append)
    after_units, after_frames = _rows(db, "units"), _rows(db, "frames")
    assert result["published_units"] == 1
    assert result["published_frames"] == 3
    assert result["semantic_text"]["status"] == "active"
    assert not result["cancelled"]
    assert {path: path.read_bytes() for path in retained} == before_files
    assert _rows(db, "films") == before_films
    assert [row for row in after_units if row["unit_id"] != short.shot_id] == [row for row in before_units if row["unit_id"] != short.shot_id]
    assert [row for row in after_frames if row["unit_id"] != short.shot_id] == [row for row in before_frames if row["unit_id"] != short.shot_id]
    old = next(row for row in before_units if row["unit_id"] == short.shot_id)
    new = next(row for row in after_units if row["unit_id"] == short.shot_id)
    for name in ("unit_id", "film_id", "shot_id", "parent_shot_id", "t_start", "t_end", "dialogue"):
        assert new[name] == old[name]
    assert new["caption"] == "A man fades away beside a tree."
    assert "Hello. Stay here." in new["searchable_text"]
    assert all(SHORT_SHOT_SAMPLING_PROFILE in path for path in json.loads(new["keyframe_paths"]))
    assert {row["timestamp_source"] for row in after_frames if row["unit_id"] == short.shot_id} == {keyframe_timestamp_source(stages["captures"][0][0])}
    assert progress[-1] == {"stage": "complete", "film_id": film.film_id, "completed": 1, "total": 1}
    stages["semantic"].assert_called_once_with(config, film_id=film.film_id)
    json.dumps(result)


def test_repeat_is_idempotent_but_retries_deferred_text_refresh(config: Config, library, stages):
    db, film, _other, short, *_rest = library
    stages["semantic"].side_effect = [RuntimeError("weights unavailable"), TextBackfillResult("p", "t", 3, 12, 4, 12, 8, True)]
    first = temporal.backfill_temporal(config, film_id=film.film_id, unit_ids=[short.shot_id])
    assert first["semantic_text"] == {"status": "deferred", "error": "weights unavailable"}
    version = db.open_table("units").version
    second = temporal.backfill_temporal(config, film_id=film.film_id, unit_ids=[short.shot_id])
    assert second["published_units"] == 0
    assert second["skipped_current"] == 1
    assert second["semantic_text"]["status"] == "active"
    assert db.open_table("units").version == version
    assert stages["extract"].call_count == stages["annotate"].call_count == 1


def test_annotation_failure_leaves_entire_batch_unpublished_and_retains_prepared_files(config: Config, library, stages):
    db, film, _other, short, *_rest = library
    before = {name: _rows(db, name) for name in ("films", "units", "frames")}
    stages["annotate"].side_effect = RuntimeError("annotation failed")
    with pytest.raises(RuntimeError, match="annotation failed"):
        temporal.backfill_temporal(config, film_id=film.film_id)
    assert {name: _rows(db, name) for name in before} == before
    assert list((film.asset_dir / "keyframes" / SHORT_SHOT_SAMPLING_PROFILE).glob("*.webp"))
    stages["semantic"].assert_not_called()


def test_retry_after_later_batch_failure_skips_published_work(config: Config, library, stages):
    db, film, _other, short, second, *_rest = library
    original = stages["annotate"].side_effect

    def fail_second(shot, *args, **kwargs):
        if shot.shot_id == second.shot_id:
            raise RuntimeError("later batch failed")
        return original(shot, *args, **kwargs)

    stages["annotate"].side_effect = fail_second
    targets = [short.shot_id, second.shot_id]
    with pytest.raises(RuntimeError, match="later batch failed"):
        temporal.backfill_temporal(
            config, film_id=film.film_id, unit_ids=targets, batch_size=1,
        )
    stages["semantic"].assert_not_called()
    first_row = next(row for row in _rows(db, "units") if row["unit_id"] == short.shot_id)
    stages["annotate"].reset_mock(side_effect=True)
    stages["annotate"].side_effect = original
    result = temporal.backfill_temporal(
        config, film_id=film.film_id, unit_ids=targets, batch_size=1,
    )
    assert result["published_units"] == 1
    assert result["skipped_current"] == 1
    assert result["semantic_text"]["status"] == "active"
    assert next(row for row in _rows(db, "units") if row["unit_id"] == short.shot_id) == first_row
    assert stages["annotate"].call_count == 1
    assert stages["annotate"].call_args.args[0].shot_id == second.shot_id


def test_source_identity_change_is_rejected_before_extraction(config: Config, library, stages):
    db, film, *_rest = library
    stages["probe"].return_value = replace(film, film_id="different-content")
    with pytest.raises(RuntimeError, match="source film identity changed"):
        temporal.backfill_temporal(config, film_id=film.film_id)
    stages["extract"].assert_not_called()


@pytest.mark.parametrize("change_hash_only", [False, True])
def test_source_change_during_paid_calls_cannot_publish(config: Config, library, stages, change_hash_only: bool):
    db, film, *_rest = library
    before = {name: _rows(db, name) for name in ("units", "frames")}
    original = stages["annotate"].side_effect

    def change(*args, **kwargs):
        if change_hash_only:
            stages["hash"].return_value = "changed-content"
        else:
            film.path.write_bytes(b"replacement source bytes")
        return original(*args, **kwargs)

    stages["annotate"].side_effect = change
    with pytest.raises(RuntimeError, match="source film changed"):
        temporal.backfill_temporal(config, film_id=film.film_id)
    assert {name: _rows(db, name) for name in before} == before


def test_pending_relink_is_rejected_before_probe(config: Config, library, stages):
    _db, film, *_rest = library
    (film.asset_dir / ".scene-recall-relink.json").write_text("{}", encoding="utf-8")
    with pytest.raises(RuntimeError, match="interrupted source relink"):
        temporal.backfill_temporal(config, film_id=film.film_id)
    stages["probe"].assert_not_called()


def test_cancellation_before_work_avoids_extraction_or_publication(config: Config, library, stages):
    db, film, *_rest = library
    version = db.open_table("units").version
    result = temporal.backfill_temporal(config, film_id=film.film_id, cancelled=lambda: True)
    assert result["cancelled"]
    assert result["published_units"] == 0
    assert db.open_table("units").version == version
    stages["probe"].assert_not_called()
    stages["extract"].assert_not_called()


def test_cancellation_after_preparation_keeps_paid_cache_and_old_index(config: Config, library, stages):
    db, film, _other, short, *_rest = library
    version = db.open_table("units").version
    should_cancel = False
    paid_cache = film.asset_dir / "annotations" / "paid-response.json"
    original = stages["annotate"].side_effect

    def annotate(*args, **kwargs):
        nonlocal should_cancel
        paid_cache.parent.mkdir(parents=True, exist_ok=True)
        paid_cache.write_text("{}", encoding="utf-8")
        should_cancel = True
        return original(*args, **kwargs)

    stages["annotate"].side_effect = annotate
    result = temporal.backfill_temporal(config, film_id=film.film_id, unit_ids=[short.shot_id], cancelled=lambda: should_cancel)
    assert result["cancelled"]
    assert result["published_units"] == 0
    assert paid_cache.is_file()
    assert db.open_table("units").version == version
    stages["text"].assert_not_called()


def test_batches_are_bounded_and_completed_batches_survive_cancellation(config: Config, library, stages):
    db, film, _other, short, second, *_rest = library
    should_cancel = False

    def progress(event):
        nonlocal should_cancel
        if event["stage"] == "published temporal evidence":
            should_cancel = True

    result = temporal.backfill_temporal(config, film_id=film.film_id, batch_size=1, progress=progress, cancelled=lambda: should_cancel)
    assert result["cancelled"]
    assert result["published_units"] == 1
    assert result["semantic_text"]["status"] == "deferred"
    assert stages["extract"].call_count == stages["annotate"].call_count == 1
    plan = temporal.plan_temporal_backfill(config, film_ids=[film.film_id])
    assert plan["films"][0]["unit_ids"] == [second.shot_id]


def test_global_ingest_lock_blocks_backfill(config: Config, library, stages):
    _db, film, *_rest = library
    with temporal.global_ingest_lock(config.paths.assets_dir):
        with pytest.raises(RuntimeError, match="another film ingest"):
            temporal.backfill_temporal(config, film_id=film.film_id)
    stages["probe"].assert_not_called()


@pytest.mark.parametrize("batch_size", [0, -1, 129, 1.5, True])
def test_invalid_batch_size_is_rejected_without_work(config: Config, batch_size):
    with pytest.raises(ValueError, match="batch_size"):
        temporal.backfill_temporal(config, film_id="missing", batch_size=batch_size)
    assert not config.paths.assets_dir.exists()
