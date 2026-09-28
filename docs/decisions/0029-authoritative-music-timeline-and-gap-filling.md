# ADR-0029: Authoritative music slots and contextual gap filling

- Status: Accepted
- Date: 2026-09-11
- Supersedes: [ADR-0024](0024-source-backed-lab-and-music-sketch.md) for music timing and regeneration behavior only
- Superseded by: [ADR-0030](0030-song-specific-moments-and-clip-directions.md) for initial cuts, direction ownership and explicit timing regeneration only

## Context

The user could adjust timing markers without seeing a corresponding edit:
the first planner treated a marker as a suggestion near an energy-dependent
target, rather than an authoritative cut. Audio sections, search prompts and
selected clips occupied separate screens without a shared timeline. A draft
discarded unlocked choices and its alternative candidates. A missing clip
could not retain its position through preview.

These are concrete editing and planning failures. They do not require a new
retrieval engine, an always-on search interpreter or a general video editor.

## Decision

Add an optional `music_timeline` to schema-version-1 project documents. An old
document defaults to null and is migrated only when the editor saves timing
or a music job creates it. Preserve existing source selections. Each timeline
records its track and passage, and at most 100 stable, contiguous slots in
absolute source-track seconds. A slot contains start/end, its musical section
index, an optional selected clip ID, an editorial reason, a search error and
at most six alternatives. Each alternative contains a legal `ClipSelection`,
film title and optional reason. Existing `clips` remain the source selection
bin; playback uses slot references, not the bin's array order.

Validate complete ordered passage coverage, at least one output frame per
slot, unique slot and selected clip IDs, matching track/passage, section
references and selected clip duration. Manual timelines may exist before
analysis. Preserve unplaced saved clips independently of what is rendered.
Source identity and legal source ranges remain independently validated.

Analysis produces rhythm, at most eight musical sections and initial cut
slots before searching. Each section has one editable query and a supported
text facet: `all`, `scene`, `words`, `look` or `mood`. Old sections default to
`all`. Text never claims to perform reference-only Framing or temporal matching.
The interpretation schema/prompt advances to its faceted v3 contract while old
caches remain on disk. Explicit reanalysis requests the current brief/provider
inputs and replaces section intentions, including user overrides; identical
inputs may reuse the interpretation cache. Preserve existing slot timing and
placements. Drafting requires a current same-track/passage interpretation and
never implicitly listens. New detected beats do not silently move existing cuts.
Initial user-authored markers are exact boundaries; otherwise local rhythm
and energy produce editable timing suggestions.

The default draft action fills only empty slots. Optional `slot_ids` is
accepted for draft jobs only, validated before enqueue and frozen with the
revision snapshot. It requests replacement of precisely those unlocked slots.
Keep current choices until a valid replacement succeeds. No fitting candidate
leaves the original or gap in place with an explanation; it does not discard
other successful slots or invent a source.

Retrieve at most eight section query/facet pairs with at most 48 results each.
Reuse the existing typed recipe adapters; the Lab-only caller can retain
near-identical visual alternatives while normal junk suppression stays active.
Ordinary search behavior is unchanged. Each slot offers at most 24 legal
sources to one bounded planner request. Its context includes the whole passage,
all section intentions, exact slots, current selections before and after each
target, detected beats/downbeats, intensity and creative brief. Preserve the
user's cuts. Ground every selected ID and trim before applying it, and save
six legal alternatives per successful slot for explicit user switching.
The text planner still sees sparse caption evidence, not candidate video;
motion and visual continuity remain human judgments.

Generated revisions preserve locked clip content and source ranges as before,
and also preserve their absolute timeline positions. Existing cancellation,
revision-conflict and single-attempt hosted-provider guards remain unchanged.

For timeline projects, the `decoded-reel-timeline-gaps-v3` manifest orders
picture by slots and quantizes cumulative boundaries to 24 fps. A preview
renders an empty slot as black for its full duration while the music continues.
Export rejects gaps. Selected source ranges, crop, passage fade and source
hash preservation retain their earlier contracts. Legacy sequential rendering
is unchanged. No speed adjustment, compositing or generated imagery is added.

## Consequences

- A cut is visible and editable before retrieval; generated clips cannot move
  it merely because a different source was selected.
- Filling, replacing and switching saved suggestions are separate operations.
  One weak section can remain a gap while useful choices elsewhere survive.
- Preview preserves the temporal meaning of unfinished edits. Unplaced saved
  sources do not block rendering an otherwise valid timeline.
- Focused tests cover timing validation, exact user markers, replacement
  scope, neighbor/rhythm context, source-grounded alternatives, lock positions,
  old-document compatibility and real decoded red/black/green gap playback.
  No hosted request or editorial-quality claim is part of this validation.
