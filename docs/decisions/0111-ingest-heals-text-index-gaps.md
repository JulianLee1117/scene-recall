# ADR-0111: Film ingest heals small semantic-text gaps

- Status: Accepted
- Date: 2026-10-07
- Extends: the semantic-text activation rule in the architecture contract
  ("Activation and fallback")
- Supersedes: None
- Superseded by: None

## Context

The semantic-text profile activates only when its manifest exactly covers the
current units generation; otherwise broad search falls back to the legacy
combined text vector and the focused Scene, Words and Mood clauses refuse.
Each film ingest embedded its own views and then checked the whole library.

On 2026-10-06 the Vertigo ingest ran out of GPU memory in the moments pass,
and its follow-up text step failed with the same CUDA error. The job still
completed. Every later ingest embedded its own film, saw Vertigo's 2,432
missing and 2,414 stale story/scene views, and left the profile inactive. For
27 hours search silently ran on the fallback. A typed "beer" search ranked
two pure-black frames in its top dozen with no per-view evidence, and category
searches failed. The same silent staleness happened on 2026-10-01. Each time
it was found only by noticing bad results and running `index-text` by hand.

## Decision

`backfill_text_features_during_ingest`, used by film ingest and by evidence
refresh while it holds the ingest lock, now finishes its own film and then
measures library coverage per film. If the remaining gap is at most
`INGEST_HEAL_VIEW_LIMIT` views (25,000; Vertigo was 4,846), it reconciles
those films the same way `index-text --film-id` would: it embeds only missing
or stale views, rewrites duplicates and drops orphaned films. It then checks
completeness and publishes the manifest under the existing publication lock.
The result reports the healed films, and the ingest log names them.

Larger gaps, such as a model, view-contract or query-provenance migration,
are left unchanged. They stay an explicit `index-text` run, so an ingest never
turns into a library re-embed. The explicit scoped command keeps its old
behavior: it reconciles only the film it names.

## Consequences

- One failed text derivation is repaired by the next film ingest or evidence
  refresh instead of disabling the profile indefinitely.
- An ingest may write another film's derived text rows. They are the same
  rows `index-text` would write; raw films, units and other derivations are
  untouched, and partial generations are still never mixed.
- Each ingest still scans the library's unit text and feature metadata once,
  as the completeness check already did. Embedding cost is bounded by the
  limit.
- The focused-clause error now tells the user what still works instead of
  naming internal profiles.
