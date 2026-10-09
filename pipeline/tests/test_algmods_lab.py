"""Alg Mods as a projectless Lab session: contract, frozen source identity, durable jobs, real render."""
from copy import deepcopy
import json
import shutil

import numpy as np

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from pydantic import ValidationError

from pipeline.algmods import jobs
from pipeline.algmods.api import router
from pipeline.algmods.contracts import MODS_VERSION, Dots, Mosaic, Quadtree, RenderRequest, Stripes, catalog, render_params
from pipeline.algmods import tiles as tile_index
from pipeline.ingest.probe import _content_hash
from pipeline.lab.job_roles import role_for_kind
from pipeline.lab.media import run_process
from pipeline.lab.registry import EXPERIMENTS
from pipeline.lab.store import LabStore


def test_catalog_lists_every_treatment_with_defaults_and_controls_that_map_to_fields():
    data = catalog()
    assert data["version"] == MODS_VERSION
    assert [row["id"] for row in data["treatments"]] == ["dots", "stripes", "mosaic", "quadtree"]
    dots = data["treatments"][0]
    assert [style["id"] for style in dots["styles"]] == ["vivid", "pastel"]
    assert dots["styles"][1]["defaults"]["size_jitter"] == 0
    for row, model in zip(data["treatments"], (Dots, Stripes, Mosaic, Quadtree)):
        assert {control["key"].split(".")[0] for control in row["controls"]} <= set(model.model_fields)
        assert model.model_validate(row["defaults"]).kind == row["id"]
    assert role_for_kind(jobs.KIND) == "editor"
    assert any(row["id"] == "alg-mods" and row["route"] == "/lab/alg-mods" and row["persistence"] == "session" for row in EXPERIMENTS)


def test_contract_bounds_the_window_and_the_treatment():
    with pytest.raises(ValidationError):
        RenderRequest(source={"film_id": "f", "source_start": 0, "source_end": 13})
    with pytest.raises(ValidationError):
        RenderRequest(source={"film_id": "f", "source_start": 0, "source_end": 1}, treatment={"kind": "dots", "density": 50000})
    with pytest.raises(ValidationError):
        RenderRequest(source={"film_id": "f", "source_start": 0, "source_end": 1}, treatment={"kind": "dots", "unknown": 1})
    with pytest.raises(ValidationError):
        RenderRequest(source={"film_id": "f", "source_start": 0, "source_end": 1}, treatment={"kind": "smear"})
    mod, params = render_params({"kind": "dots", "style": "pastel", "fill": False, "protect_subject": True, "grow_in": 7})
    assert mod == "dots" and params["fill"] == "0" and params["protect"] == "subject" and params["fade_in"] == 7
    mod, params = render_params({"kind": "stripes", "direction": "fan", "whole_frame": True})
    assert mod == "stripes" and params["vanish"] == "0.5,0.5" and "direction" not in params and params["whole_frame"] == "1"
    mod, params = render_params({"kind": "quadtree", "mode": "hold"})
    assert mod == "quadtree" and params["mode"] == "hold"


@pytest.fixture
def source(config, monkeypatch):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("FFmpeg is required for Alg Mods rendering")
    pytest.importorskip("cv2", reason="feature dots need OpenCV (the algmods extra)")
    root = config.paths.films_dir
    root.mkdir(parents=True)
    path = root / "lights.mkv"
    # Moving noise: texture everywhere, so the tracker has something to seed and follow.
    run_process(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-f", "lavfi",
                 "-i", "nullsrc=s=192x108:r=30,geq=random(1)*255:random(2)*255:random(3)*255", "-t", "1.5",
                 "-c:v", "libx264", "-threads", "1", str(path)])
    identity = _content_hash(path)
    film = {"film_id": identity, "path": str(path), "duration": 1.5, "title": "Lights"}
    monkeypatch.setattr(jobs, "resolve_film", lambda _db, film_id: film if film_id == identity else (_ for _ in ()).throw(ValueError("missing")))
    monkeypatch.setattr(jobs, "_subject_detection_available", lambda: False)
    from pipeline.algmods import render as renderer
    monkeypatch.setattr(renderer, "OUT_W", 90)
    monkeypatch.setattr(renderer, "OUT_H", 160)
    monkeypatch.setattr(renderer, "WORK_H", 160)
    request = RenderRequest(source={"film_id": identity, "source_start": .2, "source_end": 1.0},
                            treatment={"kind": "dots", "style": "vivid", "density": 6000, "radius_frac": .02}).model_dump(mode="json")
    store = LabStore(config.paths.state_dir, config.paths.assets_dir)
    store.initialize()
    return request, film, store


