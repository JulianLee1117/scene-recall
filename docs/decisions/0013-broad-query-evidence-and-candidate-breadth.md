# ADR-0013: Broad-query evidence and candidate breadth

- Status: Accepted
- Date: 2026-08-29
- Supersedes: None
- Superseded by: None

## Context

The main bar serves both conceptual discovery and literal recall. A broad
single-word query such as `beautiful` exposed an ordering failure: a result
with the word in incidental dialogue could receive an independent full-text
vote and outrank a visually stronger result. A word list or query LLM would
make that boundary opaque and brittle, while removing lexical retrieval from
all searches would damage literal and compound queries.

Unscoped results also concentrated in a few films. The existing soft diversity
pass could reorder only candidates already present in each channel's bounded
pool, so a relevant scene below that pool could never become eligible. Adding
an Explore mode would expose a retrieval implementation detail as another user
choice instead of improving the default behavior.

## Decision

Use query shape and the explicit search surface to select lexical evidence. In
normal broad search, omit full-text as an independent reciprocal-rank-fusion
vote when the query has exactly one unquoted word token and at least one
visual or semantic channel is active. A completely quoted query, a compound
query, and a lexical-only evaluation retain full-text retrieval. Focused Words
search remains its existing dialogue/OCR semantic adapter.

For normal unscoped broad search, retrieve three times the configured
candidate depth independently from each enabled channel before rank fusion.
After fusion, retain the configured candidate prefix and a bounded cross-film
reserve: the best remaining candidate from each of at most one page's worth of
films not represented in that prefix. Keep the configured depth for explicit
film scopes, internal multi-clause rankings, and Framing. Apply the existing
page-wise film preference only after this union is deduplicated and filtered.
It continues to preserve the configured number of strong same-film results,
relevance-backfill every slot, and relax its cumulative per-film target on
later pages; it never guarantees representation for every film.

This changes query-time policy only. It adds no model, router, index, vector
space, persisted evidence, or backfill.

## Consequences

- An incidental word occurrence cannot independently boost a broad one-word
  concept, while users can request literal evidence with quotes.
- The rule scales by query shape rather than an adjective or concept list and
  adds no model latency or opaque classification.
- Cross-film candidates can enter the fused set from below the former
  per-channel boundary without adding a visible discovery mode.
- With the default candidate depth, unscoped broad search may materialize up to
  600 candidates per enabled channel, but visual deduplication receives only
  the configured 200-candidate fused prefix plus at most 12 cross-film reserve
  rows. Explicit scopes and expensive Framing reranking do not pay that
  expansion.
- Passive diversity still cannot prove relevance for a missing film. Quality
  and latency remain subject to the repository's normal human comparison gates.
