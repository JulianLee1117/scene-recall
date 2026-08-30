# ADR-0015: Fail closed on degenerate Whisper output

- Status: Accepted
- Date: 2026-08-30
- Supersedes: None
- Superseded by: None

## Context

Local speech transcription is an optional fallback when authoritative subtitle
text is unavailable. Silence, music, or incorrect language detection can make
Whisper emit structurally repeated hallucinations. Those rows previously
entered dialogue and semantic indexes as if they were source evidence. A scan
of existing caches found a clear failure cluster with exact runs of 16–46
identical segments; accepted transcripts had no run longer than six.

Rejecting a whole film would also discard useful visual and hosted-caption
search. Language-specific heuristics, phrase deny lists, and general transcript
quality scores would be brittle for multilingual, sparse, musical, or
intentionally repetitive films.

## Decision

Apply a narrow structural gate only to Whisper-derived segments, after local
transcription and before dialogue is saved or indexed. Normalize Unicode width,
case, punctuation, and whitespace, then reject output when either:

- one exact line of at least three tokens and eight letters appears in 16
  consecutive segments; or
- at least 20 segments last 25 seconds or longer, contain no more than eight
  tokens, and repeat text that occurs at least five times globally.

On rejection, log the reason, discard the replaceable transcript rows, and
continue ingestion with empty dialogue so visual and caption retrieval remain
available. Preserve the source film and audio unchanged. Save the ordinary
Whisper source manifest with the empty result so the same deterministic profile
does not retry forever.

Record the gate version and exact thresholds in the Whisper transcription
profile. A changed model, engine, transcription setting, gate, or threshold
therefore invalidates the cached result and deliberately retries the derivation.
External and embedded subtitle text bypasses this heuristic.

## Consequences

- Gross silence-loop hallucinations cannot poison Words or general semantic
  search.
- A film remains useful through visual and hosted-caption evidence when its
  optional speech transcript is rejected.
- An extreme real chant could theoretically cross the conservative exact-run
  threshold; authoritative subtitle sources are never subject to this gate.
- This gate detects structural degeneration, not transcription accuracy or
  completeness. Those remain evaluation concerns.
