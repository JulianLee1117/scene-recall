# Temporal evidence validation — 2026-09-14

The short-shot evidence change in [ADR-0052](../decisions/0052-short-shot-temporal-evidence-and-targeted-backfill.md)
was validated on six placed clips in **BAANDIT — rumination, revision 19**.
The read-only project plan found **9,574 eligible legacy one-frame short
units across 32 placed-source films**, out of 40,610 scanned units. Existing
project revisions, source intervals, trims and saved selection evidence remain
unchanged. Production staging completed as **329 durable `waiting_worker`
batches** covering all 9,574 unique units; worker activation remains pending.
This record does not claim a completed library refresh.

## Observed annotation changes

The final pilot used `short-shot-edge-middle-native-pts-v2`. Each case retained
three distinct decoded frames in source order and received the temporal prompt.
The following observations compare its new description with the legacy one:

- **Clip 57, The Fellowship of the Ring, unit 0428:** the former empty-tree
  description missed the person entirely. The new description records an older
  man beside the tree, his subsequent absence, and a cut to a startled crowd.
  Camera motion remains `unknown`; the model does not infer a fade mechanism
  from absence alone or guess the character's name.
- **Clip 15, Marty Supreme, unit 1426:** a static description of a hand holding
  metallic objects becomes a description of the hand shifting and partially
  closing, making the objects less visible.
- **Clip 69, The Tree of Life, unit 0138:** a general river-and-waders
  description gains the swimmer's splashing, the arm reaching down, and a wader
  revealed farther across the changing view.
- **Clip 111, All About Lily Chou-Chou, unit 0787:** an indistinct blue-lit
  close-up gains the changing visibility of two people, including the nearer
  figure turning or moving partly out of view. Camera motion stays `unknown`.
- **Clip 138, Everything Everywhere All At Once, unit 2113:** the former
  single-person description gains a partially visible second person in the
  early image and the woman's subsequent forward movement and expression
  change. The visible people count rises from one to two.
- **Clip 145, The Lighthouse, unit 0466:** the static coastal description gains
  the scene darkening across the images while the beacon remains visible.
  The final prompt response keeps camera motion `unknown`.

These are diagnostic cases, not a broad action-recognition benchmark or proof
that every event inside a selected trim is captured by three stills.

## Bilbo interval and preserved boundaries

The exact stored unit is
`d27dbc7256d4f11d32923513c1224a498379dea78627ba83579a3e5fff13ed82_0428`,
covering **1527.1089167–1528.3184583 seconds**. Its new retained player-relative
times are **1527.151, 1527.693 and 1528.277 seconds**.

Decoded source inspection places the last tree image at approximately
1527.818 seconds, the reaction cut at 1527.860–1528.277, and the next crowd cut
at approximately 1528.318. The existing detector merges a reaction shorter
than `flash_min_duration=0.5` seconds into the preceding unit. The later
reaction image is therefore inside the saved interval; it is not a sampling
offset error. This migration preserves that interval and the project's trim.
Whole-unit descriptions and the exact footage selected by an edit remain
distinct evidence scopes.

## Verification and rollout boundary

- Full Python suite: **1,768 passed, 7 skipped** (154.78 seconds; three existing
  dependency warnings).
- Native timing regressions cover positive and negative container epochs,
  short intervals, adjacent-shot exclusion and distinct one-/two-frame cases.
- An isolated CPU pipeline produced **6 updated units and 18 frame rows** with
  real source decoding, local visual/text embeddings and Lance publication.
  It reused validated cached paid annotations; repeats published **0 units**.
- The isolated exercise left production indexes and raw source films unchanged.
  Text-profile activation waited for complete current-generation coverage.
- The actual FastAPI keyframe routes served all **18 profiled WebPs** from the
  isolated pilot database with exact published bytes, and Pillow decoded every
  response. Bilbo's indices 0–2 returned 200, index 3 returned 404, and index 0
  differed from the retained legacy midpoint. Database versions were unchanged.
- Keyframe serving, frame-index repair, source relinking and the exported shot
  embedding helper support profiled paths while retaining legacy evidence.

The wider migration is staged in the existing durable worker, with batches of
at most 32 shots. The 19 eligible units already placed in the edit have
priority; the first batch contains four Fellowship units, including Bilbo.
The six validated paid annotation caches were copied under film locks into
their matching production cache profiles, avoiding another paid request when
those exact inputs reach the worker. No production index publication occurred
during staging.

Project revision **19** and document SHA-256
`ec7b03488a6eecfe5cbb6d449ab4ae3dbde94aa0f1f816e5a2d79d24d5d848a8`
matched before and after staging. All new batches use `waiting_worker`, so the
already-running older worker ignores them. `temporal-jobs` shows **queued**;
later batches retain this internal state when an updated worker is processing
earlier work. The API process already includes the profiled
keyframe endpoint. An existing verified idle-reload helper remains active and
waits for the current ingestion and queued Lab generation to drain before
restarting the worker. Its activation preserves foreground jobs; this work did
not interrupt active ingestion.

Deferred semantic-text failures now retain published evidence and result counts
but report an actionable failed job. Exact `--unit-id` retries can reconcile
text for current units without another annotation; broad plans skip current
units and suggest `index-text --film-id`. Direct apply writes its requested
receipt before exiting nonzero on a deferred text failure.

Local diagnostic artifacts are retained under `.tmp/tp2/` (final samples and
annotations), `.tmp/tv2/report.json` (v2 timestamp/sample checks),
`.tmp/tp/bilbo-boundaries.jpg` (source cut inspection),
`.tmp/tfull/report.json` (isolated publication and repeat results),
`.tmp/tfull/api-verification.json` (exact API image verification) and
`.tmp/tp2/staging-receipt.json` (frozen scope, jobs, preserved cache hashes and
project-identity proof).
