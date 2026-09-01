# ADR-0020: Bounded discovery rank and visual candidate reserve

- Status: Accepted
- Date: 2026-08-31
- Supersedes: ADR-0013 ordinary unscoped result-diversity clause only
- Superseded by: ADR-0021 (unscoped non-image-gated typed-recipe diversity clause only)

## Context

Two inspected real-library failures had different causes. `nature` retained
eligible scenes from 26 of 27 films, but showed only 4 films in its first 12
and 10 in its first 48; this is an ordering failure. An exact Titanic still
reached only 7 of 27 films at the uploaded-image candidate gate; this is a
candidate-breadth failure. A one-result-per-film cap would hide valid multiple
matches and make deeper prefixes misleading. A separate forced-new-film policy
would similarly promote ungraded weak visual hits.

## Relationship to earlier decisions

This replaces only ADR-0013's final page-wise diversity policy for ordinary
unscoped broad search. ADR-0013's query-shape lexical rule, three-times channel
candidate depth, and bounded fused-text reserve remain in force. It amends
ADR-0009 only at unscoped uploaded-reference candidate intake: the normal
96-frame spatial base may add up to 12 cross-film candidates from the
post-base pool, including eligible selectively hydrated reserve rows.
ADR-0009's 65% global / 35% spatial scorer, complete-cache requirement, and
whole-query live fallback remain unchanged.

## Decision

For ordinary unscoped broad text search only, use a deterministic bounded
diminishing-return rerank after normal filtering and the existing ordinary
temporal spread. A film's next candidate receives priority:

`original_rank + strength * repeats / (repeats + 1)`.

The configured strength is 32. It is a bounded measured default/prototype, not
a claim of universal optimality. The rerank is a full permutation with stable
12/48/96/200 prefixes; it never excludes a candidate. Explicit movie scopes
remain strict relevance order. Image-gated and other constrained recipes retain
the established page-wise, relevance-backfilled diversity preference rather
than receiving this broad-search rerank or a forced unseen-film promotion.

For unscoped uploaded Look and Framing references, query a fixed 7,200-row
global frame metadata projection. Collapse to one strongest frame per unit,
then hydrate only the ordinary 200-unit base and at most one reserve unit for
each of 12 films not represented in that base. The reserve is not used for an
explicit movie scope, which preserves the prior three-frames-per-candidate
depth. Preserve the original global rank through Look hydration and recipe
fusion. Framing scores up to 12 cross-film additions drawn from rows after the
first 96 through either the complete cache or its all-live fallback with the
same scorer; those additions can include metadata-reserve rows, while any
remaining candidates stay semantic backfill. It does not mix cache and live
paths. The existing page-wise preference may still surface a deeply ranked
reserve row ahead of a same-film backfill, because this prototype has no
calibrated visual relevance floor.

Do not activate a separate visual diversity policy. Human-grade roughly 10-15
image references before changing the 7,200 depth, reserve size, or strength,
or before treating the visual reserve as a generally successful recall change.

## Consequences

- Ordinary discovery gains soft cross-film competition without deleting strong
  repeated scenes or adding a visible Explore mode.
- Uploaded visual search can expose bounded missing-film evidence while unit
  hydration remains limited to at most 212 rows before later filtering.
- Deep reserve rows may displace same-film backfill, so their global rank,
  distance, and human relevance must be judged before claiming quality.
- No new model, index, vector space, persistent evidence, ingestion stage, or
  backfill is introduced.
