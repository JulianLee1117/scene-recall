"""Idle search warmup yields to work and never owns publication during inference."""

from contextlib import contextmanager
from dataclasses import replace
import threading
import time
from types import SimpleNamespace

import lancedb
import pytest

from pipeline.lab import search_warmup, worker
from pipeline.lab.store import LabStore
from pipeline.lab.worker_control import request_worker_stop
from pipeline.lab.worker_runtime import RELOAD_EXIT_CODE


@pytest.fixture
def idle(config, tmp_path, monkeypatch):
    # Establish the real editor policy before importing encoder modules. Their
    # functions are replaced below; no model is loaded or inference performed.
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "-1")
    from pipeline.index import text_features, writer
    from pipeline.ingest import embed, text_embed
    from pipeline.lab import index_snapshot

    config = replace(config, lab=replace(config.lab, beat_device="cuda"))
    db = lancedb.connect(str(tmp_path / "warmup-index"))
    db.create_table("frames", data=[{"frame": 1}])
    state = SimpleNamespace(config=config, db=db, now=0.0, text_ready=True,
                            calls=[], readiness=[], timeouts=[], callbacks={}, published=[])
    monkeypatch.setattr(search_warmup, "time", SimpleNamespace(
        monotonic=lambda: state.now, perf_counter=time.perf_counter))

    def ready(cfg, connection):
        assert writer._PUBLICATION_LOCK.locked(), "readiness must be read under publication exclusion"
        state.readiness.append((cfg, connection))
        return object() if state.text_ready else None

    original_read = index_snapshot.publication_read

    @contextmanager
    def read(connection, *, timeout):
        state.timeouts.append(timeout)
        with original_read(connection, timeout=timeout):
            yield

    def encode(kind, query, cfg):
        state.calls.append((kind, query, cfg))
        callback = state.callbacks.get(kind)
        return callback() if callback else None

    monkeypatch.setattr(text_features, "resolve_ready_text_profile", ready)
    monkeypatch.setattr(index_snapshot, "publication_read", read)
    monkeypatch.setattr(embed, "embed_text", lambda query, cfg: encode("visual", query, cfg))
    monkeypatch.setattr(text_embed, "embed_semantic_query", lambda query, cfg: encode("text", query, cfg))
    return state


def _queue(config, kind="draft", identity="foreground", *, cancelled=False):
    store = LabStore(config.paths.state_dir)
    store.initialize()
    with store.connection() as con:
        con.execute("INSERT INTO jobs (id,kind,status,snapshot,created_at,cancel_requested) VALUES (?,?,?,?,?,?)",
                    (identity, kind, "queued", "{}", 100.0, int(cancelled)))
    return store


def _stop_after_idle(monkeypatch):
    stop = threading.Event()
    waits = []

    def wait(seconds):
        waits.append(seconds)
        stop.set()
        return True

    monkeypatch.setattr(stop, "wait", wait)
    return stop, waits


def test_ready_models_warm_once_using_the_configured_profiles_and_cpu_policy(idle):
    warmup = search_warmup.EditorSearchWarmup()
    warmup.step(idle.config, idle.db, lambda: False)
    idle.now = 120
    warmup.step(idle.config, idle.db, lambda: False)
    assert [(kind, query) for kind, query, _ in idle.calls] == [("visual", ["warmup"]), ("text", "warmup")]
    for _, _, cfg in idle.calls:
        assert cfg.models is idle.config.models
        assert cfg.retrieval is idle.config.retrieval
        assert cfg.paths is idle.config.paths
        assert cfg.lab.beat_device == "cpu"
    assert idle.config.lab.beat_device == "cuda", "the caller's configuration stays unchanged"
    assert len(idle.readiness) == 1
    assert idle.timeouts == [0.1]


@pytest.mark.parametrize("cuda_mask", [None, "", "0"])
def test_warmup_refuses_to_bypass_editor_cpu_initialization(idle, monkeypatch, cuda_mask):
    if cuda_mask is None:
        monkeypatch.delenv("CUDA_VISIBLE_DEVICES", raising=False)
    else:
        monkeypatch.setenv("CUDA_VISIBLE_DEVICES", cuda_mask)
    with pytest.raises(RuntimeError, match="CPU resource profile"):
        search_warmup.EditorSearchWarmup().step(idle.config, idle.db, lambda: False)
    assert idle.calls == idle.readiness == idle.timeouts == []


