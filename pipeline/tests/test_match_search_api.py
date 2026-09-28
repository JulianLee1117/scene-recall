"""Durable Match search stays projectless and previews only its saved proposals."""

from copy import deepcopy
import hashlib
import json
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from pipeline.lab.models import ProjectDocument
from pipeline.lab.store import LabStore
from pipeline.matching.contracts import SearchRequest

CANDIDATE_ID = "match-" + "a" * 16
PREVIEW_BYTES = b"verified-media-fixture"


@pytest.fixture
def search_state(config):
    store = LabStore(config.paths.state_dir)
    store.initialize()
    project = store.create_project("Existing user edit", "visual-rhymes")
    project = store.update_project(project["id"], 1, {
        **project["document"], "brief": "Keep my existing choices",
        "clips": [{"id": "saved", "film_id": "saved-film", "source_start": 1., "source_end": 4., "locked": True}],
    })
    request = SearchRequest(cohort_id="cohort-test", reference={"unit_id": "shot-a", "time": 3.}).model_dump(mode="json")
    reference = {"unit_id": "shot-a", "film_id": "film-a", "t_start": 1., "t_end": 5., "time": 3.}
    profiles = {"scorer": "exact-pair-test", "subjects": "prepared-subjects", "camera": "prepared-camera"}
    return store, request, reference, profiles


def user_state(store):
    with store.connection() as con:
        return {table: [tuple(row) for row in con.execute(f"SELECT * FROM {table} ORDER BY 1,2")]
                for table in ("projects", "revisions")}


def proposal():
    outgoing = ProjectDocument(clips=[{
        "id": "reference", "unit_id": "shot-a", "film_id": "film-a", "source_start": 2.04,
        "source_end": 3.04, "reference_time": 3.,
    }]).model_dump(mode="json")["clips"][0]
    incoming = {**outgoing, "id": CANDIDATE_ID, "unit_id": "shot-b", "film_id": "film-b",
                "source_start": 6., "source_end": 7., "reference_time": 6.}
    return {"id": CANDIDATE_ID, "outgoing": outgoing, "incoming": incoming,
            "reference_frame_pts": 3., "candidate_frame_pts": 6., "crop": None,
            "preview_ready": False, "evidence": "Position match", "components": {}}


def verified_candidate(candidate):
    return {**deepcopy(candidate), "preview_ready": True,
            "preview_sha256": hashlib.sha256(PREVIEW_BYTES).hexdigest(),
            "boundary_checks": {"proposed": {
                "profile": "boundary-image-mae-v1", "passed": True,
                "checks": [{"source_pts": candidate[field], "within_tolerance": True, "mean_pixel_error": .5}
                           for field in ("reference_frame_pts", "candidate_frame_pts")],
            }}}


def queued_search(state):
    store, request, reference, profiles = state
    return store.enqueue_match_search(request, profiles, reference)


def test_projectless_snapshot_is_durable_and_cannot_edit_projects(search_state):
    store, request, reference, profiles = search_state
    before = user_state(store)
    expected_request, expected_reference = deepcopy(request), deepcopy(reference)
    job = queued_search(search_state)
    request["reference"]["time"] = 4.
    reference["film_id"] = "changed-by-caller"
    reopened = LabStore(store.root.parent)
    private = reopened.get_job(job["id"], private=True)
    assert private["project_id"] is None and private["base_revision"] is None
    assert private["kind"] == "match-search"
    assert private["snapshot"]["match_search"]["request"] == {**expected_request, "profile_id": profiles}
    assert private["snapshot"]["match_search"]["reference"] == expected_reference
    public = reopened.get_job(job["id"])
    assert not {"snapshot", "path_key", "log"} & public.keys()
    store.claim()
    store.finish(job["id"], result={"candidates": [proposal()]})
    assert user_state(store) == before


def test_projectless_progress_and_cancel_are_durable_without_project_changes(search_state):
    store, _request, _reference, _profiles = search_state
    before = user_state(store)
    job = queued_search(search_state)
    store.claim()
    first = {"candidates": [proposal()]}
    store.publish_match_progress(job["id"], first)
    assert store.get_job(job["id"])["result"] == first
    store.cancel(job["id"])
    store.publish_match_progress(job["id"], {"candidates": []})
    assert store.get_job(job["id"])["result"] == first
    assert store.finish(job["id"], result=first)["status"] == "cancelled"
    assert user_state(store) == before


