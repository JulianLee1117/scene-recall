# ADR-0022: Require explicit review for uncertain external subtitles

- Status: Accepted
- Date: 2026-08-31
- Supersedes: None
- Superseded by: None

## Context

[ADR-0014](0014-preserve-external-subtitle-evidence.md) safely preserves one
usable English-marked SRT when its filename clearly associates it with the
selected film. This decision extends that boundary for a reviewed release that
instead contains a valid English subtitle with no language marker, or several
otherwise eligible files that automatic intake must not guess between.
Silently ignoring those files forces a slower and less exact Whisper fallback
even though the person importing the film can identify the English track.

A content-based language classifier would add another model and still could not
reliably distinguish translations, commentary, forced tracks, or similarly
named variants. The selection must also not let a browser supply an arbitrary
filesystem path.

## Decision

Keep ADR-0014's narrow automatic selection unchanged. If no sidecar is selected
automatically but one or more usable, feature-associated candidates remain
after the established hard exclusions, expose those candidates conditionally
in **Review & add**. Show only a bounded, cleaned dialogue excerpt for local
review; it is transient interface data and is never persisted as evidence.

Require an explicit import decision to use exactly one candidate as English or
to skip subtitles. Omission fails before any source mutation. At import time,
the backend recomputes the eligible candidates and accepts a selected
root-relative handle only when it exactly matches that set; a stale or arbitrary
path fails closed. Multiple candidates remain distinct choices and are never
merged or selected by hidden heuristics.

Selecting a candidate copies it byte-for-byte beside the canonical film as
`<film-stem>.en.srt` before the film is moved. Skipping preserves every subtitle
only in the incoming release. In both cases the release copy remains untouched
as raw evidence. Forced, commentary, extra-associated, known foreign-marked,
unassociated, malformed, oversized, trivial, and promo-only files are not
offered for review.

## Consequences

- Valid unmarked or otherwise ambiguous English subtitles can replace an
  avoidable Whisper fallback through one explicit review step.
- Automatic intake remains deterministic and conservative for the common case.
- No language model, inferred language label, or new durable evidence type is
  introduced.
- Import cannot silently choose among candidates or escape the reviewed
  release through a browser-supplied path.
- Skipping is deliberate and leaves the raw release available for later review.