def test_freeze_pins_the_source_and_refuses_a_window_past_the_film(source):
    request, film, _store = source
    proposal = jobs.freeze(request, None)
    assert proposal["version"] == MODS_VERSION and proposal["source"]["film_id"] == film["film_id"]
    too_long = deepcopy(request)
    too_long["source"]["source_end"] = 1.6
    with pytest.raises(ValueError, match="beyond"):
        jobs.freeze(too_long, None)
    with pytest.raises(ValueError):
        jobs.freeze({**request, "source": {**request["source"], "film_id": "nope"}}, None)


def test_render_runs_on_the_editor_worker_verifies_and_reuses_exact_requests(config, source):
    request, _film, store = source
    proposal = jobs.freeze(request, None)
    job = jobs.enqueue(proposal, config, store)
    assert job["status"] == "queued" and job["worker_role"] == "editor"
    assert jobs.enqueue(proposal, config, store)["id"] == job["id"]              # queued twins collapse
    claimed = store.claim(role="editor")
    assert claimed["id"] == job["id"]
    steps = []
    result = jobs.run(claimed, config, None, store, steps.append, lambda: False)
    store.finish(job["id"], result=result)
    assert result["frames"] == 24 and result["fps"] == 30 and result["subject_detection"] is False
    assert result["side_by_side"] is False and result["width"] == 90 and result["height"] == 160
    assert any(step.startswith("dots") for step in steps)
    saved = store.get_job(job["id"], private=True)
    root = jobs.output_root(config, job["id"])
    assert {path.name for path in root.iterdir()} == {"output.mp4", "manifest.json"}
    manifest = json.loads((root / "manifest.json").read_text())
    assert manifest["request"] == request and manifest["audio"] == "muted"
    for kind in ("video", "manifest"):
        assert jobs.artifact(config, saved, kind).is_file()
    # The exact request is served from the verified completed job, not rendered twice.
    again = jobs.enqueue(jobs.freeze(request, None), config, store)
    assert again["id"] == job["id"] and again["status"] == "completed"
    # A different treatment is a new job, and the other treatments render through the same path.
    other = deepcopy(request)
    other["treatment"]["style"] = "pastel"
    pastel = jobs.enqueue(jobs.freeze(other, None), config, store)
    assert pastel["id"] != job["id"]
    for treatment in (other["treatment"], {"kind": "stripes", "count": 8, "max_lag": 4, "whole_frame": True}, {"kind": "quadtree", "mode": "hold"}):
        queued = store.enqueue_algmods_render(jobs.freeze({**request, "treatment": treatment}, None))
        claimed = store.claim(role="editor")
        assert claimed["id"] == queued["id"]
        result = jobs.run(claimed, config, None, store, lambda _: None, lambda: False)
        store.finish(queued["id"], result=result)
        assert result["treatment"]["kind"] == treatment["kind"] and result["frames"] == 24


def test_cancellation_stops_the_render_and_leaves_no_partial(config, source):
    from pipeline.lab.media import JobCancelled
    request, _film, store = source
    job = store.enqueue_algmods_render(jobs.freeze(request, None))
    claimed = store.claim(role="editor")
    calls = {"n": 0}

    def cancelled():
        calls["n"] += 1
        return calls["n"] > 1
    with pytest.raises(JobCancelled):
        jobs.run(claimed, config, None, store, lambda _: None, cancelled)
    assert not (jobs.output_root(config, job["id"]) / "output.partial.mp4").exists()


