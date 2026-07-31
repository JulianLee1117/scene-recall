# Scene Recall search architecture

Status: hybrid baseline, frame-level retrieval, and the first real-film review
pool are implemented. The remaining sections describe the staged target.

## Decision

Scene Recall should be a multimodal information-retrieval system, not an LLM
or text-RAG system with images attached.

```text
natural-language query
        |
        v
deterministic parser ---- optional schema-constrained planner
        |                         |
        +-------- SearchPlan -----+
                     |
        +------------+-------------+--------------+-------------+
        |            |             |              |             |
   frame/clip     dense text    dialogue/OCR   typed facets   scene search
     vectors       vectors         BM25          + color        (vibe)
        |            |             |              |             |
        +------------+-------- candidate union ---+-------------+
                                  |
                         intent-weighted RRF
                                  |
                    optional clause-aware reranker
                                  |
                      relevance cutoff + diversity
                                  |
               matched frame, preview, and match evidence
```

RAG belongs above retrieval. It can support grounded follow-ups such as “more
like result 3, but warmer,” explanations, reel building, and questions about a
retrieved moment. It must not be the first-stage retriever or introduce shots
that were not retrieved from the actual index.

There is no universally optimal model blend. The architecture is designed so
that models and weights can be selected against Scene Recall's own judged
queries without changing the product contract.

## What the current baseline proves

The current repair is a useful Layer 1:

- PE text-to-image, PE caption-vector, and lexical candidate lists are fused
  with weighted reciprocal-rank fusion (RRF).
- Credits, logos, title cards, blank frames, and TV-static/freeze-frame
  artifacts are suppressed unless requested. A static camera or still
  composition is valid program content and is not junk.
- Near-duplicate suppression uses visual evidence; time proximity alone no
  longer removes distinct shots.
- All 1,800 existing Fallen Angels keyframes have independent PE vectors.
  Frame candidates collapse to shots by best similarity while preserving the
  matched frame and reconstructed seek timestamp as result evidence.
- A reference-still prototype retrieves 96 global PE frame candidates and
  lightly reranks them with PE's learned final-layer features pooled to a 6x6
  fixed-position grid. Upload and in-result **Similar** inputs both use this
  path without hosted API calls, schema changes, or re-ingestion.
- The API returns rank and per-channel debug evidence.
- Results render in row-major rank order and expose an optional debug view.
- A 42-query, seven-category review pool freezes 504 real candidates with
  blank human-owned grades and category-aware scoring.

It is not the final design:

- `models.text_encoder` is configured as Qwen, but `embed_text()` currently
  uses the visual PE encoder. `img_vec` and `txt_vec` are correlated PE-space
  evidence, not independent visual and text channels.
- Frame retrieval is limited to the one or three sparse keyframes extracted by
  the original ingest. There are no ordered clip embeddings for temporal
  actions or camera motion yet.
- Caption, dialogue, and mood are mixed into one searchable string.
- Shot type, palette, action, people, era, OCR, and junk status are not typed
  evidence.
- The current BM25-like channel scans Python rows. Native indexed FTS is the
  scalable replacement.
- The new Fallen Angels candidates are deliberately ungraded. Search quality
  is observable, but relevance metrics remain unavailable until a person
  supplies judgments.

## Retrieval units and index schema

Use multiple granularities. Every inferred field stores confidence, evidence
time/frame, source model, prompt/schema version, and model revision.

| Table | Purpose | Important fields |
| --- | --- | --- |
| `films` | Exact library scope and production metadata | title aliases, release year, decade, country, language, director, cast, genres, duration |
| `scenes` | Broad vibe, place, narrative, and browse retrieval | time range, summary, place/time, vibe text and vector, representative shots |
| `shots` | Composition and moment-level result unit | scene ID, time range, literal/action/cinematography/vibe text, typed facets, content kind, quality, motion |
| `frames` | Exact visual evidence and thumbnail selection | shot ID, timestamp, path, visual vector, color vector, palette, OCR, people/face evidence, sharpness/black/text coverage |
| `clips` | Ordered temporal evidence | short window times, ordered-video vector, action caption, motion/camera features |
| `utterances` | Dialogue retrieval without contaminating visual text | start/end, original, same-language normalized, optional translation, language, optional speaker cluster, dense vector, FTS field |
| `facet_evidence` | Flexible multi-label facts and provenance | unit ID, facet, value, confidence, evidence frame/time, producer version |
| `embedding_manifest` | Prevent incompatible vector comparisons | table/column, model ID and revision, dimension, instruction, sampling, index version |

