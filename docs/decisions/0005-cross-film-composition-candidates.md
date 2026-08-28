# ADR-0005: Cross-film composition candidates

- Status: Accepted
- Date: 2026-08-28
- Supersedes: None
- Superseded by: None

## Context

Result-card composition matching reused a frame from an indexed film but only
removed that exact unit after retrieval. A film's photography and repeated
visual motifs can occupy most of the bounded ANN shortlist, leaving few or no
candidates from other films. Filtering the source after retrieval cannot
recover the candidate slots it already consumed.

Users invoke this action primarily to discover comparable compositions, while
uploaded reference images have no source film to exclude. Explicit movie scope
must remain authoritative, including a deliberate scope containing only the
source film.

## Decision

Result-card composition matching excludes its source film before both image
and optional text candidate generation by default. An unscoped request expands
the published library to an inclusion scope without the source, preserving the
existing diversity preference across the remaining films.

The caller may opt into same-film results. An explicit single-film scope takes
precedence over the cross-film default so the request remains usable rather
than becoming an ambiguous empty scope. The exact source unit remains excluded
when that film is intentionally searched.

This extends ADR-0003 without changing its evidence semantics: the reference
shortlist remains mandatory, and text can rerank but not introduce unrelated
candidates.

## Consequences

- Other films receive candidate slots before spatial reranking, so the result
  action behaves as cross-film discovery instead of a source-style echo.
- Uploaded-reference behavior and the mandatory reference-shortlist contract
  are unchanged.
- Expanding an unscoped request into positive film predicates is appropriate
  for the current small local library; a native negative predicate may be
  preferable if library scale makes that expression costly.
- A library containing no other published film correctly returns no cross-film
  candidates unless the user deliberately scopes or opts back into the source.
