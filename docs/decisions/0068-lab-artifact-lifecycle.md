# ADR-0068: Owned Lab artifacts and retryable cleanup

- Status: Accepted
- Date: 2026-09-15
- Extends: ADR-0006, ADR-0035 and ADR-0059
- Supersedes: ADR-0035's records-only project deletion
- Superseded by: ADR-0100 for project render retention only (each project keeps its newest preview and export)

## Observed failure

Project deletion removed ledger records without removing associated renders or
private request snapshots. Rendering also retained encoded clips and concat
files after producing its final output. A local read-only inventory found about
146 MB of orphan UUID render roots and 169 MB of terminal-job intermediates,
before applying an age grace. Most remaining render bytes belonged to explicitly
named experiment results and must not be treated as abandoned projects.

## Decision

Keep ownership simple: normal jobs have durable UUID identities; their existing
render roots, request files (including part/stage suffixes) and Match thumbnail
roots carry that UUID. The cleanup allowlist owns these three namespaces only.
Use an exact identity boundary, refuse links/junctions and resolved path escapes,
and never accept deletion paths from browser input. Original tracks/films,
shared versioned evidence, model files and non-UUID experiment namespaces are
outside this policy.

The revision-checked project deletion transaction still rejects queued/running
jobs. It also records each deleted job ID and configured assets root in a small
`artifact_cleanup` table before removing project/history/jobs. Immediately after
commit, attempt physical removal. A crash, offline volume or locked file leaves
the ticket for retry; a partial removal is idempotent. Acknowledgment requires a
complete inventory and successful removal. SQLite/filesystem cleanup failures
do not misreport a committed project deletion as failed. Return `cleanup_pending`
and keep the UI successful with a neutral retry notice.

Reuse the existing editor worker's idle loop rather than add a service or queue
framework. Retry at most every five minutes, with 100 tickets and 100 garbage
paths per pass. The same module has a read-only CLI and explicit `--apply` mode.
Reconcile preexisting orphan UUID artifacts after a 24-hour grace, preserving a
directory with any recent child. Preserve all existing jobs, including standalone
Match sessions, their final outputs/manifests and diagnostic receipts. Remove
only known render intermediates for terminal jobs. New renderer invocations
unlink their explicit temporary files in `finally` after subprocess teardown;
failed cleanup logs a warning without masking a valid output or original error.

The exact content-addressed PCM WAV namespace is scratch: all consumers decode
it again from original music before use. Remove files older than 24 hours only
with no queued/running editor work. Hold the same SQLite write serialization as
enqueue/claim across the final eligibility check and removal. Preserve hosted
interpretation caches, which may be required to validate saved provenance, and
all timestamped shared observations/context. Do not introduce a general cache
TTL or discard paid evidence merely because one project was deleted.

## Consequences and limits

No new daemon, scheduler, vector space, model call, per-file registry or project
document migration is needed. The small additive cleanup table preserves intent
across restarts; removing a project does not depend on browser storage working.
Future artifact namespaces must declare their owner/retention and extend this
allowlist with tests. Directory discovery remains a shallow scan plus eligible
tree checks; batch limits bound removals rather than wall time. A storage quota
or a shared-evidence collector needs an observed need and explicit reference
tracking, not an indiscriminate age rule.

Original imports, saved-project exports, standalone Match histories and explicit
experiment results remain retained. This is disposal of owned/dead/scratch work,
not a global disk quota. Copies exported outside configured storage are untouched.

Tests cover ownership variants, shared/live/experiment preservation, stale
children, cancellation, dry-run, bounded batches, crash/locked-file recovery,
database failure after commit, complete-inventory acknowledgment, path safety
and serialization against new work. Renderer tests cover successful rendering,
failure, cancellation and cleanup-error ordering.
