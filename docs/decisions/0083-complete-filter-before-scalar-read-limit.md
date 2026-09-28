# ADR-0083: Complete filtering before scalar read limits

- Status: Accepted
- Date: 2026-09-20
- Extends: ADR-0082

## Context

LanceDB 0.33.0 can push a plain scan limit before residual predicates when
another predicate uses a scalar index. Film-and-time lookups returned no rows
where thirteen existed; a unit-and-frame-index lookup missed its valid third
frame. This affected bookmark validation, editor source resolution and context
windows. Increasing the limit did not establish correctness.

## Decision

Use `pipeline.index.reads` for bounded compound scalar reads. The engine scan
has no row limit and returns completely filtered Arrow batches. The bounded
wrapper collects only the requested matching rows and closes the reader;
consumers requiring ordering can stream the matches while retaining a bounded
selection. Columns and batch size bound materialization. Early-stop consumers
must close their iterator.

Keep existing scalar indexes, indexed and unindexed rows, source identities and
pinned snapshot versions. Ranked vector and full-text queries retain their
own retrieval limits and policies. This is not a relevance change or a claim
that the earlier listening-booth semantic retrieval failure had this cause.

## Verification and limits

Real indexed scratch tables exercise late matches, compound timestamp ranges,
third-frame identity and rows appended after index creation. Verify affected
editor/bookmark/context consumers as well as reader cleanup and result bounds.
Read-only live checks recover the missing booth rows and third frame.

Correct filtering may still inspect many candidate rows. Bounded memory does
not imply constant latency; an upstream fix or alternative implementation must
pass these same regression cases before replacing this adapter.
