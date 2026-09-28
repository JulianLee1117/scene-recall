"""Project deletion is revision-safe and never deletes shared source evidence."""

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from pipeline.lab.api import router
from pipeline.lab.store import ActiveProjectJobs, LabStore, RevisionConflict


@pytest.fixture
def store(tmp_path):
    assets = tmp_path / "assets"
    assets.mkdir()
    result = LabStore(tmp_path, assets)
    result.initialize()
    return result


def test_delete_removes_project_history_and_jobs_but_preserves_shared_evidence(store, tmp_path):
    original = tmp_path / "film.mkv"
    original.write_bytes(b"original film")
    track_path = store.root / "tracks" / "original.mp3"
    track_path.write_bytes(b"original music")
    track = store.add_track("track", "Song", 30, track_path)
    project = store.create_project("Delete this edit", "music-sketch")
    other = store.create_project("Keep this edit", "visual-rhymes")
    document = {**project["document"], "track": {key: track[key] for key in ("id", "name", "duration")}}
    project = store.update_project(project["id"], 1, document)
    finished = store.enqueue("analyze", project["id"], project["revision"])
    store.claim()
    store.finish(finished["id"], result={"message": "Done"})
    cancelled = store.enqueue("render", project["id"], project["revision"])
    store.cancel(cancelled["id"])
    ingestion = store.enqueue("ingest", path=original)
    other_job = store.enqueue("render", other["id"], other["revision"])

    assert store.delete_project(project["id"], project["revision"]) == {"deleted": project["id"], "cleanup_pending": False}
    with store.connection() as con:
        assert con.execute("SELECT COUNT(*) FROM revisions WHERE project_id=?", (project["id"],)).fetchone()[0] == 0
        assert con.execute("SELECT COUNT(*) FROM jobs WHERE project_id=?", (project["id"],)).fetchone()[0] == 0
    with pytest.raises(KeyError):
        store.get_project(project["id"])
    assert store.get_project(other["id"]) == other
    assert store.get_job(other_job["id"])["status"] == "queued"
    assert store.get_job(ingestion["id"], private=True)["snapshot"]["path"] == str(original.resolve())
    assert store.get_track("track") == track
    assert Path(track["path"]).read_bytes() == b"original music"
    assert original.read_bytes() == b"original film"


def test_stale_delete_does_not_remove_newer_work(store):
    project = store.create_project("Keep my new work", "music-sketch")
    saved = store.update_project(project["id"], 1, {**project["document"], "brief": "New work"})
    with pytest.raises(RevisionConflict, match="reload before deleting"):
        store.delete_project(project["id"], 1)
    assert store.get_project(project["id"]) == saved
    assert len(store.revisions(project["id"])) == 2


@pytest.mark.parametrize("running", [False, True])
def test_delete_requires_active_jobs_to_stop_and_cancelled_results_cannot_resurrect_project(store, running):
    project = store.create_project("In progress", "music-sketch")
    job = store.enqueue("analyze", project["id"], 1)
    if running:
        store.claim()
    with pytest.raises(ActiveProjectJobs, match="Cancel"):
        store.delete_project(project["id"], 1)
    assert store.list_projects()[0]["active_job_count"] == 1
    store.cancel(job["id"])
    if running:
        # Cancellation is cooperative: a live operation still owns its snapshot.
        with pytest.raises(ActiveProjectJobs):
            store.delete_project(project["id"], 1)
        assert store.finish(job["id"], document=project["document"])["status"] == "cancelled"
    assert store.list_projects()[0]["active_job_count"] == 0
    store.delete_project(project["id"], 1)
    assert store.claim() is None
    assert store.list_projects() == []


def test_delete_api_validates_revision_and_reports_conflicts(store):
    app = FastAPI()
    app.state.lab = store
    app.include_router(router)
    with TestClient(app) as client:
        project = store.create_project("API edit", "music-sketch")
        url = f"/lab/projects/{project['id']}"
        assert client.delete(url).status_code == 422
        assert client.delete(url, params={"base_revision": 0}).status_code == 422
        assert client.delete(url, params={"base_revision": 2}).status_code == 409
        job = store.enqueue("analyze", project["id"], 1)
        blocked = client.delete(url, params={"base_revision": 1})
        assert blocked.status_code == 409
        assert "Cancel" in blocked.json()["detail"]
        store.cancel(job["id"])
        deleted = client.delete(url, params={"base_revision": 1})
        assert deleted.status_code == 200
        assert deleted.json() == {"deleted": project["id"], "cleanup_pending": False}
        assert client.get(url).status_code == 404
        assert client.get(url + "/revisions").status_code == 404
        assert client.get(url + "/jobs").status_code == 404
        assert client.delete(url, params={"base_revision": 1}).status_code == 404
