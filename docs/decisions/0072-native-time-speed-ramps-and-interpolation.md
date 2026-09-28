# ADR-0072: Native-time speed ramps and bounded interpolation in Transitions

- Status: Accepted
- Date: 2026-09-16
- Extends: ADR-0070 and ADR-0071
- Supersedes: ADR-0071's deferral of local source retiming and interpolation
- Preserves: the separate gate for automatic editor integration and hosted generation

## Context

Played transition review exposed a concrete capability gap: the existing whip
and zoom curves animate a flat image while the source action continues at 1×.
They cannot accelerate the actual camera or subject movement into a music cut,
or slow a moment around it. Converting footage to 30 fps before any retiming
also discards high-frame-rate samples and bakes repeated frames into a slowdown.
The user explicitly requested implementation of true speed ramps, optional
optical-flow smoothing, researched AI directions and a few marked experiments.

## Decision

Add an optional, portable `retime` configuration to local render requests. Off
preserves the ordinary source-time behavior. Rush, slow-hit and speed-pulse
envelopes specify an actual source-speed multiplier, a bounded span in source
seconds, a curve and an interpolation choice. Both selected source windows stay
authoritative: do not extend a window, move its evidence timestamps or pull in
additional shots to satisfy an envelope. Derive output duration from the time
map and validate overlap against the resulting output frame counts.

Apply retiming to native source timestamps before final 30-fps output sampling.
Retain a monotonic output-to-source mapping and the actual preparation policy
in each render's receipt. Frame sampling, frame blending and local motion-
compensated interpolation are distinct methods. Motion interpolation operates
within each source independently, before light effects and A/B compositing; it
must not infer motion across the A/B cut. The initial motion-compensated mode
uses the installed FFmpeg implementation. It is not a neural model or a claim
to match proprietary optical-flow quality.

Use the existing editor worker, bounded render jobs, cancellation and artifact
ownership. Each source's active speed preparation has a four-minute deadline;
individual external commands receive at most three minutes, reduced to the
remaining stage budget with a one-second timeout floor. Native/flow timestamp lists allow at most 4096
frames; normalization may write the 4097th frame only to detect and reject
overflow. Monitor at most 1 GiB of native-plus-flow scratch per source and stop
when that budget is exceeded. This is not an atomic disk quota. Keep decoded
state to two decoded frames per reader, with disk-backed intermediates. Flow
runs at 60–120 fps over the ramp and up to 0.25 seconds of within-window context
per side. Final seam compositing keeps its separate five-minute budget. Cleanup
runs after success, error or cancellation; locked-file cleanup logs a warning
rather than replacing the render outcome.

Do not add a GPU worker, download interpolation weights, or alter raw films,
annotations or search profiles. A future neural interpolation backend requires
a demonstrated quality failure, license/runtime review, measured resource costs
and played comparisons against this baseline.

Version the final temporal semantics as `transitions-rgb-v6`. The initial RGB-v5
implementation sampled an endpoint-inclusive output interval; its review exposed
interior drop/duplication even at nominal 1×. RGB-v6 instead samples the inverse
speed integral at the real output frame timestamps `i / 30`. First and last
native frames within each selected window remain explicit endpoint locks, not
a rescaling of all intermediate sample times. Record this policy as
`output-frame-time-inverse-speed-integral-fixed30fps-v2`.

Keep source-player and raw-media timestamp spaces explicit. Seek in source-player
seconds with up to one second of preroll, omitting the seek at the beginning or
where it would enter nonpositive absolute media time. Trim against raw absolute
PTS, then subtract the fixed container origin before writing the lossless native
intermediate. Retain that origin and the native timestamp-space policy in the
receipt; this translation changes neither source evidence nor native cadence.

Completed RGB-v5 test artifacts and earlier renders remain readable and immutable.
Frozen requests queued for a different renderer cannot silently change semantics.
Comparison identity remains the source windows, framing and aspect so users can
compare different speed choices. Working-setting equality and comparison reuse
also account for effective recipe and speed settings. Compare a hard cut at the
same saved speed to isolate the visual effect; compare the same saved effect with
Speed off to isolate retiming.

Keep the controls shared across visual recipes, with explicit Off and familiar
starting envelopes. Show duration consequences and actual requested smoothing.
Save these settings in history, named recipes and timing variants. Add a separate
Experimental recipe group for defocus and a restrained 2D prismatic push, whose
descriptions do not imply depth estimation, optical lens simulation or generated
camera travel.

Improve manual/hosted AI directions with source suitability, user-supplied motion
notes and checks for both joins. Preserve the distinction between two still-image
conditioning and source-video conditioning. This change does not extend hosted
conditioning or bridge assembly to the new local time maps: importing or
generating against a retimed parent requires a separately rendered Speed-off
version, with a clear client and server explanation before upload or spending.
Existing unretimed bridges and their receipts remain available.

## Consequences

- Source movement can carry a transition independently of the chosen visual
  effect, and the original source evidence remains inspectable.
- Slowdowns can expose interpolation artifacts; a successful encode is not a
  quality pass. Compare faces, occlusions, blur and motion at normal speed and
  with frame stepping, with an ordinary sampling result available.
- Short source handles can make a requested effect infeasible after retiming.
  Report that constraint rather than silently changing source windows.
- No API key or external asset pack is needed for the local experiments. Hosted
  AI cost/provenance safeguards and the separate editor-promotion gate remain.
