# AI Music Video planning review

Design proposal, 2026-09-13; refined 2026-09-14. This is a research and architecture review, not an accepted ADR or an implemented replacement. README and the architecture contract remain authoritative. The working first target is a montage built around themes and visual motifs with intentional musical pacing; exact match-cut and lyric-driven modes share foundations but require different evidence.

The recommendation is to plan the song's progression globally, discover source-backed footage opportunities, and choose exact footage windows and cut timing together inside a bounded editable passage. Keep the current committed timeline, renderer, source authority, job lifecycle and Undo. Replace the assumption that every AI-planned shot must have a fixed duration before retrieval.

## Musical timing stays expressive

The user's clarification is explicit: cuts need not land on hard beats. A cut
may anticipate a sound, follow a gesture, interrupt an action deliberately, sit
between pulses or wait through several beats. A musical event can align with
movement inside a shot. Neither completing every action nor synchronizing every
cut is a universal objective. Avoid artificial timing jitter as well.

The current ADR-0060 timing request already allows arbitrary integer output
frames and calls beats optional. The restriction comes later: ADR-0041/0058
selection retains the proposed shot count/order and offers only nominal/band
endpoints plus up to four nearby beat/downbeat frames. Another prompt sentence
cannot remove that structural limit.

In the proposed assembler, internal cuts may occupy any legal output frame.
Music cues are evidence, with their confidence and timestamp precision retained;
only user-protected positions and exact requested passage endpoints are hard
boundaries. Editorial section labels are soft regions: a shot may cross a phrase
change without a cut. Processing limits must not manufacture a visible boundary.
Do not optimize beat-hit rate, off-beat rate or duration variation as taste scores.

## Search as an editorial tool

The near-term opportunity is discovering relationships across films: different
expressions of affection, repeated circular forms, a progression from cool to
warm imagery, alternation between crowds and solitude, or a recurring image
that changes meaning when it returns. These are montage ideas to test, not
claims that the current library contains every example or that exact motion
correspondence is available.

Search several complementary expressions of an idea into a shared bounded pool,
then let discoveries influence the sequence. Keep query provenance and candidate
evidence. A matching color or mood does not establish the scene's action or plot;
semantic claims need their own support. Do not assign a detailed imaginary scene
to every beat before discovering what the library can offer.

Distinguish accidental reuse of the same footage, unintended concentration on
one film and deliberate visual repetition. Preserve current automatic footage
reuse guards; manual deliberate reuse stays available. Related images or several
shots from one film may be appropriate. Prefer comparably suitable alternatives
when concentration adds nothing, without imposing film quotas or penalizing the
very motif the user requested.

## Failures observed before ADR-0041

The inspected Steve Lacy project is revision 6, passage 9.94–39.94 seconds, with eight placed clips. Its musical interpretation describes urgency and forward motion; the visual planner turns that into preparation and an urban journey. That is a creative interpretation, not verified song meaning. The three user-reported failures have different origins:

- Parasite: the stored caption describes intimate fabric pulling. The chooser converts it into readiness without evidence of fastening clothing or leaving. All three annotation frames are inside the chosen window, so simply centering a different trim is insufficient.
- Dune: the annotation says the person is motionless, while the user reports movement in playback. This is a temporal assertion from sparse stills propagated into selection.
- Wild Strawberries: the annotated subdivision spans approximately 15.27 seconds; only one sampled frame falls inside the selected four seconds. Retaining that frame does not establish the complete action or dream context.

At the time of this audit, generation adopted audio-proposed cuts before scene
retrieval and selection required a candidate for each nonempty offer. ADR-0041
subsequently added bounded source-aware cut choices and explicit abstention;
ADR-0043 added pace density and footage-reuse guards. Those original limitations
are no longer implementation tasks. Sparse-frame semantic fit and motion/context
evidence remain separate quality questions. The
[search adapter](../../pipeline/lab/search_plan.py) filters legal durations from
a bounded retrieved prefix, and the [recipe engine](../../pipeline/search/recipe.py)
combines most clues as a ranked union: a mood contribution does not establish
the requested action. Consult the current architecture contract for implemented
generation behavior.

