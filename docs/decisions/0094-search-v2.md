# ADR-0094: Search v2 — evidence views fused by rank, quotes, bounded priors, resident vectors

- Status: Accepted
- Date: 2026-09-28
- Supersedes: ADR-0086, ADR-0088 and ADR-0091 (intent-routing experiments,
  retired); the exact sequential semantic scan; stills-guessed camera movement
  as searchable evidence
- Builds on: ADR-0093 (evidence v2)

## Context

With evidence v2 in place (story, scenes, characters, fame and craft from the
understanding pass; measured camera, subjects and look; synced subtitles),
the remaining search problems were ordering and cost:

- a view that says exactly what was asked (a one-line story) lost to long
  visual captions, because views were collapsed by raw cosine distance, and
  distances are not comparable across document styles;
- quotes were stemmed and stop-worded away by the unit full-text index
  ("you talkin' to me" is almost entirely stop words);
- nothing distinguished the iconic moment from ordinary coverage;
- flat Lance scans cost about a second per channel (4.9 s per query).

The personal eval set (`pipeline/eval/searchset.yaml`) measured each change.

## Decision

1. **Views v3.** Semantic text views gain `story` (action, characters, iconic
   note, setting) and `scene` (scene summary shared by its shots); `mood` uses
   the understanding pass's emotion, scene tone and sound where present;
   `facets` carries measured camera movement (with a "slow" qualifier for
   sustained drift) and never the stills-based guess.
2. **Rank fusion inside the text channel.** Each view ranks independently and
   votes by weighted reciprocal rank; the view where a shot ranked best is its
   displayed evidence.
3. **Quote channel.** Subtitle lines are compiled into `dialogue_lines` and
   indexed on normalized text with positions, no stemming and stop words kept.
   Phrase and term hits are re-scored by ordered token overlap over a line and
   its neighbouring cues. Quote-like queries weight this channel up and keep
   half-strength priors, so exact lines lead and fame decides between films
   that share a line.
4. **Bounded priors and presets.** After relevance, a prior multiplies a
   shot's rank-derived relevance by at most a small factor: iconic and
   well-crafted shots rise within the relevant pool and are never pulled in
   from outside it. Presets: balanced (default), famous, hidden gems. Shots
   without evidence are neutral.
5. **Presentation.** One card per dramatic scene with its other matching shots
   as alternatives; hero-frame thumbnails through a display-only
   `thumbnail_url` (the keyframe fields keep naming the exact frame used when a
   result becomes a search source); badges, story line, scene title and the
   matched subtitle line with exact times.
6. **Resident vectors.** Text views and frames are searched exactly over
   float16 matrices loaded from the request's pinned snapshot, keyed by table
   and version (GPU when there is room, CPU otherwise, Lance as fallback).
   A table version holds one vector space, so resident search cannot mix
   profiles.
7. **Cross-encoder rerank.** Qwen3-Reranker-0.6B reads the query and each
   shortlisted shot's evidence (film, scene, action, visual caption,
   dialogue) and is blended with the fused rank inside the shortlist
   (`retrieval.rerank_shortlist`).
8. **Retirement.** The Jev/intent-routing comparison (API route, web panel,
   experiments and tests) and the exact sequential scanner are removed.

## Consequences

- Eval (5 pilot films with evidence, 27 known items): MRR 0.29 → 0.89 without
  and 0.96 with the rerank; hit@1 4 → 26. Median latency 4.9 s → 1.7 s; the
  rerank of a 40-shot shortlist adds about 0.25 s on the GPU.
- Evidence films are favoured by story and scene views until the library
  backfill completes; the effect disappears once every film has evidence.
- The API process holds about 2.7 GB of resident vectors; new film
  publications load the next table version on first use.
