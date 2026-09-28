"""HTTP and durable job boundaries for auditioning before applying a scene pair."""
from copy import deepcopy

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from pipeline.lab import next_scene, next_scene_media
from pipeline.lab.api import router
from pipeline.lab.models import ClipSelection, JobRequest, NextSceneAdjust, NextSceneApply
from pipeline.lab.worker import execute_job
from pipeline.tests.test_lab import db, store  # noqa: F401
from pipeline.tests.test_lab_generation import _project


@pytest.fixture
def pair(config, store, db, tmp_path, monkeypatch):
    project = _project(store, db, tmp_path, filled=(0,))
    for identity, start, end in (("unit-0", 8., 15.), ("incoming", 20., 30.)):
        db.open_table("units").add([{"unit_id": identity, "film_id": "film", "shot_id": identity,
                                    "t_start": start, "t_end": end, "caption": identity,
                                    "img_vec": [1., 0., 0., 0.], "txt_vec": [1., 0., 0., 0.]}])
    options = {"anchor_slot_id": "slot-0", "flexible_cut": True}
    scope = next_scene._anchor_handles(next_scene.validate_request(project["document"], options), db)
    candidate = {
        "id": "next-0123456789abcdef", "cut": 2., "outgoing": deepcopy(project["document"]["clips"][0]),
        "incoming": ClipSelection(id="new-clip", film_id="film", unit_id="incoming", source_start=20, source_end=22).model_dump(mode="json"),
        "incoming_authority": {"film_id": "film", "unit_id": "incoming", "t_start": 20., "t_end": 30.},
        "reason": "A wider image follows the solitary opening", "preview_ready": True,
        "manifest": {"fixture": "verified separately by media tests"},
    }
    queued = store.enqueue("next-scene", project["id"], project["revision"], next_scene=options)
    store.claim()
    job = store.finish(queued["id"], result={"contract": next_scene.CONTRACT, "scope": scope, "candidates": [candidate]})
    path = config.paths.assets_dir / "lab" / "renders" / f"{job['id']}-{candidate['id']}" / "output.mp4"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"Media fidelity has independent decoded-video tests")
    monkeypatch.setattr(next_scene_media, "preview_manifest", lambda *_: dict(candidate["manifest"]))
    app = FastAPI()
    app.include_router(router)
    app.state.config, app.state.db, app.state.lab = config, db, store
    with TestClient(app) as client:
        yield {"project": project, "job": job, "scope": scope, "candidate": candidate,
               "client": client, "path": path, "store": store, "db": db, "config": config}


def _apply(pair, **values):
    return pair["client"].post(f"/lab/jobs/{pair['job']['id']}/apply-next-scene", json={
        "base_revision": pair["project"]["revision"], "candidate_id": pair["candidate"]["id"], **values})


def test_finding_and_previewing_do_not_save_but_apply_is_one_revision(pair):
    store, project = pair["store"], pair["project"]
    assert store.get_project(project["id"]) == project
    response = pair["client"].get(f"/lab/jobs/{pair['job']['id']}/next-scenes/{pair['candidate']['id']}/preview")
    assert response.status_code == 200 and response.content == pair["path"].read_bytes()
    assert store.get_project(project["id"]) == project
    applied = _apply(pair)
    assert applied.status_code == 200, applied.text
    saved = applied.json()
    assert saved["revision"] == project["revision"] + 1
    assert saved["document"]["music_timeline"]["slots"][1]["clip_id"] == "new-clip"
    assert saved["document"]["music_timeline"]["slots"][2] == project["document"]["music_timeline"]["slots"][2]
    assert saved["document"]["analysis"] == project["document"]["analysis"]
    assert _apply(pair, base_revision=saved["revision"]).status_code == 409
    assert store.restore(project["id"], saved["revision"], project["revision"])["document"] == project["document"]


def test_newer_edit_is_never_overwritten_by_old_suggestion(pair):
    project = pair["project"]
    saved = pair["store"].update_project(project["id"], project["revision"], {**project["document"], "brief": "New direction"})
    assert _apply(pair).status_code == 409
    assert _apply(pair, base_revision=saved["revision"]).status_code == 409
    assert pair["store"].get_project(project["id"]) == saved


