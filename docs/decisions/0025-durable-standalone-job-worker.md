# ADR-0025: Durable standalone ingestion and Lab worker

- Status: Accepted
- Date: 2026-09-11
- Supersedes: None
- Superseded in part by: ADR-0059 (role separation, ownership and resource policy)

## Context

The API-memory queue loses pending work and status on restart. Music analysis
and rendering also need cancellation, frozen inputs and durable results.

## Decision

Use a schema-versioned SQLite job ledger in `state_dir/lab/lab.sqlite3`, separate
from the bookmark database. The API enqueues; one standalone worker, protected
by a state-directory lock, claims FIFO jobs transactionally. Preserve the
existing ingestion API response shape and isolated low-priority ingest child.

The child owns the existing global ingest lock. Music analysis/drafting also
acquires that lock so a separate CLI ingest cannot collide with its local
models. Search remains an independent interactive process; this does not
promise cross-process GPU memory eviction or distributed scheduling.

Jobs record a revision snapshot, progress, result and failure state. Queued work
survives restart. Abandoned running work becomes interrupted, never silently
requeued, because hosted request completion may be uncertain. Cancellation is
checked between model operations and during render subprocesses. Results apply
only if the base revision and locked clips remain valid; retain stale proposals.

## Consequences

- Running the application now requires a separate worker for ingestion and Lab
  jobs. API reload no longer loses the queue.
- One local worker deliberately bounds resource concurrency; it is not a
  distributed task system. Heavy model placement still needs measurement on
  the user's hardware before increasing concurrency.
- No paid request is automatically replayed during crash recovery.
