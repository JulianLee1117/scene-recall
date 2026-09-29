"""Match-cut moment index: ``python -m pipeline.matching.moments <command>``.

``index`` rebuilds the library index from the ``moments`` evidence
(``python -m pipeline.evidence moments`` produces it); ``search`` prints the
matches for one reference instant.
"""

from __future__ import annotations

import argparse
import json
import sys


def main(argv: list[str] | None = None) -> int:
    from dotenv import load_dotenv
    load_dotenv()
    parser = argparse.ArgumentParser(prog="python -m pipeline.matching.moments", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", default=None)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("index", help="rebuild and publish the library moment index")
    search = commands.add_parser("search", help="print matches for a reference instant")
    search.add_argument("unit_id")
    search.add_argument("time", type=float)
    search.add_argument("--focus", default="auto")
    search.add_argument("--output", default="landscape")
    search.add_argument("--reframe", action="store_true")
    search.add_argument("--same-film", action="store_true")
    search.add_argument("--direction", default="next", choices=["next", "previous"])
    search.add_argument("--limit", type=int, default=12)
    search.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    from pipeline.config import load_config
    from pipeline.index.writer import open_db
    from pipeline.matching.moments import find, index
    config = load_config(args.config)
    if args.command == "index":
        index.build(config, open_db(config))
        return 0
    loaded = index.load(config)
    if loaded is None:
        print("No moment index is built yet: run `python -m pipeline.matching.moments index`.", file=sys.stderr)
        return 1
    result = find.find(loaded, find.Request(unit_id=args.unit_id, time=args.time, focus=args.focus, output=args.output,
                                            reframe=args.reframe, include_same_film=args.same_film,
                                            direction=args.direction, limit=args.limit))
    if args.json:
        print(json.dumps(result, indent=1))
        return 0
    reference = result["reference"]
    print(f"{reference['film_title']} @ {reference['time']:.2f}s  ({result['elapsed_ms']})")
    for rank, row in enumerate(result["results"], start=1):
        reasons = ", ".join(reason["label"] for reason in row["reasons"][:3])
        print(f"{rank:2d}. {row['score']:.3f}  {row['film_title'][:40]:40s} {row['time']:8.2f}s  {reasons}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
