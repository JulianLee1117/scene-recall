# Intent-guided retrieval comparison

Status: first bounded run completed on 2026-09-21; no strategy promoted. The
protocol and requests below were frozen before execution. This is an explicitly
invoked comparison; ordinary search does not depend on hosted interpretation.

## Question and three strategies

Does interpreting the query before retrieving extra evidence improve access to
useful footage compared with ordinary search and a simple fixed retrieval
strategy? Earlier Jev runs could only reorder an already retrieved pool. This
comparison can introduce additional source-backed candidates.

1. **Normal:** the existing whole-query hybrid search, with its existing scope,
   candidate generation, ranking and final result preferences.
2. **Fixed:** that same ordinary candidate pool plus a total of 120 allocated
   supplemental rows spread across the available caption, facets, mood,
   dialogue and OCR views.
3. **Jev:** the same ordinary candidate pool plus a total of 120 allocated
   supplemental rows spread across at most three available views selected from
   the query interpretation. No confident supported route means a disclosed
   ordinary-search fallback.

Both supplemental strategies retain the unchanged whole query. They merge by
stable unit identity, then apply the same final result preferences. Rank fusion
gives the ordinary ranking one half of the influence and the strongest targeted
view ranking the other half, using reciprocal rank with constant 60. Taking the
strongest targeted vote prevents overlapping routes from multiplying that
group's influence. This is a predeclared experimental policy, not a calibrated
claim about optimal relevance. Jev's classification probabilities select routes;
they are never interpreted as candidate relevance scores.

The fixed strategy is a necessary control: an improvement over normal search
alone may come from performing more retrieval rather than better interpretation.
The two supplemental strategies have the same allocated row budget when routes
are available. They do not necessarily have equal physical compute, returned
row count or final unique-candidate count. Report fallbacks and missing views.

## Interpretation and evidence boundary

`pipeline/search/intent.py` asks eight independent typed questions: visual
description, appearance, shot type, mood, dialogue, on-screen text, narrative
context and action timing. Multiple aspects may apply. A supported positive
signal with probability at least 0.55 can select an existing evidence view;
deterministic application code owns routes, allocations and fusion.

The provider receives only the original query, confirmed film IDs, definitions
and fixed instructions. Expected windows, captions, retrieved results and
human/agent labels stay out of the request. Title-like text cannot create a hard
film filter. Exclusions remain in the full query, with no inferred hard-negative
predicate. Textual shot/appearance requests use stored facets; they do not
activate the image-reference Framing gate.

