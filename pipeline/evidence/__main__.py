"""Evidence v2 command line: ``python -m pipeline.evidence <command>``.

Commands operate on every published film unless ``--film`` selectors are
given (film ID, ID prefix of 8+ characters, or a title substring).
"""

from __future__ import annotations

import argparse
import sys

from dotenv import load_dotenv


def _config(path: str | None):
    from pipeline.config import load_config
    return load_config(path)


def _films(config, selectors):
    from pipeline.evidence.library import resolve_films
    from pipeline.index.writer import open_db
    db = open_db(config)
    return db, resolve_films(db, selectors)


def cmd_metadata(args) -> int:
    from pipeline.evidence import compile as compiler, metadata
    config = _config(args.config)
    db, films = _films(config, args.film)
    counts = metadata.run(config, films, force=args.force)
    print(f"[metadata] {counts}")
    compiler.compile_film_meta(config, db, _films(config, None)[1])
    return 0 if counts["failed"] == 0 else 1


def cmd_subtitles(args) -> int:
    from pipeline.evidence import subtitles
    config = _config(args.config)
    _db, films = _films(config, args.film)
    counts = subtitles.run(config, films, max_downloads=args.max_downloads, force=args.force)
    print(f"[subtitles] {counts}")
    return 0


def cmd_understand(args) -> int:
    from pipeline.evidence import understanding
    config = _config(args.config)
    db, films = _films(config, args.film)
    if args.batch == "submit":
        print(f"[understanding:batch] {understanding.submit_batches(config, db, films, model=args.model, max_usd=args.max_usd, max_chunks=args.max_chunks)}")
        return 0
    if args.batch == "collect":
        summary = understanding.collect_batches(config, db)
        print(f"[understanding:batch] {summary}")
        return 0 if summary["failed"] == 0 else 1
    if args.batch == "run":
        summary = understanding.run_batches(config, db, films, model=args.model, max_usd=args.max_usd)
        print(f"[understanding:batch] {summary}")
        return 0 if summary["failed"] == 0 else 1
    if args.batch == "status":
        for job in understanding.batch_status(config):
            print(f"{job['created']}  {job['state']:26s} {job['chunks']:4d} chunks  {job['name']}")
        return 0
    summary = understanding.run(config, db, films, model=args.model, max_usd=args.max_usd,
                                concurrency=args.concurrency, force=args.force)
    print(f"[understanding] {summary}")
    return 0 if summary["chunks_failed"] == 0 else 1


def cmd_measure(args) -> int:
    from pipeline.evidence import measure
    config = _config(args.config)
    db, films = _films(config, args.film)
    counts = measure.run(config, db, films, force=args.force)
    print(f"[measure] {counts}")
    return 0 if counts["failed"] == 0 else 1


def cmd_hero(args) -> int:
    from pipeline.evidence import hero
    config = _config(args.config)
    db, films = _films(config, args.film)
    counts = hero.run(config, db, films, force=args.force)
    print(f"[hero] {counts}")
    return 0 if counts["failed"] == 0 else 1


def cmd_synthesize(args) -> int:
    from pipeline.evidence import synthesis
    config = _config(args.config)
    db, films = _films(config, args.film)
    counts = synthesis.run(config, db, films)
    print(f"[synthesis] {counts}")
    return 0


def cmd_compile(args) -> int:
    from pipeline.evidence import compile as compiler
    config = _config(args.config)
    db, films = _films(config, args.film)
    if args.rebuild:
        # Compiled tables hold no primary data: drop them and recompile every film.
        from pipeline.evidence import tables
        for name in (tables.SHOT_EVIDENCE, tables.SCENES, tables.DIALOGUE_LINES):
            tables.drop_table(db, name)
        films = _films(config, None)[1]
    compiler.compile_film_meta(config, db, _films(config, None)[1])
    compiler.compile_films(config, db, films)
    if args.no_text:
        return 0
    # Semantic views (story, scene, mood, measured camera) read the compiled
    # tables; a full reconciliation re-embeds only changed views and activates
    # the profile once every unit is covered.
    from pipeline.index.backfill_text import backfill_text_features
    result = backfill_text_features(config)
    print(f"[compile] text views: {result.embedded} embedded, {result.skipped_current} current, "
          f"profile {'active' if result.activated else 'NOT active'}")
    return 0 if result.activated else 1


