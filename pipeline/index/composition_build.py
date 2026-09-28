"""Bounded local fitting of the two approved composition challengers."""
from collections import defaultdict
import hashlib
import json
import math

import numpy as np

from pipeline.index.composition import MAX_SAMPLE_CELLS, fit_projection, save_profile, profile_directory, _atomic_json
from pipeline.index.framing_cache import canonical_grid, load_partial_grids, read_input
from pipeline.index.framing_features import configured_framing_spatial_profile
from pipeline.index.search_features import _COLUMNS
from pipeline.index.snapshot import publication_read
from pipeline.ingest.embed import embed_spatial_images
from pipeline.ingest.locks import global_ingest_lock


def fitting_sample(db, grid_size=6):
    """Choose frames evenly across films before sampling individual cells."""
    with publication_read(db):
        rows = db.open_table("frames").search().select(_COLUMNS).limit(None).to_list()
    groups = defaultdict(list)
    for row in rows:
        groups[row["film_id"]].append(row)
    for group in groups.values():
        group.sort(key=lambda row: hashlib.sha256(row["frame_id"].encode()).digest())
    films = sorted(groups, key=lambda key: hashlib.sha256(key.encode()).digest())
    chosen = []
    position = 0
    maximum = math.ceil(MAX_SAMPLE_CELLS / grid_size**2)
    while len(chosen) < maximum:
        step = [groups[film][position] for film in films if position < len(groups[film])]
        if not step:
            break
        chosen.extend(step[:maximum - len(chosen)])
        position += 1
    return chosen


def fit_profiles(config, db, *, cell_dims=(32, 64), progress=lambda message: None, cancelled=lambda: False):
    from pipeline.lab.media import JobCancelled
    source_profile = configured_framing_spatial_profile(config)
    if source_profile is None:
        raise ValueError("Prepare the configured visual model before fitting composition evidence")
    rows = fitting_sample(db, source_profile.grid_size)
    values, provenance = [], []
    # Each quantum releases the shared ingest lock. A separate foreground CLI
    # taking that slot stops this explicit fit cleanly instead of racing the GPU.
    for start in range(0, len(rows), 32):
        if cancelled():
            raise JobCancelled("Composition fitting cancelled")
        with global_ingest_lock(config.paths.assets_dir):
            inputs = [read_input(row) for row in rows[start:start + 32]]
            cached = load_partial_grids(db, source_profile, inputs)
            missing = [item for item in inputs if item.source.frame_id not in cached]
            if missing:
                _, grids = embed_spatial_images([item.image() for item in missing], config,
                                                grid_size=source_profile.grid_size,
                                                model_revision=source_profile.model_revision)
                if grids is None or len(grids) != len(missing):
                    raise RuntimeError("Composition sample has incomplete spatial evidence")
                cached.update({item.source.frame_id: canonical_grid(grid, source_profile)
                               for item, grid in zip(missing, grids, strict=True)})
            for item in inputs:
                values.append(cached[item.source.frame_id].reshape(-1, source_profile.feature_dim))
                provenance.append({"frame_id": item.source.frame_id, "film_id": item.source.film_id,
                                   "path": str(item.source.path), "sha256": item.sha256})
        progress(f"Sampled {min(start + 32, len(rows))}/{len(rows)} retained frames")
    if not values:
        raise ValueError("No retained frames are available for composition fitting")
    sample = np.concatenate(values, axis=0)[:MAX_SAMPLE_CELLS]
    sample_hash = hashlib.sha256(sample.astype("<f4").tobytes() + json.dumps(provenance, sort_keys=True).encode()).hexdigest()
    result = []
    for cell_dim in cell_dims:
        if cancelled():
            raise JobCancelled("Composition fitting cancelled")
        progress(f"Fitting {cell_dim} dimensions per spatial cell")
        profile, matrix = fit_projection(sample, source_profile, sample_hash, cell_dim)
        save_profile(config, profile, matrix)
        from pipeline.index.search_storage import reserve_search_storage
        evidence = {"sample_sha256": sample_hash, "cells": len(sample), "seed": 0,
                    "center": False, "iterations": 4, "frames": provenance}
        with reserve_search_storage(config, len(json.dumps(evidence).encode()) * 2 + 4096):
            _atomic_json(profile_directory(config, profile.profile_id) / "sample.json", evidence)
        result.append(profile)
    return result
