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

reference image
  -> PE frame candidates
  -> bounded spatial reranking

reference image + text
  -> mandatory reference shortlist
  -> text reranks only that shortlist

result
  -> film + timestamp + matched frame/text + playable media
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
but only within the same film identity. Missing or temporarily unavailable
source/index data leaves an explicit unavailable bookmark rather than silently
rebinding or deleting user state.

## Implemented dataflow

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

### Reference retrieval

Reference-image search retrieves PE frame candidates and spatially reranks a
bounded shortlist using an on-demand learned 6x6 feature grid. This represents
composition and subject position, not pose or motion.

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

The PE visual tables are a frozen legacy exception. Frame rows contain only a
coarse encoder name, unit rows lack exact visual-model lineage, and the loader
does not pin an immutable upstream checkpoint. Search and publication reject a
configured encoder-name mismatch, but this baseline must not be rebuilt
piecemeal after upstream weights change.

Any future visual or multimodal replacement must use a separate versioned table
with exact revision lineage and its own activation manifest.

## Known limitations

- Film, frame, and unit writes are not cross-table atomic.
- The legacy PE baseline lacks immutable checkpoint lineage.
- Hosted annotations record the requested model identifier, not a
  provider-resolved immutable revision.
- Sparse keyframes cannot reliably encode temporal direction, brief action, or
  camera movement.
- Dialogue is embedded at shot level rather than as utterance rows.
- OCR comes from the general annotator rather than a dedicated OCR pass.
- Typed facets are not first-class query filters.
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

Paid library-wide processing, global model activation, and removal of a working
fallback require the small comparison above. Structural versioning, cache
preservation, and safe backfills do not require a permanent golden corpus.

## Current next action

Grade the paired position/relation prompts in
`pipeline/eval/compositional_queries.yaml` before changing retrieval. If useful
shots are absent from the pooled candidate window, test a separate versioned
grounded layout representation on a representative subset. If those shots are
present but ordered poorly, shadow-test a shortlist reranker instead. A query
router or LLM decomposition layer is not a substitute for evidence the index
does not contain and must not become an always-on dependency without the same
paired comparison.

If action or camera-motion failures recur, the next experiment is one separate
temporal profile over a small action-heavy subset, not a full-library migration.
Temporal, audio, reranking, routing, RAG, pose/face analysis, scene graphs, and
speculative metadata remain deferred until they pass the decision gates.

Historical rationale is recorded in
[`docs/decisions/`](decisions/README.md).
