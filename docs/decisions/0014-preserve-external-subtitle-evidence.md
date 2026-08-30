# ADR-0014: Preserve external subtitle evidence

- Status: Accepted
- Date: 2026-08-29
- Supersedes: None
- Superseded by: None

## Context

Reviewed torrent releases can contain an English SRT beside a movie that has
no convertible embedded subtitle stream. Film intake previously moved only the
movie, leaving that higher-quality timestamped dialogue behind and forcing the
ingest to transcribe audio with Whisper. Dialogue caching also depended only on
the existence of `dialogue.json`, so adding better raw evidence later could not
invalidate stale text.

## Decision

When a reviewed release has one usable English-marked SRT with minimally useful
dialogue whose filename is positively associated with the selected feature,
copy it beside the canonical film as `<film-stem>.en.srt` before queuing
ingestion. Retain the release copy in `incoming`; do not ingest subtitle files
associated with extras, choose among ambiguous or non-English candidates, or
promote malformed, oversized, trivial, or promo-only files.

Dialogue resolution applies the same non-destructive minimum-content check to
an already-canonical sidecar, then prefers a usable sidecar, a convertible
embedded subtitle stream, and the configured local Whisper model in that
order. The check is a conservative content floor, not a language or
completeness classifier. Store a contract-versioned dialogue manifest
containing the sidecar content hash, embedded stream identity, or film identity
and Whisper model. For Whisper, store a separately versioned transcription
profile containing the faster-whisper package version and the exact intentional
options. The current profile enables VAD, transcribes the detected source
language, majority-votes across up to five voiced 30-second language-detection
windows, and disables previous-window text conditioning. Reuse `dialogue.json`
only when that manifest still matches.

Filter known promotional cues only from the parsed sidecar dialogue derivation,
not from the raw SRT. Record that rule in a sidecar derivation profile so an
older cache is rebuilt without invalidating embedded or Whisper-derived text.

The sidecar is durable raw timestamped evidence. Parsed dialogue remains a
replaceable derivation and downstream annotation/search data remains
independently rebuildable.

## Consequences

- Available release subtitles avoid slower and less exact audio transcription.
- Changing a sidecar or Whisper model invalidates dialogue on the next ingest.
- Changing the Whisper engine version or transcription profile invalidates only
  Whisper-derived dialogue rather than silently mixing decoding behavior.
- A rejected sidecar remains untouched as raw evidence while dialogue falls
  through to embedded text or Whisper.
- Promotional cues do not enter search even when an otherwise useful sidecar
  remains the selected evidence source.
- Film content identity remains based on the immutable movie file.
- Automatic intake deliberately declines ambiguous multi-subtitle releases;
  an explicit language-selection workflow can be added only when needed.