Use Arrow scalar and list fields rather than JSON-encoded strings so LanceDB
can use BTREE, BITMAP, and LABEL_LIST indexes and prefilters. Denormalize
frequently scored and filtered facets onto `shots` or `frames`; keep detailed
provenance and long-tail evidence in `facet_evidence` rather than joining it
for every result in the hot path.

### Facets

- `content.kind`: program content, credits, title card, logo, blank,
  transition.
- Shot scale: extreme wide, wide, full, medium-wide, medium, medium close-up,
  close-up, extreme close-up.
- Angle/framing: overhead, high, eye, low, Dutch, POV, over-shoulder, insert,
  single, two-shot, group, centered, symmetric, negative space, silhouette.
- Camera movement: static, pan, tilt, truck, dolly, push, pull, crane,
  handheld, zoom, rack focus.
- Lighting: high/low key, hard/soft, practical/neon, interior/exterior,
  day/night, weather.
- People, objects, and actions: count buckets plus
  subject-predicate-object-setting evidence. Relations must distinguish “a
  woman smoking in a car” from “a woman beside a smoking man.” Named recurring
  people use optional local face clustering and user-confirmed labels;
  uncertain identity or demographic guesses are not searchable facts.
- Vibe: open tags plus continuous valence, arousal, tension, intimacy, energy,
  and surrealism.
- Era: authoritative `film.release_year` is separate from inferred
  `depicted_era` and `visual_style_reference`.

Color should not rely on VLM prose. Compute dominant CIELAB colors, palette,
hue distribution, brightness, saturation, contrast, and warmth locally.
Distinguish the frame's global palette from object-local color evidence so
“red car” does not silently become “a car under red lighting.” Store a small
color vector as its own retrieval channel; keep semantic palette terms only as
supplemental evidence.

### Frame and clip aggregation

Search individual frames and compare maximum, fixed-count top-k, and
frame-count-normalized aggregation on the benchmark; do not let shots win only
because more frames were sampled. Retain the argmax frame ID and the exact
extracted timestamp, including any start-padding adjustment. This gives the UI
the actual evidence thumbnail instead of frame zero, the middle frame, or an
averaged representation.

Use short ordered clips for actions and camera movement. A still-image model
cannot reliably distinguish “sits down” from “stands up” or a pan from a
static composition. For a clip match, choose a sharp, nonblank,
query-relevant frame for the thumbnail and start the preview near the evidence
time.

### Position, pose, and momentum

These are separate retrieval channels:

- **Composition/position** starts from a reference still. The implemented
  prototype blends global visual similarity at 0.65 with corresponding-cell
  6x6 PE patch similarity at 0.35. It deduplicates broad retrieval by shot,
  then computes patch grids for at most 96 candidates. Results prefer
  90 seconds of temporal diversity, then backfill strong adjacent matches if
  needed. This is a general film, animation, music-video, commercial, and
  mixed-media feature: it can match subject scale, normalized screen position,
  and visual mass, but backgrounds and palette still influence it.
- **Body pose** is also general-purpose. It needs people boxes and normalized
  joints, retaining both
  absolute screen coordinates (for match cuts) and root-centered coordinates
  (for pose alone). Stylized animation is an extra domain risk for pose models
  trained on real humans, so no pose metadata should be indexed until overlays
  on representative live-action and animated samples pass review.
- **Momentum** requires a 1-2 second reference clip and dense ordered frames.
  Sparse shot keyframes cannot encode velocity or direction. A later local
  test should compare coarse optical-flow grids after separating camera motion
  from residual subject motion.

The static experiment earns a persistent backfill only if a blind 8-12 query
A/B improves usable precision@10 by roughly 0.20 without collapsing into
same-location or same-palette results. Pose and motion each require their own
small gate before becoming ingest stages.

