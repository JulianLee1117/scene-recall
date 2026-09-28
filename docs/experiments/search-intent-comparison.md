# Search interpretation and evidence comparison

Status: bounded experiment; ordinary search remains local and unchanged.

## Questions this experiment can answer

Does a small hosted decision model improve the ordering of already retrieved
scenes? Does interpreting mixed query intent add value beyond judging candidate
evidence? Do those gains justify added latency and another service dependency?

This first comparison freezes the existing API's result pool. It cannot measure
whether a different retrieval policy would find an absent scene, nor establish
that a classification layer should become mandatory. A failed ordering policy
does not establish that every integration of the model would fail.

## Frozen queries and evidence

`pipeline/eval/search_intent_queries.yaml` contains 25 queries grouped around
inspected source anchors. Paraphrases are deliberately correlated; count the
groups as well as individual queries. Include visual content, framing language,
appearance, mood, remembered scenes, dialogue and on-screen words. Explicit film
IDs are authoritative and remain identical across variants.

Expected windows carry evidence status. An inspected still supports visible
properties at that frame; it does not establish movement, spoken dialogue,
interpersonal intent or plot causality. Timed-text examples are provisional
until heard against the source. Open-ended mood queries can have many relevant
answers. Expected-window hits are diagnostics, not substitute human relevance
grades. Negative distinctions and known misleading captions are retained.

Capture the warm local API sequentially with at most 200 results per query.
Record complete returned evidence and channel ranks, query/fixture fingerprints,
capture latency and observed table versions before/after each request. Reject a
case if those versions change. On-disk configuration and code hashes do not prove
which versions an already-running API loaded; retain that limitation explicitly.
No second GPU model process, index preparation or film ingestion is required.

