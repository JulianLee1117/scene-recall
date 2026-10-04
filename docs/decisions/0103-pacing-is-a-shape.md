# ADR-0103: Pacing is a shape; harness v2 is the default editor

- Status: Accepted
- Date: 2026-10-03
- Amends: ADR-0096 (concept paces, assembly bounds, opt-in status)
- Superseded by: None

## Context

ADR-0096 gave every section one of four paces and kept each within one step of
the owner's pacing preference. A Patient edit could never flash, and a section's
duration bounds applied to every shot inside it. The owner's edits often do the
opposite: long holds, then a burst of images on a hit. Pacing is a shape over
time, not one calm-to-energetic level.

The owner also asked for one editor to iterate on, not two. v1 and v2 tied in
blind judging (ADR-0096), v2 is 2-4x faster and built on measured evidence, and
the owner left the choice to us.

## Decision

1. **Paces are free.** `planner_settings.pacing` is the edit's overall tendency.
   The concept picks any of the four paces per section; the one-step clamp is
   removed. The concept contract becomes `harness-concept-v2`.
2. **Moves.** A concept act may carry up to four moves:
   - a **flash** is a burst of shots 3-12 frames long (0.125-0.5 s, target
     0.25 s), cut on beats, half and quarter beats, and every accent;
   - a **hold** is one shot across the move. It splits only when no candidate
     covers it, and never into pieces shorter than 1.5 s.

   Move times are seconds from the section's start, snapped to the nearest beat
   or strong off-beat accent within half a beat. Overlapping and too-short
   moves are dropped. A gap shorter than a beat between a move and the act's
   edge or the previous move joins the move; beside a hold, so does a gap
   shorter than the section's shortest shot. A move may name its own query; it
   otherwise uses the act's queries.
3. **The planner sees each section's measured shape**, keyed to the section's
   start:
   - the strongest accents;
   - rises (onsets where loudness jumps, entrances out of quiet first);
   - the passage's quietest spans;
   - loudness per second.

   It anchors moves on these measurements rather than guessing times
   (current-work principles 1 and 2).
4. **Assembly** (`beat-lattice-assembly-v2`) treats each move as its own act,
   with fixed bounds. Flashes and holds are not scaled by intensity or by a
   critique's pace scale. Searches shared by an act's pieces run once.
5. **`lab.harness` defaults to `v2`.** v1 remains selectable with
   `lab.harness: v1`, and the test fixture pins v1 for the v1 generation tests.

## Consequences

- An edit can hold through a build and flash on the drop, or stay rapid and
  breathe once.
- Moves are planner proposals. A bad time snaps to a beat or is dropped; it
  never breaks the edit.
- Flash shots are a few frames long. Their relevance comes from the act's
  queries, so a flash reads as texture, not story.
- Measured rises use passage-relative loudness. In a uniformly loud passage they
  are rare, and accents carry the hits.