def test_adjustments_require_the_exact_prepared_preview(pair, monkeypatch):
    assert _apply(pair, source_start=20.5).status_code == 409
    parent = pair["job"]
    response = pair["client"].post(f"/lab/jobs/{parent['id']}/next-scenes/{pair['candidate']['id']}/preview",
                                    json={"source_start": 20.5, "cut_time": 2.5})
    assert response.status_code == 200, response.text
    queued = response.json()
    assert queued["kind"] == "next-scene-preview"
    claimed = pair["store"].claim()
    assert claimed["snapshot"]["next_scene_preview"]["document"] == pair["project"]["document"]

    def render(identity, document, *_):
        path = pair["config"].paths.assets_dir / "lab" / "renders" / identity / "output.mp4"
        path.parent.mkdir(parents=True)
        path.write_bytes(b"Adjusted preview")
        assert document["music_timeline"]["slots"][0]["end"] == 2.5
        return {"manifest": pair["candidate"]["manifest"]}

    monkeypatch.setattr(next_scene_media, "render_preview", render)
    completed = execute_job(claimed, pair["config"], pair["db"], pair["store"])
    assert completed["status"] == "completed", completed
    assert pair["store"].get_project(pair["project"]["id"]) == pair["project"]
    wrong = _apply(pair, source_start=21., cut_time=2.5, preview_job_id=queued["id"])
    assert wrong.status_code == 409
    applied = _apply(pair, source_start=20.5, cut_time=2.5, preview_job_id=queued["id"])
    assert applied.status_code == 200, applied.text
    document = applied.json()["document"]
    assert document["music_timeline"]["slots"][0]["end"] == 2.5
    assert next(clip for clip in document["clips"] if clip["id"] == "new-clip")["source_start"] == 20.5


def test_manual_crop_requires_matching_preview_applies_once_and_undo_restores(pair, monkeypatch):
    crop = {"x": .2, "y": .1, "width": .5, "height": .7}
    draft = {"source_start": 20.5, "cut_time": 2., "crop": crop}
    assert _apply(pair, **draft).status_code == 409
    response = pair["client"].post(f"/lab/jobs/{pair['job']['id']}/next-scenes/{pair['candidate']['id']}/preview", json=draft)
    assert response.status_code == 200, response.text
    queued = response.json()
    claimed = pair["store"].claim()
    assert claimed["snapshot"]["next_scene_preview"]["adjustments"] == draft

    def manifest(document, *_):
        incoming = next(clip for clip in document["clips"] if clip["id"] == "new-clip")
        return {"incoming_crop": incoming["crop"], "source_start": incoming["source_start"]}

    def render(identity, document, *_):
        path = pair["config"].paths.assets_dir / "lab" / "renders" / identity / "output.mp4"
        path.parent.mkdir(parents=True)
        path.write_bytes(b"Cropped preview")
        assert document["clips"][:-1] == pair["project"]["document"]["clips"]
        return {"manifest": manifest(document)}

    monkeypatch.setattr(next_scene_media, "preview_manifest", manifest)
    monkeypatch.setattr(next_scene_media, "render_preview", render)
    completed = execute_job(claimed, pair["config"], pair["db"], pair["store"])
    assert completed["status"] == "completed", completed
    assert completed["result"]["candidate"]["incoming"]["crop"] == crop
    assert _apply(pair, **{**draft, "crop": {**crop, "x": .3}}, preview_job_id=queued["id"]).status_code == 409
    assert _apply(pair, **{**draft, "crop": None}, preview_job_id=queued["id"]).status_code == 409
    applied = _apply(pair, **draft, preview_job_id=queued["id"])
    assert applied.status_code == 200, applied.text
    saved, original = applied.json(), pair["project"]
    assert saved["revision"] == original["revision"] + 1
    assert saved["document"]["clips"][-1]["crop"] == crop
    assert saved["document"]["clips"][:-1] == original["document"]["clips"]
    for index in (0, 2):
        assert saved["document"]["music_timeline"]["slots"][index] == original["document"]["music_timeline"]["slots"][index]
    assert pair["store"].restore(original["id"], saved["revision"], original["revision"])["document"] == original["document"]


def test_explicit_crop_reset_survives_http_and_frozen_job_but_null_times_are_omitted(pair):
    response = pair["client"].post(f"/lab/jobs/{pair['job']['id']}/next-scenes/{pair['candidate']['id']}/preview",
                                    json={"crop": None, "source_start": None, "cut_time": None})
    assert response.status_code == 200, response.text
    frozen = pair["store"].get_job(response.json()["id"], private=True)
    assert frozen["snapshot"]["next_scene_preview"]["adjustments"] == {"crop": None}


