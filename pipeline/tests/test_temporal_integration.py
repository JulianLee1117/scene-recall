"""Targeted publication, maintenance scheduling, and explicit CLI boundaries."""
from dataclasses import replace
import json
from pathlib import Path
from uuid import UUID

import numpy as np
import pytest
from click.testing import CliRunner

from pipeline.cli import cli
from pipeline.index.writer import FrameWrite, UnitWrite, create_tables, open_db, publish_film_index, publish_unit_updates
from pipeline.ingest.media import keyframe_paths
from pipeline.ingest.probe import FilmRecord
from pipeline.ingest.shots import Shot, SHORT_SHOT_SAMPLING_PROFILE
from pipeline.lab.store import LabStore
from pipeline.lab.worker import execute_job


@pytest.fixture
def publication(config):
    db = open_db(config)
    create_tables(db, vector_dim=4)
    film = FilmRecord("film-a", config.paths.films_dir / "a.mkv", config.paths.assets_dir / "film-a",
                      30, 24, False, "A")
    other = replace(film, film_id="film-b", asset_dir=config.paths.assets_dir / "film-b", path=config.paths.films_dir / "b.mkv")

    def prepared(film, number, *, enhanced=False):
        start = number * 3.0
        shot = Shot(f"{film.film_id}_{number}", start, start + 1.2, None,
                    [start + .1, start + .6, start + 1.1] if enhanced else [start + .6],
                    sampling_profile=SHORT_SHOT_SAMPLING_PROFILE if enhanced else "")
        vec = np.array([1, 0, 0, 0], dtype=np.float32)
        annotation = {"caption": "A person disappears" if enhanced else "An empty tree", "mood": ["quiet", "dark"],
                      "searchable_text": "A person disappears" if enhanced else "An empty tree"}
        frames = []
        for index, path in enumerate(keyframe_paths(film, shot)):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"new" if enhanced else b"old")
            frames.append(FrameWrite(shot.shot_id, shot.shot_id, index, shot.keyframe_times[index], path,
                                     config.models.visual_encoder, vec, index == len(shot.keyframe_times) // 2,
                                     "decoded_container_relative_pts_v2" if enhanced else "ingest_keyframe_seek_v1"))
        return UnitWrite(shot, annotation, vec, vec), frames

    first, second, foreign = prepared(film, 0), prepared(film, 1), prepared(other, 0)
    publish_film_index(db, film, [first[0], second[0]], first[1] + second[1])
    publish_film_index(db, other, [foreign[0]], foreign[1])
    return db, film, prepared


def rows(db, table):
    return sorted(db.open_table(table).search().limit(None).to_list(), key=lambda row: row.get("frame_id", row.get("unit_id")))


def test_targeted_publication_preserves_other_units_and_original_images(publication):
    db, film, prepared = publication
    before_units, before_frames = rows(db, "units"), rows(db, "frames")
    replacement, frames = prepared(film, 0, enhanced=True)
    publish_unit_updates(db, film, [replacement], frames)
    after_units, after_frames = rows(db, "units"), rows(db, "frames")
    assert len(after_units) == 3 and len(after_frames) == 5
    assert [row for row in before_units if row["unit_id"] != "film-a_0"] == [row for row in after_units if row["unit_id"] != "film-a_0"]
    assert [row for row in before_frames if row["unit_id"] != "film-a_0"] == [row for row in after_frames if row["unit_id"] != "film-a_0"]
    old = next(row for row in before_units if row["unit_id"] == "film-a_0")
    assert Path(json.loads(old["keyframe_paths"])[0]).read_bytes() == b"old"
    assert all(row["timestamp_source"] == "decoded_container_relative_pts_v2" for row in after_frames if row["unit_id"] == "film-a_0")
    assert db.open_table("units").search("disappears", query_type="fts").limit(10).to_list()[0]["unit_id"] == "film-a_0"


