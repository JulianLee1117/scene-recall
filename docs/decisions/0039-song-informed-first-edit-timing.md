# ADR-0039: Song-informed timing for the first generated edit

- Status: Accepted
- Date: 2026-09-13
- Extends: ADR-0029, ADR-0030, ADR-0033 and ADR-0035
- Supersedes: treating untouched local rhythm starters as fixed user cuts
- Superseded by: ADR-0041 for fixing first-edit cuts before source selection
- Partially superseded by: [ADR-0064](0064-scoped-editor-direction-and-two-tab-workspace.md), which makes Fill gaps preserve starter cuts and uses explicit whole-edit regeneration for first Generate

## Context

The inspected Steve Lacy passage at 9.94–39.94s has 36 beat guides and nine
downbeats. The balanced local bar/RMS heuristic created two empty positions of
14 and 16 seconds. Generate then preserved those positions, even though the
user had never chosen their timing. The audio interpretation's proposed moments
could not influence the first edit. The listening prompt also over-associated
repetition with economical long holds.

## Decision

Keep local beat preparation and editable starter placeholders. Mark only a newly
created, wholly empty rhythm timeline with optional `provisional_timing`, using
contract `local-rhythm-starter-v1` and a SHA-256 fingerprint of track, passage and
ordered slot IDs/start/end. Absence means fixed timing, including legacy projects.
Never infer provisional status merely from empty slots or matching durations.

Default Generate may adopt current validated audio editorial moments before
planning and searching only when this marker and fingerprint still match,
rhythm provenance belongs to the current track/passage, and the document has no
source selections, user timing markers, custom directions or saved shot work.
Check eligibility before and after listening. Adopt the new timing as the fixed
baseline for subsequent planning/selection, and retain the usual guard against
any later cut movement. Clear the marker after successful generation, even when
some searches leave gaps. Missing editorial moments retain the current timing
with an explicit progress message.

Manual timing changes, placement, per-shot directions/feedback and explicit shot
planning/search take ownership of the displayed slots. The frontend clears the
marker, and backend eligibility/fingerprint checks provide a second guard.
No-op edits and global creative guidance do not claim starter timing. Standalone
analysis preserves existing timing; ordinary fill and next-scene search do not
gain a general retiming capability. Cancellation/failure leaves the original
project unchanged; the existing worker saves one final revision with Undo.

The timeline labels eligible placeholders as starter cuts. For an existing edit,
**Timeline options → Plan cuts from music** explicitly invokes the existing
`analyze` job with `replan_timing: true`. Explain that it replaces cuts with empty
positions, retains prior footage in the bin, rejects locked placements, and is
undoable. It does not require re-importing music or a new job type.

Advance the interpretation cache contract to `pacing-aware-song-edit-moments-v6`.
Keep the existing audio model, schema and provider boundary. Repeated energetic
figures may support visual answers and contrast without an invented audio change;
calm sustained passages may still justify long holds. Pacing preferences are
creative guidance, not a minimum cut count or a fixed-duration template. No new
hosted stage, detector, automatic action verification or learning system is added.

## Validation and limits

Exercise the real measured failure fixture, strict legacy/manual protection,
stale fingerprints, custom work, current analysis scope, interruption/cancellation
and one-revision restoration. Test the explicit replan UI and starter notice.

A live same-passage listening check with the revised prompt proposed eight
moments (six four-second and two three-second moments) in 53.48 seconds, compared
with four moments in the retained earlier interpretation and two heuristic slots
in the saved project. This demonstrates removal of the timing bottleneck, not
verified vocal alignment or artistic pacing. The remaining regularity and broad
cue descriptions need playback judgment; shot-aware timing remains separate work.

Prompt changes are evaluated against the actual failure instead of adding a new
model stage; this follows the clear-instruction and systematic-evaluation guidance
in [OpenAI's optimization documentation](https://developers.openai.com/api/docs/guides/optimizing-llm-accuracy#optimization).
