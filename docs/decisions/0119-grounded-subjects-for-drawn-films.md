# 0119. Grounded subjects for drawn films

Status: Accepted (2026-10-10)

## Context

The measurement pass (ADR-0093) and the moments pass (ADR-0099) find subjects with RF-DETR
over COCO's 80 classes. On the first animated films in the library the detector saw a person
in 13% of Spirited Away's shots, 39% of Fantastic Mr Fox's and 75% of Spider-Verse's, against
80 to 90% in live action; foxes came back as person, dog or teddy bear, a diving fox as a
bird, and drawn figures mostly as nothing. The class name reaches only the People filter (now
the annotation's count, ADR-0113 amendment), the pose gate of match cuts and the Lab
segmenter, but a missing detection removes the subject box, silhouette and keypoints that
editor crops, subject motion and the subject, shape, eye and pose channels of match cuts
depend on. The Match Cuts lab showed the failure plainly: Chihiro beside a car, and only the
car seen.

Measured alternatives: a swap to an open-vocabulary detector for every film costs two to ten
times RF-DETR's time per frame and loses its object classes (16 distinct ones on a sample of
live-action frames), for 73% agreement with it on people counts and 81% on the largest box
where both find something. Grounding DINO prompted with generic subject words boxed every
drawn figure tried (Chihiro, No-Face, the soot sprites, both foxes, Akira's people, Avatar's
banshees). RF-DETR's own queries overlap those boxes at IoU 0.9 to 0.99 while scoring the
class under 0.2: it localises what it cannot name, so its mask head yields the silhouette for
free. ViTPose on the grounded person boxes gives keypoints with mean confidence 0.7 to 0.9 on
drawn humanoids.

## Decision

`pipeline/evidence/subjects.py` defines two subject backends with one record:

- `coco`: RF-DETR detection, segmentation and keypoints, unchanged, for live action.
- `grounded`: Grounding DINO tiny prompted with `person . character . animal . creature .
  vehicle .`, duplicates merged across words, class codes 1 (person, character), 81 (animal),
  82 (creature) and 83 (vehicle) beside the dense COCO codes; the silhouette of RF-DETR's
  best-overlapping query (IoU at least 0.5), else none; ViTPose keypoints on person boxes.

A film's backend follows its genre families from Wikidata genres and form (ADR-0113): the
families in `ingest.grounded_subjects` (default Animation) take `grounded`, everything else
`coco`. The backend is part of a grounded artifact's cache inputs and is recorded in the
measurement and moments artifacts and in the match index's film rows; `coco` artifacts keep
their original inputs, so the library is not re-measured by this decision. Scoring families
treat grounded animals and creatures as animals and grounded vehicles as vehicles; the pose
channel compares COCO-17 keypoints whichever model produced them.

Not decided: gap-filling live-action frames where RF-DETR finds nothing, and a drawn-character
pose model. Both wait for a concrete failure.

## Consequences

- Drawn films carry subject boxes, silhouettes and keypoints in the same arrays as live films;
  editor crops, subject motion and every match-cut channel work on them. Non-human characters
  still have no pose.
- A grounded film costs about ten times the GPU time of a live one in the measurement and
  moments passes (tens of minutes rather than minutes per film), bounded to the configured
  families and paid once per film.
- The `measure` extra gains Grounding DINO and ViTPose through `transformers`; both download
  on first use.
