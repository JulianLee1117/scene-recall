# ADR-0092: Bounded framing representation pilot and operator hold

- Status: Accepted for isolated evaluation; no serving promotion
- Date: 2026-09-21
- Extends: ADR-0082 and ADR-0059

## Context

The full-grid framing backlog has substantial remaining compute and storage
cost, while final-layer PE features and the current global/spatial blend have
not been shown to be the best affordable framing representation. The user
approved pausing bulk preparation and comparing alternative methods alongside
the independent Jev search evaluation. Ordinary search and baseline Framing
remain usable with partial acceleration coverage.

## Decision

Drain the active 32-frame batch gracefully. Hold currently runnable optional
preparation and composition-fitting jobs using the existing `waiting_worker`
state with an explicit operator marker. Refuse the pause transaction while
those kinds are running. Preserve cursors, results and completed features.
Only explicit `search-features resume` clears that marker; duplicate enqueue
and process restart must not resume held jobs. Other jobs and future-ingest
preparation retain their existing admission policy. No queue schema or new
scheduler is introduced.

Run the [framing representation protocol](../experiments/framing-representation-pilot.md)
on eight films, 512 independently sampled candidates and twelve provisional
references. Four references are tuning cases and eight are held out. Selection
must not depend on any model's rankings or cache coverage. The initial fixture
may be deterministic and unjudged; relevance and user preference stay unknown.

Compare the installed PE final grid, block index 17 of the same checkpoint,
and an accessible small dense spatial model. DINOv3 access is gated; the
public PE-Spatial-S16-512 checkpoint is an explicit fallback, not an equivalent
model. Further EUPE/model sweeps, preprocessing ablations and compression are
deferred until a concrete pilot finding justifies them. Do not migrate product
dependencies or accept third-party access terms on the user's behalf.

Each arm exactly scores every eligible cross-film candidate. Keep source hashes,
checkpoint/code revisions, transforms, token handling, precision and output
checksums frozen. Spatial-only and fixed-PE-global blend results are separate;
never compare vectors across model spaces. Native resolution is recorded and
limits model-only causal claims. Shared PE forward time is shared work, not
separate per-arm latency.

Save at most 32 frames per batch with explicit resume. Use the existing ingest
lock, stop if busy or explicitly requested, cap pilot computation at 90 minutes,
derived artifacts at 2 GiB and new checkpoint downloads at 1 GiB. Keep normal
host sleep behavior. No production table, index or activation setting is written.
Keep receipts and rankings; any later cleanup targets only identified pilot
artifacts, never raw evidence, shared model caches or live database internals.

Jev uses a separate frozen text-query comparison and single-strategy timing
diagnostic under ADR-0091. It is not a framing encoder. Do not conflate its
routing gains with changes to image evidence.

## Consequences

The held full-library batch resumes only after an explicit cost/benefit decision.
Feature extraction success, top-ten disagreement or a faster small model alone
cannot establish relevance. Human image assessment and independent candidate
inspection are required before selecting a quality winner. ADR-0082's complete
coverage, twelve-reference human review, latency and storage gates remain intact.
