# ADR-0099: Moment-level match cuts from library-wide evidence

- Status: Accepted
- Date: 2026-09-28
- Supersedes: ADR-0040 and ADR-0048 (scene-based Match search), ADR-0046's
  cue fusion, and ADR-0067's reference selection inside `/match`
- Refines: ADR-0008 (Match Cut stays out of ordinary search), ADR-0093
  (a new `moments` evidence producer) and ADR-0096 (assembly transitions)
- Leaves in place: ADR-0027 and ADR-0038 for existing saved Match Cuts
  projects (`/lab/visual-rhymes`), frozen

## Context

The owner wants match cuts the AI editor can use: two wide shots of people
flowing into each other, cuts that carry a pose, a movement or the light,
TikTok/Reels-style, and chains of them. The Lab search did not get there:

- **Shots, not instants.** Recall ran over up to three indexed keyframes per
  shot, then decoded at most ten windows. A better-matching instant elsewhere
  in a shot was invisible, which the owner called out: "thumbnails are only part
  of a scene".
- **Bounded and slow.** Generic references needed a prepared cohort of 200
  shots; people needed an on-demand Mask R-CNN; SAM tracked masks per query.
  First playable result took about 26 s, and ADR-0048 recorded SAM missing both
  foreground dancers.
- **No editor path.** The editor judged continuity from one mean subject box
  per shot.

Evidence v2 (ADR-0093) now measures camera motion at 6 fps for every shot of
the library, so a library-wide, per-instant description became affordable.

## Decision

1. **Moments evidence.** A local producer (`moments`, `python -m
   pipeline.evidence moments`) decodes each film once and describes every
   instant on the film's grid at 4 fps inside every shot, in content
   coordinates (letterbox bars removed):
   - RF-DETR segmentation (COCO): the largest six objects, each with a box and
     a 16x16 silhouette inside it;
   - RF-DETR keypoints (COCO 17) for the four largest people, on instants with
     a segmented person;
   - a 32x18 luma thumbnail, a 16x9 doubled-angle edge field with edge energy,
     8x5 mean colour, sharpness and brightness.

   Arrays go in a compressed `.npz` beside the JSON artifact, which records
   their SHA-256. Camera motion is joined from `measure`, not recomputed.
2. **Library index.** `python -m pipeline.matching.moments index` compiles the
   artifacts into one memory-mapped index (`assets_dir/matching/moments/<id>`),
   published atomically. It is derived and rebuildable, like compiled tables.
   While a new producer profile backfills, a film serves its newest earlier
   profile (as ADR-0095), and the manifest records which.
   - **Usable cut points.** An instant qualifies if something in it is lit, it
     sits at least 0.3 s from a hidden cut, and it falls inside one of its
     shot's pictures (ADR-0098), never in the dissolve between two.
   - **Coarse vectors.** Every other instant gets separately normalized parts:
     layout, light, lines, main silhouette, main pose, colour and motion,
     each PCA-reduced. A query weights the parts by focus, so one dot product
     is a weighted sum of part cosines.
   - **Calibration.** The build measures each component's quantiles over
     random pairs of instants.
3. **Pair scoring.** A cut pair is an outgoing instant and an incoming one,
   each seen through a crop; all comparisons happen in output coordinates.
   Rewards are calibrated against random pairs: 0 is chance, 0.8 the top 1%,
   1 the top 0.1%.
   - `subject`: instance IoU after assignment; class families only discount.
   - `eyes`: the eye-trace point. A person's eyes come from keypoints; a person
     whose face is out of frame has none.
   - `pose`: object keypoint similarity over prominent people. Parts shown in
     only one frame count against the match, so a close-up meets a close-up.
   - `shape`: silhouettes compared inside their own boxes.
   - `light`, `lines`, `colour`: correlation of the luma layout, agreement of
     the edge orientations and carry-over of the colour layout.
   - `motion`: camera and subject travel continuing across the cut.

   Penalties cover a brightness jump and zoom. A flat picture gives its light
   weight to the subject. Weights follow a focus: auto, subject,
   shape & pose, motion, composition or colour.
4. **Reframing and formats.** Output can be 16:9, 9:16 or 1:1. A vertical or
   square output crops a window of its shape around the subject. **Reframe to
   align** zooms the incoming crop up to 1.5x and moves it so its eye point
   lands on the outgoing one. Scale follows height, or width when a frame cuts
   its subject off. Crops are ordinary render crops.
5. **Search.** `POST /matching/moments/search` is synchronous, with no job.
   - It retrieves over every coarse instant, then scores all usable instants
     near the best shots' hits exactly.
   - It keeps one result per shot, one per scene and three per film.
   - The source film is excluded unless asked; same-film results skip the
     reference's scene.
