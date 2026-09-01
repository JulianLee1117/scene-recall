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
  -> conditional full-text candidates
  -> rank fusion and filtering

reference image (standalone API compatibility)
  -> PE frame candidates
  -> bounded spatial reranking

reference image + text (standalone API compatibility)
  -> mandatory reference shortlist
  -> text reranks only that shortlist

typed recipe (product UI; one to three total clauses)
  -> explicit text or indexed-scene evidence adapters
  -> optional query-bound uploaded Look or Framing adapter
  -> one ranking per independent input and reciprocal-rank fusion
  -> mandatory uploaded-image or composition gate when present
  -> final filtering and diversity

result
  -> film + timestamp + matched frame/text + playable media
```

The product calls spatial reference matching **Framing**; the standalone API
and recipe contract retain the `composition` name for compatibility. It is
coarse appearance and position matching, not an exact editorial Match Cut
mode.

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
- a selected raw external subtitle sidecar when the release provides one;
- shot and time boundaries;
- extracted frames and media recipes;
- reconstructable clip ranges.

Model outputs are replaceable derivations:

- annotations and semantic views;
- parsed subtitle and speech-transcript rows;
- embeddings and indexes;
- future clip, audio, or summary profiles;
- active-profile manifests.

A derivation must be independently backfillable and identified by its model,
available immutable revision, dimensions, contract, inputs, and relevant
schema or prompt. When a model resolver exposes only an alias, the manifest
must at least include that exact identifier plus the engine and profile
versions, and the alias must not be refreshed without a profile bump. Never mix
incompatible vector spaces. Preserve old profiles until a replacement is
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
2. Dialogue extraction from a usable canonical English SRT sidecar, a
   convertible embedded subtitle stream, or local speech transcription, in
   that order.
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

Film intake copies one usable English-marked SRT with minimally useful dialogue
that is filename-associated with the selected feature beside the canonical
film as `<film-stem>.en.srt` while retaining the release copy. When no sidecar
is safe to select automatically but usable, feature-associated candidates
remain, intake exposes only those candidates with bounded, non-persisted text
previews and requires an explicit choice to use one as English or skip
subtitles. An omitted or stale decision fails before the film or subtitle is
moved; the server recomputes the eligible set and accepts only an exact member,
never an arbitrary browser-supplied path. Multiple candidates are not merged or
guessed between. Skipping leaves every release SRT untouched in `incoming`.

A shared, non-destructive content floor rejects malformed, oversized, trivial,
and promo-only external SRTs both at intake and dialogue resolution; it is not
a language or completeness classifier. Forced, commentary, extra-associated,
known foreign-marked, and unassociated sidecars are neither selected nor
offered for review. A preview is transient interface data, not durable evidence
or a derived dialogue source. Dialogue derivation records a contract-versioned
manifest containing the selected sidecar content hash, embedded stream
identity, or Whisper model and transcription profile. The sidecar derivation
profile removes known promotional cues from parsed dialogue without changing
the raw file. The Whisper fallback uses VAD and
source-language transcription. A canonical `en` or `eng` tag on the primary
audio stream supplies an English language hint; missing, `und`, and all other
tags retain automatic majority voting over up to five voiced 30-second
language-detection windows. The manifest records the exact resolved language
option and the primary-audio-tag evidence when used, along with the remaining
options and faster-whisper package version. Previous-window text conditioning
is disabled. A versioned Whisper-only structural gate rejects
high-confidence exact repetition loops before dialogue publication. Rejection
stores empty dialogue for that profile and continues through visual and hosted
caption evidence; external and embedded subtitle text bypasses the heuristic.
Changing or rejecting a source, transcription profile, or gate invalidates only
dialogue and its downstream derivations; it does not mutate the immutable film
identity or rejected raw evidence.

Publication spans separate LanceDB transactions. Units are the visibility
boundary for a new film. Replacing an existing film can briefly expose new
frames beside old unit metadata; removing that legacy window requires
generation-tagged schemas or cross-table transaction support.

### Text retrieval

Normal text search independently ranks:

- PE text-to-frame visual matches;
- the active semantic text profile;
- native full-text matches over `searchable_text` when the broad query is
  compound or explicitly quoted.

An unquoted broad query with exactly one word token omits full-text as an
independent fusion vote when a visual or semantic channel is active. This is a
query-shape policy, not a vocabulary classifier: it prevents an incidental
caption, subtitle, or OCR occurrence from promoting an otherwise weaker match
for an open concept such as `beautiful`. Quoting the term restores explicit
word evidence, compound broad queries retain lexical corroboration, and a
lexical-only evaluation remains a true retrieval mode. The focused Words facet
continues to search only dialogue and OCR semantic views.

The semantic profile uses Qwen3-Embedding-0.6B and stores independent non-empty
`caption`, `dialogue`, `ocr`, `facets`, and `mood` views. `facets` remains the
broad structured document used by normal text search. The narrow `mood` view
contains only stored mood labels and known energy, serialized as labeled text;
it excludes setting, framing, time, camera, palette, and subjects. Matching
views collapse to one vote per unit before weighted reciprocal-rank fusion. The
winning view and text are returned as evidence.

Deterministic filtering handles unrequested credits, logos, title cards, blank
frames, and static artifacts. Visual deduplication suppresses near-identical
evidence. After that hard suppression, ordinary unscoped text and unconstrained
typed-recipe streams apply a 30-second defer-only temporal spread: nearby
results from the same film move behind the first available result from each
competing sequence, but remain as relevance backfill. Explicit film scopes
preserve strict relevance order.

Normal unscoped broad search retrieves three times the configured per-channel
candidate depth before fusion so that a film outside a source-heavy top window
can become eligible through cross-channel agreement. After fusion, it retains
the configured candidate prefix plus the best deep candidate from each of at
most one page's worth of films absent from that prefix. This bounded reserve
prevents the deeper pool from expanding quadratic visual-deduplication work.
This ordinary unscoped broad stream then uses the bounded repeat-rank policy:
a film's next candidate has priority
`original_rank + strength * repeats / (repeats + 1)`. The configured default
strength of 32 is a measured prototype value, not a universal optimum. The
finite penalty is deterministic, preserves every eligible result in its full
permutation, and keeps deeper 12/48/96/200 prefixes exact. Unscoped typed
recipes without a mandatory uploaded-image or indexed-Framing candidate gate
apply the same final repeat-rank policy after clause fusion, filtering, visual
deduplication, and their ordinary 30-second spread. They keep their established
internal clause depths and do not inherit broad search's deeper channels or
cross-film reserve. Explicit scopes on those ordinary streams remain strict.
Unscoped mandatory visual recipes retain the existing page-wise per-film
preference, which relevance-backfills each unfilled slot and relaxes its
cumulative per-film target on later pages; neither policy guarantees
representation for every film. A movie-scoped mandatory visual recipe skips
film balancing but retains its separate 90-second reference spread.

### Reference and Framing retrieval

Reference-image search retrieves PE frame candidates and spatially reranks a
bounded shortlist using a learned 6x6 feature grid. An unscoped uploaded image
first reads a fixed 7,200-row frame metadata projection, collapses it to the
best frame per unit, and hydrates only the ordinary 200-unit base plus at most
one unit for each of 12 films absent from that base. The projection is a
bounded measured prototype, not a library-size-scaled guarantee; explicit
movie scope keeps the former three-frames-per-candidate depth. The global frame
rank survives selective hydration so Look evidence and recipe fusion do not
overstate a deep reserve hit. Framing keeps a bounded spatial work set: it
scores the normal 96-row spatial base plus at most 12 cross-film additions from
the post-base pool, including eligible reserve rows, through the same cache or
whole-query live fallback. Remaining candidates stay semantic backfill, and
the two evidence paths are never mixed within one query. The query
image is encoded once. Candidate grids come from a complete compatible Framing
cache when one is active; otherwise every candidate in the bounded spatial
shortlist is encoded through the established live path. Cached and live
candidate evidence are never mixed within one query. This represents
composition and subject position, not pose or motion.

Spatially added reserve rows are fully scored, while reserve rows left in the
semantic backfill retain only their global evidence; neither path has a
calibrated relevance floor. The existing page-wise preference can surface a
deep reserve row ahead of same-film backfill. Treat this as a prototype
candidate-recall experiment, not a
relevance-safe guarantee. Before changing its depth or claiming it improves
visual discovery, human-grade the deep rows' original global rank, distance,
relevance, and displaced repeats across roughly 10-15 image references.

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

The frontend exposes one result action for all five modular facets rather than
a separate Framing shortcut. Choosing Framing, or dragging the same result onto
the Framing tile, adds the same composition source clause to the active recipe;
existing clauses remain within the three-clause limit. Its source-film
exclusion is applied server-side before candidate generation, so source style
cannot consume the bounded reference shortlist. Explicit film scope still
controls the allowed library, and a scope containing only the source film takes
precedence rather than becoming an empty cross-film search.

Text and reference clauses can be combined. The reference result set remains
mandatory; text reranks only those candidates and cannot introduce a visually
unrelated result. The exact matched reference frame is preserved, and matched
text evidence is attached when available.

Search responses are authoritative bounded prefixes. Every search surface
accepts an optional result limit: omission uses the configured 48-result
default, while an explicit request may deepen the same ranking up to the
configured maximum of 200. Below that maximum the backend probes one
additional eligible row and returns `has_more` and `next_limit`; clients never
infer exhaustion from a full page alone. A deeper request returns the complete
prefix and replaces the earlier one, preserving one application of ranking,
deduplication, temporal spread, and film-diversity policy. The frontend may
reveal a prefix in viewport-sized batches, but it does not regroup that prefix
into a separate Best per movie result mode. This bounded contract requires no
cursor or server-side search-session state.

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

`POST /search/recipe` composes one to three explicit JSON clauses without
adding a router, query LLM, model, index, or ingestion dependency. The
multipart `POST /search/recipe/image` variant carries exactly one bounded
uploaded still assigned to either `look` or `composition`, alone or with at
most two text or indexed-scene refinements. The complete recipe therefore
remains bounded to one to three total clauses. Clause IDs and typed-refinement
facets must be unique. Text clauses support broad `all` search plus `scene`,
`words`, `look`, and `mood`; indexed-scene sources support those same focused
facets except `all`, and also support `composition`. Uploaded-image clauses
support only `look` and `composition`; unsupported image facets fail request
validation rather than silently changing meaning. Both recipe variants accept
the same optional bounded result-prefix limit as the standalone search
surfaces.

The adapters deliberately expose evidence already present in the current
system:

- `all` uses normal hybrid text retrieval;
- `scene` searches caption semantic views;
- `words` searches dialogue and OCR semantic views;
- `look` uses the paired PE text/frame space without spatial reranking;
- `composition` uses the indexed source frame and existing spatial reranker;
- `mood` searches only the dedicated mood-and-energy semantic view.

Both uploaded-image adapters embed the still once through the existing PE
image tower. Uploaded `look` retrieves bounded candidates from the global PE
frame space. Uploaded `composition` retrieves the same kind of global
candidates and applies the existing 6x6 spatial-layout reranker. The two modes
are explicit alternatives, not independent fusion votes. Either uploaded-image
set is a mandatory recipe gate, so text or indexed-scene refinements may rerank
it but cannot introduce visually unrelated units. The upload has no source
film to exclude, so explicit movie scope and the normal unscoped diversity
preference apply. Uploaded images are query-bound inputs: they are validated
and decoded for that request, never persisted as library evidence, and
introduce no new vector space or backfill. Multipart recipes reserve the
existing bounded image-work capacity before decoding and hold it through the
serialized model work, so rejected concurrent requests cannot allocate decoded
image buffers first.

The frontend accepts at most one uploaded image. Its picker and an image dropped
on the open workspace assign the upload to Look by default; a direct drop on
Look or Framing assigns that facet immediately. The image is rendered as a
source card inside its tile and can be moved between those two categories or
removed using the same visible modular state. Moving replaces, rather than
copies, the image clause and any target clue. Up to two optional category or
main-text refinements remain visible beside it and use only backend-owned
recipe ranking. Scene, Words, and Mood never pretend to interpret arbitrary
image content; enabling those destinations requires a separately versioned
query-time caption, OCR, or mood adapter with explicit privacy, latency, and
cost boundaries. The standalone image endpoint remains API-compatible, and
the product never maintains a separate browser-fused reference result state.

Source clauses address an indexed unit and, for `look` or `composition`, an
exact frame index. The server resolves the corresponding unit, film, vector,
and file path from the active index; browser-supplied paths or vectors are
never trusted. A scene used as a `mood` source derives its query from the same
labeled mood-and-energy serialization as the indexed view. Every source and
its facet-required evidence is validated before an empty composition target
scope can return no results. Every referenced source unit is removed from the
result set.
Composition retains the ADR-0005 cross-film default and is a mandatory
candidate gate. Its effective result-film scope is resolved before any bounded
clause retrieval and shared by every clause, so source-film hits cannot consume
an auxiliary clause's candidate window. Other clauses may rerank composition
candidates but cannot introduce composition-unrelated results. The uploaded
Look or composition clause is likewise a mandatory gate; global retrieval and,
for composition, spatial reranking remain internal to that one clause.

A recipe containing only one broad `all` text clause delegates to normal text
search, preserving its complete bounded candidate union and diversity behavior,
then adds the recipe match evidence. All other facet adapters return bounded
raw rankings; recipes with multiple clauses combine them using equal
reciprocal-rank fusion inside any mandatory candidate gate. An uploaded image
contributes one ranking regardless of its internal stages. Existing
junk suppression and visual deduplication are applied once. Ordinary unscoped
recipes then use the 30-second defer-only spread; recipes with an uploaded image
or indexed composition source instead retain the established 90-second
reference spread as their sole temporal preference. Final film diversity runs
afterward: unscoped recipes without either mandatory visual gate use the
bounded, saturating repeat-rank policy, while mandatory visual recipes retain
the page-wise, relevance-backfilled preference when unscoped. Explicit movie
scopes preserve strict relevance order for ordinary non-image-gated recipes;
scoped mandatory visual recipes retain the 90-second reference spread but skip
film balancing. Each returned product result includes
stable `keyframe_index` evidence and a `matches` entry for every clause that
retrieved it, including the contributing rank and matched text or frame when
available.

The recipe response describes each resolved source input separately from
result-match evidence, deriving that description from the same resolved
in-memory source input passed to its ranking adapter. Caption, dialogue/OCR,
and mood sources expose the exact effective text used by their adapter. Look and
composition sources expose a global-visual or spatial-visual frame mode and
never fabricate an English description for a vector or learned grid. An
upload produces one source-evidence record explicitly labeled as query-bound
image input and reports `pe_global` for Look or `pe_global+spatial_6x6` for
composition; it likewise never receives generated text.

Focused `scene`, `words`, and `mood` clauses require a complete active
semantic-text profile. They fail explicitly when it is unavailable instead of
silently using the inseparable legacy combined text vector. Broad `all` search
retains the established safe fallback.

## Activation and fallback

The semantic-text table identity includes its model revision, dimensions, and
embedding contract. Its manifest additionally records the complete ordered
view-projection contract. Its manifest must exactly cover the current units
generation before the profile becomes active. A view-contract change
invalidates an older manifest, while unchanged rows in the same compatible
Qwen vector space may be reused by reconciliation.

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
- `scene` uses the current caption annotation and `mood` is a narrow projection
  of current mood/energy annotations rather than an independently learned
  representation; there is no dedicated plot representation.
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
