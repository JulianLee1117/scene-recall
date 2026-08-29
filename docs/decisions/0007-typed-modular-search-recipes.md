# ADR-0007: Typed modular search recipes

- Status: Accepted
- Date: 2026-08-28
- Supersedes: None
- Superseded by: None

## Context

Users need to combine a remembered scene with explicit refinements such as
spoken words, visual appearance, composition, or mood. The current system
already stores independently searchable text views, global frame vectors, and
spatial frame evidence, but its public workflows expose only one broad text
query or one reference image with optional text.

Automatically decomposing every query with an LLM would add latency and an
opaque routing dependency before there is evidence that automatic routing is
needed. Adding new facet indexes would likewise create model, activation, and
backfill obligations even though a useful prototype can be built from current
evidence.

## Decision

Add a typed recipe workflow containing one to three clauses with unique facet
identities. A clause is either user text or a stable indexed unit/frame
reference. The supported facets are broad `all`, caption-backed `scene`,
dialogue/OCR-backed `words`, global PE `look`, spatial `composition`, and the
existing annotation-backed `mood` view.

Each facet remains an adapter over an existing independently replaceable
retrieval boundary. View-specific semantic clauses require the complete active
semantic-text profile and never fall back to the legacy combined unit vector.
This work adds no query router, LLM dependency, model, index, or backfill.

A lone broad `all` clause preserves normal text search exactly and only adds
its recipe evidence record. All other facet adapters return bounded raw
rankings so no clause applies product filtering or diversity before fusion or
the final single-clause preference pass.

Fuse clause rankings with equal reciprocal-rank fusion. When composition is
present, its candidates are mandatory; other clauses can reorder but cannot
expand that set. Exclude every source unit, retain ADR-0005 source-film
behavior for composition, and apply its effective result-film scope to every
bounded clause retrieval. Then apply the established junk, visual duplicate,
temporal-spread, and film-diversity preferences once to the final ordering.
Return per-clause rank and text/frame evidence with each product result.
Resolve every source clause and validate its required text, vector, frame, or
file evidence before an empty composition target scope may return no results.

Do not expose per-clause weights, custom filter logic, or a plot facet in this
prototype. Those boundaries require observed failures and the existing
decision gates.

## Consequences

- The interface can offer explicit, editable search intent without hiding
  behavior behind automatic decomposition.
- Dragged scenes remain reproducible because the server resolves stable unit
  and frame identities instead of trusting client file paths or vectors.
- Clause agreement is inspectable in product-level evidence, while each
  underlying retrieval system can still evolve independently.
- Composition recall remains bounded by its visual shortlist, and source-film
  discovery behavior is unchanged.
- `scene` and `mood` inherit the limits of current hosted annotations, and
  `words` remains shot-level; no dedicated plot or temporal semantics are
  implied.
