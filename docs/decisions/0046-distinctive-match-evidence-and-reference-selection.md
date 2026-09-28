# ADR-0046: Distinctive Match evidence and automatic reference selection

- Status: Accepted
- Date: 2026-09-13
- Supersedes: ADR-0040's cue-rank fusion and strongest-cue automatic ordering

## Context

The user reported the same Stargate footage for different centered characters.
Inspection of four saved automatic searches found position primary in all four
top results and nine of twelve top-three entries. The exact EEAAO search chose
right-side background instead of the character, then recommended a full-height
right-side light band from 2001. Position strength .786 and an unconditional
second-cue bonus beat subject movement strength .439. The silhouette strength
was only .201. Lily Chou-Chou similarly selected the sky. Pulp Fiction's selected
hand and Mirror's selected man were useful controls against indiscriminate
rejection of cropped subjects.

The legacy shape scorer includes centroid position in addition to foreground
mask overlap. Position and shape receive separate shortlist votes and another
bonus at final ranking. Nearly filled bounding-box masks become generic shape
templates, while the most salient automatic reference can be background.

## Decision

Version scene search as `scene-match-distinctive-exact-pair-v3`. Keep prepared
SAM/RAFT evidence, extraction profiles, legacy Lab scoring, source media and
exact decoded-boundary rendering intact. All changes consume existing summaries.

Choose an automatic reference from the retained tracks using mask stability,
area and center proximity, retaining the full-span factor of 0.2 and applying
0.65 for picture-border contact. Border contact is a preference, not a ban on
close-ups, hands, off-center subjects or other cropped details. Explicit point
or box selection bypasses this automatic choice. This does not identify people
or guarantee that the intended subject is among the retained masks.

Search shape evidence balances foreground IoU and negative-space IoU with their
geometric mean. Require foreground overlap at least 0.6, negative-space overlap
at least 0.5, balanced overlap at least 0.6, and physical aspect agreement at
least 0.5. Aspect and scale support the score with weights 0.75 and 0.25;
centroid position contributes nothing. A mask with fewer than three empty-cell
equivalents in its 8x8 summary can qualify only against another such solid shape,
both contained inside the picture. Thus real interior rectangles remain useful
while clipped filled bands cannot establish a complete silhouette.

In automatic ranking, position contributes at most 0.25. Take the maximum
within each static (shape, position) and temporal (subject, camera) family, then
the stronger family plus 0.1 times the weaker. These are explicit ranking
heuristics, not calibrated confidence. They remove duplicate credit within a
family and make independent support proportional to its strength. Explicit
focus uses only its requested cue's strength. Position-only discovery remains.

Use the same policy for coarse exact-pair proposals and final refinement.
Deduplicate each shot by its best pair's score and seed. Multiple cue names,
different timestamps or different masks no longer buy extra retrieval votes.
Keep five initial and at most ten unique refinement windows. Do not disguise
poor relevance by blacklisting particular films, adding randomness or expanding
preparation. Explanations refer to measured selected regions without asserting
that an automatically segmented region is a person.

## Verification and limits

Regression cases cover the actual EEAAO region mistake, preservation of cropped
details and explicit selection, common-position/weak-shape ranking, correlated
cue support, outline agreement and solid-region failure modes. Replays use the
existing bounded prepared cohort and the four saved references. Report source
selection, retrieval changes and played-boundary verification separately from
human editorial usefulness. The four references are development cases; broader
recall, held-out usefulness and library expansion remain gated by ADR-0038/0040.
