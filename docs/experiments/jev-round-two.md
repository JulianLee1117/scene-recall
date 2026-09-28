# Jev second round: constraint judgment and category interpretation

Status: completed bounded experiment, 2026-09-20 local / 2026-09-21 UTC.
The protocol below was frozen before hosted execution. Ordinary search stays unchanged.

The user requested another Jev experiment. This round separates two questions:
whether explicit source/constraint instructions improve textual evidence judgments,
and whether independent query labels could inform optional category suggestions.
The first pilot's frozen sources and receipts remain immutable.

## Evidence experiment

Twelve predeclared cases in `pipeline.experiments.jev_evidence_probe.CASE_IDS`
cover almost-kissing, screen lettering versus speech, left/right capsule placement,
wide hallway framing, silhouettes/profiles, narrative purpose, mixed dialogue/image
requests, and the known false Moonlight caption. They are targeted follow-ups to
known failures, not held-out queries. Use the same first 48 candidates and exact
query, explicit scope, caption and winning-text excerpts as the original run.
The missing Matrix anchor remains missing; the independent blank-filter fix is
not applied to the frozen pool or counted as a Jev improvement.

Compare five arms: captured baseline; original prompt/original scoring; original
prompt/contradiction penalty; revised prompt/original scoring; revised
prompt/contradiction penalty. Existing responses cost no new calls. The revised
prompt clarifies that explicit conflicts take precedence over partial support,
unknown details are not contradictions, source type matters, and static descriptions
cannot prove action timing or missing narrative context. It receives no answer key,
new evidence, source images, or per-case hints.

Freeze both scoring policies before execution. Original scoring is the existing
baseline anchor `1 / (60 + position)` plus half that anchor multiplied by
supported=1, partial=1/3, contradicted=0, unknown=0. The penalty variant additionally
subtracts half the anchor for explicit contradiction only. No intent bonus in
this ablation. Preserve all candidate identities and the unchanged tail beyond
48. Do not tune weights after seeing outputs. This penalty is a hypothesis,
not a production recommendation.

An independent agent labels baseline top-three rows and exact anchors within
the eligible head, before new calls. Allowed-label sets disclose ambiguous
interpretations. This is agent annotation of textual support, not human relevance,
image verification, or a representative sample. Prior source observations and
case selection are known. Report decision agreement, unsupported full-support
labels, observed contradictions, label transitions, exact-anchor ranks and
predeclared scene-window ranks separately. Do not grade unknown rows as irrelevant.

## Category experiment

Freeze 24 new utterances and agent-authored expected labels before calls. Each
query has seven independent three-way judgments: content, appearance, framing,
mood, dialogue, on-screen text, and narrative context. Multiple positive aspects
are allowed. Negative-only mentions do not activate that aspect. Ambiguous
literal wording can be unclear rather than forced into dialogue or OCR. Movie
words never establish a hard scope. Expected answers and rationale stay outside
the provider payload.

These are semantic labels for an experiment, not an approved UI taxonomy or
production routes. Narrative/context recognition does not make contextual
evidence available. Report exact-query agreement, per-label errors, source-type
confusion and mixed-query behavior. A small author-created fixture does not prove
real user interpretation accuracy, useful suggestions, or retrieval quality.

## Execution and limits

At most 36 new calls: twelve evidence requests and 24 category requests. Admit
at most $0.25 in conservative cost reserves for the entire round ($0.08 evidence,
$0.17 categories); actual reported usage is recorded. This is an application
admission bound, not a provider account spending cap. No automatic retries.
Persist attempt accounting before transport. Unknown-cost attempts halt paid
work; stop the other experiment too if such an attempt occurs. A STOP file
prevents the next request. Execute the experiments sequentially.

Use `typesafe/jev-1.13` through the existing OpenRouter Decisions transport and
record returned version, source/request/implementation hashes, raw responses,
usage, transport duration, failures and fallbacks. Fresh transport timing is
separate from Python bookkeeping, and neither is production end-to-end latency.
No models are loaded locally, no API/service is restarted, and no index, source
film, ingestion or production ranking is changed.

Output root: `pipeline/eval/runs/search-intent-20260921-round2/`. Preserve plans,
prelabels and receipts as separate artifacts. Human grades remain pending.

