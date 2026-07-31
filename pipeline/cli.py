"""cli.py — Click-based CLI entry point for the cinema-search pipeline.

Usage::

    python -m pipeline.cli ingest <film_path>
    python -m pipeline.cli ingest-batch <directory> [--force]
    python -m pipeline.cli index-frames [--film-id FILM_ID]
    python -m pipeline.cli eval [--queries pipeline/eval/gold_queries.yaml]

The ``ingest`` command runs the full ingest pipeline and prints a summary.
The ``ingest-batch`` command ingests every video in a directory, skipping
films whose content hash is already fully indexed.
The ``eval`` command runs the evaluation harness against indexed films.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import click
from dotenv import load_dotenv

load_dotenv()

from pipeline.config import VIDEO_EXTENSIONS, Config, load_config
from pipeline.ingest.pipeline import run_pipeline
from pipeline.ingest.probe import _content_hash
from pipeline.index.writer import open_db, published_film_ids

_DEFAULT_QUERIES = Path(__file__).parent / "eval" / "gold_queries.yaml"


def _lower_own_priority() -> None:
    """Drop this process below normal priority, best-effort.

    Ingest saturates CPU/GPU for hours; not starving the desktop is a
    property of the workload itself, regardless of who launched it.
    """
    try:
        if sys.platform == "win32":
            import ctypes

            below_normal_priority_class = 0x00004000
            handle = ctypes.windll.kernel32.GetCurrentProcess()
            ctypes.windll.kernel32.SetPriorityClass(
                handle, below_normal_priority_class
            )
        else:
            os.nice(10)
    except (AttributeError, OSError):
        pass


@click.group()
def cli() -> None:
    """Cinema search pipeline."""


@cli.command()
@click.argument("film_path", type=click.Path(exists=True, path_type=Path))
def ingest(film_path: Path) -> None:
    """Ingest FILM_PATH through the full pipeline and index it."""
    _lower_own_priority()
    config = load_config()
    run_pipeline(film_path, config)


@cli.command("ingest-batch")
@click.argument(
    "directory",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
)
@click.option(
    "--force",
    is_flag=True,
    help="Re-ingest films whose content hash is already indexed.",
)
def ingest_batch(directory: Path, force: bool) -> None:
    """Ingest every video file in DIRECTORY, one film at a time.

    Films whose content hash is already fully indexed are skipped unless
    ``--force`` is given.  A failing film is reported and the batch moves on;
    the command exits non-zero if any film failed.
    """
    _lower_own_priority()
    config = load_config()
    video_files = sorted(
        path
        for path in directory.iterdir()
        if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS
    )
    if not video_files:
        click.echo(f"No video files found in {directory}.")
        return

    indexed = frozenset() if force else _indexed_film_ids(config)
    ingested = skipped = 0
    failures: list[str] = []
    for position, path in enumerate(video_files, start=1):
        click.echo(f"\n=== [{position}/{len(video_files)}] {path.name} ===")
        if indexed and _content_hash(path) in indexed:
            click.echo("already indexed — skipped (use --force to redo)")
            skipped += 1
            continue
        try:
            run_pipeline(path, config)
            ingested += 1
        except Exception as exc:
            failures.append(path.name)
            click.echo(f"FAILED: {exc}", err=True)

    click.echo(
        f"\nBatch complete: {ingested} ingested, {skipped} skipped, "
        f"{len(failures)} failed."
    )
    if failures:
        click.echo("Failed films: " + ", ".join(failures), err=True)
        raise SystemExit(1)


def _indexed_film_ids(config: Config) -> frozenset[str]:
    """Return film IDs that are fully published (film row plus ready units)."""
    return published_film_ids(open_db(config))


@cli.command("index-frames")
@click.option(
    "--film-id",
    default=None,
    help="Only index keyframes belonging to this film ID.",
)
@click.option(
    "--batch-size",
    type=click.IntRange(min=1),
    default=128,
    show_default=True,
    help="Number of frame rows written per database batch.",
)
def index_frames_cmd(film_id: str | None, batch_size: int) -> None:
    """Build the local frame index from already-extracted keyframes."""
    from pipeline.index.backfill_frames import backfill_frames

    result = backfill_frames(
        load_config(),
        film_id=film_id,
        batch_size=batch_size,
    )
    click.echo(
        "Frames: "
        f"{result.discovered} found, "
        f"{result.embedded} embedded, "
        f"{result.upserted} indexed, "
        f"{result.skipped_current} already current."
    )


@cli.command("eval")
@click.option(
    "--queries",
    "queries_path",
    type=click.Path(path_type=Path),
    default=None,
    show_default=True,
    help="Path to gold_queries.yaml (default: pipeline/eval/gold_queries.yaml).",
)
def eval_cmd(queries_path: Path | None) -> None:
    """Run the evaluation harness against indexed films.

    Reads QUERIES_PATH (a YAML file of gold queries), calls search() for each
    non-placeholder query, and prints per-query hit@5 / hit@10 results plus
    aggregate metrics at the end.
    """
    from pipeline.eval.run_eval import main as run_eval_main

    run_eval_main(queries_path or _DEFAULT_QUERIES)


if __name__ == "__main__":
    cli()
