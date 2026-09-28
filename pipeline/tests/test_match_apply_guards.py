"""Modern Keep cannot bypass the exact verified proposal used in its preview."""

from copy import deepcopy
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from pipeline.lab import api
from pipeline.lab.media import MATCH_BOUNDARY_PROFILE
from pipeline.lab.models import ProjectDocument
from pipeline.lab.store import LabStore


@pytest.fixture
def match(config, monkeypatch):
    store = LabStore(config.paths.state_dir)
    store.initialize()
    project = store.create_project("Verified match", "visual-rhymes")
    document = ProjectDocument(clips=[{
        "id": "reference", "unit_id": "a", "film_id": "a-film", "source_start": 1.,
        "source_end": 3., "reference_time": 2.,
    }]).model_dump(mode="json")
    project = store.update_project(project["id"], 1, document)
    job = store.enqueue("match", project["id"], 2, match={"focus": "camera"})
    store.claim()
    identity = "match-" + "a" * 16
    candidate = {
        "id": identity, "outgoing": {**document["clips"][0], "source_start": 1.04, "source_end": 2.04},
        "incoming": {**document["clips"][0], "id": identity, "unit_id": "b", "film_id": "b-film",
                     "source_start": 4., "source_end": 5., "reference_time": 4.},
        "reference_frame_pts": 2., "candidate_frame_pts": 4., "preview_ready": False,
    }
    result = {"reference_clip_id": "reference", "candidates": [candidate]}

    def manifest(doc, *_args, **_kwargs):
        return {"profile": MATCH_BOUNDARY_PROFILE, "clips": deepcopy(doc["clips"]), "fps": 24}

    monkeypatch.setattr(api, "render_manifest", manifest)
    monkeypatch.setattr(api, "validate_sources", lambda *_args, **_kwargs: None)
    app = FastAPI()
    app.include_router(api.router)
    app.state.config, app.state.lab, app.state.db = config, store, None
    with TestClient(app) as client:
        yield config, store, project, job, candidate, result, client, manifest


def write_preview(config, job_id, candidate, manifest, *, changed=False):
    directory = config.paths.assets_dir / "lab" / "renders" / f"{job_id}-{candidate['id']}-proposed"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "output.mp4").write_bytes(b"rendered-preview-fixture")
    document = {"clips": [candidate["outgoing"], candidate["incoming"]]}
    prepared = manifest(document)
    if changed:
        prepared["profile"] = "previous-render-profile"
    (directory / "manifest.json").write_text(json.dumps(prepared), encoding="utf-8")


@pytest.mark.parametrize("state", ["not-ready", "missing-media", "changed-manifest", "verified"])
def test_modern_keep_requires_available_verified_exact_render(match, state):
    config, store, project, job, candidate, result, client, manifest = match
    candidate["preview_ready"] = state != "not-ready"
    if state in {"changed-manifest", "verified"}:
        write_preview(config, job["id"], candidate, manifest, changed=state == "changed-manifest")
    store.finish(job["id"], result=result)
    response = client.post(f"/lab/jobs/{job['id']}/apply-match", json={"base_revision": 2, "candidate_id": candidate["id"]})
    assert response.status_code == (200 if state == "verified" else 409), response.text
    assert store.get_project(project["id"])["revision"] == (3 if state == "verified" else 2)


@pytest.mark.parametrize("wrong_trim", [False, True])
def test_on_demand_preview_authorizes_only_its_original_proposal(match, wrong_trim):
    config, store, project, job, candidate, result, client, manifest = match
    store.finish(job["id"], result=result)
    preview = store.enqueue("match-preview", project["id"], 2, match={
        "candidate": deepcopy(candidate), "document": project["document"], "match_job_id": job["id"],
    })
    store.claim()
    prepared = deepcopy(candidate)
    prepared["preview_ready"] = True
    if wrong_trim:
        prepared["incoming"].update(source_start=4.2, source_end=5.2, reference_time=4.2)
        prepared["candidate_frame_pts"] = 4.2
    store.finish(preview["id"], result={"match_job_id": job["id"], "candidate": prepared})
    write_preview(config, preview["id"], prepared, manifest)
    response = client.post(f"/lab/jobs/{job['id']}/apply-match", json={"base_revision": 2, "candidate_id": candidate["id"]})
    assert response.status_code == (409 if wrong_trim else 200), response.text
    assert store.get_project(project["id"])["revision"] == (2 if wrong_trim else 3)


def test_adjusted_preview_keeps_adjustment_when_snapshot_and_render_agree(match):
    config, store, project, job, candidate, result, client, manifest = match
    store.finish(job["id"], result=result)
    adjusted = deepcopy(candidate)
    adjusted.update(adjusted=True, preview_ready=False, candidate_frame_pts=4.2)
    adjusted["incoming"].update(source_start=4.2, source_end=5.2, reference_time=4.2)
    preview = store.enqueue("match-preview", project["id"], 2, match={
        "candidate": adjusted, "document": project["document"], "match_job_id": job["id"],
    })
    store.claim()
    adjusted["preview_ready"] = True
    store.finish(preview["id"], result={"match_job_id": job["id"], "candidate": adjusted})
    write_preview(config, preview["id"], adjusted, manifest)
    response = client.post(f"/lab/jobs/{preview['id']}/apply-match", json={"base_revision": 2, "candidate_id": candidate["id"]})
    assert response.status_code == 200, response.text
    assert response.json()["document"]["clips"][1]["source_start"] == 4.2


@pytest.mark.parametrize("state", ["not-ready", "missing-media", "changed-manifest", "verified"])
def test_retry_reuses_only_a_current_playable_preview(match, state):
    config, store, project, job, candidate, result, client, manifest = match
    store.finish(job["id"], result=result)
    preview = store.enqueue("match-preview", project["id"], 2, match={
        "candidate": deepcopy(candidate), "document": project["document"], "match_job_id": job["id"],
    })
    store.claim()
    prepared = deepcopy(candidate)
    prepared["preview_ready"] = state != "not-ready"
    store.finish(preview["id"], result={"match_job_id": job["id"], "candidate": prepared})
    if state in {"changed-manifest", "verified"}:
        write_preview(config, preview["id"], prepared, manifest, changed=state == "changed-manifest")
    response = client.post(f"/lab/jobs/{job['id']}/matches/{candidate['id']}/preview")
    assert response.status_code == 200, response.text
    reused = response.json()["id"] == preview["id"]
    assert reused == (state == "verified")
    assert store.get_project(project["id"]) == project


def test_preview_cannot_be_nested_under_another_preview(match):
    _config, store, project, job, candidate, result, client, _manifest = match
    store.finish(job["id"], result=result)
    preview = store.enqueue("match-preview", project["id"], 2, match={
        "candidate": candidate, "document": project["document"], "match_job_id": job["id"],
    })
    store.claim()
    store.finish(preview["id"], result={"match_job_id": job["id"], "candidate": candidate})
    response = client.post(f"/lab/jobs/{preview['id']}/matches/{candidate['id']}/preview")
    assert response.status_code == 422
