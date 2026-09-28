"""A bounded context pilot reads source evidence without altering the library."""
import hashlib
import json

from PIL import Image
import pytest

from pipeline.context import build
from pipeline.index.writer import create_tables, open_db
from pipeline.ingest.probe import _content_hash
from pipeline.lab import music


@pytest.fixture
def pilot(config, tmp_path, monkeypatch):
    path = tmp_path / "SECRET Film Name.mp4"
    path.write_bytes(b"original film bytes")
    film_id = _content_hash(path)
    db = open_db(config)
    create_tables(db, vector_dim=4)
    db.open_table("films").add([{"film_id": film_id, "title": "SECRET Film Name", "path": str(path),
                                "duration": 60., "fps": 24.}])
    root = config.paths.assets_dir / film_id
    root.mkdir(parents=True)
    units, frames = [], []
    for index in range(20):
        start = index * 3.
        identity = f"unit-{index}"
        image = root / f"frame-{index}.jpg"
        Image.new("RGB", (800, 450), (index * 8, 20, 30)).save(image)
        units.append({"film_id": film_id, "unit_id": identity, "t_start": start, "t_end": start + 3.,
                      "caption": f"A person walks toward a doorway, image {index}."})
        frames.append({"film_id": film_id, "unit_id": identity, "timestamp": start + 1.,
                       "timestamp_source": "indexed-player-seconds", "path": str(image)})
    db.open_table("units").add(units)
    db.open_table("frames").add(frames)
    dialogue = [{"start": float(index * 3), "end": float(index * 3 + 2), "text": f"I will leave after this conversation {index}."}
                for index in range(20)]
    (root / "dialogue.json").write_text(json.dumps(dialogue), encoding="utf-8")
    (root / "dialogue.manifest.json").write_text(json.dumps({"contract_version": 2, "kind": "embedded_text",
                                                          "film_id": film_id, "stream_index": 2}), encoding="utf-8")
    calls = []

    def hosted(config, prompt, schema, *, receipt_path, **kwargs):
        calls.append({"prompt": prompt, "schema": schema, "receipt_path": receipt_path, **kwargs})
        ids = schema["$defs"]["Claim"]["properties"]["evidence_refs"]["items"]["enum"]
        output = {"claims": [{"kind": "narrative", "text": "A departure is discussed while a person approaches a doorway.",
                               "status": "supported", "evidence_refs": [ids[0], next(key for key in ids if key.startswith("dialogue"))]}],
                  "uncertainty": "Speaker identity, unseen motives and what happens after this window are unknown."}
        music.write_json(receipt_path, {"status": "completed", "prompt_hash": music.digest(prompt),
                                      "schema_hash": music.digest(schema), "output": output})
        return output

    monkeypatch.setattr(music, "_hosted_json", hosted)
    plan = {"schema_version": 1, "windows": [{"film_id": film_id, "start": 0., "end": 60.}]}
    return config, db, path, root, plan, calls


def run(pilot, **kwargs):
    config, db, _, _, plan, _ = pilot
    return build.build_plan(plan, config, db, progress=lambda _: None, **kwargs)


def test_dry_run_is_bounded_and_does_not_publish_or_call(pilot):
    config, db, path, root, _, calls = pilot
    before = {name: db.open_table(name).version for name in ("units", "frames", "films")}
    source = path.read_bytes()
    result = run(pilot)
    window = result["windows"][0]
    assert window["status"] == "ready" and window["image_count"] == 16
    assert window["evidence_count"] == 56 and result["new_calls"] == 0 and not calls
    assert not (config.paths.assets_dir / "context").exists()
    assert path.read_bytes() == source
    assert before == {name: db.open_table(name).version for name in before}


def test_late_window_reads_complete_evidence_with_scalar_indexes(pilot, monkeypatch):
    config, db, _, _, plan, calls = pilot
    for name in ("units", "frames"):
        db.open_table(name).create_scalar_index("film_id", index_type="BTREE")
    # The indexed film has twenty earlier rows, but this window has only two.
    # Its result cap must apply after the timestamp predicates.
    monkeypatch.setattr(build, "MAX_ROWS", 3)
    monkeypatch.setattr(build, "MAX_FRAME_ROWS", 3)
    plan["windows"][0].update(start=54., end=60.)
    result = run(pilot)
    window = result["windows"][0]
    assert window["status"] == "ready", window
    assert window["image_count"] == 2 and window["evidence_count"] == 6
    assert not calls


