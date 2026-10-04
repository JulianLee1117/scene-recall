# ADR-0096: Editor harness v2 — measured music, evidence pools and beat-lattice assembly

- Status: Accepted; amended by ADR-0103 (free paces, flash and hold moves, the default editor)
- Date: 2026-09-28
- Supersedes for v2 regeneration: ADR-0060 (LLM numeric cut timing), ADR-0041 and
  ADR-0058 (source-aware first timing through text selection)

## Context

Whole-edit regeneration asked language models for everything:
- the listening pass proposed moments;
- a timing request returned cut frames;
- per-group planning wrote a search for every shot;
- a selector picked sources from caption text and chose where to trim them.

Models are weak at exact timing, and the selector never saw measured motion or
where an action peaks. Evidence v2 (ADR-0093) now provides the missing
measurements for every shot:
- action peak times;
- measured camera segments;
- main subject position at the start and end of the shot;
- motion, hidden cuts, fame, craft and scene membership.

## Decision

Harness v2 keeps models for meaning and measures the edit:

1. **Music map** (local, deterministic):
   - Beat This! beats and downbeats;
   - a band-wise spectral-flux onset envelope, with accents as its salient peaks;
   - loudness per beat;
   - the listening pass's sections, snapped to downbeats.

   Section energy from listening sets the absolute level, because percentiles
   within a passage cannot tell a calm song from a loud one.
2. **Concept and arc** (one planner request, cached by its full input). The user's
   direction, the song's meaning and the sections become one act per section, each with:
   - a visual intent;
   - one to four search-v2 queries;
   - a fame target (anchor, fresh or any);
   - a pace.
3. **Pools**: each act's queries run through ordinary search. Candidates carry
   their compiled shot evidence and image embedding.
4. **Assembly**: a lattice beam search over the beat grid chooses cuts, shots and
   source windows together.
   - **Grid:** beats, plus half-beats and strong accents at fast paces, snapped to frames.
   - **Screen time** carries relevance, motion matching intensity, craft and the fame target.
   - **Per shot:** the action peak landing on the span's strongest accent, and the
     distance from a pace target scaled by intensity.
   - **Transitions:** eye-trace and screen-direction continuity, and penalties for the
     same scene, the same film back to back, and jump cuts.
   - **Variety:**
     - image similarity to recent shots;
     - reuse of a visual cluster anywhere in the edit;
     - growing reuse of a film.
   - **Constraints:** hidden cuts and near-black stretches (fades, compiled from
     measured brightness) are never used; locked shots are fixed spans.
5. **Sequence review** (one planner request): the model sees the assembled edit with
   pre-timed alternatives per slot. It may swap shots for flow, visual rhymes,
   direction fit or variety. Timing never changes.
6. Optional critique (`lab.harness_critique`): Gemini watches a rendered rough cut
   with its audio. Its timestamped issues ban shots or rescale an act's pace for
   one re-assembly.
7. The result is the ordinary project document:
   - AI slot directions with resolved searches and up to six alternatives;
   - a timing receipt the existing timing panel reads.

   The worker still performs the single checked revision write.

**Fill gaps** under v2 keeps every cut and placed shot:
- placed shots become fixed neighbours for continuity;
- the edit's v2 concept is reused when it matches the passage;
- a slot with a user-written search gets its own pool from that query;
- the optimizer picks shots and windows for the empty slots.

Targeted replacement and next-scene suggestions keep the v1 path. OpenTimelineIO export
(`GET /lab/projects/{id}/timeline.otio`) hands any saved edit to DaVinci Resolve
on the original media.

## Consequences

- Regeneration makes two planner requests instead of one timing request plus one
  planning and one selection request per 32-shot group. A 33-second passage
  regenerates in about 100 s instead of about 250 s.
- The same inputs always give the same assembly. Receipts record the concept, the
  review's swaps, the music-map summary and the assembly contract.
- Blind judging on 2026-09-28: Gemini watched each render alone with audio at
  4 fps and scored it 1–10, twice. Fresh v1 against v2 on three of the owner's
  projects (overall):

  | Project | v1 | v2 |
  |---|---|---|
  | `everything` | 6.0 | 6.0 |
  | `In My Head` | 6.0 | 6.5 |
  | `101` | 6.0 | 6.0 |

  v2 regenerates 2–4x faster (a 109 s rap passage: 169 s against 661 s).
  - The judge credits actions landing on accents and stronger imagery.
  - Both versions draw notes on mixing film stocks and aspect ratios; later
    continuity terms address this.
  - The critique loop re-assembled every edit but gained nothing measurable, so
    it stays off by default.
  - A side-by-side judge preferred whichever edit it saw first, so it is not
    used.

  v2 stays opt-in until the owner has compared edits on their own projects.
- Shots without evidence stay usable with neutral values, so the harness works
  during and after the library backfill.