## SearchPlan

Users keep typing ordinary prose. Deterministic syntax and visible editable
chips are optional power features, for example:

```text
film:"Fallen Angels" year:1990..1999 shot:close-up color:red
dialogue:"exact words" -credits like:<frame-id>
```

A representative normalized plan:

```json
{
  "schema_version": "1.0",
  "raw_query": "red close-up of two women smoking in a car, no credits",
  "session": {"operation": "new", "prior_query_id": null},
  "intent": {
    "primary": "visual",
    "mixture": {
      "visual": 0.30,
      "action": 0.25,
      "cinematography": 0.25,
      "vibe": 0.15,
      "narrative": 0.05,
      "dialogue": 0.0,
      "metadata": 0.0,
      "similarity": 0.0
    }
  },
  "scope": {
    "film_ids": [],
    "titles": [],
    "release_year": {"gte": null, "lte": null},
    "languages": [],
    "countries": []
  },
  "clauses": [
    {
      "id": "c1",
      "polarity": "include",
      "occur": "should",
      "field": "palette.hues",
      "operator": "contains",
      "value": ["red"],
      "boost": 1.2,
      "parser_confidence": 0.99,
      "hard_filter_safe": false,
      "source_span": "red"
    },
    {
      "id": "c2",
      "polarity": "include",
      "occur": "should",
      "field": "shot.scale",
      "operator": "in",
      "value": ["close_up", "medium_close_up"],
      "boost": 1.4,
      "parser_confidence": 0.98,
      "hard_filter_safe": false,
      "source_span": "close-up"
    },
    {
      "id": "c3",
      "polarity": "include",
      "occur": "should",
      "field": "people.count",
      "operator": "gte",
      "value": 2,
      "boost": 1.0,
      "parser_confidence": 0.95,
      "hard_filter_safe": false,
      "source_span": "two women"
    },
    {
      "id": "c4",
      "polarity": "include",
      "occur": "should",
      "field": "actions",
      "operator": "semantic",
      "value": ["smoking"],
      "boost": 1.4,
      "parser_confidence": 0.99,
      "hard_filter_safe": false,
      "source_span": "smoking"
    },
    {
      "id": "c5",
      "polarity": "exclude",
      "occur": "must",
      "field": "content.kind",
      "operator": "in",
      "value": ["credits"],
      "boost": 1.0,
      "parser_confidence": 1.0,
      "hard_filter_safe": true,
      "source_span": "no credits"
    }
  ],
  "retrieval": {
    "visual_query": "two women smoking inside a car, red lighting, close view",
    "text_query": "two women smoking in a car; red-lit close-up",
    "dialogue_query": null,
    "ocr_query": null,
    "temporal_query": "women smoking while riding in a car",
    "candidate_k_per_channel": 100
  },
  "similar_to": {
    "unit_id": null,
    "frame_id": null,
    "aspects": [],
    "weight": 0.0
  },
  "presentation": {
    "limit": 12,
    "group_by": "scene",
    "max_per_group": 2,
    "strictness": "balanced"
  },
  "ambiguities": []
}
```

The planner does not choose unrestricted numeric ranking weights. It selects a
versioned intent profile; the server maps that profile to bounded weights
tuned on evaluation data.

### Parser and fallback

1. Parse quoted phrases, explicit negatives, years/ranges, known field
   operators, colors, shot vocabulary, people counts, and resolved film titles
   deterministically.
2. Use an optional schema-constrained local or hosted model only for compound
   unstructured prose. Validate every enum and retain its exact source span.
3. Preserve ambiguity. “The 90s” may mean release era or a scene that looks
   1990s; unresolved alternatives stay soft and become editable chips.
4. If the planner is unavailable, run the deterministic parse and raw query
   over all default channels. Search must not depend on Ollama or a cloud API.
5. Cache normalized plans and query embeddings.
6. Detect query language. Exact dialogue operates over original and
   same-language normalized text; cross-lingual dense retrieval and optional
   stored translation provide fallback without pretending a translated phrase
   was an exact subtitle match.

