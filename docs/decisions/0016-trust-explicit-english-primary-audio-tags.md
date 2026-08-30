# ADR-0016: Trust explicit English primary-audio tags

- Status: Accepted
- Date: 2026-08-30
- Supersedes: None
- Superseded by: None

## Context

Whisper chooses one language for a normal full-film transcription. Majority
voting over the first five VAD-filtered windows reduced single-window mistakes,
but Before Sunrise still selected German because its foreign-language opening
filled that detection sample. The container's primary audio stream was
explicitly tagged `eng`, and the resulting German-constrained pass would have
mis-transcribed the predominantly English film.

Blindly forcing English would break films such as Parasite, whose untagged
primary audio correctly detects as Korean. Treating every container language
tag as authoritative would also expand the policy beyond the observed failure
without evidence that release metadata for every supported language is
reliable.

## Decision

Probe the first audio stream, which is the stream faster-whisper decodes by
default, and retain its normalized tag only when it is exactly `en` or `eng`.
For either retained tag, pass `language="en"` to Whisper. For missing, empty,
`und`, and all other tags, preserve the existing automatic majority-vote
detection.

Record the exact resolved language option in the existing transcription
profile. When the English hint is active, also record its source and raw
normalized tag. This invalidates an older auto-detected cache only for a film
whose new effective options differ; compatible untagged caches remain current.
The raw film, audio, and metadata remain unchanged.

## Consequences

- Predominantly English films with foreign-language cold opens no longer depend
  on where voiced windows happen to fall.
- Untagged international films retain source-language auto-detection.
- The rule is deliberately narrow. Additional tag mappings require a measured
  failure and an explicit extension rather than an assumed ISO-code conversion.
- Changing the retained English tag or resolved language option independently
  invalidates the replaceable dialogue derivation and its downstream text
  features. Changes among discarded tags intentionally remain auto-detected
  and cache-compatible.