@pytest.mark.parametrize("fault", ["bounds", "new-unit", "outside", "duplicate-time", "dimension", "missing-frame"])
def test_bad_targeted_generation_cannot_mutate_current_rows(publication, fault):
    db, film, prepared = publication
    unit, frames = prepared(film, 9 if fault == "new-unit" else 0, enhanced=True)
    if fault == "bounds":
        unit = replace(unit, shot=replace(unit.shot, t_end=1.3))
    elif fault == "outside":
        frames[0] = replace(frames[0], timestamp=-.1)
    elif fault == "duplicate-time":
        frames[1] = replace(frames[1], timestamp=frames[0].timestamp)
    elif fault == "dimension":
        unit = replace(unit, txt_vec=np.array([1, 0], dtype=np.float32))
    elif fault == "missing-frame":
        frames.pop()
    before = (rows(db, "units"), rows(db, "frames"))
    with pytest.raises(ValueError):
        publish_unit_updates(db, film, [unit], frames)
    assert (rows(db, "units"), rows(db, "frames")) == before


@pytest.fixture
def store(config):
    store = LabStore(config.paths.state_dir)
    store.initialize()
    return store


def test_maintenance_batches_dedupe_and_yield_to_new_foreground_jobs(store, tmp_path, monkeypatch):
    # Force timestamp ties and reverse UUID ordering, so FIFO cannot pass by luck.
    identities = iter(UUID(int=index) for index in range(100, 0, -1))
    monkeypatch.setattr("pipeline.lab.store.time.time", lambda: 1_700_000_000.)
    monkeypatch.setattr("pipeline.lab.store.uuid.uuid4", lambda: next(identities))
    jobs = store.enqueue_temporal_backfill("film", ["u3", "u1", "u2"], sampling_profile=SHORT_SHOT_SAMPLING_PROFILE, batch_size=2)
    assert len(jobs) == 2
    with store.connection() as con:
        assert con.execute("SELECT id FROM jobs WHERE status='queued'").fetchone() is None
    assert all(job["status"] == "waiting_worker" for job in jobs)
    assert [j["id"] for j in store.enqueue_temporal_backfill("film", ["u2", "u3", "u1"], sampling_profile=SHORT_SHOT_SAMPLING_PROFILE, batch_size=2)] == [j["id"] for j in jobs]
    assert [job["id"] for job in store.temporal_jobs()] == [job["id"] for job in jobs]
    foreground = store.enqueue("ingest", path=tmp_path / "new.mkv")
    second_foreground = store.enqueue("ingest", path=tmp_path / "second.mkv")
    assert [(job["job_id"], job["queue_position"]) for job in store.ingest_snapshots()] == [
        (foreground["id"], 1), (second_foreground["id"], 2),
    ]
    assert store.claim()["id"] == foreground["id"]
    store.finish(foreground["id"])
    assert store.claim()["id"] == second_foreground["id"]
    store.finish(second_foreground["id"])
    assert store.claim()["id"] == jobs[0]["id"]
    store.finish(jobs[0]["id"])
    next_foreground = store.enqueue("ingest", path=tmp_path / "another.mkv")
    assert store.claim()["id"] == next_foreground["id"]
    store.cancel(jobs[1]["id"])
    assert store.get_job(jobs[1]["id"])["status"] == "cancelled"


def test_worker_runs_scoped_maintenance_without_edit_revision(store, config, monkeypatch):
    import pipeline.ingest.backfill_temporal as backfill
    project = store.create_project("Keep this edit", "music-sketch")
    store.enqueue_temporal_backfill("film", ["u1"], sampling_profile=SHORT_SHOT_SAMPLING_PROFILE)
    calls = []
    def run(config, **kwargs):
        calls.append(kwargs)
        kwargs["progress"]({"stage": "Annotated", "completed": 1, "total": 1})
        return {"published_units": 1, "cancelled": False}
    monkeypatch.setattr(backfill, "backfill_temporal", run)
    result = execute_job(store.claim(), config, None, store)
    assert result["status"] == "completed" and result["result"]["published_units"] == 1
    assert calls[0]["film_id"] == "film" and calls[0]["unit_ids"] == ["u1"]
    assert store.get_project(project["id"]) == project


