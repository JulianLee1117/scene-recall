"""CLI coverage for the local frame-index migration."""

from __future__ import annotations

from unittest.mock import patch

from click.testing import CliRunner

from pipeline.cli import cli
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
