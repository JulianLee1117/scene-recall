# Scene Recall architecture

Status: current architecture contract.

`README.md` documents commands and runnable behavior. This document defines the
current system boundaries. Records in `docs/decisions/` explain why material
choices were made but do not override this document.

## Product contract

Scene Recall supports three related jobs:

1. Find a film moment someone remembers.
2. Discover visually related references and playable source moments.
3. Save source moments for later retrieval.

Retrieval returns source-backed evidence: film identity, time range, and the
matched frame or text. Generation may later explain or organize retrieved
evidence, but it must not invent first-stage results.

```text
text query
  -> PE text-to-frame candidates
  -> semantic text-view candidates
  -> exact-word candidates
  -> rank fusion and filtering

reference image (production Framing)
  -> PE frame candidates
  -> bounded spatial reranking

reference image + text
  -> mandatory reference shortlist
  -> text reranks only that shortlist

typed recipe (one to three clauses)
  -> explicit evidence adapters
  -> equal reciprocal-rank fusion
  -> mandatory composition gate when present
  -> final filtering and diversity

result
  -> film + timestamp + matched frame/text + playable media
```

The product calls the current reference workflow **Framing**; the API retains
the `composition` facet name for compatibility. It is coarse appearance and
position matching, not an exact editorial Match Cut mode.

The accepted experimental boundary is deliberately separate:

```text
indexed reference frame (shadow Match Cut; not product-active)
  -> independent grounded-layout candidates + PE candidates
  -> union by stable frame identity, never by mixing vector scores
  -> exact grounded-layout reranking
  -> human promotion gate and complete profile manifest
  -> bounded source-backed refinement inside top shots
  -> actual decoded timestamp for the proposed cut instant

short source window (future Motion Match; not implemented)
  -> camera motion + subject/object residual trajectories
  -> independent temporal candidates and window reranking
  -> separate action-heavy evaluation and activation
```

## Durable evidence and replaceable derivations

Durable assets are the source film and timestamped evidence that can support
future derivations:

- source identity, hash, and path;
- shot and time boundaries;
- extracted frames and media recipes;
- original dialogue with timestamps;
- reconstructable clip ranges.

Model outputs are replaceable derivations:

- annotations and semantic views;
- embeddings and indexes;
- future clip, audio, or summary profiles;
- active-profile manifests.

A derivation must be independently backfillable and identified by its model,
revision, dimensions, contract, inputs, and relevant schema or prompt. Never
mix incompatible vector spaces. Preserve old profiles until a replacement is
complete and deliberately activated; missing optional derivations must degrade
to a known-safe baseline.

## Durable user state

Bookmarks are user-authored state, not an index derivation. They live in a
schema-versioned SQLite database under the configured `state_dir`, outside the
replaceable `assets_dir`. Index repair, backfill, and film reingestion must not
delete them.

Each bookmark preserves the source `film_id` and evidence timestamp as its
durable anchor. The unit ID and frame index recorded when it was saved are
derived lookup hints. If a later compatible reingest changes shot boundaries,
the API may resolve the bookmark to the current unit containing that timestamp,
but only within the same film identity. Unit intervals are resolved as
half-open ranges, so an exact shared boundary belongs to the following unit;
the absolute final boundary of a film deterministically falls back to its last
unit. Missing or temporarily unavailable source/index data leaves an explicit
unavailable bookmark rather than silently rebinding or deleting user state.

## Dataflow

The ingestion, retrieval, and recipe sections below describe production
behavior. The Match Cut section is explicitly shadow-only, and its refinement
and Motion Match subsection defines accepted future boundaries rather than
implemented behavior.

### Ingestion

The current pipeline performs:

1. Content-addressed film probing.
2. Subtitle extraction or local speech transcription.
3. Shot detection and bounded subdivision of long shots.
4. One or three ordered keyframes per shot.
5. Local preview generation.
6. PE Core visual embeddings for frames and legacy shot vectors.
7. One hosted structured annotation over the ordered keyframes.
8. Publication of film, frame, and unit records.
9. A non-blocking local semantic-text derivation.

Hosted annotation caches are scoped by provider, requested model, prompt,
schema, settings, and ordered frame hashes. Changed profiles coexist instead of
overwriting prior generations.

Publication spans separate LanceDB transactions. Units are the visibility
boundary for a new film. Replacing an existing film can briefly expose new
frames beside old unit metadata; removing that legacy window requires
generation-tagged schemas or cross-table transaction support.

### Text retrieval

Normal text search independently ranks:

- PE text-to-frame visual matches;
- the active semantic text profile;
- native full-text matches over `searchable_text`.

The semantic profile uses Qwen3-Embedding-0.6B and stores independent non-empty
`caption`, `dialogue`, `ocr`, and `facets` views. Matching views collapse to one
vote per unit before weighted reciprocal-rank fusion. The winning view and text
are returned as evidence.

