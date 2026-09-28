"""Local model identity must fail closed before the pilot imports model code."""
import json

import pytest

from pipeline.experiments import framing_models as model


def test_profile_requires_completed_local_artifacts(tmp_path, monkeypatch):
    monkeypatch.setattr(model, "ROOT", tmp_path)
    with pytest.raises(FileNotFoundError):
        model.spatial_profile()


def test_profile_detects_weight_and_code_drift(tmp_path, monkeypatch):
    monkeypatch.setattr(model, "ROOT", tmp_path)
    names = (*model.CODE_FILES, "core/__init__.py", "core/vision_encoder/__init__.py", model.MODEL + ".pt")
    for name in names:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(name.encode())
    receipt = {"model_revision": model.REVISION, "code_revision": model.CODE_REVISION,
               "files": {name: model.file_hash(tmp_path / name) for name in names}}
    (tmp_path / "download.json").write_text(json.dumps(receipt))
    original = model.spatial_profile()
    assert original["dimensions"] == 384
    assert original["files"] == receipt["files"]
    for name in (model.MODEL + ".pt", "core/vision_encoder/pe.py"):
        path = tmp_path / name
        before = path.read_bytes()
        path.write_bytes(b"different")
        with pytest.raises(ValueError, match="artifact changed"):
            model.spatial_profile()
        path.write_bytes(before)
    assert model.spatial_profile() == original


def test_profile_rejects_unlisted_extra_artifact(tmp_path, monkeypatch):
    monkeypatch.setattr(model, "ROOT", tmp_path)
    (tmp_path / "download.json").write_text(json.dumps({"model_revision": model.REVISION,
        "code_revision": model.CODE_REVISION, "files": {"../unrelated": "bad"}}))
    with pytest.raises(ValueError, match="Incomplete"):
        model.spatial_profile()
