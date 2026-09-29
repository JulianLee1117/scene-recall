# Architecture decision log

This directory records why material Scene Recall architecture choices were
accepted. It is historical rationale, not a second specification.

- [`README.md`](../../README.md) documents runnable behavior and commands.
- [`docs/search-architecture.md`](../search-architecture.md) is the current
  architecture contract.
- ADRs explain decisions but never override that contract.

## When to add an ADR

Add one only for a cross-cutting, expensive-to-reverse choice involving:

- durable evidence, model lineage, storage, migration, or backfill boundaries;
- retrieval activation, fallback, fusion, or evidence semantics;
- privacy, deployment, hosted-provider, or material cost boundaries;
- activation of a system currently marked deferred.

Do not add ADRs for normal bug fixes, refactors, tests, styling, dependencies,
or ordinary interface changes. Git records implementation details. Do not
commit speculative ADRs; add one when the choice is accepted.

## Maintenance

1. Use the next four-digit number and a short kebab-case filename.
2. Use `Accepted` or `Superseded` as the status.
3. Update the architecture contract in the same work. Update the operational
   README only if commands or runnable behavior change.
4. Do not rewrite an accepted decision. To reverse it, add a new ADR, mark the
   old record `Superseded`, and link both records.
5. Add every ADR to the index below.

## Template

```markdown
# ADR-NNNN: Short decision title

- Status: Accepted
- Date: YYYY-MM-DD
- Supersedes: None
- Superseded by: None

## Context

What forced the choice and which constraints mattered.

## Decision

The accepted boundary or behavior.

## Consequences

- What becomes easier or safer.
- What becomes more expensive or remains limited.
```

## Index

