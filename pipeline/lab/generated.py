"""Generated sources: AI clips registered by content so edits can place them like film shots (ADR-0109).

A generated clip is new source media, not a derived annotation. Its bytes live
under ``state_dir/lab/generated`` named by their digest, and one row in the
``lab_generated`` table gives it a film-shaped identity (``gen-<sha256[:20]>``)
with its provenance. Library films never mix with it: search, ingestion and the
films table do not see these rows; only source resolution falls back to them.
"""
from __future__ import annotations

import hashlib
import json
import math
import shutil
import time
from pathlib import Path

import pyarrow as pa

TABLE = "lab_generated"
PREFIX = "gen-"
SCHEMA = pa.schema([
    pa.field("film_id", pa.string()),
    pa.field("title", pa.string()),
    pa.field("path", pa.string()),
    pa.field("duration", pa.float64()),
    pa.field("fps", pa.float64()),
    pa.field("sha256", pa.string()),
    pa.field("provenance", pa.string()),      # JSON: provider, model, prompt, endpoint frames, task
    pa.field("created_at", pa.float64()),
])
_FILM_KEYS = ("film_id", "title", "path", "duration", "fps")


def is_generated(film_id: str) -> bool:
    return str(film_id).startswith(PREFIX)


def find(db, film_id: str) -> dict | None:
    """The film-shaped row of a registered generated clip, or None."""
    from lancedb.expr import col, lit
    from pipeline.index.writer import table_names

    if not is_generated(film_id) or TABLE not in table_names(db):
        return None
    rows = db.open_table(TABLE).search().where(col("film_id") == lit(film_id)).limit(1).to_list()
    if not rows or rows[0].get("film_id") != film_id:
        return None
    return {key: rows[0][key] for key in _FILM_KEYS}


def _video_facts(path: Path) -> tuple[float, float]:
    from pipeline.lab.media import probe_media

    probe = probe_media(path)
    video = next((stream for stream in probe.get("streams", []) if stream.get("codec_type") == "video"), None)
    if video is None:
        raise ValueError("A generated source must contain video")
    duration = float(video.get("duration") or probe.get("format", {}).get("duration") or 0)
    num, _, den = str(video.get("avg_frame_rate") or "0/1").partition("/")
    fps = float(num) / float(den or 1) if float(den or 1) else 0.0
    if not (math.isfinite(duration) and 0 < duration <= 120 and math.isfinite(fps) and 1 <= fps <= 120):
        raise ValueError("A generated source needs a readable video of at most two minutes")
    return duration, fps


def register(config, db, source: Path, title: str, provenance: dict) -> dict:
    """Copy a generated clip into Lab state by content and record it; registering it again returns the same row."""
    from pipeline.index.writer import _PUBLICATION_LOCK, _database_write_lock, table_names

    source = Path(source)
    with source.open("rb") as handle:
        digest = hashlib.file_digest(handle, "sha256").hexdigest()
    film_id = PREFIX + digest[:20]
    existing = find(db, film_id)
    if existing and Path(existing["path"]).is_file():
        return existing
    duration, fps = _video_facts(source)
    destination = Path(config.paths.state_dir) / "lab" / "generated" / f"{film_id}{source.suffix.lower() or '.mp4'}"
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.is_file():
        partial = destination.with_suffix(destination.suffix + ".partial")
        shutil.copyfile(source, partial)
        partial.replace(destination)
    row = {"film_id": film_id, "title": str(title)[:300], "path": str(destination), "duration": duration, "fps": fps,
           "sha256": digest, "provenance": json.dumps(provenance, sort_keys=True), "created_at": time.time()}
    with _PUBLICATION_LOCK, _database_write_lock(db):
        if TABLE not in table_names(db):
            db.create_table(TABLE, schema=SCHEMA, exist_ok=True)
        table = db.open_table(TABLE)
        table.delete(f"film_id = '{film_id}'")
        table.add(pa.Table.from_pylist([row], schema=SCHEMA))
    return {key: row[key] for key in _FILM_KEYS}


def main(argv=None):
    import argparse

    from pipeline.config import load_config
    from pipeline.index.writer import open_db

    parser = argparse.ArgumentParser(description="Register a generated clip as a Lab source")
    parser.add_argument("path", type=Path)
    parser.add_argument("--title", required=True)
    parser.add_argument("--provenance", default="{}", help="JSON object: provider, model, prompt, endpoint frames")
    args = parser.parse_args(argv)
    config = load_config()
    print(json.dumps(register(config, open_db(config), args.path, args.title, json.loads(args.provenance))))


if __name__ == "__main__":
    main()
