"""No pretrained model downloads: tiny fake checkpoints, models and images."""

from __future__ import annotations

import json
import sys
from types import SimpleNamespace

import numpy as np
from PIL import Image
import pytest

from pipeline.experiments import dense_geometry as dense
from pipeline.experiments.region_geometry import Box


@pytest.fixture
def checkpoint(tmp_path):
    path = tmp_path / "checkpoint"
    path.mkdir()
    (path / "config.json").write_text(json.dumps({"model_type": "dinov3_vit", "patch_size": 16, "hidden_size": 3, "num_register_tokens": 2}))
    (path / "preprocessor_config.json").write_text(json.dumps({"image_processor_type": "DINOv3ViTImageProcessor", "do_normalize": True}))
    # These bytes are fingerprinted only; fake loaders prevent deserialization.
    (path / "model.safetensors").write_bytes(b"fake immutable test weights")
    return path


def test_checkpoint_profile_changes_with_weights_config_preprocessing_and_resolution(checkpoint):
    first = dense.checkpoint_profile(checkpoint, "test/dinov3", 224)
    assert len(first["weight_revision"]) == 64
    assert first["shadow_only"]
    assert first["dimensions"] == 3
    dense.validate_profile(first)
    assert first["profile_id"] != dense.checkpoint_profile(checkpoint, "test/dinov3", 448)["profile_id"]
    (checkpoint / "model.safetensors").write_bytes(b"different immutable bytes")
    second = dense.checkpoint_profile(checkpoint, "test/dinov3", 224)
    assert first["profile_id"] != second["profile_id"]
    (checkpoint / "preprocessor_config.json").write_text('{"image_processor_type":"DINOv3ViTImageProcessor","image_mean":[0,0,0]}')
    assert second["profile_id"] != dense.checkpoint_profile(checkpoint, "test/dinov3", 224)["profile_id"]


def test_pinned_profile_cannot_be_silently_edited_or_loaded_after_weight_change(checkpoint, monkeypatch):
    profile = dense.checkpoint_profile(checkpoint, "test/dinov3", 224)
    changed = {**profile, "dimensions": 6}
    with pytest.raises(ValueError, match="fingerprint"):
        dense.validate_profile(changed)
    monkeypatch.setattr(dense, "_load_checkpoint", lambda *args: pytest.fail("Changed weights must fail before loading"))
    (checkpoint / "model.safetensors").write_bytes(b"replaced")
    with pytest.raises(ValueError, match="differs"):
        dense.DinoV3LocalAdapter(checkpoint, profile)


def test_sharded_checkpoint_rejects_ambiguous_or_traversing_weight_names(checkpoint):
    index = checkpoint / "model.safetensors.index.json"
    index.write_text(json.dumps({"weight_map": {"key": "weights-01.safetensors"}}))
    with pytest.raises(ValueError, match="ambiguous"):
        dense.checkpoint_profile(checkpoint, "test/dinov3", 224)
    (checkpoint / "model.safetensors").unlink()
    index.write_text(json.dumps({"weight_map": {"key": "../escape.safetensors"}}))
    with pytest.raises(ValueError, match="basenames"):
        dense.checkpoint_profile(checkpoint, "test/dinov3", 224)


def test_adapter_checkpoint_cannot_redirect_the_pinned_base_model(checkpoint):
    (checkpoint / "adapter_config.json").write_text('{"base_model_name_or_path":"remote/model"}')
    with pytest.raises(ValueError, match="redirect"):
        dense.checkpoint_profile(checkpoint, "test/dinov3", 224)


def test_loader_uses_builtin_types_and_local_safe_weights_only(monkeypatch, checkpoint):
    calls = []
    class FakeModel:
        @classmethod
        def from_pretrained(cls, path, **kwargs):
            calls.append(("model", path, kwargs))
            return cls()
        def to(self, device):
            assert device == "cpu"
            return self
        def eval(self):
            pass
    class FakeProcessor:
        @classmethod
        def from_pretrained(cls, path, **kwargs):
            calls.append(("processor", path, kwargs))
            return cls()
    monkeypatch.setitem(sys.modules, "transformers", SimpleNamespace(DINOv3ViTModel=FakeModel, DINOv3ViTImageProcessor=FakeProcessor))
    dense._load_checkpoint(checkpoint, "cpu")
    assert len(calls) == 2
    for _, path, options in calls:
        assert path == str(checkpoint)
        assert options["local_files_only"] is True
        assert options["trust_remote_code"] is False
    assert calls[1][2]["use_safetensors"] is True
    assert calls[1][2]["attn_implementation"] == "eager"


def test_extraction_removes_cls_register_tokens_and_retains_letterbox_mapping(checkpoint, monkeypatch):
    import torch
    profile = dense.checkpoint_profile(checkpoint, "test/dinov3", 224)
    class Processor:
        def __call__(self, *, images, **kwargs):
            assert images.size == (224, 224)
            assert kwargs["do_resize"] is False and kwargs["do_center_crop"] is False
            return {"pixel_values": torch.zeros(1, 3, 224, 224)}
    class Model:
        def __call__(self, **kwargs):
            tokens = torch.zeros(1, 3 + 14 * 14, 3)
            tokens[:, :3, 0] = 99  # CLS + two register tokens must never leak.
            tokens[:, 3:, 1] = 2
            return SimpleNamespace(last_hidden_state=tokens)
    monkeypatch.setattr(dense, "_load_checkpoint", lambda *args: (Processor(), Model()))
    adapter = dense.DinoV3LocalAdapter(checkpoint, profile)
    features, viewport = adapter.extract(Image.new("RGB", (400, 200)))
    assert features.shape == (14, 14, 3)
    np.testing.assert_allclose(features[:, :, 0], 0)
    np.testing.assert_allclose(features[:, :, 1], 1)
    assert viewport == Box(0, .25, 1, .5)


