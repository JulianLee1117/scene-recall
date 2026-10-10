# Framing representation pilot

> Retired on 2026-10-10 by [ADR-0120](../decisions/0120-framing-runs-live.md);
> its code and the commands below were removed. The findings stay as a record.

Status: first mechanical comparison and an
[AI visual audit](framing-visual-audit-20260921.md) completed, 2026-09-21;
human preference remains unmeasured. The audit found complementary strengths
and specific buried matches, not a universal quality winner. Bulk preparation
remains paused while bounded evaluation establishes what is worth scaling.
This document does not activate a profile or replace
[ADR-0082](../decisions/0082-shared-search-foundation-and-composition-challenger.md). The accepted contract
and the [implementation record](search-foundation-implementation.md) remain
authoritative; successful feature extraction alone is not a quality result.

## First local run

The current bulk batch was drained and **134 pending jobs were put on durable
hold**. The ingest worker was then restarted and remained idle/available for
foreground work. Eighteen optional jobs had already completed; their output
and all held cursors were preserved. The generic worker status still counts
held jobs as queued; `search-features queue` exposes their explicit pause marker.

Completed three representation arms over **524 source-hashed frames**:
PE-Core final grid, the same model's block 17, and PE-Spatial-S16-512. There are
512 candidates and twelve provisional references; each reference searches
448 cross-film candidates. The hash-ordered films were Marketa Lazarova, Rear
Window, No Country for Old Men, The Fellowship of the Ring (extended), The
Batman, Le Samourai, The Wailing and 2001: A Space Odyssey. Timeline selection
was independent of rankings, but this is not yet a curated framing benchmark.

Both 32-frame feasibility passes succeeded on the RTX 5070 Ti. The two-frame
PE baseline parity and repeat checks had zero maximum absolute difference;
PE-Spatial also repeated exactly in the check. All descriptor batches passed
shape, finite-value and checksum validation. Saved batches were reused when
resuming the full run.

Measured local costs:

- Shared PE extraction for final grid, block 17 and global control: **20.313 s**.
- Separate PE-Spatial extraction: **10.246 s**.
- Cumulative admitted runtime across feasibility/resume calls: **55.468 s**;
  model and source validation before admission is excluded.
- Pilot artifacts: **95,070,533 bytes**; the new public checkpoint is
  **87,981,467 bytes**, plus about 40 KiB of pinned official code/license.
- Each uncompressed final/intermediate PE grid is **72 KiB/frame**;
  PE-Spatial is **27 KiB/frame**. This excludes database/index/history overhead.

Extraction timings include small feasibility checks and are not a fair
standalone-model speed ratio: PE extracts two layers plus global features in one
forward. Nor does small-pool ranking time establish library-scale latency.

The arms materially disagree. Across the eight held-out references, intermediate
and PE-Spatial grid-only rankings share on average **0.875 and 0.25 of ten**
results with the final PE grid. The current final-grid 65/35 blend shares
**6.125 of ten** with global-only ranking. This makes representation and blend
choice worth inspecting, but disagreement is not evidence of better relevance.
No positive/negative labels, preference wins or nDCG are claimed.

The frozen manifest, rankings, checksums and timing receipts are retained in
`pipeline/eval/runs/framing-representation-20260921/`; derived arrays remain in
`.tmp/framing-pilot-20260921/`. The adapter and checkpoint live only in
`.tmp/framing-models/PE-Spatial-S16-512/`. Production indexes and activation
settings were unchanged. The cumulative focused verification passed 78 tests;
the final adapter/pilot subset passed 12 tests after the download-path and
repeatability changes.

DINOv3 was deferred because local checkpoint access returned HTTP 401; no access
agreement was submitted. PE-Spatial was the explicitly recorded public substitute.
EUPE, bar removal, aspect-preserving preprocessing and compression remain
follow-ups. The subsequent AI audit includes independent candidate inspection
and exact rank checks; it does not satisfy the human promotion gate. Keep bulk
preparation held until an explicit cost/benefit decision.

