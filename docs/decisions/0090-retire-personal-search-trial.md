# ADR-0090: Retire the interactive personal search trial

- Status: Accepted
- Date: 2026-09-21
- Supersedes: [ADR-0089](0089-personal-search-assistance-trial.md)

## Context and decision

The owner requested removal: staged goals, suggestion feedback and useful/not-yet
judgments felt unintuitive and did not answer whether better footage was absent
from the retrieved results. The trial changed optional category suggestions, not
the retrieval engine. Its interaction judgments could not establish recall.

Disable live collection and remove the trial feature, API service/router,
instrumentation, dedicated tests and activation helper. Remove the trial-only
category-opening and playback callbacks rather than leaving speculative hooks.
Preserve ordinary search, manual categories, inline film scopes, source playback,
ingestion and unrelated work. Retain the small local ledger as historical evidence,
especially its unpriced provider attempt; no completed quality comparison is claimed.
Earlier ADR-0086/0088 offline tools and paid receipts remain independent.

## Next decision gate

Reprioritize around retrieval failures before adding another interaction study.
A future manual comparison can present playable results for the same query and
confirmed scope, with neutral A/B labels and better/same/neither judgments.
It must name what changes: existing Jev code judges stored text and reorders a
fixed pool; it is not an independent video retriever and cannot recover candidates
outside that pool. Measure candidate coverage separately using independent
retrieval channels, deeper inspected pools and known source moments. A pooled
union still cannot prove exhaustive recall.

Use that diagnosis to choose between missing source evidence, candidate generation
and ranking work. Preserve explicit scope and exclusions. This ADR does not
authorize a new paid run, serving policy, reranker, metadata migration or review
platform. The owner may choose the next narrow comparison after reviewing the
proposal; no replacement experiment is implemented as part of this cleanup.
