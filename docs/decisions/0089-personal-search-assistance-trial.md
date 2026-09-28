# ADR-0089: Opt-in personal search assistance trial

- Status: Superseded by [ADR-0090](0090-retire-personal-search-trial.md)
- Date: 2026-09-20

Retired at the owner's request on 2026-09-21. The record below is historical;
the interactive trial and its runtime integration are removed.

## Context

ADR-0086/0088 found that query category recognition can work while evidence
judgments and reranking remain mixed. The owner is the sole participant and
authorized an interactive experiment during normal use. Offline agreement with
agent labels does not establish that suggestions help them find useful footage.

## Decision

Add an isolated `pipeline.search_trial` service and `web/features/search-trial`
interface. A persistent, explicit switch admits two calibration goals followed
by twelve fresh goals. Each block of four evaluation goals contains two manual
and two assisted assignments, shuffled before collection. Capture the initial
query, confirmed film scope and optional private intent before revealing the
assignment; reformulations retain their goal and condition. New goal is explicit.
The ledger begins disabled in a fresh installation; the owner may enable or pause
it without changing ordinary search.

Assistance classifies only the submitted query and confirmed film IDs using the
existing typed Jev transport. It never receives private notes, result captions,
playback or feedback. A frozen mapping exposes at most two unselected suggestions
for existing Scene, Look, Mood or combined Words controls. Framing language and
plot intent do not invent available capabilities. Choosing a suggestion opens
the existing editor; the user must apply their refinement. Manual controls and
retrieval are identical in both conditions. This does not authorize reranking,
query rewriting, inferred movie scope or hidden filters.

Interpretation runs independently of retrieval, with one in-flight provider
attempt and no retry or backlog. Suggestions arriving after a one-second display
deadline, after another search or after pausing are not displayed. Opening Refine
snapshots available suggestions so an open menu does not shift. Late provider
receipts still count toward spend.

A small SQLite ledger in the existing state directory owns goals, submissions,
events, explicit feedback, frozen policy and provider receipts. Transactions
reserve each paid attempt before transport. The shared trial allowance is sixty
attempts and $0.25, with conservative per-call admission rather than a provider
billing guarantee. Unknown charges, interrupted attempts or changed interpretation
policy halt further paid calls. Pausing and restarting do not reset accounting.
No new worker, ingestion schema, index or telemetry service is introduced.

Playback is an observation, not success. Useful feedback requires playback plus
the owner's explicit judgment; missing feedback remains unknown. Calibration,
assigned condition, latency, unsupported requests and failed/late assistance stay
inspectable. Model labels remain separate from user intent and source truth.

## Evaluation and limits

The [personal trial protocol](../experiments/jev-human-search-study.md) defines the
readout. This small comparison informs this owner's workflow; task difficulty,
learning and a changing library prevent a population accuracy claim. Genuine
search goals and human judgments are still required. Existing regression queries,
automated fixtures and QA do not populate the personal ledger.

This narrowly supersedes ADR-0088's prohibition on normal-interface hosted calls
only while the explicit personal trial is enabled. Its frozen experiments and
promotion gates remain intact. Default assistance, automated routing or a reranker
require a separate decision supported by relevant interaction and quality evidence.