## Two independent questions

1. **Framing:** which affordable image representation retrieves similar subject
   placement, relative scale, negative space and composition across different
   films, rather than just similar people, colours or settings?
2. **Jev:** does query interpretation select useful additional text evidence
   more effectively than ordinary search or fixed expansion, at acceptable
   serving latency?

Run these as separate lanes. Jev does not inspect reference pixels or generate
framing descriptors. Its textual shot-type route currently searches stored
facets; it does not activate image-reference Framing. Reuse the frozen decisions
and queries in [the existing Jev comparison](intent-guided-retrieval.md), with no
new hosted calls by default. Keep ordinary search as the control and measure a
single guided-search execution separately from three-strategy comparison time.
Only test their interaction after either lane demonstrates an independent gain.

## Small, independent fixture

The target fixture is **eight films, 512 candidates and twelve references**:

- Freeze eight available films spanning aspect ratios, colour/monochrome,
  bright/dark scenes and varied shot scales. Keep selection reasons explicit.
  Choose 64 existing retained frames per film, deterministically distributed
  across its timeline. Do not derive this pool from current search rankings,
  caption queries or spatial-cache availability.
- Select twelve reference frames from contact sheets before seeing any model's
  ranking: off-centre single subjects, centred symmetry, two-shots, close-ups,
  silhouettes, foreground/background relationships and negative space. Assign
  four references to tuning and eight to held-out evaluation, grouped by source
  scene so neighbouring shots cannot cross the split.
- Exclude the reference shot, temporal neighbours and exact/visually duplicate
  frames by one frozen rule shared by every strategy. Report actual eligible
  counts. Include cross-film-only results as the primary framing view, with
  same-film results shown separately; colour or identity shortcuts are useful
  counterexamples, not convincing composition matches.
- Before rankings are visible, allow at most 64 additional candidate frames
  selected from independent contact sheets: plausible composition analogues
  with different appearance, and similar-looking frames with wrong layouts.
  Record selection provenance and provisional relevance. If not inspected,
  leave this diagnostic set absent rather than asserting fabricated positives.

Every model searches every eligible candidate in this frozen pool with exact
scoring. No baseline shortlist, ANN, production database publication or
full-library inference is involved. The fixture is deliberately diagnostic;
it cannot establish library-wide recall or performance at 1,000 films.

The initial automated fixture uses SHA-256-ordered published films and timeline
quantiles, with 64 candidates per film and twelve separate references (524 total
source images). It requires at least 66 distinct units per selected film; exact
eight-film selection is also supported. Same-film and source-byte duplicates are
excluded from ranking. Perceptual duplicates, diverse-case coverage and hard
negatives still need contact-sheet review; none are claimed verified. Mark these
references as unreviewed. The owner can later compare actual images side by side
without inventing queries or filling out category questionnaires. Human quality
and preference claims remain pending until that review happens.

## Candidates and controlled comparisons

Start with three representations, not a model sweep:

1. **Current baseline:** the installed PE-Core L/14 checkpoint, current final
   patch features, 6x6 pooling, normalization and float16 round trip. First
   reproduce the existing score. Also record global-only and grid-only scores
   from the same features to show whether appearance is dominating.
2. **PE intermediate:** reuse that exact checkpoint and transform; extract
   preregistered transformer block index 17 from the 24-block model instead of
   the final block. Record the index convention, token selection, normalization
   and output shape. This upper-middle block is a hypothesis, not a demonstrated
   best layer. No layer sweep in the first pass. This tests the cheapest change
   without downloading another model.
3. **DINOv3 ViT-S/16:** use the web-image pretrained small checkpoint, dense patch
   tokens and corresponding-position 6x6 pooling. Exclude class/register tokens.
   Checkpoint access and compatible local loading must pass feasibility first.
   DINOv3 supplies dense visual features, not explicit cinematic framing labels.

