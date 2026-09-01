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

### Manual interaction checklist

- [ ] Main and facet text search: submit, edit, blur, Enter, and Escape.
- [ ] Scene and uploaded-image clues: add, move, replace, and remove.
- [ ] Independent facet finder, movie scope, and progressive **Show more**.
- [ ] Bookmark actions and playback from both result cards and the player.
- [ ] Desktop and narrow viewport layout, including drag targets and overflow.

### Discovery evaluation baseline (2026-08-31)

- The bounded discovery rank is active for ordinary unscoped search. On the
  current 27-film library, `nature` improved from 4 to 7 films in the first 12
  and from 10 to 13 in the first 48. It is a soft repeat cost, not a one-per-film
  quota, so multiple genuinely strong scenes from one film remain eligible.
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

1. Evaluate representative real-library searches and classify misses as
   candidate recall, ordering, or missing evidence.
2. Design one unified result-control surface that subsumes the current movie
   scope instead of accumulating standalone switches. Use the evaluation to
   decide which backend-owned controls are justified for relevance versus
   cross-film discovery, near-duplicate suppression, and film inclusion; keep
   advanced choices behind one calm surface and preserve a strong default.
3. Reduce measured retrieval and hydration overhead without changing ranking.
   Start with lightweight candidate/evidence rows, hydrate final scenes once,
   and warm Qwen at API startup; remeasure before considering ANN.
4. Expand and human-grade the grounded Match Cut shadow experiment before any
   product activation.
5. Add a versioned narrative evidence profile only if the evaluation proves
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
