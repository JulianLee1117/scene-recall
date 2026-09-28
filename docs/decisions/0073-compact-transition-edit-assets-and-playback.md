# ADR-0073: Compact transition edit assets and synchronized playback

- Status: Accepted
- Date: 2026-09-16
- Extends: ADR-0068, ADR-0071 and ADR-0072
- Preserves: immutable source evidence, generated originals and the editor-promotion gate

## Observed need

The user requested comparison with Resolve/Premiere and practical playback and
storage behavior. Repeating an already completed local request created another
render. Comparison playback advanced its shared clock while a pane buffered,
and source players could load full-film playback under retained endpoint stills.
The lab offered only 480/720-pixel output, limiting external edit use.

A read-only audit found 40 retained transition jobs totaling 73,956,448 bytes
(about 70.5 MiB), including one imported original. Scratch was zero. This is a
prevention and visibility change, not evidence of an existing cache crisis.

## Decision

Reuse a completed local render only when its full frozen request, source
identity and renderer profile match, all retained artifact digests verify, and
the ledger still agrees under write serialization. A missing, modified,
cancelled or incompatible result does not qualify. Do not deduplicate paid
generations or user imports by appearance or silently rerender old artifacts.
No new shared-media cache or cross-job hardlinks are needed.

Expose measured output/original/retained/scratch byte counts for flat owned job
directories. Unreadable or unsafe storage is unknown, not a false zero. Retain
all completed outputs, recipes, source evidence, originals and provider receipts.
Do not introduce broad age eviction, an automatic quota, or a cleanup action
that discards experiments. Report a reused result distinctly in the UI.

Apply a throttled check for 256 MiB free headroom and a monitored 1.5 GiB of
known job scratch during rendering, preserving the stricter 1 GiB native/flow
per-source bound. These checks are not filesystem reservations or atomic quotas.
Preserve cancellation priority. Extend the existing terminal-job scratch
allowlist only for demonstrably replaceable transition assembly/transfer files.
Provider and atomic JSON partial receipts may contain unique billing/task state
and remain evidence. Cleanup failures warn rather than mask the render outcome.

Add an explicit `export` quality at a 1080-pixel short edge, alongside existing
`draft` 480 and `high` 720. A saved local result can request a 1080p copy of its
frozen settings. Never generate that copy automatically or upscale the saved
draft: prepare it from the same original source windows. Repeated export requests
reuse a verified matching result. Landscape is 1920×1080, portrait 1080×1920 and
square 1080×1080. Existing temporal, source-window and scratch limits still apply.

Version local output as `transitions-rgb-v7` and bridge assembly independently.
Record a common `compact-h264-closed-gop-2s-v1` encoding policy: muted 30-fps SDR
Rec.709 H.264, yuv420p, fast-start MP4, scene-cut keyframes and at most 60 frames
between random-access points. Retain CRF 21 for draft and 17 for review/export,
with the bounded fast encoder. This is a compact rendered edit asset, not a
lossless, HDR, alpha-bearing or editable native NLE effect.

Codec comparison on three short real examples found that medium encoding and
one-second GOPs did not consistently reduce size. The selected two-second GOP
keeps the current quality settings and bounds decode work while seeking; it can
slightly increase file size. Do not claim storage savings from that change.
Storage savings come from avoiding duplicate/automatic copies and scratch cleanup.

The browser comparison clock and music hold together when a required pane is
not ready, and resume only after the current panes can continue. Show preparation
state instead of silently comparing different moments. Stale readiness events,
selection changes, pause and unmount must not restart playback. Source-film
media loads on deliberate source inspection, not just to cover it with a still.

## Consequences

The lab can produce and play compact full-HD assets for an external edit while
retaining its focused experiment workflow. It does not reproduce Resolve's
Speed Warp, arbitrary spline/node tools, Premiere's timeline/proxy systems or
their proprietary interpolation quality. Direct automatic use in the AI editor
still needs its separate timeline/continuity decision and acceptance evidence.
Fixed 30-fps SDR export remains explicit; frame-rate conversion in another
timeline can change cadence. AI originals are never mistaken for disposable
preview cache.

See [the NLE comparison](../experiments/transition-nle-comparison-2026-09.md) for
dated primary documentation and the measured implementation review.