## Architectural alternatives

**Storyboard first, then fill and repair.** This remains useful for user-authored
fixed cuts. Explicit abstention is implemented; additional evidence checks and
a bounded repair search remain proposals for reducing unsupported substitutions.
Fixing all imagery and durations before seeing footage still constrains selection.

**Build by repeatedly choosing the next scene.** This uses the strongest existing interactive primitive: a played pair with source-window adjustment. It is easy to inspect and revise. Used alone, it can drift from the song's arc, repeat familiar imagery and create an awkward ending. Keep it for local revision and as an assembly building block.

**Global musical intent with bounded source-aware assembly — recommended experiment.** Establish a small progression of editorial roles, explore footage, then coordinate several actual windows and their lengths together. A local choice is assessed against both neighbors and the passage's destination. This preserves a manageable decision space without treating the initial shortlist as a catalog inventory.

**A learned end-to-end editor or a full-library transition graph.** These could eventually capture preferences that hand-written rules miss. Today, the project lacks a trustworthy editing reward and representative feedback. A global graph also multiplies pairwise work before there is evidence that local candidate quality is adequate. Revisit only after the bounded experiment identifies a specific limitation.

## Proposed flow

**1. Prepare independent music evidence.** Keep measured beats/downbeats, relative amplitude, approximate heard events, supplied lyrics and interpretations separate. Separate reusable listening evidence from creative visual planning so changing a motif does not require relistening. A beat is a timing opportunity. An inferred emotional interpretation is a hypothesis, not an acoustic measurement. Automatic transcription/alignment is a separate experiment for lyric-led editing, especially with singing and repeated adlibs.

**2. Establish a few editorial intentions.** Describe what a passage should do: introduce tension, sustain intimacy, build momentum, offer a contrast or recall an earlier image. Include loose pacing and desired relationships, but defer shot counts, precise shot descriptions and internal cut positions. Intentions may cover overlapping musical regions; their boundaries do not require cuts. Existing user-written requirements remain binding. For a new one-button edit, the system chooses a default concept automatically; an alternate concept is optional customization.

**3. Discover and inspect a bounded pool.** Search distinct interpretations of the intent, keeping high-relevance options and useful alternatives. For yearning, physical distance, missed connection and restless waiting are different routes; three paraphrases of the same street scene are not. Let strong discoveries refine AI-owned imagery while preserving its musical purpose. A small shared pool across neighboring sections allows a promising shot to move to a better part of the song.

Use progressively more expensive evidence: existing indexes and metadata first; indexed images for appearance; exact source-window inspection for shortlisted actions; bounded neighboring-shot/dialogue context when scene meaning matters. Record what each observation actually covers. Neither a single-frame crop nor three stills establishes continuous motion or an action's completion.

**4. Assemble windows and timing together.** For an untouched first-generation passage, choose source identity, source-in/out, shot count, ordering and internal cut points jointly within exact outer bounds. A six-second action may earn a longer hold; a short gesture may become a brief insert. A musical accent can land on an event inside the shot rather than requiring a cut. Source identity must come from the offered pool, but internal cuts may use any legal output frame. Deterministic code checks total coverage, frame-grid timing, source handles and protected edits. Musical cues inform a boundary without restricting every cut to a beat. Precise action synchronization requires observed source timing; broad captions cannot establish it.

Initially generate one sequence, with at most one bounded revision for a concrete defect. An optional alternative should change an editorial interpretation or sequence relationship, not randomize the same ranking. A beam search or numerical sequence optimizer is a later option if a small candidate-pool experiment shows greedy selection is the limiting factor; there is currently no calibrated scalar score for taste.

**5. Check and publish one editable result.** Separate hard technical validity, observed content fit and subjective editorial fit. Essential action requirements may be supported, contradicted or unknown. Reject contradictions and do not silently satisfy an unknown mandatory action. Unknown optional preferences can remain explicit. If no suitable option is found, revise the AI-owned search once or keep a gap/current clip. A failed search is not proof that the library contains no suitable scene. Preserve one job transaction and one Undo, with actual playback for review.

## The search changes that matter

