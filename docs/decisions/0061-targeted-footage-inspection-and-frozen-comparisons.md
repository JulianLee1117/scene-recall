# ADR-0061: Targeted footage inspection and frozen editor comparisons

- Status: Accepted
- Date: 2026-09-14
- Extends: ADR-0036, ADR-0041, ADR-0058 and ADR-0060
- Supersedes: the blanket deferral of automatic footage inspection in ADR-0060, only for the bounded optional slice below

## Observed failure

Caption-led selection has placed footage whose actual action or relationship
contradicts the requested image. An indexed description also cannot establish
whether a gesture completes within the selected trim. Musical timing and legal
source handles alone do not resolve those failures. At the same time, measured
beat guides may bias timing toward pulse-following cuts; this requires a
controlled comparison rather than an assumption that more beat detail helps.

## Decision

Keep one Generate edit action and the existing private proposal, locks,
cancellation, revision guards and atomic publication. Add an optional internal
post-selection review, disabled by default with `lab.footage_inspection: false`.
Do not add a mandatory user panel, a separate editor version, or inspection to
ordinary main search. Fixed/user-owned work retains its existing timing and
ownership rules. The initial slice is deliberately bounded to two flagged
positions across the complete job, not two per processing batch.

The selector records `none`, `action_timing` or `visual_fit` inspection hints in
its candidate ledger. Prioritize action-dependent claims, then uncertain visual
fit, in timeline order. A hint is a model nomination, not proof of a problem.
Only eligible selected unlocked footage may enter inspection. Preserve full
eligible flag IDs separately from the at-most-two targeted positions so evaluation
can distinguish missed flags from the inspection budget.

For each target, inspect its selected source and at most one already-offered,
distinct legal alternative. Do not search again, invent candidates or expand
an indexed unit's source bounds. Each neutral observation uses a source window
no longer than eight seconds, with approximately two samples per second and
the endpoints, capped at 17 actual decoded presentation-timestamp frames.
The display-normalized JPEGs have a 640-pixel maximum edge and preserve any
requested crop in the sampling identity. At most four observation cache misses
may each issue one image request; one text request then reviews both targets
jointly. There is no retry, critic loop or provider substitution.

Store neutral reusable observations under `source-window-observations-v1`,
independently of song or editing intent. Cache identity includes authoritative
source identity/fingerprint, indexed unit bounds, requested window and crop,
sampling profile, model/provider and prompt dependencies. Record actual sample
IDs, source times, frame ends and hashes. Structured events bracket an observable
before/after change with sample IDs and state whether completion is visible,
uncertain or not visible. These are model observations of sparse stills, never
continuous action verification or precise onset/completion timestamps. Recheck
source authority before reuse; preserve raw films and prior derivations.

The song-aware review receives those observations, the target's musical purpose,
scoped song evidence, existing source choices and caption-only neighbors. It
can retain the baseline, choose a server-offered trim, or leave an explained gap
when the baseline is contradicted and no supported option fits. Uncertainty
preserves the baseline. When inspection rejects a targeted replacement, retain
the previously placed clip under the existing failed-replacement contract and
label it uninspected; rejection does not establish that the earlier clip was
visually supported. This uses the existing progress/Details flow, with no extra
required UI. Observed-action options must include their sampled
before/after bracket and the final evidence frame; ongoing motion or an intentional
interruption need not finish every depicted action. A long clip can extend beyond
its inspected window and must not be described as wholly verified.

Reuse the deterministic cut solver to fit selected legal trims with the least
movement from the provisional edit. Only already-offered nearby cuts in the
original timing scope may move, with fixed group endpoints, protected neighbors,
original source starts of unselected neighbors, source duration bounds and exact
passage coverage. Existing/manual timing remains fixed. No new cuts, merges,
reordering, arbitrary patches or unseen source expansion are allowed. Recheck
source reuse, search evidence and changed alternatives. If individually valid
review choices cannot fit together, retain supported baselines and explain gaps
for contradicted ones rather than inventing timing or substituting another source.

Source/ownership violations remain hard errors. Optional observation or review
unavailability retains the provisional edit with an explicit incomplete status;
it does not become a successful inspection. Cancellation and stale revisions
retain their normal job semantics. Record eligible/inspected/reviewed scope,
sample artifacts, cache hits, changed choices, uncertainty and actual stage
progress in the existing Details flow. Full private inspection input receipts
retain the provisional document and selector contexts for a reproducible replay.

## Evaluation

Add a standalone frozen-document harness with a dry default and explicit execution
and hosted-call ceilings. It never listens again, queues jobs, changes source
evidence or writes saved projects. Execution may create versioned derivations,
model receipts and comparison artifacts. Inspection-off/on uses exactly the same
provisional choices and candidate contexts; report source/timing changes, gaps,
cache use, requests and latency separately from human preference. User-supplied
known failures are examples, not an exhaustive negative-labelled set. Report
automatic flag coverage and deliberately injected fixture hints separately.

Timing beat ablation uses internal `beat_guides` keywords, defaulting to the
unchanged production behavior. The disabled variant withholds measured beats,
downbeats and derived markers from the timing payload and additional measured
pulse options from source-fitting offers. Keep supplied lyrics, user markers,
RMS and the same listening observations. Whitelist provenance metadata and use
opaque dependency digests rather than leaking old pulse arrays through nested
request receipts. Do not rewrite saved provenance or introduce a project setting.
This measures dependence on explicit measured guides; it does not erase rhythm
already described in the frozen audio interpretation. Cache identities must
distinguish treatment variants, and a cache-only baseline must not consume a
budget intended for the ablation.

The first real timing comparison is limited to one short Nocturne and one short
Rumination passage, at most two uncached timing requests total, reusing their
existing listening and cached baseline plans. Further hosted inspection replay
requires its own explicit bounded budget. Mechanical tests and model explanations
do not establish improved creative pacing or scene fit; played acceptance remains
a user judgment.

## Deferred boundaries

No query repair, unsupported-intention rewriting, action-aware timeline merge,
automatic Match Cuts integration, object tracking, movement correspondence,
dialogue mixing, learned ranking, RL, new vector representation or blanket
ingestion work is included. Expand coverage only after concrete replay and
played examples show what this small inspection slice misses and whether its
latency is useful.
