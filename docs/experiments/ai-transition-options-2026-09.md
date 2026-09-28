# Endpoint-guided AI transition options

Research checked **2026-09-16** against primary vendor documentation and the lab's
provider, generation, and handoff contracts. No paid generations, account changes,
source uploads, or model downloads were performed. This note records API support
and promising experiments; it does not rank visual quality or establish that a
technique is popular. The README and architecture contract describe shipped
behavior.

## Recommended additions: H3 Max and Wan 3 through Runway

Keep Seedance 2.5 as the existing default. Add two explicit model choices under the
same Runway credential and durable job lifecycle: **H3 Max** for a seeded,
endpoint-guided comparison with controllable prompt rewriting, and **Wan 3** for
shorter, lower-cost endpoint experiments. Neither choice needs another account.
They should remain explicit alternatives, without automatic model fallback or an
unreviewed second submission.

Runway added `h3_max` on **September 3, 2026** and `wan3` on **August 26, 2026**.
H3 Max accepts 5–15 seconds at 480p or 768p. Wan accepts 2–30 seconds at 480p,
720p, or 1080p. H3 Max is a distinct API model from `hailuo3`/MiniMax H3; do not
borrow the latter's 2K or multimodal-reference settings. Sources:
[Runway changelog](https://docs.dev.runwayml.com/api-details/api_changelog/),
[model catalog](https://docs.dev.runwayml.com/guides/models/).

### Exact endpoint request differences

Both use `POST /v1/image_to_video`, with `promptImage` containing two objects:
`{uri, position: "first"}` and `{uri, position: "last"}`.

- **H3 Max:** `model: "h3_max"`, `promptText` up to 6,000 characters,
  integer `duration`, `resolution: "480p" | "768p"`, optional uint32 `seed`,
  and `promptExpansionMode: "disabled" | "balanced" | "quality"`.
  There is **no `ratio` or `audio` field**.
- **Wan 3:** `model: "wan3"`, `promptText` up to 20,000 characters,
  integer `duration`, `ratio: "auto_480p" | "auto_720p" | "auto_1080p"`,
  and `audio: false` for this silent lab. There is **no separate resolution,
  seed, or prompt-expansion field**. Omit unsupported settings rather than
  sending placeholders.
- Successful submission requires `id` and `estimatedCost.credits`; terminal
  `cost.credits` is separate from the acceptance estimate. Neither model exposes
  a dedicated transition-style or negative-prompt parameter in this contract.

Source: the current
[Runway OpenAPI](https://docs.dev.runwayml.com/openapi.json), checked directly
rather than inferred from consumer-product screens.

H3's output aspect follows the input image, and each endpoint must be at least
256 pixels on both sides. Its default rewrite mode is `balanced`; the lab should
default to `disabled` so a saved prompt/seed experiment does not silently add a
rewrite. Wan defaults to `auto_1080p`, so always send the reviewed tier explicitly.
Wan's first/last keyframes **cannot be combined with reference images, videos, or
audio**. Its multimodal-reference mode therefore does not establish support for
both source clip tails plus both endpoint locks. Runway also warns that third-party
models may crop or resize differently. Normalized input geometry is controlled by
the lab; exact output framing still needs inspection. Source:
[input requirements](https://docs.dev.runwayml.com/assets/inputs/).

### Cost and duration boundaries

Runway credits cost **$0.01 each**. Published endpoint rates are:

- H3 Max: **5 / 8 credits per second** at 480p / 768p. A five-second test is
  **25 / 40 credits ($0.25 / $0.40)**.
- Wan 3: **5 / 10 / 20 credits per second** at 480p / 720p / 1080p. A two-second
  test is **10 / 20 / 40 credits ($0.10 / $0.20 / $0.40)**.
- Existing Seedance 2.5: **20 / 30 / 68 credits per second** with an
  **80-credit minimum**. This is a cost comparison, not a quality ranking.

No additional H3/Wan minimum or Wan audio surcharge is listed on the checked
pricing page. Preserve the provider's acceptance-cost check; published rates can
change. Source: [Runway pricing](https://docs.dev.runwayml.com/guides/pricing/).

Use narrower lab durations: H3 **5–8 seconds**, Wan **2–8 seconds**, existing
Seedance **4–8 seconds**, all subject to the explicit 300-credit ceiling. A model's
minimum duration is not a promise that it will produce a one-beat transition. Keep
the complete downloaded original; shortening or accelerating it must be an
explicit subsequent import/edit decision.

## Other current paths, and why they are not the next adapters

**Veo 3.1** is a real endpoint-guided alternative, including through Runway's
existing credential. Google's current official guide identifies
`veo-3.1-generate-001` and the fast variant with first and last images, 4/6/8-second
output, 720p/1080p, landscape/portrait, a seed, and a negative prompt. It would be a
useful later comparison, but H3 and Wan already add two distinct cost/duration
choices without expanding the first testing matrix further. Do not transfer this
support to every older Veo version. Source, updated **January 2, 2026**:
[Google first/last-frame guide](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/video/generate-videos-from-first-and-last-frames).

**Runway Gen-4.5 and Gen-4 Turbo** accept a starting image in the checked
image-to-video contract, not this two-endpoint bridge contract. Aleph 2 is a video
editing path with timestamped keyframes. It is a separate experiment in conditioning
an existing shot, not evidence that arbitrarily supplied A and B clip tails will
be joined with preserved velocity. The shared first/last adapter should not pretend
these paths are interchangeable. Sources:
[Runway API reference](https://docs.dev.runwayml.com/api/),
[Aleph 2 announcement](https://docs.dev.runwayml.com/api-details/api_changelog/).

**Luma Ray 3.2** is the strongest distinct future conditioning experiment. Its new
Agents API uses `model: "ray-3.2"`, `type: "video"` on
`https://agents.lumalabs.ai/v1/generations`, with `video.start_frame` and
`video.end_frame`. Alternatively it supports 1–64 keyframes with explicit indexes
on a 24fps grid. These two input modes are mutually exclusive. The legacy
start/end pair does not support ten seconds; the multi-keyframe path does. That
offers a possible way to describe approach and departure poses, but it does not
prove source-motion continuity. Seed control was not established by the checked
guide. Source: [Ray 3.2 generation](https://docs.agents.lumalabs.ai/guides/videos/generation/).

Luma lists five-second SDR output at **$0.06 / $0.15 / $0.30 / $1.20** for
360p / 540p / 720p / 1080p. A new integration needs its own credential and task
lifecycle. Legacy Dream Machine API models are scheduled to retire after
**October 4, 2026**; building a fresh Ray 2 adapter from still-indexed old examples
would immediately incur migration work. Sources:
[current pricing](https://docs.agents.lumalabs.ai/guides/pricing),
[migration deadline and contracts](https://docs.agents.lumalabs.ai/guides/videos/migration/).

**Higgsfield now has an official public API.** Its checked OpenAPI describes
`higgsfield-ai/dop/lite`, `standard`, and `turbo` with a required starting
`image_url`, optional `end_image_url`, seed, prompt enhancement, and up to two
motion IDs with strengths. The published DoP request does not establish a
duration/resolution selector. It also describes Veo 3.1 first/last routes. Actual
motion UUIDs and supported combinations must be discovered through its API;
website effect names are not request identifiers. Source:
[Higgsfield OpenAPI](https://docs.higgsfield.ai/docs/openapi.json).

Higgsfield's authenticated `/estimate/<modelpath>` is the account-specific cost
authority. Its documented cancellation/refund boundary is queued work, unlike a
universal promise to stop processing. Outputs are retained for at least seven
days, so owned downloads would still be required. No authenticated estimate was
requested here. Source:
[billing and retention](https://docs.higgsfield.ai/docs/concepts/billing-and-retention).

**Kling:** current consumer examples are not enough to establish a direct API
adapter. The official direct documentation was inaccessible to this research
session, and the checked Higgsfield schema lists older Kling routes. No current
direct Kling 3 endpoint shape or price is claimed here. External generation and
the existing explicit import workflow remain available without presenting them
as verified direct integrations.

## Four additional creative directions

These are **prompt directions using the same first/last model input**, not native
Runway effects, numerical geometry controls, or guarantees. The official
[Higgsfield Effects collection](https://higgsfield.ai/collection/effects) currently
lists Portal, Point Cloud, Melt Transition, Smoke Transition, and Wireframe among
its examples. Catalog presence establishes vendor demonstrations, not measured
adoption or a quality result in our footage.

- **Reflection passage — cinematic:** approach a reflective surface in A, let its
  reflected world occupy the frame, and emerge into B while keeping one camera
  trajectory. Tune the surface, direction, movement pace, and destination anchor
  in the prompt. A creator's own example describes a sunglasses-reflection
  passage: [“It's me” by @radiatingmoon1112](https://higgsfield.ai/contests/make-your-action-scene/submissions/56436f73-8698-41e6-916c-5fd23f2324b0).
  Test reflection plausibility and the exit camera orientation; do not promise a
  literal physically reconstructed reflection.
- **Aperture portal — experimental:** move through a physical opening or a
  deliberately stylized aperture. Specify what fills the frame at the crossing
  and what should first become visible in B. This is most useful when the source
  composition already offers a doorway, ring, window, or dark occluder. Check for
  an unrequested hard cut, rubbery edges, and a destination that appears too early.
- **Material dissolve — experimental:** choose one material behavior, such as
  liquid, smoke, paper, or particles, that consumes A and reveals B. Customize
  material, sweep direction, and how long coverage lasts. Higgsfield's own
  tutorial uses liquid-material environment transformations:
  [Marketing Studio prompts](https://higgsfield.ai/blog/marketing-studio-video-1).
  Avoid stacking incompatible materials; inspect faces and hands through the
  conversion and require a clean settled final frame.
- **Point-cloud scan — experimental:** a scan plane temporarily resolves A into
  points or a sparse mesh and reconstructs B. Prompt scan direction, apparent
  density, color, and the stable subject anchor. These words influence generated
  appearance; they do not expose a real point-cloud reconstruction or calibrated
  3D camera. Check that the effect clears completely and does not leave a frozen
  or noisy arrival frame.

The useful prompting pattern is a concrete start, one camera path, a motivated
visual crossing, and a settled arrival. Higgsfield's **August 13, 2026**
[Seedance prompting guide](https://higgsfield.ai/blog/seedance-2-5-prompting-guide)
emphasizes specific staging, camera position, light, and physical action. Many
vendor examples contain multiple shots; a bridge prompt should explicitly ask
for one continuous shot when that is the intended result. The **August 17, 2026**
[realism guide](https://higgsfield.ai/blog/ai-video-look-real-2026) provides useful
light and inertia vocabulary. Neither guide verifies execution by a particular
Runway model.

## Integration and quality gates

Keep the visible workflow small: choose a direction, describe motion, review
endpoints, then choose model/duration/quality and review cost. Preserve custom
prompt edits until the user explicitly applies a template. Hide seed for Wan;
show H3 prompt rewriting as an advanced control with `disabled` selected. Do not
add sliders whose numbers are only adjectives inside a generated prompt without
making that distinction clear.

Reuse the existing durable generation boundaries: freeze model identity, wire
settings, price version, source/endpoint hashes, framing, and assembly version;
require a reviewed quote and explicit ceiling; submit once; persist the task
identity before further validation; retain uncertain receipts; never automatically
retry a possibly accepted submission. Keep imported provenance separate from a
verified provider task. Historical outputs must not be relabeled when the default
model changes. Retimed parents remain blocked until conditioning and assembly
share a reviewed temporal mapping; the actionable workaround is **Speed off**.

After credentials and a budget are available, use a small matched set of source
pairs and prompts. Inspect both joins at normal speed and frame by frame: endpoint
framing, identity, the first/last half-second's motion direction and speed, hidden
cuts, exposure/color jumps, freeze frames, and unwanted scene changes. Record
actual resolution, duration, charge, and elapsed time. Do not equate an accepted
seed with cross-model reproducibility or endpoint conditioning with exact pixels.
Only promote a direction/model combination after it improves a concrete pair;
keep the original download and the local A → bridge → B preview available for
comparison and recovery.

## Implementation review

The Lab now exposes Seedance 2.5, H3 Max and Wan 3 with the narrower durations
above, plus ten direction recipes. The AI view separates **Generate here** from
**Bring a result**, keeps optional details collapsed, and preserves custom
writing across source/model/method changes. Experimental techniques retain a
visible label after the select closes. Import files require explicit reassociation
with a changed source render. Failed retries after an uncertain submission retain
the original reconciliation identity, even when a later response is a validation
error; recovery identifies requests belonging to an earlier source render.

Validation on 2026-09-16:

- **2,943 backend tests passed**, 11 skipped, three existing deprecation warnings.
  Focused provider/bridge/storage coverage passed 112 tests. New coverage includes
  all three models through simulated preparation, submission, receipts, original
  download and local A → bridge → B assembly, a long Wan prompt, legacy omitted
  model identities and incompatible model/price rejection.
- **438 frontend tests passed**, including 33 focused AI/direction/import checks.
  TypeScript and the production build passed.
- An independent offline comparison checked 24 model/resolution/aspect request
  combinations against the official OpenAPI. This verifies request construction,
  not account access or generated appearance.
- Actual local API/browser quotes returned 120 credits for Seedance 4s/720p,
  40 for H3 5s/768p and 20 for Wan 2s/720p. The configured credential flag remains
  false and submission remains disabled. No paid call or source upload occurred.
- Browser review confirmed H3 defaults and optional expansion, Wan's absence of
  seed controls, quote invalidation, explicit prompt application, custom text
  retained across source selection, one visible creation method, a single
  Speed-off prerequisite, and no page overflow at a 390px viewport. The viewport
  was restored and the Lab left open with a local Wan quote.

Logs and local quote receipts are under `.tmp/transition-ai-options/`. The
account-dependent image quality, endpoint continuity and provider latency remain
unmeasured; a ready adapter is not a visual-quality endorsement.
