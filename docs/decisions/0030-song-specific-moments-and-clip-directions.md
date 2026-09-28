# ADR-0030: Song-specific moments and individual clip directions

- Status: Accepted
- Date: 2026-09-11
- Supersedes: ADR-0029 for initial cut generation, direction ownership and explicit timing regeneration
- Superseded by: [ADR-0031](0031-explicit-timeline-direction-planning.md) for direction generation after manual timing edits only

## Context

The user observed monotonous cuts and repeated search prompts. Initial timing
used 2.5/3/4-second buckets derived from broad energy, and every slot within a
musical section shared its section's query. Editing one prompt therefore
changed several clips. Beat tracking measured pulse but could not explain
why a particular visual moment should begin or end. Source action also needs
room to finish, independently of a beat grid.

## Decision

Keep the existing listening, retrieval and source selection stages. Extend
the versioned audio interpretation with 1–32 contiguous editorial moments,
alongside at most eight broad emotional sections. Every moment contains
start/end, query, supported text facet, audible cue, purpose and timing note.
Ask for phrase/accent/pause/texture evidence, visual progression and purposeful
holds; prohibit invented precision and arbitrary duration variation. Validate
coverage, ordering, finite timestamps and passage bounds before application.
Keep earlier caches on disk under their original identities.

Initial cuts follow moment boundaries or explicit user markers. Do not turn
every detected beat into a cut. Old interpretations remain readable and can
use section boundaries as a fallback without synthesizing a fixed cadence.
Add optional `direction` and `direction_source` to each slot. Missing direction
inherits the old section query. Writing one creates a slot-scoped user intent;
it does not alter neighboring slots. Generated intentions can refresh during
analysis, while user-written directions, existing times and clips survive.

Each search uses its slot's effective direction. Deduplicate equal query/facet
pairs and bound a request to 32 distinct searches, at most 48 results each and
24 legal offers per slot. Reject a larger query set before retrieval. Keep one
bounded selection request with full timeline, neighboring choices, musical
moments and individual intentions. The planner still chooses only legal
offered ranges at fixed slot duration. It must explain the choice in relation
to that moment and its neighbors rather than repeat a generic mood label.

Add explicit `replan_timing: true` for analyze jobs only. Freeze it in the job
snapshot. Reject it before hosted work when a placed clip is locked. It
rebuilds empty slots from musical moments, retains source selections in the
bin, and keeps revisions available for Undo/History. Ordinary analysis and
retrieval never silently move existing cuts.

Expose simple manual timing: Space play/pause, cut dragging without default
beat snapping, Add cut at playhead, Set end at playhead, and Remove cut after
clip for a longer hold. Joining requires enough real source footage, protects
both adjacent locks, preserves passage coverage, and retains the displaced
selection. No frozen frames or speed changes are invented to fill time.

## Limits and consequences

- Beats remain measured musical guides; editorial moments are interpretations.
- The user can align a cut with an observed scene action in the live preview.
  The text planner does not watch footage or verify action completion. This
  change does not activate deferred temporal matching or a new video model.
- Longer passages can issue more searches than the former eight-section cap,
  but remain explicitly bounded and outside ordinary search latency.
- Tests cover offsets, moment validation, old-document compatibility,
  per-slot retrieval, bounded work, preservation, explicit replanning and
  real source constraints. Aesthetic quality still needs real-song review.
