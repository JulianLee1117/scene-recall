"""cli.py — Click-based CLI entry point for the cinema-search pipeline.

Usage::

    python -m pipeline.cli ingest <film_path>
    python -m pipeline.cli eval [--queries pipeline/eval/gold_queries.yaml]

The ``ingest`` command runs the full ingest pipeline and prints a summary.
The ``eval`` command runs the evaluation harness against indexed films.
"""

from __future__ import annotations

from pathlib import Path

import click

from pipeline.config import load_config
from pipeline.ingest.pipeline import run_pipeline

_DEFAULT_QUERIES = Path(__file__).parent / "eval" / "gold_queries.yaml"


@click.group()
def cli() -> None:
    """Cinema search pipeline."""


@cli.command()
@click.argument("film_path", type=click.Path(exists=True, path_type=Path))
def ingest(film_path: Path) -> None:
    """Ingest FILM_PATH through the full pipeline and index it."""
    config = load_config()
    run_pipeline(film_path, config)


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
