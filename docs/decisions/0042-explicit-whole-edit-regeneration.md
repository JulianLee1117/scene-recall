# ADR-0042: Explicit whole-edit regeneration from current settings

- Status: Accepted
- Date: 2026-09-13
- Extends: ADR-0033 and ADR-0041
- Supersedes: exposing only gap filling from the editor's generation action
- Superseded by: ADR-0043 for pacing count authority and previous-footage exclusion
- Superseded by: [ADR-0060](0060-music-led-whole-passage-timing.md) for separate musical timing and preservation of current listening on a pace-only rebuild
- Partially superseded by: [ADR-0064](0064-scoped-editor-direction-and-two-tab-workspace.md) for the AI direction tab's generation entry, canonical user direction and explicit regeneration on first Generate while Fill gaps preserves all existing cuts

## Context

A filled music edit disabled Generate. Changing Balanced to Energetic marked
directions stale but could neither replace the filled timeline nor change its
cut count. Users need one explicit action that applies their settings and rebuilds
the video, without manually clearing footage or coordinating multiple model jobs.

## Decision

Add `generate.mode: regenerate` to the existing durable generate job. It takes no
slot IDs. Keep fill and targeted improve semantics unchanged. Regeneration is an
explicit replacement of all cuts, slot directions and placements; reject placed
locks with instructions to unlock first, rather than silently overriding them or
introducing a partial retiming strategy. Unused locked bin clips do not block it.

Start a fresh arrangement privately from the frozen project while preserving its
track, passage, global settings, user song context and user-authored visual plan.
Discard old AI interpretation/direction state in that private proposal and invoke
the existing audio stage through its input-scoped cache. Pacing belongs to the
cache identity, so changing pace cannot reuse the old pace's interpretation.
Rebuild intentions from current musical moments and use the existing bounded
source-aware timing/selection contract. Retain the 32-moment generation limit.
Energetic is guidance toward shorter shots and concentrated visual changes, not
a promised minimum cut count or one cut per detected beat.

Apply the complete proposal as one revision, with the existing cancellation,
source validation and stale-revision checks. The old arrangement remains saved
until completion. Retain old clips in Saved clips; preserve the existing 100-clip
document bound with an explicit capacity error instead of silently deleting
retained footage. Undo and History recover the prior arrangement. No new job type,
database schema, model stage, retry loop or search-ranking change is needed.

Once an edit exists, the primary action reads Regenerate edit and opens the
existing settings dialog. Its explicit Regenerate edit button applies the draft
and queues the full rebuild. Apply changes only applies settings; Enter never
starts generation. Partial edits also expose Fill gaps to keep their current cuts
and placements. First Generate remains the existing direct automatic flow. Job
Details continues to show actual progress throughout regeneration.

## Validation and limits

Verify changed pace is frozen before enqueue, the old edit is retained while
running, fresh moments can change cut count, retained clips and locks are guarded,
failure/cancellation apply nothing, and success restores through existing revision
history. Browser acceptance includes the full-timeline action, staged pace changes,
generation progress, and Undo. These checks establish the regeneration workflow;
they do not establish artistic quality or guarantee more cuts for every song.
