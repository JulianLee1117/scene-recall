"""Offline exact model contracts and full owned generation/assembly integration."""
import json
import shutil
import uuid

from PIL import Image
import pytest
from pydantic import ValidationError

from pipeline.lab.media import JobCancelled, run_process
from pipeline.transitions import generation, jobs, providers
from pipeline.tests.test_transition_bridges import bridge_pair as bridge_pair, provider_frames as provider_frames
from pipeline.tests.test_transition_generation import job_for
from pipeline.tests.test_transition_generation_api import generation_api as generation_api, request_body, stub_prepare


@pytest.mark.parametrize("model,duration,resolution,credits", [("seedance2_5", 4, "720p", 120),
                                                             ("h3_max", 5, "768p", 40),
                                                             ("wan3", 2, "720p", 20)])
def test_model_quote_preparation_submission_original_and_assembly(config, bridge_pair, monkeypatch,
                                                               model, duration, resolution, credits):
    monkeypatch.setenv(providers.KEY_ENV, "offline-model-test")
    store, parent, source, _films = bridge_pair
    prompt = "Direct a continuous camera move" if model != "wan3" else "A" * 16000
    quoted = providers.quote({"parent_render_id": parent, "model": model, "prompt": prompt}, config, None, store)
    assert quoted["estimated_credits"] == credits and quoted["estimated_usd"] == credits / 100
    assert quoted["request"]["duration"] == duration and quoted["request"]["resolution"] == resolution
    assert quoted["output_shape_guaranteed"] is False
    body = {**quoted["request"], "request_id": str(uuid.uuid4()), "max_credits": credits,
            "price_version": quoted["price_version"], "parent_manifest_sha256": quoted["parent_manifest_sha256"],
            "confirm_spend": True}
    proposal = generation.prepare(body, config, None, store)
    assert proposal["preparation"]["provider"]["model"] == model
    assert proposal["request"]["seed"] == (None if model == "wan3" else 42)
    root = jobs.output_root(config, proposal["job_id"])
    for side in ("a", "b"):
        with Image.open(root / f"provider-{side}.jpg") as image:
            assert image.size == tuple(quoted["input_dimensions"][key] for key in ("width", "height"))
    generated = source.with_name("model-output.mp4")
    run_process(["ffmpeg", "-v", "error", "-nostdin", "-f", "lavfi", "-i", "color=lime:s=192x108:r=24",
                 "-t", str(duration), "-c:v", "libx264", "-threads", "1", str(generated)])
    calls = []
    task = str(uuid.uuid4())
    def request(_self, method, path, payload=None):
        calls.append((method, path, payload))
        if method == "POST":
            assert json.loads((root / "provider-task.json").read_text())["state"] == "submitting"
            return {"id": task, "estimatedCost": {"credits": credits}}
        assert method == "GET"
        return {"id": task, "status": "SUCCEEDED", "cost": {"credits": credits - 1},
                "output": ["https://outputs.runwayml.com/example.mp4?private-token"]}
    monkeypatch.setattr(providers.RunwayClient, "_request", request)
    monkeypatch.setattr(providers.RunwayClient, "download", lambda _self, _url, dest, **_kwargs: shutil.copyfile(generated, dest))
    monkeypatch.setattr(generation, "_wait", lambda cancelled, _deadline: (_ for _ in ()).throw(JobCancelled("cancelled")) if cancelled() else None)
    job = job_for(proposal)
    job["result"] = generation.run(job, config, None, store, lambda _:None, lambda:False)
    job["status"] = "completed"
    payload = calls[0][2]
    common = {"model", "promptText", "promptImage", "duration"}
    expected = common | ({"resolution", "promptExpansionMode", "seed"} if model == "h3_max" else
                         {"ratio", "audio"} if model == "wan3" else {"ratio", "audio", "seed"})
    assert set(payload) == expected
    assert [image["position"] for image in payload["promptImage"]] == ["first", "last"]
    if model == "h3_max":
        assert payload["promptExpansionMode"] == "disabled" and payload["resolution"] == "768p"
    elif model == "wan3":
        assert payload["ratio"] == "auto_720p" and payload["audio"] is False
    assert job["result"]["model"] == model and job["result"]["actual_credits"] == credits - 1
    assert generation.artifact(config, job, "original").read_bytes() == generated.read_bytes()
    manifest = json.loads(generation.artifact(config, job, "manifest").read_text())
    assert manifest["request"]["provenance"]["model"] == model
    assert manifest["request"]["provenance"]["seed"] == (None if model == "wan3" else 42)
    assert manifest["request"]["provenance"]["prompt"] == prompt
    assert manifest["generation"]["download"]["media"]["width"] == 192, "Probe returned media instead of inventing provider dimensions"
    assert generation.artifact(config, job, "video").exists()
    monkeypatch.setattr(providers, "MODEL", "future-default")
    assert generation.receipt(config, job)["model"] == model
    assert "private-token" not in json.dumps(generation.receipt(config, job))
    with pytest.raises(ValueError, match="already started"):
        generation.run(job, config, None, store, lambda _:None, lambda:False)
    assert len(calls) == 2