PE-Spatial-S16-512 is the feasible distinct-model candidate for the initial run:
DINOv3 checkpoint access is gated. Its 384-dimensional grids remain independent
of the 1024-dimensional PE-Core grids. Do not relabel one as the other. A small EUPE
checkpoint is an optional efficiency follow-up only when weights, dense-feature
output and existing dependencies are readily available within the same budget.
Defer blocked candidates rather than extending this into package migrations,
large downloads, fine-tuning or segmentation/depth pipelines.

For the representation comparison, preserve the same full picture geometry and
6x6 correspondence. Required native input resolution and normalization are
recorded per checkpoint, so conclusions compare the complete descriptor recipe,
not just model weights. Keep current PE global scores fixed in the diagnostic
65% global / 35% spatial blend; a different encoder's global vector must never
be compared directly with PE vectors. Spatial-only ranking is the primary
representation diagnostic. Raw similarity scales differ across models, so a
blend win alone is not proof that the spatial features improved.

Use uncompressed descriptors for this small pool. Test compact projections only
after choosing a promising representation; fit using separate, film-balanced
tuning samples and freeze their hashes before held-out evaluation. Do not fit
to held-out references or select layers, grid sizes and weights using their
results. The current 32/64-dimensional production challenger is a separate
compression/serving decision, not an assumed requirement for this pilot.

## Keep preprocessing separate

First compare model recipes using current full-image square resizing. Then test
the baseline and at most one promising alternative with two explicit ablations:

- Remove verified encoded black bars, retaining square resizing.
- Use that same active picture with aspect-preserving resize/padding, a recorded
  valid-picture mask and pooling coordinates relative to the active picture.

Inspect proposed bar bounds on the small fixture, especially dark frames and
changing aspect ratios; uncertain bounds retain the original picture. Preserve
source images. Record crops, resize interpolation, padding and masks in profile
identity. Avoid default centre crops that discard the very subject placement
being evaluated. These ablations separate bar contamination from stretching;
neither is presumed to improve results.

## Execution budget and receipts

- Begin with a **32-frame feasibility pass** per representation: loading,
  deterministic dimensions, finite/nonzero features, repeatability, memory and
  extraction throughput. Process models sequentially under the existing GPU
  coordination; stop for foreground work or resource pressure.
- Admit at most **90 minutes of pilot computation** and **2 GiB of new derived
  artifacts/scratch**. Count downloads separately with a **1 GiB total new
  checkpoint cap**; reuse existing weights. Downloads are not a default action.
  Check projected space/time before the full fixture. These are admission caps,
  not a promise that every candidate will fit or complete.
- Keep the host's normal sleep policy. With sleep expected in four hours, stop
  admission at least 30 minutes before that time. Save each batch of at most 32
  frames; never
  require the PC to finish a model sweep before it can sleep.
- Put all pilot-owned features and outputs in one run directory. Freeze source
  frame/unit IDs, film/timestamps, source SHA-256, file metadata, candidate and
  reference manifests, split/seed, exact model revisions/checkpoint hashes,
  preprocessing, source-code/dependency versions, dtypes and hardware. Hash
  completed descriptor files and ranking receipts before marking them complete.
- Resume only exact-compatible completed batches after revalidating their
  checksums and source identities. A source/profile change creates a new run or
  branch of results, never a mixed vector space. Record failed/skipped models
  and interruption separately from zero quality.
- Keep durable manifests, rankings and selected review images. After review,
  delete only disposable pilot-owned descriptors/scratch through a path-checked
  cleanup. Never remove canonical films, original retained frames, shared model
  caches or live Lance internals. Resuming the bulk queue remains an explicit
  operational decision.

## Runnable first pass

The runner implements frozen preparation, PE-Core final versus zero-based block
17, and an optional separate PE-Spatial pass. Preprocessing ablations, compact
projections, human judgments and DINOv3/EUPE inference are not implemented by this
runner. PE-Core grids share one forward pass; the first two frames also check
parity with the existing baseline and repeated inference within absolute
tolerance 0.001. Receipts disclose that extra feasibility work.

