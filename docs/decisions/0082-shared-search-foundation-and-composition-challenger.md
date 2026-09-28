# ADR-0082: Shared search foundation and independent composition challenger

- Status: Accepted
- Date: 2026-09-20
- Supersedes: ADR-0009's whole-cache requirement for the new source-hashed cache
- Extends: ADR-0008, ADR-0020 and ADR-0059; their Match Cuts promotion gates remain

## Context

Repeated full-grid encoding makes Framing slow, incomplete cache coverage
discards useful work, and appearance-first retrieval can exclude useful layouts.
At roughly 491,000 retained frames, full 6x6x1024 float16 grids alone approach
34 GiB. Keeping that representation for every frame at 1,000 films is unsuitable.
The user approved a 64 GiB optional-search budget, a five-second warm Framing
p95 target and a twelve-reference human review before changing its ranking.

## Decision

Share pinned read-only snapshots and bounded frame retrieval between search
and editorial consumers. Keep their eligibility, final scoring and output
contracts separate. Reuse query vectors and hydrated metadata only inside one
request, with a 4 MiB bound and stage timings. Do not cache user queries globally.
Managed BTREE/BITMAP lookups carry still-valid feature manifests across
index-only version changes. They never certify previously stale evidence.

The new spatial cache uses source SHA-256, frame identity, current file metadata,
immutable extraction profile and descriptor checksum. Reuse valid entries and
infer only misses. Both live and cached grids undergo the same float16 round
trip. Cache occupancy cannot select candidates or provide a ranking advantage.
Legacy complete caches retain their existing validation during migration.

Fit experimental frozen, uncentered low-rank projections of existing PE spatial
cells, comparing 32 and 64 dimensions per cell. Sample films evenly and retain
at most 65,536 cells, the matrix, its checksum and sample provenance. Normalize
each projected cell, preserve screen positions, flatten and normalize for
ordinary cosine retrieval. This is not a pose, action or symbolism detector.

Composition retrieves independently over every eligible retained frame; start
with exact search. Union appearance and composition by frame identity, using
half of the 96-frame detailed budget each, RRF to fill overlaps and up to twelve
additional films. Detailed scoring keeps 65% global / 35% spatial weights;
choose each unit's best frame only afterward. Partial composition coverage
explicitly falls back to baseline Framing for the whole request. It never
silently searches only prepared films. Profile fitting/preparation alone does
not activate ranking; a validated review receipt and explicit config selection
are both required. Null config selection rolls back immediately after reload.

Prepare in the existing ingest worker, one durable job per film/profile/source
generation, yielding every 32 frames behind foreground work. New successful
ingests enqueue optional preparation. Storage or lock pressure pauses work;
explicit retry preserves the cursor. Canonical film readiness is independent.
Compact builds need not retain full grids. Optional storage accounting includes
physical history, indexes and scratch. Admission reserves conservatively and
fails closed at capacity. Cache eviction and version pruning use the existing
idle-only reader/writer guards and native Lance operations; report actual bytes
reclaimed. Do not delete internal Lance files or raw evidence manually.

Benchmark IVF_FLAT only in budgeted disposable databases. No production ANN
index is installed by evaluation, and no query uses `fast_search`. Candidate-unit
recall must be at least 99% in every evaluated route/scope, known positives must
remain and retrieval p95 must improve at least 30% before separate activation.
Composition requires complete coverage, warm p95 <=5 seconds, <=64 GiB actual
optional storage, at least 8/12 human preference wins, median nDCG@10 relative
improvement >=20%, at most two regressing cases and known-positive retention.

## Consequences and limits

The foundation changes no scene boundaries, raw films, context production or
editor action-timing behavior. Match Cuts stays in Labs and shares infrastructure;
the Framing receipt does not promote a new Lab matcher. More elaborate grounded
layout or temporal models remain subject to their existing decision gates.

Snapshots prevent concurrent publication from mixing reads within one request.
They do not make Lance's multiple tables a crash-atomic transaction or freeze
arbitrary legacy image files; source hashing protects cache reuse. Existing
publication repair semantics remain. Scalar indexes and optional preparations
do not establish the 1,000-film latency target without measurement.

The implementation and operational results are tracked in
`docs/experiments/search-foundation-implementation.md`.

## Technical sources checked

- [LanceDB vector search](https://docs.lancedb.com/search/vector-search): exact
  bypass, unquantized IVF_FLAT and inclusion of unindexed rows.
- [LanceDB multivector search](https://docs.lancedb.com/search/multivector-search):
  MaxSim does not preserve corresponding screen-cell positions.
- [PyTorch low-rank PCA](https://docs.pytorch.org/docs/2.11/generated/torch.pca_lowrank.html):
  explicit uncentered fitting and persisted projection directions.
