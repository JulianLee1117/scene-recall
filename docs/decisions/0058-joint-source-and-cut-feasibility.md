# ADR-0058: Resolve selected footage and cut preferences together

- Status: Accepted
- Date: 2026-09-14
- Extends: ADR-0041, ADR-0043, ADR-0044
- Supersedes: Independent authoritative source starts and end frames for provisional timed selection

## Observed failure

Nocturne generation `04a656fe-0d2d-4a3b-b251-d284f2fb456f` failed on shot 12.
The offered source was about 0.75075 seconds long. It fit the shortest allowed
timing, so its schema branch was valid, but the model's jointly selected cuts
required about 0.91667 seconds. Per-field bounds could not express the
dependency on the preceding shot's ending frame. The saved revision stayed 10.

## Decision

Keep retrieval, candidate identities, intention order/count, finite cut offers,
one selector call and atomic project application. For provisional timing only,
use `scoped-source-timing-preferences-v1`: each required shot names an offered
source with a normalized position in its available trim range, plus an offered
`preferred_end_frame`. The model owns its source choice and editorial cut
preference. It does not independently declare authoritative trim timestamps.

A bounded dynamic program considers only the existing offered boundary frames.
It finds a complete ordered path that fits every selected source, minimizing
total displacement from preferred cuts, then the number of changed cuts, then
earlier boundaries for deterministic ties. The small per-boundary offer lists
keep this local computation bounded; no hosted repair stage is added.

After durations are established, resolve each normalized source position into
that source's legal start interval. Validate source identities, positions, cut
membership, complete coverage and source bounds before constructing the normal
saved timeline. Record preferred/applied cuts and report any adjustments in job
progress and diagnostics. Do not substitute a source, invent a cut, sort invalid
output, truncate footage or silently change an existing user's timeline.

Fixed slots retain `scoped-scene-choices-v3` and their source-bound absolute start
timestamps. Existing projects and failed receipts remain readable unchanged;
the new response contract separates derived artifacts from older ones.

## Validation and limits

The archived failure has a legal path retaining all 26 offered source choices:
its problematic ending boundary moves from offered frame 262 to offered frame
255. Regression coverage checks this dependency, deterministic ties, null
choices, invalid identities/positions, source containment and impossible paths.

Some combinations of individually eligible short sources cannot fill a whole
passage at any offered boundaries. Such a combination still fails atomically
with an explicit feasibility error. No alternate footage or arbitrary gap is
inserted. This change establishes legal timing, not action completion,
perceptual continuity or artistic quality.
