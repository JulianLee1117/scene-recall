"""pipeline.py — full ingest orchestrator.

Wires all pipeline stages together in order:
  probe → dialogue → shots → media → embed → annotate → write

Usage::

    from pipeline.ingest.pipeline import run_pipeline
    from pipeline.config import load_config

    config = load_config()
    film = run_pipeline(Path("/path/to/film.mkv"), config)

Idempotency rules
-----------------
- ``probe``    — always runs (needed to obtain the FilmRecord and film_id)
- ``dialogue`` — skipped when ``<asset_dir>/dialogue.json`` exists (loaded from cache)
- ``shots``    — always runs (no reliable on-disk artefact to check)
- ``media``    — skipped when ``<asset_dir>/keyframes/`` is non-empty
- ``embed`` / ``annotate`` / ``write`` — always run per shot
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np

from pipeline.config import Config
from pipeline.index.writer import create_tables, open_db, write_film, write_unit
from pipeline.ingest.annotate import annotate_shot
from pipeline.ingest.dialogue import DialogueLine, extract_dialogue
from pipeline.ingest.embed import embed_text, get_vector_dim, shot_embedding
from pipeline.ingest.media import extract_media
from pipeline.ingest.probe import FilmRecord, probe_film
from pipeline.ingest.shots import detect_shots


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run_pipeline(film_path: Path, config: Config) -> FilmRecord:
    """Run the full ingest pipeline for *film_path* and return its :class:`FilmRecord`.

    Parameters
    ----------
    film_path:
        Path to the film file to ingest.
    config:
        Loaded pipeline configuration.

    Returns
    -------
    FilmRecord
        Populated record for the ingested film.
    """
    total_start = time.perf_counter()

    # ------------------------------------------------------------------
    # Stage 1: Probe — always run (provides film_id + FilmRecord)
    # ------------------------------------------------------------------
    t = time.perf_counter()
    film = probe_film(film_path, config)
    print(f"[probe] {time.perf_counter() - t:.2f}s")

    # ------------------------------------------------------------------
    # Stage 2: Dialogue — skip if cached
    # ------------------------------------------------------------------
    dialogue_path = film.asset_dir / "dialogue.json"
    if dialogue_path.exists():
        print("[dialogue] skipped (cached)")
        dialogue = _load_dialogue(dialogue_path)
    else:
        t = time.perf_counter()
        dialogue = extract_dialogue(film, config)
        print(f"[dialogue] {time.perf_counter() - t:.2f}s")

    # ------------------------------------------------------------------
    # Stage 3: Shots — always run
    # ------------------------------------------------------------------
    t = time.perf_counter()
    shots = detect_shots(film, config)
    print(f"[shots] {time.perf_counter() - t:.2f}s")

    # ------------------------------------------------------------------
    # Stage 4: Media — skip if keyframes dir is non-empty
    # ------------------------------------------------------------------
    kf_dir = film.asset_dir / "keyframes"
    if kf_dir.exists() and any(kf_dir.iterdir()):
        print("[media] skipped (cached)")
    else:
        t = time.perf_counter()
        extract_media(film, shots, config)
        print(f"[media] {time.perf_counter() - t:.2f}s")

    # ------------------------------------------------------------------
    # Stages 5-7: Embed + Annotate + Write — always run (per shot)
    # ------------------------------------------------------------------
    db = open_db(config)
    create_tables(db, vector_dim=get_vector_dim(config))
    write_film(db, film)

    t = time.perf_counter()
    for shot in shots:
        img_vec: np.ndarray = shot_embedding(shot, film.asset_dir, config)
        keyframes = [
            film.asset_dir / "keyframes" / f"{shot.shot_id}_{i}.webp"
            for i in range(len(shot.keyframe_times))
        ]
        shot_dialogue = [
            line for line in dialogue
            if line.start < shot.t_end and line.end > shot.t_start
        ]
        try:
            annotation = annotate_shot(shot, keyframes, shot_dialogue, config)
        except Exception as e:
            print(f"  [annotate] shot {shot.shot_id} failed: {e}, skipping")
            annotation = {"caption": "", "mood": [], "searchable_text": ""}
        txt_vec: np.ndarray = embed_text([annotation["searchable_text"]], config)[0]
        write_unit(
            db,
            film,
            shot,
            annotation,
            img_vec,
            txt_vec,
            dialogue=[line.text for line in shot_dialogue],
        )
    print(f"[embed+annotate+write] {time.perf_counter() - t:.2f}s")

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    total_time = time.perf_counter() - total_start
    units_tbl = db.open_table("units")
    row_count = units_tbl.count_rows()
    print(
        f"\nSummary: {len(shots)} shots | {total_time:.1f}s total | {row_count} DB rows"
    )

    return film


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _load_dialogue(path: Path) -> list[DialogueLine]:
    """Load a cached ``dialogue.json`` and return a list of :class:`DialogueLine`."""
    data: list[dict] = json.loads(path.read_text(encoding="utf-8"))
    return [DialogueLine(**d) for d in data]
