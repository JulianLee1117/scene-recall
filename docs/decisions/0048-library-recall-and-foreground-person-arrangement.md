# ADR-0048: Library recall and foreground person arrangement in Match search

- Status: Superseded by ADR-0099
- Date: 2026-09-13
- Extends: ADR-0008 and ADR-0027 only for bounded, explicit Lab experimentation
- Supersedes: ADR-0040 and ADR-0046 only for automatic scene-search recall scope and detected-person evidence
- Superseded by: None

## Context

The user selected the two foreground dancers in Pulp Fiction and received five
results justified primarily by camera movement, with none preserving the pair.
Inspection of the exact native reference at 48:20.606 found that all three
retained SAM masks described background strips or decoration. Its 3x3 point
prompts had missed both dancers. Reweighting those masks or comparing every
retained track could not recover the absent people.

The candidate pool imposed another independent limit: 80 prepared windows from
eight films, against 45,980 indexed shots and 116,962 retained keyframes from
38 films at the time of inspection. A bounded query against existing PE frame
vectors found unprepared dance and paired-person shots. This establishes a
concrete recall failure without justifying detection on every library frame.

The initial foreground-layout scorer also required physical box-aspect
agreement, making arm width an unintended pose requirement. One of twelve
diagnostic candidates passed despite several having two similarly placed
people. Subject arrangement and silhouette need distinct evidence contracts.

## Decision

Version the explicit scene Match search as
`scene-match-library-people-exact-pair-v4`. Keep one automatic search action,
deterministic explanations and actual A-to-B previews. The workspace remains a
Lab experiment, with a Labs link and no navigation back to ordinary Scene
Search. Exclude the source film by default; explicit inclusion permits other
non-overlapping shots. Saved focused jobs remain readable, and explicit API
focus constraints remain available.

With the existing ready Match cohort and compatible SAM/RAFT evidence retained,
preparing the optional person detector opts automatic scene search into recall
over available published films using the existing PE frame index. The library
metadata extends that existing cohort entry; the detector alone does not create
a ready unified search mode. Freeze its
visual-encoder identity, frames/units/films table generations and available film
scope with the job. Use the nearest retained reference keyframe as a proposal
vector; apply film scope before retrieving at most 600 frames and keeping at
most 200 unique eligible shots. These indexed timestamps propose source windows;
they are not asserted to be native cut PTS or person-layout evidence.

Use torchvision's official Mask R-CNN ResNet-50 FPN v2 COCO person model as a
separate local derivation. `python -m pipeline.matching.people prepare` is the
explicit download/preparation action. Verify the official hash prefix and the
pinned full SHA-256; runtime constructs with `weights=None` and
`weights_backbone=None`, then loads the verified local checkpoint with
`weights_only=True`. Runtime never downloads a checkpoint. Its manifest freezes
checkpoint, runtime versions, device, preprocessing/resize, precision, class
mapping, score/mask thresholds, descriptor and selection policy. The current
complete-group policy uses `matching/models/people-maskrcnn-v2`; prior v1
profiles and cached descriptions remain isolated. SAM/RAFT
profiles and legacy Lab scoring remain intact.

Describe people in full-source normalized coordinates using mask box, centroid,
area, picture aspect and an 8x8 foreground silhouette. Keep person detections
with score at least 0.7 and sufficient mask support. Retain the complete salient
group by descending mask area: every person whose area is at least one quarter
of the largest person's, within the model's 100-detection bound. Matching
supports groups of one to three. Larger foreground groups cause an explained
unsupported result, never a truncated group or camera fallback. Record other
detections without treating a
background audience as the foreground group. Detection and salience can still
fail; this is not a guarantee of semantic identity or perfect person count.

When the automatic reference has selected people, screen at most 48 retained
candidate stills. Require the same selected count and use loose center/height
support to keep promising nearby windows. Refine at most ten unique windows,
initially five and extending only when fewer than three candidates survive.
Check at most four native candidate samples per window and at most four
outgoing instants within the existing nearby/fixed timing contract. Nearby
references must preserve the anchor's selected count.

Final person-arrangement evidence requires the entire selected group on both
sides. Permutations account only for detector ordering: one person cannot
replace two, and a convenient pair cannot be extracted from a selected group of
three. Each assigned center must differ by at most 0.15 normalized picture units;
each smaller/larger person-height ratio must be at least 0.60. A mask-area ratio
of at least 0.25 is a gross-size guard. Every pair's spacing vector must differ
by at most 0.15, and smaller/larger spacing must be at least 0.50. Strength is
the weakest center, height or spacing agreement. These are development ordering
heuristics, not confidence probabilities.

Physical aspect and mask area remain diagnostic measurements. Arm width and
silhouette do not gate arrangement or determine its strength. Add silhouette
evidence only if every assigned person's outline passes the separate shape
check, without an additional ranking vote. Explain when a pair is closer
together or farther apart. Static people evidence makes no claim about pose,
dance step, action identity, phase or motion continuity. Camera similarity
cannot rescue a failed people arrangement; return no people candidate when its
requirements fail.

For an explicit point/region or a reference with no detected people, retain the
existing generic region/shape/motion checks after broad recall. Without the
optional detector manifest, automatic discovery keeps the prepared-cohort path;
explicit nonautomatic API modes also keep prepared coverage. An incompatible
present manifest fails validation instead of silently changing a queued job.
The existing compatible cohort and subject/motion evidence remain prerequisites
for the fallback paths.

Cache person descriptions independently by exact RGB pixels and dimensions plus
detector profile, with a result checksum. Validate source identity before native
refinement, retain actual decoded last-A/first-B PTS and legal handles, and reuse
the existing verified preview cache contract. Reject changed queued library or
model identities, and recheck library generations before publishing results.
Discovery and audition create no project changes or timeline placement.

## Diagnostic verification and limits

The initial v1 detector probe of the exact Pulp Fiction reference produced 19
person detections and selected the two foreground dancers. That local probe
loaded the model in 2.88 seconds, processed
the first native frame in 0.88 seconds and twelve candidate stills in 1.60 seconds
total, with a warm median of 0.118 seconds. These timings exclude retrieval,
decoding, queueing and preview rendering and are not a full-search latency claim.

Separating arrangement from arm width and relaxing spacing as specified above
increased geometric acceptance in that twelve-candidate probe from one to five.
One accepted case was borderline with strength about 0.003. The recorded source
and two actual candidate groups are retained in
`pipeline/tests/data/match_people_layout.json`; regression tests also require
missing partners and mismatched group counts to fail. One overlapping dance
pair produced an extra detection in the probe, demonstrating that detection
count is still fallible. Review also found that the initial selection silently
truncated larger salient groups to three; the accepted v2 contract preserves
the full group and explicitly abstains above three. The v1 diagnostic evidence
and timings do not establish v2 end-to-end performance. These are development
diagnostics, not human graded editorial usefulness or a held-out promotion result.

This is a narrow Lab recall and verification exception. No new main-search
model call, global person backfill, grounded ANN index, hosted call, raw-film
change, speed/crop transformation or automatic editor placement is admitted.
Sparse keyframe recall, screening limits and four-sample native refinement can
still miss useful scenes or brief alignments. Existing ADR-0008 production
quality, latency and complete-profile activation gates remain in force;
broader played-cut quality and expansion require separate human evaluation.
