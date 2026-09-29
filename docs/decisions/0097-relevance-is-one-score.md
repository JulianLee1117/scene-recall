# ADR-0097: Relevance is one score through ordering

- Status: Accepted
- Date: 2026-09-28
- Refines: ADR-0094 (the cross-encoder blend, query signals, priors and film
  diversity of search v2)

## Context

With the whole library searchable (157 films, 193k shots), the personal eval
scored MRR 0.82: 23 of 30 known-item queries at #1 and three missed. Each
non-#1 query was traced through every stage of the real search call. None
needed a query-specific fix; each loss had a general cause:

| Query | Lost at | Cause |
|---|---|---|
| scraping peach fuzz with a razor blade | priors | #1 in every channel and judged p 0.998. The balanced fame prior lifted Goodfellas' iconic garlic slicing (judged 0.934) above it, because priors scaled a rank-derived relevance that cannot see how sure the judge was. |
| neo stops the bullets in mid air | judge blend, film diversity | The judge rated the target shots highest (p 0.998-0.999), but probabilities saturate: a minigun shot at 0.968 looked close, so blending with the fused rank lifted the targets only to 4th. The film-repeat penalty then pushed the third Matrix card from #3 to #10, although "Neo" names the film. |
| woman in the red dress | signals, priors | #1 after fusion and the judge; the colour and fame lifts, each applied over ranks, put Shosanna's red gown first. |
| they pass each other in slow motion in the rain | candidate generation | The scene summary is exactly the query, but fusion rewards agreement across views, so the shot was cut before the judge (which rates it p 0.999) read it. Shots sharing one summary were also ranked against each other in arbitrary order. |
| sheltering from the rain under the eaves | eval | Search returned the right scene first; the eval's time was mid-scene. Frames show the scene opens at 1:07:44 on exactly the described shot. |
| lobby shootout; a face slowly rising from the dark basement stairs | none | Ambiguous (The Grand Budapest Hotel has a real lobby shootout, ranked first); first-stage recall (the story line ranks 228th in its view). Not ordering faults. |

## Decision

Each candidate carries one relevance score from fusion to the end of ordering.
Later stages scale that score by bounded factors instead of shifting ranks.

1. **Seed.** Relevance starts as the candidate's fused score relative to the
   best candidate's.
2. **Judge.** Qwen3-Reranker returns log-odds. The verdict is read on a fixed
   scale, linear in log-odds and saturating at p 0.001 and 0.999, and blended
   0.6/0.4 with the fused score. Candidates the judge did not read keep only
   their fused evidence.
3. **What the judge reads.** The fused shortlist, plus the three best matches of
   every channel and of each precise text view (caption, story, dialogue,
   scene). Identical documents tie: every shot of a scene shares its summary,
   so those shots share one rank. A shared document sends its three
   best-evidenced shots.
4. **Signals and priors.** Query signals (names, scale, camera, time, colour)
   and preset priors multiply relevance by their existing bounded factors. A
   prior settles a near-tie (between two equally good matches, the iconic one
   leads) but cannot overrule a clearly stronger match. Recipe results carry
   no score, so they keep the rank-derived form.
5. **Diversity.** Film diversity spares the films a query names by title,
   character or actor. A named film is an implicit scope, just as an explicit
   film scope already disables the penalty.

Two eval items were corrected, each from independent evidence:
- the shelter scene gains its opening shot (confirmed from frames);
- "agent smith says mr anderson" lists all ten of Smith's lines (confirmed from subtitles).

## Evidence

Before the change, a held-out set was added and baselined: 25 known-item
queries on 23 other films, phrased from memory rather than from their evidence.
Their targets were located in the evidence and spot-checked against frames.

| Set | Before | After |
|---|---|---|
| Design, 30 items | MRR 0.820; 23 at #1; 3 missed | 0.950; 28 at #1; 1 missed |
| Design, excluding the two corrected items | 0.843 | 0.946 |
| Held out, 25 items | 0.927; 23 at #1 | 0.962; 24 at #1 |

No held-out query regressed. The production path matches: a pinned snapshot
with resident GPU matrices and the serving budget on scores MRR 0.956, 52 of 55
at #1. Search latency is unchanged (0.99 s median).

In steady state, the judge reads a median of 51 shots (maximum 60) in 0.44 s
median (0.70 s maximum), inside its one-second budget with no overruns.

What remains:
- the basement face query (recall);
- the lobby shootout (ambiguous, #2);
- one held-out query at #17: photographing sunlight through the trees.

## Consequences

- The debug payload carries `relevance`, plus the judge's `log_odds` and
  `verdict`.
- Balanced priors act less where the judge is sure and more on ties. That is
  closer to "relevance first" and to the owner's reservations about popularity
  steering search.
- Deferred, each with a concrete failure:
  - full text over the understanding pass's story and scene text, since the
    basement query's evidence is invisible to the full-text channel today;
  - any change to the blend weights, which first needs a larger held-out set.

## Alternatives considered

- **Lower the balanced prior strength.** This fixes the peach fuzz query by
  tuning and leaves the cause in place.
- **A log-linear model**, adding the verdict and fusion in log-odds. It is the
  cleanest mathematically, but the preset multipliers would lose their meaning
  and would need recalibrating without evidence.
- **Judge every shot of a matched scene.** Exact, but the cost grows without
  bound for long scenes.
