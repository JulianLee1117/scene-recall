# ADR-0006: Separate durable user state

- Status: Accepted
- Date: 2026-08-28
- Supersedes: None
- Superseded by: None

## Context

Saved scenes express user intent and must survive search-index repair,
backfills, and compatible film reingestion. LanceDB and `assets_dir` contain
replaceable derivations, while browser storage would bind saved work to one
browser profile and make loss or migration difficult.

Unit and frame identifiers are derived from the current shot segmentation and
are not guaranteed to survive a changed segmentation recipe. The retained film
identity and source timestamp are the stable evidence available for resolving
the same moment in a later index generation.

## Decision

Store bookmarks in a schema-versioned SQLite database under a separately
configured durable `state_dir`. Preserve film identity and evidence timestamp
as the anchor, with the original unit and frame identifiers as lookup hints.

Resolve a missing derived locator only to a current unit from the same film
that contains the saved timestamp. Never rebind by title or to another film
identity. Preserve unavailable records until the user explicitly removes them.

## Consequences

- Search indexes and derived media can be rebuilt without erasing saved work.
- Compatible reingestion can recover bookmarks whose unit identifiers changed.
- The state directory requires normal user-data backup and migration care.
- The initial store is local and single-user; synchronization and named
  collections remain separate future workflows.
