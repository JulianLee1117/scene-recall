# ADR-0027: Bounded Match Finder in Visual Rhymes

- Status: Accepted
- Date: 2026-09-11
- Supersedes: ADR-0026 and ADR-0008 only for the order of Lab experiments
- Superseded by: None

## Context

Sparse stills miss useful cut instants, global appearance hides small-region
matches, and captions cannot establish movement direction or speed. The user
approved investigating these failures together in an explicit Lab workflow.
Waiting for production static activation would prevent measuring the actual
played transition that the experiment is intended to improve.

## Decision

Permit bounded decoded-frame refinement and a separate motion baseline in
Visual Rhymes before production static gates pass. This is a narrow exception:
main search activation, library-wide derivation, ANN and always-on query
interpretation remain subject to their existing gates.

`pipeline/matching` owns source/cohort/profile validation and retrieval. Lab
adapts it to durable snapshot jobs, previews and revision-checked application.
A prepared immutable cohort contains at most 200 shots, 600 indexed frames and
80 motion windows across multiple films. Derived profiles are independently
replaceable and content/checkpoint/runtime scoped. Reject missing, stale,
corrupt, incompatible or incomplete evidence; never blend raw vector spaces.

Image matching independently retrieves dense DINOv3 ViT-S/16 candidates and PE
candidates from the same cohort, combines ranks, then decodes at most ten shot
windows (each at most four seconds) for coarse-to-fine refinement. Selected
regions use dense correspondences and bounded spatial windows without object
class gates. The reference instant stays fixed unless an explicit bounded
image-reference window allows alternatives.

Movement uses real local optical flow: RAFT Small C_T_V2 is the working baseline.
Robust affine camera separation produces distinct camera and residual-region
signals. Low-confidence or negligible motion is unknown. Direction, speed and
coarse temporal order affect ranking. Motion compares the preceding second at the marked reference with candidate
forward windows. An explicit reference window permits at most four reference
alternatives before refining ten candidate shots. It does not track object
identity.
WAFT remains an isolated challenger until a held-out comparison justifies it.

Region selection is independent of cropping. Optional whole-picture crop,
translation and uniform scale are bounded to 2x output enlargement. No mirror,
rotation, compositing, generated fill or playback-speed change. Crop/context
loss affects ranking. Crop proposals are suggestions, never source mutations.

Jobs freeze source selection, project revision, cohort and profile identity.
Finding never changes a project. Return at most ten suggestions and eagerly
render proposed/original A-to-B previews for three. Compare decoded boundary
images with the selected source evidence using a recorded compression-tolerant
image check. This check is not proof of editorial quality or exact distinguishable
PTS when neighboring frames look identical. Additional candidates use on-demand preview jobs over the saved suggestion,
without applying it or changing the current project. Application rejects stale revisions
and locked clips, saves a revision, and supports Undo.

## Acceptance and consequences

Mechanics and human quality remain separate. Proposed 12 visual and eight motion
review references are ungraded; freeze human-approved references and held-out
splits before tuning. Compare PE/Framing, dense retrieval, region/refinement,
camera-confounder cases and played transitions. Record useful top-3 rate,
reference/candidate timing, crop cost, latency, memory and rejected/unknown cases.
No automatic promotion follows passing tests or a single working media demo.

This experiment adds bounded local GPU work to the worker. Main search has no
new model call, although concurrent work can contend for the same GPU.
