"""schema.py — PyArrow schemas for the LanceDB index tables.

Three tables are defined:

``units``
    One row per indexable shot/sub-segment.  Vectors are float32 with a
    configurable dimension (default 1024 for PE core L/14; 1152 for SigLIP-2).

``films``
    One row per ingested film file.

``frames``
    One row per extracted keyframe with a versioned row contract and its
    independent visual embedding.

Model-specific text feature tables are created separately with
:func:`make_text_features_schema`.  They intentionally do not share the
legacy ``units.txt_vec`` column, allowing a new text model to be built,
validated, activated, and removed without migrating the durable shot rows.

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

#: Current row/schema contract for the ``frames`` table.
FRAMES_SCHEMA_VERSION: int = 1

#: Current row contract for model-specific text feature tables.
TEXT_FEATURES_SCHEMA_VERSION: int = 1

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
            pa.field("unit_id", pa.string()),           # primary key (currently == shot_id)
            pa.field("film_id", pa.string()),
            pa.field("shot_id", pa.string()),
            # Null for base shots; the unsplit shot's ID for sub-segments, so
            # editing tools can recover a long take from its equal splits.
            pa.field("parent_shot_id", pa.string()),
            # --- timing ---
            pa.field("t_start", pa.float64()),
            pa.field("t_end", pa.float64()),
            # --- representative/search-visibility flag ---
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
            # --- typed cinematography facets (see annotate.FACET_VOCAB) ---
            pa.field("framing", pa.string()),
            pa.field("setting", pa.string()),
            pa.field("time_of_day", pa.string()),
            pa.field("people_count", pa.int32()),       # null when the model can't tell
            pa.field("energy", pa.string()),
            pa.field("camera_motion", pa.string()),
            pa.field("palette", pa.string()),           # JSON-serialised list[str]
            pa.field("subjects", pa.string()),          # JSON-serialised list[str]
            pa.field("on_screen_text", pa.string()),    # "" when no legible text
        ]
    )


def make_frames_schema(vector_dim: int = VECTOR_DIM) -> pa.Schema:
    """Return the version-1 ``frames`` schema for *vector_dim* visual vectors.

    ``timestamp`` is the reconstructed ffmpeg seek time used by the legacy
    keyframe extractor, not the decoded frame's packet presentation timestamp.
    """
    return pa.schema(
        [
            pa.field("schema_version", pa.int16()),
            pa.field("frame_id", pa.string()),
            pa.field("film_id", pa.string()),
            pa.field("unit_id", pa.string()),
            pa.field("shot_id", pa.string()),
            pa.field("frame_index", pa.int16()),
            pa.field("timestamp", pa.float64()),
            pa.field("timestamp_source", pa.string()),
            pa.field("path", pa.string()),
            pa.field("is_representative", pa.bool_()),
            # Populated by a later local quality-analysis pass.
            pa.field("quality_score", pa.float32()),
            # Detect regenerated files without re-embedding every repeat run.
            pa.field("source_size", pa.int64()),
            pa.field("source_mtime_ns", pa.int64()),
            pa.field("visual_encoder", pa.string()),
            pa.field("visual_vec", pa.list_(pa.float32(), vector_dim)),
        ]
    )


def make_text_features_schema(vector_dim: int) -> pa.Schema:
    """Return the schema for one compatible semantic-text vector profile.

    A table contains several independent textual views of each shot, but only
    one model revision and vector dimension.  A future incompatible model gets
    a new table rather than mutating or mixing this vector space.
    """
    if vector_dim < 1:
        raise ValueError("vector_dim must be positive")
    return pa.schema(
        [
            pa.field("schema_version", pa.int16()),
            pa.field("feature_id", pa.string()),
            pa.field("profile_id", pa.string()),
            pa.field("model_id", pa.string()),
            pa.field("model_revision", pa.string()),
            pa.field("film_id", pa.string()),
            pa.field("unit_id", pa.string()),
            pa.field("view", pa.string()),
            pa.field("text", pa.string()),
            pa.field("source_sha256", pa.string()),
            pa.field("is_representative", pa.bool_()),
            pa.field("vector", pa.list_(pa.float32(), vector_dim)),
        ]
    )


#: Default schema for the ``units`` table (PE core L/14, 1024 dims).
UNITS_SCHEMA: pa.Schema = make_units_schema()

#: Default schema for the version-1 ``frames`` table.
FRAMES_SCHEMA: pa.Schema = make_frames_schema()

#: Schema for the ``films`` table.
FILMS_SCHEMA: pa.Schema = pa.schema(
    [
        pa.field("film_id", pa.string()),           # primary key
        pa.field("title", pa.string()),
        pa.field("path", pa.string()),              # str(Path)
        pa.field("duration", pa.float64()),
        pa.field("fps", pa.float64()),              # for frame-accurate clip cuts
    ]
)
