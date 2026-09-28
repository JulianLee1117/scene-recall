# ADR-0036: Bounded next-scene proposals and musical pair audition

- Status: Accepted
- Date: 2026-09-12
- Extends: ADR-0025, ADR-0029, ADR-0033 and ADR-0034
- Superseded by: [ADR-0077](0077-independent-source-dialogue-clips.md) for the render profiles and independent dialogue mix in pair excerpts

## Context

Caption-based selection can miss the useful moment inside a retrieved shot.
Exact-duration filtering also removes promising sources before selection when
they are shorter than an initial placeholder. Isolated source review does not
let the user hear whether an A-to-B transition works with its music before
placing it. The user requests a useful, polished experiment without a mandatory
human-labeling program, fine-tuning or reinforcement-learning infrastructure.

## Decision

Add a Lab-only `next-scene` job over an anchor slot and the immediately following
slot. The existing AI Music Video workspace offers an optional direction,
up to three played alternatives, adjustment and explicit Use scene. Search,
audition and preview jobs do not commit project changes. Application writes one
normal, undoable project revision after rechecking source authority, locks,
the frozen revision and the exact previewed choice.

Reuse existing source identities, capability-aware recipes, musical evidence,
project documents, worker and renderer. Bound search planning to three recipes,
48 results per recipe and 24 pooled legal offers. Use at most one audio
interpretation on a cache miss, one search-intent request and one selection
request. There is no automatic retry, critic loop or provider substitution.
Results retain per-query evidence, source authority and intended relationships.
No proposed explanation establishes creative quality. The selector may return
no choices instead of forcing weak links or padding the list.

Default timing preserves the cut. Explicit Flexible cut admits a shared-cut
adjustment of at most two seconds in either direction, further constrained by
actual source handles and frame-valid durations. It requires an unlocked anchor
and an empty following slot. Anchor source-in, pair outer boundaries, music,
unrelated slots and locks remain unchanged. Fixed-timing replacement of an
unlocked following scene is allowed. Candidate admission checks whether any
permitted cut fits, rather than requiring the original placeholder duration.
Server validation enforces offered unit bounds in addition to film bounds.
The selector copies an exact offered cut with precomputed duration and source-in
bounds: the feasible original cut, endpoints and a few nearby measured guides.
The existing non-grid boundary can stay unchanged; adjusted cuts use the passage's
frame grid. User timing controls retain the full legal interval.

Keep committed timelines exact. The coordinator builds validated ordinary
documents from narrow choices; no model-authored arbitrary patch is accepted.
Affected search evidence, references and directions are invalidated or marked
for review using existing ownership rules. Outside recipes may become stale but
must never silently rebind. The normal Generate/fill timing guard remains intact.

Pair previews use a shared render engine with the original passage's cumulative
24-fps frame origin, music offset and fade envelope. Correct an existing short
render defect by constraining source decoding separately from exact output frame
counts; validate decoded output count. Version the new derivations as
`decoded-reel-timeline-gaps-v4`, `decoded-reel-audio-fade-v3` and
`next-scene-global-frame-excerpt-v1`. Preserve old caches and original media.
An adjusted source-in or cut must have a matching completed preview before Apply.
Revision conflicts remain unapplied; duplicate application cannot overwrite work.
Render manifests also record `square-pixel-display-aspect-fit-v1` as their
display-normalization component. Non-square source pixels retain the intended
display proportions, and earlier manifests become incompatible for cache reuse
or Apply. The separately gated Match Cuts geometry path remains unchanged.

Admit optional sampled-frame inspection as a bounded Lab comparison addressing
the observed missing-moment failure. It uses three actual timestamped JPEGs per
window, up to six candidate windows plus the anchor, through the explicitly
configured OpenAI text planner. Keep the default text path available; Gemini
inspection is unavailable with an explicit explanation. Cache sampled derivations
by source/window/sampling identity and retain content hashes in provider receipts.
The sampler uses `next-scene-three-pts-display-cropped-jpeg640-v2`: decoded PTS,
Lab-only square-pixel display normalization, normalized crop and bounded JPEGs.
Do not claim continuous video, exact motion, action completion or geometric
matching from these samples. No new model, vector space, global backfill or
promotion of the separately gated Match Cuts profiles is implied.

## Validation and progression

Focused checks cover offered-source bounds, flexible/fixed cuts, locks, stale
revisions, metadata invalidation, no-result and failure cases, preview parity,
adjusted Apply and Undo. Browser validation covers real musical pair playback,
Space, seeking, compact results and lifecycle behavior. Compare a bounded
text-versus-sampled-frame case with consistent offers where practical; mechanical
or model-judged results do not close human creative acceptance.

The twelve-task creative pilot remains optional structured evaluation, not a
requirement for using or polishing the experiment. Normal playback reactions
guide subsequent iteration. Automatic openings, longer sequences, learned
ranking, fine-tuning and RL remain separate decisions after useful pair behavior.