Only exact, authoritative, explicit facts are safe hard prefilters: resolved
film scope, release year, country/language metadata, access rules, explicit
time bounds, and high-confidence content-kind exclusions. Vibe, shot type,
camera movement, palette, setting, people count, identity, action, and visual
era remain strong soft constraints by default because they are inferred.

Quoted dialogue uses exact/normalized phrase retrieval first and fuzzy or
dense fallback second. Negative semantic concepts are never retrieved as
positive embedding queries. Typed negatives filter or penalize; ambiguous
negatives are verified during reranking.

## Candidate channels

Run independent lists in parallel, normally retrieving 50 to 100 candidates
per active channel:

1. Per-frame cross-modal visual search for objects, palette, framing, faces,
   setting, and appearance.
2. Ordered-clip search for action, motion, and camera movement.
3. Dedicated dense text search over separate literal, action/narrative,
   cinematography, vibe, and scene-summary fields.
4. Fielded native BM25/FTS over caption, dialogue, OCR, people/title names, and
   metadata. Dialogue phrase search keeps token positions and stop words.
5. Structured facet and objective-color scoring, with exact metadata matches
   as their own rank lists.
6. Scene-level dense search for broad vibe or narrative queries.
7. Aspect-specific more-like-this using visual composition, palette, action,
   vibe, dialogue, or a weighted combination.

For compound queries, retrieve the normalized whole query and two to four
positive atomic clause queries, then union them. The reranker rewards results
that satisfy the conjunction and verifies subject-action-object-setting
relations. This avoids a generic hub shot winning because it matches only
“car” or only “red.”

## Fusion, reranking, and diversity

Start with intent-weighted RRF. Vector cosine values, BM25 scores, facet
confidence, and color distances are not comparable raw scales. RRF is robust
before sufficient relevance judgments exist and remains the fallback even
after learned ranking is introduced.

Initial intent profiles are priors, not claims of optimality:

| Intent | Emphasize |
| --- | --- |
| Object, composition, shot scale, color | frame visual + facets/color, then literal text |
| Action or camera movement | ordered clip + action text, then frames/facets |
| Vibe or style | scene/shot vibe text + frame/clip visual |
| Dialogue or OCR | exact FTS, then dense text and temporal visual context |
| Person/count | confirmed face/people facets + frame visual |
| Production era | authoritative film metadata |
| “Looks like an era” | visual/style evidence; never the release-year field |

Rerank the top 30 to 50 using query, fielded candidate text, clause evidence,
and negatives. Use a text reranker for the cheap default. A multimodal
reranker can inspect the top 12 to 24 ambiguous visual candidates with two to
four matched frames or a short ordered clip.

Calibrate the final relevance threshold per intent and allow fewer than 12 or
no results. Do not pad a grid with weak matches.

Diversify only after relevance:

- collapse frames/clips to their shot;
- use MMR-like visual and scene penalties;
- start with at most two browse results per scene;
- do not enforce a four-result film cap in a one-film library;
- relax diversity for exact dialogue and known-item searches;
- never classify two shots as duplicates from timestamp distance alone.

Return match evidence:

- chosen frame and timestamp;
- matched/violated clause IDs;
- channel ranks and raw channel scores;
- reranker and calibrated scores;
- applied and relaxed filters;
- junk and diversity decisions;
- model/index versions and per-stage latency.

## Local and hosted model choices

The RTX 5070 Ti has 16 GB VRAM. High-volume retrieval should be local; hosted
models are most useful for offline annotation or optional complex query
planning.

### Recommended baseline and A/B candidates

- Keep PE-Core-L/14-336 for the immediate per-frame index. It is already
  installed, fast, and effective for still-image recall. Its short text
  context and shot averaging make it unsuitable as the only text/action
  system.
- Actually use Qwen3-Embedding-0.6B for caption, dialogue, scene, and vibe text
  vectors. It is a separate, instruction-aware text space and is practical on
  this GPU.
- Shadow-test Qwen3-VL-Embedding-2B as the likely local upgrade for text,
  images, mixed inputs, and ordered video. Store its vectors separately and
  select it only if it improves Scene Recall's category-level candidate
  recall. SigLIP 2 is an A/B alternative, not an automatic upgrade over PE.