def test_stop_or_queued_work_before_readiness_does_no_model_or_index_work(idle):
    search_warmup.EditorSearchWarmup().step(idle.config, idle.db, lambda: True)
    assert idle.calls == idle.readiness == idle.timeouts == []


def test_both_publication_locks_are_available_during_each_encoder_call(idle, capsys):
    from pipeline.index import writer

    def publish(kind):
        acquired = writer._PUBLICATION_LOCK.acquire(timeout=0.2)
        assert acquired, "warmup held the in-process publication lock during inference"
        try:
            with writer._database_write_lock(idle.db).acquire(timeout=0.2):
                idle.db.create_table(f"published_{kind}", data=[{"value": 1}])
                idle.published.append(kind)
        finally:
            writer._PUBLICATION_LOCK.release()

    idle.callbacks = {kind: (lambda kind=kind: publish(kind)) for kind in ("visual", "text")}
    search_warmup.EditorSearchWarmup().step(idle.config, idle.db, lambda: False)
    assert idle.published == ["visual", "text"], "publication must succeed inside the fake inference calls"
    assert "unavailable" not in capsys.readouterr().out


@pytest.mark.parametrize("empty_frames", [False, True], ids=["missing-table", "empty-table"])
def test_empty_library_defers_then_rechecks_after_thirty_seconds(idle, empty_frames):
    idle.text_ready = False
    if empty_frames:
        idle.db.open_table("frames").delete("true")
    else:
        idle.db.drop_table("frames")
    warmup = search_warmup.EditorSearchWarmup()
    warmup.step(idle.config, idle.db, lambda: False)
    assert idle.calls == [] and not warmup.attempted
    if empty_frames:
        idle.db.open_table("frames").add([{"frame": 1}])
    else:
        idle.db.create_table("frames", data=[{"frame": 1}])
    idle.text_ready = True
    idle.now = 29.9
    warmup.step(idle.config, idle.db, lambda: False)
    assert idle.calls == [] and len(idle.readiness) == 1
    idle.now = 30.0
    warmup.step(idle.config, idle.db, lambda: False)
    assert [kind for kind, _, _ in idle.calls] == ["visual", "text"]


def test_partially_ready_library_later_warms_only_the_newly_ready_model(idle):
    idle.text_ready = False
    warmup = search_warmup.EditorSearchWarmup()
    warmup.step(idle.config, idle.db, lambda: False)
    assert [kind for kind, _, _ in idle.calls] == ["visual"]
    idle.text_ready = True
    idle.now = 30
    warmup.step(idle.config, idle.db, lambda: False)
    assert [kind for kind, _, _ in idle.calls] == ["visual", "text"]


def test_publication_contention_defers_without_marking_encoders_attempted(idle, capsys):
    from pipeline.index import writer
    warmup = search_warmup.EditorSearchWarmup()
    with writer._PUBLICATION_LOCK:
        warmup.step(idle.config, idle.db, lambda: False)
    assert not warmup.attempted and not idle.calls
    assert idle.timeouts == [0.1]
    assert "warmup deferred" in capsys.readouterr().out
    idle.now = 30
    warmup.step(idle.config, idle.db, lambda: False)
    assert [kind for kind, _, _ in idle.calls] == ["visual", "text"]


def test_encoder_failures_warn_once_without_repeated_idle_attempts(idle, capsys):
    def fail():
        raise RuntimeError("fixture model unavailable")
    idle.callbacks = {"visual": fail, "text": fail}
    warmup = search_warmup.EditorSearchWarmup()
    warmup.step(idle.config, idle.db, lambda: False)
    idle.now = 60
    warmup.step(idle.config, idle.db, lambda: False)
    output = capsys.readouterr().out
    assert len(idle.calls) == 2
    assert output.count("Visual search warmup unavailable") == 1
    assert output.count("Text search warmup unavailable") == 1


