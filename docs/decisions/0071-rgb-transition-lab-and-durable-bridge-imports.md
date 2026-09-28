# ADR-0071: RGB transition experiments and durable AI bridges

- Status: Accepted
- Date: 2026-09-16
- Extends: ADR-0070, ADR-0025 and ADR-0068
- Supersedes: ADR-0070's browser-only returned-video and no-provider-execution restrictions

## Context

The user requested a wider, customizable transition lab for fast music edits,
including tasteful complex flashes and current AI techniques. A local fixture
showed that the original whip blurred each source separately while leaving its
moving join sharp. Source color normalization was incomplete, framing only fit
with padding, comparisons had independent clocks, and returned AI video played
without either source join or durable history. These concrete failures prevent
useful evaluation before editor promotion.

## Decision

Keep the projectless Lab, existing editor worker, source identity checks and
immutable artifact ownership. Introduce the versioned RGB-v3 renderer: bounded
FFmpeg normalization, explicit SDR Rec.709 color policy, per-source framing,
and streaming PyAV/NumPy linear-light compositing. Expand to thirteen curated
recipes with typed shared controls. Separate flash peak/attack/decay from the
picture-change phase. Use seeded procedural textures and a combined-source whip
sampler. Preserve exact overlap endpoints and require at least three output
frames for local effects. Continue fixed 30-fps muted MP4 output.

Keep completed older outputs readable. Reject incompatible queued renderer
snapshots rather than silently changing their meaning. Record framing, color
assumptions, recipe parameters and implementation versions. Do not label spatial
blur as optical flow or source retiming, and do not add mask/depth models without
their own observed need and asset/version contract.

Add shared seam-relative playback, user-directed BPM/frame helpers, a bounded
three-duration sweep, local audio audition and browser-local experiment notes,
favorites and named settings. These aid evaluation without changing the music
editor or asserting that an effect is automatically useful.

Admit explicit local import of an externally generated bridge as a durable
`transition-bridge` job. Validate bounded multipart/video input and own the
original, hashes, frozen source pair, parent receipt and endpoint copies. Record
trim separately from optional explicit retiming. Assemble A → bridge → B with
the same source framing/color policy and expose both joins and the synthesized
interval. Imported provider/model/prompt notes remain user-supplied and unverified.
Keep imported media separate from raw films, retrieval evidence and embeddings.

Admit one explicit hosted experiment: Runway Seedance 2.5 first/last-frame
generation, with server-only credentials and durable `transition-generate` jobs.
Local quotes bind provider price version, source receipt, duration and resolution.
Submission requires explicit confirmation and one stable request UUID; exact
HTTP retries return the same job without requeueing terminal/uncertain requests.
Own native endpoints separately from provider inputs normalized directly from
raw source at the same 30-fps segment edges used by assembly. Record these frame
indices separately from native timestamps, with the same framing/color policy.
Keep source/model/request hashes, provider task identity, estimate/final cost,
original output and synthesized interval separate from user-import provenance.

Limit requests to 4–8 seconds and a reviewed estimate of at most 300 credits.
Runway has no request-side hard credit cap: reject excessive local estimates
before submission and attempt cancellation if its acceptance estimate changes
or cannot be validated. Cancellation is best effort and may not avoid charges.
Poll no faster than five seconds within a twenty-minute budget, preserve an
exclusive receipt before submission and never resubmit uncertain/interrupted
work automatically. Download promptly with bounded size/time and assemble both
source joins. Expose sanitized receipts for reconciliation without credentials,
signed output URLs or raw provider failure payloads. A credential alone makes no
generation call. The workflow is tested with simulated provider responses; live
quality and account-specific behavior still need a configured account.

Automatic transition selection and editor integration remain gated by a separate
decision supported by played quality, continuity, latency and cost.

## Consequences

- The lab can test more expressive effects on exact source windows and retain
  both local variants and imported AI auditions across visits.
- Imports and explicit generation reuse the editor queue and UUID render
  namespace; known intermediates use existing teardown/cleanup. No new service
  or local model dependency is added. No paid call occurs without explicit submit.
- The new SDR pipeline changes render semantics, so the renderer version changes;
  old completed artifacts remain immutable.
- Numerical and synthetic-media tests establish mechanics. Played clip review
  remains necessary for taste, cadence, identity continuity and editor promotion.