def test_projectless_worker_dispatch_returns_results_without_generation(search_state, config, monkeypatch):
    from pipeline.lab.worker import execute_job
    from pipeline.matching import jobs

    store, _request, _reference, _profiles = search_state
    before = user_state(store)
    job = queued_search(search_state)
    claimed = store.claim()
    seen = []

    def run(actual, _config, _db, actual_store, progress, cancelled):
        seen.append(actual["kind"])
        assert actual_store.path == store.path and not cancelled()
        progress("Ready for preview")
        return ({"candidates": [proposal()]} if actual["kind"] == "match-search" else
                {"search_id": job["id"], "candidate": proposal()})

    monkeypatch.setattr(jobs, "run", run)
    finished = execute_job(claimed, config, MagicMock(), store)
    assert seen == ["match-search"]
    assert finished["status"] == "completed", finished
    assert finished["result"]["candidates"][0]["id"] == CANDIDATE_ID
    preview = store.enqueue_match_search_preview(job["id"], CANDIDATE_ID)
    monkeypatch.setattr(jobs, "preview_run", run)
    finished = execute_job(store.claim(), config, MagicMock(), store)
    assert seen == ["match-search", "match-search-preview"]
    assert finished["id"] == preview["id"] and finished["status"] == "completed"
    assert user_state(store) == before


@pytest.mark.parametrize("status", ["cancelled", "failed", "interrupted"])
def test_preview_rejects_unavailable_parent_even_when_result_was_published(search_state, status):
    store, _request, _reference, _profiles = search_state
    job = queued_search(search_state)
    store.claim()
    store.publish_match_progress(job["id"], {"candidates": [proposal()]})
    if status == "cancelled":
        # The cancellation request must take effect before worker acknowledgement.
        store.cancel(job["id"])
    elif status == "interrupted":
        store.recover_interrupted()
    else:
        store.finish(job["id"], result={"candidates": [proposal()]}, error="Source unavailable")
    with pytest.raises((ValueError, KeyError)):
        store.enqueue_match_search_preview(job["id"], CANDIDATE_ID)


def test_preview_is_bound_to_saved_parent_candidate_and_has_no_project(search_state):
    store, _request, reference, _profiles = search_state
    before = user_state(store)
    parent = queued_search(search_state)
    store.claim()
    saved = proposal()
    store.finish(parent["id"], result={"candidates": [saved]})
    with pytest.raises((ValueError, KeyError)):
        store.enqueue_match_search_preview(parent["id"], "forged-candidate")
    preview = store.enqueue_match_search_preview(parent["id"], saved["id"])
    private = store.get_job(preview["id"], private=True)
    assert private["project_id"] is None and private["base_revision"] is None
    frozen = private["snapshot"]["match_search_preview"]
    assert frozen == {"search_id": parent["id"], "candidate": saved, "reference": reference}
    with pytest.raises((ValueError, KeyError)):
        store.enqueue_match_search_preview(preview["id"], saved["id"])
    assert user_state(store) == before


def test_restart_preserves_projectless_queue_and_does_not_replay_running_search(search_state):
    store, request, reference, profiles = search_state
    before = user_state(store)
    first = queued_search(search_state)
    store.claim()
    second = store.enqueue_match_search({**request, "focus": "camera"}, profiles, reference)
    reopened = LabStore(store.root.parent)
    assert reopened.recover_interrupted() == 1
    assert reopened.get_job(first["id"])["status"] == "interrupted"
    assert reopened.claim()["id"] == second["id"]
    assert reopened.claim() is None
    assert user_state(store) == before


def test_identical_active_search_reuses_job_but_different_request_or_cancel_does_not(search_state):
    store, request, reference, profiles = search_state
    first = queued_search(search_state)
    assert queued_search(search_state)["id"] == first["id"]
    other = store.enqueue_match_search({**request, "focus": "position"}, profiles, reference)
    assert other["id"] != first["id"]
    changed_scorer = store.enqueue_match_search(request, {**profiles, "scorer": "new-engine"}, reference)
    assert changed_scorer["id"] not in {first["id"], other["id"]}
    store.cancel(first["id"])
    assert queued_search(search_state)["id"] != first["id"]


