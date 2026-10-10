# ADR-0120: Framing runs live; its grid caches and challenger are retired

- Status: Accepted
- Date: 2026-10-10
- Supersedes: ADR-0009; ADR-0082's source-hashed cache, preparation jobs and compact composition challenger; ADR-0092
- Superseded by: None

## Context

Framing compares a reference frame's layout with candidates through 6x6 PE
feature grids. Two caches were meant to spare each search from encoding its
roughly 96 candidates:

- The complete cache (ADR-0009) activates only while it covers every frame. It
  was built for 38 films, went stale as the library grew, and search never read
  it beside the partial cache.
- The source-hashed partial cache (ADR-0082) was filled by per-film preparation
  jobs until 134 of them were cancelled on 2026-10-01. It held 23 complete films
  and one partial: 10% of frames.

On 2026-10-10 four Framing searches took 3-4 s whether or not the partial cache
hit; the one with no hits was the fastest, because validating an entry means
hashing the candidate's source image. The compact composition challenger
(ADR-0082) was never selected and had no tables. The framing representation
pilot (ADR-0092) was frozen.

## Decision

- Framing encodes the query and its candidates in one request, with the same
  loaded model. No grid is cached.
- Removed: both caches and `index-framing`; the search-features preparation and
  fitting jobs, their `search-features` CLI and ledger kinds; the compact
  composition challenger and its profiles; the framing pilot; and the
  optional-storage budget (`retrieval.optional_storage_gib`). Scalar lookup
  indexes, the one other user of that budget, check free disk space instead,
  under `pipeline.cli index-lookups`.
- The cache tables, `feature-manifests/framing` and any `search-profiles` are
  dropped while the API and workers are stopped.
- Framing's next representation is the match-cut moments index (ADR-0099). It
  already measures subject place and size, silhouettes, poses, light and lines
  at 4 fps for every shot and updates itself after ingest. It replaces the 6x6
  grids only after ADR-0008's comparison on 10-15 owner-reviewed references,
  with a query-time moments pass for uploaded images.

## Consequences

- About 5,000 lines of code and tests, and 12.3 GB of derived data, go without
  a measured change in Framing latency, which stays at 3-4 s until the
  moments-based Framing lands.
- Framing and Match Cuts stay two products: Framing finds shots composed like a
  reference (shot level; motion ignored), Match Cuts finds where to cut from
  one instant (eye trace, motion, cut point, crop). Sharing the moments index
  will make them agree, and Vision can then explain both.
