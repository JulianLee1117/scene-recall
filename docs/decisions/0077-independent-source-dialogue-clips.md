# ADR-0077: Independent source dialogue clips in music edits

- Status: Accepted
- Date: 2026-09-16
- Supersedes: ADR-0024's music-only audio restriction and ADR-0028's fade-in-only audio boundary; updates ADR-0036's render profiles
- Superseded by: [ADR-0078](0078-shared-voice-focus-audio-and-overlapping-dialogue.md) for source processing, overlapping dialogue, duck timings and render profiles

## Context

The user wants a romantic cross-film edit in which original spoken lines continue
over independently chosen pictures, with an audible music bed and an ending fade.
The previous renderer discarded all source audio, making that requested edit
impossible to preserve and revise within the app. This is a concrete manual audio
editing need, not authority for automatic quote selection or a generic effects system.

## Decision

Extend the existing version 1 project document compatibly with `dialogue_clips`
(default empty, at most 32). Each clip has a unique `id`, indexed `film_id`, optional
`unit_id` hint, editable `title` and `text`, `source_start`, `source_end`, and `start`
on the absolute song clock. Its duration is exactly the original source duration.
The clip carries `gain_db` (default 0), linear amplitude source fades
`fade_in_seconds` (.08) and `fade_out_seconds` (.12), and `music_duck_db` (-8).
Dialogue gain ranges from -60 to +24 dB so quiet original speech can be made
audible explicitly; music duck values range from -60 to 0 dB. Fade values range from 0 to 90 seconds and are
clamped independently to the source clip duration. Labels are user evidence, not
verified transcription or speaker identity. Dialogue clips require a song, must
fit its selected passage, and cannot overlap one another. They may span any number
of picture cuts or black preview gaps. Picture changes never retime dialogue.

Add `music_gain_db` (default 0, -60..0 dB) and `audio_fade_out_seconds` (default 0,
0..90 seconds, no longer than the passage). Existing entrance fade remains. Music
fades and gain apply only to the imported song. For each dialogue clip, music gain
ramps linearly in amplitude from 1 to `10^(music_duck_db/20)` during the .25 seconds
before its start, holds through its end, and returns to 1 during the next .5 seconds.
Intersecting attack/release tails use the minimum gain. Multiply that envelope by
the music gain and both passage fades. Dialogue gain and its own two fades are
independent. Positive dialogue gain is an editable amplitude boost, not automatic
loudness normalization; excessive gain can clip and must be checked by listening.
No speech isolation is implied; the source
track can include score, effects, background speech and noise.

Resolve audio only from the indexed original film path and its default audio stream
(first audio stream when no default exists). Validate film duration, on-disk source
identity using the canonical film hash, actual container duration and the presence
of audio before rendering. Record stream index and source fingerprint in the frozen
manifest and check that fingerprint before and after encoding. Preserve original
audio timing, including an initial audio-stream offset. Missing or un-decodable audio
fails explicitly; no silent substitute, transcription or generated voice is used.

Both music render paths use `decoded-reel-dialogue-mix-v5` and the audio component
`source-dialogue-linear-envelope-v1`. Pair audition uses
`next-scene-dialogue-global-frame-excerpt-v2`. Decode source dialogue at original
speed, mix independently of picture using 48 kHz stereo and AAC output, and apply
all envelopes on the full passage clock before trimming an excerpt. Preview and
export share the same mixer. A bounded filter script avoids Windows argument
limits and is removed with the existing render intermediates.

Dialogue and levels are ordinary durable project state with revision checks,
Undo and History. Existing saved source anchors remain editable when temporarily
unavailable; new or changed anchors must resolve. Replacing music explicitly clears
the old dialogue arrangement, preserving it in the previous revision. AI picture
generation must preserve all user audio fields. Private bounded generation scopes
omit dialogue while working, then merge picture results into the original document.
No new hosted calls, search vectors, database tables or raw-media mutation are added.

## Validation and limits

Synthetic real-media regressions verify source seek timing and delayed audio,
dialogue across picture cuts, music duck depth and exit fade, identical full
preview/export audio, and pair excerpts matching the full mix within AAC tolerance.
Other checks cover invalid bounds/overlaps/gains, absent audio, changed source
identity, durable revisions, song replacement/restore and AI preservation.

The browser follows the same envelope and song-clock convention; it remains a live
audition subject to browser decode and scheduling tolerances. The rendered output
is the deterministic export reference. Manual listening is required to confirm the
actual line boundaries, language, source balance and creative quality. Automatic
quote finding, diarization, word alignment, source separation, overlapping dialogue
tracks and Match Cuts integration remain separate decisions.
