# ADR-0086: Evaluate hosted search decisions against frozen candidates

- Status: Superseded by [ADR-0094](0094-search-v2.md) (retired 2026-09-28)
- Date: 2026-09-20

## Context

Mixed descriptions and remembered moments can perform poorly even when a useful
candidate exists. The user approved a bounded comparison of ordinary search,
intent assistance, candidate evidence judgment and both together. Fast typed
decisions are feasible, but neither their low cost nor correct output types
establish retrieval quality. Inferred categories can also hide useful candidates
if translated into exclusive retrieval routes.

## Decision

Add an isolated experiment module consuming frozen ordinary API results. It
does not import a retrieval encoder, prepare indexes, ingest media, modify an
editor project or change serving configuration. The input preserves the original
query, explicit film IDs, returned source evidence, channel ranks, capture
provenance and matching before/after observed table versions.

Compare four variants over the same candidate pool. The baseline keeps the
captured order. Intent assistance uses independent visible, semantic and literal
signals with existing img/txt/lex ranks. Evidence judgment asks whether bounded
caption/winning-text excerpts support, partly support, contradict or leave the
request unknown. Both combines those same two responses. Only the first 48 rows
may be reordered; the complete candidate set and remaining tail are preserved.

Freeze the initial conservative scoring policy in code and receipts. Each row
has baseline anchor `1 / (60 + baseline_position)`. The intent bonus is half the
sum of channel weight times intent signal divided by `60 + channel_rank`, with
weights 0.4/0.4/0.2. The evidence bonus is half the anchor times a support value:
supported 1, partial 1/3, contradicted and unknown 0. Missing excerpts earn no
bonus. These numbers define a testable policy; they are not calibrated relevance
probabilities or an optimal ranking claim.

Use a narrow OpenRouter Decisions adapter with explicit model/prompt/request
identities. Bound payloads, responses, paid attempts and cost admission. Persist
attempt accounting before transport, record actual usage and stop further paid
calls if cost is unknown or exceeds admission. No automatic provider retries.
Exact completed receipts can be replayed into a new output directory; a recorded
execution cannot be silently rerun. Transport changes do not change the frozen
query/evidence or authorize new ranking policy under the same version.

Generate a blind review with randomized treatment aliases and a separate key.
Candidate grades and preferences stay human-owned and initially empty. Preserve
frame-inspected, source-playback and provisional references as distinct evidence
statuses. Target-window ranks are diagnostics; they cannot stand in for broad
relevance, action verification or subjective metaphor judgments.

The standalone review generator may explicitly resolve existing playback URLs
through the loopback API, selecting its pinned prepared-media representation.
It creates no media and changes no playback policy. Unresolved sources remain
explicitly unavailable; a resolved URL is not a playback or audio-quality grade.

## Limits and promotion

This first experiment measures ordering within a captured result pool. Candidate
generation is unchanged, so it cannot establish exhaustive recall or a benefit
from automatic category routing. Its three channel signals do not create new
user-facing categories, split Words into dialogue/OCR, or provide plot context.
It is text-only and cannot verify facts absent from the supplied evidence.

Model-call time, replay time and the earlier API capture time are recorded
separately. Offline timing is not production end-to-end latency. Model scores,
confidence and valid output are not human acceptance. Activation requires a
separate reviewed relevance/interaction comparison and an explicit subsequent
architecture decision. Normal search remains independent of the hosted service.

## Sources

- [OpenRouter Decisions API](https://openrouter.ai/docs/api/api-reference/alphadecisions/submit-a-decisions-questions-and-answers-request)
- [TypeSafe model limits](https://docs.typesafe.ai/models)
- [TypeSafe known failure modes](https://docs.typesafe.ai/model-jaggedness/jev-1.13)
- [Experiment protocol](../experiments/search-intent-comparison.md)
