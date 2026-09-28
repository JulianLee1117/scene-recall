# ADR-0063: Idle editor search warmup

- Status: Accepted
- Date: 2026-09-14
- Extends: ADR-0034 and ADR-0059
- Supersedes: None
- Superseded by: None

## Context

A manual search for `face` in one split music slot took 21.55 seconds after a
0.20-second queue wait. It issued one candidate-only draft, with no hosted
planning or selection. A read-only replay with the editor's CPU/offline settings
took 17.69 seconds cold and 2.57 seconds warm; ordinary API search took 1.33 seconds.
Most of the cold cost was initializing the process-local models after a development
reload. Offering unused references from other placed clips added about 0.73 seconds
even though the request contained only text.

## Decision

Prepare configured editor search encoders while its queue is empty. Keep the
same CPU resource policy, checkpoints, normalization and vector identities.
Check available index readiness under a short publication guard, then release
it before loading or running a model. Attempt each encoder once per process;
retry missing readiness at most every 30 seconds. Log failures and preserve
ordinary search error behavior. Skip idle preparation in `--once` mode and when
the existing `SCENE_RECALL_SKIP_WARMUP=1` test switch is set.

Check queued work, graceful stop and source reload before each encoder. Model
loading is synchronous and cannot be safely preempted midway; a newly arriving
job may wait for the active load. Keep existing FIFO claims and worker ownership.
Do not enqueue warmup jobs or mutate projects, search evidence or indexes.

Only construct offered source references when a requested direction includes a
source clause. Plain text and combined text recipes retain the existing search
resolver, rankings, duration checks and evidence. Source recipes still rebuild
their references and reject stale, forged or self-referencing anchors.

## Validation and limits

Exercise queue/stop/reload yielding, single attempts, unavailable readiness,
nonfatal failure, CPU enforcement and publication-lock release during inference.
Retain source-recipe authority tests and compare the exact manual search's ranked
alternatives before and after the optimization. Development reloads clear model
caches, so idle preparation repeats in the new process. This reduces the observed
cold-start penalty; it does not guarantee a latency bound or remove queueing for
other editor work.