The official [model limits](https://docs.typesafe.ai/models) and
[known failure modes](https://docs.typesafe.ai/model-jaggedness/jev-1.13) were checked
before execution. They support testing focused typed judgments and reduced
ambiguity; they do not establish task accuracy. This round retains the original
48-row state to isolate prompt changes, so it does not test smaller batch sizes.

## Results

All 36 requests completed without retries, failures or unknown charges. Returned
model was `typesafe/jev-1.13-20260917`, matching the first round's recorded model.
The twelve evidence calls reported $0.009173388; the 24 category calls reported
$0.001395660. Total: **$0.010569048**. The $0.25 admission limit was not approached.

### Category recognition

Of 22 strict queries, **18 matched every predeclared category**. Individual-label
agreement was 150/154, comprising all 32 expected positive labels and 118/122
negative labels. The high label-level fraction is partly due to many negatives;
do not present it as general search accuracy. The strict set spans eight
correlation groups. Both mixed shot/style/mood queries, including the typo case,
and all four explicit source-negation cases matched the fixture.

The four extra labels concern overlapping concepts: two story/realization
requests gained Content; “standing close” gained Framing; romantic tension gained
Narrative context. “Standing close” can reasonably be subject arrangement under
our own definition. These are disagreements with the frozen taxonomy, not four
unambiguous reasoning failures. The expectations were not adjusted afterwards.

Two ambiguity cases remain separate. The broad allowed answers for “blue” matched.
For a bare quotation, dialogue was unclear but on-screen text was excluded, outside
the fixture's allowed yes/unclear answers. A quoted phrase still needs a clean way
for the user to specify its intended source.

This supports a **prototype of optional, editable category suggestions**. It does
not justify automatically restricting routes or adding hard filters. Keep original
words, exclusions and confirmed movie scope authoritative. The labels here do
not resolve the UI taxonomy or make plot evidence searchable.

### Evidence judgments and ranking

The independent agent's 37 frozen text labels span twelve queries and eight
reference groups. Primary agreement changes **25/37 to 26/37**; agreement allowing
the eleven declared ambiguity sets stays **28/37**. There are seven primary
conflict labels, but four allow partial as an alternative. On the three
unambiguous conflicts, detection improves **1/3 to 3/3**. Unsupported full-support
judgments fall from three to one. However, contradiction judgments outside the
allowed labels increase **three to seven**. Seven strict agreements are gained
and six lost; ambiguity-aware gains and losses are four each.

The distinction between missing and conflicting evidence remains the main issue.
An extreme close-up describing one capsule does not explicitly rule out another
capsule elsewhere; a “narrow hallway” does not establish a narrow shot. Stronger
conflict instructions can over-penalize these unknowns.

Concrete ranking diagnostics:

- The remembered scoped booth query's neighboring unit `0207`, within the
  predeclared booth window, moves **11 baseline → 9 old evidence → 5 revised
  evidence**. The penalty leaves it fifth. Exact anchor `0206` is still absent.
  The first four captions describe other romantic settings; this is improved
  access to the booth, not full resolution of the remembered query.
- The no-kiss description's booth neighbor moves **30 → 25 → 24**, and to 19
  with revised judgments plus the penalty. This is a scene-window diagnostic,
  not verification of no kissing throughout the source shot.
- The Shining's explicit distant-pair/not-close-up anchor improves **2 → 1**.
  However, the broader eerie-symmetry query's strong matching-girls hallway
  caption is newly contradicted and drops **1 → 12** with the penalty. A better
  anchor rank in one wording does not establish general framing improvement.
- The Dune tight-profile anchor stays sixth under both evidence prompts,
  compared with eighteenth baseline. The revised prompt adds no gain there.
- Moonlight's incorrect elderly-man caption stays first in every arm. The
  penalty promotes several previously low-ranked unknowns to positions 2–5.
  The model neither verifies nor repairs the underlying source annotation.

All 60 case/arm orders preserve candidate identity and the tail. Missing candidates
remain missing. Human played preference, factual source review and broader
relevance remain pending. The old decisions were reused rather than repeated,
so prompt comparisons do not estimate provider sampling variance.

**Decision:** no reranker or category routing is promoted. The best next product
experiment is user-correctable category interpretation; the next ranking experiment
should distinguish explicit contradiction from absent evidence with smaller,
focused questions on new cases, rather than strengthening this penalty again.
Resolve candidate loss and caption/source defects independently.

### Timing and verification

Median HTTP duration was **0.125 seconds** for category requests and **0.233 seconds**
for evidence requests. Maxima were **2.201** and **3.525 seconds**, both on the first
request of their run. These are fresh sequential HTTP observations, not model-only
compute, mobile latency, production p95 or end-to-end search measurements.

The isolated harness/builders passed 60 focused tests initially. A retained-data
comparison then caught a floating-point operation-order discrepancy in the local
scoring replay: algebraically equivalent multiplication reordered near ties.
The replay now uses the original scorer's exact addition/multiplication order;
all twelve original-control orders match their retained results exactly. Four
focused evidence tests, including the new near-tie regression, passed afterwards
(61 distinct focused tests total). This is an implementation correction to the
predeclared policy, not tuned weights or new provider calls. The prepared provider
plan is unchanged. The results view also passed TypeScript checking.

Retained artifacts under the output root include `evidence-plan.json`,
`categories-plan.json`, `agent-evidence-labels.json`, each run's `frozen-plan.json`,
raw responses and `run.json`, plus `analysis.json`, `category-analysis.json`,
`independent-review.md`, and the reproducible offline `analyze.py`. No film,
ingestion, serving configuration or source index was modified for this round.
