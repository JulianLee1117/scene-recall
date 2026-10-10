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


def test_index_lookups_inspects_by_default_and_installs_with_apply(config) -> None:
    plan = [{"table": "units", "column": "unit_id", "type": "BTREE", "name": "scene_lookup_unit_id_v1", "ready": True}]
    with (
        patch("pipeline.cli.load_config", return_value=config),
        patch("pipeline.cli.open_db", return_value=object()),
        patch("pipeline.index.search_indexes.lookup_plan", return_value=plan) as inspect,
        patch("pipeline.index.search_indexes.install_lookup_indexes", return_value=[]) as install,
    ):
        inspected = CliRunner().invoke(cli, ["index-lookups"])
        assert inspected.exit_code == 0, inspected.output
        assert '"scene_lookup_unit_id_v1"' in inspected.output and not install.called
        applied = CliRunner().invoke(cli, ["index-lookups", "--apply"])
    assert applied.exit_code == 0, applied.output
    install.assert_called_once()
    assert inspect.call_count == 1
