"""Bounded, resumable preparation in the existing ingestion worker lane."""
from dataclasses import asdict
import hashlib
import json

import numpy as np
from lancedb.expr import col, lit

from pipeline.index.framing_cache import load_partial_grids, read_input, write_cache_rows
from pipeline.index.framing_features import configured_framing_spatial_profile
from pipeline.index.search_storage import reserve_search_storage
from pipeline.index.snapshot import publication_read
from pipeline.ingest.embed import embed_spatial_images
from pipeline.ingest.locks import global_ingest_lock

BATCH_SIZE = 32
_COLUMNS = ["frame_id", "film_id", "unit_id", "path", "source_size", "source_mtime_ns", "visual_encoder"]


def film_frames(db, film_id):
    return sorted(db.open_table("frames").search().where(col("film_id") == lit(film_id))
                  .select(_COLUMNS).limit(None).to_list(), key=lambda row: row["frame_id"])


def source_generation(rows):
    return hashlib.sha256(json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def preparation_request(config, db, film_id, *, composition_profile=None):
    profile = configured_framing_spatial_profile(config)
    if profile is None:
        raise ValueError("Prepare the configured visual model before caching search features")
    with publication_read(db):
        rows = film_frames(db, film_id)
    if not rows:
        raise ValueError("Film has no published frames")
    if any(row["visual_encoder"] != profile.encoder_name for row in rows):
        raise ValueError("Film frames use an incompatible visual profile")
    if composition_profile is not None:
        from pipeline.index.composition import load_profile
        compact, _ = load_profile(config, composition_profile)
        if compact.source_profile_id != profile.profile_id:
            raise ValueError("Composition profile uses a different spatial encoder")
    return {"film_id": film_id, "source_generation": source_generation(rows),
            "profile": asdict(profile), "frame_count": len(rows), "composition_profile": composition_profile}


def queue_published_film(config, db, film_id):
    """One optional job after successful ingest; never part of film readiness."""
    from pipeline.lab.store import LabStore
    options = preparation_request(config, db, film_id,
                                  composition_profile=config.retrieval.composition_profile)
    store = LabStore(config.paths.state_dir, config.paths.assets_dir)
    store.initialize()
    return store.enqueue_search_features(options)


def prepare_batch(config, db, options, cursor="", *, cancelled=lambda: False):
    """One work quantum; no inference or disk budget scan under publication lock."""
    from pipeline.lab.media import JobCancelled
    if cancelled():
        raise JobCancelled("Search feature preparation cancelled")
    profile = configured_framing_spatial_profile(config)
    if profile is None or asdict(profile) != options["profile"]:
        raise ValueError("Queued feature profile changed; prepare a fresh request")
    with global_ingest_lock(config.paths.assets_dir):
        with publication_read(db):
            rows = film_frames(db, options["film_id"])
        if source_generation(rows) != options["source_generation"]:
            return {"done": True, "superseded": True, "cursor": cursor, "written": 0}
        batch = [row for row in rows if row["frame_id"] > cursor][:BATCH_SIZE]
        if not batch:
            return {"done": True, "cursor": cursor, "written": 0}
        inputs = [read_input(row) for row in batch]
        current = load_partial_grids(db, profile, inputs)
        missing = [item for item in inputs if item.source.frame_id not in current]
        written = 0
        if missing:
            _, grids = embed_spatial_images([item.image() for item in missing], config,
                                                grid_size=profile.grid_size, model_revision=profile.model_revision)
            if cancelled():
                raise JobCancelled("Search feature preparation cancelled")
            if grids is None or len(grids) != len(missing):
                raise RuntimeError("Spatial encoder returned incomplete evidence")
            from pipeline.index.framing_cache import canonical_grid
            current.update({item.source.frame_id: canonical_grid(grid, profile)
                            for item, grid in zip(missing, grids, strict=True)})
            # A compact build must not require retaining full grids for the
            # whole library. Full-grid caching is a separate optional job.
            if not options.get("composition_profile"):
                estimate = len(missing) * (profile.grid_size**2 * profile.feature_dim * 2 + 4096) * 3 + 1024**2
                with reserve_search_storage(config, estimate):
                    written = write_cache_rows(db, profile, missing, grids)
        composition_written = 0
        composition_ready = False
        if options.get("composition_profile"):
            from pipeline.index.composition import load_profile, write_features, publish_coverage
            compact, matrix = load_profile(config, options["composition_profile"])
            if compact.source_profile_id != profile.profile_id:
                raise ValueError("Composition and spatial evidence profiles differ")
            with reserve_search_storage(config, len(inputs) * (compact.dimension * 4 + 4096) * 3 + 1024**2):
                composition_written = write_features(db, compact, matrix, inputs,
                    np.stack([current[item.source.frame_id] for item in inputs]))
            if batch[-1]["frame_id"] == rows[-1]["frame_id"]:
                from pipeline.index.composition import reconcile_film
                reconcile_film(db, compact, options["film_id"])
                composition_ready = publish_coverage(config, db, compact)
        return {"done": batch[-1]["frame_id"] == rows[-1]["frame_id"],
                "cursor": batch[-1]["frame_id"], "written": written,
                "processed": len(batch), "cached": len(inputs) - len(missing),
                "composition_written": composition_written, "composition_ready": composition_ready}
