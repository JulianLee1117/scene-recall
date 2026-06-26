"""retrieve.py — Dense semantic search over the LanceDB units table.

Public API
----------
``search(query, db, config)``
    Embed the query text, run kNN over ``txt_vec``, return the top-20
    representative units as a list of dicts ready for JSON serialisation.
"""

from __future__ import annotations

import lancedb
import numpy as np

from pipeline.config import Config
from pipeline.ingest.embed import embed_text


def search(query: str, db: lancedb.DBConnection, config: Config) -> list[dict]:
    """Dense semantic search over unit text embeddings.

    Embeds *query* with the configured text encoder, then runs approximate
    nearest-neighbour search over the ``txt_vec`` column of the ``units``
    table, filtering to ``is_representative = true``.

    Parameters
    ----------
    query:
        Free-text search query from the user.
    db:
        Open LanceDB connection (from :func:`pipeline.index.writer.open_db`).
    config:
        Pipeline configuration; selects the text-embedding model.

    Returns
    -------
    list[dict]
        Up to 20 results ordered by cosine distance.  Each dict contains:
        ``unit_id``, ``film_id``, ``t_start``, ``t_end``,
        ``caption``, ``keyframe_url``, ``preview_url``.
    """
    # embed_text returns shape (N, D); take the single query row.
    vec: np.ndarray = embed_text([query], config)[0]

    rows = (
        db.open_table("units")
        .search(vec, vector_column_name="txt_vec")
        .metric("cosine")
        .limit(20)
        .where("is_representative = true")
        .to_list()
    )

    results: list[dict] = []
    for row in rows:
        shot_id: str = row["shot_id"]
        results.append(
            {
                "unit_id": row["unit_id"],
                "film_id": row["film_id"],
                "t_start": row["t_start"],
                "t_end": row["t_end"],
                "caption": row["caption"],
                "keyframe_url": f"/media/keyframe/{shot_id}/0",
                "preview_url": f"/media/preview/{shot_id}",
            }
        )

    return results