Deterministic filtering handles unrequested credits, logos, title cards, blank
frames, and static artifacts. Visual deduplication suppresses near-identical
evidence, and unscoped searches apply a soft film-diversity preference.

### Reference and Framing retrieval

Reference-image search retrieves PE frame candidates and spatially reranks a
bounded shortlist using a learned 6x6 feature grid. The query image is encoded
once. Candidate grids come from a complete compatible Framing cache when one
is active; otherwise every candidate in the bounded spatial shortlist is
encoded through the established live path. Cached and live candidate evidence
are never mixed within one query. This represents composition and subject
position, not pose or motion.

The Framing cache is an optional, independently backfillable acceleration of
the existing scorer, not a candidate vector space or a new retrieval mode. One
profile table stores float16 grids by stable frame identity and is scoped by
the resolved immutable PE checkpoint revision, extraction contract, grid and
feature dimensions, row schema, storage dtype, and relevant OpenCLIP, timm,
Torch, Torchvision, and Pillow versions. Backfill and compatible-cache queries
load that exact checkpoint revision rather than resolving mutable `main`
independently. Its manifest proves exact coverage of one published `frames`
generation, including the frame-identity digest and profile-table generation.
The idempotent `index-framing` command derives it from retained keyframes
without raw-film decoding or hosted inference.

The result-card composition workflow defaults to a composition-only search of
other films. Its source-film exclusion is applied before candidate generation,
so source style cannot consume the bounded reference shortlist. Explicit film
scope still controls the allowed library; a scope containing only the source
film takes precedence rather than becoming an empty cross-film search. Users
may deliberately include the source film or retain their current text clause.

Text and reference clauses can be combined. The reference result set remains
mandatory; text reranks only those candidates and cannot introduce a visually
unrelated result. The exact matched reference frame is preserved, and matched
text evidence is attached when available.

Frontend best-per-movie grouping retains the highest-ranked item for each film
already present in the returned window. It is presentation, not guaranteed
per-film retrieval, and does not imply that every indexed film is relevant or
present in the global candidate pool.

### Match Cut shadow profile

Match Cut is not an alias or silent upgrade for the current Framing workflow.
It is an approved, separate shadow experiment under ADR-0008. Inspected
tight-profile references exposed missing candidates as well as poor ordering:
useful side-profile matches fell outside bounded PE/spatial pools, while
frontal or motion-confounded people ranked ahead of closer geometry. Adjusting
the 6x6 spatial weight cannot supply the missing entity, scale, orientation, or
pose evidence.

The shadow `match-layout-v1` contract represents the active picture plus a
bounded, salience-ordered set of normalized entities. Entity evidence may
include class or family, box, silhouette, pose, and screen orientation, but an
extractor records only supported evidence and never fabricates low-confidence
pose. Zero-detection frames remain explicit profile rows so completeness is
measurable rather than biased toward easy images. The corresponding coarse
vector and exact scoring contract belong to the same versioned profile.

Candidate generation searches grounded-layout and legacy PE spaces
independently, unions their bounded rankings by stable frame identity, and then
uses an inspectable layout scorer over the pooled shortlist. Raw vector scores
from incompatible spaces are never normalized together. Exact scoring follows
the human criteria in `pipeline/eval/match_cut_cases.yaml`: subject/object,
normalized position, scale, viewpoint/orientation, pose, and relations or
negative space. Match Cut retains cross-film discovery by default while
respecting explicit movie scope.

The initial Dune cases demonstrate the failures but are not an acceptance
corpus. Before Match Cut becomes product behavior, the human-owned set must
grow to 10-15 representative references and freeze a 12-reference acceptance
slice covering profiles, full-body pose, objects, multi-subject relations,
scale, orientation, and negative space.

The first grounded-score probe is not activation-ready: case A positives scored
.602/.542/.632 versus hard negatives .457/.544/.536, while case B positives
scored .677/.583 versus hard negatives .672/.759/.603. No orientation evidence
was emitted, and the second case's confounders can outrank its positives.

Against current Framing, a later challenger must:

- win at least 8 of 12 blinded side-by-side choices;
- improve median per-case nDCG@10 by at least 20% on fully judged pooled top
  tens, with no more than two case regressions and every initial Dune case
  improved or held;
- place at least three judged positives with grade-2 or grade-3 geometry in the
  tight-profile case's top ten; and
- keep warm p95 candidate-union plus static-rerank latency below 250 ms on the
  target hardware.

Passing quality and latency is still insufficient without a complete manifest
for the current published frame generation. Shadow outputs never affect
production ranking until both requirements pass and the profile is explicitly
selected.

### Exact-frame refinement and Motion Match

