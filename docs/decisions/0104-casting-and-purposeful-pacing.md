# ADR-0104: Casting the moments, and pacing that serves the music

- Status: Accepted
- Date: 2026-10-04
- Amends: ADR-0103 (section paces, flashes), ADR-0096 (the harness pipeline)
- Superseded by: None

## Context

The owner reviewed round 1 of the style edits (ADR-0103) and found three
problems:
- **Flashes.** Every edit had one. They cut on irregular accents, their shots
  had nothing to do with each other, and the owner called them awkward and
  purposeless.
- **Pace against the music.** One edit held long shots over a driving beat.
  When the planner was free, it also slowed a Rapid edit to Balanced on a
  mellow song.
- **No intent.** Shots came from generic queries ranked by relevance, so
  choices felt random and lacked impact. A Dune: Part Two edit never reached
  the film's key moments, and the arc was planned without seeing them.

## Decision

1. **Casting** (`harness-cast-v1`, `pipeline.lab.harness.cast`). After the
   pools are gathered, one cached planner request reads each act's top 16
   candidates and casts them. It chooses the shots that carry the act's intent,
   in the order they should play (about the shots the act needs, plus spares),
   and the peak shot that lands the act's biggest musical moment.
   - Assembly gives cast shots a bonus, plus more for the peak, and keeps cast
     order within the act. Other pool shots only fill time the cast cannot.
   - A shot is cast at most once in the edit.
2. **Catalog.** For an edit scoped to at most three films, those films' key
   moments (14 each) and hidden gems (8 each) come from the synthesized priors.
   The concept planner sees them while planning the arc, and the casting
   request may place them in any act.
3. **Pace floor.** A section may be faster than the owner's pacing preference,
   but at most one step slower. Holds remain the way to give one moment more
   time, and the planner keeps them for music that sustains or breathes.
4. **Flashes only on request.** The planner places a flash only where the
   direction asks for fast cutting or a flash. A flash then cuts on a steady
   pulse, one shot per step, never on irregular accents. The step is the beat
   divided by 1, 2 or 4, whichever is nearest a quarter second. Within a flash,
   shots that look alike score higher, so the burst reads as one idea.
5. Contracts become `harness-concept-v6` and `beat-lattice-assembly-v3`. Fill
   gaps keeps its earlier behaviour and is not cast.

## Consequences

- Each edit costs one more planner request (text only, cached).
- An edit scoped to one film is built around that film's key moments.
  Library-wide edits still depend on the concept's queries for their options.
- A Rapid edit stays rapid. A slow section has to come from a hold or from a
  Kinetic setting.
- An edit has no flashes unless the brief asks for them.
