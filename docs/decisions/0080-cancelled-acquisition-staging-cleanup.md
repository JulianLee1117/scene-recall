# ADR-0080: Discard owned download staging on explicit cancellation

- Status: Accepted
- Date: 2026-09-18
- Supersedes: ADR-0047 and ADR-0049's cancellation retention only

## Context

Cancel previously stopped a managed torrent while retaining its files. Abandoned
or rejected releases therefore accumulated partial movies and release payloads.
The user explicitly requested cancellation that cleans up these files, with a
small maintainable implementation rather than another storage service.

## Decision

Use the existing acquisition record and singleton monitor. An explicit Cancel
enters a durable `cancelling` state with pending cleanup, including from failed
items. Preserve side-effect journals written by an in-flight operation without
letting stale updates erase cancellation. Do not label the item cancelled until
cleanup completes; show errors and retry through restart.

Reconcile canonical moves and ingestion enqueue crash windows. Request linked
ingestion cancellation and wait for terminal job status. Stop the exact owned
torrent, remove it from qBittorrent with `deleteFiles=false`, and confirm absence
on a later tick. Journal add attempts before submission and observed ownership
separately; uncertain add acceptance cannot be treated as confirmed detachment.

Verify the fixed managed directory, marker identity and bounded complete tree
before any unlink. Reject links, junctions, unexpected wrapper contents and
changed identities. Delete only that acquisition's staging files and empty
directories, retaining its ownership marker until last. Journal the wrapper
identity before deletion to recover a crash after removing its marker. Locked
files or unavailable storage remain pending with automatic bounded-delay retry.

Preserve imported canonical films and selected subtitles, shared derived assets,
manual incoming sources, saved application state and existing evidence archives.
Cancellation does not become a general library deletion API. Ordinary successful
acquisitions retain their intact archival policy. This is an explicit user
discard of an abandoned managed release, not automatic expiry of raw evidence.

Keep small source descriptors and history for recovery. Retrying a cleaned item
before import redownloads it with fresh stage journals; retrying after import
reuses the retained canonical source and records that staging was intentionally
discarded. Historical cancelled items are not silently migrated or bulk-purged.

## Consequences

- Abandoned managed downloads stop consuming large amounts of staging storage.
- Cancellation is asynchronous and honest about incomplete cleanup.
- Uncertain client state, ownership conflicts and disconnected drives can delay
  cancellation; retained files are safer than guessing about ownership.
- An imported film remains a library source even when its preparation is stopped.
  Removing a library film remains a separate operation.
