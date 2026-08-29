# ADR-0009: Complete optional Framing spatial cache

- Status: Accepted
- Date: 2026-08-29
- Supersedes: None
- Superseded by: None

## Context

Production Framing first searches the legacy PE frame vectors, then loads and
re-encodes up to 96 candidate keyframes to obtain learned 6x6 spatial grids.
The candidate bound keeps model work roughly constant as the library grows,
but repeating that GPU work makes Framing materially slower than other search
facets. The same keyframe evidence is already retained locally, so the grid is
a replaceable derivation and does not require raw-film reingestion.

Adding grids to the legacy `frames` rows would deepen an already unversioned
visual-table exception. Reading any available subset would also let cached and
newly computed evidence drift across model or library versions and make query
behavior dependent on which films happened to be backfilled.

At the current 53,414-frame corpus size, uncompressed 6x6x1024 grids require
about 7.88 GB at float32 or 3.94 GB at float16 before database overhead. A
five-query probe over 97 real library grids measured maximum float16 spatial
score error of 0.0000371, median Kendall rank correlation of 1.0 (minimum
0.99956), identical top-ten order and top-48 membership in all five cases, and
one adjacent ordering swap inside one top 48.

## Decision

Keep Framing candidate generation and its 65% global / 35% spatial scoring
formula unchanged. Store the candidate spatial grids as an optional separate
profile table keyed by stable frame identity. The cache is reranking evidence,
not a Lance ANN vector column, a new visual space, or a Match Cut profile.

Use little-endian float16 storage under an explicit storage contract. Scope
each physical table and manifest to the configured encoder name, resolved
immutable model snapshot, row-schema and extraction-contract versions, grid
and feature dimensions, storage dtype, and relevant OpenCLIP, timm, and Torch
versions, including Torchvision and Pillow preprocessing lineage. Backfill and
cache-backed queries both load the exact recorded checkpoint revision;
configuration and weight discovery cannot independently follow mutable
`main`. Checksum each descriptor payload when it is loaded and during
idempotent reconciliation so corruption is repaired rather than skipped.

Activate a cache only when its atomic manifest proves exact coverage of the
current published `frames` generation: matching table version and row count,
stable frame-ID digest, profile-table version and row count, and current source
and profile metadata for every frame. A missing, partial, stale, duplicate,
corrupt, or incompatible profile contributes no cached evidence. The entire
query uses the established live candidate encoder instead; cached and live
candidate grids are never mixed.

Provide an explicit, local, idempotent `index-framing` backfill over existing
keyframes. It shares the global ingest lock and never decodes source films or
calls a hosted provider. A scoped run may add one film, but it activates only
if all other current frames already have compatible rows. Scoped
reconciliation also removes obsolete rows belonging to that film, including
when it no longer has any canonical frames, without touching another film.
Publishing a new film invalidates the old manifest immediately. During a
multi-film batch, Framing safely uses live reranking; one full reconciliation
after the batch embeds only new or stale frames and atomically restores cache
activation.

Do not add this optional derivation to the canonical film-publication
transaction. Keeping ingest publishable without it is more important than
maintaining acceleration continuously during a batch.

## Consequences

- Warm Framing queries encode only the reference image and load roughly 7 MB
  of cached candidate grids for a full 96-frame shortlist.
- Current raw cache storage is roughly 3.94 GB, or 72 KiB per PE Core frame,
  before Lance overhead; a future quantization change requires a new profile.
- Float16 can cause a minute adjacent-rank change for nearly tied candidates,
  but it preserves the scorer and measured top-result behavior while halving
  the dominant storage and read volume.
- Backfill cost is local GPU inference over each new or stale keyframe. No
  annotation payment, raw-media decode, or film reingestion is required.
- Interactive acceleration temporarily disappears after each film publication
  until reconciliation, while correctness and availability remain unchanged
  through the whole-query live fallback.
- Validating the uncached stable-ID digest adds about 59-71 ms per active-cache
  query at 53,414 frames on the current machine. Avoiding that scan would
  require a robust physical-generation identity; caching only URI, Lance
  version, and row count can miss a same-shape table replacement.
