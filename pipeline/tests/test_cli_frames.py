"""CLI coverage for the local frame-index migration."""

from __future__ import annotations

from unittest.mock import ANY, patch

from click.testing import CliRunner

from pipeline.cli import cli
from pipeline.index.backfill_framing import (
    FramingBackfillProgress,
    FramingBackfillResult,
)
from pipeline.index.backfill_frames import FrameBackfillResult


def test_index_frames_reports_counts(config) -> None:
    result = FrameBackfillResult(
        discovered=12,
        embedded=9,
        upserted=9,
        skipped_current=3,
    )

    with (
        patch("pipeline.cli.load_config", return_value=config),
        patch(
            "pipeline.index.backfill_frames.backfill_frames",
            return_value=result,
        ) as backfill,
    ):
        command = CliRunner().invoke(
            cli,
            ["index-frames", "--film-id", "film-a", "--batch-size", "32"],
        )

    assert command.exit_code == 0, command.output
    assert "12 found" in command.output
    assert "9 embedded" in command.output
    assert "3 already current" in command.output
    backfill.assert_called_once_with(config, film_id="film-a", batch_size=32)


def test_index_frames_rejects_invalid_batch_size() -> None:
    command = CliRunner().invoke(
        cli,
        ["index-frames", "--batch-size", "0"],
    )

    assert command.exit_code == 2
    assert "Invalid value for '--batch-size'" in command.output


def test_index_framing_reports_profile_activation(config) -> None:
    result = FramingBackfillResult(
        profile_id="framing-spatial-test",
        table_name="frame_framing_test",
        discovered=12,
        embedded=9,
        upserted=9,
        skipped_current=3,
        activated=True,
    )

    with (
        patch("pipeline.cli.load_config", return_value=config),
        patch(
            "pipeline.index.backfill_framing.backfill_framing_features",
            return_value=result,
        ) as backfill,
    ):
        command = CliRunner().invoke(
            cli,
            ["index-framing", "--film-id", "film-a", "--batch-size", "16"],
        )

    assert command.exit_code == 0, command.output
    assert "12 found" in command.output
    assert "9 embedded" in command.output
    assert "Profile framing-spatial-test: active" in command.output
    backfill.assert_called_once_with(
        config,
        film_id="film-a",
        batch_size=16,
        progress_callback=ANY,
    )


def test_index_framing_emits_flushed_coarse_progress(config) -> None:
    result = FramingBackfillResult(
        profile_id="framing-spatial-test",
        table_name="frame_framing_test",
        discovered=100,
        embedded=100,
        upserted=100,
        skipped_current=0,
        activated=True,
    )

    def fake_backfill(
        passed_config,
        *,
        film_id,
        batch_size,
        progress_callback,
    ):
        assert passed_config is config
        assert film_id is None
        assert batch_size == 512
        for completed in (0, 5, 10, 19, 20, 99, 100):
            progress_callback(
                FramingBackfillProgress(
                    discovered=100,
                    completed=completed,
                    embedded=completed,
                    skipped_current=0,
                )
            )
        return result

    with (
        patch("pipeline.cli.load_config", return_value=config),
        patch(
            "pipeline.index.backfill_framing.backfill_framing_features",
            side_effect=fake_backfill,
        ),
    ):
        command = CliRunner().invoke(cli, ["index-framing"])

    assert command.exit_code == 0, command.output
    progress_lines = [
        line
        for line in command.output.splitlines()
        if line.startswith("[framing]")
    ]
    assert progress_lines == [
        "[framing] caching spatial grids: 0% (0/100)",
        "[framing] caching spatial grids: 10% (10/100)",
        "[framing] caching spatial grids: 20% (20/100)",
        "[framing] caching spatial grids: 90% (99/100)",
        "[framing] caching spatial grids: 100% (100/100)",
    ]
