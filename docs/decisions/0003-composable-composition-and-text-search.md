# ADR-0003: Composable composition and text search

- Status: Accepted
- Date: 2026-08-26
- Supersedes: None
- Superseded by: None

## Context

Reference-image and text search were separate interface modes: choosing one
cleared the other. A useful visual-reference query often needs both clauses,
such as "this composition, but at night with two people."

A premature universal multimodal vector would couple the product to one model
and obscure whether composition or text supplied the evidence.

## Decision

Keep reference composition retrieval and normal hybrid text retrieval as
independent, replaceable clauses.

The reference image produces the mandatory candidate shortlist. Text search
cannot introduce unrelated text-only candidates; weighted reciprocal-rank
fusion reranks only the reference shortlist. Preserve the exact matched frame
from reference retrieval, attach matched text evidence when available, and
namespace clause-level debug evidence.

For combined queries, defer visual near-duplicate suppression until text has an
opportunity to distinguish visually similar repeated moments. Image-only
search retains its established deduplication behavior.

## Consequences

- Users can refine an image with natural-language constraints without learning
  backend channel concepts.
- Each clause can evolve or fall back independently.
- The visual shortlist still bounds recall; a text-perfect but visually
  unrelated result cannot enter.
- Late fusion is easy to inspect but does not provide the joint reasoning of a
  future cross-attention multimodal reranker.
