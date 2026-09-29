# ADR-0101: Fold look-alike shots into their card, and measure ordinary moments blind

- Status: Accepted
- Date: 2026-09-29
- Refines: ADR-0094 (visual deduplication and scene cards) and ADR-0097 (evaluation)

## Context

Every known item in the search eval targeted an iconic moment. So a blind set
was added: 30 random non-iconic shots from 30 films (seed 2026). Each query was
written from three frames of the shot, without its title or evidence text, and
the queries were frozen before any search ran. The set scored MRR 0.43, with
11 misses.

In 9 of the 11 misses, the right film and scene ranked first but the exact shot
was gone. Visual deduplication removes a shot whose picture nearly repeats a
better-ranked one: cosine at least 0.92, or at least 0.90 within 30 s in the
same film. It ran before scene grouping, so it removed the target whenever a
film repeats a look:
- The Tree of Life's recurring river shots dropped the one the judge rated
  highest.
- 2001's reverse angles in the pod dropped the targets from their own scene.
- Portrait of a Lady on Fire's red dress dropped the target in favour of a
  shot from another scene.

Deduplication predates scene cards. It threw shots away where grouping would
have folded them in.

The first runs of this set were also silently degraded. When another process
fills the GPU, the judge rests and semantic text can fall back. That is right
for serving, but it wrong-footed the eval: a run with the judge skipped for all
95 queries reported normally.

## Decision

1. **Same-film look-alikes fold into the card.** A near-identical shot of the
   same film folds into the card it resembles, as one of the card's other
   matching shots, capped at 8 in relevance order. It moves with that card
   through scene grouping. Near-identical shots from other films are still
   dropped, because the player seeks within one film. The grid is unchanged.
2. **Neutral labels.** The card and player call these "more matching shots",
   not "more in this scene".
3. **Evals measure ranking, not machine load.** With no time budget, the judge
   also ignores the full-GPU rest. A run reports how many queries lost the
   judge or semantic text, and warns that such a run does not compare with
   others. Each hit records whether it was the card's own shot or found inside
   the card.

## Evidence

Clean runs, with the judge on every query:

| Set | Before | After |
|---|---|---|
| Blind ordinary moments, 30 | MRR 0.433; 11 at #1; 11 missed | 0.667; 18 at #1; 4 missed (12 hits found inside the card) |
| Original and held-out sets, 55 | 0.956; 52 at #1 | 0.956; 52 at #1 |

The four remaining misses are ambiguous. In three, the right film ranks first,
but eight more relevant look-alikes fill the card; Marianne wears that red
dress in dozens of shots. The fourth, "a blond woman arguing in a kitchen",
fits several films.

## Consequences

- **What users see.** A card's thumbnail is its most relevant shot, and the
  remembered moment may sit one click inside it.
- **Judge saturation.** The judge's log-odds scale saturates at ±7, so one
  target rated 7.95 tied a neighbour rated 7.09. Recalibrating that scale is
  deferred until a larger set shows it matters.
- **Remaining ordinary misses.** They come from evidence gaps: a caption that
  says "monitor" where the query says "basketball on TV", and abstract close-ups.
