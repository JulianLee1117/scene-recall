# ADR-0021: Unified repeat rank for open discovery

- Status: Accepted
- Date: 2026-08-31
- Supersedes: ADR-0020 unscoped non-image-gated typed-recipe diversity clause only
- Superseded by: None

## Context

A dragged Scene source exposed an ordering failure that the ordinary broad
search policy did not reach. The source is honestly resolved to its stored
caption and searched against caption evidence, so nearby shots from the same
film can be the strongest semantic matches. For a measured Tree of Life beach
source, four of the first five results came from that film even though the
bounded result set contained 168 eligible scenes from 23 films. The existing
page-wise preference explicitly allowed four results from one film in the first
12, while the 30-second temporal spread could not treat a roughly three-minute
montage as one moment.

This is not evidence for excluding the source film, enforcing one result per
film, widening every temporal window, or deleting visually distinct shots. It
is the same diminishing-returns ordering problem already measured for ordinary
broad search. A source-film-specific rule would also assume that every dragged
scene means "show another movie," which is not part of the facet contract.

## Decision

After clause fusion, source removal, junk filtering, visual deduplication, and
the ordinary 30-second temporal spread, apply ADR-0020's deterministic bounded
film-repeat rerank to every unscoped recipe that has no mandatory visual
candidate gate. This includes text and indexed Scene, Words, Mood, and Look
clauses and their non-image-gated combinations. The priority remains:

`original_rank + strength * repeats / (repeats + 1)`.

Reuse the configured strength of 32. This is a final-ordering policy only:
typed clauses keep their established candidate depths, evidence adapters, and
fusion behavior and do not inherit broad search's three-times channel depth or
cross-film candidate reserve. The rerank remains a full permutation with exact
deeper prefixes and never deletes a candidate.

Explicit movie scopes on ordinary non-image-gated recipes continue to preserve
strict relevance order. Recipes gated by an uploaded Look or Framing image, or
by an indexed Framing source, retain their established 90-second reference
spread. When unscoped they also retain the page-wise, relevance-backfilled film
preference while the visual reserve is still being human-graded; an explicit
movie scope disables that film balancing but not the reference spread. The
referenced unit is removed as before, but its film receives no special
exclusion or additional penalty.

Keep the hard visual-duplicate thresholds and the ordinary 30-second temporal
window unchanged. Same-moment grouping may later become an expandable result
presentation when reliable cluster evidence and the unified result-control
design justify it; it is not a substitute for this default ordering policy.

## Consequences

- In the measured Tree of Life case, the source film moves from four of the
  first five results to one of the first five and two of the first 12, while
  first-12 film representation rises from 8 to 11.
- Strong repeated results remain reachable, including multiple results from
  one film when their rank advantage survives the finite cost.
- Unscoped non-image-gated modular search now shares one passive discovery
  policy with ordinary broad search instead of exposing another interface
  choice or maintaining a page quota just for recipes.
- Mandatory visual recipes retain their separately measured candidate and
  ranking contract rather than promoting uncalibrated deep visual reserve
  rows under this decision.
- No model, index, vector space, stored evidence, ingestion stage, or backfill
  is added.
