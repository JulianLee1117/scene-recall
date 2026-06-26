"""Tests for pipeline/ingest/pipeline.py and pipeline/cli.py — TDD.

All pipeline stage functions are mocked — no real ffmpeg, models, or API calls.

Coverage:
  - run_pipeline calls probe, dialogue, shots, media, embed, annotate, write in order
  - run_pipeline returns the FilmRecord from probe
  - run_pipeline skips extract_dialogue when dialogue.json is already present
  - run_pipeline skips extract_media when keyframes dir is non-empty
  - run_pipeline always calls shots, embed, annotate, write (no caching)
  - CLI `ingest` command exits with code 0 and calls run_pipeline
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from pipeline.config import Config
from pipeline.ingest.probe import FilmRecord
from pipeline.ingest.shots import Shot
from pipeline.ingest.dialogue import DialogueLine


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

VEC_DIM = 1024


def _make_film(tmp_path: Path) -> FilmRecord:
    asset_dir = tmp_path / "assets" / "film_abc"
    asset_dir.mkdir(parents=True, exist_ok=True)
    return FilmRecord(
        film_id="film_abc",
        path=tmp_path / "test.mkv",
        asset_dir=asset_dir,
        duration=60.0,
        fps=24.0,
        has_embedded_subs=False,
        title="Test Film",
    )


def _make_shots(film: FilmRecord) -> list[Shot]:
    return [
        Shot(
            shot_id=f"{film.film_id}_{i:04d}",
            t_start=float(i * 10),
            t_end=float(i * 10 + 9),
            parent_shot_id=None,
            keyframe_times=[float(i * 10 + 4.5)],
        )
        for i in range(2)
    ]


def _rand_vec() -> np.ndarray:
    v = np.random.randn(VEC_DIM).astype(np.float32)
    return v / np.linalg.norm(v)


def _make_annotation() -> dict:
    return {
        "caption": "A test caption.",
        "mood": ["dramatic"],
        "searchable_text": "A test caption.",
    }


def _fake_dialogue() -> list[DialogueLine]:
    return [DialogueLine(start=1.0, end=2.0, text="Hello")]


def _all_mocks(film: FilmRecord, shots: list[Shot], call_order: list[str]):
    """Return a dict of context-manager patches with call-tracking side effects."""
    img_vec = _rand_vec()
    annotation = _make_annotation()
    txt_vec = _rand_vec()
    dialogue = _fake_dialogue()

    def track(name, ret=None):
        def side_effect(*args, **kwargs):
            call_order.append(name)
            return ret
        return side_effect

    mock_conn = MagicMock()
    mock_tbl = MagicMock()
    mock_tbl.count_rows.return_value = len(shots)
    mock_conn.open_table.return_value = mock_tbl

    return {
        "probe": patch("pipeline.ingest.pipeline.probe_film", side_effect=track("probe", film)),
        "dialogue": patch("pipeline.ingest.pipeline.extract_dialogue", side_effect=track("dialogue", dialogue)),
        "shots": patch("pipeline.ingest.pipeline.detect_shots", side_effect=track("shots", shots)),
        "media": patch("pipeline.ingest.pipeline.extract_media", side_effect=track("media", None)),
        "shot_embedding": patch("pipeline.ingest.pipeline.shot_embedding", return_value=img_vec),
        "annotate": patch("pipeline.ingest.pipeline.annotate_shot", return_value=annotation),
        "embed_text": patch("pipeline.ingest.pipeline.embed_text", return_value=np.array([txt_vec])),
        "open_db": patch("pipeline.ingest.pipeline.open_db", return_value=mock_conn),
        "create_tables": patch("pipeline.ingest.pipeline.create_tables"),
        "write_film": patch("pipeline.ingest.pipeline.write_film"),
        "write_unit": patch("pipeline.ingest.pipeline.write_unit"),
    }


# ---------------------------------------------------------------------------
# run_pipeline — stage ordering
# ---------------------------------------------------------------------------


def test_run_pipeline_calls_stages_in_order(tmp_path: Path, config: Config) -> None:
    """probe → dialogue → shots → media are called in that order."""
    film = _make_film(tmp_path)
    shots = _make_shots(film)
    film_path = tmp_path / "film.mkv"
    film_path.touch()

    call_order: list[str] = []
    patches = _all_mocks(film, shots, call_order)

    with (
        patches["probe"],
        patches["dialogue"],
        patches["shots"],
        patches["media"],
        patches["shot_embedding"],
        patches["annotate"],
        patches["embed_text"],
        patches["open_db"],
        patches["create_tables"],
        patches["write_film"],
        patches["write_unit"],
    ):
        from pipeline.ingest.pipeline import run_pipeline
        run_pipeline(film_path, config)

    assert call_order.index("probe") < call_order.index("dialogue"), "probe must run before dialogue"
    assert call_order.index("dialogue") < call_order.index("shots"), "dialogue must run before shots"
    assert call_order.index("shots") < call_order.index("media"), "shots must run before media"


def test_run_pipeline_returns_film_record(tmp_path: Path, config: Config) -> None:
    """run_pipeline returns the FilmRecord produced by probe_film."""
    film = _make_film(tmp_path)
    shots = _make_shots(film)
    film_path = tmp_path / "film.mkv"
    film_path.touch()

    call_order: list[str] = []
    patches = _all_mocks(film, shots, call_order)

    with (
        patches["probe"],
        patches["dialogue"],
        patches["shots"],
        patches["media"],
        patches["shot_embedding"],
        patches["annotate"],
        patches["embed_text"],
        patches["open_db"],
        patches["create_tables"],
        patches["write_film"],
        patches["write_unit"],
    ):
        from pipeline.ingest.pipeline import run_pipeline
        result = run_pipeline(film_path, config)

    assert result is film


# ---------------------------------------------------------------------------
# run_pipeline — idempotency: dialogue
# ---------------------------------------------------------------------------


def test_run_pipeline_skips_dialogue_when_cached(tmp_path: Path, config: Config) -> None:
    """extract_dialogue is NOT called when dialogue.json already exists in asset_dir."""
    film = _make_film(tmp_path)
    shots = _make_shots(film)
    film_path = tmp_path / "film.mkv"
    film_path.touch()

    # Pre-write dialogue.json to simulate a previous run
    dialogue_path = film.asset_dir / "dialogue.json"
    cached = [{"start": 0.0, "end": 1.0, "text": "Cached line"}]
    dialogue_path.write_text(json.dumps(cached), encoding="utf-8")

    call_order: list[str] = []
    patches = _all_mocks(film, shots, call_order)

    with (
        patches["probe"],
        patches["dialogue"] as mock_dialogue,
        patches["shots"],
        patches["media"],
        patches["shot_embedding"],
        patches["annotate"],
        patches["embed_text"],
        patches["open_db"],
        patches["create_tables"],
        patches["write_film"],
        patches["write_unit"],
    ):
        from pipeline.ingest.pipeline import run_pipeline
        run_pipeline(film_path, config)

    mock_dialogue.assert_not_called()


# ---------------------------------------------------------------------------
# run_pipeline — idempotency: media
# ---------------------------------------------------------------------------


def test_run_pipeline_skips_media_when_keyframes_exist(tmp_path: Path, config: Config) -> None:
    """extract_media is NOT called when the keyframes directory is non-empty."""
    film = _make_film(tmp_path)
    shots = _make_shots(film)
    film_path = tmp_path / "film.mkv"
    film_path.touch()

    # Pre-populate keyframes dir
    kf_dir = film.asset_dir / "keyframes"
    kf_dir.mkdir(parents=True, exist_ok=True)
    (kf_dir / "film_abc_0000_0.webp").touch()

    call_order: list[str] = []
    patches = _all_mocks(film, shots, call_order)

    with (
        patches["probe"],
        patches["dialogue"],
        patches["shots"],
        patches["media"] as mock_media,
        patches["shot_embedding"],
        patches["annotate"],
        patches["embed_text"],
        patches["open_db"],
        patches["create_tables"],
        patches["write_film"],
        patches["write_unit"],
    ):
        from pipeline.ingest.pipeline import run_pipeline
        run_pipeline(film_path, config)

    mock_media.assert_not_called()


# ---------------------------------------------------------------------------
# run_pipeline — embed/annotate/write always run
# ---------------------------------------------------------------------------


def test_run_pipeline_calls_write_unit_for_each_shot(tmp_path: Path, config: Config) -> None:
    """write_unit is called once per shot regardless of cache state."""
    film = _make_film(tmp_path)
    shots = _make_shots(film)
    film_path = tmp_path / "film.mkv"
    film_path.touch()

    call_order: list[str] = []
    patches = _all_mocks(film, shots, call_order)

    with (
        patches["probe"],
        patches["dialogue"],
        patches["shots"],
        patches["media"],
        patches["shot_embedding"],
        patches["annotate"],
        patches["embed_text"],
        patches["open_db"],
        patches["create_tables"],
        patches["write_film"],
        patches["write_unit"] as mock_write_unit,
    ):
        from pipeline.ingest.pipeline import run_pipeline
        run_pipeline(film_path, config)

    assert mock_write_unit.call_count == len(shots)


# ---------------------------------------------------------------------------
# CLI — ingest command
# ---------------------------------------------------------------------------


def test_cli_ingest_exits_zero(tmp_path: Path, config: Config) -> None:
    """CLI `ingest <film_path>` exits with code 0."""
    from click.testing import CliRunner
    from pipeline.cli import cli

    film_path = tmp_path / "film.mkv"
    film_path.touch()

    film = _make_film(tmp_path)

    runner = CliRunner()
    with (
        patch("pipeline.cli.load_config", return_value=config),
        patch("pipeline.cli.run_pipeline", return_value=film),
    ):
        result = runner.invoke(cli, ["ingest", str(film_path)])

    assert result.exit_code == 0, f"CLI exited with {result.exit_code}:\n{result.output}"


def test_cli_ingest_calls_run_pipeline(tmp_path: Path, config: Config) -> None:
    """CLI `ingest <film_path>` calls run_pipeline with the correct path and config."""
    from click.testing import CliRunner
    from pipeline.cli import cli

    film_path = tmp_path / "film.mkv"
    film_path.touch()

    film = _make_film(tmp_path)

    runner = CliRunner()
    with (
        patch("pipeline.cli.load_config", return_value=config),
        patch("pipeline.cli.run_pipeline", return_value=film) as mock_run,
    ):
        runner.invoke(cli, ["ingest", str(film_path)])

    mock_run.assert_called_once_with(film_path, config)
