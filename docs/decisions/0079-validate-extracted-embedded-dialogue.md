# ADR-0079: Validate extracted embedded dialogue before using it

- Status: Accepted
- Date: 2026-09-17
- Supersedes: ADR-0056's metadata-only embedded subtitle acceptance and cache reuse rules
- Superseded by: None

## Context

The downloaded Tár release marks an English text subtitle as default rather
than forced. Its extracted contents contain only 38 cues and 190 words across
a 158-minute film. Metadata selection alone would accept this partial track
as the entire dialogue transcript and skip the available English audio.

This is a concrete failure of the existing embedded selection boundary. The
shared deterministic SRT validator already identifies its insufficient
coverage and density; a new model or acquisition-time transcription is not
needed.

## Decision

Retain the existing metadata rules to choose one embedded English text stream.
Within ingestion, validate the extracted SRT against film duration using the
existing bounded structural, language, density and coverage checks. Accept it
as dialogue only when it passes automatic eligibility. Otherwise retain the
extracted timestamped evidence and use the existing local Whisper fallback,
including its primary-audio language hint and transcription profile. Do not
merge a partial subtitle track into the audio transcript or alter the film.

Record the content decision in a versioned, source-bound receipt alongside the
extracted SRT. Bind it to film identity, stream index, duration, validator
profile and full SRT hash. The dialogue manifest must describe the actual
source: accepted embedded text, or Whisper with the rejected embedded-source
dependency. Missing, malformed or changed receipts/evidence invalidate that
embedded decision; a changed fallback model/profile invalidates its transcript.
Unchanged evidence and configuration must reuse a completed fallback without
repeated extraction or transcription.

Scope the new policy to embedded sources. Preserve canonical external-sidecar
precedence, its existing content floor, unrelated Whisper caches, and the
global dialogue contract version. Older embedded caches are reconsidered on
their next explicit ingestion; no library-wide backfill or hosted request is
scheduled. These checks run in the ingestion worker, not the API or acquisition
monitor.

## Consequences

- Incorrectly labelled partial tracks cannot silently replace full dialogue.
- Sparse or unusual but valid tracks can fail the conservative eligibility
  rules and use audio transcription; originals remain available as evidence.
- English language and temporal coverage checks do not certify translation,
  exact synchronization or full-film transcription accuracy.
- A fallback retains truthful model/source lineage and remains independently
  rebuildable without changing film identity or unrelated derived profiles.
