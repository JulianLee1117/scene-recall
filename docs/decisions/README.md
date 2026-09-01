# Architecture decision log

This directory records why material Scene Recall architecture choices were
accepted. It is historical rationale, not a second specification.

- [`README.md`](../../README.md) documents runnable behavior and commands.
- [`docs/search-architecture.md`](../search-architecture.md) is the current
  architecture contract.
- ADRs explain decisions but never override that contract.

## When to add an ADR

Add one only for a cross-cutting, expensive-to-reverse choice involving:

- durable evidence, model lineage, storage, migration, or backfill boundaries;
- retrieval activation, fallback, fusion, or evidence semantics;
- privacy, deployment, hosted-provider, or material cost boundaries;
- activation of a system currently marked deferred.

Do not add ADRs for normal bug fixes, refactors, tests, styling, dependencies,
or ordinary interface changes. Git records implementation details. Do not
commit speculative ADRs; add one when the choice is accepted.

## Maintenance

1. Use the next four-digit number and a short kebab-case filename.
2. Use `Accepted` or `Superseded` as the status.
3. Update the architecture contract in the same work. Update the operational
   README only if commands or runnable behavior change.
4. Do not rewrite an accepted decision. To reverse it, add a new ADR, mark the
   old record `Superseded`, and link both records.
5. Add every ADR to the index below.

## Template

```markdown
# ADR-NNNN: Short decision title

- Status: Accepted
- Date: YYYY-MM-DD
- Supersedes: None
- Superseded by: None

## Context

What forced the choice and which constraints mattered.

## Decision

The accepted boundary or behavior.

## Consequences

- What becomes easier or safer.
- What becomes more expensive or remains limited.
```

## Index

| ADR | Status | Decision |
| --- | --- | --- |
| [0001](0001-durable-evidence-replaceable-derivations.md) | Accepted | Preserve source evidence; version and backfill derivations |
| [0002](0002-independent-semantic-text-profile.md) | Accepted | Keep text views independent and activate only complete profiles |
| [0003](0003-composable-composition-and-text-search.md) | Accepted | Rerank a mandatory composition shortlist with text |
| [0004](0004-local-first-and-conditional-advanced-retrieval.md) | Accepted | Stay local-first and require demonstrated need for advanced retrieval |
| [0005](0005-cross-film-composition-candidates.md) | Accepted | Exclude the source film before composition candidate generation |
| [0006](0006-separate-durable-user-state.md) | Accepted | Keep bookmarks outside replaceable search indexes |
| [0007](0007-typed-modular-search-recipes.md) | Accepted | Compose explicit search facets over current evidence |
| [0008](0008-grounded-match-cut-and-temporal-motion-boundaries.md) | Accepted | Separate grounded still matching, exact-frame refinement, and temporal motion |
| [0009](0009-complete-framing-spatial-cache.md) | Accepted | Cache production Framing grids only under complete profile activation |
| [0010](0010-dedicated-mood-semantic-view.md) | Accepted | Isolate Mood to stored feeling and energy evidence |
| [0011](0011-expose-resolved-source-inputs.md) | Accepted | Explain exact dragged-source inputs without inventing visual text |
| [0012](0012-query-bound-uploaded-image-recipes.md) | Superseded | Use one uploaded still as a bounded broad-visual recipe signal |
| [0013](0013-broad-query-evidence-and-candidate-breadth.md) | Accepted | Match broad-query lexical intent and expose passive cross-film candidates |
| [0014](0014-preserve-external-subtitle-evidence.md) | Accepted | Preserve selected external subtitle evidence before dialogue derivation |
| [0015](0015-fail-closed-on-degenerate-whisper-output.md) | Accepted | Discard structurally degenerate Whisper rows while preserving visual search |
| [0016](0016-trust-explicit-english-primary-audio-tags.md) | Accepted | Prevent foreign-language cold opens from overriding explicit English audio metadata |
| [0017](0017-progressive-authoritative-result-windows.md) | Accepted | Deepen one bounded backend-ranked result stream on demand |
| [0018](0018-category-bound-uploaded-image-facets.md) | Accepted | Bind uploaded stills to honest Look or Framing retrieval |
| [0019](0019-soft-temporal-result-spread.md) | Accepted | Defer nearby ordinary results without deleting them |
| [0020](0020-bounded-discovery-rank-and-visual-reserve.md) | Accepted | Use bounded broad-search repeat rank and selective visual reserve |
| [0021](0021-unified-open-discovery-repeat-rank.md) | Accepted | Use one bounded repeat rank for open non-image-gated discovery |
| [0022](0022-explicit-external-subtitle-review.md) | Accepted | Require explicit review before using an uncertain external subtitle |
| [0023](0023-operator-archive-imported-releases.md) | Accepted | Allow intact imported releases to move into operator-managed evidence storage |
