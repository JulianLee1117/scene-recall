# ADR-0026: Region-aware Visual Rhymes research

- Status: Accepted
- Date: 2026-09-11
- Supersedes: None (extends ADR-0008's shadow research boundary)
- Superseded by: ADR-0027 for Lab experiment ordering only; production gates unchanged

## Context

Sparse keyframes can miss an editable match inside a promising shot. Full-frame
position and scale penalties can also reject useful pairs when a smaller
object could be aligned by cropping and uniformly resizing the picture.

## Decision

First build a human oracle using fixed reference instants or bounded source
windows on either side of a cut. Compare existing sparse frames with manually
chosen instants and audition actual A→B transitions, retaining surrounding
handles. This measures the possible improvement without activating automatic
exact-frame search before ADR-0008's static gates pass.

Add class-agnostic region alignment as shadow research. Proposed transforms
operate on the whole picture: crop, uniform scale and translation. Preserve
source regions and transforms; penalize crop/context loss and inadequate output
resolution. A geometrically valid crop is not proof of a good played transition;
stability and editorial judgments remain separate. Object cutouts, independent
object movement and generated fill are excluded.

Compare candidate recall independently from geometric ordering. Later, one
pinned DINOv3 dense-geometry challenger may run on the same representative
shadow subset, with its own descriptor space and manifest. No global download,
backfill, activation or score mixing is implied. Exact decoded-PTS search inside
shortlisted windows still follows the existing static promotion gates and a
separate temporal comparison.

## Consequences

- Manual pair audition and offline crop proposals can be useful immediately.
- A resize-assisted match can be investigated even when the current full-frame
  matcher excludes it; candidate recall is an explicit metric.
- ADR-0008's 12-case human, latency, completeness and activation requirements
  remain unchanged. A static frame match is never labeled Motion Match.
