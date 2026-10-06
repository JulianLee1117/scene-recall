# ADR-0107: Hard crops, panels and masks in effects

- Status: Accepted
- Date: 2026-10-05
- Extends: ADR-0106 (feature-locked effects)
- Supersedes: ADR-0106's eased settle after an eye-locked dissolve (now opt-in)
  and its gate on masks
- Superseded by: None

## Context

The owner reviewed ADR-0106's demos. Eye-locked overlays were "interesting and
sometimes effective, especially with larger subjects". The incoming shot easing
from the outgoing eyes back to its own framing read as "a bit manual, novice,
iMovie-ish", and semi-transparent overlays were not the effect meant. The owner
pointed to four reference reels:
- opaque, hard-edged eye and mouth strips from other pictures, pasted exactly
  over a face for two or three frames each;
- several crops and panels on one frame, some turned;
- masked objects and people. A bowl's soup shows a train. A shot's people
  appear, as cutouts or white silhouettes, over the previous shot before the
  cut;
- vertical strips of several shots side by side.

ADR-0106 deferred masks because the index's 16x16 silhouettes are too coarse.
The library's own segmentation model (RF-DETR Seg Small, already used by the
moments producer) gives clean full-frame masks at about 15 ms a frame once
loaded.

## Decision

1. **Overlay regions.** An `overlay` takes a `region`:
   - `full` (as before);
   - `eyes`, `mouth` or `face`: a hard rectangle around that part of the
     overlay's own face, from its eye pair and turned with it. Pinned eye on
     eye, it lands on the picture's face;
   - `subject`: the overlay's segmented instances of `classes` (COCO names,
     default person).

   `edge` is `soft` by default only for full overlays. A `matte` colour
   paints the shown area flat, for silhouettes. An overlay with `rect` shows
   only inside that screen rectangle, for split screens and half-and-half
   faces. An aligned overlay keeps that window filled under the cover rule:
   it zooms about the feature, or eases its move, rather than leaving a
   smaller picture floating in the window.
2. **New kinds.**
   - `fill` shows its source inside the picture's own segmented subject
     (`classes`). With a `rect`, the source is set into that rectangle
     first, so a scene can play on a TV screen, hidden where someone in front
     of the TV covers it.
   - `panel` sets its source, covering, into `rect` (output fractions),
     turned by `turn` degrees, with hard edges.
   - `strips` lays two to twelve `sources` side by side in vertical strips,
     each strip showing the middle of its source.
3. **Settle is opt-in.** A `lock_cut` keeps the incoming shot in its own
   framing after the cut unless `settle` is set. Eye-on-eye dissolves should
   come from shots already aligned by crops.
4. **Masks at render time.** A segmenter loads only when an effect needs a
   mask, in the editor worker. Fill masks are computed on the picture being
   composited, after any punch or zoom.
5. **Render identity.** The effects profile stays `feature-locked-effects-v1`.
   The new fields are part of each effect, so changed documents re-render.
   ADR-0106's documents keep their meaning, except that a lock cut no longer
   settles unless `settle` is set.

## Consequences

- Agent-built edits can make the referenced looks: fast hard feature
  patches, panel collages, strip collages, cutout pre-reveals of the next
  shot, white-silhouette flashes, and fills through a subject.
- Segmentation adds about 15 ms a frame where masks are used, plus a one-time
  load of about 18 s per render.
- Masks follow detector quality. A missed person shows nothing rather than a
  wrong region.
- Gates are unchanged: the harness and a UI wait for the owner to keep
  effects in reviewed edits.