- A/B Qwen3-Reranker-0.6B for fielded text candidates and
  Qwen3-VL-Reranker-2B for the small multimodal shortlist. The official
  multimodal benchmark does not show a gain on every video slice, so the
  reranker must earn its latency on the film benchmark.
- Avoid an always-resident 8B embedding or reranker on 16 GB. Load 2B models
  sequentially or with conservative frame/pixel budgets if simultaneous
  residency is unstable.
- Add audio retrieval only if real queries demand soundtrack, speech tone, or
  ambient sound; PE-AV is a plausible later local channel.

The user-facing fast path should return fused index results first. An optional
quality mode may progressively replace them after multimodal reranking. Warm
targets are under one second for the fast path and under four seconds for the
quality path, but they are targets to benchmark on this Windows machine, not
assumptions.

### OpenAI's role

Keep OpenAI as the current high-quality offline visual annotator for new or
selectively re-annotated shots. Use schema-constrained output for versioned
facets and cache every artifact by media hash, model revision, prompt version,
and schema version so re-running ingest does not repay unchanged work.

Offline annotation is suitable for the Batch API, which supports Responses
jobs at lower cost with a 24-hour window. OpenAI text embeddings are a viable
hosted alternative, but a local text encoder avoids recurring query/ingest
cost and keeps the search path available offline.

Gemini is optional provider compatibility, not an architectural dependency.

## Evaluation

Model selection and weight tuning must start with real judgments. Seed a
carefully judged 30-to-50-query Fallen Angels benchmark using the reported
broad searches, the six known-item needles, compound constraints, negations,
and genuine no-match cases. Use it to validate the frame migration. Then
expand through challenger pooling to this 150-query set:

| Category | Queries |
| --- | ---: |
| Literal object/person/place | 20 |
| Action/motion/temporal relation | 20 |
| Cinematography | 20 |
| Color/lighting | 20 |
| Vibe/style | 20 |
| Dialogue/OCR | 15 |
| Narrative moment | 15 |
| Negative, junk, and genuine no-match | 10 |
| More-like/follow-up | 10 |

Include at least 50 single-constraint queries, 60 with two to three
constraints, 20 with four or more, 20 contrast/negation pairs, and 20 genuine
no-match queries. Pool the top 30 from baseline and challenger systems plus
random hard negatives. Two blind judges grade 0 (irrelevant), 1 (partial), 2
(good), or 3 (exact), record acceptable time intervals, and score each clause.
Adjacent same-scene shots are important hard negatives. Until exhaustive
judgments exist, label Recall@K honestly as recall over known pooled positives.

Track:

- candidate Recall@100;
- nDCG@10 as the primary graded metric;
- Success@1/5 and MRR@10 for known-item and dialogue queries;
- temporal recall at multiple IoU thresholds for moment localization;
- required-clause coverage and must-not violation rate;
- junk@10, duplicate@10, and unique-scene coverage;
- no-match false-positive rate and precision at the display cutoff;
- matched-thumbnail accuracy;
- warm/cold p50 and p95 latency, VRAM, index size, ingest time, and API cost;
- macro and per-intent results plus channel ablations.

Provisional release gates, frozen after the first judged baseline:

- candidate Recall@100 at least 95%;
- nDCG@10 at least 0.75 overall and 0.65 in every evaluated category;
- Success@5 at least 85%;
- exact-dialogue MRR@10 at least 0.90;
- explicit-negative violations at most 2%;
- unsolicited junk in the top ten at most 1%;
- duplicate rate in the top ten at most 10%;
- matched-thumbnail accuracy at least 90%;
- no-match false-positive display rate at most 10%;
- warm end-to-end p95 at most 2.5 seconds.

If those absolute gates are unrealistic after judging the first baseline,
require at least a 15% relative nDCG gain, no per-category regression greater
than 0.05, and no regression on junk, negatives, duplicate, or thumbnail
safety gates.

