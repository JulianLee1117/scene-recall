"""Shared pytest fixtures for pipeline tests.

``config`` fixture
    Loads a minimal Config object whose assets_dir points at a temporary
    directory.  Uses an in-memory YAML string so tests don't depend on the
    real config.yaml.

``test_clip`` fixture
    Returns the path to ``pipeline/tests/fixtures/test_clip.mkv``.
    The clip is a 30-second synthetic video generated once with ffmpeg
    (a colour-bar test pattern + 1kHz tone).  It is gitignored.

    If the clip is missing the fixture calls ``pytest.skip`` so that
    individual tests can decide whether they need real video.  Run the
    helper below to regenerate it::

        python -m pipeline.tests.make_fixtures
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
import yaml

from pipeline.config import load_config, Config

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_FIXTURES_DIR = Path(__file__).parent / "fixtures"
_TEST_CLIP = _FIXTURES_DIR / "test_clip.mkv"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def config(tmp_path: Path) -> Config:
    """Minimal Config whose assets_dir is a fresh temp directory."""
    cfg_text = textwrap.dedent(f"""\
        paths:
          films_dir: {tmp_path / "films"}
          assets_dir: {tmp_path / "assets"}

        models:
          visual_encoder: pe_core_l14
          text_encoder: qwen3-embedding-0.6b
          annotator: gemini-3-flash
          router: qwen3:8b
          whisper: large-v3

        thresholds:
          shot_dedup_cosine: 0.12
          scene_visual_sim: 0.75
          scene_dialogue_gap: 2.5
          scene_max_duration: 300
          subsegment_min_duration: 20

        retrieval:
          weights:
            img: 0.4
            txt: 0.4
            lex: 0.2
          diversity:
            max_per_scene: 2
            max_per_film: 4
          rerank_enabled: false

        scoring:
          duration_weight: 0.05
          motion_weight: 0.05
          frame_worthiness_weight: 0.10
    """)

    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(cfg_text, encoding="utf-8")
    return load_config(cfg_file)


@pytest.fixture()
def test_clip() -> Path:
    """Path to the 30-second synthetic test clip.

    Skips the test if the clip hasn't been generated yet.
    Run ``python -m pipeline.tests.make_fixtures`` to create it.
    """
    if not _TEST_CLIP.exists():
        pytest.skip(
            f"Test clip not found at {_TEST_CLIP}. "
            "Run: python -m pipeline.tests.make_fixtures"
        )
    return _TEST_CLIP
