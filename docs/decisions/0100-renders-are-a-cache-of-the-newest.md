# ADR-0100: Project renders are a cache of the newest preview and export

- Status: Accepted
- Date: 2026-09-29
- Supersedes: ADR-0068's retention of every saved-project export
- Superseded by: None

## Context

Every preview or export kept its full video under `assets_dir/lab/renders/<job>`
until its project was deleted. Closing the export dialog removed nothing, and
the editor only ever offers a project's newest completed render ("Latest
export"). Iterating on one 58-second edit left about a dozen unreachable 1080p
renders, and 15 render jobs held about 400 MB. The output endpoint already
treats a missing file as a removed cache ("render this revision again"), and
every render is reproducible from the saved revision frozen in its job.

## Decision

Each project keeps its newest completed `render` job per mode (preview and
export), ordered like the editor's job list. The existing idle maintenance pass
removes the render directories of that project's older completed renders. It
rechecks the ledger under the same write transaction as other garbage removal.

- A failed, cancelled or still-running render never supersedes a completed one.
- A preview never removes an export, and an export never removes a preview.
- Job records, receipts and project history are untouched; only the video cache
  goes.
- Projectless sessions (Match Cuts discovery, Transitions) keep their histories,
  and named experiment results stay outside the policy (ADR-0068).

## Consequences

- Storage per project is bounded at two renders, with no quota, age rule, grace
  period or new setting to tune.
- An older revision's video must be rendered again to watch it inside the app.
  Downloaded copies are unaffected.
- The dry-run CLI (`python -m pipeline.lab.cleanup`) lists superseded renders
  before anything is removed.
