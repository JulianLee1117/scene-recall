# ADR-0032: Shared musical evidence and editable planning

- Status: Accepted
- Date: 2026-09-11
- Supersedes: ADR-0030 for listening evidence; ADR-0031 for planning context and sequence intent
- Superseded by: [ADR-0033](0033-search-aware-editor-and-atomic-generation.md) for the default generation workflow
- Superseded by: [ADR-0060](0060-music-led-whole-passage-timing.md) for separate whole-passage timing and reuse of current listening across pace-only regeneration
- Partially superseded by: [ADR-0064](0064-scoped-editor-direction-and-two-tab-workspace.md) for canonical user direction and independent listening inputs

## Context

The editor had detailed pulse and amplitude measurements but coarse musical
meaning. Prompt planning omitted amplitude entirely, while scene selection
received a long raw array. Neither stage had aligned lyrical context or a
persistent visual arc. Repeated generic directions needed explicit editorial
preferences. Sparse captions also described source windows imperfectly, making
actual footage review necessary.

## Decision

Use one versioned `music_evidence` packet for listening, prompt planning and
scene selection. Admit derived beat/downbeat/RMS data only when track, passage
and waveform bounds match. Summarize relative normalized amplitude in at most
24 windows and per existing slot. Amplitude is not emotion; pulse is not an
accent detector. Manual markers have their own track/passage scope.

New listening results include at most 32 sparse audio observations, separate
from contiguous creative edit moments. Each observation has source-track
start/end, label, kind and model-reported confidence. Validate bounds and unique
IDs. Retain explicit `ai-observed` provenance; confidence is neither calibrated
nor a guarantee that an event exists. Old interpretations remain readable.
Listening excludes previous interpretations to prevent self-conditioning.

Add durable patient/balanced/kinetic pacing preferences and
ignore/literal/metaphorical/counterpoint lyric treatment. These guide creative
decisions without overriding fixed timing. Track-scoped song context contains
notes and up to 80 supplied lyric/paraphrase lines, with source-track spans and
meanings. Only overlapping lines condition a passage. This is user context,
not automatic transcription or forced alignment.

Keep a compact visual plan (arc and motifs), with user/AI ownership. Direction
planning may propose one alongside the exact requested directions; a user-owned
plan remains authoritative. Optional per-slot feedback requests less literal
imagery, more contrast, different energy or more readable action. Preferences,
context and feedback participate in relevant cache identities and model inputs.
Default targets continue protecting user directions and locks.

Expose a compact Planner settings dialog with one Apply/Cancel boundary and
Undo. Lyric spans can start at the playhead or use a selected clip's interval;
numeric controls are secondary. A cue lane distinguishes approximate AI
observations from supplied lyrics, separately from cut markers and the optional
beat grid. Source-window review uses actual video seeking and bounded playback
at the proposed trim, not indexed thumbnails presented as exact source frames.
Trim changes require Apply and retain duration, source bounds and lock protection.

Track replacement clears song context and sequence plan, preserving the prior
revision and preferences. Passage changes retain track-scoped lyrics and user
sequence intent while invalidating derived passage evidence. Keep existing
single-attempt jobs, cancellation, revision conflicts, caches and Undo. Ordinary
search and vector spaces do not change.

## Limits and evaluation

Listening still generates creative moments in the same request, so its cache
remains coupled to brief and planning context. Shared measured evidence is
independently reusable; fully separating listening and creative interpretation
would require a separately justified stage.

No full-song orchestration, automatic transcription, stem separation, new
structural model, automatic footage analysis or automatic retiming is activated.
The text planner cannot verify gesture completion or motion matches. Exact
footage audition makes that boundary reviewable. Real-song acceptance must
compare useful clips kept, manual trim changes, incorrect musical/lyrical claims
and editing time; mechanical tests do not establish creative quality.