Keep one shared retrieval engine for people and the AI editor. The editor can add orchestration and inspection above it; ordinary descriptive search should retain its direct, low-latency path.

**Candidate eligibility:** make duration/source eligibility part of bounded candidate collection, with a typed parameter or bounded refill before offers are finalized. Preserve visual gates and film scope. Record when filtering exhausts the search budget so a duration mismatch is not mislabeled as semantic absence.

**Evidence-aware offers:** retain the indexed shot/subdivision, a proposed playback window and contextual evidence as three separate scopes. Add a small envelope above current shot results rather than replacing canonical unit identities. Suggested fields are source authority, legal bounds, proposed windows, query-specific matches, observed evidence, requirement status and uncertainty. Context never expands trim authority.

**One editor pool policy:** make near-duplicate handling and temporal spread consistent across broad and combined searches. Current film balancing is not coverage of different ideas. Keep multiple useful moments from the same film when they serve a deliberate sequence; novelty should not force incoherence.

**Requirements versus preferences:** retain broad ranked fusion for discovery. Add explicit evaluation of the essential subject/action after retrieval; do not convert every mood or composition clue into a hard intersection. Technical constraints can be checked deterministically. Semantic and temporal requirements need the corresponding evidence.

**Relationships:** allow the planner to express continuation, contrast or a callback and compile those intentions into supported text/reference searches. Current reference similarity does not mean verified movement continuation. Exact outgoing/incoming frame or motion queries require their own prepared profiles and the existing Match Cuts decision gates.

**Candidate diagnostics:** ADR-0044 now retains complete offered-source manifests,
including IDs, source bounds, evidence and input/schema hashes, even when
selection fails. The six saved alternatives are a presentation shortlist, not
the complete audit record. Any additional query/profile lineage or pre/post
eligibility counts should extend those diagnostics only when needed to separate
retrieval misses, filtering, selection and trim failures. Keep this an execution
artifact rather than another prominent editor panel.

These improvements can later support human requests such as finding a readable gesture within a duration range or a visual continuation of a selected moment. They do not require an always-on natural-language agent for every normal search.

## Ingestion and scale

The library is searched through reusable indexes, not put into an LLM context window. Keep query, candidate, inspection and retry budgets tied to the edit request. Index latency and storage still require measurement as the collection grows; a fixed LLM budget does not guarantee constant retrieval time or adequate recall.

Maintain three conceptual resolutions: images/shots for discovery; time-localized moments for action and trimming; surrounding events for meaning. Start by hydrating nearby existing captions/dialogue and inspecting the selected source windows. This can reveal local contradictions, but a dream or character relationship may need wider evidence than two neighboring shots.

If the small comparison shows that good moments never enter the pool, pilot a versioned temporal representation on a representative subset. If context repeatedly changes selection, pilot a separately scoped event/context annotation. Keep raw sources and timestamped evidence intact; backfill derived profiles independently. More descriptive prose is not a substitute for better observations. Do not globally reannotate the collection merely because a newer model exists.

This also avoids two bad scaling shortcuts: committing to the first retrieved images as the only possible concept, and creating a comprehensive ontology or all-film scene graph before its value is measured. Limited diverse exploration is a hypothesis to test, not a guarantee of catalog coverage.

## Minimal implementation boundary

Keep `ProjectDocument` as the single committed timeline. The generation job carries an ephemeral proposal with four parts: music evidence, editorial intentions, source offers, and an assembly scope. The scope freezes the project revision, exact passage endpoints and protected content. Exact clips are materialized only when the proposal validates. Existing saved edits remain fixed unless the user explicitly requests replanning a selected region.

Use a separate versioned assembly response, rather than weakening today's fixed
slot selector: an ordered variable-length list of offered source aliases,
ending frames, normalized legal source positions and concise reasons.
Resolve starts after duration validation, preserving ADR-0058's separation of
source position from authoritative timestamps. Its existing finite-boundary
solver cannot itself provide variable count/order or unrestricted timing; reuse
validation primitives without claiming that solver already solves this proposal.
Invalid or impossible output must not silently stretch footage, snap to beats,
invent sources or overwrite the saved edit.