| ADR | Status | Decision |
| --- | --- | --- |
| [0001](0001-durable-evidence-replaceable-derivations.md) | Accepted | Preserve source evidence; version and backfill derivations |
| [0002](0002-independent-semantic-text-profile.md) | Accepted | Keep text views independent and activate only complete profiles |
| [0003](0003-composable-composition-and-text-search.md) | Accepted | Rerank a mandatory composition shortlist with text |
| [0004](0004-local-first-and-conditional-advanced-retrieval.md) | Accepted | Stay local-first and require demonstrated need for advanced retrieval |
| [0005](0005-cross-film-composition-candidates.md) | Accepted | Exclude the source film before composition candidate generation |
| [0006](0006-separate-durable-user-state.md) | Accepted | Keep bookmarks outside replaceable search indexes |
| [0007](0007-typed-modular-search-recipes.md) | Accepted | Compose explicit search facets over current evidence |
| [0008](0008-grounded-match-cut-and-temporal-motion-boundaries.md) | Accepted | Separate grounded still matching, exact-frame refinement, and temporal motion |
| [0009](0009-complete-framing-spatial-cache.md) | Accepted | Cache production Framing grids only under complete profile activation |
| [0010](0010-dedicated-mood-semantic-view.md) | Accepted | Isolate Mood to stored feeling and energy evidence |
| [0011](0011-expose-resolved-source-inputs.md) | Accepted | Explain exact dragged-source inputs without inventing visual text |
| [0012](0012-query-bound-uploaded-image-recipes.md) | Superseded | Use one uploaded still as a bounded broad-visual recipe signal |
| [0013](0013-broad-query-evidence-and-candidate-breadth.md) | Accepted | Match broad-query lexical intent and expose passive cross-film candidates |
| [0014](0014-preserve-external-subtitle-evidence.md) | Accepted | Preserve selected external subtitle evidence before dialogue derivation |
| [0015](0015-fail-closed-on-degenerate-whisper-output.md) | Accepted | Discard structurally degenerate Whisper rows while preserving visual search |
| [0016](0016-trust-explicit-english-primary-audio-tags.md) | Accepted | Prevent foreign-language cold opens from overriding explicit English audio metadata |
| [0017](0017-progressive-authoritative-result-windows.md) | Accepted | Deepen one bounded backend-ranked result stream on demand |
| [0018](0018-category-bound-uploaded-image-facets.md) | Accepted | Bind uploaded stills to honest Look or Framing retrieval |
| [0019](0019-soft-temporal-result-spread.md) | Accepted | Defer nearby ordinary results without deleting them |
| [0020](0020-bounded-discovery-rank-and-visual-reserve.md) | Accepted | Use bounded broad-search repeat rank and selective visual reserve |
| [0021](0021-unified-open-discovery-repeat-rank.md) | Accepted | Use one bounded repeat rank for open non-image-gated discovery |
| [0022](0022-explicit-external-subtitle-review.md) | Superseded | Require explicit review before using an uncertain external subtitle |
| [0023](0023-operator-archive-imported-releases.md) | Accepted | Allow intact imported releases to move into operator-managed evidence storage |
| [0024](0024-source-backed-lab-and-music-sketch.md) | Accepted | Add durable Lab projects and bounded audio-to-source edit planning |
| [0025](0025-durable-standalone-job-worker.md) | Accepted | Execute durable ingestion and Lab jobs in a standalone local worker |
| [0026](0026-region-aware-visual-rhymes-research.md) | Accepted | Evaluate within-shot instants and whole-picture region alignment in shadow |
| [0027](0027-bounded-lab-match-finder.md) | Accepted | Admit bounded image and movement matching in Visual Rhymes before production promotion |
| [0028](0028-explicit-music-audio-provider-and-progress.md) | Accepted | Select the music audio provider explicitly, persist progress and apply passage-relative fade-in |
| [0029](0029-authoritative-music-timeline-and-gap-filling.md) | Accepted | Make music cuts authoritative and fill or replace explicit slots with whole-sequence context |
| [0030](0030-song-specific-moments-and-clip-directions.md) | Accepted | Plan song-specific moments, keep directions per clip, and make timing regeneration explicit |
| [0031](0031-explicit-timeline-direction-planning.md) | Accepted | Regenerate coordinated clip directions independently of listening, retrieval and manual cut edits |
| [0032](0032-shared-musical-evidence-and-editable-planning.md) | Accepted | Share scoped musical evidence, expose editable lyric/sequence intent, and review exact source footage |
| [0033](0033-search-aware-editor-and-atomic-generation.md) | Accepted | Share executable search capabilities, preserve query evidence and generate an edit in one atomic job |
| [0034](0034-explicit-shot-search-and-selection.md) | Accepted | Search a selected shot without placing footage, then let the user choose a result |
| [0035](0035-local-timing-and-editor-lifecycle.md) | Accepted | Prepare local timing before creative generation, browse scenes in the editor, and manage project deletion and exit |
| [0036](0036-next-scene-proposals-and-pair-audition.md) | Accepted | Audition bounded next-scene proposals with original music, explicit application and optional sampled-frame inspection |
| [0037](0037-manual-moment-and-crop-in-pair-preview.md) | Accepted | Adjust a suggested scene's source moment and framing in the pair preview with exact crop proof |
| [0038](0038-played-match-cuts-and-tracked-subjects.md) | Accepted | Simplify played match cuts, track subjects, and compare boundary timing with explicit effectiveness gates |
| [0039](0039-song-informed-first-edit-timing.md) | Accepted | Let untouched rhythm starters acquire song-informed first-edit timing while preserving manual work |
| [0040](0040-scene-based-match-search.md) | Accepted; entry partially superseded by 0067 | Discover next clips from a scene with projectless jobs and independent cues measured at one exact cut |
| [0041](0041-simple-editor-and-source-aware-first-cuts.md) | Accepted | Simplify the editor, expose real progress and choose bounded first-edit cuts with available footage and explicit abstention |
| [0042](0042-explicit-whole-edit-regeneration.md) | Accepted | Rebuild the entire music edit explicitly from current settings while retaining the previous revision |
| [0043](0043-song-meaning-edit-density-and-fresh-footage.md) | Accepted | Separate heard meaning from atmosphere, enforce whole-edit pace density and prevent automatic footage reuse |
| [0044](0044-scoped-scene-selection-and-compact-evidence.md) | Accepted | Bind scene choices and cuts to each shot, compact repeated model evidence and preserve failed-job offer diagnostics |
| [0045](0045-bounded-full-length-music-video.md) | Accepted | Support ten-minute music videos with bounded analysis and generation sections inside one atomic edit |
| [0046](0046-distinctive-match-evidence-and-reference-selection.md) | Accepted | Correct background reference selection, require outline evidence and bound common-position ranking |
| [0047](0047-managed-film-acquisition.md) | Accepted | Manage download, validation, canonical import and intact evidence archival with explicit client ownership |
| [0048](0048-library-recall-and-foreground-person-arrangement.md) | Accepted | Recall existing library frames and verify complete foreground-person arrangements in bounded Match search |
| [0049](0049-stop-managed-torrents-after-download.md) | Accepted | Stop completed managed downloads through per-torrent client limits while preserving verified import and intact evidence archival |
| [0050](0050-rapid-pacing-and-inspectable-edit-evidence.md) | Accepted | Add bounded Rapid pacing and expose recorded shot intentions, neighboring footage and source evidence |
| [0051](0051-shared-lab-navigation-and-unsaved-drafts.md) | Accepted | Share Lab entry and return navigation, keep untouched editors unsaved and create complete projects on first save |
| [0052](0052-short-shot-temporal-evidence-and-targeted-backfill.md) | Accepted | Add versioned temporal sampling for short shots and bounded background backfill without repeating unrelated ingestion |
| [0053](0053-neutral-semantic-query-instruction.md) | Accepted | Keep semantic query instructions neutral and reconcile activation without reembedding unchanged document evidence |
| [0054](0054-caption-backed-visual-junk-filtering.md) | Accepted | Classify visual junk from caption evidence without letting dialogue suppress ordinary scenes |
| [0055](0055-idle-worker-development-reload.md) | Accepted | Refresh development workers between jobs without interrupting active work or replaying hosted requests |
| [0056](0056-automated-external-subtitle-checks.md) | Accepted | Validate external subtitles locally and choose embedded fallback conservatively from English metadata |
| [0057](0057-conservative-release-layout-selection.md) | Accepted | Select one clearly associated complete film and handle release layouts without guessing or extracting archives |
| [0058](0058-joint-source-and-cut-feasibility.md) | Accepted | Resolve provisional cut preferences and selected source durations together within existing timing offers |
| [0059](0059-independent-editor-and-library-workers.md) | Accepted | Separate CPU editor work from GPU library jobs with one launcher, scoped ownership and visible graceful controls |
| [0060](0060-music-led-whole-passage-timing.md) | Accepted | Plan musical timing across the passage before batching footage work, using soft pace preferences and reusable listening evidence |
| [0061](0061-targeted-footage-inspection-and-frozen-comparisons.md) | Accepted | Inspect at most two flagged montage positions using reusable bounded source observations and compare frozen timing/selection evidence |
| [0062](0062-source-preserving-browser-audio-playback.md) | Accepted | Prepare optional browser audio repair without changing source films, evidence timelines or active byte-range representations |
| [0063](0063-idle-editor-search-warmup.md) | Accepted | Prepare editor search models while idle and omit unused source-reference reads from text searches |
| [0064](0064-scoped-editor-direction-and-two-tab-workspace.md) | Accepted | Keep canonical global/range direction separate from listening in one AI direction/Edit workspace |
| [0066](0066-source-context-pilot.md) | Accepted | Build independently versioned source context for bounded editorial comparisons, preserving retrieval and trim authority |
| [0065](0065-private-discovery-and-flexible-assembly.md) | Accepted | Compare fixed slots, joint assembly and bounded discovery expansion using private frozen evidence |
| [0067](0067-match-cuts-lab-only-entry.md) | Accepted | Keep experimental Match Cuts in its independent Lab workspace without actions in ordinary search or the main scene player |
| [0068](0068-lab-artifact-lifecycle.md) | Accepted | Clean owned job artifacts on project deletion, retry durably and reclaim disposable render/audio leftovers in the existing worker |
| [0069](0069-tiered-playback-and-idle-database-maintenance.md) | Accepted | Separate full-length playback storage and prune database history only under idle reader and writer guards |
| [0070](0070-projectless-transition-recipes.md) | Accepted | Compare durable local transition recipes in a projectless Lab, with manual endpoint-frame handoff for external AI experiments |
| [0071](0071-rgb-transition-lab-and-durable-bridge-imports.md) | Accepted | Expand bounded RGB recipes, music audition, durable imports and explicitly quoted Runway bridge jobs; keep editor promotion gated |
| [0072](0072-native-time-speed-ramps-and-interpolation.md) | Accepted | Add source-preserving local speed ramps, optional within-shot interpolation and marked experiments; retain the separate AI temporal and editor-promotion gates |
| [0073](0073-compact-transition-edit-assets-and-playback.md) | Accepted | Reuse verified local renders, expose retained bytes, bound scratch and synchronize playback; add explicit compact 1080p edit assets |
| [0074](0074-model-aware-ai-transition-experiments.md) | Accepted | Add model-specific H3 Max and WAN 3 endpoint bridges, preserved custom direction drafts and a focused generate/import flow |
| [0075](0075-smooth-transition-motion-and-direct-manipulation.md) | Accepted | Smooth frame-timed whip/zoom and native-time endpoint sampling; add direct trim/framing and distinct local/AI workflows |
| [0076](0076-camera-whip-and-program-monitor-timeline.md) | Accepted | Version whole-frame Camera whip and use one program/source monitor with a continuous pair timeline and explicit saved-preview state |
| [0077](0077-independent-source-dialogue-clips.md) | Accepted | Mix independent original-source dialogue clips on the song clock with durable levels, fades and music ducking |
| [0078](0078-shared-voice-focus-audio-and-overlapping-dialogue.md) | Accepted | Share source-window PCM across audition/export, focus declared surround centers, and allow overlapping dialogue with editable duck timings |
| [0079](0079-validate-extracted-embedded-dialogue.md) | Accepted | Validate extracted embedded subtitle content, preserve rejected evidence and record cacheable audio-transcription fallback lineage |
| [0080](0080-cancelled-acquisition-staging-cleanup.md) | Accepted | Clean explicitly cancelled managed staging after worker teardown and downloader detachment, preserving imported library sources |
| [0081](0081-read-only-project-guide.md) | Accepted | Add an independent expandable technical guide with allowlisted loaded settings and no processing side effects |
| [0082](0082-shared-search-foundation-and-composition-challenger.md) | Accepted | Share snapshots and lookups, reuse source-hashed partial caches, and gate independent compact composition retrieval |
| [0083](0083-complete-filter-before-scalar-read-limit.md) | Accepted | Apply the complete scalar filter before bounded reads, retaining indexed and unindexed matches |
| [0084](0084-confirmed-movie-scope-in-search.md) | Accepted | Suggest explicit movie scope in the search input and browse selected films without a fabricated query |
| [0085](0085-inline-confirmed-movie-mentions.md) | Accepted | Keep confirmed movie identity inline using native text and validated ranges |
| [0086](0086-frozen-search-intent-comparison.md) | Superseded by 0094 | Compare bounded hosted intent and evidence judgments on frozen search candidates |
| [0087](0087-conservative-whole-frame-blank-filter.md) | Accepted | Require whole-frame blank evidence rather than suppressing meaningful dark screens or imagery |
| [0088](0088-bounded-jev-follow-up-probes.md) | Superseded by 0094 | Separate frozen-evidence constraint judgments from query-only category interpretation with bounded hosted receipts |
| [0089](0089-personal-search-assistance-trial.md) | Superseded by 0090 | Historical personal category-assistance trial |
| [0090](0090-retire-personal-search-trial.md) | Accepted | Remove the interactive trial and distinguish candidate coverage from ranking before another search experiment |
| [0091](0091-intent-guided-retrieval-comparison.md) | Superseded by 0094 | Compare ordinary, fixed expansion and Jev-guided evidence retrieval with frozen playable result lists |
| [0092](0092-bounded-framing-representation-pilot.md) | Accepted for evaluation | Hold the optional bulk batch and compare frozen framing representations independently of Jev |
| [0093](0093-evidence-v2.md) | Accepted | Versioned per-film evidence artifacts, open metadata with world knowledge, measured-over-guessed facts and compiled search tables |
| [0094](0094-search-v2.md) | Accepted | Search v2: evidence views fused by rank, quote channel, bounded priors and presets, scene cards, resident vectors, cross-encoder rerank; retire the intent experiments |
| [0095](0095-serve-last-complete-generation.md) | Accepted | Compile from the newest available profile while a new one backfills; API search keeps its last complete snapshot during publication |
| [0096](0096-editor-harness-v2.md) | Accepted (opt-in) | Editor harness v2: measured music map, concept acts, evidence pools, beat-lattice assembly and a sequence review; OTIO export |
| [0097](0097-relevance-is-one-score.md) | Accepted | One relevance score through ordering: log-odds judge verdicts on the shortlist plus every retriever's best matches, bounded signal and prior factors, named films exempt from film diversity |
| [0098](0098-focus-span.md) | Accepted | Show each shot's action: pictures from keyframe similarity, a focus span around the peak, hero frames and hover previews inside it, editor windows bounded by it |
| [0099](0099-moment-match-cuts.md) | Accepted | Moment-level match cuts: a library-wide 4 fps moments pass (masks, keypoints, light, edges), a calibrated pair scorer with crops, a one-screen Lab workspace with in-browser audition and chains, and measured match cuts in editor transitions |
| [0101](0101-fold-look-alikes-and-measure-ordinary-moments.md) | Accepted | Fold same-film look-alike shots into their card instead of dropping them; a blind ordinary-moment eval; evals report runs that lost the judge or semantic text |
