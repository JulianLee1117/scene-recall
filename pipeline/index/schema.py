"""schema.py — PyArrow schemas for the LanceDB index tables.

Two tables are defined:

``units``
    One row per indexable shot/sub-segment.  Vectors are 1024-dimensional
    float32, matching the PE core L/14 encoder used in Phase 1.

``films``
    One row per ingested film file.

Vector dimension
----------------
Both ``img_vec`` and ``txt_vec`` are fixed at **1024 dimensions** for Phase 1
(PE core L/14 visual encoder via CLIP).  Changing the encoder in Phase 2 will
require a schema migration.
"""

from __future__ import annotations

import pyarrow as pa

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Embedding dimension for PE core L/14.  Fixed for Phase 1.
VECTOR_DIM: int = 1024

# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

#: Schema for the ``units`` table.
UNITS_SCHEMA: pa.Schema = pa.schema(
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
        # --- vectors (PE core L/14, L2-normalised float32, dim=1024) ---
        pa.field("img_vec", pa.list_(pa.float32(), VECTOR_DIM)),
        pa.field("txt_vec", pa.list_(pa.float32(), VECTOR_DIM)),
        # --- annotation ---
        pa.field("caption", pa.string()),
        pa.field("searchable_text", pa.string()),
        pa.field("mood", pa.string()),              # JSON-serialised list[str]
        pa.field("dialogue", pa.string()),          # JSON-serialised list[str]
        pa.field("keyframe_paths", pa.string()),    # JSON-serialised list[str]
    ]
)

#: Schema for the ``films`` table.
FILMS_SCHEMA: pa.Schema = pa.schema(
    [
        pa.field("film_id", pa.string()),           # primary key
        pa.field("title", pa.string()),
        pa.field("path", pa.string()),              # str(Path)
        pa.field("duration", pa.float64()),
    ]
)
