# Framing blind test, 2026-10-10

Status: judged blind by the owner on the
[review page](https://claude.ai/artifact/SFzywpV2QgeCKCKdHdqXyE), which keeps
their note on every reference. Result: a tie, 5 to 5 with 3 the same. Decision
pending.

## Question

Should Framing take its layout signal from the match-cut moments index
(ADR-0099) instead of the learned 6x6 PE grid? This was the direction
recorded with ADR-0120.

## Arms

- **Today:** production Framing. It ranks appearance candidates 65% by
  appearance and 35% by a learned 6x6 grid, encoding every candidate live
  (4-6 s a search).
- **Hybrid:** the same candidate pool, ranked 50% by appearance and 50% by the
  moments index's measured layout: subject place and size, silhouette, pose,
  eye point, light and lines, with motion ignored. Each shot is shown at its
  best-matching instant, and the layout pass took 0.1-0.4 s.
- **Layout alone** (the moments index searched by layout) was piloted on four
  references and dropped. It matched light and placement but returned
  unrelated content, such as a beach for a road. That kind of match is what
  Match Cuts is for.

## Protocol

There were 13 references, one per composition type, eight results per arm, and
the same per-film and per-scene caps for both. Sides were shuffled per
reference and balanced, with today's set on side A six times and side B seven
times. Nothing on the page named a method. For each reference the owner picked
the closer set, or "about the same".

## Results

| Reference | Composition | Closer |
|---|---|---|
| Titanic | centred figure in a symmetric corridor | Hybrid |
| Dune: Part Two | tiny figure on a dune, seen face-on | Today |
| Blade Runner 2049 | an eye filling the frame | Same |
| Requiem for a Dream | two heads split left and right | Hybrid |
| The Wolf of Wall Street | over the shoulder | Hybrid |
| When Harry Met Sally | frontal two-shot on a sofa | Hybrid |
| Blue Velvet | silhouette in a lit doorway | Hybrid |
| Drive | road to a vanishing point | Same |
| Scott Pilgrim vs. the World | overhead, a figure on the floor | Today |
| Aftersun | figure against the sky, arms out | Today |
| American Beauty | lit window in a dark facade | Today |
| The Handmaiden | face at a window, off-centre | Same |
| When Harry Met Sally | crowd from above | Today |

The split follows what each layout signal measures. Measured layout won where
the composition is the arrangement of people: their size and place, two-shots,
over-the-shoulder, a silhouette in a frame. The learned grid, with more weight
on appearance, won where the composition is the camera's view of the whole
scene or an unusual pose: face-on rather than top-down, lying, leaping, a crowd.

The owner's notes judge size, position, angle and pose. Two of them (the window
and the crowd) also weigh theme, and both of those went to today's Framing.
Without them the hybrid leads 5 to 3. All 13 references have now been seen, so
any tuning needs fresh references.
