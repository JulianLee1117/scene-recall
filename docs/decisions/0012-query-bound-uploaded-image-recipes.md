# ADR-0012: Query-bound uploaded-image recipe signal

- Status: Superseded
- Date: 2026-08-29
- Supersedes: None
- Superseded by: [ADR-0018](0018-category-bound-uploaded-image-facets.md)

## Context

Uploaded stills were available only through a standalone reference workflow,
which disconnected them from modular refinements. Assigning the same upload to
Look or Framing made the user choose an implementation distinction even though
a remembered frame normally carries both overall appearance and spatial
layout. Repeating upload controls inside those categories also obscured the
single main search action.

Fusing a separate image request in the browser would make ranks incomparable
and move retrieval semantics outside the backend. Translating an arbitrary
image into Scene, Words, or Mood text would also introduce new hosted or local
derivations without evidence that those adapters are needed.

## Decision

Add a multipart recipe endpoint carrying exactly one bounded uploaded still as
a broad-visual clause. The image may be the complete recipe or may combine with
at most two text or indexed-scene refinements; the total recipe bound remains
one to three clauses. It is request-level visual evidence, not a Look or
Framing category assignment. Keep the existing JSON recipe and standalone
image endpoints compatible.

Make the frontend's main bar its sole upload affordance and accept both its
file picker and direct file drops. Show one compact query-local reference and
search it automatically. Category boxes continue to accept typed text or
indexed library scenes, but they do not repeat the upload affordance or require
the user to move the still between visual interpretations. Replacing or
removing the compact reference replaces or removes the image clause. Do not
maintain a second hidden frontend reference state that replaces the facet rail.

The uploaded image is decoded and held only for the request. The broad-visual
adapter embeds it once through the existing PE image tower, retrieves bounded
global-appearance candidates, and applies the existing 6x6 spatial-layout
reranker. These are correlated stages of one visual ranking, not two
reciprocal-rank-fusion votes. That ranking is the mandatory candidate gate;
optional text or indexed-scene clauses can rerank its units but cannot add a
visually unrelated unit. The adapter has no source film to exclude and uses
the established image-work slot and serialization boundary. This adds no
model, durable image, vector space, index, ingestion stage, or backfill.

Return one explicit uploaded-image source-evidence record naming the combined
broad-visual adapter and its global-candidate and spatial-reranking stages.
Never fabricate an English description for the image.

## Consequences

- External visual references compose with the same backend-owned recipe
  ranking as text and indexed-scene clues.
- The main image affordance uses appearance and layout together without making
  the user assign a still to an arbitrary category.
- Appearance and layout weighting may evolve within one versioned adapter
  without double-counting the same source as independent evidence.
- Category boxes remain modular text and indexed-scene refinements rather than
  redundant image destinations.
- Request size and GPU concurrency retain the existing image-search bounds.
- A recipe currently accepts one uploaded image; multiple external references
  require a later demonstrated workflow and multipart contract extension.
- Arbitrary-image Scene, Words, and Mood adapters remain deferred because they
  would require separately versioned caption, OCR, or mood derivations.