@pytest.mark.parametrize("crop", [{"x": -.1}, {"width": 0}, {"x": .8, "width": .3}, {"height": 2}, {"rotation": 90}])
def test_invalid_manual_crop_is_rejected_before_enqueue_or_apply(pair, crop):
    response = pair["client"].post(f"/lab/jobs/{pair['job']['id']}/next-scenes/{pair['candidate']['id']}/preview", json={"crop": crop})
    assert response.status_code == 422 and _apply(pair, crop=crop).status_code == 422
    assert pair["store"].get_project(pair["project"]["id"]) == pair["project"]


def test_stale_preview_uses_the_original_snapshot_but_cannot_apply(pair):
    project = pair["project"]
    changed = pair["store"].update_project(project["id"], project["revision"], {**project["document"], "brief": "Later thought"})
    response = pair["client"].post(f"/lab/jobs/{pair['job']['id']}/next-scenes/{pair['candidate']['id']}/preview", json={})
    assert response.status_code == 200
    frozen = pair["store"].get_job(response.json()["id"], private=True)
    assert frozen["document"] == changed["document"]
    assert frozen["snapshot"]["next_scene_preview"]["document"] == project["document"]
    assert _apply(pair, base_revision=changed["revision"]).status_code == 409


def test_missing_preview_and_changed_source_manifest_block_apply(pair, monkeypatch):
    monkeypatch.setattr(next_scene_media, "preview_manifest", lambda *_: {"fixture": "changed source"})
    assert _apply(pair).status_code == 409
    pair["path"].unlink()
    assert _apply(pair).status_code == 409
    assert pair["store"].get_project(pair["project"]["id"]) == pair["project"]


def test_changed_offered_unit_is_rejected_even_when_film_is_unchanged(pair):
    pair["db"].open_table("units").update(where="unit_id = 'incoming'", values={"t_end": 25.})
    response = _apply(pair)
    assert response.status_code == 422 and "source range changed" in response.text
    assert pair["store"].get_project(pair["project"]["id"]) == pair["project"]


def test_preview_parent_cannot_cross_project_boundary(pair):
    other = pair["store"].create_project("Another edit", "music-sketch")
    with pytest.raises(ValueError, match="in this project"):
        pair["store"].enqueue("next-scene-preview", other["id"], other["revision"], next_scene_preview={
            "job_id": pair["job"]["id"], "candidate_id": pair["candidate"]["id"]})


def test_new_job_keeps_result_separate_and_cancellation_prevents_application(pair, monkeypatch):
    store, project = pair["store"], pair["project"]
    response = pair["client"].post(f"/lab/projects/{project['id']}/jobs", json={
        "kind": "next-scene", "base_revision": project["revision"], "next_scene": {"anchor_slot_id": "slot-0"}})
    assert response.status_code == 200, response.text
    job = store.claim()

    def run(*_):
        store.cancel(job["id"])
        return pair["job"]["result"]

    monkeypatch.setattr(next_scene, "run", run)
    completed = execute_job(job, pair["config"], pair["db"], store)
    assert completed["status"] == "cancelled"
    assert store.get_project(project["id"]) == project


@pytest.mark.parametrize("payload", [
    {"kind": "next-scene"}, {"kind": "draft", "next_scene": {"anchor_slot_id": "a"}},
    {"kind": "next-scene", "slot_ids": ["a"], "next_scene": {"anchor_slot_id": "a"}},
    {"kind": "next-scene", "next_scene": {"anchor_slot_id": "a", "flexible_cut": "true"}},
])
def test_next_scene_request_scope_is_strict(payload):
    with pytest.raises(ValueError):
        JobRequest(base_revision=1, **payload)


def test_adjustments_reject_unknown_controls_and_nonfinite_times():
    with pytest.raises(ValueError):
        NextSceneAdjust(region={})
    with pytest.raises(ValueError):
        NextSceneAdjust(cut_time=float("nan"))
    with pytest.raises(ValueError):
        NextSceneAdjust(crop={"width": float("inf")})


def test_shared_adjustment_payload_preserves_reset_and_excludes_apply_metadata():
    assert NextSceneAdjust().adjustment_payload() == {}
    assert NextSceneAdjust(source_start=None, cut_time=None, crop=None).adjustment_payload() == {"crop": None}
    body = NextSceneApply(base_revision=2, candidate_id="next-0123456789abcdef", preview_job_id="preview", crop={"width": .5})
    assert body.adjustment_payload() == {"crop": {"x": 0., "y": 0., "width": .5, "height": 1.}}
