"""cli.py — Click-based CLI entry point for the cinema-search pipeline.

Usage::

    python -m pipeline.cli ingest <film_path>

The ``ingest`` command runs the full ingest pipeline and prints a summary.
"""

from __future__ import annotations

from pathlib import Path

import click

from pipeline.config import load_config
from pipeline.ingest.pipeline import run_pipeline


@click.group()
def cli() -> None:
    """Cinema search pipeline."""


@cli.command()
@click.argument("film_path", type=click.Path(exists=True, path_type=Path))
def ingest(film_path: Path) -> None:
    """Ingest FILM_PATH through the full pipeline and index it."""
    config = load_config()
    run_pipeline(film_path, config)


if __name__ == "__main__":
    cli()
