"""Focused orchestration and CLI tests for the film ingest pipeline."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from pipeline.config import Config
from pipeline.ingest.dialogue import DialogueLine
from pipeline.ingest.probe import FilmRecord
from pipeline.ingest.shots import Shot


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
            shot_id=f"{film.film_id}_{index:04d}",
            t_start=float(index * 10),
            t_end=float(index * 10 + 9),
            parent_shot_id=None,
            keyframe_times=[float(index * 10 + 4.5)],
        )
        for index in range(2)
    ]


def _rand_vec() -> np.ndarray:
    vector = np.random.randn(VEC_DIM).astype(np.float32)
    return vector / np.linalg.norm(vector)


def _make_annotation() -> dict:
    return {
        "caption": "A test caption.",
        "mood": ["quiet", "dramatic"],
        "searchable_text": "A test caption.",
    }


def _pipeline_mocks(
    film: FilmRecord,
    shots: list[Shot],
    call_order: list[str],
) -> tuple[dict[str, MagicMock], object]:
    """Return mocks plus one patch.multiple context for the orchestrator."""
    image_vector = _rand_vec()
    text_vector = _rand_vec()
    dialogue = [DialogueLine(start=1.0, end=2.0, text="Hello")]

    def tracked(name: str, value: object):
        def side_effect(*_args, **_kwargs):
            call_order.append(name)
            return value

        return side_effect

    table = MagicMock()
    table.count_rows.return_value = len(shots)
    connection = MagicMock()
    connection.open_table.return_value = table
    mocks = {
        "probe_film": MagicMock(side_effect=tracked("probe", film)),
        "extract_dialogue": MagicMock(
            side_effect=tracked("dialogue", dialogue)
        ),
        "detect_shots": MagicMock(side_effect=tracked("shots", shots)),
        "extract_media": MagicMock(side_effect=tracked("media", None)),
        "embed_images": MagicMock(
            return_value=np.array(
                [
                    image_vector
                    for _shot in shots
                    for _time in _shot.keyframe_times
                ],
                dtype=np.float32,
            )
        ),
        "pool_image_embeddings": MagicMock(return_value=image_vector),
        "annotate_shot": MagicMock(return_value=_make_annotation()),
        "embed_text": MagicMock(
            return_value=np.array(
                [text_vector for _shot in shots],
                dtype=np.float32,
            )
        ),
        "open_db": MagicMock(return_value=connection),
        "create_tables": MagicMock(),
        "publish_film_index": MagicMock(),
    }
    return mocks, patch.multiple("pipeline.ingest.pipeline", **mocks)


def test_run_pipeline_calls_stages_in_order_and_returns_film(
    tmp_path: Path,
    config: Config,
) -> None:
    film = _make_film(tmp_path)
    shots = _make_shots(film)
    film_path = tmp_path / "film.mkv"
    film_path.touch()
    call_order: list[str] = []
    _mocks, context = _pipeline_mocks(film, shots, call_order)

    with context:
        from pipeline.ingest.pipeline import run_pipeline

        result = run_pipeline(film_path, config)

    assert result is film
    assert call_order[:5] == ["probe", "probe", "dialogue", "shots", "media"]


def test_run_pipeline_rejects_source_identity_change_while_waiting(
    tmp_path: Path,
    config: Config,
) -> None:
    film = _make_film(tmp_path)
    changed = FilmRecord(
        film_id="different-film-id",
        path=film.path,
        asset_dir=tmp_path / "assets" / "different-film-id",
        duration=film.duration,
        fps=film.fps,
        has_embedded_subs=film.has_embedded_subs,
        title=film.title,
    )
    film_path = tmp_path / "film.mkv"
    film_path.touch()

    with (
        patch(
            "pipeline.ingest.pipeline.probe_film",
            side_effect=[film, changed],
        ),
        patch("pipeline.ingest.pipeline._run_pipeline_locked") as run_locked,
        pytest.raises(RuntimeError, match="identity changed"),
    ):
        from pipeline.ingest.pipeline import run_pipeline

        run_pipeline(film_path, config)

    run_locked.assert_not_called()


def test_run_pipeline_reports_another_global_ingest_without_waiting(
    tmp_path: Path,
    config: Config,
) -> None:
    """A separate CLI/API process fails clearly instead of waiting for hours."""
    from pipeline.ingest.locks import global_ingest_lock
    from pipeline.ingest.pipeline import run_pipeline

    film_path = tmp_path / "film.mkv"
    film_path.touch()
    with global_ingest_lock(config.paths.assets_dir):
        with pytest.raises(RuntimeError, match="another film ingest"):
            run_pipeline(film_path, config)


def test_run_pipeline_reuses_cached_dialogue(
    tmp_path: Path,
    config: Config,
) -> None:
    film = _make_film(tmp_path)
    shots = _make_shots(film)
    film_path = tmp_path / "film.mkv"
    film_path.touch()
    (film.asset_dir / "dialogue.json").write_text(
        json.dumps([{"start": 0.0, "end": 1.0, "text": "Cached"}]),
        encoding="utf-8",
    )
    from pipeline.ingest.dialogue import _dialogue_source

    (film.asset_dir / "dialogue.manifest.json").write_text(
        json.dumps(_dialogue_source(film, config), sort_keys=True),
        encoding="utf-8",
    )
    call_order: list[str] = []
    mocks, context = _pipeline_mocks(film, shots, call_order)

    with context:
        from pipeline.ingest.pipeline import run_pipeline

        run_pipeline(film_path, config)

    mocks["extract_dialogue"].assert_not_called()


def test_run_pipeline_always_validates_media_even_when_directory_nonempty(
    tmp_path: Path,
    config: Config,
) -> None:
    """Per-artifact resume belongs to extract_media, not a directory shortcut."""
    film = _make_film(tmp_path)
    shots = _make_shots(film)
    film_path = tmp_path / "film.mkv"
    film_path.touch()
    keyframes = film.asset_dir / "keyframes"
    keyframes.mkdir()
    (keyframes / f"{shots[0].shot_id}_0.webp").touch()
    call_order: list[str] = []
    mocks, context = _pipeline_mocks(film, shots, call_order)

    with context:
        from pipeline.ingest.pipeline import run_pipeline

        run_pipeline(film_path, config)

    mocks["extract_media"].assert_called_once_with(film, shots, config)


def test_run_pipeline_reuses_each_frame_embedding_for_shot_and_frame_rows(
    tmp_path: Path,
    config: Config,
) -> None:
    """One batched visual pass feeds both shot and frame rows."""
    film = _make_film(tmp_path)
    shots = _make_shots(film)
    film_path = tmp_path / "film.mkv"
    film_path.touch()
    call_order: list[str] = []
    mocks, context = _pipeline_mocks(film, shots, call_order)

    with context:
        from pipeline.ingest.pipeline import run_pipeline

        run_pipeline(film_path, config)

    mocks["embed_images"].assert_called_once()
    embedded_paths = mocks["embed_images"].call_args.args[0]
    assert embedded_paths == [
        film.asset_dir / "keyframes" / f"{shot.shot_id}_0.webp"
        for shot in shots
    ]
    assert mocks["pool_image_embeddings"].call_count == len(shots)
    mocks["embed_text"].assert_called_once_with(
        [_make_annotation()["searchable_text"] for _shot in shots],
        config,
    )
    mocks["publish_film_index"].assert_called_once()
    _db, published_film, units, frames = (
        mocks["publish_film_index"].call_args.args
    )
    assert published_film is film
    assert [unit.shot for unit in units] == shots
    assert [frame.unit_id for frame in frames] == [
        shot.shot_id for shot in shots
    ]
    assert [frame.timestamp for frame in frames] == [
        shot.keyframe_times[0] for shot in shots
    ]


def test_run_pipeline_passes_durable_annotation_cache(
    tmp_path: Path,
    config: Config,
) -> None:
    film = _make_film(tmp_path)
    shots = _make_shots(film)
    film_path = tmp_path / "film.mkv"
    film_path.touch()
    call_order: list[str] = []
    mocks, context = _pipeline_mocks(film, shots, call_order)

    with context:
        from pipeline.ingest.pipeline import run_pipeline

        run_pipeline(film_path, config)

    assert all(
        call.kwargs["cache_dir"] == film.asset_dir / "annotations"
        for call in mocks["annotate_shot"].call_args_list
    )


def test_run_pipeline_does_not_publish_after_annotation_failure(
    tmp_path: Path,
    config: Config,
) -> None:
    film = _make_film(tmp_path)
    shots = _make_shots(film)
    film_path = tmp_path / "film.mkv"
    film_path.touch()
    call_order: list[str] = []
    mocks, context = _pipeline_mocks(film, shots, call_order)
    mocks["annotate_shot"].side_effect = [
        _make_annotation(),
        RuntimeError("annotation failed"),
    ]

    with (
        context,
        pytest.raises(RuntimeError, match="annotation failed"),
    ):
        from pipeline.ingest.pipeline import run_pipeline

        run_pipeline(film_path, config)

    mocks["publish_film_index"].assert_not_called()


def test_cli_ingest_exits_zero_and_calls_pipeline(
    tmp_path: Path,
    config: Config,
) -> None:
    from click.testing import CliRunner
    from pipeline.cli import cli

    film_path = tmp_path / "film.mkv"
    film_path.touch()
    film = _make_film(tmp_path)
    runner = CliRunner()

    with (
        patch("pipeline.cli.load_config", return_value=config),
        patch("pipeline.cli.run_pipeline", return_value=film) as run,
    ):
        result = runner.invoke(cli, ["ingest", str(film_path)])

    assert result.exit_code == 0, result.output
    run.assert_called_once_with(film_path, config)


def test_indexed_film_ids_repairs_search_index_before_batch_skip(
    config: Config,
) -> None:
    """A post-publication FTS failure resumes without re-ingesting media."""
    from pipeline.cli import _indexed_film_ids

    db = MagicMock()
    expected = frozenset({"film_complete"})
    with (
        patch("pipeline.cli.open_db", return_value=db),
        patch("pipeline.cli.ensure_search_indexes") as ensure,
        patch("pipeline.cli.published_film_ids", return_value=expected) as published,
    ):
        result = _indexed_film_ids(config)

    assert result == expected
    ensure.assert_called_once_with(db)
    published.assert_called_once_with(db)


def test_cli_repair_search_index_reports_coverage_without_ingest(
    config: Config,
) -> None:
    """FTS recovery is narrow, visible, and never invokes film processing."""
    from click.testing import CliRunner
    from pipeline.cli import cli

    index = MagicMock()
    index.name = "units_searchable_text_fts_v1"
    after = MagicMock(num_indexed_rows=10_271, num_unindexed_rows=0)
    table = MagicMock()
    table.list_indices.return_value = [index]
    table.index_stats.return_value = after
    db = MagicMock()
    db.list_tables.return_value.tables = ["units"]
    db.open_table.return_value = table

    runner = CliRunner()
    with (
        patch("pipeline.cli.load_config", return_value=config),
        patch("pipeline.cli.open_db", return_value=db),
        patch("pipeline.cli.ensure_search_indexes") as ensure,
        patch("pipeline.cli.run_pipeline") as run,
    ):
        result = runner.invoke(cli, ["repair-search-index"])

    assert result.exit_code == 0, result.output
    assert "10271 indexed, 0 pending" in result.output
    ensure.assert_called_once_with(db)
    run.assert_not_called()


def test_cli_ingest_batch_skips_indexed_and_survives_one_failure(
    tmp_path: Path,
    config: Config,
) -> None:
    """The batch skips indexed films, keeps going past failures, exits 1."""
    from click.testing import CliRunner
    from pipeline.cli import cli

    for name in ("a.mkv", "b.mkv", "c.mkv"):
        (tmp_path / name).touch()
    (tmp_path / "notes.txt").touch()

    ingested: list[str] = []

    def fake_run(path: Path, _config: Config) -> None:
        ingested.append(path.name)
        if path.name == "c.mkv":
            raise RuntimeError("annotation quota exhausted")

    runner = CliRunner()
    with (
        patch("pipeline.cli.load_config", return_value=config),
        patch(
            "pipeline.cli._indexed_film_ids",
            return_value=frozenset({"hash-a.mkv"}),
        ),
        patch(
            "pipeline.cli._content_hash",
            side_effect=lambda path: f"hash-{path.name}",
        ),
        patch("pipeline.cli.run_pipeline", side_effect=fake_run),
    ):
        result = runner.invoke(cli, ["ingest-batch", str(tmp_path)])

    assert result.exit_code == 1
    assert ingested == ["b.mkv", "c.mkv"]
    assert "1 ingested, 1 skipped, 1 failed" in result.output


def test_cli_ingest_batch_force_reingests_indexed_films(
    tmp_path: Path,
    config: Config,
) -> None:
    from click.testing import CliRunner
    from pipeline.cli import cli

    for name in ("a.mkv", "b.mkv"):
        (tmp_path / name).touch()

    runner = CliRunner()
    with (
        patch("pipeline.cli.load_config", return_value=config),
        patch("pipeline.cli._indexed_film_ids") as indexed,
        patch("pipeline.cli.run_pipeline") as run,
    ):
        result = runner.invoke(cli, ["ingest-batch", str(tmp_path), "--force"])

    assert result.exit_code == 0, result.output
    indexed.assert_not_called()
    assert run.call_count == 2