def test_worker_refuses_stale_queued_sampling_policy(store, config, monkeypatch):
    import pipeline.ingest.backfill_temporal as backfill
    store.enqueue_temporal_backfill("film", ["u1"], sampling_profile="obsolete")
    monkeypatch.setattr(backfill, "backfill_temporal", lambda *_args, **_kwargs: pytest.fail("must not run"))
    result = execute_job(store.claim(), config, None, store)
    assert result["status"] == "failed" and "sampling profile changed" in result["error"]


def test_cli_plan_never_runs_paid_work_and_enqueue_freezes_exact_scope(config, monkeypatch, tmp_path):
    import pipeline.cli as commands
    import pipeline.ingest.backfill_temporal as backfill
    monkeypatch.setattr(commands, "load_config", lambda: config)
    plan = {"sampling_profile": SHORT_SHOT_SAMPLING_PROFILE, "eligible_units": 2, "film_count": 1,
            "films": [{"film_id": "film", "title": "Film", "eligible_units": 2, "unit_ids": ["u1", "u2"]}]}
    scopes = []
    def make_plan(config, **kwargs):
        scopes.append(kwargs)
        return plan
    monkeypatch.setattr(backfill, "plan_temporal_backfill", make_plan)
    monkeypatch.setattr(backfill, "backfill_temporal", lambda *_args, **_kwargs: pytest.fail("must not run"))
    runner = CliRunner()
    output = tmp_path / "receipt.json"
    result = runner.invoke(cli, ["backfill-temporal", "--film-id", "film", "--unit-id", "u1", "--output", str(output)])
    assert result.exit_code == 0, result.output
    assert "Plan only" in result.output and json.loads(output.read_text())["jobs"] == []
    result = runner.invoke(cli, ["backfill-temporal", "--film-id", "film", "--enqueue", "--batch-size", "1"])
    assert result.exit_code == 0, result.output
    store = LabStore(config.paths.state_dir)
    assert len(store.temporal_jobs()) == 2
    assert scopes[0] == {"film_ids": ["film"], "unit_ids": ["u1"]}


def test_plan_preserves_current_ids_only_for_explicit_shot_retries(publication, config):
    from pipeline.ingest.backfill_temporal import plan_temporal_backfill

    db, film, prepared = publication
    unit, frames = prepared(film, 0, enhanced=True)
    publish_unit_updates(db, film, [unit], frames)
    explicit = plan_temporal_backfill(config, unit_ids=[unit.shot.shot_id])
    assert explicit["eligible_units"] == 0
    assert explicit["films"][0]["current_unit_ids"] == [unit.shot.shot_id]
    broad = plan_temporal_backfill(config, film_ids=[film.film_id])
    assert broad["films"][0]["current_unit_ids"] == []


def _current_retry_plan():
    return {
        "sampling_profile": SHORT_SHOT_SAMPLING_PROFILE,
        "eligible_units": 0, "film_count": 0,
        "films": [
            {"film_id": film, "title": film, "eligible_units": 0, "unit_ids": [],
             "skipped_current": 1, "current_unit_ids": [unit]}
            for film, unit in [("film-a", "a1"), ("film-b", "b1")]
        ],
    }


