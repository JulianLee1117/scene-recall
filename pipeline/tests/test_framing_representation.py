"""A frozen pilot cannot hide candidates, mix sources or repeat completed GPU work."""
from types import SimpleNamespace
import hashlib
import os

import numpy as np
import pytest

from pipeline.experiments import framing_representation as pilot


def test_fixture_is_film_balanced_deterministic_and_reference_units_disjoint():
    films = [f"film-{i}" for i in range(8)]
    rows = [{"film_id": film, "unit_id": f"{film}-u-{i}", "frame_id": f"{film}-f-{i}",
             "timestamp": i * 100} for film in films for i in range(80)]
    selected = pilot.select_fixture(rows, films)
    assert selected == pilot.select_fixture(list(reversed(rows)), films)
    assert len(selected) == 524
    for film in films:
        assert sum(r["film_id"] == film and r["role"] == "candidate" for r in selected) == 64
    assert sum(r["split"] == "tune" for r in selected) == 4
    assert sum(r["split"] == "heldout" for r in selected) == 8
    assert len({r["unit_id"] for r in selected}) == 524
    assert {r["judgment"] for r in selected} == {"unjudged"}
    with pytest.raises(ValueError, match="66"):
        pilot.select_fixture(rows[:65] + [r for r in rows if r["film_id"] != films[0]], films)


def arrays(count):
    return {"pe_final": np.full((count, 6, 6, 1024), 1 / 32, np.float16),
            "pe_block17": np.full((count, 6, 6, 1024), 1 / 32, np.float16),
            "pe_global": np.full((count, 1024), 1 / 32, np.float32)}


def test_full_pool_ranking_can_find_spatial_match_outside_global_shortlist():
    rows = [{"frame_id": f"f-{i}", "film_id": "source" if i == 0 else "candidate-film",
             "role": "reference" if i == 0 else "candidate", "split": "heldout" if i == 0 else "candidate",
             "path": f"{i}.jpg", "timestamp": i} for i in range(20)]
    values = arrays(20)
    # The last candidate is the least similar globally and best spatial match.
    values["pe_global"][19] *= -1
    values["pe_final"][1:19] *= -1
    report = pilot.rank_results({"frames": rows, "manifest_sha256": "test"}, values)
    case = report["results"][0]
    assert case["eligible_candidate_count"] == 19
    assert case["rankings"]["pe_final"][0]["frame_id"] == "f-19"
    assert "f-19" not in {r["frame_id"] for r in case["rankings"]["global_only"]}
    assert report["quality_status"] == "unjudged"


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    out = tmp_path / "run"
    out.mkdir()
    source = tmp_path / "frame.bin"
    source.write_bytes(b"original source")
    stat = source.stat()
    films = [f"film-{i}" for i in range(8)]
    rows = [{"film_id": film, "unit_id": f"{film}-u-{i}", "frame_id": f"{film}-f-{i}",
             "timestamp": i, "path": str(source), "source_size": stat.st_size,
             "source_mtime_ns": stat.st_mtime_ns, "sha256": hashlib.sha256(source.read_bytes()).hexdigest()}
            for film in films for i in range(80)]
    profile = {"fake": "local-weights"}
    monkeypatch.setattr(pilot, "pe_profile", lambda config: profile)
    monkeypatch.setattr(pilot, "code_hashes", lambda: {"runner": "frozen"})
    payload = {"contract": pilot.CONTRACT, "frames": pilot.select_fixture(rows, films),
               "profile": profile, "code_sha256": {"runner": "frozen"},
               "max_bytes": pilot.MAX_BYTES, "max_seconds": pilot.MAX_SECONDS}
    manifest = {**payload, "manifest_sha256": pilot.digest(payload)}
    pilot._write(out / "prepared.json", manifest)
    config = SimpleNamespace(paths=SimpleNamespace(assets_dir=tmp_path / "assets"))
    return out, source, config


def test_same_size_mtime_source_replacement_is_rejected(prepared):
    out, source, config = prepared
    pilot.validate_manifest(out, config)
    stat = source.stat()
    source.write_bytes(b"tampered source")
    os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert source.stat().st_size == stat.st_size
    with pytest.raises(ValueError, match="checksum drift"):
        pilot.validate_manifest(out, config)


def test_missing_source_reference_and_manifest_tampering_fail(prepared):
    out, source, config = prepared
    payload = pilot._read(out / "prepared.json")
    reference = next(r for r in payload["frames"] if r["role"] == "reference")
    reference["path"] = str(source.parent / "missing.jpg")
    pilot._write(out / "prepared.json", payload)
    with pytest.raises(ValueError, match="manifest checksum"):
        pilot.validate_manifest(out, config)
    payload["manifest_sha256"] = pilot.digest({k: v for k, v in payload.items() if k != "manifest_sha256"})
    pilot._write(out / "prepared.json", payload)
    with pytest.raises(FileNotFoundError):
        pilot.validate_manifest(out, config)


