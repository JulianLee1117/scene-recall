"""schema.py — PyArrow schemas for the LanceDB index tables.

Two tables are defined:

``units``
    One row per indexable shot/sub-segment.  Vectors are float32 with a
    configurable dimension (default 1024 for PE core L/14; 1152 for SigLIP-2).

``films``
    One row per ingested film file.

Vector dimension
----------------
Use :func:`make_units_schema` to build the ``units`` schema for a specific
encoder dimension.  The default ``VECTOR_DIM`` (1024) matches PE core L/14.
Switching to SigLIP-2 (1152-dim) only requires passing the new dim to
:func:`pipeline.index.writer.create_tables`.
"""

from __future__ import annotations

import pyarrow as pa

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Default embedding dimension (PE core L/14).
VECTOR_DIM: int = 1024

# ---------------------------------------------------------------------------
# Schema factories
# ---------------------------------------------------------------------------


def make_units_schema(vector_dim: int = VECTOR_DIM) -> pa.Schema:
    """Return a PyArrow schema for the ``units`` table with *vector_dim*-dim vectors.

    Parameters
    ----------
    vector_dim:
        Embedding dimension.  Defaults to :data:`VECTOR_DIM` (1024 for PE core L/14).
        Pass 1152 for SigLIP-2 (``google/siglip2-so400m-patch14-384``).
    """
    return pa.schema(
        [
            # --- identity ---
            pa.field("unit_id", pa.string()),           # primary key (== shot_id in Phase 1)
            pa.field("film_id", pa.string()),
            pa.field("shot_id", pa.string()),
            # --- timing ---
            pa.field("t_start", pa.float64()),
            pa.field("t_end", pa.float64()),
            # --- dedup flag (Phase 2 will set this to False for duplicates) ---
            pa.field("is_representative", pa.bool_()),
            # --- vectors (L2-normalised float32) ---
            pa.field("img_vec", pa.list_(pa.float32(), vector_dim)),
            pa.field("txt_vec", pa.list_(pa.float32(), vector_dim)),
            # --- annotation ---
            pa.field("caption", pa.string()),
            pa.field("searchable_text", pa.string()),
            pa.field("mood", pa.string()),              # JSON-serialised list[str]
            pa.field("dialogue", pa.string()),          # JSON-serialised list[str]
            pa.field("keyframe_paths", pa.string()),    # JSON-serialised list[str]
        ]
    )


#: Default schema for the ``units`` table (PE core L/14, 1024 dims).
UNITS_SCHEMA: pa.Schema = make_units_schema()

#: Schema for the ``films`` table.
FILMS_SCHEMA: pa.Schema = pa.schema(
    [
        pa.field("film_id", pa.string()),           # primary key
        pa.field("title", pa.string()),
        pa.field("path", pa.string()),              # str(Path)
        pa.field("duration", pa.float64()),
    ]
)