def test_cli_explicit_current_shots_retry_semantic_text_without_rescoping_films(config, monkeypatch):
    import pipeline.cli as commands
    import pipeline.ingest.backfill_temporal as backfill

    monkeypatch.setattr(commands, "load_config", lambda: config)
    monkeypatch.setattr(commands, "_lower_own_priority", lambda: None)
    monkeypatch.setattr(backfill, "plan_temporal_backfill", lambda *_args, **_kwargs: _current_retry_plan())
    calls = []
    def refresh(_config, **kwargs):
        calls.append((kwargs["film_id"], kwargs["unit_ids"]))
        return {"published_units": 0, "skipped_current": 1, "semantic_text": {"status": "active"}}
    monkeypatch.setattr(backfill, "backfill_temporal", refresh)
    runner = CliRunner()
    result = runner.invoke(cli, ["backfill-temporal", "--unit-id", "a1", "--unit-id", "b1", "--apply"])
    assert result.exit_code == 0, result.output
    assert calls == [("film-a", ["a1"]), ("film-b", ["b1"])]
    assert "Semantic text retry: 2" in result.output
    result = runner.invoke(cli, ["backfill-temporal", "--unit-id", "a1", "--unit-id", "b1", "--enqueue"])
    assert result.exit_code == 0, result.output
    jobs = LabStore(config.paths.state_dir).temporal_jobs()
    assert [(job["snapshot"]["temporal_backfill"]["film_id"], job["snapshot"]["temporal_backfill"]["unit_ids"]) for job in jobs] == calls


def test_cli_broad_current_plan_gives_index_repair_instruction_without_enqueuing_rows(config, monkeypatch):
    import pipeline.cli as commands
    import pipeline.ingest.backfill_temporal as backfill

    monkeypatch.setattr(commands, "load_config", lambda: config)
    monkeypatch.setattr(backfill, "plan_temporal_backfill", lambda *_args, **_kwargs: _current_retry_plan())
    monkeypatch.setattr(backfill, "backfill_temporal", lambda *_args, **_kwargs: pytest.fail("must not rerun current library rows"))
    result = CliRunner().invoke(cli, ["backfill-temporal", "--film-id", "film-a", "--enqueue"])
    assert result.exit_code == 0, result.output
    assert LabStore(config.paths.state_dir).temporal_jobs() == []
    assert "index-text --film-id film-a" in result.output


def test_cli_deferred_semantic_refresh_fails_actionably_after_preserving_receipt(config, monkeypatch, tmp_path):
    import pipeline.cli as commands
    import pipeline.ingest.backfill_temporal as backfill

    monkeypatch.setattr(commands, "load_config", lambda: config)
    monkeypatch.setattr(commands, "_lower_own_priority", lambda: None)
    monkeypatch.setattr(backfill, "plan_temporal_backfill", lambda *_args, **_kwargs: _current_retry_plan())
    monkeypatch.setattr(backfill, "backfill_temporal", lambda *_args, **_kwargs: {"published_units": 0, "semantic_text": {"status": "deferred", "error": "weights unavailable"}})
    receipt = tmp_path / "receipt.json"
    result = CliRunner().invoke(cli, ["backfill-temporal", "--unit-id", "a1", "--unit-id", "b1", "--apply", "--output", str(receipt)])
    assert result.exit_code == 1
    assert "index-text --film-id film-a" in result.output
    assert json.loads(receipt.read_text(encoding="utf-8"))["results"][0]["semantic_text"]["status"] == "deferred"


def test_worker_retains_publication_receipt_when_semantic_refresh_needs_attention(store, config, monkeypatch):
    import pipeline.ingest.backfill_temporal as backfill

    store.enqueue_temporal_backfill("film", ["u1"], sampling_profile=SHORT_SHOT_SAMPLING_PROFILE)
    completed = {"published_units": 1, "published_frames": 3, "cancelled": False,
                 "semantic_text": {"status": "deferred", "error": "weights unavailable"}}
    monkeypatch.setattr(backfill, "backfill_temporal", lambda *_args, **_kwargs: completed)
    result = execute_job(store.claim(), config, None, store)
    assert result["status"] == "failed"
    assert result["result"] == completed
    assert "index-text --film-id film" in result["error"]
