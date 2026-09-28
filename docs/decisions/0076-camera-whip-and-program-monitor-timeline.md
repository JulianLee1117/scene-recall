# ADR-0076: Camera whip and a program-monitor timeline

- Status: Accepted
- Date: 2026-09-16
- Extends: ADR-0071, ADR-0072, ADR-0073 and ADR-0075
- Supersedes: RGB-v8's adjacent-picture whip composition and separate source-card editing layout

## Context

The user still found two independent trim cards above the preview difficult to
use. Changing source settings while watching a saved render also made it unclear
which picture represented the working draft. The desired workflow is closer to
an editor: inspect or trim either clip on one strip, shape the join, and play the
result in the same monitor.

The prior whip translated adjacent A/B panels. That moving picture boundary could
look like a slide transition even with smoother timing and stronger shutter blur.
The supplied motion reference instead motivates a short whole-picture camera
smear with a clean landing. This changes the local recipe's composition rather
than its source-time or AI boundary.

## Decision

Version the new local profile as `transitions-rgb-v9`. Keep recipe ID `whip-pan`
and expose it as **Camera whip**. Replace the adjacent-picture strip with modest
whole-frame movement in the same screen direction on both sources, a temporary
sourcewise crop protecting transformed edges, and a short full-frame crossover
near the blur peak. Freeze the policy as
`whole-frame-camera-whip-short-crossover-v1` in the render receipt.

Reuse `softness` as **Cut blend**, the crossover phase span, without introducing
another overlapping control. The starter is 12 output frames, .65 shutter
exposure, .55 shutter softness and .04 cut blend. Keep frame-timed spatial
exposure, restrained optional settle, bounded processing and exact composited
endpoint locks. Camera movement is procedural; it does not match subject motion
or introduce shake. Other effects and the native-time speed policy remain intact.

Present one visible monitor with Clip A, Transition and Clip B views. Keep source
and saved-render state available when switching views, but pause hidden source
media and pause transition playback/music during source inspection. Place a
continuous output-time A-to-B strip directly below the monitor. Four accessible
clip-edge handles edit source windows; the overlap region identifies the join
and its duration handle adjusts output frames. Pointer drags freeze their time
scale and cancel on stale source identity or disabled interaction. Keyboard
equivalents preserve the same limits.

Use rounded retimed output lengths for strip geometry. Source inspection maps
through the nominal speed integral and remains explicitly approximate with
active Speed; a working draft lacks decoded native sampling receipts. Preserve
independent raw endpoint stills by source film/window and apply working framing
in the monitor. A retained native still remains evidence, not a new exclusive
out point inferred from the playhead.

Keep the saved transition distinct from the working draft. Show a visible update
banner when controls differ, and scrub saved sequence time only when it matches
the working request. Rendering is explicit; selecting history does not replace
working controls, and **Load to refine** remains a separate action. The timeline
does not fetch media, create projects or render automatically.

## Consequences

Existing RGB-v8 and earlier artifacts remain immutable comparisons. Loading their
settings and rendering again uses RGB-v9 rather than promising identical pixels.
Durable job ownership, request reuse, storage bounds, compact export profiles,
source evidence and the Speed-off boundary for AI bridges do not change.

The monitor and timeline provide a shared place to inspect the pair and judge a
new render. Numerical tests can establish timing, bounded geometry and interaction
state, but they do not establish creative quality. Browser and played-film
acceptance remain required; this decision does not claim their completion.
No hosted AI result, main-editor capability, depth reconstruction or source-motion
conditioning is introduced by this change.
