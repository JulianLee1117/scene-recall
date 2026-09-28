# Match Cuts: region and cut-instant evaluation

This is an offline shadow experiment. It does not alter Framing, retrieve
additional candidates, decode-search a time window, activate a model, or
demonstrate that a transition is good. ADR-0008 remains the activation gate.

The immediate question is whether a better instant and a reasonable crop turn
already discovered shots into useful transitions. Judge these separately from
the question of whether retrieval found the right shots.

## Region crop proposals

Run from the repository root. All output commands refuse to overwrite files.
Use `pipeline/eval/runs/` for replaceable research artifacts.

```powershell
uv run python -m pipeline.experiments.visual_rhymes catalog --unit-id REFERENCE_UNIT --unit-id CANDIDATE_UNIT --output pipeline/eval/runs/rhymes/source-catalog.json
uv run python -m pipeline.experiments.visual_rhymes template --kind proposal --output pipeline/eval/runs/rhymes/cases.json
```

The catalog resolves the selected units through the local index, verifies each
source's Scene Recall fingerprint, and records its picture dimensions, legal
unit range, and indexed timestamps. It exports no local media paths. It is an
operator snapshot, not a replacement for server-side validation when importing
an edit. Re-export after reingestion or source changes. Non-square pixels and
rotation require a separately versioned display normalization and are rejected.

Edit `cases.json` with real film/unit IDs from that catalog. Regions and crops
use `{x, y, width, height}` in the **full decoded source image**, including
letterboxing. These are not the grounded layout profile's active-picture
coordinates; convert explicitly before using detected entities.

- A `fixed` reference uses its supplied `timestamp`.
- A `window` also supplies `window_start` and `window_end`, with a human-chosen
  `timestamp` inside the half-open window. The harness does not search it.
- `indexed_seek` requires a catalog `frame_index` and its unchanged timestamp.
  The legacy extractor's seek time is not a decoded frame presentation time.
- `operator_decoded_pts` requires `pts_evidence` referring to the inspected
  decoded frame or decoder log. This remains human-supplied evidence.
- `handle_seconds` defaults to 0.5 seconds of outgoing footage before the
  reference instant and incoming footage after the candidate instant. Both
  ranges must fit inside their source units. Inspect longer handles when a
  moving subject makes the alignment short-lived.

```powershell
uv run python -m pipeline.experiments.visual_rhymes propose --catalog pipeline/eval/runs/rhymes/source-catalog.json --cases pipeline/eval/runs/rhymes/cases.json --output pipeline/eval/runs/rhymes/proposals.json
uv run python -m pipeline.experiments.visual_rhymes blind --proposals pipeline/eval/runs/rhymes/proposals.json --output pipeline/eval/runs/rhymes/judging.json --key pipeline/eval/runs/rhymes/private-key.json
```

The proposer keeps a supplied reference crop fixed, or finds the largest crop
with the output aspect ratio that retains the reference object. It searches a
bounded scale grid and analytical size alignments on the candidate. Crops use
uniform pixel scaling and translation only, fill the output, and retain the
whole selected region. Padding, mirroring, rotation, object cutouts,
compositing, and motion tracking are outside this scorer. An incompatible
aspect or insufficient resolution returns an explicit infeasible result.

The default output is 1920×1080 and maximum enlargement is 2×; both are explicit
case settings. `resolution_headroom` is retained source pixels per output
pixel: 1 means native resolution, 0.5 requires 2× enlargement. The proposal
retains both original regions, crops, output regions, actual supplied times,
and source ranges for audition. Geometry similarity penalizes position and
size mismatch. The shadow score subtracts crop loss (weight 0.15), lost
surrounding context (0.20), and insufficient resolution (0.20). A supplied
`protected_context` box measures context retention there; otherwise context
means the remainder of the source picture outside the selected object. These
weights belong to `region-crop-shadow-v1`, not a production ranker.

No object class is required, so an eye and a sun can be proposed geometrically.
Boxes alone cannot establish matching silhouettes, pose, appearance, or
editorial meaning. Do not present this score as a learned visual resemblance.

The blind packet pairs full-frame and proposed-crop variants at the same
selected instants. Its private key assigns conditions to A/B. Render the
manifest's two source ranges with the shared Lab renderer, preserve full images
with letterboxing for uncropped variants, and use identical durations and audio
policy. Show judges only the rendered A/B clips, not the packet metadata or key.
The packet is an audition manifest; this CLI does not itself render videos.

## Human oracle and acceptance records

```powershell
uv run python -m pipeline.experiments.visual_rhymes template --kind evaluation --output pipeline/eval/runs/rhymes/evaluation.json
uv run python -m pipeline.experiments.visual_rhymes score --evaluation pipeline/eval/runs/rhymes/evaluation.json --output pipeline/eval/runs/rhymes/evaluation-report.json
```

For each frozen reference, record:

1. Known useful source units and the candidate pool. This measures known-positive
   recall, not exhaustive corpus recall.
2. Sparse indexed reference/candidate anchors and manually selected anchors in
   `instant_oracle`. Compare the actual two cuts, grading each 0–3. A better
   manual cut indicates an instant-selection opportunity; it does not establish
   that an automatic refiner can find it.
3. Full-frame versus region-crop played cuts in `region_comparison`, with the
   blind packet digest and 0–3 grades. Keep this gain distinct from instant gain.
4. Independent Framing and grounded-layout ranked top tens using stable
   candidate identities, pooled human geometry grades, and blinded preference.
   Every item in both top tens must have a grade before nDCG is computed.

Null grades remain unjudged. The scorer reports missing candidates, instant
gain, crop gain, and static retrieval quality separately. It can evaluate the
ADR-0008 numerical gates: exactly twelve fully judged cases, at least eight
blind wins, median nDCG@10 at least 20% above baseline, at most two regressions,
both original Dune cases holding or improving, and three strong positives in
the tight-profile top ten. It uses nearest-rank warm p95 over at least twenty
measurements and requires p95 below 250 ms. Complete compatible manifest
verification is an explicit operator attestation with profile and generation
identity; the harness does not reconcile or select a profile itself.

Use the existing `pipeline.eval.match_cut` scorer for detailed candidate-gate
loss and per-criterion geometry comparisons. Freeze the twelve-reference slice
before comparing challengers; include full-body pose, objects, multiple
subjects, orientation, scale, and negative space. Add separate cross-category
graphic-rhyme diagnostics without replacing the frozen static slice.

Passing this report cannot activate retrieval. Exact-instant refinement and
region reframing still need their own human comparison and explicit activation
decision. An independent [local-only DINOv3 challenger](dense-geometry.md) can
produce dense region rankings from a pinned operator-supplied checkpoint. It
does not download weights, mix existing vector spaces, or activate retrieval;
an actual model run and measured comparisons remain pending.
