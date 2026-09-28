# ADR-0037: Manual moment and framing adjustments in the pair preview

- Status: Accepted
- Date: 2026-09-12
- Extends: ADR-0036

## Context

Source-start sliders in suggestion cards are separated from the picture they
change. The user needs to choose the useful moment and reframe a suggested scene
while seeing the result, without adding another editor or an AI crop pipeline.
The project already represents normalized crops and renders them consistently.

## Decision

Use the existing AI Music Video monitor for one selected next-scene alternative.
Moment adjusts the incoming source-in inside its offered shot while preserving
duration. Crop & zoom provides direct picture positioning, a zoom control and
reset to the full image. Local adjustment shows the incoming source immediately;
an explicit Preview with music renders the complete adjusted pair. Apply remains
one explicit, undoable revision. Candidate cards identify and open alternatives;
the active preview owns adjustment and application controls.

Reuse the same source control in ordinary footage review for already placed
clips. Verified film duration permits browsing nearby source windows; otherwise
retain the supplied bounds. That manual workflow keeps its existing project-edit,
save and Undo lifecycle, with local Cancel and explicit Use this footage. It does
not require next-scene generation or its pair-preview receipt. Locked clips remain
read-only. Source-window or crop changes invalidate reused visual search evidence.

Extend the existing `NextSceneAdjust` payload with optional `crop`, using the
existing normalized `Crop` model. Omission preserves the candidate crop; explicit
null resets it. A shared payload normalizer preserves that distinction across
HTTP, durable preview snapshots and application. The UI sends its complete draft
source-in, cut time and crop because adjustments resolve against the immutable
original proposal. No new endpoint, timeline representation or database table is
required. Dragging is local; it starts neither rendering nor a hosted request.

Only explicit manual adjustments can add an incoming crop. Model selection keeps
its existing narrow source/time schema. Anchor source-in and crop, pair outer
times, music, outside edits, locks and offered-source bounds remain protected.
The existing equality checks over incoming/outgoing clips and render manifests
include crop: a differently cropped or uncropped preview cannot authorize Apply.

Discard current visual/inspection evidence when its source window or framing no
longer matches. Crop changes clear incoming framing references and resolved search
proof and flag the direction for review, retaining the written intent and original
job/receipt as history. A cropped clip does not offer its uncropped indexed frame
as Look or Framing evidence for subsequent planning; whole-shot semantic references
retain their existing meaning. This does not infer a new geometric score.

Reuse the shared renderer and versioned display normalization. Existing normalized
crop semantics and original media are unchanged. Automatic crop selection, object
tracking, animated reframing, region compositing and Match Cuts profile promotion
remain separate work, not prerequisites for these manual controls.

## Validation

Verify strict crop bounds, omitted versus null serialization, exact cropped-preview
proof, reset, one-step restoration, and preservation of the rest of the edit.
Compare incoming crop pixels with an independently decoded source and compare the
pair with its full-timeline excerpt, including anamorphic display proportions and
the original music/fade clock. Exercise dragging, zoom, reset, keyboard playback,
preparation progress and control discoverability in the browser where available.
