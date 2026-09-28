"""Source-hashed partial acceleration of the existing spatial scorer.

This cache has no coverage gate and cannot introduce candidates. Its numerical
contract is the same float16 round trip for cached and newly computed grids.
Legacy complete-cache tables remain readable by the old path during rollout.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import hashlib
from io import BytesIO
from pathlib import Path

import numpy as np
import pyarrow as pa
from PIL import Image

from pipeline.index.framing_features import (
    FramingSpatialSource, configured_framing_spatial_profile, decode_descriptor,
    encode_descriptor, framing_feature_is_current, make_framing_feature_rows,
    _any_frame,
)
from pipeline.index.schema import make_framing_features_schema
from pipeline.index.writer import table_names

CONTRACT = "source-hashed-spatial-cache-v2"


def cache_table_name(profile):
    return profile.table_name + "_source_v2"


def cache_schema():
    return make_framing_features_schema().append(pa.field("source_sha256", pa.string()))


def resolve_partial_profile(config, db):
    names = table_names(db)
    if not any(name.startswith("frame_framing") and name.endswith("_source_v2") for name in names):
        return None
    try:
        profile = configured_framing_spatial_profile(config)
        if profile is not None and cache_table_name(profile) in names:
            if db.open_table(cache_table_name(profile)).schema.equals(cache_schema()):
                return profile
    except (OSError, RuntimeError, ValueError):
        pass
    return None


@dataclass(frozen=True)
class FrameInput:
    source: FramingSpatialSource
    sha256: str
    data: bytes

    def image(self):
        with Image.open(BytesIO(self.data)) as image:
            return image.convert("RGB")


def read_input(row):
    path = Path(row["path"])
    before = path.stat()
    data = path.read_bytes()
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError("Keyframe changed while reading source evidence")
    for name, actual in (("source_size", after.st_size), ("source_mtime_ns", after.st_mtime_ns)):
        if row.get(name) is not None and row[name] != actual:
            raise ValueError("Indexed keyframe source metadata is stale")
    source = FramingSpatialSource(str(row["frame_id"]), str(row["film_id"]),
                                 str(row["unit_id"]), path, after.st_size, after.st_mtime_ns)
    return FrameInput(source, hashlib.sha256(data).hexdigest(), data)


def canonical_grid(grid, profile):
    return decode_descriptor(encode_descriptor(np.asarray(grid), profile), profile)


def load_partial_grids(db, profile, inputs):
    """A missing, duplicate, stale or corrupt row invalidates only that entry."""
    if not inputs or cache_table_name(profile) not in table_names(db):
        return {}
    ids = list(dict.fromkeys(item.source.frame_id for item in inputs))
    rows = []
    try:
        table = db.open_table(cache_table_name(profile))
        for start in range(0, len(ids), 32):
            rows.extend(table.search().where(_any_frame(ids[start:start + 32])).limit(None).to_list())
    except (OSError, RuntimeError, ValueError):
        return {}
    counts = Counter(row["frame_id"] for row in rows)
    sources = {item.source.frame_id: item for item in inputs}
    result = {}
    for row in rows:
        source = sources.get(row["frame_id"])
        if source is None or counts[row["frame_id"]] != 1:
            continue
        if (row.get("source_sha256") == source.sha256
                and framing_feature_is_current(source.source, row, profile)):
            result[row["frame_id"]] = decode_descriptor(row["descriptor"], profile)
    return result


def resolve_candidate_grids(rows, limit, db, config, profile, encode):
    """Read bounded source bytes once; decode and infer only cache misses."""
    inputs, selected = [], []
    for row in rows[:limit]:
        try:
            inputs.append(read_input(row))
            selected.append(row)
        except (OSError, ValueError, KeyError):
            continue
    cached = load_partial_grids(db, profile, inputs)
    missing, images, usable = [], [], []
    for row, item in zip(selected, inputs, strict=True):
        if item.source.frame_id not in cached:
            try:
                images.append(item.image())
            except (OSError, ValueError):
                continue
            missing.append(item)
        usable.append(row)
    if images:
        _, grids = encode(images, config, grid_size=profile.grid_size, model_revision=profile.model_revision)
        if grids is None or len(grids) != len(missing):
            raise RuntimeError("Spatial encoder did not return every requested descriptor")
        cached.update({item.source.frame_id: canonical_grid(grid, profile)
                       for item, grid in zip(missing, grids, strict=True)})
    matrix = (np.stack([cached[row["frame_id"]] for row in usable]) if usable else
              np.empty((0, profile.grid_size, profile.grid_size, profile.feature_dim), dtype=np.float32))
    return [*usable, *rows[limit:]], matrix, len(usable) - len(missing)


def write_cache_rows(db, profile, inputs, grids):
    """Publish only inputs that still match their current source frame rows.

    The caller owns the optional storage reservation and the ingest work slot.
    Encoding must have completed before entering this short publication lock.
    """
    from pipeline.index.writer import _PUBLICATION_LOCK, _database_write_lock, _merge_rows
    rows = make_framing_feature_rows([item.source for item in inputs], grids, profile)
    for row, item in zip(rows, inputs, strict=True):
        row["source_sha256"] = item.sha256
    if not rows:
        return 0
    with _PUBLICATION_LOCK, _database_write_lock(db):
        current = db.open_table("frames").search().where(_any_frame([row["frame_id"] for row in rows])).limit(None).to_list()
        counts = Counter(row["frame_id"] for row in current)
        by_id = {row["frame_id"]: row for row in current}
        accepted = []
        for row, item in zip(rows, inputs, strict=True):
            frame = by_id.get(row["frame_id"])
            if frame is None or counts[row["frame_id"]] != 1:
                continue
            try:
                now = read_input(frame)
            except (OSError, ValueError, KeyError):
                continue
            if now.source == item.source and now.sha256 == item.sha256:
                accepted.append(row)
        if not accepted:
            return 0
        name = cache_table_name(profile)
        db.create_table(name, schema=cache_schema(), exist_ok=True)
        if not db.open_table(name).schema.equals(cache_schema()):
            raise ValueError("Partial Framing cache schema is incompatible")
        _merge_rows(db, name, "frame_id", accepted)
        return len(accepted)
