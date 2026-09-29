# ADR-0102: Rejected releases are deleted, not archived

- Status: Accepted
- Date: 2026-09-29
- Extends: explicit film index withdrawal (`pipeline.index.remove_film`)
- Superseded by: None

## Context

A release that turns out to be bad (watermarked, a duplicate download, a wrong
cut) had no supported end state. Film withdrawal removed index rows only, so
operators and agents moved the rejected video, its asset folder and its playback
copy aside by hand into ad hoc `discarded` folders. On 2026-09-29 these held
5.7 GB:
- watermarked copies of *Sinners* and *Drive My Car*;
- a *Drive My Car* playback copy;
- a duplicate *Sinners* download;
- 10,700 derived files.

None was referenced by the index or any record. The owner asked for deletion to
be the standard.

## Decision

`python -m pipeline.index.remove_film FILM_ID --expected-path PATH --delete-files`
rejects a release in one explicit operation. It is dry-run by default; apply
requires a new receipt.

1. It withdraws the index rows exactly as before (locks, profile republication,
   rollback on failure).
2. It then deletes:
   - the source video, only while its content hash still equals the film ID, so a
     replacement at the same filename is kept;
   - the film's asset folder, emptied under the film lock and removed after it is
     released while the global ingestion lock still excludes ingestion;
   - the film's playback copy.

   Deletion refuses links and anything outside the configured roots.
3. A failure (for example, a file in use) keeps the index withdrawal and marks the
   receipt `files_pending`. Rerunning finishes it, and also cleans a film that
   was withdrawn earlier.

Jobs, download records, bookmarks and projects are never deleted. Imported
releases' archived evidence (ADR-0023, ADR-0047) is unchanged, and so is
cancellation, which already deletes a download's staging folder. Without the
flag, withdrawal stays index-only.

## Consequences

- Rejected copies no longer accumulate; a rejection leaves nothing to clean up
  later.
- Deletion is irreversible. The dry run, content-hash identity check and receipt
  are the safeguards; recovering a deleted release means downloading it again.
- Saved clips from a rejected film show as unavailable, as they already did after
  a manual move.