def test_resume_skips_completed_batches_and_honors_stop(prepared):
    out, source, config = prepared
    calls = []
    class FakeExtractor:
        def __init__(self, profile):
            pass
        def __call__(self, rows):
            calls.append([r["frame_id"] for r in rows])
            return arrays(len(rows))
    first = pilot.run(out, config, max_batches=1, extractor_factory=FakeExtractor)
    assert first["status"] == "batch_limit"
    assert first["completed_frames"] == 32
    with pytest.raises(ValueError, match="--resume"):
        pilot.run(out, config, extractor_factory=FakeExtractor)
    (out / "STOP").write_text("stop")
    stopped = pilot.run(out, config, resume=True, extractor_factory=FakeExtractor)
    assert stopped["status"] == "stopped"
    assert len(calls) == 1
    (out / "STOP").unlink()
    second = pilot.run(out, config, resume=True, max_batches=1, extractor_factory=FakeExtractor)
    assert second["completed_frames"] == 64
    assert len(calls) == 2
    assert not set(calls[0]) & set(calls[1])
    assert not (out / "results.json").exists()
    # A modified descriptor is never accepted just because the cursor advanced.
    (out / "pe_final-0000.npy").write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="Descriptor checksum"):
        pilot.run(out, config, resume=True, extractor_factory=FakeExtractor)


def test_busy_ingest_lock_performs_no_inference(prepared):
    from pipeline.ingest.locks import global_ingest_lock
    out, source, config = prepared
    def forbidden(profile):
        pytest.fail("No model may load while another ingest owns the slot")
    with global_ingest_lock(config.paths.assets_dir):
        state = pilot.run(out, config, extractor_factory=forbidden)
    assert state["status"] == "ingest_lock_busy"
    assert state["completed_frames"] == 0


def test_changed_model_profile_refuses_resume(prepared, monkeypatch):
    out, source, config = prepared
    monkeypatch.setattr(pilot, "pe_profile", lambda config: {"fake": "different-revision"})
    with pytest.raises(ValueError, match="profile or dependencies changed"):
        pilot.validate_manifest(out, config)


def test_spatial_feasibility_resumes_its_own_chunks_and_cannot_change_profile(prepared, monkeypatch):
    from pipeline.experiments import framing_models
    out, source, config = prepared
    profile = {"spatial": "frozen-checkpoint"}
    payload = pilot._read(out / "prepared.json")
    payload["spatial_profile"] = profile
    payload["manifest_sha256"] = pilot.digest({k: v for k, v in payload.items() if k != "manifest_sha256"})
    pilot._write(out / "prepared.json", payload)
    monkeypatch.setattr(framing_models, "spatial_profile", lambda: profile)
    calls = []
    class Spatial:
        def __init__(self, actual):
            assert actual == profile
        def __call__(self, rows):
            calls.append([r["frame_id"] for r in rows])
            return np.full((len(rows), 6, 6, 384), 1 / np.sqrt(384), np.float16)
    monkeypatch.setattr(framing_models, "SpatialExtractor", Spatial)
    first = pilot.run(out, config, phase="spatial", max_batches=1)
    assert first["spatial_completed_frames"] == 32
    assert first["completed_frames"] == 0
    assert not (out / "pe_final-0000.npy").exists()
    second = pilot.run(out, config, phase="spatial", resume=True, max_batches=1)
    assert second["spatial_completed_frames"] == 64
    assert len(calls) == 2 and not set(calls[0]) & set(calls[1])
    monkeypatch.setattr(framing_models, "spatial_profile", lambda: {"spatial": "changed"})
    with pytest.raises(ValueError, match="PE-Spatial profile changed"):
        pilot.run(out, config, phase="spatial", resume=True)


def test_time_budget_is_cumulative_across_resume(prepared):
    out, source, config = prepared
    manifest = pilot._read(out / "prepared.json")
    pilot._write(out / "progress.json", {"manifest_sha256": manifest["manifest_sha256"],
                "elapsed_seconds": 60., "completed_frames": 0})
    def forbidden(profile):
        pytest.fail("Budget exhaustion must stop before model loading")
    state = pilot.run(out, config, resume=True, max_seconds=60, extractor_factory=forbidden)
    assert state["status"] == "time_budget"
    assert state["completed_frames"] == 0
