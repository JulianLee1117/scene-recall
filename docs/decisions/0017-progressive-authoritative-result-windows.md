# ADR-0017: Progressive authoritative result windows

- Status: Accepted
- Date: 2026-08-30
- Supersedes: None
- Superseded by: None

## Context

Search initially returned one fixed 48-result window. The frontend could reveal
that window in smaller visual batches, but it could not ask retrieval for more
candidates. Its separate Best per movie control then grouped only those 48
rows in the browser. That label suggested broader movie recall even though the
backend had not searched a deeper result window, and it discarded useful
same-film scenes without changing retrieval.

A stateful cursor would add cache lifetime, corpus-generation invalidation,
and request-identity concerns. It is unnecessary while the complete supported
interactive window remains bounded at 200 results and the existing ranking
pipeline can reproduce stable prefixes.

## Decision

Expose an optional result-prefix limit on every search surface. Omission keeps
the configured default window; a request may deepen that same search up to the
configured maximum. Each response contains the complete authoritative prefix,
the resolved and maximum limits, and bounded `has_more` and `next_limit`
metadata. The server probes one additional eligible result when below the cap
so `has_more` does not guess from the requested row count.

The frontend keeps one ranked stream. It initially renders a viewport-sized
portion of the default prefix, reveals already-returned rows first, and then
requests a larger prefix when the current one is exhausted. A deeper response
replaces the earlier prefix rather than being appended or independently fused.
The browser resubmits the complete query, including a transient uploaded image,
movie scope, and modular clauses. It does not group results into a separate
Best per movie mode.

Do not add cursors, infinite scrolling, or a generic filter language for this
bounded workflow. A future result filter must answer a demonstrated user need
and remain backend-owned when it changes candidate eligibility or ranking.

## Consequences

- **Show more** can search beyond the initial 48 rows without implying that every
  film must contribute a result.
- Every displayed order remains one backend-ranked prefix with the existing
  deduplication, temporal spread, and film-diversity policies applied once.
- Deepening repeats bounded retrieval and, for an upload, image encoding. It
  occurs only after an explicit user action and remains capped at 200 by the
  current configuration.
- No server-side search-session state, cursor invalidation, model, index,
  vector space, ingestion stage, or backfill is introduced.
- Movie inclusion scope remains an independent explicit control. More custom
  filters remain deferred until their behavior and evidence are concrete.
