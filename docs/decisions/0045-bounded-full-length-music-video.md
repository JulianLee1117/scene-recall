# ADR-0045: Full-length music videos through bounded generation

- Status: Accepted
- Date: 2026-09-13
- Extends: ADR-0032, ADR-0033, ADR-0041, ADR-0043 and ADR-0044
- Supersedes: the 90-second project passage and 100-clip project limits
- Superseded by: [ADR-0060](0060-music-led-whole-passage-timing.md) for per-section timing/count authority, text batching and rejection of individual holds over 90 seconds; bounded listening and atomic publication remain active

## Context

The user requested complete music videos up to ten minutes. Raising only the
passage limit would leave short-edit limits in audio interpretation, shot count,
selection, source timing and saved clips. The current selector already receives
hundreds of source candidates for a 90-second edit. Sending a ten-minute edit in
one larger request would dilute musical detail and substantially enlarge input.

## Decision

Allow passages up to 600 seconds, 300 timeline positions and 600 saved clips,
including retained footage. These project limits are distinct from the existing
32-shot model-request bound. Keep the current editor, project identity, timeline,
durable job queue and atomic revision application. Import still starts with a
30-second selection. The song picker offers an explicit full-song selection,
capped at ten minutes; waveform fitting remains a separate view control.

Preserve the existing path for passages no longer than 90 seconds. For longer
passages, use deterministic sections no longer than 90 seconds inside one frozen
job. Analyze local rhythm across the whole selection. Hosted listening remains
bounded to individual sections and retains the existing per-section response
schema and content-scoped caches. Store the section interpretations with their
source timestamps and provenance; retain all validated section evidence in the
aggregate. Do not invent a global meaning or silently truncate events to old
short-edit limits. No additional summary-model call is required.

Plan and select each section with its local musical detail, a compact overview
of the whole song, neighboring section context and preceding selected scenes.
Carry one visual arc forward and track already-used footage across sections.
Preserve the existing per-shot evidence, source bounds, cut choices and explained
abstention. The 32-shot response bound applies to each model request, not to the
complete video. Merge section proposals into one contiguous timeline using the
original source-track clock and exact final endpoint.

The existing average-duration envelope applies independently to each processing
section. Individual quick clusters and longer holds remain available within it;
this does not allocate a shared shot budget across sections with different
average densities. Explicit timeline editing remains the route for stronger
local timing control. Evaluating global density allocation requires a concrete
creative failure rather than another orchestration layer by default.

Existing edits retain their timing, written directions, locks and placed footage.
Fill and targeted work operate on bounded groups of requested empty positions;
they must not reset the rest of the song or apply section-local timestamps as
global times. Regeneration builds a private complete replacement and retains old
footage under the saved-clip capacity. A failure, cancellation or stale revision
cannot save a half-generated timeline. Reuse the durable worker's existing single
final commit; do not create child projects or independently committed section jobs.

A manually fixed individual shot longer than 90 seconds requires splitting
before bounded automatic selection. Untouched full-song generation establishes
its own bounded shots. Local starter guides use the wider timeline capacity for
long passages, so their cut proposals can reach the end of the selected song.

Progress identifies the section and current operation. Receipts and diagnostics
must distinguish sections and retain aggregate counts. Audio and selection stages
remain separate; the selector still reads sparse indexed evidence rather than
watching every source video. Longer support does not establish motion accuracy,
precise lyric alignment or artistic coherence.

Export uses the explicit export profile through the existing sequential renderer.
Keep source-duration and output-frame verification. Main search, film ingestion,
vector spaces and raw media are unchanged.

## Validation

Cover 600-second acceptance and rejection above the limit, nonzero passage
origins, section boundaries, all retained audio evidence, more than 100 placed
clips, bounded model requests, cross-section source reuse, fixed-timing fills,
locks, cancellation, atomic failure and saved-revision conflicts. Verify full-song
picker selection, cancellation, timeline navigation and explicit export mode.
Use deterministic provider fixtures to test full-length orchestration separately
from hosted-model musical quality; perform live service and media checks after
the focused tests pass.
