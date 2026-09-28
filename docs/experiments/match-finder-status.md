# Match Cuts implementation and evidence

## Two-dancer recall and foreground arrangement — 2026-09-13

[ADR-0048](../decisions/0048-library-recall-and-foreground-person-arrangement.md)
adds bounded existing-index recall and independently prepared person evidence
to automatic scene Match search. The source film is excluded by default. The
interface distinguishes the full library index from the selected film scope,
labels the detected foreground group and restores the compact **Labs** link.
Scene Search remains separate.

Saved failure `2870eea7-4545-4590-8677-49b91efe824b` selected Pulp Fiction's two
dancers at about 48:20.60. All three retained automatic SAM masks covered
background; the prepared pool contained only 80 windows from eight films.
The existing index contained 116,962 keyframes and 45,980 shots from 38 films.
A bounded PE-vector probe found paired dance shots outside that prepared pool,
including Singin' in the Rain. These are recall candidates, not verified cuts.

A local v1 Mask R-CNN probe detected 19 people in the actual reference frame
and selected the two foreground dancers. Model loading took 2.875 seconds,
the first frame 0.875 seconds and twelve candidate stills 1.596 seconds total.
These exclude source decoding, queueing and previews. The layout ablation
accepted one versus five of those twelve stills after separating placement
and height from arm width; one accepted score was borderline. No editorial
quality or full-search latency is established by that diagnostic.

Review found that the initial detector truncated groups above three people,
which could hide extra foreground subjects. The current v2 profile retains the
complete salient group and explicitly abstains above three. Its manifest is
`ff3c42cdf0183941d1c55f6dabd9d2fb344471d53add21da93f0f76f9c350e38` on this
CUDA environment. It uses the same verified checkpoint in a separate model
directory; v1 evidence is not relabeled or silently reused.

The broad implementation's full backend suite passed 1,636 tests with one
platform skip before the final complete-group correction. All 154 follow-up focused
tests passed, covering that correction, native-frame sampling, missing partners, profile
and source changes, bounded work, cancellation and cache corruption. The
19 frontend matching checks and TypeScript passed, and browser inspection
confirmed the Labs link. Real end-to-end v2 search and played-cut acceptance
remain pending: the shared worker is importing a film with more imports queued.
Its running work has not been interrupted to activate this update. The API now
advertises library coverage, but the existing worker still needs to load the
new search modules before library-backed jobs can be tested in the live
interface. Passing tests does not establish useful played cuts.

Artifacts: `.tmp/match-dancers/` (original job, masks, person probe, layout
ablation, and bounded `verify_library.py` acceptance runner),
`.tmp/match-dancers-broad-probe/` (existing-index proposals), and the retained
regression fixture `pipeline/tests/data/match_people_layout.json`. Static people
arrangement does not establish matching dance steps, pose or movement.

## Background-region and repeated-result correction — 2026-09-13

[ADR-0046](../decisions/0046-distinctive-match-evidence-and-reference-selection.md)
versions scene search as `scene-match-distinctive-exact-pair-v3`. It reselects the
automatic reference from retained tracks, checks silhouette negative space as
well as foreground, bounds automatic position contribution and removes
correlated-cue votes. Prepared models, legacy Lab scores and refinement budgets
are unchanged. `/match` also has no Scene Search return/brand navigation.

Four inspected saved references exposed two background-selection failures:
EEAAO selected the right background and Lily Chou-Chou selected sky. The new
policy selects the face/hand and a person respectively, while preserving Pulp
Fiction's hand and Mirror's man. Actual anchor geometry is retained in
`pipeline/tests/data/match_reference_selection.json`; all 12 regression cases
pass without inference. This is a geometric preference, not person detection.

The four-reference CPU coarse replay changed top-five overlap to 1/5, 0/5,
3/5 and 1/5 respectively. Among the 20 new seeds, 13 are camera, five shape,
one subject motion and one position. Position dominance is reduced, but camera
motion now dominates this small development set. A separate static replay over
229 prepared track templates reduced shape qualifications from 5.32 to 2.58 per
query and Stargate top-three occurrences from 13 to nine; top-one occurrences
remained five. Neither replay establishes useful edits or resolves all hubness.

Two fresh live searches used five refinement windows each:

- Reported EEAAO case: first playable 19.36s; final three 47.53s. The reported
  Stargate shot moved from first to fifth, with camera evidence only; its false
  subject-position/silhouette claims disappeared. Final top three were
  Synecdoche 29:30.23, 2001 76:11.23 and Mirror 62:10.15, all camera-led.
