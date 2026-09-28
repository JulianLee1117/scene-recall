# Next-scene experiment: implementation and optional creative evaluation

Date: 2026-09-12. Status: **implemented under ADR-0036; browser walkthrough pending**.
This narrows the next AI Music Video experiment. README and the current
architecture contract remain authoritative. The accepted implementation boundary
is recorded in [ADR-0036](../decisions/0036-next-scene-proposals-and-pair-audition.md).
Existing discovery, Match Cuts and release gates remain open.

User correction: prioritize a useful editor without a training-data burden.
The formal pilot below is optional structured evaluation. It does not block
implementation or normal use; brief playback reactions guide polishing. No RL,
fine-tuning, feedback dashboard or mandatory reference-labeling exercise is added.

The delivered slice uses the placed clip's Find following scene action, explicit pair scope, up to three
music-backed alternatives, source/cut adjustment, a timeline preview guide,
revision-checked Use scene and one Undo. The selector can abstain. Optional
sampled-frame inspection is available with OpenAI, off by default. The README
describes current controls; the architecture contract defines exact bounds.
The broader pilot and progression ideas below remain optional evaluation work.

Mechanical verification: 952 backend tests, 32 frontend checks, TypeScript and
production build pass. A real 10cc pair completed search, rendering, Apply and
restoration; adjusted source-in preview/Apply also passed. In a controlled
comparison over the same six offers, text-only and sampled-frame selection
chose the same shot and trim. This does not show a ranking benefit; inspection
stays optional. Background test-server startup was blocked by automatic approval
review during the original verification. API and worker were subsequently
restarted; the complete pair-endpoint browser walkthrough remains separate
from the completed source-control checks. Detailed evidence is in the
[September milestone history](../history/lab-milestones-2026-09.md).

## Product question

Can Scene Recall suggest an unexpected next scene that works with an existing
image at a particular musical moment, and get the user to a usable cut faster
than ordinary search and trimming?

Test one transition before expanding automatic sequence planning. The first
quality comparison supplies an anchor scene to isolate this question. Automatic
opening selection and longer sequences are later tests; the intended product
still supports a one-button draft with optional human direction.

Keep this inside **AI Music Video**, using its current monitor, timeline and
right-hand Library. Main search remains independently useful. This is not a new
Lab app, replacement main branch, style framework or ingestion rewrite.

## Why this is the next test

- `generation._guard_edit` and direction planning preserve exact existing cuts.
  `music_planner.fill_timeline` then rejects candidates shorter than each slot.
  An excellent six-second shot can disappear behind an eight-second placeholder
  before creative selection ever considers it.
- The selector reads captions, annotations and retrieved-frame evidence. It
  does not watch footage, so a convincing explanation can describe an action
  that is absent from the chosen source window.
- Offered indexed references currently favor the retained clip's midpoint.
  That reference is not necessarily the actual outgoing image at the cut.
- Beat/bar/amplitude scaffolding and the completed 10cc run establish working
  mechanics. Human musical/editing acceptance and the reference viewing study
  remain unfinished. Variable shot lengths alone do not establish good pacing.

## User experience

1. Select a scene, or the empty position immediately after it. **Find next
   scene** identifies the anchor and highlights the affected pair on the
   timeline. Never infer an anchor across an intervening gap.
2. Optionally write a direction such as “carry the isolation into a wider
   image” or “echo the turning gesture.” There is one visible intent field.
   Search recipes and evidence remain available under details; the normal
   Library search remains available for direct browsing.
3. Return **up to three** distinct, legal choices. Each shows its source,
   proposed duration and a short intended relationship to the anchor/music.
   Preview plays the actual A→B cut with the same music in the existing monitor.
   The main edit remains unchanged while auditioning.
4. **Use scene** applies the displayed scene and timing together. A concise
   timing note and a temporary cut outline make any adjustment visible before
   applying. **Undo** restores the entire previous edit in one action.
5. Edit the direction and find again to change the suggestions. Direct trim
   and cut dragging remain available. Users do not need to coordinate separate
   Rewrite prompt, Find scenes and selector stages.