The one-or-three keyframes retained for a shot are candidate-recall evidence,
not a guarantee that an indexed image is the best cut instant. After the static
Match Cut profile passes its gate, an exact-frame refiner may operate only on a
bounded set of top candidate shots. It decodes coarse samples from the retained
source film, searches a finer neighborhood around each local winner, and
returns the actual decoded presentation timestamp and frame evidence. A paused
player timestamp may be the source query instant; otherwise the indexed
keyframe timestamp remains the source anchor.

The backend resolves the source film, unit interval, and legal timestamp. A
browser path or vector is never authoritative. Decoded frames and layouts are
replaceable caches keyed by source identity, unit/time range, decoder contract,
and extractor/scorer profile. This design avoids a library-wide every-frame
index while preserving enough source evidence to repeat or backfill a result.
Exact-frame refinement needs a separate human comparison that judges the
returned instant rather than only its containing shot before it can change
product results.

Motion Match remains a separate future short-window profile. It must describe
temporal direction using optical flow, estimated global camera motion, and
tracked subject/object trajectories after that camera motion is removed. It
does not reuse a still layout score as proof of motion similarity, and Framing
or still Match Cut is never a fallback presented under the Motion Match label.
Activation requires its own versioned manifest, latency budget, and
action-heavy human cases containing direction, relative-motion, and camera
motion confounders.

### Modular recipe retrieval

`POST /search/recipe` composes one to three explicit clauses without adding a
router, query LLM, model, index, or ingestion dependency. Clause IDs and facets
must be unique. Text clauses support broad `all` search plus `scene`, `words`,
`look`, and `mood`; indexed-scene sources support those same focused facets
except `all`, and also support `composition`.

The adapters deliberately expose evidence already present in the current
system:

- `all` uses normal hybrid text retrieval;
- `scene` searches caption semantic views;
- `words` searches dialogue and OCR semantic views;
- `look` uses the paired PE text/frame space without spatial reranking;
- `composition` uses the indexed source frame and existing spatial reranker;
- `mood` searches the existing structured-facet semantic view.

Source clauses address an indexed unit and, for `look` or `composition`, an
exact frame index. The server resolves the corresponding unit, film, vector,
and file path from the active index; browser-supplied paths or vectors are
never trusted. Every source and its facet-required evidence is validated before
an empty composition target scope can return no results. Every referenced
source unit is removed from the result set.
Composition retains the ADR-0005 cross-film default and is a mandatory
candidate gate. Its effective result-film scope is resolved before any bounded
clause retrieval and shared by every clause, so source-film hits cannot consume
an auxiliary clause's candidate window. Other clauses may rerank composition
candidates but cannot introduce composition-unrelated results.

A recipe containing only one broad `all` text clause delegates to normal text
search, preserving its complete bounded candidate union and diversity behavior,
then adds the recipe match evidence. All other facet adapters return bounded
raw rankings; recipes with multiple clauses combine them using equal
reciprocal-rank fusion. Existing junk suppression, visual deduplication,
reference temporal spread when applicable, and final film diversity are then
applied once to the resulting ordering. Each returned product result includes
stable `keyframe_index` evidence and a `matches` entry for every clause that
retrieved it, including the contributing rank and matched text or frame when
available.

Focused `scene`, `words`, and `mood` clauses require a complete active
semantic-text profile. They fail explicitly when it is unavailable instead of
silently using the inseparable legacy combined text vector. Broad `all` search
retains the established safe fallback.

## Activation and fallback

The semantic-text table identity includes its model revision, dimensions, and
embedding contract. Its manifest must exactly cover the current units
generation before the profile becomes active.

If the profile or manifest is missing, stale, partial, corrupt, unreadable, or
unavailable at query time, the complete dense-text channel falls back to the
legacy PE `units.txt_vec` representation. Partial generations are never mixed.

Existing films build or repair this profile with the idempotent `index-text`
command documented in `README.md`. New ingestion attempts the same derivation
after publication; failure leaves the film searchable through the fallback.

The production Framing spatial cache has its own whole-profile activation
manifest tied to the exact `frames` table version, row count, and stable-ID
digest. Every current frame must have one row whose source file metadata,
resolved model revision, library versions, extraction contract, grid shape,
and storage dtype match the selected profile. Descriptor bytes are checksummed
when loaded and during idempotent reconciliation, so corrupt rows are
repairable. Missing, stale, partial, duplicate, corrupt, or incompatible cache
data disables the cache for the whole query; Framing then runs its
established bounded live candidate encoding with the same candidate set and
scoring formula. A query never combines cached grids for some candidates with
newly encoded grids for others. The cache's explicit float16 storage can move
an almost-tied adjacent result by a minute amount; changing that storage
contract requires a new profile.