- Pulp hand control: first playable 12.34s; final three 27.02s. Top three were
  shape-led: Dune 137:30.28, 2001 123:06.42 and LOTR 182:40.83. An adjacent
  Stargate shot remains a shape suggestion; repetition is not fully resolved.

All six top-three previews passed exact source-frame boundary checks. The
EEAAO preview played in the browser, and the cleaned header was checked in both
empty and result states. The first run followed a service restart and overlapped
CPU tests; these observations do not establish p95 latency or a causal
performance regression. Its 15s/30s playback targets were missed. Editorial
usefulness remains ungraded and stricter outline gates may reduce recall.

The full backend run passed 1,346 tests with one platform skip, followed by the
12 new real-geometry regressions. All 16 focused matching frontend tests passed.
Artifacts: `.tmp/match-ranking-repetition/` (saved jobs, captured tracks, coarse
replay and both fresh live jobs), `.tmp/match-search-hub-replay/report.json`, and
`.tmp/pytest-match-v3-full.log`. Live EEAAO job:
`35da5764-4ebd-485e-afdc-7eb0ab27fd4a`; Pulp control:
`33598a12-2b3c-4dc7-98b8-9cd6c2b5b180`.

## Scene-based Match search — 2026-09-13

[ADR-0040](../decisions/0040-scene-based-match-search.md) adds `/match` from scene
cards, the source player and the Lab discovery card. It needs no edit project.
The portable request holds an indexed source/time, focus, timing, film scope,
same-film inclusion and required incoming duration. The new
`scene-match-shared-exact-pair-v2` engine shares one shortlist, decoding,
tracking and optical flow. Every displayed cue refers to the exact same native
last-A/first-B pair. Position qualifies independently, without inventing shape
or movement support. Subject and camera flow must touch their actual boundary.

An initial non-progressive shared-search probe still took 25.80s to first
playback, so the worker now auditions the first supported candidate during
remaining refinement. It preserves final ranking and reuses media only for an
identical pair; at most one extra encode is allowed. Cache playback requires
the current render manifest, source identities, exact boundary proof and the
saved MP4 SHA-256. Invalid media can be regenerated from the saved proposal.

Controlled diagnostic comparison on the same development reference, local
RTX 5070 Ti, same-film exclusion and nearby timing:

- Fresh-process legacy v3 adapter: first verified playback **31.60s**, three
  verified previews **33.63s**.
- Fresh-process shared v2 adapter: first verified playback **15.48s**, three
  verified previews **28.97s**. The early candidate finished at rank three.
- Separate warm default Auto run with same-film inclusion: first **11.36s**,
  top three **26.47s**. Its early candidate finished fourth, exercising the
  four-encode bound.
- Separate warm point-selected Position run: first **6.27s**, top three
  **12.85s**. Its top three were position-only, with no shape/movement claims.

All 12 top-three previews across these runs passed the played-boundary checks.
Each new search refined five unique windows from 200 prepared shots / 80
windows / eight films. Matching source code hashes did not change during the
runs. Evidence: `.tmp/match-cut/effectiveness-scene-search-jobs/comparison.json`
and each named run's JSON, media, manifests and progress/publication log.
This is one development reference with several configurations, not a held-out
editorial study or p95 latency estimate. Cold first playback still narrowly
misses the 15s target; no broader quality gate has passed.

The live API/worker smoke independently verified progressive playback, all
top-three media routes and cue PTS, and unchanged existing project/revision
state. It measured first playback at 19.64s and completion at 41.72s during
application startup/development activity; keep that observation separate from
the controlled adapter comparison. Evidence:
`.tmp/match-search-http-v2/report.json`.

The full backend suite passes **1,152 tests with one platform skip**. This
includes projectless persistence/cancellation, request validation, exact-pair
cue/scoping tests, cache corruption/source-change rejection, progressive
publication and the four-encode bound. Human played-cut usefulness remains
ungraded; ordinary descriptive search and the v3 Lab engine remain intact.

Final frontend validation passes **93 tests and the production build**. Browser
checks confirm early and on-demand A-to-B playback (`readyState=4`, playing,
no media error), completed-search refresh without an extra search job, focus
selection/cancellation, and a 390px layout with no horizontal overflow. The
search-options panel fits that viewport. The browser uncovered a native-dialog
focus ordering bug; Escape, X and backdrop now close the dialog before restoring
the opening button. Escape/X restoration was verified again in the browser.
Console warnings/errors were empty. An existing tab was usable after new-tab
creation stalled; the finished results tab is retained for review.