def cmd_refresh_dialogue(args) -> int:
    """Re-ingest films with an accepted synced subtitle that their published dialogue does not use yet."""
    import json
    from pathlib import Path

    from pipeline.evidence import compile as compiler
    from pipeline.evidence.subtitles import accepted_download
    from pipeline.ingest.pipeline import run_pipeline
    config = _config(args.config)
    db, films = _films(config, args.film)

    def adopted(film) -> bool:
        download = accepted_download(config, film.film_id)
        if download is None:
            return True
        path = Path(config.paths.assets_dir) / film.film_id / "dialogue.manifest.json"
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        return manifest.get("kind") == "downloaded_srt" and manifest.get("sha256") == download["synced_sha256"]

    stale = [film for film in films if not adopted(film)]
    print(f"[dialogue] {len(stale)} of {len(films)} films have an accepted synced subtitle to adopt")
    # Library-scale passes (batch understanding, measure) run separately; only
    # the dialogue-dependent derivations refresh here.
    config.ingest.evidence = False
    failed = 0
    for number, film in enumerate(stale, start=1):
        try:
            # Shots, keyframes and captions are cached; dialogue, embeddings and text views refresh.
            run_pipeline(film.path, config)
            print(f"[dialogue] {number}/{len(stale)} {film.title}: re-ingested", flush=True)
        except Exception as exc:  # noqa: BLE001 - one film must not stop the batch
            failed += 1
            print(f"[dialogue] {number}/{len(stale)} {film.title}: failed ({str(exc)[:300]})", flush=True)
    if stale:
        compiler.compile_films(config, db, stale)
    return 0 if failed == 0 else 1


def cmd_refresh(args) -> int:
    from pipeline.evidence.pipeline import refresh_films
    config = _config(args.config)
    db, films = _films(config, args.film)
    summary = refresh_films(config, db, films, hosted=not args.local_only, measure_pass=not args.skip_measure)
    print(f"[evidence] {summary}")
    return 0 if not any(isinstance(result, dict) and "error" in result for result in summary.values()) else 1


def cmd_status(args) -> int:
    from pipeline.evidence import metadata, speech, store, subtitles
    config = _config(args.config)
    _db, films = _films(config, args.film)
    from pipeline.evidence import hero, measure, synthesis, understanding
    producers = {"metadata": metadata.PRODUCER, "subtitles": subtitles.PRODUCER, "audio": speech.PRODUCER,
                 "understanding": understanding.producer(), "measure": measure.PRODUCER, "hero": hero.PRODUCER,
                 "synthesis": synthesis.PRODUCER}
    for kind, producer in producers.items():
        present = sum(1 for film in films if store.read_artifact(config.paths.assets_dir, film.film_id, producer))
        print(f"{kind:14s} {producer.profile_id:34s} {present}/{len(films)} films")
    return 0


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(prog="python -m pipeline.evidence", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", default=None, help="config.yaml path (default: CINEMA_CONFIG or ./config.yaml)")
    commands = parser.add_subparsers(dest="command", required=True)

    parsers = {}

    def add(name: str, handler, help_text: str):
        command = commands.add_parser(name, help=help_text)
        parsers[name] = command
        command.add_argument("--film", action="append", help="film ID/prefix or title substring (repeatable)")
        command.set_defaults(handler=handler)
        return command

    add("metadata", cmd_metadata, "fetch open metadata (Wikidata, Wikipedia, Wikiquote, pageviews, IMDb votes)") \
        .add_argument("--force", action="store_true", help="refetch even when a current artifact exists")
    subs = add("subtitles", cmd_subtitles, "download, sync and validate English subtitles for Whisper-only films")
    subs.add_argument("--max-downloads", type=int, default=20, help="download budget for this run (daily quota applies)")
    subs.add_argument("--force", action="store_true", help="reprocess films with an existing result")
    und = add("understand", cmd_understand, "hosted video understanding: scenes, story, per-shot action, fame and craft")
    und.add_argument("--model", default="gemini-3.8-flash")
    und.add_argument("--max-usd", type=float, default=5.0, help="spend ceiling for this run (standard pricing)")
    und.add_argument("--concurrency", type=int, default=6)
    und.add_argument("--force", action="store_true")
    und.add_argument("--batch", choices=["run", "submit", "collect", "status"],
                     help="half-price batch transport: run (submit, poll and collect until done), or one step")
    und.add_argument("--max-chunks", type=int, default=None, help="with --batch submit: cap chunks this run")
    mea = add("measure", cmd_measure, "local GPU pass: camera motion, hidden cuts, subjects, look and hero frames")
    mea.add_argument("--force", action="store_true")
    add("hero", cmd_hero, "pick and extract one hero frame per shot from measured samples")         .add_argument("--force", action="store_true")
    add("synthesize", cmd_synthesize, "per-shot priors: fame, craft, distinctiveness, iconic and hidden-gem flags")
    add("compile", cmd_compile, "rebuild search tables (film_meta, shot_evidence, scenes, dialogue_lines) and text views")         .add_argument("--no-text", action="store_true", help="skip the semantic text-view refresh")
    parsers["compile"].add_argument("--rebuild", action="store_true",
                                    help="drop compiled tables and recompile every film (after a schema change)")
    add("refresh-dialogue", cmd_refresh_dialogue,
        "re-ingest films with an accepted synced subtitle their dialogue does not use yet")
    ref = add("refresh", cmd_refresh, "run every evidence pass for the selected films (cached passes skip)")
    ref.add_argument("--local-only", action="store_true", help="skip hosted passes (metadata, subtitles, understanding)")
    ref.add_argument("--skip-measure", action="store_true", help="skip the GPU measurement pass")
    add("status", cmd_status, "show evidence coverage")
    args = parser.parse_args(argv)
    return int(args.handler(args) or 0)


if __name__ == "__main__":
    sys.exit(main())
