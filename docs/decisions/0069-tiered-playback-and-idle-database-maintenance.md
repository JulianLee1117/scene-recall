# ADR-0069: Separate playback storage and idle database maintenance

- Status: Accepted
- Date: 2026-09-16
- Extends: ADR-0062 and ADR-0059
- Supersedes: None

## Context

Full-length browser audio compatibility copies occupy SSD space although their
measured streaming bitrate fits the external film drive comfortably. Search
indexes and small random-access media benefit more from SSD latency. Repeated
database publications also retained substantial obsolete text and full-text
index files. Generic LanceDB 0.33 optimization can hit a known compaction decoder
failure, and the editor can retain older checked-out snapshots between jobs.

## Decision

Add an optional playback root independent of source films and search assets.
Preparation targets that root; omitted configuration preserves existing layouts.
Readers retain legacy fallback during a deliberate migration. A supported
relocator locks each film, copies and hashes exact validated bytes, publishes
the rebound destination receipt and then removes only the verified old output.
It preserves representation tokens, source identity and source-relative timing.
It skips active films and resumes from its receipts after interruption.

Retire obsolete database versions only through an explicit native pruning-only
command. Pin the optional maintenance runtime to the Lance version underlying
the installed database and certify it with row/vector/full-text tests. Default
retention is fourteen days; latest-only pruning requires an explicit option.
All table plans must succeed before pruning begins. Refuse tagged conflicts and
unverified-file deletion, and verify unchanged current heads, counts and indexes.

An API lifetime reader lease, worker ownership, global ingestion and publication
locks exclude unsafe concurrent use. Drain jobs and stop the API before applying
cleanup; saved queue entries survive. Do not compact live data, automatically
prune during ingestion, or infer disposable files by parsing private manifests.

## Consequences

- Full-length derivatives can use capacity storage while search and thumbnail
  access keep SSD latency. Storage accounting includes both roots.
- Existing configurations and incomplete migrations remain playable.
- An already-running ingestion finishes under its original configuration; its
  output is relocated afterward. Queued jobs load the new configuration.
- Cleanup is measurable and repeatable, but requires an idle maintenance window
  and intentionally reduces rollback history according to the selected policy.
- Raw films, timestamped evidence, saved projects and bookmarks are untouched by
  these storage operations.
