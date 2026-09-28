# ADR-0059: Independent local editor and library workers

- Status: Accepted
- Date: 2026-09-14
- Extends: ADR-0025, ADR-0055
- Supersedes: One serial worker and a whole-job ingestion lock for ordinary editor work

## Context

A full film ingestion blocked music generation, analysis and export even when
the editor used already indexed films. The existing global GPU gate also covered
cloud requests. Multiple API keys would not address this local scheduling issue.

## Decision

Keep the existing SQLite WAL job ledger. Run two fixed local roles, each claiming
its own job kinds transactionally and processing them serially:

- `editor`: generation, audio analysis, rhythm, direction planning, scene search,
  next-scene suggestions, previews and rendering.
- `ingest`: film ingestion, temporal backfill, and advanced `match`/`match-search`
  operations using prepared GPU evidence. Foreground work precedes backfill;
  foreground jobs preserve FIFO ordering within this role.

`python -m pipeline.lab.worker` starts both fresh processes from one launcher.
`--reload` preserves between-job source refresh independently in each process.
Explicit `--role editor` or `--role ingest` supports separate terminals or local
service managers. `--role all` retains serial compatibility; an unqualified
`--once` still processes at most one job through that serial mode.

Each separate role holds a lifetime shared lock on the legacy worker lock and
an exclusive role lock. Serial/older workers require the exclusive legacy lock.
This prevents an old worker recovering another active role's jobs, including
after launcher death. Startup recovery marks abandoned running work interrupted
only within the owned role. It never requeues a paid request.

The editor hides CUDA before model imports and limits its CPU model threads.
Use the explicit native mask `CUDA_VISIBLE_DEVICES=-1`: on Windows, assigning an
empty Python value removes the native variable and does not reliably hide CUDA.
It uses the same configured PE/Qwen encoders and vector identities, with local
Beat This on CPU. Ingestion retains its existing GPU ownership. Prepared native
Match evidence has device/precision identities, so those GPU jobs stay on the
library role rather than silently switching their profiles. API search keeps
its existing independent runtime; this is not automatic GPU memory eviction.

## Library consistency

Each retrieval/planning job reads a pinned, read-only set of Lance table versions
and captured semantic/framing readiness manifests. Capture holds the existing
publication lock briefly; hosted calls and query inference never hold it.
The editor retains its last complete in-memory snapshot and refreshes between
jobs when current publication is ready. If a new publication is incomplete,
it continues using the prior complete library. A fresh process without such a
snapshot waits up to 60 seconds, with cancellation/progress, before reporting
that indexing must finish. Never fabricate compatible profile coverage.

No persistent generation pointer or new responsibility for all ingestion writers
is introduced. Render/rhythm/listening jobs do not need this search snapshot.
This pins index rows and readiness manifests, not arbitrary derived files on disk.
Original films remain immutable; temporal backfill uses distinct sample paths.
An explicit legacy reingestion can still replace derived keyframe files.
Final source validation uses the current canonical database; normal revision,
source and cancellation checks still guard application.

## Management and limits

The existing ledger also stores small per-role heartbeat/control rows, with
process identity, current job and graceful stop intent. These are diagnostic
records, not distributed leases: only file locks grant execution ownership.
`--status` shows both roles; `--stop` stops after active work. `--role` scopes a
stop request. Pending jobs remain saved. Neither command kills arbitrary PIDs,
cancels active jobs or changes the queue order.

The read-only `/lab/workers` endpoint lets queued tasks distinguish an offline
editor, another editor task, or a GPU match waiting on ingestion. Existing task
details expose the assigned worker. Start commands remain in operational help.

Tests exercise real separate Windows processes, shared/exclusive legacy locks,
transactional claims, scoped crash recovery, graceful stop, source reload,
CPU initialization and snapshots across publication. This remains a single-host
application; a broker, distributed leases and multiple GPU schedulers are deferred.
SQLite's [WAL concurrency model](https://www.sqlite.org/wal.html) fits these short
local ledger transactions; hosted requests never keep database transactions open.