Keep Space playback, frame nudging, Save/Exit/Delete and the shared amber theme.
Use compact result rows and one monitor; switching alternatives restarts the
same audition range for a fair comparison. Do not make isolated source playback
the only way to review a choice. Show fewer results rather than padding with
duplicates. “Why this” distinguishes an intended connection from observed
evidence; omit creative-quality percentages.

For the first implementation, an absent anchor has a clear explanation and a
link back to existing scene selection/Generate edit. It does not silently choose
an unrelated reference. A no-anchor opening-pair flow is a separate follow-up.

## One shared engine, one small proposal layer

Reuse the existing source identities, recipes, capability description, musical
evidence, project documents, durable worker and renderer. Add a small
`pipeline/lab/next_scene.py` coordinator with typed request/result models. Extract
candidate/evidence helpers from the current planner only where both callers
need them; do not copy retrieval ranking or introduce a generic agent framework.

The intended execution is:

```text
Frozen project + permitted pair + optional direction
  → reuse music interpretation, or listen once if needed
  → propose bounded search intentions using actual available capabilities
  → existing scene retrieval and query-specific evidence
  → legal source windows and cut alternatives
  → choose up to three concrete pair proposals
  → prepare playable previews
  → explicit Apply → one normal project revision
```

These are responsibilities, not separate services or mandatory agent personas.
Initially use the configured audio interpreter and text planner: at most one
audio call on a cache miss, one search-intent call and one selection call.
An explicit executable search can skip intent generation. Ordinary Library
search remains free of editorial LLM calls. Optional sampled-image selection
uses the same proposal contract; a later video selector can replace that stage
without changing the timeline contract. Reuse
interpretation/evidence components rather than invoking the current analyze job
wholesale: that job can also prepare or refresh timeline state. New listening
output stays in the proposal/cache and does not commit unrelated project changes.

Implemented work bounds are three recipes with at most three supported
clauses each, up to 48 results per recipe, at most 24 distinct offers to selection
and up to three previews. Preserve a small per-recipe reserve before pooling so
one broad search cannot erase the alternative ideas; retain per-query evidence
for shared source IDs. Do not sum incompatible scores. Return a useful failure
when no legal proposal remains; do not silently broaden a mandatory visual gate.

The planner receives the anchor's actual source range, nearby placed footage,
the selected musical interval with its existing passage context, current
direction, and available search signals. It should seek different useful
relationships, not force fixed “style” categories or invent diversity through
random trim offsets. On an explicit retry, include the user's correction and
last offers as bounded context. No autonomous retry/critic loop is introduced.

Reuse music/evidence caches when their identities match. Record stage duration,
hosted request count, candidate loss reasons and preview readiness in the job.
Progress names actual work; do not display “Watching footage” for a text-only
run. Cancellation, provider failures and interrupted work use existing behavior,
with no hidden hosted retry or provider substitution.

## Exact edits; flexible proposals

The initial request targets exactly two adjacent timeline positions: anchor A
and next position B. Freeze their IDs, the project's base revision and outside
times T0 and T2. An alternative has one exact shared cut C, outgoing source
window, incoming source window and the original song segment.

Saved timelines remain exact. Duration ranges are request constraints, not a new
timeline representation. The server constructs ordinary validated documents
from narrowly typed source choices; a model cannot return arbitrary project
patches. The existing Generate/fill guard remains unchanged.

Begin the controlled comparison with the existing cut fixed. For the subsequent
timing experiment, explicitly enable a displayed **Flexible cut** scope. It may
roll only this shared boundary, inside the highlighted pair. Existing cuts are
not presumed disposable because they resemble an old AI timing suggestion.
The first flexible scope ends at B's existing end and requires B to be empty;
existing filled replacements retain fixed timing. This avoids implicit ripple
editing or a new per-cut ownership system.

The anchor's film and source-in remain fixed; flexible timing changes only its
outgoing tail. A locked anchor fixes that tail and the shared cut. A locked B
cannot be replaced. Everything outside the pair, the music passage, user
directions and other locks remain unchanged. Beat snapping stays optional.