def test_execute_preserves_source_and_reuses_exact_artifact_without_another_request(pilot):
    config, db, path, _, plan, calls = pilot
    original = path.read_bytes()
    first = run(pilot, execute=True, max_calls=1)
    assert first["windows"][0]["status"] == "completed", json.dumps(first, indent=2)
    second = run(pilot, execute=True, max_calls=0)
    assert second["windows"][0]["status"] == "reused", second
    assert second["windows"][0]["artifact_id"] == first["windows"][0]["artifact_id"]
    assert first["new_calls"] == len(calls) == 1 and second["new_calls"] == 0
    assert path.read_bytes() == original
    request = calls[0]
    assert "SECRET Film Name" not in request["prompt"] and plan["windows"][0]["film_id"] not in request["prompt"]
    assert "song" in build.PROMPT and "remembered plot" in build.PROMPT
    assert len(request["images"]) == 16
    for item in request["images"]:
        with Image.open(item["path"]) as image:
            assert image.format == "JPEG" and max(image.size) <= 640
    from pipeline.context.store import ContextStore
    artifact = ContextStore(config.paths.assets_dir).read_artifact(plan["windows"][0]["film_id"], build.PROFILE_ID,
                                                                 first["windows"][0]["artifact_id"])
    assert artifact["legal_clip_authority"] is False
    assert artifact["coverage"] == [{"start": 0., "end": 60.}]


@pytest.mark.parametrize("mutation", ["duplicate", "too_many_windows", "too_many_films", "negative", "reversed", "long", "nan", "extra", "bool"])
def test_invalid_plan_never_reaches_evidence_or_provider(pilot, monkeypatch, mutation):
    plan = pilot[4]
    if mutation == "duplicate":
        plan["windows"] *= 2
    elif mutation == "too_many_windows":
        plan["windows"] = [{"film_id": "a" * 64, "start": float(index), "end": float(index + 1)} for index in range(21)]
    elif mutation == "too_many_films":
        plan["windows"] = [{"film_id": str(index) * 64, "start": 0., "end": 1.} for index in range(4)]
    elif mutation == "extra":
        plan["windows"][0]["instructions"] = "Invent a plot"
    else:
        plan["windows"][0].update({"negative": {"start": -1.}, "reversed": {"end": 0.}, "long": {"end": 181.},
                                   "nan": {"start": float("nan")}, "bool": {"start": False}}[mutation])
    monkeypatch.setattr(build, "_collect", lambda *_: pytest.fail("Invalid plan cannot inspect evidence"))
    with pytest.raises(ValueError):
        run(pilot, execute=True, max_calls=1)
    assert not pilot[5]


def test_strict_budget_and_disjoint_window_resume(pilot):
    plan = pilot[4]
    film_id = plan["windows"][0]["film_id"]
    plan["windows"] = [{"film_id": film_id, "start": 0., "end": 27.}, {"film_id": film_id, "start": 30., "end": 60.}]
    first = run(pilot, execute=True, max_calls=1)
    assert [row["status"] for row in first["windows"]] == ["completed", "budget-exhausted"], first
    second = run(pilot, execute=True, max_calls=1)
    assert [row["status"] for row in second["windows"]] == ["reused", "completed"], second
    assert len(pilot[5]) == 2 and second["new_calls"] == 1


@pytest.mark.parametrize("bad", ["unsupported", "duplicate", "missing"])
def test_bad_claim_references_never_publish_or_retry(pilot, monkeypatch, bad):
    def hosted(*args, **kwargs):
        pilot[5].append(True)
        refs = {"unsupported": ["invented-frame"], "duplicate": ["caption-0", "caption-0"], "missing": []}[bad]
        return {"claims": [{"kind": "narrative", "text": "A claim", "status": "supported", "evidence_refs": refs}],
                "uncertainty": "Sparse evidence"}
    monkeypatch.setattr(music, "_hosted_json", hosted)
    first = run(pilot, execute=True, max_calls=1)
    assert first["windows"][0]["status"] == "error"
    second = run(pilot, execute=True, max_calls=1)
    assert second["windows"][0]["status"] == "previous-attempt" and len(pilot[5]) == 1
    assert not list((pilot[0].paths.assets_dir / "context").rglob("published.json"))


def test_uncertain_provider_attempt_is_not_automatically_repeated(pilot, monkeypatch):
    def fail(*args, **kwargs):
        pilot[5].append(True)
        raise music.MusicUnavailable("Network outcome uncertain")
    monkeypatch.setattr(music, "_hosted_json", fail)
    assert run(pilot, execute=True, max_calls=1)["windows"][0]["status"] == "error"
    assert run(pilot, execute=True, max_calls=1)["windows"][0]["status"] == "previous-attempt"
    assert len(pilot[5]) == 1


def test_completed_receipt_resumes_publication_without_paid_retry(pilot, monkeypatch):
    from pipeline.context.store import ContextStore
    original = ContextStore.publish
    monkeypatch.setattr(ContextStore, "publish", lambda *a, **k: (_ for _ in ()).throw(OSError("Interrupted publication")))
    assert run(pilot, execute=True, max_calls=1)["windows"][0]["status"] == "error"
    monkeypatch.setattr(ContextStore, "publish", original)
    result = run(pilot, execute=True, max_calls=0)
    assert result["windows"][0]["status"] == "completed", result
    assert len(pilot[5]) == 1 and result["new_calls"] == 0