After the bounded model-staging command has explicitly downloaded the optional
small public PE-Spatial checkpoint, use a new run directory:

```powershell
uv run python -m pipeline.experiments.framing_models --download
uv run python -m pipeline.experiments.framing_representation --out .tmp/framing-pilot-20260921 --spatial
uv run python -m pipeline.experiments.framing_representation --out .tmp/framing-pilot-20260921 --execute --max-batches 1
uv run python -m pipeline.experiments.framing_representation --out .tmp/framing-pilot-20260921 --execute --resume --phase spatial --max-batches 1
uv run python -m pipeline.experiments.framing_representation --out .tmp/framing-pilot-20260921 --execute --resume
```

Omit model staging and `--spatial` for a PE-Core-only run. Execution follows the
prepared manifest; it cannot add models afterward. `--max-minutes` can lower
the cumulative 90-minute admission ceiling. A run-local `STOP` file stops before
the next batch; removing that file and explicitly resuming reuses verified
completed chunks. A busy ingest lock stops promptly. Normal sleep is unchanged.

`prepared.json` preserves source/profile/split identities; per-arm `.npy` chunks
and batch receipts preserve completed work. `progress.json` records status and
time, and `results.json` appears only after every prepared arm completes. It
contains exact cross-film top-ten lists, global-only and fixed 65/35 controls,
overlap counts and an explicit unjudged quality status. Perceptual-duplicate
review is still pending. Inspect these files before cleaning the pilot directory;
retain the small manifests, receipts and rankings if deleting its derived arrays.
No run cleanup command touches shared caches or production assets.

## What to measure and how to decide

Record cold load, per-frame extraction, peak GPU memory, feature bytes/frame,
exact retrieval time and end-to-end reference time separately. Warm timings need
repeated runs, fixed eligible pools and disclosure of other worker activity.
Small-pool search timing is not a production latency claim. Extrapolated storage
must include retained versions/index overhead and be labelled an estimate.

For each held-out reference, show blind, order-randomized top-ten image strips
and an independently sampled set of candidate images. Pool the strategies' top
results for consistent human grading of subject positions, scale, negative
space and depth arrangement; retain appearance similarity as a separate note.
Allow ties and “none useful.” Do not use Jev, another model or an agent's visual
judgment as the user's preference or as ground truth.

Report per-reference known-positive ranks, inspected hard-negative failures,
top-ten overlap and human list preferences. Compute nDCG@10 only with explicit
human relevance grades; disclose the judged pool and leave unjudged items
unknown. Pooling and random checks reduce blind spots but do not establish
exhaustive recall. Show individual failures and group related references; eight
held-out cases are a screening result, not strong statistical proof.

Choose a next candidate only for a visible held-out gain with acceptable cost
and no unexplained known-positive losses. If gains are absent or labels remain
pending, retain the current serving behavior and keep bulk preparation paused
until the cost/benefit decision is made. A successful pilot can justify the
larger evaluation; it cannot activate partial coverage. ADR-0082 still requires
complete compatible coverage, its twelve-reference human gate (at least 8 wins,
median nDCG@10 relative gain at least 20%, at most 2 regressions and retained
known positives), warm p95 at most 5 seconds and actual optional storage at most
64 GiB. `retrieval.composition_profile` remains null until that gate passes.

## Primary sources checked

These motivate candidates, not claims of cinematic framing superiority:

- [Meta Perception Encoder](https://github.com/facebookresearch/perception_models/blob/main/apps/pe/README.md):
  distinct Core and Spatial representations; intermediate-layer investigation.
- [Meta DINOv3](https://github.com/facebookresearch/dinov3): dense features and the
  21M-parameter ViT-S/16 web-image checkpoint; weight access requirements apply.
- [Meta EUPE](https://github.com/facebookresearch/EUPE): optional efficient
  representation candidate, subject to local feasibility.