def test_region_sampling_ignores_padding_coordinates_and_matches_corresponding_cells():
    grid = np.zeros((16, 16, 2), dtype=np.float32)
    grid[:, :, 0] = 1
    grid[4:12, :, :] = [0, 1]
    region = dense.region_descriptor(grid, Box(0, .25, 1, .5))
    expected = np.broadcast_to([0, 1], region.shape)
    assert dense.dense_similarity(region, expected) == pytest.approx(1)
    assert dense.dense_similarity(region, np.broadcast_to([1, 0], region.shape)) == pytest.approx(0)


def test_spatial_swap_changes_score_even_with_identical_unordered_features():
    first = np.zeros((8, 8, 2), dtype=np.float32)
    first[:, :4, 0] = 1
    first[:, 4:, 1] = 1
    second = first[:, ::-1].copy()
    assert dense.dense_similarity(first, first) == pytest.approx(1)
    assert dense.dense_similarity(first, second) == pytest.approx(0)


def test_region_resampling_matches_a_pattern_at_a_different_source_position():
    reference = np.zeros((8, 8, 2), np.float32)
    reference[:, :4, 0] = 1
    reference[:, 4:, 1] = 1
    candidate = np.zeros((8, 16, 2), np.float32)
    candidate[:, :8, 1] = 1
    candidate[:, 8:] = reference
    reference_region = dense.region_descriptor(reference, Box(0, 0, 1, 1))
    candidate_region = dense.region_descriptor(candidate, Box(0, 0, 1, 1), Box(.5, 0, .5, 1))
    assert dense.dense_similarity(reference_region, candidate_region) == pytest.approx(1)
    assert dense.dense_similarity(reference_region, dense.region_descriptor(candidate, Box(0, 0, 1, 1))) < 1


@pytest.mark.parametrize("values", [np.zeros((8, 8, 2)), np.full((8, 8, 2), np.nan), np.ones((8, 2)), np.ones((0, 8, 2))])
def test_missing_or_invalid_dense_evidence_is_rejected(values):
    with pytest.raises(ValueError):
        dense.normalize_features(values)


def _bundle(tmp_path, checkpoint):
    profile = dense.checkpoint_profile(checkpoint, "test/dinov3", 224)
    class FakeAdapter:
        def extract(self, image):
            axis = 0 if image.getpixel((0, 0))[0] else 1
            values = np.zeros((14, 14, 3), np.float32)
            values[:, :, axis] = 1
            return values, Box(0, 0, 1, 1)
    adapter = FakeAdapter()
    adapter.profile = profile
    frames = []
    for name, red in (("reference", True), ("same-b", True), ("same-a", True), ("other", False)):
        filename = f"{name}.png"
        Image.new("RGB", (8, 8), (255 if red else 0, 0, 0)).save(tmp_path / filename)
        frames.append({"id": name, "path": filename})
    output = tmp_path / "bundle"
    dense.extract_bundle(adapter, {"schema_version": 1, "kind": "dense_geometry_images", "frames": frames}, output, image_base=tmp_path)
    queries = {"schema_version": 1, "kind": "dense_geometry_queries", "cases": [{"id": "test", "reference": {"frame_id": "reference"}, "candidates": [{"frame_id": name} for name in ("other", "same-b", "same-a")]}]}
    return output, queries


def test_completed_bundle_ranks_separately_and_ties_use_stable_identity(tmp_path, checkpoint):
    bundle, queries = _bundle(tmp_path, checkpoint)
    result = dense.rank_bundle(bundle, queries)
    assert result["production_activation"] is False
    assert [row["frame_id"] for row in result["cases"][0]["ranking"]] == ["same-a", "same-b", "other"]
    assert result["cases"][0]["ranking"][0]["dense_region_cosine"] == pytest.approx(1)
    assert "PE" in result["note"]


def test_bundle_tampering_partial_coverage_and_duplicate_identities_fail(tmp_path, checkpoint):
    bundle, queries = _bundle(tmp_path, checkpoint)
    manifest_path = bundle / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    descriptor = bundle / manifest["frames"][0]["descriptor_file"]
    descriptor.write_bytes(b"corrupted descriptor")
    with pytest.raises(ValueError, match="content changed"):
        dense.rank_bundle(bundle, queries)
    manifest["complete"] = False
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="complete"):
        dense.rank_bundle(bundle, queries)


def test_frame_and_comparison_bounds_fail_before_extraction_or_ranking(tmp_path):
    adapter = SimpleNamespace(extract=lambda *args: pytest.fail("Must not infer outside bounds"))
    with pytest.raises(ValueError, match="200"):
        dense.extract_bundle(adapter, {"schema_version": 1, "kind": "dense_geometry_images", "frames": [{"id": str(i), "path": "missing.png"} for i in range(201)]}, tmp_path / "bundle", image_base=tmp_path)
    assert not (tmp_path / "bundle").exists()


def test_cli_profile_does_not_load_models_and_refuses_overwrite(checkpoint, tmp_path, monkeypatch):
    monkeypatch.setattr(dense, "_load_checkpoint", lambda *args: pytest.fail("Profile creation must not load a model"))
    output = tmp_path / "profile.json"
    args = ["profile", "--checkpoint", str(checkpoint), "--model-id", "test/dinov3", "--output", str(output)]
    assert dense.main(args) == 0
    before = output.read_bytes()
    assert dense.main(args) == 2
    assert output.read_bytes() == before
