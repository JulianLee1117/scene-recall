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


def cmd_status(args) -> int:
    from pipeline.evidence import metadata, store
    config = _config(args.config)
    _db, films = _films(config, args.film)
    producers = {"metadata": metadata.PRODUCER}
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

    def add(name: str, handler, help_text: str):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("--film", action="append", help="film ID/prefix or title substring (repeatable)")
        command.set_defaults(handler=handler)
        return command

    add("metadata", cmd_metadata, "fetch open metadata (Wikidata, Wikipedia, Wikiquote, pageviews, IMDb votes)") \
        .add_argument("--force", action="store_true", help="refetch even when a current artifact exists")
    add("status", cmd_status, "show evidence coverage")
    args = parser.parse_args(argv)
    return int(args.handler(args) or 0)


if __name__ == "__main__":
    sys.exit(main())
