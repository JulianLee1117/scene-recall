# ADR-0018: Category-bound uploaded-image facets

- Status: Accepted
- Date: 2026-08-30
- Supersedes: [ADR-0012](0012-query-bound-uploaded-image-recipes.md)
- Superseded by: None

## Context

ADR-0012 treated an uploaded still as a separate broad-visual recipe signal
that always combined appearance and spatial layout. In practice, that made the
upload feel disconnected from the five modular categories and hid which visual
criterion controlled the search. Assigning the upload to every category would
be equally misleading: the current image tower does not infer narrative
events, spoken or visible words, or mood as versioned semantic evidence.

The upload must remain one bounded request-level input. Retrieval semantics
stay backend-owned, and the interface must not create duplicate image clauses
or silently count the same still as independent evidence.

## Decision

Bind an uploaded still to exactly one supported recipe facet: `look` or
`composition` (Framing). The main file picker and a drop on the general search
workspace assign it to Look by default. Dropping a file directly on Look or
Framing assigns it to that tile, and the same uploaded input may be moved
between those two tiles without copying it.

Look embeds the upload with the existing PE image tower and performs bounded
global visual retrieval. Framing uses the same global PE candidate gate and
then the existing 6x6 spatial-layout reranker. The selected image clause is
mandatory: it supplies the candidate set and may combine with at most two
other recipe clues under the existing three-clause bound. Optional clauses may
rerank those candidates but may not introduce candidates outside the image
gate.

Do not interpret uploaded images for Scene, Words, or Mood yet. Activating any
of those destinations requires a separately versioned query-time adapter and
an explicit decision about model provenance, privacy, latency, and operating
cost. Until then, the interface must not imply that those facets accept or
understand uploaded images.

Keep the standalone `/search/image` surface compatible. It remains the direct
broad visual reference workflow; category binding applies to multipart modular
recipes and does not require a migration of stored evidence, embeddings, or
indexes. Reserve the existing bounded image-work slot before a multipart upload
is decoded, and hold it through serialized model execution, so concurrency also
bounds decoded-image memory.

## Consequences

- Uploaded images participate visibly in the same modular category model as
  text and indexed-scene clues.
- Look and Framing have distinct, inspectable retrieval semantics without
  counting one image as two fusion votes.
- A recipe still accepts only one transient uploaded image and at most three
  total clauses.
- Moving or replacing the image changes one clause and one mandatory candidate
  gate; it does not retain a hidden broad-visual clause.
- Scene, Words, and Mood image understanding remains deferred until its
  evidence, privacy, and cost boundaries are accepted.
- Existing raw films, derived profiles, vector spaces, ingestion stages, and
  backfill requirements are unchanged.
