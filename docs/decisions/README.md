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
