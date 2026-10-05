# ADR-0106: Feature-locked effects in AI Music Video renders

- Status: Accepted
- Date: 2026-10-05
- Extends: ADR-0099 (moment index), ADR-0100 (renders are a cache)
- Leaves frozen: the projectless Transitions Lab (ADR-0070 to ADR-0076)
- Superseded by: None

## Context

The owner asked for effects that many edits use. The main one overlays a clip
on another with a matched feature, eye on eye. It is used as a transition into
the next clip or stacked fast. The owner also asked for other striking effects.
Finishing stays in our own editor: a Resolve 21.1 trial mislinked OTIO media
and could not apply ramps or masks headlessly.

The AI Music Video renderer only cuts. The frozen Transitions Lab composites a
single projectless pair, so it cannot touch an edit. The moment index
(ADR-0099) already describes every 4 fps instant of every indexed shot,
including the main person's COCO keypoints (eyes included) and subject boxes.
That makes feature locking a lookup, not a new model.

## Decision

1. **Effects on the document.** A project document gains an optional
   `effects` list. Each effect has an ID, a kind and a span on the song clock:
   - `overlay` plays another source window (`source`: film, start, optional
     crop) over the picture. `align` is `eyes`, `subject` or `none`; `track`
     re-solves the alignment every frame; there are blend modes and an
     attack/release opacity envelope;
   - `lock_cut` dissolves across the cut at `at`. Before the cut, the incoming
     shot's pre-roll fades in with its feature pinned to the outgoing feature.
     After the cut, the incoming shot eases from that alignment back to its own
     framing while the outgoing shot's post-roll fades out on top;
   - `zoom_through` pushes into the outgoing feature (moving it to the
     centre), cuts, and pulls back out of the incoming feature;
   - `punch` is a sudden zoom around the feature that decays;
   - `flash` lifts exposure toward white;
   - `echo` blends each frame with the previous output, leaving a decaying
     trail.

   Blend modes: normal, screen, lighten, multiply, difference, and `luma`, a
   double exposure where the overlay shows through the picture's shadows.
2. **Robust to edits.** Effects anchor to song time, not slot IDs. A cut
   effect uses the cut within one frame of `at`, or is skipped. An effect
   outside the rendered reel is skipped. The document stays saveable after
   passage or timeline changes. A document without effects keeps exactly its
   previous manifest and render identity.
3. **Rendering.** The cut sequence renders as before. When effects exist, a
   compositing pass decodes it (PyAV), composites in NumPy with PIL affine
   resampling, and re-encodes at libx264 CRF 18. Music and dialogue are then
   muxed as before, and the frame count is enforced. The manifest records
   `feature-locked-effects-v1`. The job result reports skipped effects,
   unaligned effects and whether an index was available.
4. **Features.**
   - **Eyes:** both eyes of the main person when both are shown (confidence at
     least 0.35).
   - **Otherwise:** the scorer's eye-trace point, sized by the subject's box.
   - **Interpolation:** features are interpolated between grid instants.
   - **Coordinates:** they map through the film's content box, the clip's crop
     and the renderer's fit into output pixels.
   - **Alignment:** a similarity transform. Two points fix scale and turn, but
     a turn over 15° is dropped (it reads as a tilted card, and a half turn
     means mirrored faces). Scale is limited to 0.6-3 (smaller reads as a pasted thumbnail).
   - **Clean pictures:** overlay edges fade over 8% of the picture's shorter
     side. A moved base picture (a settle or a zoom-through) zooms up to 2x
     about its feature, so it keeps filling its frame without black corners.
   - **No feature, or no index:** the effect falls back to the picture centre
     at unit scale and is reported as unaligned.
5. **Shot boundaries.** When the index knows the shot, pre-rolls and post-rolls
   hold a frame rather than show a neighbouring shot.
6. **Scope.**
   - Effects reach documents through the project API; the editor harness does
     not place them.
   - The browser player plays the cuts, so effects show in renders only.
   - OTIO export carries the cuts only.

## Consequences

- Agent-built edits and API clients can use feature-locked transitions and
  overlays now. The owner judges them in rendered dailies.
- A render with effects re-encodes the whole reel once. A 3-minute preview
  adds a few minutes to the render.
- Alignment reads the index at render time. Rebuilding the index can move an
  unchanged document's alignment slightly.
- Gates for automatic use: the harness may place effects, and a UI may expose
  them, only after the owner keeps effects in reviewed edits. Masks
  (silhouette reveals) wait for a measured need. The index's 16x16
  silhouettes are too coarse for clean edges.
