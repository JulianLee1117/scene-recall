"""Explicit planning, preparation and inspection of optional search evidence."""
import json

import click

from pipeline.config import load_config
from pipeline.index.writer import open_db


@click.group("search-features")
def search_features():
    """Inspect and prepare reusable search evidence without re-ingesting films."""


def _queue_action(method):
    from pipeline.lab.store import LabStore
    config = load_config()
    store = LabStore(config.paths.state_dir, config.paths.assets_dir)
    try:
        click.echo(json.dumps(getattr(store, method)(), indent=2))
    except (OSError, RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc


@search_features.command("queue")
def queue():
    """Inspect optional preparation jobs and their saved cursors."""
    _queue_action("search_feature_queue")


@search_features.command("pause")
def pause():
    """Hold currently pending optional jobs; drain active preparation first."""
    _queue_action("pause_search_features")


@search_features.command("resume")
def resume():
    """Resume only jobs explicitly held by search-features pause."""
    _queue_action("resume_search_features")


@search_features.command("storage")
def storage():
    """Report physical optional-search usage, including retained versions."""
    from pipeline.index.search_storage import storage_status
    click.echo(json.dumps(storage_status(load_config()), indent=2))


@search_features.command("discard-cache")
@click.option("--table", required=True, help="Exact frame_framing cache table; source tables cannot be removed.")
@click.option("--apply", is_flag=True, help="Requires stopped API and workers; default only inspects.")
def discard_cache(table, apply):
    """Reclaim a rebuildable full-grid cache without removing films or evidence."""
    from pipeline.index.search_storage import discard_spatial_cache
    try:
        click.echo(json.dumps(discard_spatial_cache(load_config(), table, apply=apply), indent=2))
    except (OSError, RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc


@search_features.command("retire-profile")
@click.option("--profile", required=True)
@click.option("--apply", is_flag=True, help="Requires idle services and no pending jobs for this inactive profile.")
def retire_profile(profile, apply):
    """Reclaim one inactive compact profile after choosing the winning model."""
    from pipeline.index.search_storage import retire_composition_profile
    try:
        click.echo(json.dumps(retire_composition_profile(load_config(), profile, apply=apply), indent=2))
    except (OSError, RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc


@search_features.command("indexes")
@click.option("--apply", is_flag=True, help="Install managed scalar lookups; default only inspects.")
def indexes(apply):
    """Inspect or install scalar indexes without re-embedding source evidence."""
    from pipeline.index.search_indexes import install_lookup_indexes, lookup_plan
    config = load_config()
    try:
        db = open_db(config)
        result = install_lookup_indexes(config, db) if apply else lookup_plan(db)
        click.echo(json.dumps(result, indent=2))
    except (OSError, RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc


@search_features.command("prepare")
@click.option("--film-id", default=None, help="Published film identity.")
@click.option("--all-films", is_flag=True, help="Plan one bounded job per published film.")
@click.option("--composition-profile", default=None, help="Also prepare a fitted experimental composition profile.")
@click.option("--enqueue", is_flag=True, help="Queue preparation; default only shows the plan.")
def prepare(film_id, all_films, composition_profile, enqueue):
    """Prepare one film in bounded low-priority batches on the existing worker."""
    from pipeline.index.search_features import preparation_request
    from pipeline.lab.store import LabStore
    config = load_config()
    try:
        if bool(film_id) == bool(all_films):
            raise ValueError("Choose --film-id or --all-films")
        db = open_db(config)
        from pipeline.index.writer import published_film_ids
        ids = sorted(published_film_ids(db)) if all_films else [film_id]
        requests = [preparation_request(config, db, identity, composition_profile=composition_profile) for identity in ids]
        if enqueue:
            store = LabStore(config.paths.state_dir, config.paths.assets_dir)
            store.initialize()
            jobs = [store.enqueue_search_features(request) for request in requests]
            click.echo(json.dumps([{"job_id": job["id"], "status": job["status"]} for job in jobs], indent=2))
        else:
            click.echo(json.dumps(requests, indent=2))
    except (OSError, RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc


@search_features.command("review")
@click.option("--profile", required=True)
@click.option("--references", required=True, type=click.Path(exists=True, dir_okay=False))
@click.option("--output", required=True, type=click.Path(exists=False, dir_okay=False))
def review(profile, references, output):
    """Run twelve frozen baseline/challenger comparisons; leave human votes blank."""
    from pipeline.eval.search_comparison import compare_references
    from pathlib import Path
    config = load_config()
    try:
        result = compare_references(config, open_db(config), profile,
                                    json.loads(Path(references).read_text(encoding="utf-8")))
        with Path(output).open("x", encoding="utf-8") as handle:
            json.dump(result, handle, indent=2, allow_nan=False)
        click.echo(f"Saved comparison to {output}; no ranking was promoted.")
    except (OSError, RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc


@search_features.command("promote")
@click.option("--receipt", required=True, type=click.Path(exists=True, dir_okay=False))
def promote(receipt):
    """Validate a completed human review; config still selects the active profile."""
    from pathlib import Path
    from pipeline.eval.search_foundation import validate_promotion
    from pipeline.index.composition import ready_profile, profile_directory, _atomic_json
    from pipeline.index.search_storage import storage_status, reserve_search_storage
    from pipeline.index.snapshot import publication_read
    config = load_config()
    try:
        document = json.loads(Path(receipt).read_text(encoding="utf-8"))
        identity = document["profile_id"]
        validate_promotion(document, identity)
        if storage_status(config)["available_bytes"] <= 0:
            raise ValueError("Optional search storage is full")
        db = open_db(config)
        with reserve_search_storage(config, len(json.dumps(document).encode()) * 2 + 4096), publication_read(db):
            if ready_profile(config, db, identity) is None:
                raise ValueError("The profile no longer has complete source coverage")
            _atomic_json(profile_directory(config, identity) / "promotion.json", document)
        click.echo(f"Review accepted. Select retrieval.composition_profile: {identity} and reload services. Set null to roll back.")
    except (OSError, RuntimeError, ValueError, KeyError) as exc:
        raise click.ClickException(str(exc)) from exc


@search_features.command("benchmark")
@click.option("--ann", is_flag=True, help="Build IVF_FLAT only in a disposable, budgeted evaluation database.")
@click.option("--output", required=True, type=click.Path(dir_okay=False))
def benchmark(ann, output):
    """Measure frame retrieval per scope; never install a production vector index."""
    from pathlib import Path
    from pipeline.eval.search_benchmark import benchmark_frames
    try:
        config = load_config()
        result = benchmark_frames(config, open_db(config), ann=ann)
        with Path(output).open("x", encoding="utf-8") as handle:
            json.dump(result, handle, indent=2)
        click.echo(f"Saved retrieval measurements to {output}")
    except (OSError, RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc


@search_features.command("fit-composition")
@click.option("--apply", is_flag=True, help="Fit the two local challengers; default shows the sample size.")
@click.option("--enqueue", is_flag=True, help="Fit after ingestion and cache jobs, then queue compact preparation.")
def fit_composition(apply, enqueue):
    """Fit frozen 32/64-dimensional spatial projections; never activate ranking."""
    from dataclasses import asdict
    from pipeline.index.composition_build import fitting_sample, fit_profiles
    config = load_config()
    try:
        if apply and enqueue:
            raise ValueError("Choose immediate --apply or background --enqueue")
        if enqueue:
            from pipeline.lab.store import LabStore
            store = LabStore(config.paths.state_dir, config.paths.assets_dir)
            store.initialize()
            job = store.enqueue_composition_fit()
            click.echo(json.dumps({"job_id": job["id"], "status": job["status"], "promoted": False}, indent=2))
            return
        db = open_db(config)
        result = ([asdict(profile) for profile in fit_profiles(config, db, progress=click.echo)] if apply else
                  {"sample_frames": len(fitting_sample(db)), "cell_dimensions": [32, 64], "activates_search": False})
        click.echo(json.dumps(result, indent=2))
    except (OSError, RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
