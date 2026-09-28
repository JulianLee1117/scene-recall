# ADR-0053: Neutral semantic query instruction

- Status: Accepted
- Date: 2026-09-14
- Supersedes: None
- Superseded by: None

## Context

The user reported the same weakly related scenes appearing across different
searches. Reproducing `red` found three film-camera captions near the top of the
semantic channel: Oppenheimer unit suffix `_2224` at text rank 1, City of God
`_1323` at text rank 3, and Mirror `_0121` at text rank 4. Their fused product
positions were 4, 5, and 6 respectively. These are semantic retrieval errors
before film balancing, rather than a reason to hide those films or scenes.

Every semantic query received an instruction about film-shot evidence,
remembered dialogue, visible content, cinematography, mood, and narrative
moments. Those added concepts contaminated short queries and favored captions
about filmmaking. Weighted reciprocal-rank fusion then gave the erroneous
semantic leaders their normal vote. The existing one-word lexical policy
already omitted a separate full-text vote for `red`; changing lexical weights
or diversity would not correct this query representation.

Local instruction ablations compared the existing prompt, a generic retrieval
instruction, bare query embeddings, and concise description-matching
instructions against the same stored document vectors. Generic and bare
variants exposed incidental word matches in sampled queries. A concise
description instruction removed the film-production bias but also promoted
incidental dialogue for some visual searches. Keeping the task's scene context
in a concise instruction better preserved scene-description evidence without
the earlier list of filmmaking concepts. This comparison does not establish
universal retrieval quality or eliminate every recurring weak result.

The first rollout also exposed unnecessary coupling between query policy and
document readiness. A long-running unrelated ingest held the index resource
lock and would later publish a v1 instruction manifest. Query instructions
never generated document vectors, so requiring a fresh document reconciliation
solely for a query prompt would delay the correction and misclassify that
older worker's compatible publication as unusable.

## Decision

Use `Retrieve scene descriptions matching the query.` as the semantic query
instruction, versioned as `scene-recall-semantic-query-v2`. The user's text
supplies the concepts within the scene-description task. Do not inject a list
of film, cinematography, dialogue, or narrative examples into every query, or
add a special color vocabulary or scene blacklist to compensate for this
failure.

Keep the configured Qwen model and revision, document embedding contract 1,
independent semantic views, PE visual channel, lexical policy, rank fusion,
candidate depths, facet adapters, and diversity unchanged. This is a correction
to the existing query representation, not a new model, reranker, or index.

Keep query instruction text and version in the manifest as producer provenance,
independent of the query policy selected by the running API. Accept only the
exact recognized v1 or v2 text/version pairs. Reject unknown or mismatched pairs,
and keep every model, revision, dimension, embedding/view contract, and complete
table-generation check. This compatibility does not authorize a partial index,
rewrite a live manifest, or bypass the ingestion resource lock.

After the code update, restart the API to activate v2 queries against compatible
complete document evidence. An older worker may continue and publish its v1
manifest; the new API accepts it only when all document readiness checks pass.
Record the actual serving query policy separately in semantic result debug and
evaluation provenance, so the producer's instruction cannot be mistaken for
the instruction used to retrieve a result.

The existing full `index-text` reconciliation remains available for repair or
refreshing producer metadata. It verifies current complete evidence coverage
and reuses unchanged document vectors, table identity, and table generation.
No backfill is required solely to select the compatible v2 query policy. Do not
bump the document embedding contract merely to change a query prompt.

## Consequences

- Short queries no longer acquire the previous instruction's film-production
  concepts. Real-library comparisons remain necessary to assess relevance
  across color, content, mood, literal words, and compound descriptions.
- The correction applies through existing semantic adapters without adding
  visible search controls or per-scene exceptions.
- Document activation remains complete while query policy can evolve
  independently. Regression tests verify recognized historical provenance,
  rejection of unknown or mismatched pairs, strict generation checks, and
  optional reconciliation without changing vectors or their table generation.
- Raw films, timestamped evidence, annotations, and compatible indexes remain
  available. No hosted calls, new derivation, or film reingestion are required
  for an otherwise current text profile.

## Validation and limits

A diagnostic compared 11 queries against the same published units, frames,
and text generations, using the full hybrid pipeline before and after this
instruction change. Queries covered red, blue, snow, kissing, loneliness,
rain, beach, explosion, a camera crew, a solitary walk in a snowy forest,
and remembered dialogue. The reported camera captions disappeared from the
first page of `red`; inspection of its twelve matched keyframes confirmed
red imagery. Camera scenes remained eligible for the camera-crew query.
Rain, kissing, and loneliness retained topical visual scene descriptions.
These are diagnostic judgments, not a frozen human-graded acceptance set or
proof of a globally optimal instruction.

The opt-in `pipeline/tests/test_semantic_query_relevance.py` runs five
pairwise checks with the pinned local Qwen model. Set
`SCENE_RECALL_LOCAL_TEXT_EVAL=1` and `HF_HUB_OFFLINE=1` with its weights already
cached. The red regression passes with v2 and fails when only the old v1
instruction is restored. Normal tests skip model inference. Separate tests
verify historical producer compatibility, invalid provenance rejection,
generation invalidation, and served query-policy provenance.

Residual image-channel mistakes remain possible: in this comparison `snow`
promoted an existing image-only black-and-white seaweed/sand result from rank
33 to 12. Long compound queries still retrieved some partially matching
forests. This correction removes the demonstrated instruction bias; it does
not establish calibrated relevance or complete visual understanding.
