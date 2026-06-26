"""Tests for pipeline/ingest/embed.py — written before implementation (TDD).

Tests:
  - embed_images: shape (N, D), float32, L2 norm ≈ 1.0 per row
  - embed_text: shape (N, D), float32, L2 norm ≈ 1.0 per row
  - shot_embedding: 1D vector of dim D, L2 norm ≈ 1.0
  - Model cache: CLIPModel.from_pretrained called only once across two _load_model calls
  - Batching: multiple images/texts handled correctly

All model loading is mocked so no weights are downloaded during CI.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
import torch
from PIL import Image

from pipeline.config import Config
from pipeline.ingest.shots import Shot


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_dummy_image(parent: Path, name: str = "frame.jpg") -> Path:
    """Write a tiny solid-colour JPEG and return its path."""
    img = Image.new("RGB", (64, 64), color=(100, 150, 200))
    p = parent / name
    img.save(p)
    return p


def _fake_loader(embed_dim: int = 1024):
    """Return a fake ``(model, processor, device)`` triple matching ``_load_model`` output.

    The processor returns a dict keyed by 'pixel_values' or 'input_ids'.
    The model returns random tensors of shape ``(batch, embed_dim)``.
    """
    proc = MagicMock()

    def proc_side_effect(*args, **kwargs):
        images = kwargs.get("images") or []
        texts = kwargs.get("text") or []
        if images:
            bs = len(images) if isinstance(images, list) else 1
            return {"pixel_values": torch.zeros(bs, 3, 224, 224)}
        bs = len(texts) if isinstance(texts, list) else 1
        return {"input_ids": torch.zeros(bs, 32, dtype=torch.long)}

    proc.side_effect = proc_side_effect

    model = MagicMock()

    def fake_image_features(**kwargs):
        v = next(iter(kwargs.values()))
        return torch.randn(v.shape[0], embed_dim)

    def fake_text_features(**kwargs):
        v = next(iter(kwargs.values()))
        return torch.randn(v.shape[0], embed_dim)

    model.get_image_features.side_effect = fake_image_features
    model.get_text_features.side_effect = fake_text_features

    return model, proc, torch.device("cpu")


def _make_shot(shot_id: str, n_keyframes: int) -> Shot:
    return Shot(
        shot_id=shot_id,
        t_start=0.0,
        t_end=float(n_keyframes * 2),
        parent_shot_id=None,
        keyframe_times=[float(i) for i in range(n_keyframes)],
    )


# ---------------------------------------------------------------------------
# embed_images
# ---------------------------------------------------------------------------


def test_embed_images_shape(tmp_path: Path, config: Config) -> None:
    """embed_images returns shape (1, 1024) for a single image with PE core L/14."""
    from pipeline.ingest.embed import embed_images

    img = _make_dummy_image(tmp_path)
    fake = _fake_loader(embed_dim=1024)

    with patch("pipeline.ingest.embed._load_model", return_value=fake):
        result = embed_images([img], config)

    assert result.shape == (1, 1024)


def test_embed_images_l2_norm(tmp_path: Path, config: Config) -> None:
    """embed_images output rows have L2 norm ≈ 1.0."""
    from pipeline.ingest.embed import embed_images

    img = _make_dummy_image(tmp_path)
    fake = _fake_loader(embed_dim=1024)

    with patch("pipeline.ingest.embed._load_model", return_value=fake):
        result = embed_images([img], config)

    norms = np.linalg.norm(result, axis=1)
    np.testing.assert_allclose(norms, 1.0, atol=1e-5)


def test_embed_images_batch_shape(tmp_path: Path, config: Config) -> None:
    """embed_images handles a batch of 5 images and returns correct shape."""
    from pipeline.ingest.embed import embed_images

    imgs = [_make_dummy_image(tmp_path, f"f{i}.jpg") for i in range(5)]
    fake = _fake_loader(embed_dim=1024)

    with patch("pipeline.ingest.embed._load_model", return_value=fake):
        result = embed_images(imgs, config)

    assert result.shape == (5, 1024)
    norms = np.linalg.norm(result, axis=1)
    np.testing.assert_allclose(norms, 1.0, atol=1e-5)


def test_embed_images_dtype(tmp_path: Path, config: Config) -> None:
    """embed_images returns float32 array."""
    from pipeline.ingest.embed import embed_images

    img = _make_dummy_image(tmp_path)
    fake = _fake_loader()

    with patch("pipeline.ingest.embed._load_model", return_value=fake):
        result = embed_images([img], config)

    assert result.dtype == np.float32


# ---------------------------------------------------------------------------
# embed_text
# ---------------------------------------------------------------------------


def test_embed_text_shape(config: Config) -> None:
    """embed_text returns shape (N, D) for N texts."""
    from pipeline.ingest.embed import embed_text

    texts = ["a gunfight", "rain on glass", "close-up of a face"]
    fake = _fake_loader(embed_dim=1024)

    with patch("pipeline.ingest.embed._load_model", return_value=fake):
        result = embed_text(texts, config)

    assert result.shape == (3, 1024)


def test_embed_text_l2_norm(config: Config) -> None:
    """embed_text output rows have L2 norm ≈ 1.0."""
    from pipeline.ingest.embed import embed_text

    fake = _fake_loader(embed_dim=1024)

    with patch("pipeline.ingest.embed._load_model", return_value=fake):
        result = embed_text(["some query text"], config)

    norms = np.linalg.norm(result, axis=1)
    np.testing.assert_allclose(norms, 1.0, atol=1e-5)


def test_embed_text_dtype(config: Config) -> None:
    """embed_text returns float32 array."""
    from pipeline.ingest.embed import embed_text

    fake = _fake_loader()

    with patch("pipeline.ingest.embed._load_model", return_value=fake):
        result = embed_text(["hello world"], config)

    assert result.dtype == np.float32


# ---------------------------------------------------------------------------
# shot_embedding
# ---------------------------------------------------------------------------


def test_shot_embedding_shape(tmp_path: Path, config: Config) -> None:
    """shot_embedding returns a 1D vector of the correct dimension."""
    from pipeline.ingest.embed import shot_embedding

    shot = _make_shot("abc_0001", n_keyframes=3)
    kf_dir = tmp_path / "keyframes"
    kf_dir.mkdir()
    for i in range(3):
        _make_dummy_image(kf_dir, f"{shot.shot_id}_{i}.webp")

    fake = _fake_loader(embed_dim=1024)

    with patch("pipeline.ingest.embed._load_model", return_value=fake):
        result = shot_embedding(shot, tmp_path, config)

    assert result.ndim == 1
    assert result.shape == (1024,)


def test_shot_embedding_l2_norm(tmp_path: Path, config: Config) -> None:
    """shot_embedding result has L2 norm ≈ 1.0."""
    from pipeline.ingest.embed import shot_embedding

    shot = _make_shot("abc_0002", n_keyframes=3)
    kf_dir = tmp_path / "keyframes"
    kf_dir.mkdir()
    for i in range(3):
        _make_dummy_image(kf_dir, f"{shot.shot_id}_{i}.webp")

    fake = _fake_loader(embed_dim=1024)

    with patch("pipeline.ingest.embed._load_model", return_value=fake):
        result = shot_embedding(shot, tmp_path, config)

    assert abs(float(np.linalg.norm(result)) - 1.0) < 1e-5


def test_shot_embedding_single_keyframe_equals_frame_embedding(
    tmp_path: Path, config: Config
) -> None:
    """shot_embedding with 1 keyframe equals the normalized frame embedding."""
    from pipeline.ingest.embed import shot_embedding

    shot = _make_shot("abc_0003", n_keyframes=1)
    kf_dir = tmp_path / "keyframes"
    kf_dir.mkdir()
    _make_dummy_image(kf_dir, f"{shot.shot_id}_0.webp")

    # Fixed raw vector: norm=5, normalized = [0.6, 0.8, 0, ...]
    raw = np.zeros((1, 1024), dtype=np.float32)
    raw[0, 0] = 3.0
    raw[0, 1] = 4.0

    model = MagicMock()
    proc = MagicMock()
    proc.side_effect = lambda *a, **k: {"pixel_values": torch.zeros(1, 3, 224, 224)}
    model.get_image_features.return_value = torch.tensor(raw)

    fake = (model, proc, torch.device("cpu"))

    with patch("pipeline.ingest.embed._load_model", return_value=fake):
        result = shot_embedding(shot, tmp_path, config)

    expected = raw[0] / float(np.linalg.norm(raw[0]))
    np.testing.assert_allclose(result, expected, atol=1e-5)


def test_shot_embedding_dtype(tmp_path: Path, config: Config) -> None:
    """shot_embedding returns float32 array."""
    from pipeline.ingest.embed import shot_embedding

    shot = _make_shot("abc_0004", n_keyframes=1)
    kf_dir = tmp_path / "keyframes"
    kf_dir.mkdir()
    _make_dummy_image(kf_dir, f"{shot.shot_id}_0.webp")

    fake = _fake_loader()

    with patch("pipeline.ingest.embed._load_model", return_value=fake):
        result = shot_embedding(shot, tmp_path, config)

    assert result.dtype == np.float32


# ---------------------------------------------------------------------------
# Model cache — singleton
# ---------------------------------------------------------------------------


def test_load_model_cached_on_second_call(config: Config) -> None:
    """_load_model returns the same objects on the second call without re-loading."""
    from pipeline.ingest import embed

    embed._MODEL_CACHE.clear()

    fake_model = MagicMock()
    fake_model.to.return_value = fake_model
    fake_model.eval.return_value = fake_model
    fake_proc = MagicMock()

    with (
        patch("transformers.CLIPModel.from_pretrained", return_value=fake_model) as mock_cls,
        patch("transformers.CLIPProcessor.from_pretrained", return_value=fake_proc),
    ):
        r1 = embed._load_model("pe_core_l14")
        r2 = embed._load_model("pe_core_l14")

    assert mock_cls.call_count == 1, "CLIPModel.from_pretrained must be called exactly once"
    assert r1 is r2, "Second call must return the same cached tuple"

    embed._MODEL_CACHE.clear()


def test_load_model_unknown_name_raises(config: Config) -> None:
    """_load_model raises ValueError for unrecognised model names."""
    from pipeline.ingest import embed

    with pytest.raises(ValueError, match="Unknown"):
        embed._load_model("not_a_real_model")


# ---------------------------------------------------------------------------
# Empty-input guards
# ---------------------------------------------------------------------------


def test_embed_images_empty_paths(config: Config) -> None:
    """embed_images with an empty list returns shape (0, D) float32 without loading model."""
    from pipeline.ingest.embed import embed_images

    result = embed_images([], config)

    assert result.shape == (0, 1024)
    assert result.dtype == np.float32


def test_embed_text_empty_texts(config: Config) -> None:
    """embed_text with an empty list returns shape (0, D) float32 without loading model."""
    from pipeline.ingest.embed import embed_text

    result = embed_text([], config)

    assert result.shape == (0, 1024)
    assert result.dtype == np.float32


def test_shot_embedding_empty_keyframes_raises(tmp_path: Path, config: Config) -> None:
    """shot_embedding raises ValueError when shot.keyframe_times is empty."""
    from pipeline.ingest.embed import shot_embedding

    shot = _make_shot("abc_0005", n_keyframes=0)

    with pytest.raises(ValueError, match="keyframe"):
        shot_embedding(shot, tmp_path, config)
