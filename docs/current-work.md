# Current work

> Temporary execution checklist, not a specification. `README.md` and
> `docs/search-architecture.md` remain authoritative. Delete this file when
> these priorities are complete or deliberately replaced.

## Current milestone: stabilize modular search for merge

- [x] Resolve stale facet-edit and result behavior.
- [x] Reconcile and activate the complete Framing cache.
- [x] Clean the implementation without changing intended behavior.
- [x] Run focused tests, the full backend suite, the frontend production build,
  and browser smoke checks.
- [ ] Complete the manual interaction checklist.
- [ ] Review the final diff and confirm the prototype is ready to merge.

Exit gate: automated checks pass and the core interface is confirmed through
hands-on use at representative viewport sizes.

### Next-session handoff

This manual pass is the first task after restarting the project. Start the API
and frontend with the README commands, complete every interaction below with
the user at desktop and narrow widths, and record any concrete failure before
changing code. If all checks pass, fetch `origin`, confirm
`prototype/modular-search` is still based directly on `origin/master`, and ask
the user to approve the recommended fast-forward merge into `master`. Do not
hold the merge for the later 10–15-query relevance study; that work belongs on
a fresh branch after this stable baseline lands.

After the manual result is resolved—merged or explicitly deferred—delete this
file and remove its pointer from `AGENTS.md` in the same cleanup commit. If the
remaining post-merge priorities are still useful, move them into a newly
scoped plan before deleting this handoff; do not leave a stale merge checklist.

### Manual interaction checklist

- [ ] Main and facet text search: submit, edit, blur, Enter, and Escape.
- [ ] Scene and uploaded-image clues: add, move, replace, and remove.
- [ ] Independent facet finder, movie scope, and progressive **Show more**.
- [ ] Bookmark actions and playback from both result cards and the player.
- [ ] Desktop and narrow viewport layout, including drag targets and overflow.

### Expanded-library batch closeout (2026-09-01)

- The library now contains 38 indexed films and 45,980 shots. Pulp Fiction's
  1,330-shot ingest completed after one title card used an explicitly
  prompt-versioned no-transcription fallback; its other 1,329 annotations use
  the normal profile.
- Eleven completed release folders were moved intact from `incoming` to
  `V:/scene-recall/evidence/imported-releases/`. No raw release or timestamped
  subtitle evidence was deleted.
- The complete Framing cache is active across the 116,962-frame generation;
  the post-batch reconciliation reused 79,182 descriptors and embedded 37,780.
- Wild Strawberries now uses a reviewed 941-cue English SRT aligned to its
  embedded PGS track. A targeted cached reingest replaced the Swedish Whisper
  text, and an exact Words query returns the repaired dialogue first.

### Pre-expansion discovery evaluation baseline (2026-08-31)

- The bounded discovery rank is active for ordinary unscoped search. On the
  then-current 27-film library, `nature` improved from 4 to 7 films in the
  first 12 and from 10 to 13 in the first 48. It is a soft repeat cost, not a
  one-per-film quota, so multiple genuinely strong scenes from one film remain
  eligible.
- The same bounded repeat rank is active for unscoped modular recipes without
  a mandatory uploaded-image or Framing gate. For Tree of Life beach source
  unit `61ff742b..._1313`, the bounded set already contained 168 eligible
  scenes from 23 films, proving that its concentration was ordering rather
  than library size or candidate recall. The source film changed from four of
  the first five results to one of the first five and two of the first 12;
  first-12 film representation changed from 8 to 11. Grade more source scenes
  before changing the shared strength of 32 or adding a source-film rule.
- The exact Titanic reference previously reached only 7 films at the
  uploaded-image candidate gate. The active bounded visual reserve reaches 19
  films for both Look and Framing (6 in the first 12) while selectively
  hydrating at most 212 units. Human-grade 10–15 varied references before
  changing its depth, reserve size, or default strength.
- Current same-process timings were about 8.3 seconds for a cold Look request,
  0.34 seconds warm, and 0.52 seconds for Framing. Optimize measured hydration
  and model warm-up before considering ANN.
- Keep distinct strong scenes eligible, and treat same-moment or near-identical
  results as expandable groups rather than destructive removal. Future
  discovery and deduplication controls belong in the unified result-control
  surface, not as more standalone switches.

## Ordered next priorities

1. Close the expanded-library merge gate: finish the manual interaction
   checklist, smoke-test playback and search for the newly indexed films,
   rerun the release checks, and review the final diff.
2. Human-grade 10-15 representative main-text, Scene, Words, Mood, uploaded
   Look, and Framing searches on the 38-film library. Classify misses as
   candidate recall, final ordering, same-moment duplication, or missing
   evidence, and record cold/warm latency.
3. Reduce measured retrieval and hydration overhead without changing ranking.
   Start with lightweight candidate/evidence rows, hydrate final scenes once,
   and warm Qwen at API startup; remeasure before considering ANN.
4. Design one unified result-control surface that subsumes the current movie
   scope instead of accumulating standalone switches. Use the evaluation to
   decide which backend-owned controls are justified for relevance versus
   cross-film discovery, near-duplicate grouping, and film inclusion; keep
   advanced choices behind one calm surface and preserve a strong default.
5. Replace the API-memory ingestion queue with a durable standalone worker
   before the next large batch. Preserve serialized GPU access and resumable,
   profile-scoped caches; this boundary change requires an ADR, architecture
   contract update, and matching README commands.
6. Expand and human-grade the grounded Match Cut shadow experiment before any
   product activation. Add exact-frame refinement only if its frozen quality
   and latency gates pass; keep Motion Match separate.
7. Add a versioned narrative evidence profile only if the evaluation proves
   that contextual evidence is missing.

The Match Cut item is the next material retrieval experiment already admitted
by the architecture contract. Stabilization and measurement come first without
changing that boundary.

## Deferred unless evidence changes the decision

- ANN indexing.
- LLM query decomposition or routing.
- More facets or a generic filter language outside the unified control design.
- Standalone diversity or deduplication controls outside that design.
- Library-wide every-frame indexing.
- Motion Match.

## Maintenance

Update this file only when priorities or milestone state deliberately change.
Move accepted behavior and architectural boundaries into their authoritative
documents and, when required, an ADR. Delete this entire file and its
`AGENTS.md` pointer when the plan is complete; Git already records the history.
