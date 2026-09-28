# ADR-0060: Music-led timing before bounded footage planning

- Status: Accepted
- Date: 2026-09-14
- Extends: ADR-0032, ADR-0041, ADR-0045 and ADR-0058
- Supersedes: ADR-0043's combined count/direction planning and density envelopes; ADR-0045/0050's quota-driven text sections and rejection of single holds over 90 seconds; the listening/pacing coupling under ADR-0032/0042 for pace-only regeneration
- Partially superseded by: [ADR-0061](0061-targeted-footage-inspection-and-frozen-comparisons.md), which admits bounded optional post-selection inspection while preserving this timing stage
- Partially superseded by: [ADR-0064](0064-scoped-editor-direction-and-two-tab-workspace.md), which restricts whole-passage timing to explicit regeneration, including first Generate, and preserves starter cuts during Fill gaps

## Observed failure

The Rapid Nocturne retry saved 110 positions across 90 seconds: 98 shorter than
one second and none longer than 1.375 seconds. Per-processing-section count
requirements spent nearly the same cut density throughout the passage before
the selector saw footage. Local source fitting could move nearby boundaries but
could not merge intentions into a meaningful hold. Detailed musical events were
empty despite broader listening interpretation and available beat/amplitude data.

The user chose a separate compact timing call after discussing whether to keep
timing inside visual planning. This is an accepted additional hosted stage,
justified by the observed pacing failure. It does not activate action inspection,
automatic Match Cuts or a larger editor architecture.

## Decision

Keep one Generate edit action and one frozen job with one final revision. For
eligible untouched first edits and explicit regeneration, run a compact text
timing request over the complete selected passage before planning scene searches.
Give it current scoped listening evidence, measured beats/downbeats and relative
intensity, supplied musical context and the pace preference. Keep listening's
existing bounded schema; improve its instructions for useful timed landmarks
without treating interpretation as a detector or requiring richer evidence fields.

The timing response contains ordered passage-relative ending frames and at most
32 concise span notes with musical reasons and evidence references. It does not
return footage choices or a full search description for every shot. Validate
1–300 positions, integer strictly increasing boundaries, at least one output
frame of source time per position, complete coverage and the original exact
passage endpoint. Invalid responses fail explicitly; do not sort, snap, clamp,
retry or manufacture events to make them valid.

Patient, Balanced, Energetic and Rapid are soft editorial preferences. Remove
minimum/maximum average-duration bands, processing-section shot quotas and the
Rapid-specific 288-position limit. A preference can support brief clusters and
long holds within the same passage. Do not reward variation by itself or equate
loudness with emotion. Unknown events remain unknown; broader interpretation
and measured cues can still support a qualified timing proposal. The 300-position
project limit is a technical bound, not a desired density.

After timing is chosen, group consecutive planned shots for visual direction
planning and source selection: at most 32 shots per request, preferably spanning
no more than 90 seconds. Never create a cut to satisfy a processing limit. A
longer hold occupies a batch by itself, including when filling fixed positions.
Preserve global source-track timing, shared song context, one visual arc,
neighbors and used-source evidence across batches.

Hosted listening remains separately limited to sections no longer than 90 seconds.
Retain the complete validated per-section interpretation and provenance. Reuse
current same-track/passage evidence across a pace-only regeneration instead of
discarding it with old directions or selection diagnostics. Each text batch uses
a deterministic scoped view of this evidence. Missing or invalid listening still
runs through the authorized Generate flow; explicit listening retains its own
request and cache controls. The timing artifact has its own input identity so
changed pacing produces a new timing decision without requiring another listen.

Saved browser JSON may normalize integer-valued floats, changing an old aggregate
interpretation's byte-sensitive provenance digest. Before re-listening, private
generation may restore the original number types from the exact referenced
interpretation caches. Require 64-character hexadecimal IDs, valid original
provenance hashes, unchanged track/passage and full evidence equivalence allowing
only numeric representation differences; booleans are not numbers. Changed or
missing originals retain the normal invalid-analysis fallback. Do not rewrite
saved projects during this check or change the global digest/vector profiles.

Keep ADR-0058's source-aware finite cut offers and deterministic feasibility
solver after visual planning. The selector retains source choice and preferred
cuts; source starts resolve only after legal durations are known. Batch endpoints
and intention order/count remain fixed during this local fit. No source
substitution, invented cuts, forced gaps or automatic repair call is introduced.
Fixed/manual/per-shot work retains exact timing, written instructions and lock
guards. Failure, cancellation and stale revisions preserve the saved edit.

Persist optional `direction_plan.timing_plan` under the
`music-led-passage-timing-v1` contract and expose it in the generation result:
contract/artifact identity, track, passage, fps, proposed ending frames,
bounded notes and cache reuse. Retain nominal/final duration diagnostics and
final ending frames alongside existing source-fitting adjustments. Progress
distinguishes listening, whole-passage timing, visual planning and scene
selection. Existing Details shows recorded work; do not introduce mandatory
setup panels, percentages or explanations invented after selection.

## Consequences and validation boundary

The added compact call costs latency and tokens but gives musical timing the
whole passage before expensive footage work. Request limits govern computation
without deciding the rhythm of the edit. A long planned hold may have no suitable
source in the library; existing strict validation and explained abstention remain
honest outcomes. Source fitting still cannot infer whether an action is complete.

Focused verification must cover contrasting burst/hold plans, soft presets,
nonzero/subframe endpoints, 300-position capacity, batching without extra cuts,
holds longer than 90 seconds, current listening reuse, missing event evidence,
fixed/manual/locked preservation, source feasibility, receipts, cancellation and
atomic failure. These are acceptance requirements, not a claim that tests have
passed or that generated pacing is artistically successful. Played Nocturne and
Rumination comparisons remain necessary; track scene fit, repetition, gaps,
runtime and cost separately from subjective editing preference.

Automatic action inspection, action-aware assembly, optional Match integration,
new editor personalities, retrieval changes, RL and blanket ingestion expansion
remain outside this change. Later work must satisfy its own evidence gates.
