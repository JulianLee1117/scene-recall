# ADR-0011: Expose resolved recipe source inputs

- Status: Accepted
- Date: 2026-08-29
- Supersedes: None
- Superseded by: None

## Context

A scene dragged into a modular facet contributes different evidence depending
on the facet. Scene, Words, and Mood derive exact stored text, while Look and
Framing compare learned frame representations. Showing only the source image
made those inputs opaque; inventing an English description for visual vectors
would make the interface more legible but less truthful.

Result-match evidence cannot answer this question because it describes why a
candidate matched, not what the source scene contributed to the request.

## Decision

Resolve every source clause once per recipe execution. Derive an additive
`source_evidence` response from that same per-request snapshot and return it
beside the ranked results.

For caption, dialogue/OCR, and mood adapters, expose the exact effective text
used by retrieval and its stored view boundaries. For Look and Framing, expose
the exact frame identity and visual adapter mode without generating a caption.
Keep this source-input evidence separate from each result's `matches` evidence.

The normal interface reveals the explanation through a compact source control;
debug mode may additionally show adapter and view details. This adds no model,
query rewrite, hosted call, or derived index.

## Consequences

- Dragged-scene behavior is inspectable without making the default search UI
  carry retrieval jargon.
- The explanation cannot drift from ranking during a concurrent reingest.
- Dragged visual sources remain honestly non-linguistic; typed Look queries
  still use the user's own text.
- The recipe response gains compatible metadata, but ranking and fallback
  behavior remain unchanged.
