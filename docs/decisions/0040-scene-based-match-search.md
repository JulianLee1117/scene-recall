# ADR-0040: Scene-based Match search with exact-pair evidence

- Status: Superseded by ADR-0099
- Date: 2026-09-13
- Extends: ADR-0038 through an explicit search action
- Superseded by: ADR-0067 for entry from ordinary search results and the main source player only

## Context

The user wants to search for possible next clips from an existing scene, with
results explaining the particular connection, including position alone. A Lab
project is unnecessary for discovery. Automatic currently refines each channel
before combining shot ranks, taking 29 seconds to first playback in one probe.
Different channels can favor different cut points inside the same shot. The
whole-reference-film exclusion also prevents useful same-film discovery.

## Decision

Expose an explicit Match cuts action from search results and the source player,
opening `/match`. Ordinary descriptive search and its retrieval path stay intact.
The route accepts an indexed reference and selected source time, displays its
prepared coverage, and returns candidate rows with a primary measured cue and
an actual A-to-B audition. It creates durable search jobs without a project.

The independent `pipeline.matching.search` engine consumes a typed reference,
focus, timing and eligibility constraints. It proposes a shared coarse shortlist,
deduplicates work, refines bounded incoming windows, and evaluates position,
shape, subject and camera cues on each identical final pair of native timestamps.
Position can qualify without shape agreement; it must be labeled as position.
Explanations are deterministic descriptions of supporting measurements, never
generated narrative or an uncalibrated overall percentage. Unknown evidence
remains explicit. Original framing and playback speed are the initial contract.

Allow other non-overlapping shots from the reference film when requested. Source
handles and incoming-duration eligibility are caller constraints, independent
of timeline placement. A future editor adapter must preserve its timeline,
locks, musical clock and required clip duration; an audition is not permission
to replace an edit or copy its short preview windows onto a timeline.

Reuse the existing singleton worker and nullable-project durable job ledger.
Freeze validated requests and compatible profile identities server-side. Search
and preview jobs can only publish results, never project revisions. Preview
requests resolve immutable server candidates and inherit parent cancellation.
Existing source identity and decoded-boundary preview checks remain mandatory.
Requests, active deduplication and media URLs have their own `/matching` boundary.

The first shared-shortlist probe improved cold first playback from about 29 to
26 seconds, still missing the 15-second target. Therefore expose an optional
candidate callback from the engine. The worker previews the first supported
pair while remaining refinement continues, then reuses it only if the final
pair is identical and prepares any missing final top-three previews. This adds
at most one encode, never another model/refinement window. Final ranking may
reorder early candidates. Cached playback also requires successful exact-PTS
boundary proof, the saved MP4 SHA-256 and current source identities.

Keep the existing 200-shot/80-window experiment limits and independently prepared
profiles. The explicit entry does not imply full-library coverage or automatic
main-search routing. No new model, ANN service, broad backfill, speed changes,
hosted calls or automatic editor placement is introduced. Preserve the v3 Lab
path so prior jobs and diagnostic comparisons remain reproducible.

## Verification and expansion

Test independent position evidence, common exact cut points, source eligibility,
bounded work, profile changes, projectless persistence/cancellation and verified
previews. Compare real diagnostic results and first-playable times with v3;
correctness tests do not establish editorial quality. Validate source selection,
cue display and previews in the browser. Expand prepared coverage or connect an
editor only after measured candidate usefulness and explicit placement contracts
justify that step. The reusable engine is the foundation for those integrations.

## Research check

The [Netflix match-cut research](https://openaccess.thecvf.com/content/WACV2023/papers/Chen_Match_Cutting_Finding_Cuts_With_Smooth_Visual_Transitions_WACV_2023_paper.pdf)
separates framing and motion retrieval in a modular candidate-finding system.
That supports independent cues and a reference-based discovery interface, though
it does not validate our local implementation's quality. Newer
[MatchDiffusion (ICCV 2025)](https://www.openaccess.thecvf.com/content/ICCV2025/papers/Pardo_MatchDiffusion_Training-free_Generation_of_Match-Cuts_ICCV_2025_paper.pdf)
generates structurally related video pairs; it addresses a different task from
finding editable cuts in preserved library footage.

[SAM 3.1](https://github.com/facebookresearch/sam3/blob/main/RELEASE_SAM3p1.md)
and [WAFT](https://arxiv.org/abs/2506.21526) remain relevant tracking/flow
challengers. Their published segmentation or flow results do not establish
better played match cuts or latency on this hardware. Keep those model changes
independent of the product/API change and require a bounded comparison before
adopting them. Sources checked 2026-09-13.
