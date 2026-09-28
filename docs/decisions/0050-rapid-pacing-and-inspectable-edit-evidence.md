# ADR-0050: Rapid pacing and inspectable edit evidence

- Status: Accepted
- Date: 2026-09-14
- Extends: ADR-0043 and ADR-0045
- Supersedes: ADR-0045's single-request path for Rapid selections below 90 seconds
- Superseded by: [ADR-0060](0060-music-led-whole-passage-timing.md) for Rapid's average-duration band, quota-driven text scopes and 288-position limit; the preset and evidence inspector remain active

## Context

The reviewed 116.61-second rumination edit has 148 shots, averaging 0.79 seconds.
Energetic targets 2–4 seconds and cannot reproduce that density. A larger response
would exceed the established 32-shot model bound. Users also need to inspect why
similar imagery recurs without cluttering the editor or generating a retrospective
explanation unsupported by the original selection.

## Decision

Add an explicit Rapid pace with a 0.65–1.2-second average band and 0.8-second target.
Individual brief holds remain available; musical evidence chooses cut positions.
Keep Balanced as the default and preserve existing timing when settings are applied.
Regenerate is the explicit action that rebuilds a completed edit.

Rapid uses the existing sequential bounded-generation path with text scopes targeting
32 × 0.8 = 25.6 seconds. Cap at nine scopes, so automatic generation remains within
288 positions of the 300-position timeline. Show the approximate density and this
limit in settings. Longer selections therefore cannot maintain the preferred Rapid
density indefinitely; choose a shorter passage for faster cutting. Other presets
keep the existing 90-second scopes. This is a bounded preset extension, not a new
global pacing allocator or additional model role.

Reuse current audio analysis by clipping/composing its timestamped evidence for
each generation scope. Preserve exact passage endpoints, output-frame boundaries,
the whole-song outline, one visual arc, neighbors and used-source context. Private
subrequests must not recursively repartition. Fixed Rapid layouts with more than
32 positions use bounded fill even on short passages; manual cuts remain fixed.
Failures and cancellation retain the previous project revision.

Local starter timing may use individual measured pulses for Rapid. Traverse resumed
landmarks after a beat-grid gap, without interpolating nonexistent beats. If proposals
exceed capacity, keep measured landmarks distributed throughout the passage instead
of spending the allowance near its start. These are editable heuristic cuts, not
verified phrases or action-completion timing.

Expose an optional Why this shot inspector with current neighbors, recorded intention,
selection notes and separate indexed evidence. Do not invent missing transition
reasons or call a model to rationalize a previous choice. Film titles reuse canonical
metadata; thumbnails remain labeled indexed samples. Exact unit/time reuse guards
remain in force but do not establish perceptual diversity across different units.

## Validation

Cover short and ten-minute Rapid scopes, nonzero origins and subframe endpoints,
reused audio evidence, shared context, request limits, fixed fills and one revision.
Verify starter capacity across the whole passage and resumption after missing beats.
Check explicit settings application, film titles across cuts/seeks, optional evidence
inspection and zoomed playback following in the browser. Provider fixtures establish
orchestration behavior; artistic quality still requires watching the generated edit.