Film publication intentionally does not make this optional acceleration part
of the canonical ingest transaction. Each new `frames` generation therefore
invalidates the previous cache manifest immediately. During a multi-film
batch, Framing remains correct through live fallback. Running `index-framing`
after the batch reconciles all metadata, embeds only new or stale keyframes,
and atomically reactivates complete coverage; a scoped run may do the same
after one film, but it cannot overlap the shared ingest lock.

The PE visual tables are a frozen legacy exception. Frame rows contain only a
coarse encoder name and unit rows lack exact visual-model lineage. The runtime
loader now resolves one immutable upstream checkpoint per model operation, but
those legacy row and table contracts still do not record or activate that
revision. Search and publication reject a configured encoder-name mismatch,
but this baseline must not be rebuilt piecemeal after upstream weights change.

Any future visual or multimodal replacement must use a separate versioned table
with exact revision lineage and its own activation manifest.

The grounded Match Cut profile is such a separate visual derivation. Its
manifest records extractor model IDs, immutable revisions or weight hashes,
library versions, preprocessing and active-picture normalization, thresholds
and label mapping, layout schema, vector contract and dimensions, scorer
versions, input frame generation, frame-identity coverage digest, and expected
and completed row counts. It is complete only when every target frame has a
row, including zero detections.

If that manifest or any required profile data is missing, stale, partial,
corrupt, incompatible, or unavailable at query time, Match Cut is disabled as
a whole. Production Framing remains independently usable, but it is never a
silent Match Cut fallback. Exact-frame refinement and Motion Match follow the
same whole-profile activation rule when implemented.

## Known limitations

- Film, frame, and unit writes are not cross-table atomic.
- The legacy PE baseline lacks immutable checkpoint lineage.
- Hosted annotations record the requested model identifier, not a
  provider-resolved immutable revision.
- Sparse keyframes can propose shots but can miss the best match-cut instant;
  exact source-backed within-shot refinement is not product-active.
- The optional Framing cache becomes inactive after any film publication until
  `index-framing` reconciles the new frame generation; live Framing remains
  available during that interval.
- Grounded Match Cut and Motion Match are not product-active. Current Framing
  cannot reliably match pose, temporal direction, brief action, or camera
  movement.
- Dialogue is embedded at shot level rather than as utterance rows.
- OCR comes from the general annotator rather than a dedicated OCR pass.
- `scene` and `mood` reuse current annotation views rather than independently
  learned representations; there is no dedicated plot representation.
- There are no clip, audio, scene-summary, reranker, router, or RAG indexes.
- Saved scenes are local to one configured state database; named collections
  and account synchronization are not implemented.

These are boundaries, not an automatic backlog. Retained source evidence makes
targeted future derivations possible without predicting every metadata field.

## Decision gates

Add a representation only when a concrete workflow or repeatable failure shows
that current retrieval lacks necessary evidence.

For a material retrieval change:

1. Determine whether the problem is missing candidates, poor ordering, or a
   missing workflow.
2. Choose the smallest representation that addresses that failure.
3. Build it as a separate versioned profile on a representative subset.
4. Compare old and new top results on roughly 10-15 real queries.
5. Activate it only after complete coverage and explicit selection.
6. Retain the compatible fallback until removal is separately justified.

Prefer local models for deterministic, high-volume candidate generation.
Hosted models are appropriate for bounded annotation or shortlist processing
when they add evidence or quality unavailable locally.

A reranker is justified only when recall is adequate and ordering is the
problem. Temporal retrieval is justified only when action or camera-motion
failures recur. RAG is justified only for grounded reasoning, comparison, or
reel-building above retrieved evidence.

Match Cut is the concrete exception now admitted to shadow evaluation because
inspected references showed both candidate and ordering failures. It follows
the stricter human, latency, completeness, and explicit-selection gates above.
That approval does not activate exact-frame refinement or Motion Match.

Paid library-wide processing, global model activation, and removal of a working
fallback require the small comparison above. Structural versioning, cache
preservation, and safe backfills do not require a permanent golden corpus.

## Current next action

Expand and human-grade `pipeline/eval/match_cut_cases.yaml`, then compare the
current Framing baseline with the `match-layout-v1` candidate union and exact
reranker at every declared gate. Backfill only a representative shadow subset
until the acceptance slice shows that the profile clears the ADR-0008 quality
and latency thresholds. The evaluation must distinguish a candidate lost
before reranking from a candidate ordered poorly afterward.

Only after the static grounded profile passes should exact-frame refinement be
tested inside top shots. Motion Match follows later as one separate temporal
profile over an action-heavy subset; it must not be smuggled into the static
layout score. A query router or LLM decomposition layer cannot replace missing
visual or temporal evidence and must not become an always-on dependency without
its own demonstrated need.

Audio, routing, RAG, scene graphs, and speculative metadata remain deferred
until they pass the decision gates.

Historical rationale is recorded in
[`docs/decisions/`](decisions/README.md).
