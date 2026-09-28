# ADR-0047: Managed film acquisition through explicit client ownership

- Status: Accepted
- Date: 2026-09-14
- Extends: ADR-0014, ADR-0022 and ADR-0025
- Supersedes: ADR-0023's manual-only archival restriction for application-owned acquisitions only
- Amended by: ADR-0049 for client-enforced stopping after download completion; [ADR-0056](0056-automated-external-subtitle-checks.md) for shared subtitle validation and automatic review defaults
- Superseded by: [ADR-0057](0057-conservative-release-layout-selection.md) for automatic feature selection and release-layout handling; [ADR-0080](0080-cancelled-acquisition-staging-cleanup.md) for cancellation retention only

## Context

Adding a film currently requires searching outside Scene Recall, downloading
through a torrent client, manually stopping/removing the torrent, reviewing the
release, importing and queuing ingestion. The user requested one Films workflow
and CLI that carry a selected release through these stages. Downloads may take
hours and must not occupy the worker shared by ingestion and the two Labs.

## Decision

Use qBittorrent 5.x's explicit Web API as the downloader and optional Prowlarr
search over configured indexers. Keep manual magnet and bounded `.torrent`
upload available independently of release search. Do not add a site-specific
scraper, another movie-library manager, an LLM acquisition agent or a torrent
engine inside Scene Recall. The user supplies the canonical title/year/edition
when choosing a release. Report basic media validation honestly; torrent
integrity and sampled decoding do not prove film identity or subtitle sync.

Persist acquisition intent, revisions, opaque expiring release selections and
recovery journals separately under `state_dir/acquisition`. A singleton local
acquisition monitor polls the client independently of the inference worker,
with at most two downloading acquisitions. API and CLI submit the same durable
commands; neither performs background inference. Completed canonical sources
enter the existing durable ingestion queue. An ingestion failure or interruption
requires explicit retry, reusing valid pipeline caches without silently replaying
hosted operations after process restart.

Every submitted torrent has a normalized v1 info hash, category `scene-recall`,
unique acquisition tag and fixed `incoming/.scene-recall-managed/<id>/data`
save path. A marker outside the downloaded data tree proves directory ownership.
Existing torrents with another owner or save path are never adopted or altered.
Reject traversal, link/junction escapes, conflicting filenames and executable
release payloads. Use configured server origins only; browser release IDs cannot
choose a download URL. Credentials, tracker URLs and recovery internals stay out
of public responses and error logs. Per-torrent settings prevent inherited
automatic deletion, and enabled client external-program hooks block managed add.

Wait for verified download completion and stopped client state. Reuse the shared
manual-intake naming and conservative subtitle rules; ambiguous videos or
subtitles require explicit review. Probe media and decode bounded samples, then
persist the exact import plan before detaching the torrent with file deletion
disabled. Move the source on the same volume without replacing existing files,
preserve the selected raw subtitle and reconcile interrupted imports against
recorded file identity. Keep the existing film identity and vector spaces.

Only after the linked ingest job succeeds and the film is fully published may
managed cleanup move the entire remaining acquisition directory intact into
`incoming_dir.parent/evidence/managed-releases/<id>`. Preserve every raw subtitle,
extra and ownership record. Verify canonical source identity and all remaining
file identities before archival; unfamiliar or changed files stop cleanup.
Cancellation preserves downloaded and canonical files. There is no recursive
delete, no torrent-client delete-files action and no cleanup of unrelated
incoming directories, old evidence archives, saved scenes or Lab projects.

## Consequences

- Films and CLI can show a resumable acquisition pipeline with real download
  progress, explicit review, cancellation and stage-aware retry.
- Existing manually downloaded releases keep their current workflow and manual
  archival rule. The managed subtree is excluded from manual discovery/import.
- qBittorrent and optional Prowlarr run as separately configured local services;
  opening the Films tab installs nothing and starts no download.
- Subtitles and extras still consume archive space. Clearing staging is distinct
  from destructive retention, which remains outside this implementation.
- Duplicate torrent submissions, stale commands and interrupted client/network
  operations reconcile against durable identity rather than guessing completion.
