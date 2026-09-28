"""Operator holds affect only pending optional jobs and preserve saved progress."""
import json

from click.testing import CliRunner
import pytest

from pipeline.index import search_cli
from pipeline.lab.store import LabStore, SEARCH_FEATURE_PAUSE_REASON


def _options(film_id="film-a"):
    return {"film_id": film_id, "source_generation": "a" * 64,
            "profile": {"version": "test"}, "frame_count": 100}


def _seed(store, identity, kind, status, *, error=None, cancel_requested=0):
    with store.connection() as con:
        con.execute("INSERT INTO jobs (id,kind,status,snapshot,created_at,error,cancel_requested) "
                    "VALUES (?,?,?,?,?,?,?)", (identity, kind, status, "{}", 1., error, cancel_requested))


def _raw(store, identity):
    with store.connection() as con:
        return dict(con.execute("SELECT * FROM jobs WHERE id=?", (identity,)).fetchone())


def test_hold_survives_reopen_and_duplicate_enqueue_without_losing_cursor(tmp_path):
    store = LabStore(tmp_path)
    store.initialize()
    feature = store.enqueue_search_features(_options())
    fit = store.enqueue_composition_fit()
    assert store.claim("ingest")["id"] == feature["id"]
    store.continue_search_features(feature["id"], {"cursor": "frame-32", "written": 32})
    before = _raw(store, feature["id"])

    assert store.pause_search_features() == {"paused": 2, "job_ids": [feature["id"], fit["id"]]}
    assert store.pause_search_features() == {"paused": 0, "job_ids": []}
    after = _raw(store, feature["id"])
    assert after == {**before, "status": "waiting_worker", "error": SEARCH_FEATURE_PAUSE_REASON}
    reopened = LabStore(tmp_path)
    assert reopened.claim("ingest") is None
    assert reopened.enqueue_search_features(_options())["error"] == SEARCH_FEATURE_PAUSE_REASON
    assert reopened.enqueue_composition_fit()["id"] == fit["id"]
    assert reopened.claim("ingest") is None
    assert all(row["paused"] for row in reopened.search_feature_queue())
    assert reopened.search_feature_queue()[0]["cursor"] == "frame-32"

    assert reopened.resume_search_features() == {"resumed": 2, "job_ids": [feature["id"], fit["id"]]}
    assert _raw(reopened, feature["id"]) == before
    resumed = reopened.claim("ingest")
    assert resumed["id"] == feature["id"] and resumed["result"] == {"cursor": "frame-32", "written": 32}
    reopened.finish(feature["id"])
    assert reopened.claim("ingest")["id"] == fit["id"]
    assert reopened.resume_search_features() == {"resumed": 0, "job_ids": []}


@pytest.mark.parametrize("kind", ["prepare-search-features", "fit-search-composition"])
def test_pause_refuses_active_work_atomically(tmp_path, kind):
    store = LabStore(tmp_path)
    store.initialize()
    _seed(store, "active", kind, "running")
    _seed(store, "pending", "prepare-search-features", "queued")
    before = [_raw(store, identity) for identity in ("active", "pending")]
    with pytest.raises(ValueError, match="active batch to finish"):
        store.pause_search_features()
    assert [_raw(store, identity) for identity in ("active", "pending")] == before


def test_pause_and_resume_leave_other_work_and_unrelated_errors_untouched(tmp_path):
    store = LabStore(tmp_path)
    store.initialize()
    unchanged = [
        ("film", "ingest", "queued", None, 0),
        ("edit", "generate", "queued", None, 0),
        ("storage", "prepare-search-features", "waiting_worker", "storage full", 0),
        ("failed", "prepare-search-features", "failed", "decode failed", 0),
        ("interrupted", "prepare-search-features", "interrupted", "worker stopped", 0),
        ("cancelled", "prepare-search-features", "cancelled", None, 1),
        ("cancelling", "prepare-search-features", "waiting_worker", None, 1),
        ("other-hold", "backfill-temporal", "waiting_worker", SEARCH_FEATURE_PAUSE_REASON, 0),
    ]
    for identity, kind, status, error, cancel_requested in unchanged:
        _seed(store, identity, kind, status, error=error, cancel_requested=cancel_requested)
    before = {row[0]: _raw(store, row[0]) for row in unchanged}
    _seed(store, "queued", "prepare-search-features", "queued")
    _seed(store, "fit", "fit-search-composition", "waiting_worker")
    assert store.pause_search_features()["job_ids"] == ["queued", "fit"]
    assert store.resume_search_features()["job_ids"] == ["queued", "fit"]
    assert {row[0]: _raw(store, row[0]) for row in unchanged} == before
    store.pause_search_features()
    assert store.claim("ingest")["id"] == "film"
    assert store.claim("editor")["id"] == "edit"


def test_new_preparation_remains_available_and_cancelled_holds_do_not_resume(tmp_path):
    store = LabStore(tmp_path)
    store.initialize()
    held = store.enqueue_search_features(_options())
    store.pause_search_features()
    store.cancel(held["id"])
    assert store.resume_search_features()["resumed"] == 0
    new = store.enqueue_search_features(_options("film-new"))
    assert store.claim("ingest")["id"] == new["id"]
    assert not next(row for row in store.search_feature_queue() if row["job_id"] == held["id"])["paused"]


def test_queue_cli_preserves_state_and_reports_actionable_active_error(config, monkeypatch):
    monkeypatch.setattr(search_cli, "load_config", lambda: config)
    runner = CliRunner()
    store = LabStore(config.paths.state_dir)
    for command, expected in [("queue", []), ("pause", {"paused": 0, "job_ids": []}),
                              ("resume", {"resumed": 0, "job_ids": []})]:
        result = runner.invoke(search_cli.search_features, [command])
        assert result.exit_code == 0, result.output
        assert json.loads(result.output) == expected
        assert not store.path.exists()
    store.initialize()
    job = store.enqueue_search_features(_options())
    assert json.loads(runner.invoke(search_cli.search_features, ["pause"]).output)["paused"] == 1
    assert json.loads(runner.invoke(search_cli.search_features, ["queue"]).output)[0]["paused"]
    assert json.loads(runner.invoke(search_cli.search_features, ["resume"]).output)["resumed"] == 1
    store.claim("ingest")
    failed = runner.invoke(search_cli.search_features, ["pause"])
    assert failed.exit_code == 1
    assert "--role ingest --stop" in failed.output
    assert store.get_job(job["id"])["status"] == "running"
