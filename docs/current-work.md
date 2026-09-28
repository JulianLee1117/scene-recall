# Current work

Temporary execution plan. README and the architecture contract remain
authoritative; historical rationale is in the ADRs. Completed implementations,
verification results and earlier comparison feedback are preserved in the
[September milestone history](history/lab-milestones-2026-09.md).
Remove this file and its AGENTS.md pointer when the remaining work is complete.

## Sleep/restart handoff — verified 2026-09-21, 16:49 Pacific

**Current decision:** pause the expensive full-library framing build and compare
small, frozen representations before choosing what to scale. Evaluate Jev's
query routing separately. Ordinary search remains the default;
`retrieval.composition_profile` remains null. Do not resume the bulk batch or
promote either experiment merely because extraction/timing checks succeeded.

**Saved operational state:** 134 optional framing preparation/fitting jobs are
on durable operator hold (`waiting_worker` with the explicit pause marker).
Eighteen optional jobs completed; their features and all held job cursors are
preserved. No optional job is running. The ingest and editor workers were online
and idle, and the API/web were listening on ports 8000/3000 at this check. The
generic worker status still says “134 queued”; these are held jobs, not 134
processes. The hold survives sleep, app restart and duplicate enqueue. It covers
this existing batch; future film ingestion may enqueue new optional work.

**Both bounded runs finished; nothing from these experiments needs to stay
awake or continue processing.** Normal Windows sleep settings are unchanged.

- **Framing:** PE-Core final, PE block 17 and PE-Spatial-S16-512 completed on
  524 frames from eight films (512 candidates, 12 provisional references).
  Numerical baseline/repeat checks passed. The subsequent
  [AI visual audit](experiments/framing-visual-audit-20260921.md) reviewed all
  twelve references plus independently inspected pool candidates. It found
  complementary strengths and useful matches buried by the current-style blend;
  no universal winner is established. The owner's subsequent first impression
  was excessive repetition and weak framing across all methods. This is recorded
  qualitative feedback; the structured human promotion gate remains unmeasured.
  Read the
  [protocol and measured results](experiments/framing-representation-pilot.md).
  Frozen manifests, rankings and checksum/timing receipts:
  `pipeline/eval/runs/framing-representation-20260921/`.
  Derived arrays and completed progress: `.tmp/framing-pilot-20260921/`.
  Isolated public checkpoint/code: `.tmp/framing-models/PE-Spatial-S16-512/`.
  Artifacts are about 95 MB, plus the 88 MB checkpoint. DINOv3 access was
  unavailable; PE-Spatial is the recorded substitute. EUPE, preprocessing
  ablations and compression remain deferred.
- **Jev:** four saved queries, two repeats per strategy, 24 independent searches
  completed with zero new hosted calls. Receipt:
  `pipeline/eval/runs/intent-latency-20260921.json`; original frozen decisions:
  `pipeline/eval/runs/intent-retrieval-20260921/`. Read the
  [Jev report](experiments/intent-guided-retrieval.md). Broad ordinary searches
  took about 3.8 s versus 5.9–6.5 s for single-strategy Jev retrieval, excluding
  interpretation. This is a small warm diagnostic, not p95 or proof of quality.
- **Verification:** 78 focused tests passed; the final adapter/pilot subset
  passed 12 tests after the last adapter changes. Serving index versions were
  unchanged by the pilots. The API had been restarted with the comparison fix
  and the frozen-only benchmark endpoint; it does not run with code autoreload.

### First actions after waking or continuing in another task

1. Recheck live state; this timestamped note is a snapshot. If services stopped,
   use the existing **Start Scene Recall.cmd** on-demand launcher. Check
   `uv run python -m pipeline.lab.worker --status` and
   `uv run python -m pipeline.cli search-features queue`. Keep operator holds.
   Do **not** run `search-features resume` just to restore normal services.
2. Read the saved receipts before running anything. These runs are complete;
   do not replay hosted calls, rebuild features or rerun completed captures.
   If a later deliberately started pilot is interrupted, its documented
   `--execute --resume` command reuses compatible saved batches after source,
   model and code validation; never edit hashes to force an incompatible resume.
3. Read the completed AI framing audit and its retained notes/ranks in
   `pipeline/eval/runs/framing-visual-review-20260921/`; do not repeat the initial
   image review or extraction. All 84 saved top-ten lists replayed exactly.
   The owner's negative first impression is recorded in the audit. Next separate
   result repetition from weak framing evidence: inspect a consistent grouped
   result view, then compare an explicit layout baseline on known-positive
   examples before assuming fusion will fix it. Existing Lab person-layout code
   may supply a person-only baseline; it does not establish general composition.
   Use fresh held-out references for claims about improvements. No new model
   diagnostic or serving change has been run yet. Use direct image comparisons,
   not the retired goal/category questionnaire. Structured per-reference human
   preference remains unmeasured. Separately assess
   whether Jev adds useful candidates compared with ordinary and fixed expansion
   enough to justify its measured extra latency.
4. Record the evidence and choose what warrants a larger evaluation before
   resuming any full-library build. Existing ADR-0082 promotion gates and
   [ADR-0092](decisions/0092-bounded-framing-representation-pilot.md) remain in
   force. Preserve unrelated concurrent-task edits and raw film evidence.

