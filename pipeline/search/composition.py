"""Independent, exact composition challenger with explicit promotion gates."""
from contextlib import contextmanager
from contextvars import ContextVar
import json
import logging
from functools import reduce
from operator import or_

import numpy as np
from lancedb.expr import col, lit

from pipeline.index.composition import profile_directory, project_grids, ready_profile
from pipeline.index.framing_cache import canonical_grid, resolve_candidate_grids
from pipeline.search.candidates import frame_neighbors

_SHADOW = ContextVar("composition_shadow", default=None)
_LOG = logging.getLogger(__name__)


@contextmanager
def shadow_profile(identity):
    """Evaluation only: scoped to this execution, never change global config."""
    token = _SHADOW.set(identity)
    try:
        yield
    finally:
        _SHADOW.reset(token)


def selected_profile(config, db):
    identity = _SHADOW.get() or config.retrieval.composition_profile
    if not identity:
        return None
    try:
        if not _SHADOW.get():
            path = profile_directory(config, identity) / "promotion.json"
            receipt = (db.read_profile_manifest(path) if getattr(db, "is_index_snapshot", False) is True
                       else json.loads(path.read_text(encoding="utf-8")))
            from pipeline.eval.search_foundation import validate_promotion
            validate_promotion(receipt, identity)
        ready = ready_profile(config, db, identity)
        if ready is None:
            raise ValueError("complete current-library composition coverage is unavailable")
        return ready
    except (OSError, ValueError, TypeError, KeyError) as exc:
        _LOG.warning("Composition unavailable; using baseline Framing: %s", exc)
        return None


def balanced_union(appearance, composition, *, limit=96, reserve=12):
    """Equal independent budgets, deterministic RRF fill and cross-film reserve."""
    routes = [appearance, composition]
    scores, rows = {}, {}
    for route in routes:
        seen = set()
        for rank, row in enumerate(route, 1):
            identity = row["frame_id"]
            if identity in seen:
                continue
            seen.add(identity)
            rows.setdefault(identity, dict(row))
            scores[identity] = scores.get(identity, 0) + 1 / (60 + rank)
    selected = {}
    for route, quota in zip(routes, (limit // 2, limit - limit // 2), strict=True):
        for row in route[:quota]:
            selected.setdefault(row["frame_id"], rows[row["frame_id"]])
    order = sorted(rows, key=lambda identity: (-scores[identity], identity))
    for identity in order:
        if len(selected) >= limit:
            break
        selected.setdefault(identity, rows[identity])
    films = {row["film_id"] for row in selected.values()}
    added = 0
    for identity in order:
        row = rows[identity]
        if added >= reserve:
            break
        if row["film_id"] not in films:
            selected[identity] = row
            films.add(row["film_id"])
            added += 1
    return list(selected.values())


def independent_candidates(db, profile, matrix, query_global, query_grid, film_ids=(), *, limit=96, reserve=12):
    """Retain frames until detailed scoring; compact-only hits get true PE scores."""
    where = reduce(or_, (col("film_id") == lit(identity) for identity in film_ids)) if film_ids else None
    columns = ["frame_id", "film_id", "unit_id", "shot_id", "frame_index", "timestamp", "path",
               "source_size", "source_mtime_ns", "_distance"]
    pool = max(limit * 6, 600)
    appearance = frame_neighbors(db, query_global, columns=columns, limit=pool, where=where, exact=True)
    vector = project_grids(np.asarray([query_grid]), profile, matrix)[0]
    query = db.open_table(profile.table_name).search(vector, vector_column_name="vector").metric("cosine").bypass_vector_index()
    if where is not None:
        query = query.where(where)
    hits = query.select(["frame_id", "film_id", "unit_id"]).limit(pool).to_list()
    ids = [row["frame_id"] for row in hits]
    if not ids:
        return balanced_union(appearance, [], limit=limit, reserve=reserve)
    from pipeline.index.framing_features import _any_frame
    hydrated = db.open_table("frames").search().where(_any_frame(ids)).select(
        [column for column in columns if column != "_distance"] + ["visual_vec"]
    ).limit(len(ids)).to_list()
    by_id = {row["frame_id"]: row for row in hydrated}
    compact = []
    normalized_query = np.array(query_global, dtype=np.float32, copy=True)
    normalized_query /= max(float(np.linalg.norm(normalized_query)), 1e-12)
    for hit in hits:
        row = by_id.get(hit["frame_id"])
        if row is None:
            continue
        visual = np.asarray(row.pop("visual_vec"), dtype=np.float32)
        row["_distance"] = float(1 - np.dot(visual, normalized_query) / max(float(np.linalg.norm(visual)), 1e-12))
        compact.append(row)
    return balanced_union(appearance, compact, limit=limit, reserve=reserve)


def rank_candidates(db, config, ready, source_profile, query_global, query_grid, film_ids, *, limit, reserve, encode, score):
    profile, matrix = ready
    if profile.source_profile_id != source_profile.profile_id:
        raise ValueError("Composition and detailed scoring profiles differ")
    grid = canonical_grid(query_grid, source_profile)
    rows = independent_candidates(db, profile, matrix, query_global, grid, film_ids, limit=limit, reserve=reserve)
    if not rows:
        return []
    rows, grids, hits = resolve_candidate_grids(rows, len(rows), db, config, source_profile, encode)
    if not len(grids):
        return []
    spatial = score(grid, grids)
    semantic_order = sorted(range(len(rows)), key=lambda i: (float(rows[i]["_distance"]), rows[i]["frame_id"]))
    semantic_ranks = {index: rank for rank, index in enumerate(semantic_order, 1)}
    spatial_order = sorted(range(len(spatial)), key=lambda i: (-float(spatial[i]), rows[i]["frame_id"]))
    spatial_ranks = {index: rank for rank, index in enumerate(spatial_order, 1)}
    for index, row in enumerate(rows):
        semantic = max(-1., min(1., 1 - float(row["_distance"])))
        row.update(_reference_score=.65 * semantic + .35 * float(spatial[index]),
                   _semantic_score=semantic, _semantic_rank=semantic_ranks[index],
                   _semantic_rank_scope="candidate_union", _composition_profile=profile.profile_id,
                   _spatial_score=float(spatial[index]), _spatial_rank=spatial_ranks[index])
    rows.sort(key=lambda row: (-row["_reference_score"], row["frame_id"]))
    # The first (best scored) frame per unit wins only now, after layout evidence.
    units = {}
    for row in rows:
        units.setdefault(row["unit_id"], row)
    _LOG.info("Composition profile=%s frames=%d units=%d cache_hits=%d", profile.profile_id, len(rows), len(units), hits)
    return list(units.values())
