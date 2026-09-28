# ADR-0052: Short-shot temporal evidence and targeted backfill

- Status: Accepted
- Date: 2026-09-14
- Supersedes: None
- Superseded by: None

## Context

A selected Music Video Lab clip contains a person disappearing beside a tree,
but its 1.21-second source shot was represented by one midpoint frame after
the person had disappeared. Its annotation described an empty, static scene.
The editor could not recover an action that ingestion evidence never showed.
Improving only the planner would leave the same failure in search and future
edits. Repeating whole-film ingestion would unnecessarily repeat detection,
dialogue and unrelated annotation work, while disrupting ongoing Lab jobs.

ADR-0001 already preserves source media and timestamped evidence and requires
replaceable, versioned derivations. ADR-0008 distinguishes sparse still evidence
from verified motion. ADR-0025 provides a durable local worker and resource lock.
These boundaries support a narrow evidence repair without an every-frame index,
a second inference queue, or activation of the deferred Motion Match profile.

## Decision

Use sampling profile `short-shot-edge-middle-native-pts-v2` for shots shorter
than the configured threshold, initially 2 seconds. Propose instants near the
beginning, middle and end, then retain distinct decoded source frames within
the shot. Store both their raw presentation timestamps and the container-origin
offset, and publish their difference on the player/ffmpeg-relative timeline.
Use `decoded_container_relative_pts_v2` as the frame timestamp lineage; a
nonzero container start time cannot shift the selected evidence. One- and
two-frame source shots remain valid; never duplicate an image to imply
additional evidence. Preserve shot IDs and boundaries, and separate
detection-cache identity from sampling.
Leave longer-shot sampling and hosted annotation identity unchanged.

Only profiled shots select a new prompt that describes observable changes
across chronologically ordered images. People and subjects visible earlier
must not vanish from the annotation merely because the final image is empty.
Subject energy and camera movement are independent; insufficient motion
evidence produces `unknown`. Absence alone cannot establish a fade or an
unseen cause. Do not guess character identity, plot or actions between frames.
Keep the established response schema, three-image cap, one normal request per
shot and bounded provider retry/fallback behavior. Include sampling profile and
selected prompt SHA in the annotation cache identity, including fallbacks;
retain old evidence and annotations alongside the new profiles.

Add a targeted, read-only-by-default `backfill-temporal` plan over legacy
one-frame short shots. Scope by explicit films, units, or the source films
used by placed clips in one project. Reuse retained sources, shot boundaries,
dialogue and previews; derive only affected images, annotation and compatible
visual/text search rows. Before a broad paid migration, inspect a small source-backed set
covering disappearance, entrance, exit and gesture, comparing new images and
descriptions with the actual footage. Sparse stills improve coverage but do not
guarantee recognition of every short event.

Direct apply uses the existing exclusive ingest resource lock. Queued apply
uses the existing durable job ledger and standalone worker in bounded
maintenance batches, initially 32 and at most 128 shots each. Foreground
ingestion and Lab jobs take precedence, including work submitted between
batches. Persist the selected unit scope and expose inspection and cancellation.
Stage new jobs in `waiting_worker`, which older workers ignore. Updated workers
claim waiting maintenance after queued foreground work; the CLI displays
`queued` until claimed, including batches behind an already-updated worker.
If an older worker is still running, restart it only after active work becomes
idle, without interrupting ingestion.
Do not automatically replay failed or interrupted hosted work; an explicit
retry reuses matching successful caches.
Allow exact unit-ID retries to repair semantic text after evidence is current;
broad scopes must not silently schedule that repair for every current unit.
Deferred semantic-text failures preserve published evidence and report a failed
job with a local `index-text --film-id` or exact-unit retry instruction. Direct
apply writes its requested receipt before returning a nonzero exit status.

Validate a batch before publishing its targeted frame and unit changes. Keep
the current compatible visual model profile, and retain generation-complete
activation requirements for optional text and Framing profiles. This is not a
cross-table transaction: new frames can briefly coexist with old unit metadata
before the target-only unit merge, as in existing film publication. Never alter
raw films, saved bookmarks, project revisions, selected footage or edit timings.
Saved clip titles and `search_evidence` retain the original selection record.
Updated metadata improves future searches without rewriting that history.

## Consequences

- New short shots receive better event coverage without increasing the normal
  hosted request count or invalidating unchanged longer-shot annotations.
- Existing films need only a scoped media and derivation pass; the original
  evidence and previously paid annotations remain available for comparison.
- Additional image bytes, native decoding, local embeddings and hosted image
  processing have a real cost. Small validation gates and bounded maintenance
  batches limit that cost and contention with ongoing creative work.
- A whole-shot annotation still cannot certify what happens within a particular
  selected trim. Source-window inspection remains necessary for precise editing;
  this change does not introduce continuous-motion evidence or identity claims.
