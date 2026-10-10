# ADR-0117: Matched words on hover and source dialogue in shot details

- Status: Accepted; evidence display and saved anchors amended by ADR-0118
- Date: 2026-10-09
- Extends: ADR-0094 (source-backed search presentation)

## Context

- **Original:** hover chose evidence from competing retrieval channels, so
  categories changed from card to card and were hard to scan. The title had
  also lost its preferred gold color. Dialogue was buried in ranking details.
- **First iteration:** restore the gold title, show a short shot description,
  and reserve spoken/on-screen excerpts for explicit Words searches. This was
  quieter, but hid why ordinary searches returned dialogue matches.
- **Now:** keep the same minimal layout, allow useful matched words from either
  search path, and fall back to the shot description. Details add source
  dialogue with highlighted matches, expansion and playable timestamps.

This small UX improvement required human taste and feedback on successive
implementations. The minimal final form emerged through use and iteration;
the first pass did not resolve the tradeoff between consistency and explanation.

## Decision

Keep one snippet beneath the film title and time. Prefer the explicit Words
clause's own dialogue/on-screen evidence, then a strong main-query quote or its
selected semantic dialogue/on-screen evidence, else the shot description. Use
the existing strong-quote threshold (0.8 ordered overlap); this is a display
rule, not a calibrated probability or a ranking change. Do not infer user intent
or select a snippet by comparing ranks across unrelated channels.

Preserve each clause's text, source, quote score and timing together before
fusion. A timed snippet and initial playback use that same passage. Indexed
frame identity continues to govern visual references and saved source anchors.

Expose bounded shot dialogue as an on-demand compiled-evidence read. Resolve
the published shot and overlapping cues in one pinned snapshot, preserving
full cue times across cuts. Show a few lines with the match emphasized,
expand longer dialogue and play timestamps. Keep on-screen text distinct.
Facts stay tied to the retrieved result as playback advances.

## Consequences

No new producer, model, index or backfill is required. Search result payloads
gain optional evidence provenance; full dialogue is fetched only in details.
Missing dialogue does not assert silence. No continuously scrolling transcript
or generated explanation is introduced. The current contract specifies the
endpoint and bounded response.