def test_api_queues_lists_and_serves_renders(config, source):
    request, _film, store = source
    app = FastAPI()
    app.include_router(router)
    app.state.config, app.state.db, app.state.lab = config, None, store
    client = TestClient(app)
    assert client.get("/lab/alg-mods/catalog").json()["version"] == MODS_VERSION
    bad = client.post("/lab/alg-mods/renders", json={**request, "source": {**request["source"], "source_end": 20}})
    assert bad.status_code == 422
    queued = client.post("/lab/alg-mods/renders", json=request)
    assert queued.status_code == 200
    body = queued.json()
    assert body["status"] == "queued" and body["source_title"] == "Lights" and body["request"] == request and body["reused"] is False
    assert client.get("/lab/alg-mods/renders").json()["renders"][0]["id"] == body["id"]
    assert client.get(f"/lab/alg-mods/renders/{body['id']}/video").status_code == 409     # not ready
    claimed = store.claim(role="editor")
    store.finish(body["id"], result=jobs.run(claimed, config, None, store, lambda _: None, lambda: False))
    video = client.get(f"/lab/alg-mods/renders/{body['id']}/video")
    assert video.status_code == 200 and video.headers["content-type"] == "video/mp4"
    receipt = client.get(f"/lab/alg-mods/renders/{body['id']}/manifest").json()
    assert receipt["job_id"] == body["id"]
    assert client.post("/lab/alg-mods/renders", json=request).json()["reused"] is True
    assert client.get("/lab/alg-mods/renders/not-a-job").status_code == 404


def _fake_bank(config, film="f" * 64, units=24):
    """A small tile bank: one fake film with detailed keyframes, built with the real indexer."""
    from PIL import Image
    rng = np.random.RandomState(1)
    root = config.paths.assets_dir / film / "keyframes"
    root.mkdir(parents=True, exist_ok=True)
    for unit in range(units):
        for k in range(3):
            block = rng.randint(0, 255, (5, 8, 3)).astype(np.uint8)
            Image.fromarray(np.kron(block, np.ones((16, 16, 1), np.uint8))).save(root / f"{film}_{unit:04d}_{k}.webp")
    summary = tile_index.build(config.paths.assets_dir, workers=1, progress=lambda _m: None)
    assert summary["tiles"] == units * 3
    return film


def test_mosaic_freezes_its_tile_set_and_renders_from_the_bank(config, source):
    request, _film, store = source
    _fake_bank(config)
    library = {**request, "treatment": {"kind": "mosaic", "source": "library", "columns": 6, "moving": False}}
    job = store.enqueue_algmods_render(jobs.freeze(library, None))
    result = jobs.run(store.claim(role="editor"), config, None, store, lambda _: None, lambda: False)
    store.finish(job["id"], result=result)
    assert result["treatment"]["kind"] == "mosaic" and result["frames"] == 24
    # a search-sourced mosaic pins the shots the search returned, and the worker never searches
    searched = {**request, "treatment": {"kind": "mosaic", "source": "search", "queries": ["hands"], "columns": 6, "moving": False, "reveal": "in-out"}}
    with pytest.raises(ValueError, match="through the API"):
        jobs.freeze(searched, None)
    units = [("f" * 64, unit) for unit in range(24)]
    proposal = jobs.freeze(searched, None, resolve_tiles=lambda queries: units)
    assert len(proposal["tile_units"]) == 24 and proposal["tile_units"][0] == ["f" * 64, 0]
    job = store.enqueue_algmods_render(proposal)
    result = jobs.run(store.claim(role="editor"), config, None, store, lambda _: None, lambda: False)
    store.finish(job["id"], result=result)
    assert result["frames"] == 24
    with pytest.raises(ValueError, match="too few"):
        jobs.freeze(searched, None, resolve_tiles=lambda queries: units[:3])
