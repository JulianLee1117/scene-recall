# ADR-0034: Explicit shot search and selection

- Status: Accepted
- Date: 2026-09-11
- Supersedes: ADR-0033 for the selected-shot interaction
- Superseded by: None

## Context

The selected-shot action called Improve this shot combined prompt rewriting and
automatic replacement. Its name did not describe either change, and editing a
precise search still required finding a separate advanced action. The existing
draft path also placed a result immediately, so relabeling it Find scenes would
not provide explicit user selection.

## Decision

Make the selected-shot flow edit search, Find scenes, preview, Use scene. Add
`suggest_only: true` to the existing durable draft request, requiring exactly
one unlocked slot. Reuse bounded recipe retrieval, duration checks, source
authority and per-query evidence. Save up to six ranked legal alternatives and
the resolved search/error, preserving all placed clips, timing, directions,
feedback, analysis and the current scene's reason/evidence. No hosted planning
or selection call runs for this search. Choosing a result remains a separate
undoable edit through the existing placement helper.

Keep AI prompt assistance optional. Rewrite prompt uses the existing plan job
with user intent, feedback, music and neighboring footage as context. It changes
the prompt, not the selected scene. Keep whole-edit Generate edit automatic and
label its standalone placement stage Fill gaps. Preserve the existing targeted
generation API for compatibility, without exposing it as the default inspector
action. No new index, retrieval model or job framework is needed.

## Validation and limits

Verify request/store scope validation, no hosted selector call, legal candidate
windows and evidence, preserved placement and unrelated slots, no-match behavior,
locks, revision conflicts, cancellation and browser selection/Undo. Retrieval
rank does not establish creative quality; source review remains available.