For each candidate, compute the feasible cut interval from source availability,
anchor handles, explicit timing constraints and output-frame granularity. Keep
the candidate if **any permitted cut** lets both clips fit; do not apply the old
exact B-duration filter first. Each chosen result must still cover T0..T2
without gaps/overlap, at original speed, inside both server-resolved offered
ranges. Do not extend across a shot/subdivision merely because a parent ID or
film duration suggests footage exists beyond its offered interval. The current
`validate_sources` checks film bounds, so the coordinator must separately enforce
offered unit/window bounds. If safe anchor handles cannot be resolved, keep the
cut fixed. Aligning an observed event also constrains source-in; total candidate
duration alone does not establish that the required moment fits.

This narrow test does not add/remove cuts or produce a one-shot hold. It can
hold A across musical beats by moving C later when permitted. Evaluate variable
cut counts separately when expanding to short sequences.

## Proposal storage, preview and application

Use one bounded `next-scene` job kind in the current queue. Store immutable
suggestions in its result, not in the committed project's alternatives field.
Each suggestion needs a schema version, proposal ID, frozen request/revision,
source authority, exact pair/cut, resolved search/evidence, intended relationship,
unverified requirements and preview status. Existing models represent the actual
clips and timeline; no new database table is justified for this pilot.

Reuse Match Cuts' lifecycle pattern, but not its apply endpoint: that endpoint
updates a sequential clip list and does not implement music-slot application.
Likewise, reuse `suggest_only` retrieval helpers, not its fixed-duration and
automatic project-write semantics. Request data and internal source paths stay
server-owned.

Finding, rendering and auditioning create no project revision. At Apply, reload
the project, check the original revision, locks, pair scope and source authority,
construct the narrowly allowed mutation, and validate it again before saving.
Stale suggestions remain viewable but cannot overwrite newer work or be silently
rebased. Applying an already consumed/stale suggestion cannot create another
edit. Local unsaved edits also invalidate Apply; use the existing explicit
save/frozen-snapshot lifecycle rather than hiding those edits from the server.
When a trim or cut changes, recompute duration-dependent search details and
invalidate obsolete source references, frame evidence and saved alternatives.
Retain written intent and mark affected generated directions for review using
the existing ownership rules; never carry an old reference as new evidence.
Clip-local reference instants/windows must remain inside the new range or be
cleared. Outside recipes that reference a changed anchor stay untouched but must
fail clearly as stale on later execution, rather than silently rebinding.

Render auditions with the existing media pipeline and cache them by exact
source windows, music identity/offset, cut, crop/output settings and render
profile. Preserve song position and passage-relative fade when rendering an
excerpt; an audition must not restart the fade at every pair. Preserve source
presentation timestamps and quantize boundaries using the full passage's
cumulative 24-fps origin, rather than independently rounding the excerpt. The
current renderer has no excerpt-origin contract and starts its fade at zero:
stage 2 needs a narrow, versioned excerpt adapter. The initial manual comparison
can extract pairs from complete renders to retain identical timing and audio.
The auditioned boundary must be the boundary applied and exported. Pending
previews are labeled; never play an older candidate under a newly selected result.

## Footage inspection: earn the added cost

Start with actual playback and manually marked useful source instants for the
comparison. Existing indexed images remain labeled as indexed evidence, not
the cut image or an observed action peak. This establishes whether better
windows would rescue the choices before adding another model stage.

If relevant sources repeatedly fail because the chosen moment is wrong, test
one bounded inspection method on the same shortlist: decode up to six offered
windows and inspect timestamped frames or short clips. Cache observations by
source identity/range, decode/sampling profile, model revision, prompt and schema.
Keep observations separate from musical interpretation and creative intent.
Record supporting source times and unavailable/uncertain evidence. A frame
sequence does not establish precise motion; a model's confidence is not human
verification. Do not promote model-observed action timing to verified evidence.

Compare this challenger with text-only selection and a manual-window oracle.
Use the same sources/order when testing timing. Add it only if played results
improve enough to justify observed latency and cost. A missing candidate instead
triggers retrieval diagnosis; it is not evidence that another selector is needed.

