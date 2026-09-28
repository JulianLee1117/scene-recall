# ADR-0055: Reload development workers between jobs

- Status: Accepted
- Date: 2026-09-14
- Extends: ADR-0025
- Supersedes: None

## Context

A saved music project with Rapid pacing passed validation in the current API,
but a worker started before Rapid was added rejected its frozen document using
an older Pydantic schema. The project was valid and generation failed before
any model work. Refreshing the API alone cannot update imported worker modules.
Waiting for the complete queue to drain also postpones deployment behind newly
arriving foreground work and maintenance batches.

## Decision

Add an explicit `python -m pipeline.lab.worker --reload` development mode. A
small launcher starts the worker in a fresh Python process with the same
configuration and inherited logging. It does not reload imported modules in
place or alter the project schema.

Watch runtime Python source under `pipeline`, excluding tests and cache files.
Capture source identity before worker project imports and check it before the
next job claim. When source changes, finish the active job, release the worker
lock, and replace the process before claiming more work. Preserve normal FIFO
and foreground-over-maintenance priority. Do not monitor media, generated
assets, test output, or configuration secrets. Configuration changes continue
to require an explicit restart.

The launcher waits for each child to exit before starting its replacement. A
startup/import failure remains visible and waits for a subsequent source edit
instead of looping or retrying hosted work. `--once` remains deterministic and
cannot be combined with reload mode. Normal worker invocation is unchanged.

## Consequences

- Local backend edits can become active without cancelling ongoing ingestion
  or generation, and without waiting for the whole queue to empty.
- No new queue, capability handshake, schema migration or model fallback is
  introduced. Rapid keeps its own pacing semantics.
- Crash recovery still records interrupted work and never automatically replays
  hosted requests. Retrying a failed generation remains an explicit action.
- Tests cover changes while idle and during a job, configuration forwarding,
  failed startup, source exclusions, and saved pacing values through the real
  store and worker dispatch boundaries.
