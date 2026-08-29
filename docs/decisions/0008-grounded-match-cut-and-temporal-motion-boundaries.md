# ADR-0008: Grounded Match Cut and temporal Motion Match boundaries

- Status: Accepted
- Date: 2026-08-29
- Supersedes: None
- Superseded by: None

## Context

The production composition workflow compares PE features plus a learned 6x6
spatial grid. That is useful for broad framing, but it does not explicitly
represent subject identity, count, scale, orientation, articulated pose,
relations, or negative space. Inspected tight-profile references demonstrate
both failure classes: useful matches can fall outside the bounded PE/spatial
candidate window, while frontal or motion-confounded people can rank ahead of
closer geometric matches.

The retained keyframes also sample only a few instants within each shot. They
can identify a promising shot while missing the best alignment during a head
turn, gesture, or camera move. A single frame cannot encode movement direction,
subject trajectories, or camera motion at all.

Calling the existing workflow "Match Cut" would therefore overstate its
evidence. Indexing every decoded frame would instead multiply storage and
backfill cost before the grounded matcher has demonstrated useful quality.

## Decision

Keep the current production feature as **Framing**: coarse global appearance
and position similarity over the existing PE and spatial evidence. Its API
facet remains `composition`, and ADR-0003, ADR-0005, and ADR-0007 continue to
govern its mandatory-candidate, cross-film, and recipe behavior.

Develop still-frame **Match Cut** as a separate, shadow-only, versioned grounded
layout profile. A profile row describes the active picture and normalized,
salience-ordered entities, including class or family, box, silhouette, and only
the pose or screen-orientation evidence that its extractor can support. A
zero-detection frame still receives an explicit row. The profile also owns any
coarse retrieval vector and exact layout-scoring contract.

Candidate generation unions independent rankings from the grounded-layout
profile and the legacy PE profile by stable frame identity. Raw scores from the
two vector spaces are never combined. A model-independent exact layout scorer
then ranks the pooled shortlist by the human-visible criteria: subject/object,
normalized position, scale, viewpoint/orientation, pose, and relations or
negative space. Match Cut keeps the cross-film discovery default unless an
explicit film scope says otherwise.

The sparse indexed frames are candidate-recall evidence, not necessarily the
final cut instant. After the static grounded profile passes its promotion gate,
an exact-frame refiner may decode a bounded set of top candidate shots from the
retained source films. It searches coarse samples and then a local neighborhood
around the best sample, returns the actual decoded presentation timestamp, and
caches only replaceable profile-scoped derivations. The server resolves film,
unit, source path, and legal time range; it never trusts a browser-supplied path
or vector. This does not create a library-wide every-frame index.

Treat **Motion Match** as a different future workflow and versioned profile.
Its query and candidates are short windows around source-backed timestamps. Its
evidence uses optical flow, estimated global camera motion, and tracked
subject/object trajectories after that camera motion is removed. It has its own
lineage, manifest, human action-heavy cases, latency budget, and activation
decision. A still Match Cut or Framing result is never presented as a motion
match.

Every grounded-layout profile manifest records exact extractor model IDs,
immutable revisions or weight hashes, library versions, preprocessing and
active-picture contract, thresholds and label mapping, layout schema, vector
contract and dimensions, scorer versions, input frame generation,
frame-identity coverage digest, and expected and completed row counts. It
becomes complete only when every frame in the target published generation has
a row, including zero detections. Missing,
partial, stale, corrupt, or incompatible data disables Match Cut as a whole;
the product must not silently substitute Framing under the Match Cut label.

Promotion from shadow to product requires all of the following:

1. Expand the human-owned set to 10-15 representative references and freeze a
   12-reference acceptance slice spanning profiles, full-body pose, object and
   multi-subject relations, scale, orientation, and negative space. The two
   initial Dune cases are diagnostic seeds, not an acceptance corpus.
2. Against the current Framing baseline, win the blinded side-by-side choice on
   at least 8 of 12 references, improve median per-case nDCG@10 by at least 20%
   on fully judged pooled top tens, and regress on no more than two cases. Every
   initial Dune case must improve or hold, and the tight-profile case must place
   at least three judged positives with grade-2 or grade-3 geometry in its top
   ten.
3. Meet an observed warm p95 query latency below 250 ms for candidate union and
   static layout reranking on the target hardware.
4. Reconcile a complete compatible manifest against the current published
   frame generation and deliberately select that profile. Shadow rows or a
   partial backfill never influence production ranking.

Exact-frame refinement and Motion Match require their own human comparisons
before they change product results. The exact-frame comparison must judge the
returned instant, not merely the containing shot. The motion comparison must
include direction, relative subject motion, and camera-motion confounders; a
win by shared appearance alone does not count.

## Consequences

- The existing Framing workflow remains honest, fast, and available while the
  more exact matcher is evaluated.
- Candidate recall and geometric ordering can improve independently without
  mixing incompatible vector scores or rebaking canonical film records.
- Retained source media makes exact timestamps possible without an exhaustive
  frame index, at the cost of bounded decode work and a replaceable cache.
- Static geometry and temporal motion cannot hide one another's missing
  evidence or fallback behavior.
- Match Cut and Motion Match remain unavailable until their respective
  profiles, evaluations, and manifests pass the accepted gates.