No library-wide dense/video reingestion, new vector space, automatic crop,
dialogue mixing, speed ramps or music-model migration is included. Exact visual
and movement matching retain ADR-0008/0027's separate evidence and activation
gates. Music instructions must not silently activate the Match Cuts engine.

## Delivery order and checks

1. **Establish the experiment.** Use three development examples to make current
   search/manual trimming, exact pair playback and failure recording repeatable.
   Include the retained 10cc song, without restoring deleted test projects.
   Reconstruct source windows from retained media, not unavailable QA projects.
   Annotate short played reference excerpts before treating their editing style
   as a target. No new automatic model is necessary for this step.
2. **Build audition and apply.** Add the bounded job/result/apply boundary and
   three-choice Library interaction, initially at fixed timing. Record the
   accepted ADR and architecture changes with this implementation. Verify source
   bounds, lock/revision/local-edit conflicts, cancellation, failed previews,
   application/Undo and unchanged surrounding edits. Already at fixed timing,
   check excerpt music offset/fade and boundary parity against the full render.
   Browser checks cover real
   pair audio playback, source switching, Space, seeking, compact desktop/narrow
   layouts and Save/Exit. Use deterministic proposal fixtures to test mechanics.
3. **Test scene-aware timing.** Add explicitly scoped shared-cut proposals and
   feasibility-based candidate admission. Keep a timing-only comparison with
   identical A/B footage/order; separately test newly admitted shorter sources.
   Validate unequal source frame rates, output duration, musical offset/fade and
   decoded boundary fidelity. Polish preview/cut adjustment before adding knobs.
4. **Run the frozen pilot.** Diagnose the outcome before choosing better
   retrieval, bounded footage inspection or a larger sequence experiment.

Use focused tests first. Full backend/frontend checks follow material code
changes; this planning document itself does not claim new test results.

## Creative acceptance pilot

After the development examples, freeze **12 transition tasks across at least
three real music passages**, including emotional change, strong rhythmic
contrast and ambiguous texture. Keep the anchor, music interval and film scope
identical across comparisons. Record tasks before tuning and retain failures in
the denominator. The existing whole-edit music cases remain a separate future
coherence evaluation; pair success cannot close that gate.

Compare the new top-three suggestions with current search/manual source-window
editing. Log the candidate pool and eligibility losses. Audition with blinded,
randomized labels where practical. For timing-only A/Bs, freeze sources, order,
search and music; vary only source windows/permitted cut. A few manually refined
windows provide a diagnostic upper bound, not evidence of automatic success.

For every task record: any suggestion worth keeping after playback; which one;
kept unchanged vs adjusted; source/cut adjustments; preference; active editing
time and elapsed time including AI/preview waiting; hosted cost; and a short
failure reason (candidate missing, wrong source moment, poor timing, weak
relationship, repetition or playback friction). Log “no useful result” honestly.

Proposed pilot go/no-go threshold: at least **8 of 12** tasks have a keepable
top-three option with only local trimming/cut adjustment, and median elapsed
time to a usable pair improves by **20%** against the baseline. Freeze a common
per-task time cap before running; report failures/timeouts rather than removing
them. These are product decision thresholds for a small pilot, not statistical
proof or production Match Cuts promotion criteria. Preference and originality
remain explicit human judgments.

If the pilot passes, test four no-anchor opening tasks and then short sequences
of three to five shots with phrase-level progression and deliberate changes in
cut density. Repeatedly choosing the best local pair does not guarantee a good
sequence; that needs its own played comparison. Only then expand full automatic
drafting or style-specific strategies. Future models may combine planning and
inspection internally while returning the same bounded, editable proposals.

## Research informing this proposal

[DIRECT](https://arxiv.org/abs/2604.04875) separates global structure, editing
intent and fine editing in a mashup system. [BEAT](https://arxiv.org/abs/2605.27067)
studies elastic shot/music alignment rather than a rigid one-to-one mapping.
Our design inference is to separate source discovery from concrete editable
proposals and let footage constrain timing. These papers do not establish the
quality of our library, cross-film pairings or preferred editing style. Their
multi-agent architectures and reported benchmark results are not requirements
or acceptance results for this experiment.