Narrative context and verified action timing are recognized but unsupported by
this plan. Recognition cannot invent evidence, repair a false caption or prove
that an action has completed. Jev is text-only. Its official
[intent-routing](https://docs.typesafe.ai/patterns/intent-routing) and
[parallel-question](https://docs.typesafe.ai/patterns/fan-out) patterns motivate
this bounded use, not a claim of Scene Recall retrieval effectiveness.

## Query fixture and reference limits

`pipeline/eval/intent_retrieval_queries.yaml` freezes 16 cases:

- Eleven retained diagnostic queries cover the Before Sunrise listening booth,
  visible Matrix lettering, capsule placement, wide hallway framing, the known
  Moonlight caption error, a tight silhouette profile, and provisional dialogue.
- Five new author-written exploration queries cover a film crew, a medium
  two-shot with neo-noir appearance, lonely rainy neon footage, narrative
  betrayal, and an unconfirmed film title embedded in the query.

Historical queries deliberately retain known difficulties and are not held out.
Several share the same scene; report their correlation groups rather than
counting them as independent successes. Existing anchors retain their original
evidence status and source-fixture hash. They have not been freshly verified by
this protocol. A previous retained-frame check does not prove motion, dialogue
alignment, narrative meaning or full-shot relevance. Provisional timed dialogue
must not be counted as a verified positive. The five exploration cases have no
declared positive windows; an empty list does not assert that no good footage
exists.

Exact-unit access and broader known-scene-window access are distinct diagnostics.
The booth's nearby shot can be useful even when the exact previously inspected
unit is absent. The known Moonlight exclusion diagnoses retrieval of a checked
counterexample; it does not label the remainder of the library negative.

## Capture and measurements

`run_comparison` pins a search snapshot, captures ordinary candidates once and
memoizes query encodings. It fetches each needed single-view pool to the maximum
depth required by either supplemental plan, then slices/replays those shared
pools for the individual strategies. Each strategy applies final filtering and
diversity once. The comparison returns source identities, timestamps, evidence,
plans and candidate provenance for inspection and playback.

The CLI calls the existing local API sequentially; it does not initialize local
models or write indexes. External table versions bracket each case. A changed
version excludes the entire case and stops capture; already completed cases
retain their own recorded versions. A later resumed case may therefore use a
newer corpus than earlier cases, which must be disclosed when interpreting
cross-case aggregates. All three strategies within one accepted case use the
same query, explicit scope and pinned data.

Report:

- Exact-anchor and overlapping-window ranks, plus the declared broader booth
  scene window, without turning these into exhaustive recall scores.
- Top 5/12/48/200 overlap with ordinary search, candidate provenance and results
  absent from the entire ordinary **returned** pool. This is not proof that a
  candidate was absent from every internal ordinary shortlist.
- Distinct film counts and query-group identity. Variety alone is not quality.
- Actual returned model, raw response and request hashes, reported cost, failures
  and fallback reasons.
- Hosted transport duration separately from decision bookkeeping and the shared
  local comparison duration. Shared-work timing is not per-strategy production
  latency; no fresh/cold/warm latency distribution is established by one run.

Human relevance remains ungraded. Playable comparison of the actual lists can
establish which returned footage is useful to the owner; it cannot identify all
relevant scenes missed by every strategy. No questionnaire, personal goal ledger
or category-label study is required.

## Bounded execution and reproduction

Finish code changes before preparation: exact fixture, request and relevant
implementation hashes must match for resume. The runner is dry by default.

```powershell
uv run python -m pipeline.experiments.intent_retrieval --out pipeline/eval/runs/intent-retrieval-20260921
uv run python -m pipeline.experiments.intent_retrieval --out pipeline/eval/runs/intent-retrieval-20260921 --execute --decisions-only --allow-hosted --max-calls 16 --max-usd 0.05
uv run python -m pipeline.experiments.intent_retrieval --out pipeline/eval/runs/intent-retrieval-20260921 --execute --resume
```

The paid stage uses `typesafe/jev-1.13` through the existing OpenRouter Decisions
transport and records the actual returned version. At most 16 attempts and
$0.05 are admitted for this run. Each attempt reserves a conservative estimated
cost before transport; no automatic retries occur. Missing cost or cost beyond
the reserve stops further paid work. Application admission is not a provider
account billing cap. A run-local `STOP` file prevents the next case.

Execution saves `prepared.json`, raw `*-decision.json` responses and `run.json`.
The second stage sends validated saved interpretations to
`POST /search/intent/compare`; that endpoint makes no hosted calls. It saves
`*-comparison.json` and `summary.json`. Resume reuses exact completed decisions
and captures, checks receipt hashes, and cannot raise the cumulative budget.
Unknown-cost attempts require reconciliation before any automatic resume.

The separately invoked interactive hosted comparison has its own bounded receipt
ledger; it is not a continuation of this offline run's budget. Ordinary searches
do not spend either budget.

## Decision gate and findings

No strategy is promoted by this protocol alone. Investigate whether new footage
is useful, whether known moments become easier to reach, whether fixed extra
retrieval performs similarly, and whether the gain justifies hosted latency and
dependency. A missing-context failure should guide evidence improvements rather
than prompt or weight tuning against this small targeted fixture.

### First-run findings — 2026-09-21

All 16 cases across 12 query groups completed. All three strategies used the same
snapshot within each case; the externally observed table versions also remained
unchanged across the complete run. All five text views were available and no
retrieval adapter failed. The prepared interpreter/retrieval implementation hashes
match those reported by the loaded API. Receipts are in
`pipeline/eval/runs/intent-retrieval-20260921/`.

Sixteen provider requests returned `typesafe/jev-1.13-20260917`, with no retries,
unpriced attempts or invalid responses. Total reported cost was **$0.001034418**.
Median observed HTTP duration was **120 ms**, with a maximum of **3.335 s**.
These are one small sequential run's transport measurements. The shared local
three-strategy comparison took a median **10.73 s**, ranging **1.47–29.69 s**;
it includes baseline retrieval, shared supplemental pools and three final lists.
It does not establish production latency for a single Jev-guided search.
Interactive UI verification is a separate invocation and is excluded from these
16-case cost and timing totals.

The clearest known-moment improvements were also achieved by the fixed control:

- **Scoped remembered listening booth:** the first result in the previously
  declared booth scene window improved from ordinary rank **11 to 7** under both
  fixed and Jev retrieval. The exact inspected anchor remained absent. With no
  film scope, the original remembered query still had no result in that scene
  window in any of the three returned lists.
- **Tight right-facing silhouette profile:** the known Dune anchor improved from
  **18 to 4** under both fixed and Jev retrieval.
- **Simple visual booth wording, visible Matrix lettering, capsule placement
  and the actual child-in-bath query:** their exact anchors stayed first in all
  strategies. The Shining exact anchor remained absent, but all three lists
  placed another nearby shot, unit `0336`, first. Its stored caption describes
  the requested girls and hallway; this is not a newly verified source match
  or evidence that the remembered scene was missed.
- **Known false caption:** the Moonlight child falsely described as an elderly
  man remained first for the elderly-man query in every strategy. Routing did
  not repair the underlying annotation.
- **Provisional dialogue:** the first declared Fallen Angels reference stayed
  first throughout. A second provisional reference moved from **13 to 15** under
  fixed retrieval and remained **13** under Jev's ordinary-search fallback.
  These timed-text references are still unverified positives.

There was also a useful post-hoc warning, separate from the predeclared reference
diagnostics. For the new query mentioning “in Before Sunrise” without confirmed
film scope, ordinary search returned booth unit `0205` first; both supplemental
strategies moved it to sixth. This case had no predeclared positive window, so it
is an exploratory observation rather than a benchmark grade. Its scope correctly
remained empty in every strategy. More retrieval and fusion can move an already
useful result down as well as move another one up.

Jev's interpretation exposed unresolved routing choices:

- The explicit computer-screen lettering query received an on-screen-text
  `yes` answer with probability **0.36**, below the frozen **0.55** threshold.
  Jev consequently added caption retrieval but no OCR route. The ordinary
  backbone still found the target first.
- The bare quotation “Are we still partners?” was classified as narrative
  context rather than dialogue/OCR, producing an unsupported-intent fallback.
  A quotation alone is source-ambiguous, but this decision did not supply a
  useful additional words search.
- The betrayal query also requested narrative context and fell back to ordinary
  search, as required by the available-evidence boundary. These were the two
  Jev fallback cases; no provider failure caused a fallback.
- The lonely-neon query selected caption and appearance/facets, with mood
  classified `no` at **0.59**. No human intention labels were frozen for this
  new query, so this is a disclosed interpretation choice, not an accuracy score.

Both strategies introduced candidates beyond the ordinary returned pool. Across
the 16 top-12 lists, fixed retrieval contributed **8 result positions across
4 queries**, while Jev contributed **6 positions across 4 queries**. Across the
top-48 lists those counts were **234 positions across all 16 queries** for fixed
and **43 positions across 12 queries** for Jev. These are result-position counts
across correlated queries, not unique relevant scenes, and more novelty is not
necessarily better. The five new exploration queries have no source-verified
relevance judgments; captions and changed membership cannot determine a winner.

**Conclusion:** this run supports testing supplemental evidence retrieval, but
does not demonstrate that Jev improves retrieval quality over the simpler fixed
control. Both achieved the same two clear known-moment rank improvements; neither
fixed missing scene context or false annotations. Keep the comparison optional
and ordinary search unchanged. The next decision should use the actual playable
results to establish usefulness, with particular attention to words routing,
regressions from fusion, and whether any Jev-specific candidate is better than
the fixed control. Do not tune thresholds or weights against these results and
then describe the same fixture as independent validation.

### Implementation and live interaction checks

Focused interpreter, retrieval, recipe, request and API checks passed. One
combined Windows run hit a path-length failure in an existing keyframe fixture;
that test passed with a shorter temporary root. All 589 frontend tests,
TypeScript and the production build passed. Real ordinary `/search` ordering
matched the comparison's normal arm exactly for all 169 returned film-crew
results.

Desktop and 390px browser checks covered Refine's aligned category row,
expanded/collapsed chevron, contained editor and Escape dismissal. The explicit
comparison button, all three cached-list switches, and source playback were
verified in the live UI. A La La Land guided result reached readyState 4 and
advanced playback without a media error. This verifies interaction, not a
comparative relevance grade.

The separate UI smoke check made one provider attempt costing **$0.000063756**.
Its initial 3.45-second response exceeded the two-second interpretation deadline,
so the UI correctly displayed ordinary fallback. The late successful receipt
was persisted; a subsequent explicit comparison used it without another paid
call and displayed guided results. The interactive transport now reuses one
bounded HTTP client across uncached queries. Result switching makes no hosted
or retrieval requests. Ingestion workers continued through API-only reloads.

### Independent retrieval latency diagnostic — 2026-09-21

After the optional full-library framing preparation was held, the new
`pipeline.experiments.intent_latency` runner measured each strategy separately
through the already running API. Four existing query decisions were read from
hash-verified comparison receipts. There were **zero new hosted requests**, no
model instances in the runner, and no index writes. The raw receipt is
`pipeline/eval/runs/intent-latency-20260921.json`.

Each query used one pinned library snapshot for two rounds, with strategy order
rotated between rounds and queries. Every strategy execution had a fresh query
memo and its own ordinary retrieval. Models and OS/database caches remained
warm. All four cases happened to retain the same table versions. The API's
loaded implementation hashes matched the runner's expected files. The complete
24-search diagnostic took **93.09 seconds**.

Median local strategy execution times from the two repetitions, at limit 48:

- Scoped remembered listening booth: ordinary **1.120s**, fixed **1.519s**,
  Jev **1.175s**; Jev selected caption and mood.
- Unscoped medium two-shot in a dim neo-noir interior: ordinary **3.804s**,
  fixed **10.146s**, Jev **5.943s**; Jev selected caption and facets.
- Unscoped lonely-neon query: ordinary **3.783s**, fixed **8.580s**,
  Jev **6.529s**; Jev selected caption and facets.
- Scoped Matrix on-screen words: ordinary **1.029s**, fixed **1.540s**,
  Jev **1.273s**; Jev selected caption only, retaining the earlier disclosed
  OCR interpretation miss.

Every strategy's returned source ordering matched between its two repetitions,
and no adapter failed. These checks establish stable measurement inputs, not
usefulness of the rankings. The benchmark excludes hosted interpretation,
response serialization and HTTP transfer from the strategy timings. It uses
48 results rather than the interactive comparison's 200, has no cold-cache or
concurrent-load coverage, and is too small for production percentiles.

This confirms that the full three-list comparison exaggerates the work needed
by one guided search. It also confirms a remaining local retrieval cost: the two
unscoped Jev cases added about **2.14–2.75 seconds** to ordinary retrieval before
any hosted latency. Optional framing preparation is not required for any of
these strategies. Keep Jev optional while evaluating retrieval efficiency and
result usefulness independently; this timing diagnostic does not establish a
quality advantage or justify changing the default search.