## Workspace and effectiveness follow-up — 2026-09-13

[ADR-0038](../decisions/0038-played-match-cuts-and-tracked-subjects.md) adds one
A→B workspace: choose a moment, Find matches, audition three choices, optionally
adjust either boundary, and Keep cut with Undo. Automatic matching uses available
subject, shape and camera evidence; focus is an override. Nearby timing searches
one second either side, and Pin this frame preserves the selected source frame.
Native frame controls can inspect the full indexed shot between sparse keyframes.

The new subject path uses the pinned official SAM 2.1 Small checkpoint with the
existing RAFT Small flow profile. Three salient candidate masks are tracked per
bounded window. Reference selection defaults to the primary salient mask and can
be overridden by a point or box. Ordered local flow retains opposing limb motion;
background camera fitting excludes the tracked masks. Shape compares visible
silhouette, physical aspect, screen position and scale. These are measured cues,
not identity, pose or action-completion guarantees. DINOv3 remains an independent,
unprepared optional dense channel.

This choice extends the existing working local flow pipeline and keeps model
preparation separate from search. The [Netflix match-cut study](https://arxiv.org/abs/2210.05766)
motivates separate action and graphic evidence, while the
[SAM 2 implementation](https://github.com/facebookresearch/sam2) supplies prompted
video masks. [WAFT](https://github.com/princeton-vl/WAFT), newer segmentation and
point tracking remain controlled challengers; a newer model alone does not prove
better played cuts. Candidate refinement remains capped at ten windows across all
channels. No full-library preparation or main-search activation was introduced.

### Concrete failures found and corrected

- The old motion scorer could accept a dissolving television/static shot as a
  camera continuation. A flow-warp photometric check now requires support in
  every ordered phase and at subject boundaries. The diagnostic dissolve passed
  the former whole-window 75% gate (6/8 pairs) but failed the new leading phase
  (1/3 pairs); stable motion controls passed all phases. This is a targeted
  reliability check, not a universal shot-transition detector.
- Whole-action similarity could reward an incompatible movement at the cut.
  Modern scoring compares exit against entry velocity, speed and acceleration,
  and rejects local reversals. Nearby refinement reevaluates both cut points.
- A 23.976-fps source exposed a one-frame outgoing preview shift. Native source
  start selection and explicit absolute-PTS render rounding now retain the
  offered boundary. Real-media tests cover 23.976, 25, 30, 50 and 60 fps, variable
  frame rates and nonzero source timestamps. Preview, saved cut and export agree;
  original source bytes are unchanged.
- An 8×8 silhouette alone can collapse tall and wide objects into the same
  shape. Physical aspect is now checked independently of resized silhouette.
- Mean pixel agreement also rewarded empty mask cells: two disjoint sparse
  silhouettes could score 0.9375. Foreground intersection-over-union now replaces
  that measure, and shape agreement multiplies layout evidence. Disjoint shapes
  score zero even when position and size match. This structural correction uses
  synthetic counterexamples, without tuning thresholds to the two film probes;
  boundary scorer identity is `cut-boundary-photometric-foreground-v3`.
- Request-local caching preserved all 18,844 measured subject-boundary scores
  while reducing that scoring pass from 3.01 to 0.77 seconds. Shape-only requests
  skip optical flow because their comparison consumes masks, not movement.

Frontend validation passes 57 tests and a production build, including asynchronous
source seeking, prompt binding, native-frame movement, preview races and timing
adjustments. Pinning the same decoded frame preserves focus; choosing a different
frame clears the old point/box. Browser walkthrough remains unverified: tab
creation stalled twice despite timeouts. Existing user browser tabs/projects were
preserved; the application preview was queued for this task.

Final integrated backend validation: **1,072 passed, one platform skip**, with
the known dependency deprecation warnings. The live HTTP/worker check passed
progressive preview, adjacent native-frame adjustment of both sides, exact Keep,
Undo, restoration and export. The first verified 48-frame preview was decoded
while its match job still ran. Export boundary MAE was 0.398/0.466 against source
frames and 0.581/0.956 against the adjusted preview (RGB levels out of 255).
All three pre-existing projects remained unchanged. The named verification
project `ec9e7009-90eb-44a4-bd5e-5844c9ee5575` remains at revision 5 with the
adjusted/exported pair; evidence and the guarded request log are retained at
`.tmp/match-http-workflow-final/report.json`. This was a functional smoke during
the full CPU test suite, not a latency benchmark or an editorial grade.

### Measured evidence

Tracked preparation completed all 80 windows with 229 retained tracks in 305.55
seconds on the RTX 5070 Ti. Manifest identity:
`90e4c20f29638bbdc5ea8dcb2d3018df4b24e080775e37d462860bc7e620a7a0`.
The independent subject profile is
`1c2ed393ae3aa96801ad4bb069ba70c1ae856ae0ee72085dd70a5a7122e6ac9f`.
Real point and box prompts tracked all 13 frames of a bounded 12-fps probe,
including propagation backward from the selected frame.

The initial subject/shape diagnostic compared fixed and nearby timing on two
real references. All 12 previews passed boundary checks; peak CUDA allocation
was 605–754 MiB. First-playable times were 24.99/34.31 seconds for subject
fixed/nearby and 22.34/26.81 seconds for shape fixed/nearby. These miss the target;
they are the baseline for subsequent performance work. The shape example
retrieved a broad silhouette/position rhyme rather than another face, and the
subject example returned a modest movement score. Neither is evidence of
editorial acceptance. Artifacts: `.tmp/match-cut/effectiveness-subject-shape`.

The foreground-v3 comparison then tested five initial refinements against ten,
continuing up to the existing cap when fewer than three reliable choices survive.
All four diagnostic requests retained the exact ranked top three shots and cut
points, with all 12 previews verified in each run. Subject first-playable time
dropped from 33.37 to 21.19 seconds nearby and 24.07 to 15.98 seconds fixed. Shape
times were 14.45 seconds nearby and 7.82 seconds fixed. The face-to-fire false
positive disappeared after foreground scoring; the replacement remains a broad
graphic suggestion requiring playback judgment. These limited comparisons
justify the bounded operating default, not a general recall or p95 claim.
The exact comparison is `.tmp/match-cut/refinement-budget-comparison.json`.

The final Automatic/nearby Mirror probe used all three ready channels and
returned three verified previews, supported respectively by camera, shape and
subject evidence. First-playable time was 28.97 seconds and all three took 31.01
seconds; peak CUDA allocation was 714 MiB. Both the 15-second first-result and
30-second top-three targets were missed on this probe. Automatic still executes
bounded channel refinements before preview publication, so the explicit-focus
improvements do not establish a fast default path. The retained run is
`.tmp/match-cut/effectiveness-automatic/run.json`. No production promotion or
claim of optimal matching is supported by these measurements.

The final camera ablation contains two diagnostic references and three variants
each. All 15 returned top-three previews passed decoded boundary checks. The
current fixed variant abstained on the badminton reference instead of forcing a
motion match. Nearby timing returned alternatives; their editorial quality is
ungraded. Observed nearby matching/top-three times were 15.89/20.02 seconds for
the aerial reference and 11.35/14.58 seconds for badminton. First-playable times
were roughly 12–17 seconds. The 15-second first-playable target is therefore not
consistently met in these probes, and two cases cannot establish p95 latency.

Artifacts are retained under `.tmp/match-effectiveness-camera-final`,
`.tmp/match-photometric-real`, `.tmp/match-boundary-final-retest`, and
`.tmp/match-native-fps`. `pipeline.experiments.match_effectiveness` records code,
source and profile identities, rendered hashes, abstention, timing and blank
playback judgments. A proposed 12-visual/24-motion study exists at
`.tmp/match-effectiveness-cases-20260913.json`; it remains a draft. Human approval
and frozen held-out playback judgments are still required before reporting
editorial acceptance. Automated mechanics are not counted as useful cuts.

The following ADR-0027 record is historical baseline evidence; its older UI,
residual-region subject path and timing figures do not describe the new workspace.

## Original implementation record

Date: 2026-09-11. Branch: `codex/scene-recall-lab`. No merge or production
promotion. Architecture decision: [ADR-0027](../decisions/0027-bounded-lab-match-finder.md).

## Implemented

Match Cuts now adapts a reusable matching service to immutable background
jobs. Image mode uses independent DINOv3/PE candidates, region windows and dense
correspondences, with bounded coarse-to-fine decoding. Movement mode uses real
RAFT Small C_T_V2 optical flow, robust affine camera separation and distinct
camera/residual-region descriptors. Static appearance is never motion evidence.

The UI offers a scene or example reference, image/movement selection, optional
region and reframing, candidate frames, three played transitions and explicit
revision-checked application. Region marking does not crop. Applying preserves
history and supports Undo. Stale edits and locked clips reject application.
Source files remain immutable; model spaces and derived profiles stay separate.

## Real local verification

- Cohort `cohort-a6fc164ae3600294`: 200 shots across eight films, 80 prepared
  real optical-flow windows. Descriptor/frame preparation is capped at 600
  indexed frames; motion windows are at most four seconds.
- Official RAFT Small weights were downloaded, checksum checked and pinned.
- Real camera-motion run returned ten cross-film candidates and three A-to-B
  previews. First run took 29.94 seconds; final run including boundary-image
  checks took 39.30 seconds. These are single-run observations, not p95 latency.
- Final job `84d1e291-ebaf-48b2-a615-d66e4c31ec54`, project
  `ac98c6c7-fd7b-4df8-9a74-bd7a77fdfcb3`. The top candidate scores were roughly
  0.37, 0.24 and 0.19: a functioning retrieval path, not established strong cuts.
- All six original/proposed preview boundary checks passed. Mean absolute RGB
  error after preview normalization was 0.38–3.66 / 255 (limit 12). This permits
  compression and resize differences; identical neighboring frames cannot
  establish unique PTS identity. Actual source PTS remain recorded separately.
- Browser: source and preview both readyState 4; two-second preview loaded;
  apply saved revision 3; Undo restored the three-second single-source edit;
  save preserved it as revision 4. A second match was launched through the UI.
- Narrow viewport check: 390px viewport, 375px document width, no horizontal
  overflow. Temporary viewport override was reset.
- Final backend full suite: 685 passed, one Windows platform skip, including
  actual-frame boundary mismatch and on-demand snapshot/revision regressions.
- Optional motion reference-window run completed in 39.16 seconds, selected a
  different reference PTS within the allowed window, and passed all three
  preview boundary checks. On-demand fourth-candidate preview loaded in the
  browser at readyState 4 while the project remained at revision 4.
- Next production build passes. Working-tree whitespace check passes.

These checks establish mechanics, not human editorial acceptance.

## Model investigation

[DINOv3's official implementation](https://github.com/facebookresearch/dinov3)
provides dense patch representations and matching examples, making it a
reasonable independent region-retrieval challenger. Real extraction remains
blocked on approved local ViT-S/16 checkpoint access. There was no cached
checkpoint or Hugging Face authentication. Approval/access was requested from
the user; no gated weights were obtained through another source. Synthetic
adapter tests are not reported as a successful real DINOv3 evaluation.

[WAFT](https://github.com/princeton-vl/WAFT) is a current optical-flow challenger
with multiple backbones and a recommended A1 downstream checkpoint. Its official
code at `b152ff1cad1af8c185ee7b141997c48ff3334c87` and the linked `tar-c-t.pth`
were tested in an isolated environment. Checkpoint SHA-256:
`9f4b24f48b3937eca690a12b73bc3190effde6d4d4c87db01998fe63d846397f`.

OpenCV was installed only in the probe environment. Redundant pretrained
initialization for the depth/ResNet constructors was disabled locally before
strictly loading the complete official checkpoint; every key matched. Four
real-frame-pair runs at 320×192 returned finite flow: 359ms cold, then 63/47/47ms
warm, approximately 397MiB peak PyTorch allocation. This is a feasibility probe
on one pair, not a representative accuracy, retrieval or memory comparison.
No WAFT dependency or default was introduced into the application.

The working baseline is the supported
[torchvision RAFT Small C_T_V2](https://docs.pytorch.org/vision/main/models/generated/torchvision.models.optical_flow.raft_small.html).
Retain it until WAFT improves held-out played-transition quality under the same
latency/coverage constraints. Benchmark leadership alone does not establish the
best editing model.

## Open acceptance work

1. Obtain approved DINOv3 weights and run real dense preparation and image/crop
   retrieval on the frozen cohort, comparing PE/Framing and dense candidates.
2. Approve the proposed 12 visual and eight motion references before tuning.
   `review-proposed.json` beside the cohort manifest contains blank judgments;
   it is not a graded or fully blinded comparison packet.
3. Judge useful top-three transitions, candidate recall, exact timing, crop
   quality and motion confounders, including camera pans, moving backgrounds,
   direction reversal and low-confidence/occluded subjects.
4. Compare RAFT and WAFT on these held-out motion windows before replacing the
   baseline. The current residual-region descriptor is not persistent object
   tracking and does not guarantee occlusion or identity consistency.
5. Compare fixed references against the optional bounded reference windows.
   Image and motion consider at most four reference alternatives. Additional
   candidates have on-demand background previews without changing the edit.

Ordinary descriptive search keeps its existing path. No LLM router, library-wide
backfill, main-branch rewrite, music-search coupling or production activation
was added in this experiment.