def test_projectless_job_cannot_apply_an_accidental_generated_document(search_state):
    store, _request, _reference, _profiles = search_state
    before = user_state(store)
    job = queued_search(search_state)
    store.claim()
    with pytest.raises(ValueError, match="cannot create or change"):
        store.finish(job["id"], document=ProjectDocument().model_dump(mode="json"))
    assert user_state(store) == before


def test_cancelling_parent_prevents_queued_preview_from_running(search_state):
    store, _request, _reference, _profiles = search_state
    before = user_state(store)
    parent = queued_search(search_state)
    store.claim()
    store.finish(parent["id"], result={"candidates": [proposal()]})
    child = store.enqueue_match_search_preview(parent["id"], CANDIDATE_ID)
    store.cancel(parent["id"])
    assert store.is_cancelled(parent["id"])
    assert store.is_cancelled(child["id"])
    assert store.get_job(child["id"])["status"] == "cancelled"
    assert store.claim() is None
    assert user_state(store) == before


@pytest.fixture
def search_http(search_state, config, monkeypatch):
    from pipeline.lab import api as lab_api
    from pipeline.matching import api, jobs

    store, request, _reference, profiles = search_state
    seen = []

    def unit(_db, identity):
        if identity != "shot-a":
            raise KeyError("Indexed shot not found")
        return {"unit_id": identity, "film_id": "film-a", "t_start": 1., "t_end": 5.}

    monkeypatch.setattr(api.cohort, "unit", unit)
    def film(_db, identity):
        path = config.paths.assets_dir / f"{identity}-private-source-path.mp4"
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_bytes(b"source-fixture")
        return {"path": str(path), "title": "Source film"}

    monkeypatch.setattr(api.cohort, "resolve_film", film)
    monkeypatch.setattr("pipeline.ingest.probe._content_hash", lambda path: path.name.removesuffix("-private-source-path.mp4"))
    engine = ModuleType("pipeline.matching.search")

    def validate(_config, _db, options):
        seen.append(deepcopy(options))
        return profiles

    engine.validate_request = validate
    monkeypatch.setitem(sys.modules, "pipeline.matching.search", engine)

    def manifest(document, *_args, **_kwargs):
        return {"profile": "current-match-render", "clips": deepcopy(document["clips"]), "fps": 24}

    monkeypatch.setattr(jobs, "render_manifest", manifest)
    app = FastAPI()
    app.include_router(api.router)
    app.include_router(lab_api.router)
    app.state.config, app.state.db, app.state.lab = config, MagicMock(), store
    with TestClient(app) as client:
        yield client, store, request, profiles, seen, manifest


def http_search(search_http):
    client, _store, request, *_ = search_http
    response = client.post("/matching/searches", json=request)
    assert response.status_code == 200, response.text
    return response.json()


def finish_search(search_http):
    _client, store, *_ = search_http
    job = http_search(search_http)
    store.claim()
    candidate = proposal()
    store.finish(job["id"], result={"candidates": [candidate]})
    return job, candidate


def write_preview(config, job_id, candidate, manifest, *, stale=False):
    from pipeline.matching.jobs import preview_document, preview_path

    output = preview_path(config, job_id, candidate["id"])
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(PREVIEW_BYTES)
    value = manifest(preview_document(candidate))
    if stale:
        value["profile"] = "previous-render-contract"
    output.with_name("manifest.json").write_text(json.dumps(value), encoding="utf-8")


def test_http_search_freezes_server_resolved_source_and_profiles_without_a_project(search_http):
    client, store, request, profiles, seen, _manifest = search_http
    before = user_state(store)
    response = http_search(search_http)
    assert response["kind"] == "match-search" and response["project_id"] is None
    assert response["request"] == request and seen == [request]
    assert "private-source-path" not in json.dumps(response)
    assert "profile_id" not in response["request"]
    source = response["reference"]
    assert source["film_id"] == "film-a" and source["unit_id"] == "shot-a"
    assert 1 <= source["source_start"] <= 3 < source["source_end"] <= 5
    frozen = store.get_job(response["id"], private=True)["snapshot"]["match_search"]
    assert frozen["reference"] == source and frozen["request"]["profile_id"] == profiles
    assert client.get(f"/matching/searches/{response['id']}").json()["reference"] == source
    assert user_state(store) == before


