"""Tests for pipeline/ingest/embed.py — written before implementation (TDD).

Tests:
  - embed_images: shape (N, D), float32, L2 norm ≈ 1.0 per row
  - embed_text: shape (N, D), float32, L2 norm ≈ 1.0 per row
  - shot_embedding: 1D vector of dim D, L2 norm ≈ 1.0
  - Model cache: OpenCLIP model creation happens once across two _load_model calls
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


def _fake_loader(embed_dim: int = 1024) -> MagicMock:
    """Return a fake encoder matching the uniform ``_load_model`` contract."""
    encoder = MagicMock()
    encoder.encode_images.side_effect = lambda images: torch.randn(
        len(images), embed_dim
    )
    encoder.encode_texts.side_effect = lambda texts: torch.randn(
        len(texts), embed_dim
    )
    return encoder


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


def test_embed_pil_images_accepts_in_memory_images(config: Config) -> None:
    """Reference-image search can embed a decoded image without a temp file."""
    from pipeline.ingest.embed import embed_pil_images

    fake = _fake_loader(embed_dim=1024)
    images = [
        Image.new("RGB", (32, 24), color=(20, 40, 60)),
        Image.new("RGB", (32, 24), color=(80, 100, 120)),
    ]

    with patch("pipeline.ingest.embed._load_model", return_value=fake):
        result = embed_pil_images(images, config)

    assert result.shape == (2, 1024)
    np.testing.assert_allclose(
        np.linalg.norm(result, axis=1),
        1.0,
        atol=1e-5,
    )


def test_embed_spatial_images_returns_position_grid(config: Config) -> None:
    """Compatible encoders expose normalized fixed-coordinate patch cells."""
    from pipeline.ingest.embed import embed_spatial_images

    fake = _fake_loader(embed_dim=1024)
    fake.encode_spatial_images.return_value = (
        torch.randn(2, 1024),
        torch.nn.functional.normalize(
            torch.randn(2, 1024, 6, 6),
            p=2,
            dim=1,
        ),
    )
    images = [
        Image.new("RGB", (32, 24), color=(20, 40, 60)),
        Image.new("RGB", (32, 24), color=(80, 100, 120)),
    ]

    with patch("pipeline.ingest.embed._load_model", return_value=fake):
        global_features, spatial_features = embed_spatial_images(
            images,
            config,
        )

    assert global_features.shape == (2, 1024)
    assert spatial_features is not None
    assert spatial_features.shape == (2, 6, 6, 1024)
    np.testing.assert_allclose(
        np.linalg.norm(spatial_features, axis=-1),
        1.0,
        atol=1e-5,
    )


def test_embed_spatial_images_falls_back_to_global_only(config: Config) -> None:
    """An encoder without patch features remains usable for image search."""
    from pipeline.ingest.embed import embed_spatial_images

    fake = _fake_loader(embed_dim=1024)
    fake.encode_spatial_images.return_value = (
        torch.randn(1, 1024),
        None,
    )

    with patch("pipeline.ingest.embed._load_model", return_value=fake):
        global_features, spatial_features = embed_spatial_images(
            [Image.new("RGB", (32, 24))],
            config,
        )

    assert global_features.shape == (1, 1024)
    assert spatial_features is None


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


def test_pool_image_embeddings_reuses_frame_matrix_for_shot_vector() -> None:
    """Frame vectors can feed both frame rows and the pooled shot row."""
    from pipeline.ingest.embed import pool_image_embeddings

    frames = np.zeros((2, 4), dtype=np.float32)
    frames[0, 0] = 1.0
    frames[1, 1] = 1.0

    pooled = pool_image_embeddings(frames)

    expected = np.array([2**-0.5, 2**-0.5, 0.0, 0.0], dtype=np.float32)
    np.testing.assert_allclose(pooled, expected, atol=1e-6)
    assert pooled.dtype == np.float32


def test_pool_image_embeddings_rejects_empty_matrix() -> None:
    from pipeline.ingest.embed import pool_image_embeddings

    with pytest.raises(ValueError, match="nonempty 2D"):
        pool_image_embeddings(np.empty((0, 1024), dtype=np.float32))


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

    fake = MagicMock()
    fake.encode_images.return_value = torch.tensor(raw)

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
    fake_preprocess = MagicMock()
    fake_tokenizer = MagicMock()

    with (
        patch(
            "open_clip.create_model_and_transforms",
            return_value=(fake_model, MagicMock(), fake_preprocess),
        ) as mock_create,
        patch("open_clip.get_tokenizer", return_value=fake_tokenizer),
    ):
        r1 = embed._load_model("pe_core_l14")
        r2 = embed._load_model("pe_core_l14")

    mock_create.assert_called_once_with("hf-hub:timm/PE-Core-L-14-336")
    assert r1 is r2, "Second call must return the same cached encoder"

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
