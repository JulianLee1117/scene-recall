"""cli.py — Click-based CLI entry point for the cinema-search pipeline.

Usage::

    python -m pipeline.cli ingest <film_path>
    python -m pipeline.cli ingest-batch <directory> [--force]
    python -m pipeline.cli repair-search-index
    python -m pipeline.cli relink-film <new_path> [--apply]
    python -m pipeline.cli recover-relink <film_id>
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
from typing import Any

import click
from dotenv import load_dotenv

load_dotenv()

from pipeline.config import VIDEO_EXTENSIONS, Config, load_config
from pipeline.ingest.probe import _content_hash
from pipeline.index.writer import (
    UNITS_FTS_INDEX,
    ensure_search_indexes,
    open_db,
    published_film_ids,
    table_names,
)

_DEFAULT_QUERIES = Path(__file__).parent / "eval" / "gold_queries.yaml"


def run_pipeline(film_path: Path, config: Config) -> Any:
    """Load the heavyweight ingest stack only when a film actually runs."""
    from pipeline.ingest.pipeline import run_pipeline as execute_pipeline

    return execute_pipeline(film_path, config)


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
    """Repair derived search state, then return fully published film IDs."""
    db = open_db(config)
    # A prior ingest can commit all film rows and then fail while refreshing
    # the derived FTS index.  Repair that cheap final stage before the batch
    # decides the film is complete and skips its expensive cached pipeline.
    ensure_search_indexes(db)
    return published_film_ids(db)


def _fts_index_coverage(db: Any) -> tuple[int, int] | None:
    """Return managed FTS indexed/pending counts, or ``None`` if absent."""
    if "units" not in table_names(db):
        return None
    table = db.open_table("units")
    if not any(index.name == UNITS_FTS_INDEX for index in table.list_indices()):
        return None
    stats = table.index_stats(UNITS_FTS_INDEX)
    if stats is None:
        return None
    return int(stats.num_indexed_rows), int(stats.num_unindexed_rows)


@cli.command("repair-search-index")
def repair_search_index_cmd() -> None:
    """Repair the derived full-text index without re-ingesting any film."""
    db = open_db(load_config())
    try:
        ensure_search_indexes(db)
    except Exception as exc:
        raise click.ClickException(f"search-index repair failed: {exc}") from exc

    after = _fts_index_coverage(db)
    if after is None:
        click.echo("No units table exists yet; nothing to repair.")
        return
    click.echo(
        f"Search index ready: {after[0]} indexed, {after[1]} pending."
    )


@cli.command("relink-film")
@click.argument(
    "new_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
@click.option(
    "--film-id",
    "expected_film_id",
    default=None,
    help="Assert the expected existing film ID before relinking.",
)
@click.option(
    "--title",
    default=None,
    help="Set an explicit display title while relinking.",
)
@click.option(
    "--title-from-filename",
    is_flag=True,
    help="Use the destination filename stem as the display title.",
)
@click.option(
    "--apply",
    "apply_changes",
    is_flag=True,
    help="Commit the validated cache and database changes.",
)
def relink_film_cmd(
    new_path: Path,
    expected_film_id: str | None,
    title: str | None,
    title_from_filename: bool,
    apply_changes: bool,
) -> None:
    """Safely relink an indexed film to an exact copied source at NEW_PATH.

    Without ``--apply`` this performs a full-hash dry run and changes no
    project data. The old source must still exist; this command never moves or
    deletes either movie file.
    """
    from pipeline.index.relink import relink_film

    if title is not None and title_from_filename:
        raise click.UsageError(
            "--title and --title-from-filename are mutually exclusive"
        )

    config = load_config()
    db = open_db(config)
    try:
        plan = relink_film(
            db,
            config,
            new_path,
            expected_film_id=expected_film_id,
            title=title,
            title_from_filename=title_from_filename,
            apply=apply_changes,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc

    click.echo(f"Film ID: {plan.film_id}")
    click.echo(f"Old source: {plan.old_path}")
    click.echo(f"New source: {plan.new_path}")
    click.echo(f"Full SHA-256: {plan.sha256}")
    click.echo(f"Title: {plan.old_title!r} -> {plan.new_title!r}")
    click.echo(
        "Cache identities: "
        f"{plan.shot_cache_changes} shot cache, "
        f"{plan.media_manifest_changes} media manifests"
    )
    if plan.is_noop:
        click.echo("Already current; no changes needed.")
    elif apply_changes:
        click.echo("Relink committed. The original source was retained.")
    else:
        click.echo("Dry run passed. Re-run with --apply to commit.")


@cli.command("recover-relink")
@click.argument("film_id")
def recover_relink_cmd(film_id: str) -> None:
    """Recover an interrupted source relink for FILM_ID."""
    from pipeline.index.relink import recover_film_relink

    config = load_config()
    db = open_db(config)
    try:
        outcome = recover_film_relink(db, config, film_id)
    except (OSError, RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    if outcome is None:
        click.echo(f"No pending relink for {film_id}.")
    else:
        click.echo(f"Recovered {film_id} to the journal's {outcome} state.")


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
