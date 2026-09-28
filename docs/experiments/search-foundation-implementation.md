# Search foundation implementation

Execution record for the approved September 20 search plan. The architecture
contract and accepted ADRs remain authoritative. Preserve concurrent editor,
acquisition and transition work.

## Agreed gates

- Target 1,000 films; warm Framing p95 at most five seconds on current hardware.
- Optional search derivations, indexes, retained versions and scratch: 64 GiB.
- New composition ranking remains experimental until complete coverage,
  automated checks and the user's twelve-reference comparison pass.
- No new action-timing or context activation; Match Cuts stays in Labs.

## Implemented

- Shared pinned read-only snapshots and frame retrieval; independent search and
  Lab scoring policies. Request-local PE/semantic vectors and batched metadata
  reuse, with a 4 MiB limit and timing diagnostics.
- Managed scalar lookups that preserve valid text/spatial/composition readiness
  through index-only changes. Sixteen lookups are installed in the live library;
  the semantic profile remained ready afterward.
- Source-hashed partial spatial caching, checksum validation, inference only for
  misses and identical float16 live/cache scoring. Bounded durable preparation,
  cancellation, explicit resumption, physical storage admission and idle-only
  cache/profile retirement.
- Frozen 32/64-dimensional composition profiles, film-balanced fitting,
  independent exact candidates, deterministic budgeted union and best-frame
  selection after detailed scoring. Complete-coverage fallback and explicit
  human promotion/rollback boundary.
- Isolated IVF_FLAT comparison tooling and a twelve-reference replay of the
  public Framing pipeline. No production ANN or composition ranking promotion.

## Live preparation

On September 20, preparation was enqueued for 138 published films. The existing
ingest lane still had one active and four queued film ingestions; those remain
ahead of optional feature work. Successful new ingestions enqueue their own
preparation. The single fitting job
`ec7a1371-84e2-49dc-9045-8dc0b1fd5818` waits behind film/cache preparation, fits
both challengers, then queues their compact features per film. No new daemon or
watcher was added. Every feature quantum yields after at most 32 frames.

Optional physical search usage after scalar installation: 10,275,403,967 bytes
out of 68,719,476,736. Budget pressure pauses preparation rather than deleting
source material or exceeding the configured admission limit. Actual preparation
growth, model quality and warm Framing latency are still to be measured; queued
work is not evidence that a full-library build or the 1,000-film target passed.

## Measurements and validation

The live exact retrieval probe used 491,002 frames. The first baseline file
samples twelve references from the initial frame slice; its maximum per-case
p95 was 688 ms unscoped and 522 ms scoped. The later indexed probe uses one
reference from each of twelve films: scoped p95 ranged 9–25 ms and unscoped
p95 560–720 ms. These are database-stage measurements during other activity,
with different reference sets, not a controlled end-to-end speedup claim.

Raw runs: `search-retrieval-baseline-2026-09-20.json` and
`search-retrieval-indexed-2026-09-20.json`. No model inference was required.

- Full backend suite: 3,314 passed, 11 skipped.
- Subsequent focused integration checks: 240 passed, one skipped.
- Info guide tests: eight passed.
- Real disposable IVF_FLAT test verifies that evaluation removes its scratch
  and leaves the production table version untouched.

Pending deployment gates: finish background derivations; run full-library ANN
recall/latency comparisons and end-to-end Framing replay; choose twelve useful
visual references and obtain actual human relevance/preference judgments. Keep
`retrieval.composition_profile` null until those quality, latency, coverage and
storage gates pass. Match Cuts/context/action-timing behavior remains unchanged.
