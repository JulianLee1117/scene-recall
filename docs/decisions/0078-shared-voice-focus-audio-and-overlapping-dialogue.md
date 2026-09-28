# ADR-0078: Shared voice-focused audio and overlapping dialogue

- Status: Accepted
- Date: 2026-09-17
- Supersedes: ADR-0077's non-overlap rule, fixed duck timings, direct source-audio playback and render profiles
- Extends: ADR-0068's regenerable local audio cleanup

## Context

The user rejected the first dialogue edit: music masked speech, original scene
backgrounds competed with the piano, and abrupt fades made separate quotations
sound disconnected. Browser audition also decoded original film audio independently
of export, so adding only browser filters would make the two sound different.
Measured source channels matter: the local Marriage Story copy is 5.1; Her, Before
Sunrise and Eternal Sunshine copies are stereo. Stereo copies have no separate
center channel that can honestly be recovered without source separation.

## Decision

Add optional `source_audio_mode: original | voice_focus`, default `original`, to
each existing source dialogue clip. Both modes resolve to one shared, bounded
source-window asset. `voice_focus` selects the actual front-center channel only
when the default audio stream explicitly declares a supported 5.1 or 7.1 layout.
It duplicates that channel into stereo and applies a gentle two-pole high-pass
at 80 Hz and low-pass at 9 kHz. Stereo, mono or undeclared layouts retain their
ordinary mix and receive only those filters. `original` retains the ordinary
stereo mix without filtering. No stereo cancellation, denoising model, source
separation, automatic gain normalization or invented isolated-dialogue claim is
introduced. Source score, room tone or noise may remain, including in a center
channel; a different source/range may sound better than stronger processing.

Version these local derivatives as `source-window-pcm48-center-band-v1`. Resolve
only indexed film IDs and validate actual media duration, canonical source hash,
selected audio stream and source file fingerprint. A request is at most ten
minutes. Preserve source timing, including an audio stream's initial offset, and
render 48 kHz stereo float PCM WAV with its requested source start at asset time
zero. Gain and fades remain independent editable mix operations outside this
asset; changing them does not re-decode the source.

`GET /lab/dialogue-audio` accepts `film_id`, `source_start`, `source_end` and mode.
It prepares a missing asset under a bounded per-request lock and redirects to
`/lab/dialogue-audio/assets/<sha256>.wav`. The canonical resolver is not HTTP-cached;
the content-addressed byte-range representation has immutable bytes and a stable
SHA ETag. Readers verify the file hash. Source fingerprint, range, mode and profile
identify lookup receipts; changes cannot reuse an unrelated source window. Source
or decoding failures are explicit. A busy preparation returns 503 with retry advice,
not silence or a different source. Original films remain untouched.

Browser sequence playback and solo audition both use this zero-based PCM, as does
the FFmpeg renderer. Render manifests record processing profile, method and input
identity; a private `dialogue-assets.json` records actual content IDs. Preview and
export now use `decoded-reel-shared-voice-mix-v6` with audio component
`source-dialogue-shared-pcm-envelope-v2`; pair audition uses
`next-scene-shared-voice-global-frame-excerpt-v3`. Whole-passage mixing still occurs
before excerpt trimming. Old rendered outputs remain intact.

Allow independent dialogue clips to overlap, retaining the 32-clip limit, unique
IDs and source/passage bounds. Their individual gains and linear fades mix together;
this supports room-tone handoffs and voice bridges without moving source words.
It does not prevent the user from deliberately overlapping words or overloading
the mix. Add `duck_attack_seconds` and `duck_release_seconds`, each finite from
0 to 5 seconds. Omission preserves old .25/.5-second timings; newly added UI clips
explicitly choose .6/1.2 seconds. Zero requests an instant boundary. Overlapping
duck envelopes use their minimum amplitude, not multiplied attenuation. Saved
revisions and queued snapshots acquire these backward-compatible defaults without
being mistaken for generated audio edits.

## Storage and validation

PCM lives under `assets_dir/lab/dialogue-audio/<profile>/audio/`, with small lookup
receipts and per-key locks in `requests/`. Readers refresh access age only after
verification. The existing idle collector can remove only exact content-addressed
WAV names older than 24 hours, with no queued/running editor work and under the
same per-asset lock; it rechecks age after acquiring the guard. A missing PCM is
regenerated from its source. Receipts/locks remain small local derivation metadata.
Project deletion does not delete shared voice assets or original films. Temporary
encode files are removed in `finally`; no general cache or evidence purge is added.

Real FFmpeg tests distinguish a center speech tone from four competing surround
tones, verify explicit stereo fallback, browser HTTP range bytes, cache reuse and
source-change invalidation, idle collection/rebuild, and processed browser PCM
matching preview/export within AAC tolerance. Overlap tests verify two audible
voices and minimum duck gain. Existing seek-offset, full/pair timeline and revision
checks remain. These establish mechanics; intelligible, emotionally connected
editing still requires listening to the actual mix rather than accepting model
praise or loudness measurements as a creative verdict.