The logical roles are perception, editorial planning and assembly/validation. They do not require three services, a general agent framework or three mandatory model calls. Reuse current audio/text adapters, capabilities, the worker, preview renderer and source validation. A stronger future model can replace a perception or planning implementation without changing source authority or the editor's project format.

Style differences should initially be a few explicit planning settings and evidence requirements. Emotional montage emphasizes expressive relationships and pacing; graphic/action montage emphasizes actual entry/exit motion or shape; lyric-led montage needs reliable words/timing and enough narrative context. A universal weighted score would conceal those differences.

## Rollout: one editor, no user-facing versions

The user superseded the v1/v2 launch proposal on 2026-09-13: old projects are
disposable and selection must stay simple. ADR-0041 implements one editor with
bounded source-aware first-cut generation, explicit abstention and inspectable
progress. Existing playback/storage contracts are shared; strategy identity lives
in diagnostic receipts. Six old Lab projects were deleted through the supported
API, preserving original tracks and films. The broader architecture above remains
an experiment proposal. ADR-0060 now separates whole-passage timing from visual
planning, and ADR-0061 implements optional bounded post-selection inspection.
Neither implements variable shot count/order after discovery or establishes
continuous action understanding. Current projects must be preserved; the earlier
one-time project deletion is not standing authorization to delete later work.

After the architecture comparison shows value, retain preview, timeline and the
existing Scenes library. A selected time range can expose one optional direction
such as "keep this blue and crowded, then open up at the chorus," with discover,
preview and explicit regeneration scoped to that range. Reuse clip locks and
direct placement for precise choices; do not build mandatory motif categories,
a separate collection-management app or a panel for every internal model stage.
Manual snapping is an editing aid, independent of AI timing. Exact user-pinned
cuts remain exact. One Generate action and existing progress/Details stay primary.

Shared search improvements should be promoted independently of the editor:
accurate annotations, precise matched moments, contextual evidence and optional
duration eligibility can benefit human search. Multiple exploratory queries,
song-conditioned sequence ranking, flexible timing and automatic clip acceptance
are editor behavior. Expensive temporal inspection can later be exposed on demand
without joining the default main-search request. Automatic placement requires a
higher acceptance standard than a discovery gallery, where partial and surprising
matches may still be useful.

Keep new retrieval options opt-in with unchanged defaults. Keep new annotation or
embedding representations in separately versioned profiles. Compare remembered-
scene and open-ended discovery queries, result diversity and latency before
promoting a shared ranking/profile change to main search. Film variety that helps
discovery can conflict with recurring-film coherence in an edit, so there should
not be one universal ranking policy.

## Experiment before integration

The next step is a frozen assembly comparison, before a large UI revision or
simultaneous changes to listening, retrieval and inspection. This proposal alone
does not authorize uncapped hosted evaluations; define the comparison's bounded
model budget and architecture decision before implementation.

**First: isolate the assembly decision.** Use one short passage containing two
musical roles, with Starjunk and Wish as contrasting follow-ups. Freeze the music
evidence, broad intent, model, eligible candidate pool and indexed evidence.
Compare the current ordered-slot assembly with one new assembly request that
can vary count, order, legal source position and interior cut frames. Give both
variants the same offered footage and technical limits. Keep inspection settings
and any frozen observations identical in both variants; do not change its product
default or infer that prior inconclusive comparisons rejected it.

The first candidate pool is an explicit experiment input, not proof of discovery
recall. Include enough usable footage to avoid imposing a shot-count quota by
starving the pool. Record coverage and exclusions. Use a few known source moments
where needed to distinguish an assembly failure from missing temporal perception;
do not claim that manually verified evidence was automatically inferred.

Exercise an off-beat cut, a hold across an interpreted section boundary, an
intentional burst, a source shorter than a nominal old slot, deliberate visual
repetition and fixed outer neighbors. These are cases the design must allow,
not patterns every render must contain. Render with the existing renderer and
compare actual playback, repetition, meaning, continuity, gaps and latency.
Keep results private and leave saved projects unchanged. No new editor version,
service, project schema, retrieval profile or automatic quality-judge loop is
needed for this first experiment.

