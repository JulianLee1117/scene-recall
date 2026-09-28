"""CPU editorial execution must not acquire ingestion's whole-job resource lock."""
from dataclasses import asdict
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from pipeline.lab.resources import configure_editor_process, editor_config
from pipeline.lab.worker import execute_job
from pipeline.tests.test_lab import db, store  # noqa: F401
from pipeline.tests.test_lab_generation import _project, _stages


def test_editor_profile_preserves_model_spaces_and_original_configuration(config, monkeypatch):
    calls = []
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(
        cuda=SimpleNamespace(is_initialized=lambda: False), set_num_threads=calls.append))
    for name in ("CUDA_VISIBLE_DEVICES", "OMP_NUM_THREADS", "MKL_NUM_THREADS",
                 "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS", "TOKENIZERS_PARALLELISM"):
        monkeypatch.setenv(name, "previous")
    config.lab.beat_device = "cuda"
    before = asdict(config)
    configure_editor_process()
    selected = editor_config(config)
    assert os.environ["CUDA_VISIBLE_DEVICES"] == "-1"
    assert os.environ["OMP_NUM_THREADS"] == "2"
    assert os.environ["TOKENIZERS_PARALLELISM"] == "false"
    assert calls == [2]
    assert selected.models is config.models  # No alternative encoder or changed vector profile.
    assert selected.lab.beat_device == "cpu"
    assert asdict(config) == before
    assert {**asdict(selected.lab), "beat_device": "cuda"} == before["lab"]


def test_editor_profile_refuses_an_already_initialized_gpu(monkeypatch):
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(cuda=SimpleNamespace(is_initialized=lambda: True)))
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "0")
    with pytest.raises(RuntimeError, match="fresh process"):
        configure_editor_process()
    assert os.environ["CUDA_VISIBLE_DEVICES"] == "0"


@pytest.mark.parametrize("role,expected", [("editor", "completed"), ("all", "failed")])
def test_generation_overlaps_an_ingestion_lock_only_in_the_cpu_editor(
    config, store, db, tmp_path, monkeypatch, role, expected,
):
    from pipeline.ingest.locks import global_ingest_lock
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "-1")
    project = _project(store, db, tmp_path)
    store.enqueue("generate", project["id"], project["revision"], generate={"mode": "regenerate"})
    job = store.claim()
    def hosted_stage(*_args):
        # Model/network stages must allow an independent publication to proceed.
        from pipeline.lab.index_snapshot import publication_read
        with publication_read(db, timeout=.1):
            pass
    calls = _stages(monkeypatch, store, project, callback=hosted_stage)
    with global_ingest_lock(config.paths.assets_dir):
        result = execute_job(job, config, db, store, role=role)
    assert result["status"] == expected, result.get("error")
    if role == "editor":
        assert [call["stage"] for call in calls] == ["timing", "plan", "draft"]
        assert result["result"]["applied"] is True
    else:
        assert not calls
        assert "shared resources" in result["error"]


def test_editor_refuses_uninitialized_cpu_policy(config, monkeypatch):
    monkeypatch.delenv("CUDA_VISIBLE_DEVICES", raising=False)
    with pytest.raises(RuntimeError, match="not initialized"):
        editor_config(config)


def test_fresh_editor_hides_cuda_from_the_native_runtime_without_allocating_models():
    # Python's environment mapping alone misses Windows deleting empty values.
    # Real Torch availability is the loader's decision boundary, so exercise it
    # in a fresh process without downloading/loading a model or allocating CUDA.
    script = """
import ctypes, json, os
from pipeline.lab.resources import configure_editor_process
configure_editor_process()
if os.name == 'nt':
    buffer = ctypes.create_unicode_buffer(256)
    ctypes.windll.kernel32.GetEnvironmentVariableW('CUDA_VISIBLE_DEVICES', buffer, len(buffer))
    native_value = buffer.value
else:
    native_value = os.environ.get('CUDA_VISIBLE_DEVICES')
import torch
print(json.dumps({'native_value': native_value, 'available': torch.cuda.is_available(),
                  'count': torch.cuda.device_count(), 'initialized': torch.cuda.is_initialized()}))
"""
    result = subprocess.run([sys.executable, "-c", script], cwd=Path(__file__).resolve().parents[2],
                            check=True, capture_output=True, text=True, timeout=30)
    assert json.loads(result.stdout) == {"native_value": "-1", "available": False, "count": 0, "initialized": False}