@pytest.mark.parametrize("changed", ["film", "caption", "dialogue", "keyframe"])
def test_source_or_evidence_change_during_call_cannot_publish(pilot, monkeypatch, changed):
    config, db, path, root, _, calls = pilot
    original = music._hosted_json
    def hosted(*args, **kwargs):
        result = original(*args, **kwargs)
        if changed == "film":
            path.write_bytes(b"different raw film")
        elif changed == "caption":
            db.open_table("units").update(where="unit_id = 'unit-0'", values={"caption": "Different evidence"})
        elif changed == "dialogue":
            (root / "dialogue.json").write_text("[]", encoding="utf-8")
        else:
            Image.new("RGB", (80, 45), "red").save(root / "frame-0.jpg")
        return result
    monkeypatch.setattr(music, "_hosted_json", hosted)
    result = run(pilot, execute=True, max_calls=1)
    assert result["windows"][0]["status"] == "error" and "changed" in result["windows"][0]["error"].lower(), result
    assert not list((config.paths.assets_dir / "context").rglob("published.json"))


@pytest.mark.parametrize("bad", ["bad_interval", "not_sorted", "wrong_source", "missing_manifest"])
def test_unusable_timed_dialogue_is_explicit_unknown_not_reconstructed_from_unit_text(pilot, bad):
    config, db, _, root, plan, _ = pilot
    if bad == "missing_manifest":
        (root / "dialogue.manifest.json").unlink()
    elif bad == "wrong_source":
        (root / "dialogue.manifest.json").write_text(json.dumps({"contract_version": 2, "kind": "embedded_text", "film_id": "a" * 64}))
    else:
        rows = json.loads((root / "dialogue.json").read_text())
        if bad == "bad_interval":
            rows[-1]["end"] = -1
        else:
            rows.reverse()
        (root / "dialogue.json").write_text(json.dumps(rows))
    collected = build._collect(build.Window.model_validate(plan["windows"][0]), config, db)
    assert all(row["kind"] != "dialogue" for row in collected["evidence"])
    assert "unavailable" in collected["limits"][0].lower()


def test_sidecar_hash_change_disables_old_timed_dialogue(pilot):
    config, db, path, root, plan, _ = pilot
    sidecar = path.with_suffix(".srt")
    sidecar.write_bytes(b"original subtitle evidence")
    (root / "dialogue.manifest.json").write_text(json.dumps({"contract_version": 2, "kind": "sidecar_srt",
        "filename": sidecar.name, "sha256": hashlib.sha256(sidecar.read_bytes()).hexdigest()}))
    window = build.Window.model_validate(plan["windows"][0])
    assert any(row["kind"] == "dialogue" for row in build._collect(window, config, db)["evidence"])
    sidecar.write_bytes(b"changed")
    assert all(row["kind"] != "dialogue" for row in build._collect(window, config, db)["evidence"])


def test_image_outside_film_asset_directory_is_rejected_before_call(pilot, tmp_path):
    config, db, _, _, _, calls = pilot
    external = tmp_path / "external.jpg"
    Image.new("RGB", (16, 16)).save(external)
    db.open_table("frames").update(where="unit_id = 'unit-0'", values={"path": str(external)})
    result = run(pilot, execute=True, max_calls=1)
    assert result["windows"][0]["status"] == "error" and "escaped" in result["windows"][0]["error"]
    assert not calls


def test_changed_model_or_prompt_has_a_distinct_request_identity(pilot, monkeypatch):
    first = run(pilot)["windows"][0]["request_id"]
    pilot[0].lab.planner_model = "future-model"
    second = run(pilot)["windows"][0]["request_id"]
    assert first != second
    monkeypatch.setattr(build, "PROMPT", build.PROMPT + "Additional limitation.")
    assert run(pilot)["windows"][0]["request_id"] != second


def test_zero_budget_never_calls_provider(pilot):
    result = run(pilot, execute=True, max_calls=0)
    assert result["windows"][0]["status"] == "budget-exhausted" and not pilot[5]


def test_cli_default_is_dry_and_prints_machine_readable_report(pilot, tmp_path, capsys):
    config, _, _, _, plan, calls = pilot
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan))
    report_path = tmp_path / "report.json"
    assert build.main(["--plan", str(path), "--config", str(tmp_path / "config.yaml"), "--report", str(report_path)]) == 0
    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert json.loads(report_path.read_text()) == report
    assert "Context window" in captured.err and not (config.paths.assets_dir / "context").exists()
    assert report["execute"] is False and report["new_calls"] == 0 and not calls