@pytest.mark.parametrize("model,changes", [("h3_max", {"duration": 4}), ("h3_max", {"resolution": "720p"}),
                                          ("h3_max", {"prompt": "x" * 6001}), ("h3_max", {"prompt_expansion_mode": "unknown"}),
                                          ("wan3", {"duration": 9}), ("wan3", {"seed": 42}),
                                          ("wan3", {"seed": False}), ("wan3", {"prompt_expansion_mode": "disabled"}),
                                          ("wan3", {"referenceVideos": []}), ("wan3", {"negativePrompt": "bad"}),
                                          ("seedance2_5", {"resolution": "768p"}), ("seedance2_5", {"prompt_expansion_mode": "balanced"})])
def test_unsupported_controls_fail_before_any_provider_call(model, changes):
    with pytest.raises(ValidationError):
        providers.RunwayRequest.model_validate({"model": model, "prompt": "Move through", "max_credits": 300, **changes})


@pytest.mark.parametrize("mode", ["disabled", "balanced", "quality"])
def test_h3_expansion_is_explicit_in_payload_and_receipt(tmp_path, provider_frames, monkeypatch, mode):
    client = providers.RunwayClient(key="offline")
    calls = []
    monkeypatch.setattr(client, "_request", lambda method, path, body=None:
                        calls.append(body) or {"id": str(uuid.uuid4()), "estimatedCost": {"credits": 40}})
    receipt = client.submit({"model": "h3_max", "prompt": "Camera push", "prompt_expansion_mode": mode,
                             "max_credits": 40}, *provider_frames, tmp_path / "receipt.json")
    assert calls[0]["promptExpansionMode"] == mode
    assert receipt["request"]["prompt_expansion_mode"] == mode
    assert "ratio" not in calls[0] and "audio" not in calls[0]


def test_registry_and_prices_reject_cross_model_authorization():
    models = {row["id"]: row for row in providers.provider_status()["models"]}
    assert models["wan3"]["capabilities"]["seed"] is False
    assert models["wan3"]["capabilities"]["durations"] == list(range(2, 9))
    assert models["h3_max"]["capabilities"]["prompt_expansion_modes"] == ["disabled", "balanced", "quality"]
    for model in ("h3_max", "wan3"):
        with pytest.raises(ValidationError, match="price changed"):
            generation.GenerateRequest.model_validate({"model": model, "prompt": "Move through", "max_credits": 300,
                "request_id": str(uuid.uuid4()), "parent_render_id": str(uuid.uuid4()), "parent_manifest_sha256": "a" * 64,
                "price_version": providers.PRICE_VERSION, "confirm_spend": True})


def test_legacy_default_request_retries_reuse_stored_job_without_migration(bridge_pair, generation_api, monkeypatch):
    monkeypatch.setattr(generation, "prepare", stub_prepare)
    body = request_body(bridge_pair)
    response = generation_api.post("/lab/transitions/generations", json=body)
    assert response.status_code == 200
    store = bridge_pair[0]
    with store.connection() as con:
        snapshot = json.loads(con.execute("SELECT snapshot FROM jobs WHERE id=?", (body["request_id"],)).fetchone()[0])
        old = snapshot["transition_generation"]["request"]
        old.pop("model"); old.pop("prompt_expansion_mode")
        serialized = json.dumps(snapshot)
        con.execute("UPDATE jobs SET snapshot=?,status='failed' WHERE id=?", (serialized, body["request_id"]))
    monkeypatch.delenv(providers.KEY_ENV, raising=False)
    retried = generation_api.post("/lab/transitions/generations", json=body)
    assert retried.status_code == 200 and retried.json()["id"] == body["request_id"] and retried.json()["status"] == "failed"
    assert store.enqueue_transition_generation(snapshot["transition_generation"])["id"] == body["request_id"]
    with store.connection() as con:
        assert con.execute("SELECT snapshot FROM jobs WHERE id=?", (body["request_id"],)).fetchone()[0] == serialized
    other = {**body, "model": "h3_max", "duration": 5, "resolution": "768p",
             "price_version": providers.model_spec("h3_max")["price_version"]}
    assert generation_api.post("/lab/transitions/generations", json=other).status_code == 409
