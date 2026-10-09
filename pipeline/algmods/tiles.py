"""Tile bank for the cross-film mosaic: every indexed keyframe as a small colour block.

Build once (``python -m pipeline.algmods.tiles build``), then the mosaic matches each cell of a
shot against these blocks. The index is version scoped and backfillable from the keyframes alone.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

TILES_VERSION = "tiles-v1"
BLOCK_W, BLOCK_H = 8, 5          # keyframes are ~1.85:1; the block keeps that shape

_LOG = logging.getLogger(__name__)


def index_path(assets_dir: Path) -> Path:
    return Path(assets_dir) / "algmods" / f"{TILES_VERSION}.npz"


def _block(path: str) -> np.ndarray | None:
    from PIL import Image
    try:
        with Image.open(path) as im:
            im = im.convert("RGB")
            im.draft("RGB", (BLOCK_W * 8, BLOCK_H * 8))
            small = im.resize((BLOCK_W, BLOCK_H), Image.Resampling.BOX)
            return np.asarray(small, np.uint8)
    except Exception:          # a damaged keyframe is skipped, never fatal
        return None


def _film_blocks(args: tuple[str, str]) -> tuple[str, list[str], np.ndarray]:
    film_id, keyframes_dir = args
    paths = sorted(str(p) for p in Path(keyframes_dir).glob("*.webp"))
    blocks, kept = [], []
    for p in paths:
        b = _block(p)
        if b is not None:
            blocks.append(b)
            kept.append(Path(p).name)
    return film_id, kept, (np.stack(blocks) if blocks else np.zeros((0, BLOCK_H, BLOCK_W, 3), np.uint8))


def build(assets_dir: Path, *, workers: int = 4, limit_films: int | None = None, progress=print) -> dict:
    """Index every film's keyframes. Returns a summary; writes ``index_path(assets_dir)``."""
    assets_dir = Path(assets_dir)
    films = sorted(d for d in assets_dir.iterdir() if (d / "keyframes").is_dir())
    if limit_films:
        films = films[:limit_films]
    jobs = [(d.name, str(d / "keyframes")) for d in films]
    film_ids: list[str] = []
    names: list[str] = []
    film_index: list[int] = []
    blocks: list[np.ndarray] = []
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for n, (film_id, kept, arr) in enumerate(pool.map(_film_blocks, jobs, chunksize=1), 1):
            if len(arr):
                film_ids.append(film_id)
                names.extend(kept)
                film_index.extend([len(film_ids) - 1] * len(kept))
                blocks.append(arr)
            progress(f"tiles {n}/{len(jobs)} films, {len(names)} keyframes")
    all_blocks = np.concatenate(blocks) if blocks else np.zeros((0, BLOCK_H, BLOCK_W, 3), np.uint8)
    out = index_path(assets_dir)
    out.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(out, version=TILES_VERSION, block_w=BLOCK_W, block_h=BLOCK_H,
                        film_ids=np.array(film_ids), names=np.array(names), film_index=np.array(film_index, np.int32),
                        blocks=all_blocks, mean=all_blocks.reshape(len(all_blocks), -1, 3).mean(axis=1).astype(np.float32))
    summary = {"version": TILES_VERSION, "films": len(film_ids), "tiles": len(names), "path": str(out)}
    (out.with_suffix(".json")).write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def load(assets_dir: Path) -> dict:
    data = np.load(index_path(assets_dir), allow_pickle=False)
    if str(data["version"]) != TILES_VERSION:
        raise ValueError("Tile index version mismatch; rebuild with `python -m pipeline.algmods.tiles build`")
    return {key: data[key] for key in data.files}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m pipeline.algmods.tiles")
    sub = parser.add_subparsers(dest="command", required=True)
    b = sub.add_parser("build")
    b.add_argument("--workers", type=int, default=4)
    b.add_argument("--limit-films", type=int, default=None)
    args = parser.parse_args(argv)
    from pipeline.config import load_config
    config = load_config()
    if args.command == "build":
        summary = build(config.paths.assets_dir, workers=args.workers, limit_films=args.limit_films)
        print(json.dumps(summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())
