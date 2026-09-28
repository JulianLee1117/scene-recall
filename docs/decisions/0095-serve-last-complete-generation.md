# ADR-0095: Serve the last complete evidence and search generation during backfill

- Status: Accepted
- Date: 2026-09-28
- Amends: ADR-0093 (which profile compiled tables read) and ADR-0094 (API search
  snapshot policy)

## Context

Two gaps showed up while the library backfill ran on 2026-09-28:

- The understanding profile changed (240p proxies instead of 360p), so the pilot
  films had artifacts only under the superseded profile. A dialogue refresh
  recompiled those films, and compilation read only current profiles. That wiped
  their scenes, fame and story views until the batch caught up.
- While the refresh re-embedded dialogue views, 30 of 40 profiled queries ran
  without the semantic text channel. Units were published before their text
  features, and API search took the latest versions even when semantic
  coverage was incomplete. The editor already kept its last complete snapshot
  in this situation; search did not.

## Decision

1. Compilation serves each evidence kind's current profile, else the newest
   earlier profile of the same producer. Each compiled row's `sources` names the
   profiles that served it. Producers still read only current-profile inputs, so
   they recompute once those exist. `prune` removes a superseded profile only
   after its replacement exists.
2. API search pins the latest complete generation. A request reuses the
   process's last complete snapshot, without waiting, in two cases: a writer
   holds the publication lock, or units are published ahead of their semantic
   text features. A fresh process without such a snapshot:
   - returns retryable unavailability while the lock is held;
   - otherwise serves the incomplete library as published and never retains it.

## Consequences

- A producer change (model, prompt, proxy) degrades gracefully: search keeps the
  previous evidence until the new profile is backfilled, and `status` still
  reports those films as pending.
- A newly ingested film becomes searchable once its text features are published,
  a few minutes after its units, instead of briefly lowering quality for every
  query.
- If a text backfill fails outright, search keeps serving the last complete
  generation until a restart. The ingest failure is reported where it happens.