@pytest.mark.parametrize("change", [
    {"profile_id": {"scorer": "client-supplied"}},
    {"project_id": "existing-project"},
    {"_context": {"skip_validation": True}},
    {"reference": {"unit_id": "shot-a", "time": .9}},
    {"reference": {"unit_id": "shot-a", "time": 5.}},
    {"reference": {"unit_id": "shot-a", "time": 3., "film_id": "forged-source"}},
    {"reference": {"unit_id": "shot-a", "time": "nan"}},
    {"reference": {"unit_id": "shot-a", "time": float("nan")}},
    {"reference": {"unit_id": "shot-a", "time": float("inf")}},
    {"allow_reframing": True},
])
def test_http_rejects_private_fields_and_invalid_source_before_queueing(search_http, change):
    client, store, request, *_ = search_http
    before = user_state(store)
    response = client.post("/matching/searches", content=json.dumps({**request, **change}),
                           headers={"content-type": "application/json"})
    assert response.status_code == 422, response.text
    with store.connection() as con:
        assert con.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
    assert user_state(store) == before


def test_http_projectless_results_cannot_be_applied_to_a_project(search_http):
    client, store, *_ = search_http
    before = user_state(store)
    job, candidate = finish_search(search_http)
    response = client.post(f"/lab/jobs/{job['id']}/apply-match", json={
        "base_revision": 2, "candidate_id": candidate["id"],
    })
    assert response.status_code == 409, response.text
    project = store.list_projects()[0]
    ordinary = store.enqueue("match", project["id"], project["revision"], match={})
    assert client.get(f"/matching/searches/{ordinary['id']}").status_code == 404
    assert client.post(f"/matching/searches/{ordinary['id']}/cancel").status_code == 404
    assert store.get_job(ordinary["id"])["status"] == "queued"
    assert user_state(store) == before


@pytest.mark.parametrize("cache", ["failed", "missing", "stale", "verified"])
def test_http_preview_retry_recovers_invalid_cache_and_reuses_exact_valid_render(search_http, config, cache):
    client, store, _request, _profiles, _seen, manifest = search_http
    before = user_state(store)
    parent, candidate = finish_search(search_http)
    url = f"/matching/searches/{parent['id']}/candidates/{candidate['id']}/preview"
    queued = client.post(url)
    assert queued.status_code == 200, queued.text
    preview = queued.json()
    assert preview["kind"] == "match-search-preview" and preview["project_id"] is None
    assert client.post(url).json()["id"] == preview["id"]  # Idempotent while queued.
    store.claim()
    prepared = verified_candidate(candidate)
    if cache == "failed":
        prepared["preview_ready"] = False
    store.finish(preview["id"], result={"search_id": parent["id"], "candidate": prepared})
    if cache in {"stale", "verified"}:
        write_preview(config, preview["id"], prepared, manifest, stale=cache == "stale")
    playable = client.get(url)
    assert playable.status_code == (200 if cache == "verified" else 409), playable.text
    retried = client.post(url)
    assert retried.status_code == 200, retried.text
    assert (retried.json()["id"] == preview["id"]) == (cache == "verified")
    assert user_state(store) == before


def test_http_child_preview_membership_and_parent_cancellation_gate_every_path(search_http, config):
    client, store, _request, _profiles, _seen, manifest = search_http
    parent, candidate = finish_search(search_http)
    base = f"/matching/searches/{parent['id']}/candidates/{candidate['id']}"
    assert client.post(base.replace(candidate["id"], "forged") + "/preview").status_code == 404
    preview = client.post(base + "/preview").json()
    store.claim()
    prepared = verified_candidate(candidate)
    store.finish(preview["id"], result={"search_id": parent["id"], "candidate": prepared})
    write_preview(config, preview["id"], prepared, manifest)
    child_url = f"/matching/searches/{preview['id']}/candidates/{candidate['id']}/preview"
    assert client.get(child_url).status_code == 200
    assert client.post(child_url).status_code == 409  # No nested preview jobs.
    cancelled = client.post(f"/matching/searches/{parent['id']}/cancel")
    assert cancelled.status_code == 200 and cancelled.json()["result"] is None
    assert cancelled.json()["status"] == "cancelled"
    assert client.get(child_url).status_code == 409
    assert client.get(base + "/preview").status_code == 409
    assert client.post(base + "/preview").status_code == 409
    assert client.get(f"/matching/searches/{preview['id']}").json()["status"] == "cancelled"


