# ADR-0098: Show each shot's action with a focus span

- Status: Accepted
- Date: 2026-09-28
- Refines: ADR-0093 (the `hero` evidence producer) and ADR-0096 (editor windows)

## Context

A search for "guy riding motorcycle with girl romantic undertones" returned
Top Gun: Maverick first. Its card showed the wrong picture. The shot (3769-3781
s) holds a beach dogpile that cross-dissolves into Maverick and Penny on the
motorcycle, and TransNetV2 kept the dissolve inside one shot:

- The hover preview is four seconds around the shot's midpoint, which here is
  mostly the dissolve.
- The hero frame (3771.7 s) showed the beach. Its pick weighs sharpness against
  closeness to the action peak, and the crisp silhouettes won.
- The understanding pass had the facts right: the peak was 3777.2 s, and the
  action read "Squad dogpile cross-dissolves into Maverick and Penny riding a
  motorcycle."

Measured before deciding:

- **How often.** Gemini's action text names a dissolve or fade in 1,004 shots
  across 151 films (0.5%); Citizen Kane alone has 48. Missed cuts and whip pans
  add to that.
- **The peak alone is not the fix.** 18% of hero frames sit more than 1.5 s
  from the peak, and 15% of previews miss it. But on 12 random such shots, the
  peak frame was better 4 times, worse 4 times (black, blurred, mid-turn) and
  equal 4 times. Gemini watches at 1-2 fps, so its peak is approximate.
- **TransNetV2's gradual-transition output does not see slow dissolves.** It
  peaked at 0.03 inside the Top Gun shot and found 5 of 8 dissolves Gemini had
  named.
- **Stored keyframe embeddings do separate pictures.** Each shot has three PE
  keyframes. In the Top Gun shot, the beach keyframe scores 0.51-0.53 against
  the motorcycle ones, which score 0.95 against each other. A pair falls below
  0.7 in 7% of all shots, against 54% of Gemini-flagged dissolves and 44% of
  shots with a recorded hidden cut. A random sample of 8 flagged shots dropped
  only frames that showed something else.

## Decision

The `hero` producer (`frame-pick` v2) decides how a shot is shown.

1. **Pictures.** The shot is split at hidden cuts, and between neighbouring
   keyframes whose cosine falls below 0.7. The picture changed somewhere between
   those two keyframes, so that gap belongs to neither side. A slow camera move
   drifts by small steps and stays one picture. Near-black stretches are
   removed.
2. **Focus span.** The focus span is the picture holding the action peak (else
   the middle of the shot). It is compiled as `focus_start` and `focus_end`.
   Multi-picture shots also keep their full picture list in the artifact.
3. **Hero frame.** The existing score picks the best still inside the focus
   span.
4. **Hover preview.** The ingest clip stays wherever it lies inside the focus
   span. Otherwise a 4 s silent 480p H.264 clip is rendered around the peak,
   inside the span, and compiled as `preview_path`. `/media/preview` serves it
   in place of the WebM, and `preview_url` carries the span as a cache key.
5. **Consumers.** Results carry the focus span, and the player opens there when
   no line or frame was matched. Editor v2 windows stay inside it.

Unchanged hero frames are hard-linked from the previous profile, so a new
profile costs only the frames and clips that changed.

## Evidence

- **Top Gun.** The shot splits into beach (3769.1-3772.1 s) and motorcycle
  (3775.1-3781.1 s). The hero frame moved to 3776.7 s, and the new preview
  covers 3775.2-3779.2 s. Every frame shows the ride.
- **Backfill cost.** Top Gun took 28 s: 69 frames re-extracted and 139
  previews rendered for 2,986 shots, with the rest reused.

## Consequences

- **Coarse bounds.** Boundaries are only as fine as the keyframes, so a focus
  span can trim some usable footage next to a change. A change between two
  similar-looking pictures goes unseen.
- **Query-independent.** The focus follows the evidence's peak. A visual search
  that matched the other picture still shows its matched keyframe as the
  thumbnail.
- **Editor v1 is unchanged.** Its planner already sees the peak time.
- **Deferred until a failure shows the need:** a dense per-frame detector, and
  splitting units at pictures.

## Alternatives considered

- **Anchor thumbnails and previews on the peak everywhere.** That trades one
  set of bad frames for another, on the evidence above.
- **Run TransNetV2's gradual-transition output over the library (4-8 h of
  decoding).** It misses the slow dissolves that caused this.
- **Run a dense descriptor pass over every shot.** It would be more precise,
  but costs a library decode for a 0.5-7% case. Keyframes already show where
  the picture changes.
