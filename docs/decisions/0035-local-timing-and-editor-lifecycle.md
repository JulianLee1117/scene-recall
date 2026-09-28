# ADR-0035: Local timing preparation and an explicit editing lifecycle

- Status: Accepted
- Date: 2026-09-11
- Extends: ADR-0025, ADR-0030 and ADR-0034
- Superseded by: [ADR-0051](0051-shared-lab-navigation-and-unsaved-drafts.md) for the separate workspace Exit control only
- Superseded by: [ADR-0068](0068-lab-artifact-lifecycle.md) for records-only deletion; deletion now also cleans owned job artifacts with durable retry

## Context

An inspected 10cc passage retained twelve near-uniform 3.6-second slots from
an older energy-bucket planner. Later interpretation improvements correctly
preserved those saved cuts, but the editor offered no clear local preparation
step before creative generation. Beat guides were hidden, scene browsing opened
a separate modal, and abandoned experiments accumulated without project deletion.

## Decision

Add a durable `rhythm` job using the existing prepared Beat This! profile,
source-scoped PCM derivative and measured RMS. It performs no hosted request
or scene retrieval. Applying a new music passage queues this local preparation;
the advanced action can also run it explicitly. The job returns beat guides
and initial empty placeholders, with one revision/Undo boundary. It preserves
existing timing unless `replan_timing` is explicitly requested. Rebuilding
rejects locked placements, retains source clips in the bin and carries user
directions by overlap through the existing timeline adapter.

`measured-bar-amplitude-placeholders-v1` groups measured bar landmarks within
pacing bounds and favors relative-amplitude changes. Beat-only output is labeled
as pulse groups. Store source landmark, quantized cut, basis, pacing and scope
under `rhythm.timing_suggestions`; these are editable heuristics, not detected
phrases, verified accents or motion-aware cut optimization. Missing beats remain
unavailable; keep one empty passage rather than invent a beat grid. The rhythm
derivation contract advances to keep detector beats separate from user markers.
All detected beats are visible by default, independently of numbered cut handles.

The right-hand Library presents direct searches and the selected slot's saved
suggestions. Preview/trim precedes explicit placement; drag-and-drop validates
the destination duration and lock state. Shorter drops retain the matched instant
when it lies inside the offered window. Changing a source window clears obsolete
match evidence. Manual candidate-only `draft` requests may run before listening
when the selected slot has its own valid search direction, without fabricating
an audio interpretation. Automatic scene selection still requires interpretation.
Compact playback controls preserve Space and fixed-speed source playback.

Add `DELETE /lab/projects/{id}?base_revision=N`. Within one transaction, reject
revision conflicts or queued/running work, then remove the project, revisions
and its job records. Preserve original tracks, film evidence and unrelated
ingestion jobs. The UI confirms the named deletion and provides Save, clean Exit,
and Save/Discard/Cancel for unsaved exits. A save racing a new local edit must
not exit. Clean exits leave durable jobs running.

## Verification and limits

The inspected 14.43–58.11s 10cc passage yields 78 beat guides and six placeholders
of approximately 7.58, 5.38, 7.13, 7.17, 8.88 and 7.55 seconds in about two seconds
of local timing work. This demonstrates removal of the fixed-bucket failure,
not musical acceptance. The tracker changes apparent pulse in this passage;
guides and proposed cuts require editorial judgment. Existing source/lock,
revision, cancellation and no-hosted-call tests cover the new boundary.

The selector continues to use captions, scalar annotations and retrieval evidence,
not candidate video. Exact action endings, visual movement continuity and lyric
utterance alignment remain unverified. No new retrieval representation, video
model, automatic retiming or full-song orchestration is introduced.