One film cannot validate release-era, cross-film, or named-person
generalization. Add three to five visually diverse films for the first
cross-film model decision, then grow toward at least eight films and 500
queries. Learn fusion-to-rank only after roughly 500 to 1,000 reliable
query-result judgments. Keep RRF as fallback, and prefer explicit
wrong-object/action/vibe/shot/frame feedback over raw clicks.

## Staged migration

### Layer 0 — complete the measured baseline

- Validate the current hybrid RRF, junk filter, visual/temporal dedupe,
  matched rank display, and debug contract.
- Preserve the six existing specific-search smoke cases.

### Layer 1 — evaluation first (pooling complete; judgments pending)

- Replace the placeholder-only evaluation with the graded Fallen Angels set.
- Start with 30 to 50 queries and grow to 150 after the first local challenger.
- Add pooled qrels, category metrics, thumbnail judgments, latency, and
  channel-ablation reporting.
- This uses no API calls and no media re-ingest.

### Layer 2 — local, no-paid reindex (frame slice complete)

- Persist all 1,800 existing keyframes as `frames` with independent PE vectors.
- Populate authoritative film metadata plus basic scene boundaries/IDs so
  release-era filters and scene diversity have real fields before the planner
  depends on them.
- Return the actual best frame and evidence timestamp.
- Replace the Python lexical scan with native fielded FTS.
- Split dialogue/utterances from visual captions.
- Add Qwen3-Embedding-0.6B text vectors.
- Compute palette, luminance, contrast, text coverage, quality, and initial
  content-kind features locally.
- Version the schema and embedding manifests. Exact scan is adequate at this
  corpus size; defer ANN complexity.

### Layer 3 — query understanding

- Add deterministic SearchPlan parsing and editable interpretation chips.
- Add the optional constrained local/OpenAI planner only for compound prose.
- Add whole-query plus atomic-clause retrieval, typed negatives, soft
  constraint coverage, and calibrated no-match behavior.

### Layer 4 — shadow visual/video upgrade

- Re-embed existing frames with Qwen3-VL-Embedding-2B in a shadow column.
- Build one short ordered clip representation per shot or temporal window.
- Compare candidate recall, category nDCG, thumbnail accuracy, latency, and
  VRAM against PE before switching.

### Layer 5 — precision and richer metadata

- A/B the local text and multimodal rerankers.
- Only if evaluation shows a gap, perform cached structured reannotation,
  scene summaries, richer action windows, face clusters, or audio indexing.
- For paid annotation work, submit only missing schema/model versions and use
  offline batching.

### Layer 6 — conversational RAG

- Add grounded plan refinement, result explanations, Q&A, and reel creation
  after retrieval evidence is trustworthy.

The immediate next decision is driven by human grades. The likely next local
slice is separate dialogue/visual text retrieval plus typed shot-scale and
objective-color evidence; those directly address broad queries such as
“close-up” and “red” without another paid annotation pass. Ordered clip
retrieval follows for actions and camera movement.

## Primary references

- [LanceDB hybrid search and default RRF](https://docs.lancedb.com/search/hybrid-search)
- [LanceDB BM25 full-text search](https://docs.lancedb.com/search/full-text-search)
- [LanceDB metadata filtering](https://docs.lancedb.com/search/filtering)
- [LanceDB multivector search](https://docs.lancedb.com/search/multivector-search)
- [LanceDB reranker evaluation](https://docs.lancedb.com/reranking/eval)
- [PE Core](https://github.com/facebookresearch/perception_models/tree/main/apps/pe)
- [Qwen3 text embedding](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B)
- [Qwen3-VL embedding and reranking](https://github.com/QwenLM/Qwen3-VL-Embedding)
- [CLIP](https://proceedings.mlr.press/v139/radford21a)
- [ColBERT late interaction](https://arxiv.org/abs/2004.12832)
- [MovieNet](https://arxiv.org/abs/2007.10937)
- [Vision-language negation limitations](https://openaccess.thecvf.com/content/CVPR2025/html/Alhamoud_Vision-Language_Models_Do_Not_Understand_Negation_CVPR_2025_paper.html)
- [OpenAI Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs)
- [OpenAI Batch API](https://developers.openai.com/api/docs/guides/batch)
- [OpenAI embeddings](https://developers.openai.com/api/docs/guides/embeddings)
