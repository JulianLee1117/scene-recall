"""Bounded storage observability, exact reuse, and safe terminal scratch cleanup."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from pipeline.lab import cleanup
from pipeline.lab.media import JobCancelled
from pipeline.transitions import bridges, generation, jobs, providers, storage
from pipeline.transitions.api import router
from pipeline.tests.test_transitions import sources as sources, _render
from pipeline.tests.test_transition_bridges import bridge_pair as bridge_pair, prepare, complete
from pipeline.tests.test_transition_generation import prepared as prepared, job_for


def client(config, store):
    app = FastAPI()
    app.include_router(router)
    app.state.config, app.state.lab, app.state.db = config, store, None
    return TestClient(app)


def test_exact_completed_local_render_is_reused_without_copying_and_reports_real_bytes(config, sources):
    job = _render(config, sources, sources[0]["recipe"]["id"])
    root = jobs.output_root(config, job["id"])
    before = {path.name: (path.stat().st_size, path.stat().st_mtime_ns) for path in root.iterdir()}
    response = client(config, sources[2]).post("/lab/transitions/renders", json=sources[0])
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["id"] == job["id"] and result["reused"] is True
    assert result["storage"] == {"output_bytes": before["output.mp4"][0], "original_bytes": 0,
                                 "retained_bytes": sum(size for size, _mtime in before.values()), "scratch_bytes": 0}
    assert {path.name: (path.stat().st_size, path.stat().st_mtime_ns) for path in root.iterdir()} == before
    assert len(sources[2].transition_renders()) == 1


@pytest.mark.parametrize("name", ["output.mp4", "manifest.json", "frame-a.jpg", "frame-b.jpg"])
def test_missing_or_changed_completed_artifact_enqueues_fresh_local_render(config, sources, name):
    job = _render(config, sources, sources[0]["recipe"]["id"])
    path = jobs.output_root(config, job["id"]) / name
    path.write_bytes(path.read_bytes() + b"changed")
    result = jobs.enqueue(jobs.freeze(sources[0], None), config, sources[2])
    assert result["status"] == "queued" and result["id"] != job["id"]
    assert path.exists(), "Invalid completed history is preserved for inspection"


@pytest.mark.parametrize("mutation", ["cancellation", "result"])
def test_reuse_rechecks_cancellation_and_result_identity_under_store_serialization(config, sources, mutation):
    job = _render(config, sources, sources[0]["recipe"]["id"])
    store = sources[2]
    proposal = jobs.freeze(sources[0], None)
    candidate = store.completed_transition_renders(proposal)[0]
    with store.connection() as con:
        if mutation == "cancellation":
            con.execute("UPDATE jobs SET cancel_requested=1 WHERE id=?", (job["id"],))
        else:
            con.execute("UPDATE jobs SET result='{}' WHERE id=?", (job["id"],))
    replacement = store.enqueue_transition_render(proposal, reusable=candidate)
    assert replacement["id"] != job["id"] and replacement["status"] == "queued"


def test_exact_reuse_is_concurrent_and_changed_settings_do_not_match(config, sources):
    job = _render(config, sources, sources[0]["recipe"]["id"])
    store = sources[2]
    proposal = jobs.freeze(sources[0], None)
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(lambda _: jobs.enqueue(proposal, config, store), range(6)))
    assert {row["id"] for row in results} == {job["id"]}
    changed = deepcopy(sources[0])
    changed["outgoing"]["framing"]["anchor_x"] = .7
    assert jobs.enqueue(jobs.freeze(changed, None), config, store)["id"] != job["id"]


def test_storage_measurement_excludes_scratch_but_retains_originals_and_provider_receipt_partials(tmp_path):
    for name in ("output.mp4", "original.media", "manifest.json", "provider-task.json.partial", "original.partial", "clip-0.native.nut"):
        (tmp_path / name).write_bytes(b"abc")
    assert storage.measure(tmp_path, "transition-generate") == {
        "output_bytes": 3, "original_bytes": 3, "retained_bytes": 12, "scratch_bytes": 6}
    assert len(list(tmp_path.iterdir())) == 6


def test_low_space_guard_cancels_before_probing_and_throttles_checks(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(storage.shutil, "disk_usage", lambda _: calls.append(True) or SimpleNamespace(free=storage.MIN_FREE_BYTES - 1))
    assert storage.Guard(tmp_path, lambda:True)() is True
    assert not calls
    with pytest.raises(ValueError, match="256 MiB"):
        storage.Guard(tmp_path)()
    monkeypatch.setattr(storage.shutil, "disk_usage", lambda _: calls.append(True) or SimpleNamespace(free=storage.MIN_FREE_BYTES * 2))
    guard = storage.Guard(tmp_path)
    assert guard() is False and guard() is False
    assert len(calls) == 2


@pytest.mark.parametrize("failure", [OSError, NotImplementedError])
def test_storage_probe_failure_is_reported_without_masking_cancellation(tmp_path, monkeypatch, caplog, failure):
    monkeypatch.setattr(storage.shutil, "disk_usage", lambda _: (_ for _ in ()).throw(failure("probe unavailable")))
    assert storage.Guard(tmp_path)() is False
    assert "Could not measure free space" in caplog.text


def test_owned_scratch_limit_rejects_without_deleting_retained_original(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "MAX_SCRATCH_BYTES", 5)
    (tmp_path / "clip-0.mp4").write_bytes(b"123456")
    original = tmp_path / "original.media"
    original.write_bytes(b"retained")
    with pytest.raises(ValueError, match="temporary-file budget"):
        storage.Guard(tmp_path)()
    assert original.read_bytes() == b"retained"


def test_low_space_stops_local_preparation_without_partial_encodes(config, sources, monkeypatch):
    store = sources[2]
    store.enqueue_transition_render(jobs.freeze(sources[0], None))
    job = store.claim(role="editor")
    monkeypatch.setattr(storage.shutil, "disk_usage", lambda _: SimpleNamespace(free=0))
    with pytest.raises(ValueError, match="free space"):
        jobs.run(job, config, None, store, lambda _:None, lambda:False)
    assert not list(jobs.output_root(config, job["id"]).iterdir())


def test_low_space_prevents_generation_submission_and_preserves_preparation(config, bridge_pair, prepared, monkeypatch):
    proposal, _generated = prepared
    root = jobs.output_root(config, proposal["job_id"])
    existing = {path.name: path.read_bytes() for path in root.iterdir()}
    monkeypatch.setattr(storage.shutil, "disk_usage", lambda _: SimpleNamespace(free=0))
    monkeypatch.setattr(providers, "RunwayClient", lambda: (_ for _ in ()).throw(AssertionError("provider must not start")))
    with pytest.raises(ValueError, match="free space"):
        generation.run(job_for(proposal), config, None, bridge_pair[0], lambda _:None, lambda:False)
    assert {path.name: path.read_bytes() for path in root.iterdir()} == existing
    with pytest.raises(JobCancelled):
        generation.run(job_for(proposal), config, None, bridge_pair[0], lambda _:None, lambda:True)


@pytest.mark.parametrize("fail", [False, True])
def test_bridge_locked_scratch_cleanup_preserves_success_or_original_failure(config, bridge_pair, monkeypatch, caplog, fail):
    proposal = prepare(config, bridge_pair)
    unlink = Path.unlink
    def locked(path, *args, **kwargs):
        if path.name == "clip-000.mp4":
            raise PermissionError("locked scratch")
        return unlink(path, *args, **kwargs)
    monkeypatch.setattr(Path, "unlink", locked)
    if fail:
        monkeypatch.setattr(bridges, "run_process", lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("original encode failure")))
        with pytest.raises(ValueError, match="original encode failure"):
            complete(config, bridge_pair, proposal)
    else:
        assert complete(config, bridge_pair, proposal)["result"]["duration"] > 0
    assert "Could not remove render intermediate" in caplog.text
    assert (jobs.output_root(config, proposal["job_id"]) / "original.media").exists()


def test_transition_terminal_cleanup_preserves_provider_receipts_and_originals(config, sources):
    store = sources[2]
    queued = store.enqueue_transition_render(jobs.freeze(sources[0], None))
    root = jobs.output_root(config, queued["id"])
    root.mkdir(parents=True)
    names = ("original.partial", "manifest.json.partial", "provider-task.json.partial", "original.media", "input.json.partial")
    for name in names:
        (root / name).write_bytes(b"keep-or-scratch")
    assert not cleanup.collect_garbage(store, apply=True)["files"], "Queued work remains untouched"
    store.cancel(queued["id"])
    result = cleanup.collect_garbage(store, apply=True)
    assert {Path(row["path"]).name for row in result["files"]} == {"original.partial", "manifest.json.partial"}
    assert {path.name for path in root.iterdir()} == {"provider-task.json.partial", "original.media", "input.json.partial"}
