# ADR-0019: Soft temporal result spread

- Status: Accepted
- Date: 2026-08-30
- Supersedes: None
- Superseded by: None

## Context

Ordinary unscoped searches can rank several distinct shots from one short
stretch of a film near the top. Those shots are not necessarily visual
duplicates: cuts within the same sequence can preserve different expressions,
actions, compositions, or dialogue and therefore remain useful results.
Deleting them would trade away evidence and recall, while leaving them
adjacent makes the initial discovery window feel repetitive.

The existing policies solve different problems. Hard visual deduplication
removes only candidates that satisfy its visual-similarity evidence and
thresholds. Reference-image retrieval separately uses a 90-second soft spread
around matched frame timestamps. Neither policy should be weakened, widened,
or silently layered onto a different search surface to address ordinary text
result repetition.

## Decision

Apply a deterministic, soft 30-second temporal spread to ordinary unscoped
production text and typed-recipe result streams. After normal eligibility and
hard visual-deduplication checks, keep the strongest candidate from a
same-film temporal neighborhood in relevance position and defer later
neighbors. Compare ordinary results by their representative unit time range.
Preserve every deferred candidate in its original relative relevance order so
it can backfill an underfilled window or appear when the user requests more.

Run this pass once on the complete bounded eligible ranking, before the
existing film-diversity preference and before result-window slicing. A deeper
authoritative result request therefore preserves its earlier prefix. Recipe
clauses continue to fuse before product preferences are applied, so temporal
spreading does not alter independent clause retrieval or fusion evidence.

An explicit film scope remains a request for strict relevance order and
bypasses the ordinary temporal spread. Reference- and uploaded-image-driven
streams continue to use only their established 90-second reference policy;
they do not receive the ordinary 30-second pass first. The hard visual
duplicate thresholds and their temporal corroboration rule remain unchanged.

Do not infer a new matched-frame deduplication policy from this decision.
Whether ordinary visual hits should use matched frame timestamps, shot bounds,
or additional sequence evidence is separate evidence-gated work and requires
a concrete failure, human comparison, and its own activation decision.

## Consequences

- Initial unscoped discovery results show more distinct film moments without
  deleting useful neighboring shots.
- Strong nearby alternatives remain reachable through relevance backfill and
  progressive **Show more** requests.
- Explicit movie searches remain predictable when a user wants every relevant
  moment from one selected scope.
- Reference-image behavior and evidence semantics remain unchanged rather than
  accumulating two temporal heuristics.
- The change adds no model, index, vector space, persisted derivation,
  ingestion stage, or backfill.
