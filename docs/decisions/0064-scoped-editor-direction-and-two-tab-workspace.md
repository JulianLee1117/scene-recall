# ADR-0064: Scoped editor direction in one two-tab music workspace

- Status: Accepted
- Date: 2026-09-14
- Extends: ADR-0032, ADR-0042, ADR-0051 and ADR-0060
- Supersedes: ADR-0032's shared creative/listening input and separate user visual-plan authority when a canonical editor direction exists; ADR-0042's settings-dialog generation entry; ADR-0039/0041/0060's first-fill starter retiming path
- Superseded by: None

## Context

The music editor mixed generation settings, song interpretation, source search
and timeline manipulation in the same working surface. Creative direction was
spread across a brief, user visual arc/motifs and per-shot searches. The user
needs one place to describe an edit, optionally direct a particular part of the
song, and explicitly generate it, alongside an ordinary editor for reviewing
and changing the result. Adding another editor version or exposing every model
stage would make that separation harder to use.

The planned experiment that discovers footage before choosing exact assembly
remains unevaluated. This decision supplies a usable direction boundary for the
current planner without activating that experiment or changing search ranking.

## Decision

Keep one project, revision stream and Undo history at `/lab/music-sketch`.
Expose **AI direction** and **Edit** as views of the same document. New work
opens AI direction; saved work with an arrangement opens Edit unless the URL
explicitly requests `view=ai` or `view=edit`. View changes are transient and do
not save, generate, move clips or dirty the project. Shared song selection,
analysis, status/details, Save, History, Undo and Export remain outside the tabs.

Persist optional `editor_direction` in the existing project JSON document:

- `instruction`: up to 24,000 characters of global creative direction.
- `ranges`: at most 32 entries containing a unique `id`, finite `start`/`end`
  source-track seconds and up to 2,000 characters of `instruction` each. Ranges
  must have positive duration, remain inside the imported track and not overlap.

This object is user input, never generated footage or heard musical evidence.
An absent/null value falls back to the legacy brief and user-authored visual
arc/motifs. A present empty object is authoritative: it clears that older
direction rather than resurrecting hidden instructions. Generated visual plans
remain derived proposals subordinate to current user direction. New edits write
the canonical object; legacy fields remain readable without a destructive
migration or another persistence table.

`editorial_context` projects this input through the versioned
`scoped-editor-direction-v1` contract. It includes global direction and only
nonblank ranges overlapping the request scope, clipped in the request without
altering saved ranges. A local instruction is more specific than global
direction inside its span, including when lyric treatment ignores lyrics.
Its edges are soft creative context, not mandatory cuts, and a shot may cross
them. Timing, direction planning, selection and optional footage review receive
the relevant context through existing request paths and cache identities.
Changing the selected passage preserves ranges outside it. Explicit track
import clears time ranges while retaining global instruction. A full-document
Save treats its validated snapshot as authoritative, preserving the supplied
song and ranges even when Undo changed the song. Restoring a saved revision
likewise restores that revision's original song and ranges together.

New listening uses `listening_evidence` and `music-listening-input-v1` to
whitelist actual music context: track/passage, beat/downbeat estimates, passage
RMS and separately identified supplied song notes/lyric meaning. Exclude editor
direction, legacy brief, visual plans, pace/lyric-treatment preferences, manual
cut markers, per-slot measurements, feedback and previous generated output.
Those editing inputs cannot change the new listening request or its cache key.
The listener still returns the existing bounded observation/interpretation
schema; it does not acquire a new transcription or rhythm model. Existing valid
saved listening evidence remains reusable with its original provenance.

AI direction contains the global instruction, optional movable/resizable ranges
on the music waveform, pace, lyric treatment, film scope and optional supplied
song meaning. Writing, dragging and changing preferences only edit the shared
document. **Generate edit / Regenerate edit** explicitly saves and invokes the
existing whole-edit `generate.mode=regenerate` job, with placed locks blocking
the rebuild. Both actions choose fresh whole-passage timing and retain the
bounded source-fitting step during selection. A successfully applied result
can open Edit; failed, cancelled,
conflicted or unapplied work cannot impersonate a new accepted arrangement.

Edit retains preview, scene library, source review and timeline tools.
**Fill gaps** uses `generate.mode=fill`, retaining every existing cut and placed
scene, including untouched starter placeholders. Remove the legacy first-fill
shortcut that replaced starter timing or allowed selection to move its cuts.
Starter metadata remains readable but cannot authorize that behavior. Per-shot
Find scenes remains candidate-only and placement
is explicit. Delete/Backspace removes the explicitly selected unlocked scene
into an empty placeholder; a selected cut retains precedence and joins its
neighbors. Text input, dialogs/popovers, repeated keys and active job locks do
not trigger timeline deletion.

Both views stay mounted to preserve editing context. The inactive view is
hidden/inert and cannot play media, process editing shortcuts or continue a
drag. Zero-width hidden layout notifications must not destroy timeline zoom,
waveform resolution or horizontal scroll. One active player owns Space.
Consecutive edits from one focused field or drag form one Undo step;
`endChange`, Save, jobs, navigation and accepted document/track/revision changes
close that group. The canonical document and history references advance
synchronously so Save or generation in the same event receives the latest input.

## Consequences and limits

- Creative direction becomes one inspectable, versioned input without a new
  service, model stage, project type or database migration.
- Editing direction does not change an already placed sequence. Whole
  regeneration remains explicit and atomic; partial range regeneration is not
  implemented by these range controls.
- Existing legal source windows, fixed-cut protection, cancellation, previous
  footage exclusions and revision checks remain authoritative. Prompted visual
  style, movement or era is not a verified filter or a guarantee of footage fit.
- The discovery-before-assembly comparison, finer rhythmic guides, sequence
  diversity, broader inspection and Match Cuts integration retain their
  separate evidence and activation gates.
- Mechanical verification must cover context precedence, request/cache
  separation, range scope, Save/Undo and hidden-view interactions. Actual
  editing usability and creative preference still require played review.
