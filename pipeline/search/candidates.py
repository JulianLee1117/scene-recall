"""Shared frame discovery; callers own eligibility, scoring and cut refinement."""
from __future__ import annotations

from typing import Any, Sequence


def frame_neighbors(db, vector, *, columns: Sequence[str], limit: int,
                    where: Any = None, exact: bool = False):
    """Search every eligible frame, including rows not yet in an ANN index.

    Do not collapse shots here: a layout caller may choose a different instant
    from an appearance caller. Raw distances are evidence within this space.
    """
    if limit < 1:
        return []
    query = db.open_table("frames").search(vector, vector_column_name="visual_vec").metric("cosine")
    if where is not None:
        query = query.where(where)
    if exact:
        query = query.bypass_vector_index()
    from pipeline.search.request import search_stage
    with search_stage("frame_retrieval"):
        return query.select(list(columns)).limit(limit).to_list()