The run directories above are local, git-ignored files that survive sleep;
they are not a remote backup. Do not sweep `.tmp/` or `pipeline/eval/runs/`
before the retained results are reviewed. No watcher or scheduled continuation
was added. This note records how to continue, not a promise of work while asleep.

## Capability priorities — agreed 2026-09-21

The owner requested this planning sync before sleep; do not start these new
experiments or implementations as part of the sync. The sleep/restart handoff
above, completed runs and operator holds remain authoritative for resumption.

- **Keep the main work:** review the frozen Framing/Jev results and diagnose
  search coverage/evidence; independently evaluate flexible assembly through
  actual playback. New capabilities must not change those frozen comparisons.
- **Connect dialogue and sound work:** begin with the existing timed-dialogue
  repair priority. Share source audio, identity, timing, decoding and audition,
  while keeping speech, sound-event retrieval and separation independently
  versioned. Broader sound discovery follows a bounded evidence comparison;
  it is not a new mandatory ingestion step. See
  [shared audio work](#supporting-work--shared-audio-evidence-and-selected-clip-processing).
- **Incorporate masks into edits:** add one selected-clip mask treatment to the
  parallel Resolve/finishing pilot after plain media/timing/audio handoff works.
  Evaluate it within an actual edit; it is not a prerequisite for assembly.
- **Keep action-aware trimming** within the existing targeted-inspection work,
  tied to observed source-trim failures.
- **Park Analyze saved scene** as a lower-priority saved-scene feature.
  **Drop Brief → Reference Reel** from the proposed directions.

These are extensions of existing search/editing workflows, not a commitment to
separate Lab workspaces. Implementation and architecture changes retain the
existing evidence, cost and promotion gates; no model/provider is selected here.

## Search and ingestion priorities — reviewed 2026-09-20

**2026-09-21 update:** the owner authorized
[ADR-0091](decisions/0091-intent-guided-retrieval-comparison.md)'s intent-guided
candidate-generation comparison. A plain search can explicitly compare ordinary,
fixed evidence expansion and Jev-selected expansion in the existing playable
grid. The retired goal/questionnaire trial remains removed. Refine now uses a
compact disclosure with a consistent category row and visible applied details.
The [protocol and findings](experiments/intent-guided-retrieval.md) record the
bounded query-only calls, candidate/window results and remaining relevance gate.
Ordinary search remains default; neither this experiment nor correct intent
labels authorize automatic activation or a new ingestion/backfill.

**Comparison loading fix — 2026-09-21:** broad category vector reads now use a
pinned exact sequential scan where supported; a same-snapshot check preserved
all three complete rankings while reducing retrieval from 55.08s to 11.66s.
Live `medium shot of two people, neo-noir lighting` completed in 10.89s and
displayed all three variants. Cancel returned immediately to ordinary results
and stopped later retrieval stages. Server/browser deadlines now bound waiting
at 45/50 seconds. Regression checks passed for cancellation, timeout, stale
responses, scan equivalence and pinned generations; 593 frontend tests and
TypeScript checking also passed. Jev effectiveness/promotion remains unproven.

The user wants search to handle remembered moments, visual descriptions, mood,
shot language and literal words, with clearer refinement and better source
evidence. The sequence below records agreed goals; implementation choices and
production activation remain subject to the stated gates. Existing editor
priorities remain independent. This plan does not authorize blanket re-ingestion.

**Completed:** [ADR-0083](decisions/0083-complete-filter-before-scalar-read-limit.md)
fixes compound scalar reads so filtering precedes the result limit; it does not
establish the cause of earlier semantic misses or change relevance ranking.
[ADR-0084](decisions/0084-confirmed-movie-scope-in-search.md) adds explicitly
confirmed title chips and bounded chronological browsing when only films are
selected. Browse is a prefix, not exhaustive multi-film pagination.

**Completed inline movie mentions:** [ADR-0085](decisions/0085-inline-confirmed-movie-mentions.md)
keeps a confirmed `@Title (year)` where it was typed, with the surrounding
sentence intact. A native input and validated ranges preserve ordinary editing;
a separate compiler sends film-ID scope and retrieval text. Backspace just after
a mention or Delete just before it removes the whole mention. Editing inside
its title returns it to literal text; another confirmed occurrence can retain
that movie's scope. Undo can restore words without restoring confirmation.
The movie picker, voice input and category searches use the same draft.

The compact caret-anchored dropdown and subtle static highlighting remain.
Enter/Tab confirms an active option; ordinary full-title suggestions start
unselected. Escape removes the active @ marker and remains in plain typing
until a fresh @ or an empty field. The bar keeps its height and reveals the
caret horizontally after confirmation. Live desktop/narrow-screen checks
covered insertion, scope, deletion, duplicate mentions and sticky Escape.
This changes no backend ranking or category policy.

The category interaction remains unsettled. After retiring the personal trial,
first diagnose candidate coverage before committing to the category split below.
The user finds drag-to-category awkward; optional refinements and automatic
routing remain design options rather than a committed implementation.
The user specifically means **TypeSafe AI's Jev model**, not generic type safety.
Its [typed decisions](https://docs.typesafe.ai/introduction) and
[intent-routing pattern](https://docs.typesafe.ai/patterns/intent-routing) make
it a candidate for a small replaceable query interpreter. Evaluate multiple
independent intent signals and keep explicit user scope/overrides authoritative;
measure relevance and latency before activation. This is a research option,
not a decision to add a hosted model to every search or replace image retrieval.

**Jev feasibility probe — 2026-09-20:** with the user's authorization, an isolated
OpenRouter Decisions API probe sent 12 hand-written search queries twice, asking
seven independent intent questions per request. The returned model was
`typesafe/jev-1.13-20260917`; 24 requests used 16,752 billed input tokens and
reported $0.000703584 total cost. Client-observed median was 122 ms. The first
request took 16.3 s (cause undiagnosed); the other 23 took 88–194 ms. All 86
predeclared unambiguous label checks passed an exploratory 0.5 threshold, but
this small synthetic set is not a relevance comparison or calibrated accuracy
estimate. One explicitly negated mood example scored 0.49, illustrating why
these probabilities must not silently become hard filters. No production search
behavior changed. Local reproduction and raw results are in
`.tmp/jev-search-probe-20260920.py` and `.tmp/jev-search-probe-20260920.json`.
The subsequent frozen comparison below tests ranking; the initial probe does
not establish production latency or a reason to activate automatic routing.

**Completed bounded comparison — 2026-09-20:**
[ADR-0086](decisions/0086-frozen-search-intent-comparison.md) adds a separate
capture/decision/review workflow. All 25 cases completed across baseline, intent,
textual evidence and both, using the same captured candidates. The fixture has
10 correlated reference groups across eight films; human relevance review is
pending. See the [protocol, results and reproduction commands](experiments/search-intent-comparison.md).
The first findings are mixed: evidence improves some reference ranks, intent
also causes regressions, and text-only judgment repeats a known false caption.
The scoped booth query includes a neighboring booth shot (11th baseline, eighth
combined); the unscoped query has no candidate in its declared booth window.
Exact-anchor absence is not equivalent to whole-scene absence. No variant was
promoted and no category semantics changed.

**Second Jev round completed:** [ADR-0088](decisions/0088-bounded-jev-follow-up-probes.md)
and the [second-round report](experiments/jev-round-two.md) separate category
recognition from constraint judging. All 36 new calls completed for $0.010569048.
On 22 strict query probes, 18 matched all declared categories, with four extra
labels at overlapping semantic boundaries; two ambiguous queries remain separate.
Revised evidence instructions improve some action conflicts and the booth-window
rank (11 to 5), but increase unsupported contradictions and harm another hallway
query. No production policy is promoted. Prioritize optional editable suggestions
over automatic routing, and test missing-versus-conflicting evidence before any
stronger ranking penalty. Human relevance and interaction acceptance stay open.

**Personal trial retired — 2026-09-21:** [ADR-0090](decisions/0090-retire-personal-search-trial.md)
supersedes ADR-0089 at the owner's request. The goal/suggestion-feedback workflow
felt unintuitive and could not show whether better scenes were missing. Live
collection is disabled; remove its runtime/UI, dedicated tests and activation
helper while retaining the small historical ledger and earlier offline receipts.
There were two calibration goals, no evaluation goals and one unpriced provider
attempt; do not infer quality or zero spend from that incomplete trial.

**Proposed next priority, not another implementation:** diagnose candidate coverage
on a small set of known moments using existing hybrid/visual/semantic/lexical
variants and source-backed windows. Trace whether losses occur in evidence,
candidate generation, filtering or ordering. Then use a simple playable A/B
comparison where useful, with equal query/scope and better/same/neither judgments.
The current Jev adapter only reorders a fixed pool; it is not an independent
retriever. A union of compared results still does not prove exhaustive recall.
Do not replace this trial with another platform, paid run or automatic category
routing before choosing a concrete failure to address with the owner.

Retirement verification: 44 remaining search/player frontend tests, 14 API
lifespan/database tests, TypeScript and the production build pass. Live browser
inspection confirms the trial controls are gone. Generated
bytecode, empty feature directories and synthetic test folders remain because
automatic approval review rejected their deletion. The old API process is
disabled for trial collection but still holds its loaded routes until restart.
The syntax-checked, unexecuted `.tmp/finish-search-trial-removal.ps1` verifies API
ownership, restarts only the API, verifies route removal and deletes only the
named generated trial artifacts. Keep the 136 KiB ledger as historical evidence.

**Independent follow-up:** the user is remote on mobile and authorized further
verification without their review. Direct inspection of 22 displayed-frame
cells confirmed mixed spatial/appearance results; eight benchmark sources
passed bounded identity, range and video/audio decoding checks. Fallen Angels'
unprepared HEVC/E-AC-3 MKV remains a browser compatibility issue, separate from
search relevance. Human preference and audiovisual alignment remain pending.
A pinned native-FTS audit proved that the Matrix monitor-text anchor was found
but incorrectly rejected as blank because its caption mentioned a black screen.
[ADR-0087](decisions/0087-conservative-whole-frame-blank-filter.md) corrects that
shared filter without a model or re-ingestion; it takes effect in existing API
processes at their next normal restart. Other active services were not restarted.
Booth duplicate suppression remains conditional because dense pre-filter lists
were not captured. Preserve that limitation in later claims.

**Completed small category fixes:** reference editors now offer staged
**Change aspect** with explicit Move/Replace and Cancel, so touch/keyboard use
does not require dragging. Existing source/frame identities, upload-only facets
and clause limits remain authoritative. IME Enter no longer applies unfinished
category text; uploaded Look explains its mandatory visual restriction. Live
desktop/narrow-view checks passed for indexed moves/replacement/cancel/removal.
The larger Refine presentation remains a prototype proposal.

A candidate to prototype is one main prompt plus optional details/reference
cards, with click and drop using the same flow and an explicit choice of what
to borrow from a scene. This is a proposal, not an accepted replacement UI.
Test remembered-scene lookup, visual exploration and precise reference matching;
observe useful results, discoverability and accidental constraints. Preserve
category-only search and distinguish hard film scope from relevance clues.

1. **Settle the shared composer and refinement.** Preserve category-only search
   without requiring a main description. Separate the user-facing intentions
   Dialogue and on-screen text/OCR from the current combined Words category.
   This requires an explicit retrieval-policy change across every OCR route,
   including typed text, reference-derived queries and fallback paths; labels
   alone must not claim separation. Keep natural examples such as “close-up of
   a woman,” “symmetrical wide shot,” and “warm, grainy night street” useful
   without treating shot scale, composition or style as inferred hard filters.
2. **Review the frozen diagnostic benchmark before tuning.** The 25-query
   capture, four-way replay and blind review page are complete. Review useful
   results by query group, then add held-out descriptions before selecting a
   policy. Distinguish candidate retrieval, evidence, ranking and UI failures;
   separate exact-shot references from declared scene windows. Timing from an
   offline comparison does not establish production end-to-end latency.
   Retain the Before Sunrise booth case: 26:53.405–28:08.981
   (1613.405–1688.981 s), unit suffix `0206`, target frame 27:17.022
   (1637.022 s). The recorded scoped query “Jesse and Celine in a listening
   booth listening to a song, with romantic tension, he almost kisses her”
   returned neighboring booth unit `0207` eleventh in the frozen comparison;
   “couple exchanging glances in a wood-paneled room” placed reference unit
   `0206` first. Captions omit booth/music; neighboring units `0201`/`0202`
   mention the booth and `0203` the turntable. Use this as a general regression
   case, never a title-specific boost or a claim that a near-kiss was verified.
3. **Repair and validate timed dialogue evidence.** The same film has a Whisper
   cue at 1592.24–1745.8 s (153.56 s), with word timestamps disabled, spreading
   surrounding speech across the largely wordless booth. Diagnose and test the
   general long-cue/shot-association defect before choosing segmentation or
   alignment changes. Subtitle structure/coverage checks do not prove audio
   alignment. Preserve raw cues and source media; backfill corrected, versioned
   derivations independently and measure quote precision as well as recall.
   Use this work as the foundation for reliable quote discovery and phrase
   trimming in the existing editor. It need not wait for a sound-event index;
   link it to the shared audio work below without conflating its representations.
4. **Develop grounded local and film-wide context from the existing pilot.**
   Reuse [ADR-0066](decisions/0066-source-context-pilot.md) and its four completed
   pieces: immutable store, bounded builder, cached selector adapter and frozen
   comparison. Distinguish timestamp-local events from film/sequence background;
   supporting evidence, claim applicability and legal clip authority stay
   separate. Keep citations, uncertainty, missing coverage and producer/input
   versions explicit. Decide evidence sources and granularity through a bounded
   pilot, not remembered plots or a new mandatory full-film framework.
   The [twenty-window pilot](experiments/source-context-pilot.md) repeated the
   Moonlight child-as-older-man error in both context and selection. Keep context
   gated and `lab.context_profile: null` until factual and played evaluation
   justify expansion; cited or producer-labeled “supported” is not validation.
5. **Consider independent context retrieval after coverage evaluation.** The
   current adapter enriches already-retrieved candidates and cannot recover an
   absent moment. A separate retrieval channel is a later option only when the
   benchmark shows that missing context evidence limits candidate coverage.
   Its representation, fusion and activation policy remain undecided.
6. **Design additional filters later, from trusted metadata.** Keep exact film
   identity distinct from fuzzy visual/narrative intent. Do not promote
   uncertain generated labels into authoritative filters; decide useful fields,
   provenance and missing-data behavior before adding controls.
7. **Pause bulk Framing preparation for a small representation comparison.**
   On September 21 the user approved comparing methods before spending the
   remaining full-library compute/storage budget. The active batch drained and
   134 pending preparation/fitting jobs were put on durable operator hold;
   completed features and cursors remain. The ingest worker stays available for
   foreground film work. Follow the bounded
   [framing pilot](experiments/framing-representation-pilot.md) and
   [ADR-0092](decisions/0092-bounded-framing-representation-pilot.md): current PE,
   one preregistered intermediate layer and an accessible small spatial model
   on the same independent fixture. Keep Jev's separate text-retrieval
   evaluation fixed; do not change both representations and routing at once.
   Resume the held batch only after deciding which representation warrants the
   cost. The [search-foundation plan](experiments/search-foundation-implementation.md)
   retains its complete-coverage, human-review, latency and storage gates.
   Warm Framing p95 ≤5 s at 1,000 films and 64 GiB optional storage remain targets,
   not achieved measurements.

## Combined editor priorities — reviewed 2026-09-17

The next objective is a good, adjustable music edit from discovered footage,
with a clear path into a familiar finishing timeline. Prioritize selection and
musical timing while testing Resolve early enough to inform the integration.
These are recommended next steps, not an accepted Resolve architecture or a
change to production defaults. This planning update makes no paid requests.

- **Main effort now:** review the existing flexible-assembly comparison, diagnose
  its actual failures, and earn a bounded integration into Generate. Do not
  rebuild the completed experiment or generalize the entire editor first.
- **Run alongside it:** a small Resolve feasibility pilot from an existing frozen
  edit. It does not need to wait for a better assembler. Test exact media/timing
  handoff before trying polished native motion or a selected-clip mask treatment.
- **Join the results:** one short, editable end-to-end music edit. Keep the
  assembly-quality decision separate from the finishing-tool decision: either
  can succeed without forcing adoption of the other.
- **Small parallel research lane:** review the existing AI studies and borrow
  relevant open-source motion/generation guidance. Expand paid AI transition
  experiments after a specific edit opportunity and usable insertion path exist.
- **Keep supporting work bounded:** inspection, rhythmic detail, Match Cuts and
  source context address observed failures. They are not prerequisites for every
  edit, and they should not change inside the assembly comparison.

The immediate review package is the frozen Wish comparison plus its ledger,
a Resolve operation/compatibility report on a separate short timeline, and the
existing v10 AI/original pairs. Playback judgments must remain distinct from
frame checks, valid output, model praise and successful tool execution. Where
human preference is still pending, record it as pending while independent
technical work proceeds. No new daily feedback workflow is required.

## Priority 1 — earn flexible discovery/assembly integration

The shared **AI direction / Edit** workspace is implemented under
[ADR-0064](decisions/0064-scoped-editor-direction-and-two-tab-workspace.md).
The private discovery/assembly comparison is now implemented under
[ADR-0065](decisions/0065-private-discovery-and-flexible-assembly.md), through
the [frozen runner](experiments/music-edit-comparisons.md). Normal Generate
still uses the existing engine; the experiment is not a production replacement.

The runner keeps all retrieved candidates and exclusions separate from the
bounded model catalog. It compares a fixed-slot replay, joint footage/timing
assembly from the same initial catalog, and one optional targeted discovery
expansion. Four hosted calls, eight recipes and one expansion are hard ceilings.
Beats and region boundaries stay optional cues. Source validity and exact
coverage remain authoritative; invalid output fails without substitution or repair.

### First: review the controlled played comparison

The first Wish run is complete: 144 eligible unique sources retained, 48 offered;
fixed replay placed 4 of 16 positions, joint assembly placed all 12 of its chosen
positions. Both previews rendered. The model requested no expansion, so three
hosted calls were used. All seven saved projects remained unchanged. See the
[pilot results and caveats](experiments/music-edit-comparisons.md#first-wish-pilot--2026-09-15).
The implementation is complete; played preference and the usefulness of expansion
remain open questions.

Verification: 160 focused comparison/selection tests passed; the backend suite
passed 2,454 tests with nine skips. Final startup/cancellation changes are covered
by the focused checks. Both previews contain 720 frames at 24 fps and 1280×720.

- Review the frozen Wish revision 4 pilot, 3.68–33.68 source-track seconds.
  Compare musical phrasing, thematic relevance, motif development, continuity,
  accidental repetition, gaps and cost. Human preference remains unset until
  played review; no per-shot annotation or training feedback is required.
- Separate adaptation from creative quality: the fixed baseline retains its
  old specific shot requests while using the shared newly discovered catalog.
  It is a controlled replay, not the historic render or a full production A/B.
  More filled positions alone cannot establish a better edit.
- Inspect the retained ledger when a useful image is missing: distinguish
  retrieval, source eligibility, omission from the model catalog and selection.
  An exhausted bounded query does not establish absence from the library.
- If promising, run a separately bounded Starjunk follow-up. Keep music/model
  evidence fixed, do not add inspection/context changes simultaneously, and
  do not automatically repeat requests until a preferred outcome appears.

Existing incomplete perception remains a separate failure to diagnose. Preserve
the [broader planning proposal](experiments/ai-music-video-planning-review.md)
as rationale, with the implemented scope defined by the accepted contract.

After useful played results from Wish and the separately bounded Starjunk
follow-up, decide integration through the existing Generate flow.
Optional range direction is now available as input for generation; it does not
regenerate only that range. Evaluate selected-range regeneration separately
if the assembly experiment earns integration. Preserve pins and exact user-owned
cuts; keep manual snapping independent from the AI's timing choices. Avoid mandatory
categories or a panel for every internal stage.

For the first production integration, preserve the existing worker, revision
checks, cancellation, atomic publication and grouped Undo. The private assembler
currently accepts only bounded passages without locks; this does not establish
safe production handling of protected content, arbitrary selected ranges or
full-length edits. Define those supported/unsupported scopes explicitly before
promotion. Never unlock, move or reinterpret user-owned cuts to make an assembly
fit. Keep the existing path available for scopes the new assembly cannot handle.

The first quality decision is whether discovering footage before choosing count,
order, source windows and interior cut frames improves an actual sequence. If it
does, test Starjunk under a separate frozen budget before production integration. If it
does not, use the ledger to choose one next correction: discovery coverage,
missing/incorrect evidence, or assembly. Do not compensate for weak scene choice
with more effects. Resolve availability is not a prerequisite for this decision.

### Supporting evidence changes — evaluate independently

Retain the following work, without making more detailed beat detection a
prerequisite for testing the assembly architecture or a definition of edit style.

### Finer rhythmic guides

Starjunk's first 40.04 seconds already contain 114 detected beats and 29
downbeats, approximately a 170 BPM pulse. The timeline displays them all.
Missing detail concerns faster attacks and fills; this is not evidence that the
main pulse detector failed.

- Add a separate, versioned local onset/attack derivation for useful drum and
  synth entrances. Preserve the original audio and existing beat/downbeat data.
- Expose optional attack guides and clear snapping alongside the existing beat
  controls. Keep the timeline readable when zoomed out and detailed when zoomed in.
- Distinguish detected attacks from interpolated subdivisions, estimated pulse
  and editorial cuts. More guide points must not impose more cuts.
- Make validated, bounded attack evidence available through the shared music
  evidence packet so both manual editing and AI timing can use it.

### More specific listening observations

Starjunk's saved interpretation has only five broad events across that passage;
these do not describe individual fills or changes in rapid articulation.

- Improve observations of articulation, rhythmic density changes, fills, pauses
  and meaningful accents where the audio supports them.
- Keep approximate listening interpretations separate from measured timestamps;
  use detailed local evidence to inform emphasis without inventing exact events.
- Preserve whole-passage context and the simple Generate edit flow. Show progress
  and optional findings in the existing analysis/details surfaces.

### Scene variety across the complete edit

Wish's completed AI revision selected Her for 4 of 16 shots, despite Her making
up about 7% of per-slot offers. Repeated urban-isolation queries made it rank
highly. Existing safeguards prevent reused footage, not different shots from
the same film. The saved fifth Her shot came from a later manual replacement.

- Broaden the planner's song-specific visual ideas when it repeatedly falls back
  to generic lonely-city, window and night-light imagery.
- Add soft consideration of film use across the complete sequence, including
  earlier batches. Prefer comparably suitable alternatives while preserving
  deliberate continuity, relevance and explicit user choices.
- Keep selection reasons inspectable. Avoid hard film quotas or random variety
  that disguises repetitive concepts or weakens a good sequence. Preserve
  intentional visual motifs and supported same-film continuity.

**Evaluation:** retain Starjunk project `bb681ae5-590a-47b1-9d7c-18b42e38583c`
revision 5 and Wish project `8d6dc709-21ce-43f4-9897-bba4be96d445` AI revision 4
as diagnostic references. Work from frozen copies, preserving saved edits.
Check attack timing against audible events, missed/false guides, useful snapping,
articulation coverage, film concentration, relevance, continuity and added cost.
Use fixed inputs and controlled comparisons; higher marker counts or more films
alone are not success. Record latency separately from creative preference.

## Priority 2 — Resolve feasibility and local motion, in parallel

Use a disposable 15–20 second edit with 4–6 known source windows and the same
music, rather than waiting for new generation. Begin from the frozen
`render_manifest` boundary in `pipeline/lab/media.py`; retain source film IDs,
the Scene Recall revision, source windows, output frames and audio decisions in
a sidecar receipt. Initially the handoff is one way into a separate Resolve
project/timeline; Resolve edits must not overwrite a Scene Recall revision that
cannot represent them.

1. **Verify the installed capability.** The last local check found Resolve
   21.0.1.11; Studio entitlement and the 21.1 assistant connection are not yet
   established. Blackmagic documents the newer assistant integration, but product
   controls do not prove every control is available to the agent. Inspect the
   installed operation catalog and record actual successes and limitations.
2. **Prove a plain timeline first.** Preserve the main editor's 24 fps, exact
   cut positions, source timing, framing and passage duration. Consume the
   manifest's cumulative frame counts rather than rounding each clip separately.
   Verify real library media import and relevant color handling. Preserve the
   existing music/dialogue
   mix as a reference; a bounded baked mix is acceptable for this first proof,
   with its lack of editable stems stated. Include a source-dialogue overlap in
   the fidelity check; picture retiming must not retime dialogue on the song clock.
   Do not silently omit overlapping dialogue, voice-focus processing, fades or
   duck envelopes from ADR-0077/0078.
3. **Test motion on a duplicate.** Compare the same sequence with a directional
   whip, one shaped speed ramp and a restrained light accent. Verify curve control,
   edge coverage, blur, incoming/outgoing motion continuity and supported settling.
   Test optical flow only where source cadence/speed needs it; inspect artifacts.
   A constant-speed API or an imported flattened render does not prove an editable
   speed ramp. Unavailable operations remain explicit.
4. **Test one useful mask treatment after plain handoff.** On a duplicate of the
   short edit, track a selected person/object for a foreground reveal,
   background-only treatment or text behind the subject. Check available native
   Resolve controls first; compare local tracking/export only where needed.
   Existing matching silhouettes do not establish clean editing mattes. Inspect
   edges, motion blur and occlusion during playback; verify corrections and the
   actual exported composite. Prepare masks only for selected source windows,
   retaining source identity, model/profile and source-to-output timing through
   trims or retiming. Record editability, correction effort, latency and storage.
   This does not authorize full-film mask ingestion or a separate masking app.
5. **Check the user workflow.** Trim a clip, adjust one effect and restore it;
   save/reopen and render. A rebuild should create an identifiable new version,
   preserve manual changes and leave unrelated timing/audio intact. Compare
   full-speed playback and actual adjustment effort with the current baseline.
   Inspect project state before retrying an operation whose outcome is uncertain.

Link original media where supported. If a source needs conversion, make only
the selected window with declared handles; do not create whole-film proxies.
Measure time to first playable, render time and total additional media/cache
bytes. Keep cache/output paths inside a named pilot workspace and retain the
few reviewed versions plus receipts. No broad cleanup or service restart is
needed for this planning work; keep ingestion independent.

**Gate:** reliable timing/audio handoff and a useful editable motion treatment
justify a narrow finishing adapter. If only import works, keep it as an optional
handoff and record which finishing operations still require manual work. If
installation/licensing blocks live testing, continue the manifest and source
compatibility work without claiming the pilot passed. Do not build a universal
NLE adapter or a second custom curve editor before this result.

## Priority 3 — converge on one usable edit and a narrow capability boundary

After the relevant pilots pass, demonstrate one short complete flow: music and
direction, discovered footage, a valid editable sequence, an optional Resolve
handoff, and a small number of intentionally selected transitions. Retain the
simple AI direction/Edit flow; a user should not have to operate internal stages
or select a different editor generation engine.

Scene Recall should retain scene discovery, source authority, music evidence,
editorial intent, generation budgets/receipts and saved revisions. The proposed
Resolve adapter owns translation to supported editing operations; Resolve owns
the detailed finishing timeline. Implement only the boundary needed by the
successful pilot, with an accepted ADR and architecture update before promotion.

Resolve the time model explicitly at this point. The main project is 24 fps and
ordinary source windows match slot duration; the independent Transition Lab is
30 fps and local ramps/overlaps can alter output duration. Map source timestamps,
output frames, clip handles, overlap ownership and retiming without silently
stretching footage or moving music. Do not directly insert a lab render by
assuming its durations or endpoint frames already match an editor cut.

A transition capability should report applicable input evidence, required
handles, duration/frame-rate behavior, editable controls, execution support and
artifact/cost provenance. This is a small validated interface, not a new general
agent framework. Start with a few proven local treatments and hard cuts. Match
Cuts remains optional and subject to its played-pair gate below. Selected-range
regeneration and bidirectional Resolve synchronization remain separate decisions.

## Priority 4 — selective AI transitions and open-source reuse

In parallel now, play and assess the existing
[v10 music studies](experiments/transition-v10-edit-studies-2026-09.md). Their
mechanical verification is complete, but full-speed visual/listening acceptance
is still open. The copper accent can also be made procedurally; it alone does
not justify another AI capability. The synchronized winter switch is a useful
candidate, with subject redrawing still a known limitation. Reuse their retained
generated originals and identical-timing comparisons before paying for variants.

Once there is a suitable edit and a tested placement path, compare a small set
of distinct opportunities: source-preserving environment/material changes;
short endpoint/reference-motion bridges between compatible scenes; or generated
elements combined with ordinary masks and motion. Choose the scenes for a visible
editorial purpose, compatible movement/framing and achievable entrance/exit.
First compare against the plain cut and a local effect on the same sequence.
Do not require an AI transition at every cut or use a morph to repair unrelated
scene selection. Test at playback speed with music as well as around the seam.

Keep each paid test separately quoted and capped, preserving the existing
submission/uncertainty receipts and avoiding automatic regeneration. Try a new
provider only for a named capability or an observed failure our current Runway
route cannot address; do not add a broad model catalog first. Reuse each generated
original for local trim, crop, mask, grade and blend variants. Changes to generated
content or camera movement may require another paid generation and must be
distinguished from those local controls. Record retained originals and derivative
bytes; preserve live references and remove only identified unused derivatives
through the established lifecycle, never raw sources or evidence.

Higgsfield's generation skills provide useful media-role/schema discovery and
source-video plus timed-guide-frame patterns. Its public AE/Blender bridge has
motion/reference and editable-transition guidance worth adapting. It is not a
Resolve connector, and its published live validation does not establish Windows
readiness or transition taste. Prefer these narrow reusable ideas over adopting
another hosted agent, unrelated advertising skills or a parallel AE integration
by default. If Resolve fails a specific motion requirement, evaluate AE on that
same requirement rather than starting a second finishing stack speculatively.

External evidence checked 2026-09-17:

- [Blackmagic Resolve features](https://www.blackmagicdesign.com/products/davinciresolve/whatsnew)
  and [21.1 assistant announcement](https://www.linkedin.com/posts/blackmagic-design_introducing-davinci-resolve-211-update-activity-7502954600224673792-SRtk).
- [Higgsfield generation workflows](https://github.com/higgsfield-ai/skills/blob/main/higgsfield-generate/references/workflows.md),
  [motion-reference guidance](https://github.com/higgsfield-ai/fnf-local-pluging-bridge-mcp/blob/main/skills/ae-clean-rig/references/02-reference-motion.md)
  and [bridge validation limits](https://github.com/higgsfield-ai/fnf-local-pluging-bridge-mcp/blob/main/docs/VALIDATION.md).

These support evaluating the capabilities; none establishes a preferred edit.

## Supporting work — evaluate the existing inspection experiment

The initial played comparisons were inconclusive. Keep inspection available
experimentally with its current default unchanged. Test a few concrete action,
source-match or timing failures; distinguish useful corrections from equally
valid creative alternatives. Improve flag precision and assess added latency
before expanding coverage. Reuse the implemented bounded comparison workflow
under [ADR-0061](decisions/0061-targeted-footage-inspection-and-frozen-comparisons.md).
Query repair, intention rewriting and merges remain deferred until their need
and architecture decision are established; do not rebuild completed inspection.

Action-aware lead-in/action/settle proposals belong to this existing inspector
when a played clip cuts off or misses the intended event. Evaluate new temporal
evidence on those failures independently of assembly; sampled timestamps alone
cannot establish exact action completion or enlarge legal source windows.

## Supporting work — selective Match Cuts refinement

For intended transitions, evaluate actual outgoing/incoming frames, applied
crops and supported motion/subject evidence. Reuse existing capabilities through
an optional integration; do not require a GPU match search at every cut or the
unfinished experiment UI. Gate broader use on played-pair usefulness and measured
latency. Unavailable evidence must remain distinct from a successful match.

## Supporting work — shared audio evidence and selected-clip processing

Connect sound discovery and quote tools through the retained source audio and
the existing source-window/audition/export machinery in ADR-0078. Keep three
independent outputs: corrected speech text with phrase/word timing; sound-event
descriptions/ranges and any separately justified audio embeddings; and optional
separated target/residual audio assets for selected excerpts. Preserve model,
input and processing lineage; do not mix audio vectors with image/text spaces.

Start with the timed-dialogue defect above, verifying audible words and quote
boundaries before exposing precise trims. Current `voice_focus` is channel
selection/filtering, not learned source separation. Evaluate selected-clip
isolation only when an actual edit needs cleaner speech or a particular sound;
compare original and processed audio through the shared audition/export path.

Broader sound search remains a later bounded experiment: choose a few films and
10–15 real sound requests, compare useful audible results and time saved, then
decide whether a new retrieval profile warrants expansion. Prepare optional,
independently backfillable evidence from retained originals rather than repeat
visual ingestion. Routine ingestion integration and library-wide processing
remain gated on that result. These additions do not delay the existing dialogue
repair or authorize new processing during the pre-sleep planning sync.

## Supporting work — targeted ingestion improvements

The user authorized the bounded source-context implementation in ADR-0066:
versioned source records, an explicit backfill, cached editor enrichment and
frozen comparisons on three films/about twenty moments. The implementation and
twenty-window backfill are complete; a cache-only replay reused every artifact.
[The pilot report](experiments/source-context-pilot.md) records a material
Moonlight caption error that survived both the new producer and context-enabled
selection, so context remains off by default. One controlled pair is rendered
and mechanically verified; human played preference remains pending. This is
independent of the assembly experiment. Keep main-search
indexes and normal ingestion unchanged; complete factual and played evaluation
before broad context activation.

Use recurring failures to distinguish retrieval misses from missing action or
context evidence. Backfill the relevant versioned representation on a
representative subset before expanding. Preserve raw films and timestamped
evidence; avoid blanket re-ingestion or mixing incompatible model spaces.

## Remaining product checks

AI direction/Edit mechanics are verified at desktop and narrow widths:
Save/reload; range creation, resizing and grouped Undo; tab switches preserving
timeline zoom/scroll and suspending playback; scene removal into a placeholder
and cut deletion. A controlled progress fixture also verified task details and
Cancel in both views while preserving the existing edit. The final two-tab checks
passed 238 frontend tests, 862 Lab tests, TypeScript and the production build.
The earlier full backend run passed 2,269 tests with eight skipped; the final
import/Undo and fixed-fill corrections were covered by the later Lab run.
Browser generation progress used a disposable deterministic fixture; no hosted
generation comparison was launched. Creative played review of the implemented
assembly experiment remains pending; the experiment itself is complete.

The follow-up UI polish keeps generation reachable while scrolling, makes range
handles visible, bounds the film menu, and aligns cut-tool icons with their labels.
Precise direction/lyric timing accepts partial numeric typing without premature
clamping; valid values reach Save immediately. Keyboard range selection, cancelled
drags and grouped Undo have focused coverage. The latest frontend run passed 246
tests, TypeScript and the production build. Desktop and 390px browser checks covered
time entry/Save/Undo, negative passage-relative times, keyboard trimming, film-menu
bounds and playback suspension. Saved footage and cuts were unchanged by direction
edits; no hosted generation was launched.

- Complete the next-scene browser flow: proposal, played pair, source/crop
  adjustment, Apply and Undo.
- Complete the saved Match Cuts project's played-pair browser walkthrough.
- Evaluate real edits for musical fit, source relevance and editing usability;
  evaluate Match Cuts usefulness and first-playable latency.
- Review representative main-search queries and the final branch diff before
  choosing a release or merge path.

## Working constraints

Keep the editor simple: one Generate flow, optional controls beside the feature
they affect, and inspectable progress. Preserve manual cuts, instructions, locks,
legal source windows, atomic publication and Undo. Keep expensive inspection
outside ordinary main search. Preserve unrelated worktree changes and saved
projects. Run focused checks first. Material architecture changes must update
[the contract](search-architecture.md) and an accepted ADR in the same work;
this temporary plan does not override their scope or evidence gates.
