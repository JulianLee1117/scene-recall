# ADR-0031: Explicit direction planning for editable cut markers

- Status: Accepted
- Date: 2026-09-11
- Supersedes: ADR-0030 for direction generation after manual timing edits only
- Superseded by: [ADR-0032](0032-shared-musical-evidence-and-editable-planning.md) for shared evidence and editable sequence intent; [ADR-0033](0033-search-aware-editor-and-atomic-generation.md) for executable search plans

## Context

Splitting a placeholder copied one prompt into both pieces. Reanalysis could
assign the same broad musical moment to several user-defined cuts, while scene
selection only saw candidates already retrieved from those repeated prompts.
The small timeline also hid basic cut operations. The user requested direct
marker editing and a creative planner that considers framing, pacing, visuals
and storytelling across the edit.

## Decision

Keep one authoritative timeline. Add a default-false `needs_direction` flag to
slots. Splitting marks both pieces, rolling marks adjacent slots, and joining
marks the combined slot. Retain prior directions and explicit user ownership
as context. These manual edits make no hosted calls and preserve existing source
constraints, locks, full passage coverage and original-speed playback.

Add an explicit `plan` job. Resolve default or requested slot IDs before enqueue
and freeze them with the revision. At most 32 targets are allowed. Default targets
are empty, non-user-directed positions; the editor can explicitly prioritize
changed generated directions. A selected-slot regenerate action can replace its
user direction. Locked placements cannot be targeted. Validate exact returned
ID coverage and the existing supported MusicDirection fields. Do not accept
model-provided timing, clips or arbitrary document changes.

Use the existing configured text planner for one coordinated request with the
whole timeline, creative brief, music interpretation/rhythm when available and
caption evidence for existing choices. Ask for an editorial arc, purposeful
shot-scale/framing relationships, motifs, transitions and realistic visual
actions within the fixed durations. A creative prescription is not proof that
the song or footage contains an event. There is no new video model or motion
matching capability. Ordinary search remains untouched.

Planning neither relistens nor retrieves scenes. A manual timeline can receive
brief-led directions without analysis, but audio analysis remains required for
scene filling. Store planning provenance separately as `direction_plan`; never
make a brief-only plan look like audio analysis. Give validated outputs their
own full-context/model/schema/settings cache identity and preserve prior caches.
Track/passage replacement clears the associated provenance.

Apply only targeted directions and clear their outdated alternatives/reasons;
keep selected clips, exact times and other directions. Use existing atomic
revision application, Undo, stale-result and cancellation guards. No implicit
hosted retry or fallback is introduced.

Make the timeline full width with larger prompt-visible cards, persistent zoom,
fit and snap controls, direct selectable cut markers and an expanded view.
Double-clicking the ruler splits; marker dragging/nudging moves a cut; Delete
joins adjacent positions where real footage allows it. Keep the creative brief
visible and planning separate from Find scenes.

## Consequences

- New placeholders can get distinct coordinated intentions without losing a
  user's timing or sending audio again.
- Prompt generation remains reviewable, explicit and bounded; dragging never
  causes repeated paid requests.
- Exact scene action and motion continuity still need preview-based review.
  Better direction planning cannot repair missing or mistimed caption evidence.
- Tests cover scope, cache isolation, exact ID validation, locks, cancellation,
  revision conflicts and unchanged sources/times; browser QA covers marker
  operations, transport, prompt planning and Undo. Real-song aesthetic review
  remains a separate acceptance step.
