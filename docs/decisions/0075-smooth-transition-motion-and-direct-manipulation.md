# ADR-0075: Smooth transition motion and direct manipulation

- Status: Accepted
- Date: 2026-09-16
- Extends: ADR-0071, ADR-0072, ADR-0073 and ADR-0074
- Supersedes: RGB-v7 motion sampling and the single-frame retime endpoint correction

## Context

Played whips/zooms felt stiff and the Lab required too much numeric editing.
The user supplied Inspired Island's *D I C A P R I O 2* as a motion reference.
Its sliding joins concentrate blur in a short burst and resolve sharply, generally
without pronounced bounce. Its within-shot zooms and layered compositions require
more than a two-shot transition and do not expand this Lab's editorial boundary.

Concrete implementation failures explained part of the stiffness: piecewise
midpoint warping could change velocity abruptly; whip exposure ignored duration;
zoom scale changed slope abruptly; and forcing the final native frame into a
Rush render created a last-frame speed spike above the requested multiplier.
Slow-flow preparation also lacked end context and fell back to repeated native
frames. The UI hid AI below a long workspace and mixed saved results with working
controls. Crop changes discarded native stills and loaded source video needlessly.

## Decision

Version these changes as `transitions-rgb-v8`. Give whip and zoom continuous
acceleration profiles, frame-timed shutter exposure, bounded adaptive radial
sampling, protected transformed edges and a small optional single settle.
Retain exact composited A/B endpoint locks. Reuse exposure/softness controls;
add only rebound and advanced overscan rather than multiple duplicate blur knobs.
This remains a streaming spatial compositor with bounded current-frame memory.

Preserve the native-time speed integral and frame count. Distribute the endpoint
correction monotonically within the existing ramp, leaving ordinary-speed timing
unchanged. Keep native endpoints and source windows. Give local MCI cloned final
frame context within the existing budgets, never unselected raw footage. Record
actual source-target speeds separately from the requested continuous curve.

Make Local effects and AI transitions explicit modes. Keep drafts mounted and
pause hidden media. Show saved-source identity, collapse historical experiments
and preserve explicit preview selection. AI preparation is an explicit local
Draft hard cut with original speed, retaining the user's local controls. Paid
generation continues to require the existing configured server credential and
reviewed submission; preparation and quotes make no paid call.

Use bounded draggable trim handles, direct on-picture framing and a duration
slider. Keep keyboard equivalents and optional precision controls. Match raw
endpoint evidence per source window, independently of working framing and the
other source; apply the current crop to that still. Preserve existing filenames,
original footage, saved results, manifests and storage/reuse boundaries.

## Consequences

Old previews remain reproducible artifacts and require an explicit new render to
test RGB-v8. Numerical continuity and endpoint tests are necessary, but creative
quality still requires played comparisons on actual footage. Mirror sampling is
edge protection, not reconstructed off-screen imagery. Sub-three-frame speed
regions remain poorly sampled; optical flow can still warp independent motion.
Shot-level effects, masking/layered graphics and promotion into the main AI editor
remain separate decisions. No external overlays or additional model download is
required for these local changes.
