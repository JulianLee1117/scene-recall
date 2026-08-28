# ADR-0004: Local-first and conditional advanced retrieval

- Status: Accepted
- Date: 2026-08-26
- Supersedes: None
- Superseded by: None

## Context

Current multimodal research offers video-native embeddings, audio-video
representations, cross-encoder rerankers, query routers, and grounded RAG. Each
can help a particular failure mode, but installing all of them before the
product workflow is concrete would increase ingestion cost, latency, schema
surface, and maintenance without evidence that users need them.

The current PE visual baseline, dedicated local text profile, lexical search,
and bounded hosted annotation already cover many remembered-moment and visual
reference queries.

## Decision

Keep high-volume candidate generation local and deterministic. Use hosted
models for bounded annotation or a future shortlist stage where they can see
temporal or audio evidence the local baseline lacks.

Do not add temporal clip indexes, audio embeddings, a reranker, an always-on
LLM router, or RAG merely because they are available. First identify a concrete
workflow or repeatable retrieval failure, then shadow-test one challenger in a
separate profile. Use a small just-in-time comparison before global activation,
paid library-wide processing, or removal of a working fallback.

Add a reranker only when recall is adequate and ordering is the demonstrated
problem. Add grounded RAG only when users want reasoning, comparison, or
reel-building above retrieved evidence rather than simply better search.

## Consequences

- Interactive search remains fast, private, and inexpensive for normal use.
- The architecture can adopt newer models without precommitting the canonical
  schema to them.
- Motion direction, brief actions, soundtrack, and cross-scene reasoning remain
  known limitations until actual demand justifies their profiles.
- Deferred items are options, not an approved implementation backlog.
