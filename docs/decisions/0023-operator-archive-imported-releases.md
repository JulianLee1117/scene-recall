# ADR-0023: Permit operator archiving of imported releases

- Status: Accepted
- Date: 2026-09-01
- Supersedes: ADR-0014 and ADR-0022 post-ingest location clauses only
- Superseded by: None

## Context

ADR-0014 and ADR-0022 require film intake to leave each release copy untouched
in `incoming` so selecting a canonical subtitle never destroys raw timestamped
evidence. That remains the safe import transaction. After a film has published
successfully and its torrent is no longer active, however, keeping every
completed release in the download staging directory makes the operational
boundary unclear and prevents `incoming` from returning to an empty queue.

The application has no need to read release debris after the canonical film
and selected sidecar exist in `films`. Some release directories still contain
unique subtitle tracks or provenance that must not be discarded merely because
they are not active search inputs.

## Decision

Keep intake and ingestion non-destructive: neither process deletes, prunes, or
automatically archives a release directory. The release remains untouched in
`incoming` through canonical import and successful film publication.

Once publication is verified and downloading or seeding has ended, an operator
may move the entire top-level directory carrying `.scene-recall-imported`
intact into external archival storage. `V:/scene-recall/evidence/imported-releases/`
is the current operator convention, not a configured application path. Preserve
the marker and every raw timestamped subtitle together; do not select, rewrite,
or delete individual evidence files during archival.

The application continues to use only the canonical film and selected sidecar
in `films`. It does not discover, index, clean, or promise availability of the
operator archive. Restoring an archived release is an explicit filesystem
operation, not an ingest fallback.

## Consequences

- `incoming` can remain a truthful download and import staging area without
  sacrificing raw subtitle provenance.
- Canonical runtime inputs stay simple and flat in `films`; no second ingest
  source or evidence lookup path is introduced.
- Archive backup, retention, and cleanup remain operator responsibilities.
- Torrent advertisements, samples, and featurettes may consume archive space
  because archival moves whole release directories rather than guessing which
  files will matter later.
