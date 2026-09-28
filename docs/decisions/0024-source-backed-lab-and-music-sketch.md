# ADR-0024: Source-backed Lab projects and Music Sketch

- Status: Accepted
- Date: 2026-09-11
- Supersedes: None
- Superseded by: [ADR-0028](0028-explicit-music-audio-provider-and-progress.md) for the default music provider and audio fade contract only
- Superseded by: [ADR-0029](0029-authoritative-music-timeline-and-gap-filling.md) for music timing and regeneration behavior only
- Superseded by: [ADR-0077](0077-independent-source-dialogue-clips.md) for the music-only audio restriction

## Context

Discovery is useful when it produces an editable sequence, not only a ranked
grid. Music Sketch is the first bounded experiment: hear a selected passage,
retrieve film moments that follow its feeling, and let the user reshape the
result. Sparse scene annotations cannot substantiate exact movement matching.

## Decision

Keep search and experiments in one application. A small explicit Lab registry
selects isolated experiment screens over shared project, source-selection,
job and render services. Avoid a generic plugin framework or duplicate search
engines. The initial routes are Music Sketch and manual Visual Rhymes.

Persist schema-versioned projects, append-only revisions, original hashed
audio, and source film/time anchors under `state_dir/lab`. Unit IDs are hints,
not durable identity. Derived audio, rhythm, interpretations and renders live
under `assets_dir/lab` and carry their own profile and input lineage.

Music Sketch handles selected passages up to 90 seconds. A pinned local Beat
This! profile supplies editable timing suggestions; waveform and RMS intensity
are local. Separately configured Gemini listens to only that passage and emits
at most eight emotional/search segments. Cache by content, range, brief, model,
schema, prompt and settings. One subsequent planner chooses only IDs and legal
trims from bounded search results. No always-on LLM layer enters ordinary search.
Editing retrieval can retain near-matching alternatives while keeping junk
filtering. This is a caller-specific selection policy, not a new vector space.

Drafts preserve locked selections and durations before them. Revision checks
prevent late jobs overwriting saved edits. Every proposed source range is
validated independently of model output. No automatic hosted retries.

A frozen source-backed render manifest drives preview and MP4. The first
renderer supports original-speed cuts, trims and whole-picture uniform crops,
24 fps, landscape/portrait, H.264 video, and music-only AAC audio. No speed
ramps, compositing, generated imagery, or film dialogue mixing.

## Consequences

- Saved work survives index repair, model replacement and API restart.
- Human scene selection, timing and crop edits are available independently of
  the hosted interpreter; candidate-motion understanding remains unproven.
- Three real music passages and a varied creator-reference study must establish
  editorial usefulness before expanding scope or automatic orchestration.
- Manual Visual Rhymes is an audition workflow, not activation of ADR-0008's
  still-matching, exact-frame or motion profiles.