def test_worker_finishes_queued_work_then_warms_only_after_an_empty_claim(idle, monkeypatch):
    monkeypatch.delenv("SCENE_RECALL_SKIP_WARMUP", raising=False)
    store = _queue(idle.config)
    stop, waits = _stop_after_idle(monkeypatch)
    events = []
    original_claim = LabStore.claim

    def claim(ledger, role, **options):
        job = original_claim(ledger, role, **options)
        events.append("claim-job" if job else "claim-empty")
        return job

    def execute(job, _config, _db, ledger, *, role):
        assert role == "editor" and not idle.calls
        events.append("execute")
        ledger.finish(job["id"])

    monkeypatch.setattr(LabStore, "claim", claim)
    monkeypatch.setattr(worker, "execute_job", execute)
    idle.callbacks = {kind: (lambda kind=kind: events.append(kind)) for kind in ("visual", "text")}
    assert worker.run_worker(idle.config, db=idle.db, stop=stop, role="editor") == 0
    assert events == ["claim-job", "execute", "claim-empty", "visual", "text"]
    assert waits == [0.5]
    assert store.get_job("foreground")["status"] == "completed"


@pytest.mark.parametrize(("role", "once", "skip"), [
    ("editor", True, False), ("ingest", False, False), ("all", False, False), ("editor", False, True),
])
def test_worker_once_other_roles_and_explicit_skip_never_construct_warmup(idle, monkeypatch, role, once, skip):
    monkeypatch.setenv("SCENE_RECALL_SKIP_WARMUP", "1" if skip else "0")
    monkeypatch.setattr(search_warmup, "EditorSearchWarmup", lambda: pytest.fail("warmup should be disabled"))
    stop, _ = _stop_after_idle(monkeypatch)
    worker.run_worker(idle.config, db=idle.db, stop=stop, role=role, once=once)
    assert idle.calls == []


def test_editor_job_arriving_after_empty_claim_preempts_both_models(idle, monkeypatch):
    monkeypatch.delenv("SCENE_RECALL_SKIP_WARMUP", raising=False)
    original_claim = LabStore.claim
    stop, _ = _stop_after_idle(monkeypatch)

    def claim(ledger, role, **options):
        job = original_claim(ledger, role, **options)
        assert job is None
        _queue(idle.config)
        return job

    monkeypatch.setattr(LabStore, "claim", claim)
    worker.run_worker(idle.config, db=idle.db, stop=stop, role="editor")
    assert idle.calls == idle.readiness == []
    assert LabStore(idle.config.paths.state_dir).get_job("foreground")["status"] == "queued"


@pytest.mark.parametrize(("kind", "cancelled", "expected"), [
    ("draft", False, ["visual"]),
    ("ingest", False, ["visual", "text"]),
    ("draft", True, ["visual", "text"]),
])
def test_queue_is_rechecked_between_encoders_and_only_active_editor_work_preempts(idle, monkeypatch, kind, cancelled, expected):
    monkeypatch.delenv("SCENE_RECALL_SKIP_WARMUP", raising=False)
    stop, _ = _stop_after_idle(monkeypatch)
    idle.callbacks["visual"] = lambda: _queue(idle.config, kind, cancelled=cancelled)
    worker.run_worker(idle.config, db=idle.db, stop=stop, role="editor")
    assert [model for model, _, _ in idle.calls] == expected
    assert LabStore(idle.config.paths.state_dir).get_job("foreground")["status"] == "queued"


@pytest.mark.parametrize("signal", ["stop", "durable-stop", "reload"])
def test_stop_and_reload_arriving_during_first_encoder_preempt_second(idle, monkeypatch, signal):
    monkeypatch.delenv("SCENE_RECALL_SKIP_WARMUP", raising=False)
    stop = threading.Event()
    changed = [False]
    waits = []
    monkeypatch.setattr(worker, "source_changed", lambda _: changed[0])

    def wait(seconds):
        waits.append(seconds)
        assert len(waits) == 1, "the worker must stop or reload at the next boundary"
        return stop.is_set()

    monkeypatch.setattr(stop, "wait", wait)

    def signal_during_encoder():
        if signal == "stop":
            stop.set()
        elif signal == "durable-stop":
            assert request_worker_stop(LabStore(idle.config.paths.state_dir), role="editor")["requested"] == ["editor"]
        else:
            changed[0] = True

    idle.callbacks["visual"] = signal_during_encoder
    result = worker.run_worker(idle.config, db=idle.db, stop=stop, role="editor", reload_fingerprint="original")
    assert result == (RELOAD_EXIT_CODE if signal == "reload" else 0)
    assert [kind for kind, _, _ in idle.calls] == ["visual"]
