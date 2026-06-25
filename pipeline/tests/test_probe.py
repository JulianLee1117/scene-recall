"""Tests for pipeline/ingest/probe.py — written before implementation (TDD)."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from pipeline.config import Config


# ---------------------------------------------------------------------------
# Basic smoke test — requires the test clip
# ---------------------------------------------------------------------------


def test_probe_film_returns_film_record(test_clip: Path, config: Config) -> None:
    """probe_film returns a FilmRecord dataclass (not a dict)."""
    from pipeline.ingest.probe import probe_film, FilmRecord

    record = probe_film(test_clip, config)
    assert isinstance(record, FilmRecord)


def test_probe_film_duration_approx(test_clip: Path, config: Config) -> None:
    """Duration is within 0.5 s of the expected 30-second clip."""
    from pipeline.ingest.probe import probe_film

    record = probe_film(test_clip, config)
    assert abs(record.duration - 30.0) < 0.5


def test_probe_film_fps(test_clip: Path, config: Config) -> None:
    """FPS is reported correctly (30 fps for the synthetic clip)."""
    from pipeline.ingest.probe import probe_film

    record = probe_film(test_clip, config)
    assert abs(record.fps - 30.0) < 0.5


def test_probe_film_asset_dir_created(test_clip: Path, config: Config) -> None:
    """asset_dir is created on disk after probe_film returns."""
    from pipeline.ingest.probe import probe_film

    record = probe_film(test_clip, config)
    assert record.asset_dir.exists()
    assert record.asset_dir.is_dir()


def test_probe_film_asset_dir_under_assets(test_clip: Path, config: Config) -> None:
    """asset_dir is a child of config.paths.assets_dir."""
    from pipeline.ingest.probe import probe_film

    record = probe_film(test_clip, config)
    # asset_dir should be assets_dir / film_id
    assert record.asset_dir.parent == config.paths.assets_dir


def test_probe_film_film_id_is_hex_string(test_clip: Path, config: Config) -> None:
    """film_id is a 64-character hex string (SHA-256)."""
    from pipeline.ingest.probe import probe_film

    record = probe_film(test_clip, config)
    assert len(record.film_id) == 64
    # All hex characters
    int(record.film_id, 16)


def test_probe_film_film_id_consistent(test_clip: Path, config: Config) -> None:
    """Calling probe_film twice on the same file yields the same film_id."""
    from pipeline.ingest.probe import probe_film

    r1 = probe_film(test_clip, config)
    r2 = probe_film(test_clip, config)
    assert r1.film_id == r2.film_id


def test_probe_film_path_stored(test_clip: Path, config: Config) -> None:
    """FilmRecord.path matches the input path."""
    from pipeline.ingest.probe import probe_film

    record = probe_film(test_clip, config)
    assert record.path == test_clip.resolve()


def test_probe_film_no_embedded_subs(test_clip: Path, config: Config) -> None:
    """Synthetic clip has no subtitle streams."""
    from pipeline.ingest.probe import probe_film

    record = probe_film(test_clip, config)
    assert record.has_embedded_subs is False


def test_probe_film_title(test_clip: Path, config: Config) -> None:
    """title is a non-empty string."""
    from pipeline.ingest.probe import probe_film

    record = probe_film(test_clip, config)
    assert isinstance(record.title, str)
    assert record.title  # not empty


def test_probe_film_duration_is_float(test_clip: Path, config: Config) -> None:
    """duration is stored as a float (not int or str)."""
    from pipeline.ingest.probe import probe_film

    record = probe_film(test_clip, config)
    assert isinstance(record.duration, float)


def test_probe_film_has_embedded_subs_true(test_clip: Path, config: Config) -> None:
    """probe_film sets has_embedded_subs=True when a subtitle stream is present."""
    from pipeline.ingest.probe import probe_film

    fake_meta = {
        "format": {"duration": "30.0"},
        "streams": [
            {"codec_type": "video", "r_frame_rate": "30/1"},
            {"codec_type": "subtitle"},
        ],
    }

    with patch("pipeline.ingest.probe._ffprobe", return_value=fake_meta):
        record = probe_film(test_clip, config)

    assert record.has_embedded_subs is True
