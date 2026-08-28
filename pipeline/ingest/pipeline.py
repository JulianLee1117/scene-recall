"""pipeline.py — full ingest orchestrator.

Wires all pipeline stages together in order:
  probe → dialogue → shots → media → visual embed → annotate → publish
  → optional semantic-text derivation

Usage::

    from pipeline.ingest.pipeline import run_pipeline
    from pipeline.config import load_config

    config = load_config()
    film = run_pipeline(Path("/path/to/film.mkv"), config)

Idempotency rules
-----------------
- ``probe``    — always runs (needed to obtain the FilmRecord and film_id)
- ``dialogue`` — skipped when ``<asset_dir>/dialogue.json`` exists (loaded from cache)
- ``shots``    — reuses a compatible ``<asset_dir>/shots.json`` cache
- ``media``    — validates and resumes every expected keyframe and preview
- ``annotate`` — reuses a matching profile-versioned per-shot cache
- ``embed`` / ``write`` — always run; unit and frame writes are batched
"""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np
from filelock import Timeout as FileLockTimeout

from pipeline.config import Config
from pipeline.index.writer import (
    FrameWrite,
    UnitWrite,
    create_tables,
    open_db,
    publish_film_index,
    require_visual_encoder_profile,
    require_current_film_source,
)
from pipeline.ingest.annotate import annotate_shot
from pipeline.ingest.dialogue import DialogueLine, extract_dialogue
from pipeline.ingest.embed import (
    embed_images,
    embed_text,
    get_vector_dim,
    pool_image_embeddings,
)
from pipeline.ingest.locks import (
    film_operation_lock,
    global_ingest_lock,
    require_no_pending_film_relink,
)
from pipeline.ingest.media import extract_media, keyframe_seek_time
from pipeline.ingest.probe import FilmRecord, probe_film
from pipeline.ingest.shots import Shot, detect_shots


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run_pipeline(film_path: Path, config: Config) -> FilmRecord:
    """Run one ingest while holding the machine-wide ingest lock."""
    ingest_lock = global_ingest_lock(config.paths.assets_dir)
    try:
        ingest_lock.acquire()
    except FileLockTimeout as exc:
        raise RuntimeError(
            "another film ingest is already running; wait for it to finish"
        ) from exc
    try:
        return _run_pipeline_with_film_lock(film_path, config)
    finally:
        ingest_lock.release()


def _run_pipeline_with_film_lock(film_path: Path, config: Config) -> FilmRecord:
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
    candidate = probe_film(film_path, config)
    with film_operation_lock(candidate.asset_dir):
        # The source can change while this process waits behind another ingest
        # or relink. Re-probe under the lock and never mutate the candidate
        # film's cache if its content-addressed identity changed.
        film = probe_film(film_path, config)
        if film.film_id != candidate.film_id:
            raise RuntimeError(
                "source film identity changed while waiting for its ingest lock"
            )
        print(f"[probe] {time.perf_counter() - t:.2f}s")
        return _run_pipeline_locked(film, config, total_start)