def test_http_changed_child_proposal_cannot_play_through_either_route(search_http, config):
    client, store, _request, _profiles, _seen, manifest = search_http
    parent, candidate = finish_search(search_http)
    base = f"/matching/searches/{parent['id']}/candidates/{candidate['id']}/preview"
    preview = client.post(base).json()
    store.claim()
    changed = deepcopy(candidate)
    changed["outgoing"]["source_start"] += .1
    changed = verified_candidate(changed)
    store.finish(preview["id"], result={"search_id": parent["id"], "candidate": changed})
    write_preview(config, preview["id"], changed, manifest)
    assert client.get(base).status_code == 409
    assert client.get(f"/matching/searches/{preview['id']}/candidates/{candidate['id']}/preview").status_code == 409


def test_http_native_frames_remain_inside_server_resolved_shot(search_http, monkeypatch):
    from pipeline.matching import api

    client, store, *_ = search_http
    before = user_state(store)
    calls = []

    def samples(path, start, end, **kwargs):
        calls.append((path, start, end, kwargs))
        return [SimpleNamespace(time=4.875, end=4.916667)]

    monkeypatch.setattr(api.media, "samples", samples)
    response = client.get("/matching/frames", params={"unit_id": "shot-a", "time": 4.9})
    assert response.status_code == 200, response.text
    assert response.json() == {"t_start": 1., "t_end": 5., "frames": [{"time": 4.875, "end": 4.916667}]}
    assert calls[0][1] == pytest.approx(4.3) and calls[0][2] == 5.
    assert calls[0][3] == {"native": True}
    assert client.get("/matching/frames", params={"unit_id": "shot-a", "time": 5.}).status_code == 422
    assert len(calls) == 1
    assert user_state(store) == before


def test_http_running_search_exposes_verified_preview_but_not_nested_preview_work(search_http, config):
    client, store, _request, _profiles, _seen, manifest = search_http
    job = http_search(search_http)
    store.claim()
    candidate = verified_candidate(proposal())
    store.publish_match_progress(job["id"], {"candidates": [candidate]})
    write_preview(config, job["id"], candidate, manifest)
    url = f"/matching/searches/{job['id']}/candidates/{candidate['id']}/preview"
    assert client.get(url).status_code == 200
    assert client.get(f"/matching/searches/{job['id']}").json()["status"] == "running"
    assert client.post(url).status_code == 409
    client.post(f"/matching/searches/{job['id']}/cancel")
    assert client.get(url).status_code == 409


@pytest.mark.parametrize("damage", ["mp4-bytes", "missing-digest", "wrong-digest", "wrong-pts", "failed-boundary", "failed-frame", "source-changed"])
def test_http_cached_preview_requires_unchanged_media_source_and_exact_boundary_proof(search_http, config, monkeypatch, damage):
    from pipeline.matching.jobs import preview_path

    client, store, _request, _profiles, _seen, manifest = search_http
    before = user_state(store)
    parent = http_search(search_http)
    store.claim()
    candidate = verified_candidate(proposal())
    if damage == "missing-digest":
        candidate.pop("preview_sha256")
    elif damage == "wrong-digest":
        candidate["preview_sha256"] = "0" * 64
    elif damage == "wrong-pts":
        candidate["boundary_checks"]["proposed"]["checks"][0]["source_pts"] += .1
    elif damage == "failed-boundary":
        candidate["boundary_checks"]["proposed"]["passed"] = False
    elif damage == "failed-frame":
        candidate["boundary_checks"]["proposed"]["checks"][1]["within_tolerance"] = False
    store.finish(parent["id"], result={"candidates": [candidate]})
    write_preview(config, parent["id"], candidate, manifest)
    if damage == "mp4-bytes":
        preview_path(config, parent["id"], candidate["id"]).write_bytes(b"truncated-or-replaced-video")
    elif damage == "source-changed":
        monkeypatch.setattr("pipeline.ingest.probe._content_hash", lambda _: "different-film-content")
    url = f"/matching/searches/{parent['id']}/candidates/{candidate['id']}/preview"
    assert client.get(url).status_code == 409
    retry = client.post(url)
    assert retry.status_code == 200, retry.text
    assert retry.json()["kind"] == "match-search-preview"
    assert retry.json()["id"] != parent["id"]
    assert user_state(store) == before