6. **Lab workspace.** `/match` is rebuilt as one screen.
   - The left pane holds the cut-point scrubber and the settings.
   - The results grid shows each incoming frame through its crop, with an
     onion-skin overlay of the outgoing frame on hover.
   - An audition plays the cut in the browser: two alternating video elements
     through their crops, with frame nudges and an overlay view.
   - **Add to chain & continue** makes the chosen shot the next reference and
     keeps chaining.
   - It remains a Lab session with no project (ADR-0051).
7. **Editor.** With harness v2 and a built index, the assembly scores every cut
   on its actual frames (`pipeline.lab.harness.matchcuts`).
   - Scoring uses the identity-crop form of the same components, weights and
     calibration, with per-instant features cached.
   - A new edit setting, **Match cuts** (`planner_settings.match_cuts`: off,
     some or many), sets the weight. `some` is the default and replaces the
     shot-level eye-trace heuristic.
   - Without an index the heuristic stays.

## Evidence

Measured on a 12-film pilot (RTX 5070 Ti, while other GPU work ran):

- **Pass cost.** About 37 film-minutes per minute with keypoints, so roughly
  9 h for the 339 h library. Decoding skips non-reference frames on NVDEC.
  Keypoints are decoded in one batched GPU step, because the library's
  per-image post-processing cost as much as the model.
- **Search cost.** About 0.3-0.5 s on a pilot index of 64k-239k instants.
  Coarse retrieval takes under 10 ms; the rest is exact scoring of 2.5k-4k
  instants.
- **Known item.** The 2001 bone-to-satellite cut is not found. Its luma
  polarity flips (a dark bone on bright sky, a bright satellite on black), the
  detector names both objects inconsistently, and the cut is a conceptual
  rhyme. Geometry alone does not rank it.
- **Wide shots of people.** From La La Land's four women walking at the camera,
  the top results were people walking or standing toward the camera,
  centred, with matching pose and eye line. The overlays confirm the
  alignment. Chaining from the chosen Forrest Gump couple returned standing
  pairs.
- **Other cases.**
  - A dialogue two-shot (Pulp Fiction) matched two-shots with the same
    placement and eye lines (La La Land, Forrest Gump).
  - In 9:16 with reframing, walking people were cropped and zoomed onto the
    reference's eyes.
  - An upside-down close-up had no real partner in 11 films and fell back to
    light matches. A frame-filling subject no longer scores as "same place",
    and a person with no evident face has no eye point.
- **Framing needs keypoints.** Silhouettes alone matched a waist-level
  two-shot to head-and-shoulders close-ups. Keypoints fix this: a hidden face
  gives no eye point, and pose counts the parts shown in one frame only.
- **Editor.** A 60 s kinetic assembly drew on 400 random pilot shots:
  - mean measured cut match rose from 0.13 (plain) to 0.34 with *some* and
    0.40 with *many*;
  - cuts scoring at least 0.6 rose from 0 to 3 and 6 of about 35;
  - assembly time rose from 9 s to 11 s, with cut pairs memoized.

## Consequences

- **What is new.** The whole library is searchable at every instant, with no
  prepared cohort and no per-query model. A new film needs its `moments` pass
  and an index rebuild.
- **Precision.** Cut points are exact to the 4 fps grid (+-0.125 s), plus up to
  two frames from skipping non-reference frames. Native-frame refinement is
  deferred until played cuts show the need. The browser audition can be a
  frame off; renders and exports use exact source times.
- **Storage.** About 15-25 MB of arrays per film, and a derived index of a few
  GB.
- **Limits.**
  - Conceptual rhymes (bone to satellite) and non-COCO shapes rest on light
    and lines only.
  - Detector class names flicker, which is why classes only discount.
  - Vertical crops of widescreen film see coarse light grids.
- **Old code.** The cohort, SAM and Mask R-CNN code paths stay only for saved
  `/lab/visual-rhymes` projects. The old `/matching` search API is retired
  from the workspace, and its code is removed in a later cleanup.
- **Deferred, pending played-cut evidence.**
  - Native-frame refinement.
  - A dense semantic channel for conceptual matches.
  - "Find match cuts" in ordinary search (ADR-0008's gate: an owner-reviewed
    reference set).
  - Match-cut candidates injected into editor pools.

## Alternatives considered

- **Keep shot-level recall and decode more windows.** It stays bounded and
  slow, and still misses instants.
- **Dense DINOv3 patch features per instant.** Strong for graphic matches, but
  about ten times the storage, with no controllable components. It is gated on
  Hugging Face.
- **A vision-language model judging cuts.** Benchmarks (VEBench 2026) show
  editing-technique recognition, not alignment. Earlier side-by-side judging
  here was position-biased (ADR-0096).
- **Learned match-cut embeddings (Netflix, WACV 2023).** They beat instance
  IoU only modestly (AP 0.35 vs 0.25), need labelled pairs, and give no
  reasons or crops.
