# ADR-0038: Played Match Cuts and tracked subject evidence

- Status: Accepted
- Date: 2026-09-12
- Extends: ADR-0027 within Lab only

## Context

The 6x6 residual-region baseline does not follow subjects; opposing movements can
cancel. Comparing average motion phases can reward restarting an action even
when velocity jumps at the boundary. The interface separates choosing, comparing
and keeping a single pair across nested steps. Sparse ingestion frames are also
mistaken for the only available cut points.

## Decision

Use one Match Cuts workspace with a single monitor, automatic matching with
explicit focus overrides, nearby outgoing timing (one second either side), and
an exact-frame pin. Frame navigation reads real source PTS and legal shot bounds.
Original framing and speed are defaults; reframing remains an explicit option.
Finding and previewing never apply footage. Keeping a cut applies one durable,
revision-checked change with Undo, in place. Timing adjustment creates a separate
immutable preview proposal and requires its played preview before application.

Retain omitted `focus`/`timing` request fields as the ADR-0027 baseline, so
experiments can compare it without recreating old dependencies. New camera
matching scores exit/entry velocity, speed change, acceleration and reversals,
jointly reevaluating allowed reference instants during candidate refinement.
Flow-warp photometric support is required in every temporal phase to reject the
observed dissolve/static false match. Boundary scoring has its own identity in
the frozen job, separate from prepared vector/track profiles. Unknown or
one-sided-static movement does not become a positive continuation.

Add an independently prepared SAM 2.1 Small tracked-mask profile using the
existing pinned RAFT flow model. Track masks in both directions from a reference
prompt. Automatic salient-region selection can be overridden by a point or box.
Candidate retrieval considers all retained tracks. Compare screen-space and
camera-compensated movement separately, fit camera motion outside tracked masks,
and retain ordered 4x4 subject-local flow and quantiles so opposite limb movement
does not disappear into one average. Grounded shape matching uses visible mask
position, scale and silhouette. Foreground intersection-over-union avoids
rewarding shared empty mask cells, and multiplicative shape/layout scoring
prevents good position alone from becoming a shape match. This does not claim
identity or verified pose.
The unavailable DINOv3 profile is an independent optional dense channel, never a
prerequisite disguised as a working feature.

Automatic mode uses only ready channels, discloses coverage and unavailable or
unknown reference evidence, and combines independent retrieval ranks. Raw model
vectors/scores are never combined. Shot-level retrieval votes are distinguished
from evidence for the winning exact proposed trim. Explicit focus never silently
substitutes a different kind of matching evidence.

The same 200-shot/600-frame/80-window cohort bound remains. Track preparation is
resumable per window and publishes a checksummed complete manifest only after
all expected rows, including empty tracks, exist. Models, runtime, precision,
sampling and descriptor contracts are independently versioned. Inference is
local-only; checkpoint download is an explicit preparation command. Refine at
most ten candidate windows across all channels, never a full-library decode.
Begin subject/shape refinement with at most five of the channel's allocated
windows and continue within its original cap if fewer than three reliable
choices survive. Four diagnostic fixed/nearby requests retained their exact
ranked top three at this budget; broader recall remains an evaluation question.
Previews publish incrementally while matching runs; cancellation makes partial
results unappliable. Uncropped proposals encode only one identical preview.
Match preview and export use a versioned absolute-source-PTS boundary policy.
The outgoing start is a decoded native frame and render rounding preserves the
offered last-A/first-B frames, including nonzero timestamps and variable frame
rates. This policy is isolated from the existing music renderer.

## Evidence and promotion

Use the reproducible `pipeline.experiments.match_effectiveness` harness to keep
legacy/fixed/nearby ablations, source identities, profile versions, execution
timings, actual preview hashes and separate human judgments. Diagnostic draft
cases and model correctness tests are not editorial acceptance. Keep the
existing static gate; a motion study freezes 24 held-out references and requires
18 useful top-threes, 16 preferences over baseline and at most three regressions.
Targets are warm p95 retrieval under 250ms, first playable result within 15s and
three within 30s on the local RTX 5070 Ti; report measured misses explicitly.

Library-wide preparation, ANN, main-search activation, distributed service,
music-editor coupling and speed changes remain gated. WAFT, SAM 3.1 and point
tracking remain challengers until held-out played-cut comparisons justify their
cost. No newer model becomes the default on benchmark rank or a working probe.
