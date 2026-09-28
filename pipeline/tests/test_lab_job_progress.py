"""The editor can inspect durable progress without exposing request snapshots."""

from pipeline.lab.store import LabStore


def test_public_progress_survives_reload_and_keeps_private_request_fields_hidden(tmp_path):
    store = LabStore(tmp_path)
    store.initialize()
    project = store.create_project("Progress", "music-sketch")
    queued = store.enqueue("analyze", project["id"], 1)
    assert queued["progress_steps"] == []
    assert queued["cancel_requested"] is False
    running = store.claim()
    for index in range(83):
        store.progress(running["id"], f"Real step {index}")

    public = LabStore(tmp_path).get_job(running["id"])
    assert public["progress_steps"] == [f"Real step {index}" for index in range(3, 83)]
    assert public["progress"] == "Real step 82"
    assert public["started_at"] == running["started_at"]
    assert public["created_at"] == queued["created_at"]
    assert {"snapshot", "document", "path_key", "log"}.isdisjoint(public)
    assert store.project_jobs(project["id"])[0]["progress_steps"] == public["progress_steps"]

    stopping = store.cancel(running["id"])
    assert stopping["cancel_requested"] is True
    assert stopping["status"] == "running"
    finished = store.finish(running["id"])
    assert finished["status"] == "cancelled"
    assert finished["finished_at"] >= finished["started_at"]
    assert finished["progress_steps"] == public["progress_steps"]


def test_private_worker_view_still_retains_the_request_and_log(tmp_path):
    store = LabStore(tmp_path)
    store.initialize()
    project = store.create_project("Progress", "music-sketch")
    job = store.enqueue("analyze", project["id"], 1)
    store.progress(job["id"], "Waiting")
    private = store.get_job(job["id"], private=True)
    assert private["document"] == project["document"]
    assert private["log"] == ["Waiting"]
    assert "progress_steps" not in private
