# ADR-0091: Compare query intent routing over independent evidence candidates

- Status: Superseded by [ADR-0094](0094-search-v2.md) (retired 2026-09-28)
- Date: 2026-09-21
- Extends: ADR-0086/0088; follows the next decision gate in ADR-0090

## Decision

The owner requested implementation and an actual retrieval comparison after
reviewing the proposed intent-first architecture. Add a model-independent intent
record, deterministic capability-aware retrieval plans, and a bounded Jev
query-only adapter. This experiment changes candidate generation, unlike the
older frozen-head rerank. It does not reinstate the retired interaction study.

Compare ordinary search, fixed expansion across available semantic evidence
views, and intent-directed expansion. Preserve the entire query and explicit
film scope. Unknown intent retains ordinary search. Narrative and action-timing
needs are recorded as unsupported; inferred concepts never become hard filters.
Shot/style hints use existing textual evidence, never a fabricated image gate.

All variants share the same pinned read-only library snapshot and baseline
candidate pool. Fixed and Jev plans divide the same supplemental candidate budget
among their selected views. Fetch each required view only once at its maximum
needed depth; slice that ranking for each plan. Report adapter calls, depths and
returned rows separately: equal candidate budgets do not establish equal compute.
Fuse a half-weight baseline reciprocal-rank vote with a half-weight strongest
targeted-view vote, then apply the ordinary final filtering/diversity once.
Duplicate correlated routes cannot multiply the targeted group's influence.
This is an initial testable policy, not a calibrated optimum.

## Interaction and operations

Ordinary search remains the default and has no hosted dependency. After a plain
text search, an explicit **Compare Jev** action produces three frozen playable
lists. Switching lists incurs no additional hosted requests or retrieval.
Manual categories, uploaded references and film-only browse retain their
existing semantics and do not silently opt into interpretation.

The offline runner journals bounded calls and exact receipts before retrieval.
The interactive adapter separately admits at most 64 attempts and $0.05 of
reported cost with a conservative per-call reserve; this is local admission,
not a provider-enforced invoice cap. Unknown cost halts further paid attempts.
Exact completed request receipts are cached, failed attempts never retry
automatically, and only query intent is transmitted. The UI gives interpretation
two seconds before falling back; a late provider operation can finish recording
its receipt without changing the displayed ranking. Concurrent paid attempts
are bounded by a file lock. No ingestion, backfill, source or saved edit changes.

The hosted comparison has a 45-second total server deadline. Cancel/disconnect
or expiry stops subsequent retrieval stages; an already running native lookup
retains the single admission slot until it exits. The UI keeps ordinary results
usable, offers Cancel, and independently recovers after 50 seconds even if a
request or response body stalls. Late results cannot overwrite a new query or
retry. Neither deadlines nor errors automatically submit another paid request.
The offline frozen comparison remains outside this interactive deadline.

The separate frozen `/search/intent/benchmark` diagnostic measures each strategy
independently using the API's already loaded models. It admits at most two rounds
of three strategies per request, a result limit of 48, and one shared pinned
snapshot. Each execution gets a fresh request memo; models and operating-system
caches remain warm. It shares the comparison admission slot, uses only validated
saved intent, and never contacts a provider. Its CLI runs at most four cases,
records loaded implementation hashes, and admits no new case after five minutes.
This isolates retrieval cost without presenting it as hosted end-to-end latency
or a relevance evaluation.

A live latency failure exposed slow scalar-index vector gathering for broad
individual-view queries. Such unscoped partial-view reads may use an exact
sequential cosine scan of the same pinned table version when the optional native
scanner is available and no vector index exists. Film-scoped and all-view reads,
vector-index policy, projections, filters, candidate limits and ranking remain
unchanged; without the scanner, use the existing query path. A same-snapshot
five-view read-path check fell from 55.08s to 11.66s with all three complete result
lists equal. That diagnostic substituted existing vectors for encoders and is
not an end-to-end query benchmark.

## Evaluation and promotion

Use known source windows plus diverse exploratory queries. Report candidate
novelty, known-window ranks, fallback behavior, provider cost and timing. Existing
anchors have selection bias; provisional dialogue/caption evidence is not source
verification. More new candidates, more films or correct intent labels do not
prove relevance. The comparison's shared-work duration is not production latency
for each strategy. Prefer an independently useful improvement over the fixed
expansion control before promoting Jev. Any default-serving activation or
evidence reranker needs a separate decision supported by held-out relevance and
interaction results.