def _run_pipeline_locked(
    film: FilmRecord,
    config: Config,
    total_start: float,
) -> FilmRecord:
    """Run cache and index mutations while holding this film's lock."""
    require_no_pending_film_relink(film.asset_dir)
    db = open_db(config)
    require_current_film_source(db, film)

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
    # Stage 3: Shots — reuse the recipe-validated cache when available
    # ------------------------------------------------------------------
    t = time.perf_counter()
    shots = detect_shots(film, config)
    print(f"[shots] {time.perf_counter() - t:.2f}s")

    # ------------------------------------------------------------------
    # Stage 4: Media — validate and resume each expected artifact
    # ------------------------------------------------------------------
    t = time.perf_counter()
    extract_media(film, shots, config)
    print(f"[media] {time.perf_counter() - t:.2f}s")

    # ------------------------------------------------------------------
    # Stages 5-7: Embed + Annotate + Write — always run (per shot)
    # ------------------------------------------------------------------
    create_tables(db, vector_dim=get_vector_dim(config))
    require_visual_encoder_profile(db, config)

    t = time.perf_counter()
    pending_frames: list[FrameWrite] = []
    shot_image_vectors: list[np.ndarray] = []
    shot_keyframes = [
        [
            film.asset_dir / "keyframes" / f"{shot.shot_id}_{frame_index}.webp"
            for frame_index in range(len(shot.keyframe_times))
        ]
        for shot in shots
    ]
    all_keyframes = [
        path
        for keyframes in shot_keyframes
        for path in keyframes
    ]
    all_frame_vectors = embed_images(all_keyframes, config)
    if len(all_frame_vectors) != len(all_keyframes):
        raise ValueError(
            "visual encoder returned an unexpected number of frame vectors: "
            f"requested {len(all_keyframes)}, returned {len(all_frame_vectors)}"
        )
    frame_cursor = 0
    for shot, keyframes in zip(shots, shot_keyframes, strict=True):
        next_cursor = frame_cursor + len(keyframes)
        frame_vectors = all_frame_vectors[frame_cursor:next_cursor]
        frame_cursor = next_cursor
        shot_image_vectors.append(pool_image_embeddings(frame_vectors))
        for frame_index, (path, vector) in enumerate(
            zip(keyframes, frame_vectors, strict=True)
        ):
            pending_frames.append(
                FrameWrite(
                    unit_id=shot.shot_id,
                    shot_id=shot.shot_id,
                    frame_index=frame_index,
                    timestamp=keyframe_seek_time(shot, frame_index),
                    path=path,
                    visual_encoder=config.models.visual_encoder,
                    visual_vec=vector,
                    is_representative=frame_index == len(keyframes) // 2,
                )
            )
    print(f"[embed] {len(all_keyframes)} keyframes", flush=True)

    shot_dialogues = [
        [
            line for line in dialogue
            if line.start < shot.t_end and line.end > shot.t_start
        ]
        for shot in shots
    ]
    annotations = _annotate_shots(
        shots,
        shot_keyframes,
        shot_dialogues,
        film,
        config,
    )

    text_vectors = embed_text(
        [annotation["searchable_text"] for annotation in annotations],
        config,
    )
    if len(text_vectors) != len(annotations):
        raise ValueError(
            "text encoder returned an unexpected number of vectors: "
            f"requested {len(annotations)}, returned {len(text_vectors)}"
        )
    pending_units = [
        UnitWrite(
            shot=shot,
            annotation=annotation,
            img_vec=img_vec,
            txt_vec=txt_vec,
            dialogue=[line.text for line in shot_dialogue],
        )
        for shot, annotation, img_vec, shot_dialogue, txt_vec in zip(
            shots,
            annotations,
            shot_image_vectors,
            shot_dialogues,
            text_vectors,
            strict=True,
        )
    ]

    # Prepare every local and hosted result before touching the current
    # searchable generation. The final unit merge is the publication boundary.
    publish_film_index(db, film, pending_units, pending_frames)
    print(f"[frames] {len(pending_frames)} indexed")

    # Semantic text features are a replaceable local derivation, not part of
    # the film publication transaction.  Build this film's independent
    # caption/dialogue/OCR/facet views after publication. If the configured
    # weights are unavailable, the completeness manifest stays stale and
    # search safely continues through the legacy PE text vector.
    try:
        from pipeline.index.backfill_text import (
            backfill_text_features_during_ingest,
        )

        text_result = backfill_text_features_during_ingest(
            config,
            film_id=film.film_id,
        )
        state = "active" if text_result.activated else "shadow"
        print(
            f"[text-features] {text_result.embedded} embedded "
            f"({state})",
            flush=True,
        )
    except Exception as exc:
        print(
            "[text-features] deferred; legacy text search remains active: "
            f"{exc}",
            flush=True,
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


def _annotate_shots(
    shots: list[Shot],
    shot_keyframes: list[list[Path]],
    shot_dialogues: list[list[DialogueLine]],
    film: FilmRecord,
    config: Config,
) -> list[dict]:
    """Annotate every shot with bounded concurrency, preserving shot order.

    Hosted annotation is network-bound, so requests overlap safely.  Each
    completed response is durably cached by ``annotate_shot`` before the next
    is awaited; a failure therefore aborts the film without losing paid work,
    and queued (not yet started) requests are cancelled instead of billed.
    """
    total = len(shots)
    cache_dir = film.asset_dir / "annotations"
    annotations: list[dict | None] = [None] * total
    completed = 0

    def annotate_one(index: int) -> dict:
        return annotate_shot(
            shots[index],
            shot_keyframes[index],
            shot_dialogues[index],
            config,
            cache_dir=cache_dir,
        )

    workers = min(max(1, config.ingest.annotation_concurrency), max(total, 1))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(annotate_one, index): index for index in range(total)
        }
        try:
            for future in as_completed(futures):
                index = futures[future]
                annotations[index] = future.result()
                completed += 1
                if completed % 25 == 0 or completed == total:
                    print(f"[annotate] {completed}/{total}", flush=True)
        except BaseException:
            for pending in futures:
                pending.cancel()
            raise

    # future.result() re-raises any failure, so success fills every index.
    return [annotation for annotation in annotations if annotation is not None]


def _load_dialogue(path: Path) -> list[DialogueLine]:
    """Load a cached ``dialogue.json`` and return a list of :class:`DialogueLine`."""
    data: list[dict] = json.loads(path.read_text(encoding="utf-8"))
    return [DialogueLine(**d) for d in data]
