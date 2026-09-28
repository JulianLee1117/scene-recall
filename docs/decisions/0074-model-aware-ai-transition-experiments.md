# ADR-0074: Model-aware AI transition experiments

- Status: Accepted
- Date: 2026-09-16
- Extends: ADR-0071, ADR-0072 and ADR-0073
- Supersedes: ADR-0071's single-model and uniform 4–8-second generation restrictions

## Context

The user requested current AI transition options, useful customization and a
clearer Lab. The existing paid path could only express Seedance 2.5 requests.
Current official Runway documentation exposes two materially different
first/last-frame options through the same credential and task lifecycle:
MiniMax H3 Max and WAN 3. Treating their parameters as Seedance parameters would
produce invalid requests or misleading repeatability and cost information.

The existing screen presented generation, external handoff and file import
simultaneously. Switching the selected source render remounted the AI draft,
discarding custom writing, while import-history refresh could replace a user's
explicit result selection. These are concrete obstacles to comparing experiments.

## Decision

Use one bounded model registry behind the existing Runway adapter, editor queue,
server credential and generation ledger. Keep Seedance 2.5 as the omitted-model
default so existing requests retain their meaning. Add `h3_max` and `wan3`, with
model-specific prompt limits, durations, resolutions, payload fields and dated
price versions. Freeze the model in the reviewed quote, prepared input metadata,
submission receipt and assembled result. Never derive saved identity from the
currently selected UI model or a mutable global default.

Lab duration choices are 4–8 seconds for Seedance, 5–8 for H3 Max and 2–8 for
WAN 3. Longer vendor capabilities do not expand this short-bridge experiment.
Keep the reviewed 300-credit ceiling, no automatic paid retry, sanitized
receipts, owned originals, source verification, cancellation and uncertain-task
reconciliation already established by ADR-0071.

H3 Max receives first/last images, resolution, seed and explicit prompt expansion
mode. Expansion defaults to disabled; balanced/quality are optional advanced
choices and reduce repeatability. It has no explicit output-ratio or audio field
in this adapter. WAN 3 receives first/last images, an `auto_<resolution>` ratio
and audio disabled; it has no seed field. Do not mix WAN keyframes with video,
image or audio references. Record normalized conditioning-image dimensions
separately from requested output shape and probe actual returned media. Endpoint
conditioning is guidance, not proof of pixel-perfect joins or identity continuity.

Expose six cinematic and four experimental editable direction recipes. Keep
energy restrained by default, restrict travel controls to the selected technique,
and allow an optional visual anchor and observed source-motion notes. These are
prompt recipes, not native provider effect identifiers, trained local effects,
or quality endorsements. Selection changes leave custom prompt text intact until
explicit application. Generated output must still be judged at both source joins.

Present one vertical direction flow followed by a choice of **Generate here** or
**Bring a result**. Keep advanced prompt/provider details collapsed, preserve the
draft across method and source changes, bind quotes/results to the current source,
and require explicit file reassociation after changing the source pair. Inactive
panels stop polling and media playback. History refresh preserves a valid selected
result. Source-video and music uploads remain outside this endpoint-only path.

## Consequences

Two additional models become directly testable without a new service, credential
type, dependency, local model or storage cache. Quotes remain local; browsing and
changing controls create no job. There is no live generated-quality claim without
a configured account and reviewed paid experiment. Durable results and originals
continue to use the existing storage bounds and explicit trim/playback workflow.

Higgsfield effects and Cinema Studio, Luma Ray 3.2 keyframes, other vendor models,
and source-video conditioning are documented research or manual import options.
Their availability does not imply a direct adapter in this Lab. Automatic AI
editor selection, source-retimed AI assembly and wider provider integrations
retain their separate decision gates.

See [the dated research](../experiments/ai-transition-options-2026-09.md) for
primary sources, exact vendor contracts and the distinction between documented
capabilities and played quality.