The repeatable command is `pipeline.experiments.capture_search_intent`; see
[README commands](../../README.md#frozen-search-decision-comparison). It accepts
at most 30 fixture queries, each within the API's 500-character limit. It never
overwrites or resumes an output. A failed/interrupted capture retains explicit
partial evidence with an empty replayable case list. Matching externally
observed table versions are a stability check, not proof of an internal pinned
API snapshot or an unchanged whole-library snapshot across every query.

## Four variants

1. **Baseline:** exact captured API order.
2. **Intent:** three independent decisions about visible, semantic and literal
   intent add a bounded contribution from existing img/txt/lex channel ranks.
3. **Evidence:** judge supplied textual evidence as supported, partial,
   contradicted or unknown, adding a bounded support contribution.
4. **Both:** combine the same intent and evidence contributions, reusing both
   responses so the comparison does not introduce different model samples.

Only the first 48 candidates can be reordered; the remaining tail stays in its
captured order. All candidates and explicit film constraints remain. Missing
evidence receives no support bonus and is not silently excluded. Truncation and
evidence hashes are recorded. The experiment has no image input or generated
query rewriting. Three retrieval-channel signals are not a complete user-facing
category taxonomy and cannot split dialogue from OCR in the current Words route.

Hosted execution requires explicit bounded calls and spending admission. Record
requested/returned model identity, prompt/input hashes, latency, token usage,
cost and failures. No automatic provider retries. The preflight cost reserve is
a conservative estimate, not a provider-enforced account billing cap. Failure
keeps the captured baseline available and never authorizes production activation.

## Review and promotion

Blind labels hide variant identity. Grade candidate relevance independently of
model judgments; keep playback and interpretation status explicit. Compare first
useful result, relevant results in the first five, target-window ranks, repeated
film/scene concentration and rank changes. Report query groups, incomplete
grades and unknowns. Measure the model stage separately from captured API time;
an offline replay does not establish production end-to-end or tail latency.

Promotion requires a reviewed improvement on held-out descriptions, acceptable
slow-request/failure behavior, and an explicit subsequent architecture decision.
Do not select a winner from schema validity, confidence, result overlap or one
successful remembered scene.

## Category interaction to test next

The proposed interaction has three distinct concepts:

- **Scope:** inline confirmed movies, followed later by trusted exact filters.
- **Description and optional focus:** one natural prompt can mix content,
  appearance, framing language and feeling. A deliberate refinement expresses
  what matters; automatic interpretation does not create a row of compulsory
  tags. A refinement must also work with the main description empty.
- **Reference:** selecting or dropping a scene leads to the same small choice
  of what to match. Appearance, framing and mood are different requests; dragging
  is a shortcut, not a prerequisite for discovering the feature.

Compare a compact **Add detail** entry against always-visible compact fields
using the same search behavior. Start with these tasks, without explaining the
controls beforehand:

1. Find the remembered booth interaction from a natural sentence and a movie.
2. Find a medium shot of two people with cold lighting and emotional distance.
3. Start with only a visual or mood refinement and discover usable scenes.
4. Use a result's framing while asking for a different situation or appearance.
5. Find an audible phrase, then find visible words on a sign.

Observe whether people can start, explain the active constraints, change one
aspect, remove a reference and return to broad search. Record time to a useful
played result and accidental constraints. Do not call agent checks a user study.

Dialogue and on-screen words are separate proposed intentions. Production Words
still combines both until all relevant backend routes are deliberately split.
Shot language works in the main description; the present Framing reference gate
must not be relabeled as an exact shot-type filter or a harmless soft preference.
Plot/context controls should only appear after usable evidence and retrieval
exist. This protocol does not approve a category redesign or activate context.

The initial prototype should make one clear promise: **Refine one aspect**.
Keep the main sentence visible and let a user add, edit or remove a focused clue
without navigating a separate search mode. Describe the aspect in ordinary
language: what happens, visual appearance, mood, or framing from a reference.
Do not require people to understand embedding channels. A text-only refinement
still runs with an empty main bar. A result's **Use as reference** action and
dragging should open the same aspect chooser; avoid an invisible default that
matches every property of a source scene. Dedicated Dialogue and On-screen text
can live in this optional refinement entry once their backend routes are split.
These are prototype labels and interaction hypotheses, not newly shipped controls.

## First pilot — 2026-09-20

All 25 cases completed across four variants. No production search or ingestion
configuration changed. The fixture contains ten reference groups across eight
films, with correlated paraphrases. Many anchors were found using existing
captions/OCR before inspecting retained frames; this is a diagnostic collection,
not a random sample or independent held-out relevance test. Human preference,
played relevance and source-audio verification remain pending.

Local artifacts live under `pipeline/eval/runs/search-intent-20260920/`:

- `capture.json`: original API evidence; file SHA-256
  `6d7334f4923412844c791b94100f62ad40248b63cb0e3deb7f0989b1d282b1ee`.
- `comparison/`: interrupted first transport run, retained receipts,
  `implementation.json` and explicit `recovery.json` accounting.
- `comparison-pooled/`: completed `run.json`, frozen inputs/requests, raw
  responses, exact cache receipts and implementation identity.
- `comparison-pooled/reference-window-audit.json`: source-hashed follow-up
  separating exact anchor hits, declared scene-window hits and nearby shots.
- `comparison-pooled/blind-review.json`, separate `blind-key.json` and
  `review-playback.html`: current generated review data/page; grades and
  preference remain unset. The earlier `review.html` is retained as the initial
  artifact and does not resolve prepared playback representations.

The current page resolves 88 distinct displayed films through the local API:
seven prepared representations and 81 original-source URLs, with none
unresolved at generation time. These are source URL receipts, not codec,
audio, playback or relevance verification. No media was generated. Recreate
with `--resolve-playback` and a new output filename if those representations
change. Offline generation leaves playback explicitly unresolved.

### Diagnostic findings

- The scoped remembered-booth query includes unit `0207` at **27:31–27:50**,
  within the declared **26:53–28:09** booth window: baseline rank 11, combined
  rank 8. The inspected exact unit `0206` is absent. The unscoped wording has
  no overlapping booth-window candidate among 175 returned rows; reranking
  the same pool cannot recover it. “Romantic tension, but not kissing” includes
  neighboring booth unit `0205` at rank 30 baseline and 24 combined.
- The literal/paraphrased Matrix screen queries put a nearby monitor shot
  first, but that shot ends 6.67 seconds before the inspected “Wake up, Neo…”
  screen. It is a useful neighboring starting point, not a verified hit for
  those visible words. The “not somebody saying it” variant promotes a nearby
  sleeping-face shot with a dialogue match from fourth to second; the captured
  evidence still does not establish the requested on-screen text.
- The inspected Dune profile anchor moves from **18 → 25 → 6 → 8** across
  baseline, intent, evidence and both. A second description of that anchor
  moves from first to second under intent. The Shining hallway-girls anchor
  moves from second to first under evidence. These are mixed reference-rank
  changes, not human-judged quality gains.
- For “an elderly gray-haired man bathing alone,” every variant keeps the
  Moonlight child-bath shot first. Its incorrect caption matches that wording,
  so the textual judge labels it supported. This is evidence support relative
  to a bad annotation, not verification of the image. The model cannot repair
  source mistakes from the same mistaken text.

Exact-unit absence must not be reported as whole-scene absence. A similarity
deduplication stage could account for some neighboring substitutions, but this
capture has no pre-dedup candidates or rejection reasons, so that cause remains
unverified. Preserve exact anchors and declared broader scene windows separately;
do not invent wider target windows after seeing results. Three timed-text
references remain provisional until source-audio alignment is checked.

### Cost and timing

The completed comparison contains 50 decision responses: 11 reused exact
receipts and 39 new calls. Returned identity was `typesafe/jev-1.13-20260917`.
Known provider-reported cost across both runs is **$0.01743399**, plus one
unresolved attempt from the interrupted first run whose charge is unknown.
Its request identity and reserve are recorded in `comparison/recovery.json`;
the successful recovery had no unpriced attempts or fallback components.

The original fresh-connection transport repeatedly waited on failed IPv6
connections before reaching IPv4. The isolated experiment was stopped and
recovered with a pooled HTTPX client and a bounded per-address connect timeout;
system networking and running services were not changed. Completed receipts
were preserved, with a new-call ceiling covering only the remaining decisions.

For the 39 new calls, recorded stage medians were **0.800 s** for intent and
**0.923 s** for evidence. These include input/receipt/journal work and do not
measure model computation alone. This run predates the new `transport_seconds`
field; no transport-only latency is inferred retroactively. Subsequent runs
record HTTP invocation separately, including encoding/body read/JSON decode but
excluding client initialization and bookkeeping. Neither offline field proves
interactive p95 or complete production latency. Do not add these medians to
capture medians and present the result as an end-to-end measurement.

### Verification and next decision

The final checks passed 83 focused frontend tests, 99 experiment/capture/review
tests and frontend type checking. Live search-bar checks covered desktop and narrow
viewports, horizontal caret visibility, mention placement, Escape, boundary
deletion and duplicate-film scope. The browser tool's URL policy blocked opening
the standalone local HTML review file. No alternate serving surface was used;
the page's data/controls have code tests, but live rendering and playback QA
remain incomplete.

First review a few distinct groups in the blind page, then add held-out wording
before tuning any score. Diagnose missing candidate coverage separately from
ranking and add evidence for subtitle timing/annotation defects. In parallel,
test the two small refinement presentations with the same existing search
semantics. Keep Jev optional and replaceable until relevance, slow-request
behavior and interaction evidence justify a specific integration.

## Independent follow-up while user review is unavailable

The user continued the work from remote mobile and authorized independent
verification. This does not turn agent observations into human preference or
played judgments. The original frozen comparison and its empty human grades
remain intact; follow-up evidence has separate source-hashed receipts.

### Direct inspection of displayed frames

`visual-inspection-20260921/inputs.json` and `observations.json` under the pilot
run directory record 22 retained-frame cells across four query groups. The
sample is the union of each variant's top three displayed frames plus the
predeclared anchor. Treatment identities and prior findings were known; this
is a purposive visual audit, not a blind relevance study.

The hallway query's evidence variants visibly promote shots showing the two
girls at the far end of a long hallway over a closer view and a furnished-room
view. That supports a narrow improvement for the requested spatial relation.
The silhouette query is mixed: the baseline's first dark profile faces left,
intent promotes a right-facing dark profile but also an ambiguous rear view,
and evidence promotes illuminated faces, including a left-facing woman. The
inspected Dune anchor is a strong visual match, but moving that one anchor up
does not prove that all earlier results improved. Still-level observations do
not grade whole-shot movement or temporal relevance.

The Matrix anchor visibly contains the complete green phrase on a black
monitor. This is nonempty picture content regardless of its dark background.
The Moonlight bath frame again shows that white lather does not establish gray
hair or old age; textual judging cannot verify that mistaken annotation.

### Media readiness, without claiming browser playback

`media-readiness-06e6f89d/audit.json` records eight canonical-source checks.
All eight sampled content identities match, source stat fingerprints stayed
stable, source/index/fixture durations agree, all ten anchor intervals and 758
displayed-row intervals are within source bounds, and the frozen resolver URLs
remain current. Eight tiny range reads returned HTTP 206. Eight video frames
and 15.251 seconds total selected-audio PCM decoded without errors, with nonzero
samples. These are bounded transport/decoder checks, not full-film integrity,
heard dialogue alignment, language verification or browser support tests.

Fallen Angels has HEVC video and E-AC-3 audio in MKV, and its current playback
resolver supplies the original source with no prepared profile found. That
remains an unresolved browser compatibility risk under ADR-0062. The Shining
also uses HEVC, with AAC audio; container/codec success in FFmpeg cannot prove
mobile playback. No full-film proxy, transcode, source change or new hosted
request was made. Browser rendering/playback of the local review page remains
unverified because of the previously recorded tool URL-policy block.

### Candidate-loss diagnosis and small interaction fixes

`candidate-loss-audit-20260921/diagnosis.json` pins the captured table versions
and records 874 bounded unit reads plus seven native full-text queries, without
loading an encoder. Matrix unit `0122` ranks first in scoped literal and
unscoped paraphrase FTS, and 22nd in the “not somebody saying it” query. The old
caption filter classifies it as blank solely because of “black screen”. None
of its captured survivors crosses the duplicate threshold for that unit. This
proves an erroneous filter, but does not reconstruct all unscoped fusion ranks.
The correction is recorded in ADR-0087; existing API processes require their
next normal restart to load the Python change.

`eligibility-check.json` replays the shared search implementation against those
captured-version rows with both encoders disabled. In this lexical-only check,
the Matrix target changes from absent to first for the literal and paraphrased
queries, and to 33rd for the negative spoken-word query. These are not fresh
hybrid-search measurements or a claim about the running API's ranking. A bounded
sample of the first 100 stored caption matches changes from 99 rejected to 89:
newly eligible descriptions contain light, fog, foliage, silhouettes, a face,
a smartphone and a theater. The sample is neither representative nor exhaustive.
Actual empty descriptions and two explicit credit/title-card forms remain
suppressed. Source playback/human relevance were not inferred from caption review.

The booth result is different. Its exact anchor is absent from remembered-query
FTS. A visual description retrieves it first. The scoped remembered result
includes neighboring unit `0207` at rank 11 with cosine 0.98486 to the anchor;
the romantic query includes `0205` with cosine 0.987159. Those are compatible
with duplicate suppression if encountered earlier, but the missing dense lists
prevent proving that ordering or the exact reason for loss. Fused-pool
truncation is also a possible cause. The already-retrieved neighbor remains a
valid route into the known booth scene; do not call it a whole-scene miss.

The category audit found an interaction defect independent of choosing a new
design: moving a selected reference to another aspect required dragging.
The existing editor now offers a staged native **Change aspect** selector with
explicit **Move to…**/**Replace…** and Cancel, preserving existing recipe and
clause-limit semantics. Composition Enter no longer commits unfinished IME
text. Uploaded Look now explains its mandatory visual restriction alongside
the existing Framing notice. These are functional corrections, not activation
of the proposed Refine redesign or separation of Words.

Live desktop and narrow-viewport checks verified indexed-reference moves,
replacement of an occupied destination at three active inputs, cancellation,
removal, retained movie scope and unchanged main text. The 390-pixel viewport
check showed the aspect editor contained within the screen; the override and
test clues were removed afterwards. Unit tests cover upload restrictions,
clause limits and IME behavior; a real mobile keyboard session remains untested.

Final focused validation: 218 backend search tests passed with one skipped,
including 36 blank-frame filter cases. The frontend interaction suite passed
89 tests, and TypeScript checking passed with `tsc --noEmit --incremental false`.
These checks validate the narrow corrections, not general relevance gains or
human acceptance of a redesigned category interface.
