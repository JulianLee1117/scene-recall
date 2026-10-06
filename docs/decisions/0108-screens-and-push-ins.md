# ADR-0108: Screens: scenes on tracked TV screens, and pushes into them

- Status: Accepted
- Date: 2026-10-05
- Extends: ADR-0106 (feature-locked effects), ADR-0107 (hard crops, panels and
  masks)
- Supersedes: None
- Superseded by: None

## Context

An agent-built edit chained through TV screens. For the beat before each cut,
the next scene played on the current shot's TV: an ADR-0107 fill set into a
rectangle and masked to the set. The owner liked the idea, but:
- the inserts were hard, square-edged rectangles that did not fit the screen,
  and did not follow it when the camera moved;
- the cut came too soon to take in what was on the TV, so it did not feel like
  entering it;
- a set that filled the frame looked like the screen the previous TV was
  showing.

They asked for it to be really clear what is happening. A rectangle cannot fit
a screen seen at an angle, and nothing in the effects layer could move the
camera into a screen.

## Decision

1. **A `screen` effect kind.** Its `source` plays inside a screen in the
   picture, in perspective. `quad` keys the screen's four corners (top-left,
   top-right, bottom-right, bottom-left, output fractions) on the song clock;
   the render interpolates linearly between keys and holds the first and last.
2. **What the screen shows.** The middle of the source frame, cut to the
   screen's own shape (never squeezed), with corners rounded by `radius` (a
   share of the shorter side) and a falloff toward the edges like a curved
   screen.
3. **A channel change.** `static` shows that many seconds of TV noise before
   the source.
4. **People in front.** `classes` are segmented classes of the picture
   (default none) that stay in front of the screen. An instance lying mostly
   (80%) on the screen is the screen's own picture, such as people on the TV,
   and is covered like the rest of it.
5. **The push.** From `push` to the effect's end, the whole frame zooms into
   the screen at a steady exponential rate that speeds up. The rounding and
   falloff fade as it enters. The last frame shows the source filling the
   frame, so the cut that follows is seamless when the next clip continues the
   same source window and crop.
6. **Rendering.** The compositing pass accepts projective (3 x 3) matrices for
   layers and for a pushed base picture, resampled through PIL's perspective
   transform.
7. **Tooling, not rendering.** `pipeline/lab/screens.py` finds a lit screen in
   the segmentation model's TV mask, with corners taken from lines fitted to
   its edges. Detection fails on dark pictures and covered screens, so an
   editor may give the corners instead. Following moves corners with the set:
   patches on the casing (not the screen, whose picture moves) are matched
   frame to frame, and a robust scale, turn and shift is fitted. A document
   stores the corners; the render never detects screens.

## Consequences

- Scenes sit on screens at any angle, follow moving cameras, and can be
  entered rather than cut to.
- Automatic detection is a starting point. In the first edit every screen got
  corners read by eye, because pictures were dark in places or hands and heads
  covered the screen.
- Segmenting occluders costs about 15 ms a frame, as other masks do.
- Effects stay render-only: the browser player plays cuts, and OTIO export
  carries cuts.

## Amendment (2026-10-06): screens toned like the set

The owner found some inserted pictures lit wrong for their set: an insert kept
its own exposure and contrast, and read as pasted on. A screen now shows its
source the way the set shows its own picture. It takes about half of the set's
brightness and tint, bounded to 0.6-1.25 times its own; the set's grey black
level; a little bloom on highlights; and 8% of what was on the glass. The tone
fades out as a push enters, so the landing frame is the source itself. The
effects profile becomes `feature-locked-effects-v2`, and earlier renders keep
their identity.
