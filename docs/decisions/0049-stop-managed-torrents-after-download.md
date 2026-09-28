# ADR-0049: Stop managed torrents after download completion

- Status: Accepted
- Date: 2026-09-14
- Extends: ADR-0047
- Amends: ADR-0047's completion handling with an independent client stopping policy
- Superseded by: [ADR-0080](0080-cancelled-acquisition-staging-cleanup.md) for explicit cancellation staging retention only; completion stopping and archival remain unchanged

## Context

Managed downloads originally supplied unlimited per-torrent sharing limits and
relied on the acquisition monitor to stop completed torrents. Choosing the Stop
action prevented inherited automatic file deletion, but unlimited limits did
not stop seeding while that monitor was unavailable. The user explicitly wants
completed downloads to stop sharing and proceed through ingestion and cleanup.

## Decision

Configure each managed torrent with `ratioLimit=0`, `seedingTimeLimit=0`,
`inactiveSeedingTimeLimit=-1`, and `shareLimitAction=Stop`. Apply the policy on
new submissions and when recovering a duplicate submission that passes the
existing exact ownership checks: info hash, application category, acquisition
tag, and save path. Reapply it before each app-controlled client start,
including a retry that resumes a stopped download. A failed limit update must
prevent that start. Monitor-controlled retries retain the existing path
ownership check before calling the client. Do not change global qBittorrent
preferences or settings for unrelated torrents. This change does not
bulk-migrate already-running client torrents.

qBittorrent enforces the completed-torrent stopping policy independently of the
acquisition monitor. Client completion and sharing-limit checks may permit a
brief transition before stopping; the contract is no sustained post-download
seeding, not zero uploaded bytes. Uploading pieces while a download is still
in progress remains possible. Manually force-starting a managed torrent is
unsupported because it can override automatic stopping.

The monitor retains its explicit stop request and verifies complete files and
stopped client state before recording the import plan. It detaches with file
deletion disabled, confirms detachment, and performs the existing verified
canonical import. Client stopping does not imply successful media validation,
ingestion, or permission to discard release evidence.

Cleanup remains the intact archival operation accepted in ADR-0047: only after
the linked ingestion succeeds and the film is published may the monitor move
the remaining owned release directory to
`incoming_dir.parent/evidence/managed-releases/<id>`. Preserve the canonical
film, selected SRT, all original subtitle tracks, extras, and ownership records.
There is no recursive deletion or torrent-client delete-files action. Existing
downloads already detached from qBittorrent require no client-policy update.

## Consequences

- A stopped acquisition monitor cannot leave newly managed completed torrents
  seeding indefinitely.
- Download completion, verified import, searchable publication, and intact
  release archival remain distinct recoverable stages.
- The policy applies to Scene Recall's owned torrents only; manual downloads
  and unrelated client settings retain their existing behavior.
- A brief client check transition and uploads during active downloading remain
  possible, and force-start is outside the managed workflow.