**Then: test discovery and evidence separately.** Add the shared pool's few
complementary searches and soft whole-sequence film-use context, retaining frozen
receipts. Evaluate richer attacks/listening independently so a gain is not
misattributed to assembly. Finer musical guides do not determine the montage's
style. Use the following targeted comparisons when their failure is observed:

**Evidence and selection comparison:** freeze a candidate pool containing the three reported failures, suitable alternatives where known, and hard negatives. Compare today's selector with requirement checks, scoped context and explicit abstention. Measure false acceptance and unnecessary rejection separately. Record whether a suitable candidate was actually offered. An AI judge may flag a contradiction; it cannot establish aesthetic success.

**Timing architecture comparison:** use short passages and the same three-to-five source candidates, with a few manually inspected action intervals as an oracle. Compare fixed audio-first cuts with source-aware variable windows inside the same outer passage. This isolates planning from missing perception. Then replace the oracle with bounded automatic inspection and measure the quality loss and runtime. Include sustained vocals, percussion, a musical change and an action that benefits from completing before the cut.

Use the existing Steve Lacy and 10cc passages as diagnostic seeds, plus one contrasting action-heavy passage for the wider evaluation. A quick blind choice between two actual renders and one reason for a bad cut is sufficient for the first product decision. Keep source validity, full duration, protected edits and preview/render parity as hard gates. Track model latency, inspected video seconds, query count and cost separately. Keep/apply/trim/undo behavior is useful but biased; an unseen alternative is not a negative training label.

Proceed with wider generation only if there is a repeatable improvement in footage fit and played sequence preference within an acceptable measured budget. If bounded assembly does not help, prioritize strong discovery and local pair audition. If candidate recall is the bottleneck, improve the relevant index or annotation before adding a reranker. RL/fine-tuning comes after a stable task and usable feedback, not before.

## Research that informs the proposal

- [DIRECT, April 2026 preprint](https://arxiv.org/html/2604.04875v1): global/local planning and source-window sequence search are closely related. It still prescribes durations in beats before window selection, and reports hallucinated unavailable footage and feature failures. Its latency and limited benchmark do not establish a ready replacement for this project.
- [CutClaw, March 2026 preprint](https://arxiv.org/html/2603.29664v1): contextual scene allocation followed by local trimming is useful precedent. Its audio-derived durations, onset-oriented synchronization metric and acknowledged latency limit what we can infer about action-led pacing.
- [Audeosynth, 2015](https://hub.hku.hk/bitstream/10722/215520/1/Content.pdf): distinguishes aligning motion inside a shot from aligning cuts to music. Its MIDI/speed-adjustment formulation is not a drop-in architecture here; the useful principle is that an audible accent need not imply a new shot.
- [GLANCE, April 2026 preprint](https://arxiv.org/html/2604.05076v1): separates global musical intent, multiple retrieval queries and local assembly with cross-section context. This supports testing the smaller separation proposed here, not adopting its task graph and negotiation machinery. Its refinement also treats missed beat synchronization as a possible defect; do not import that assumption as a universal editing rule. It is a preprint, not proof of quality in this library.
- [HIVE, EMNLP Industry 2025](https://aclanthology.org/2025.emnlp-industry.185/): narrative context is useful in editing long source footage. Its short-drama advertising domain does not establish arbitrary cross-film musical coherence.
- [Segment–Proposal–Ranking, 2025](https://arxiv.org/abs/2501.05072): supports separating indexed candidate retrieval from moment refinement. Its benchmark retrieval results are not montage-quality evidence or a reason to adopt its exact model stack.
- [Beyond Caption-Based Queries, CVPR 2026](https://davidpujol.github.io/beyond-vmr/): exposes a gap between detailed caption queries and realistic underspecified, multi-moment searches. Evaluate how people and planners actually search, rather than only replaying indexed captions as queries.

The proposed synthesis is an engineering hypothesis informed by these sources and the repository audit. None proves an optimal general-purpose AI editor. The clearest near-term opportunity is a source-grounded discovery-to-edit loop that produces good, adjustable sequences and makes its mistakes diagnosable.
