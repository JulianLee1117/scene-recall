# Transition techniques and next lab experiments

Research checked September 16, 2026. The original audit and proposals below
informed the implementation accepted in ADR-0071. Source references establish
documented techniques and provider contracts, not a measured ranking of trends
or model quality. No hosted generations were made for this research.

The sections are chronological. For the current native-time ramps, interpolation
and six AI starters, see the native-time follow-up below and
[motion-led experiments](transition-motion-research-2026-09.md).

## Implementation status — September 16, 2026

Implemented: continuous combined-source whip blur, anchored crash zoom, linear
Rec.709 color/light compositing, highlight bloom, organic burn, localized lens
flare, double-hit shutter and afterimage flashes, plus the existing reveals and
baselines. Flash peak/attack/decay are separate from picture-change timing.
Per-clip Fit/Fill/position/zoom, shared seam-relative comparison, frame/BPM
helpers, three-duration sweeps, local music audition and saved notes/favorites
are available. The minimum local effect is three frames; two endpoint frames
alone leave no interior image in which an effect can appear.

Returned bridges now have bounded durable imports, explicit trim/retime,
independent originals/receipts and assembled A → bridge → B auditions. Four
prompt starting points support manual external generation. Runway/Seedance
contracts, price quotes, explicit generation jobs and the bounded adapter are
tested offline. No key was available for live validation or paid quality tests.

Still deferred: optical-flow/source speed ramps, tracked subject/depth mattes,
datamosh, GPU preview/export parity, live provider validation and automatic editor
selection. These should follow observed failures and played comparison evidence,
not be inferred from the names of implemented effects.

The bounded local performance run used nine default recipes on the same night
city/road pair at 854×480. Compositing took 2.0–3.6 seconds after source preparation.
A separate maximum two-second/720p/5× zoom stress run took 23.19 seconds after
optimization (44.84 seconds before). These are local measurements, not an
end-to-end latency or visual-quality benchmark. Receipts and frame strips are in
`.tmp/transition-v3-review/`; the original audit below is historical, so its
listed defects are not a description of the later RGB-v3 implementation or the
current RGB-v4 polish pass.

Retained local comparison cases:

- [Neon shutter double hit](http://localhost:3000/lab/transitions?render=b54a2262-7130-4241-8151-ba7acfc0de18):
  Kill Bill Vol 1 → Fallen Angels, 12 frames (quarter note at 150 BPM), with a
  saved browser recipe and tuning note. Compare the lens sweep, highlight
  duration sweep and hard cut on this same pair.
- [Daylight highlight kiss](http://localhost:3000/lab/transitions?render=b23493c5-5621-4ef1-ba04-feabcf4c0a71):
  Her → The Master, eight frames, amount 0.28, light peak before picture change.
  Fill framing keeps the faces readable; the anchored zoom offers more energy
  but cannot remove the original difference in camera angles.
- [Luma face-pair counterexample](http://localhost:3000/lab/transitions?render=ea09b6e2-87fc-4540-b467-a6baba2d8c90):
  the midpoint mixes different faces. Retained as a negative case, not a preferred
  face transition. Normal-speed playback and midpoint inspection support the
  narrower conclusion that recipe choice must depend on the actual footage.
- The 11-frame night highlight render owns a saved import named
  `local-endpoint-crossfade-QA.mp4`. This is explicitly a local fixture, not AI
  generation. Its 0.2–1.0s trim plays in 0.4s between full A and B; the 175-frame
  audition survived reload and the downloaded original's SHA-256 matched input.

Final validation after polish: 2,837 backend tests passed, 11 skipped, using the
short Windows temporary root `.tmp/tpfull`; the focused transition suite passed
170 tests. All 400 frontend tests, TypeScript and the production build passed.
The hosted workflow was tested with simulated provider responses, including
duplicate submissions, cancellation and original recovery after assembly failure.
Live browser checks covered render/sweep, shared playback, local music cue,
favorites/notes/preset persistence, imported bridge playback/restoration,
generation quotes and a 390-pixel responsive viewport. Saved artifacts also
survived an API restart, with the imported original's hash unchanged. These checks
do not establish provider quality or guarantee editorial usefulness on other pairs.

## Procedural polish review — September 16, 2026

The follow-up review compares the same night city/road pair and daylight face
pair under RGB-v3 and RGB-v4. Five families were rendered on both pairs: whip,
burn, lens sweep, shutter double hit and afterimage flash. Before/after MP4s,
frame strips, requests and timing receipts are retained in
`.tmp/transition-polish/`. These are controlled local examples, not a universal
taste ranking.

The original burn lifted too much of the image into a broad yellow haze. Its
replacement confines illumination to an irregular edge and short tail, keeping
more scene contrast. An intermediate narrow burn exposed regularly spaced ribs
from the old sine-wave texture; that revision was rejected in favor of seeded,
multiscale smooth random fields. The texture stays fixed through time to avoid
unrelated flicker. The lens streak is softer and its default picture crossover
shorter; it still suits the neon pair better than close-up faces. Afterimage
defaults leave a quieter impression, but a double-identity effect remains an
intentional style choice. The whip did not need a cosmetic rewrite.

Durable RGB-v4 examples include the
[eight-frame night burn](http://localhost:3000/lab/transitions?render=b46a1e59-a42a-4d4f-ab07-ea7850bf86e8),
[twelve-frame night burn](http://localhost:3000/lab/transitions?render=85a67524-0b55-4d54-bb0f-9311e2667668),
[daylight lens sweep](http://localhost:3000/lab/transitions?render=a9ee0a16-5a09-4d7a-8df0-187a0e08676e)
and [daylight afterimage](http://localhost:3000/lab/transitions?render=d0cb2c50-4b4f-47f4-acc3-56554f717021).
The night sweep retained its prior preview, selected the eight-frame center,
and kept six- and ten-frame alternatives. A second hard-cut comparison reused
the existing baseline. Source-boundary changes correctly marked the preview as
different, and explicit restoration recovered the reviewed request.

One native endpoint check caught compressed-video seek overshoot: the browser
showed the following shot before the requested outgoing boundary even though the
retained source frame was correct. Source-card inspection now uses that retained
frame only for a matching pair, framing and aspect; scrubbing exposes approximate
video seeking again. Source timestamps were not shifted. Native PTS/DTS evidence
and comparison stills are in `.tmp/transition-polish/timestamp-audit.json`.
Playback starts at the source-window beginning after endpoint inspection or an
automatic stop; deliberate scrubbing retains its chosen playback position.

External overlays are optional for this pass. Procedural motion, luminance
reveals and shaped light are useful without importing an asset pack. Scanned
film and photographed light leaks could supply a particular physical texture
later; the current burn is a procedural light effect, not a claim to reproduce a
specific film scan. No personal asset folders were searched or imported.

## Native-time speed follow-up — September 16, 2026

This follows the RGB-v4 polish review above and supersedes the earlier status
line deferring source speed ramps and optical flow. That review, its source
pairs and its saved renders remain historical evidence; they are not rewritten
as tests of the newer temporal implementation.

ADR-0072 admits shared Off/Rush/Slow hit/Pulse source-speed controls and optional
within-shot frame blending or FFmpeg CPU motion compensation. Fixed source
windows remain authoritative. A 2048-interval reciprocal-speed integral derives
each output duration; overlaps must fit both retimed clips. Defocus bridge and
Prism push are marked local experiments on flat images, not reconstructed 3D
camera motion.

The final profile is `transitions-rgb-v6`. An intermediate RGB-v5 implementation
sampled across an endpoint-inclusive output interval, which could drop or repeat
interior frames at nominal 1×. RGB-v6 uses actual output times `i / 30`, with
explicit first/last native-frame locks as boundary exceptions. RGB-v5 test
artifacts stay immutable and available for comparison. Nonzero container origins
are handled by trimming in raw PTS and translating the native intermediate to
source-player seconds; the original source anchors and cadence do not change.

The renderer bounds each active source preparation to four minutes, timestamp
lists to 4096 frames and monitored native/flow scratch to 1 GiB. It detects rather
than accepts frame-budget truncation. Flow uses 60–120 fps within the ramp and up
to 0.25 seconds of adjacent context per side, always inside the selected window.
These are implementation bounds, not a measured quality ranking or a promise of
end-to-end latency. The ordinary Speed-off preparation remains available.

**Compare hard cut · same speed** isolates the visual effect; **Compare without
speed** retains the saved effect and duration to isolate retiming. Requested
edge/peak speed and the time-map speed at the composited picture change are
reported separately because a long overlap can hide the edge peak. Saved speed
settings travel with renders, sweeps and browser-local named recipes. Shared
preview playback speed remains an audition control, separate from rendered
source retiming.

AI bridge imports and hosted generation still require a separately rendered
Speed-off parent. The two-image provider path does not receive original video
motion. Existing bridge history and recovery remain usable. Tracked/depth
mattes, neural interpolation, GPU parity, live hosted quality validation and
automatic editor selection retain their separate evidence and decision gates.

## Original implementation direction

Build a small collection of expressive, composable transition families whose
timing and geometry can be adapted to the actual clips. The highest-value first
set is a better whip, anchored crash zoom, highlight bloom, and organic light
burn. Add subject occlusion, depth reveals, and generated bridges through explicit
media inputs once those foundations produce convincing played results.

Whips, luma fades, speed ramps, and datamosh are established techniques. The
verifiable recent development is easier access to tracked masks, depth, and
generation with stronger conditioning. These sources establish current tools and
creative examples; they do not establish a ranked list of social-media trends:

- Adobe added Film Impact's transition library in September 2025 and released
  Object Mask in January 2026. This makes subject-aware compositing more
  accessible in ordinary editing workflows.
  [Film Impact announcement](https://blog.adobe.com/en/publish/2025/09/09/introducing-more-than-90-new-effects-transitions-animations-in-premiere-pro),
  [Object Mask release](https://blog.adobe.com/en/publish/2026/01/20/new-ai-powered-video-editing-tools-premiere-major-motion-design-upgrades-after-effects).
- A January 2026 creator demonstration combines masking and glow into flash
  cuts. This is a useful visual reference, not proof of universal popularity.
  [Pritam Ghosh demonstration](https://www.youtube.com/watch?v=fuqfuy7U8PA).
- Depth Scanner's changelog records Depth Anything 3 support in November 2025
  and full Premiere compatibility in February 2026. Its depth slicing is a
  concrete reference for contour and foreground/background reveals.
  [Product and changelog](https://aescripts.com/depth-scanner/).
- Higgsfield's camera catalog includes crash zooms, flying-camera transitions,
  through-object moves, and low-shutter effects. Treat these as creative
  references; website effects and callable model APIs have different contracts.
  [Camera catalog](https://higgsfield.ai/camera-controls).

## Families worth building

### 1. Velocity whip and shutter smear

The desired look is acceleration into a brief obscured crossover, followed by
deceleration into the next shot. Start with direction, peak time, acceleration,
settle, and blur controls. Keep the picture change close to peak motion.
Separate source playback speed from the artificial pan; changing the easing of
a transform does not retime the source footage.

Use one continuous sampler for the moving A/B composition so every blur sample
can cross the dividing line. Derive blur width from transform velocity, with a
bounded maximum and a smooth entrance/exit. A spatial approximation should be
described as such. Actual shutter integration would need multiple transform/time
samples and a separate render profile.

Best pairs: compatible camera or subject movement, textured scenes, usable
source handles. Failure cases: opposing movement, frozen-looking arrival,
repeated edge pixels, and judder from low-frame-rate retiming. Boris's documented
swish-pan controls separate velocity, blur, and blur delay, which is a better
control model than one intensity slider.
[Swish Pan reference](https://borisfx.com/documentation/continuum/bcc-swish-pan/).

### 2. Anchored crash zoom / match transform

Let the user click an anchor in each shot: an eye, face, product, circle, or
other shared shape. Store normalized coordinates in the displayed source
viewport. Animate scale, translation, and optional rotation around those marks,
with radial smear and a short crossover near peak movement. Begin with manual
anchors; tracking can later supply the same input.

Expose zoom direction, amount, crossover, and settle. Show the required crop
before rendering. Outgoing and incoming transforms must join their surrounding
footage continuously; an anchor-aligned middle followed by a snap back to the
original framing is a failed result. This technique works best when scale and
composition are compatible. Large perspective changes need a different effect.
Film Impact demonstrates the underlying combination of position, scale,
rotation, warping, and blur.
[Camera-shot transition demonstration](https://www.filmimpact.com/resources/video-tutorials/create-a-truly-seamless-transition-between-two-camera-shots).

### 3. Highlight bloom, shadow reveal, and organic light burn

Extend the current luma reveal with bright-first/dark-first, matte source A/B,
threshold bias, feather, and matte smoothing. Derive the mask from normalized
source luminance before decorative glow. For adaptive thresholds, freeze
statistics over the selected seam window; independently normalizing every frame
could make the mask pump.

Make bloom and burn separate finishes: blurred highlight energy for bloom;
seeded, irregular spatial illumination plus a warm/cool exposure pulse for burn.
Give the picture crossover and the light envelope separate durations. Evaluate
linear-light exposure/bloom in a defined working space, and version that change
because it changes existing pixels. Preserve the original endpoint appearance.

Best pairs: backlight, practical lights, reflections, nightlife, analog fashion
and music footage. Check clipped faces, unstable noisy mattes, banding, and
long ghosted blends. Boris documents source-dependent luma patterns and a shorter
dissolve inside the film-burn envelope.
[Luma reference](https://borisfx.com/documentation/sapphire/ofx/dissolveluma/),
[Film-burn reference](https://borisfx.com/documentation/sapphire/avx/filmburntransition/).

#### Layered flash recipes

The user's refinement explicitly includes tasteful flashes and more complex
variations. Treat flash as a substantial family in the first implementation
pass. A composed flash has an origin, spatial falloff, attack, peak, and decay;
the picture change can have a different timing curve. The following names and
combinations are proposed lab recipes, not claims of named social-media trends.

- **Highlight kiss:** a brief lift around existing lights, reflections, or bright
  edges, with a tight glow and a softer secondary halo. Let the cut land near
  the peak while darker parts of the picture retain structure. Useful for a
  subtle accent that stays connected to the scene.
- **Edge burn:** warm light enters from one side, grows across an irregular
  boundary, briefly obscures the change of shot, then decays with a slight color
  shift. Position, direction, coverage, and texture seed remain adjustable.
- **Lens sweep:** a light source travels across the frame, producing a narrow
  directional streak and broad, faint glare. Start it near a visible practical
  light or a user-placed anchor. Keep streaks and any lens ghosts independently
  adjustable. A prismatic variant adds slight color separation only to the
  bright flare. Full optical lens simulation is a separate scope.
- **Shutter snap / double hit:** a quick exposure lift, a short release, and an
  optional smaller second pulse with a longer decay. Give pulse spacing, ratio,
  and cut phase their own controls. An optional very small transform kick is
  useful when the pair has compatible movement; it must settle continuously.
- **Afterimage flash:** retain a faint, offset or scaled impression of outgoing
  A for a short decay over incoming B, with restrained tint or channel spread.
  Save which source time supplies the afterimage; do not accidentally freeze
  several frames or create an uncontrolled long dissolve over faces/text.
- **Silhouette flash:** a brief rim light around a selected subject while B
  appears behind it, followed by the retained foreground resolving into B.
  This requires the imported/tracked matte path; a brightness threshold alone
  does not identify a subject. Keep the glow aligned to the transformed mask.

Compose these with motion when it serves the shot: a zoom can end in a
highlight kiss; a whip can carry a lens sweep; a passing silhouette can reveal a
brief burn. Use one dominant action per default recipe, with secondary layers
subordinate. Complexity should come from coordinated timing and spatial behavior.

Boris's UltraGlow transition documents a useful separation between the short
picture dissolve and the longer glow envelope, with thresholded primary glow,
secondary afterglow, and optional atmospheric texture. Its flashbulb and glare
families also distinguish spatially placed flashes from a uniform exposure
change. These are references for reusable primitives, not effects to copy by
name or a requirement to install proprietary plugins.
[UltraGlow controls](https://borisfx.com/documentation/sapphire/ofx/dissolveultraglow/),
[Flashbulb controls](https://borisfx.com/documentation/sapphire/ofx/dissolveflashbulbs/),
[Glare controls](https://borisfx.com/documentation/sapphire/ofx/dissolveglare/).

The initial UI should expose Amount, Spread, Warmth, and Timing, with Position
when the effect is placed spatially. Advanced controls expand into threshold,
highlight rolloff, attack/hold/decay, cut phase, pulse spacing, afterglow, texture,
and seed. Toggle each layer independently and compare subtle/medium/strong
variants on the same frozen pair. Amount should scale layer weights while
preserving the character of the recipe, rather than just adding white opacity.

Proposed starting points at the current 30 fps: 1-3 frames of attack, 0-1 at
peak, and 4-8 of decay; adjust after watching real footage. Store durations in
the recipe's timing contract and display the quantized frames. Keep dark-region
detail and face readability in the restrained defaults. Test bright/daylight and
dark/night footage separately, alongside noisy highlights and saturated lights.
Check spatial-mask stability, color clipping, afterimage ownership, exact
endpoints, and whether the light actually conceals the intended shot change.

### 4. Subject occlusion and cutout flash

Add an explicit video-matte input with source association, timestamps, feather,
expansion, inversion, and offset. This supports revealing B behind a passing
person or vehicle, keeping a cutout subject while changing the background, and
adding a brief outline/glow hit. Start with imported mattes to test the compositor
before adding inference. The mask must be aligned to source time and the same
crop/transform as its picture.

Later, a bounded analysis job can create cached masks from text/point prompts.
Meta's SAM 3.1 release is dated March 27, 2026 and supports prompted video
segmentation/tracking. It is a candidate to benchmark, not a dependency already
validated on this machine. Hair, transparency, occlusion, and unstable edges are
the important test cases.
[SAM 3.1 release](https://github.com/facebookresearch/sam3/blob/main/RELEASE_SAM3p1.md).

### 5. Depth scan and restrained parallax

Reuse the matte path for a depth sequence. Animate a depth threshold, optionally
add a narrow contour band, and only then experiment with small depth-dependent
displacement. Strong foreground/background separation is helpful. Flicker,
reflections, ambiguous geometry, and holes exposed by displacement are likely
failure cases; a single-frame depth model should not be assumed temporally stable.

Video Depth Anything is a candidate producer for this input. Its Small checkpoint
is Apache-2.0, while Base/Large are noncommercial; model identity and license
must be evaluated per checkpoint. Cache the result once for the selected windows
with source, model, and preprocessing provenance.
[Official model repository](https://github.com/DepthAnything/Video-Depth-Anything/blob/main/README.md).

### 6. Digital tear as a later stylistic option

A short, seeded block displacement with RGB separation is a manageable local
effect. Call it digital tear or glitch. Actual datamosh uses motion information
and previous-picture feedback, so it needs a different experiment. Prioritize
the motion and light families first; they fit more of the library.
[Datamosh mechanism](https://aescripts.com/datamosh/).

## Original lab audit (before RGB-v3)

Code inspected before the upgrade: `pipeline/transitions/{contracts,filters,jobs,media}.py` and
`web/features/transitions/{TransitionWorkspace.tsx,transitions.ts}`.

- The current whip chooses A or B before taking its five blur samples, then
  clamps samples within that image. A 320x180 red-to-blue fixture confirmed
  that increasing intensity from zero to one did not soften the moving join.
  The sampler must include both images at that boundary. The retained
  [measurement receipt](../../.tmp/transition-quality-audit-32c66796/measurements.json)
  names both fixture videos. These temporary artifacts are local audit evidence,
  not part of the durable render library or a visual-quality benchmark.
- Luma reveal samples outgoing Y divided by 255. The render pipeline uses YUV
  formats but does not establish a complete range/transfer/primaries contract.
  Normalize those explicitly before adding exposure or bloom. Full-range and
  limited-range footage must not silently use different threshold semantics.
- Existing source windows are trimmed and converted to 30 fps. There is no
  explicit source-time curve, independent handles, or motion analysis. A velocity
  effect must not imply those already exist.
- Sources are fitted and padded before compositing. Add explicit per-clip
  Fill/reframe and overscan controls before geometric effects, keeping Fit as
  an option; otherwise a portrait whip also moves the letterboxed canvas.
  Record framing in the recipe and comparison identity. Show overlap handles:
  changing overlap currently changes total pair duration and cadence too.
- Comparison players run independently and seam looping uses `timeupdate`.
  This is sufficient for basic audition, but not tightly aligned comparison.
  Add one transport with relative seam alignment; account for different overlap
  lengths rather than assuming equal total video durations.
- Returned AI video currently plays by itself in browser-local memory. It needs
  a durable imported asset and an assembled A-to-bridge-to-B preview before the
  lab can judge its actual joins or restore that experiment later.

The installed FFmpeg build advertises `xfade`, `gblur`, `remap`, `perspective`,
`zoompan`, `tmix`, `zscale`, and `tonemap`. This is capability discovery, not a
quality or performance benchmark. Start with bounded local filter graphs for
the first improvements. FFmpeg documents custom xfade sampling and explicit
color conversion; multi-frame blur needs a different path from current-frame
pixel sampling. [FFmpeg filter reference](https://ffmpeg.org/ffmpeg-filters.html).

## Clean implementation shape

Keep the existing projectless ledger, editor worker, source validation, artifact
ownership, cancellation, and immutable manifests. Evolve the recipe into a
typed, versioned composition plan:

1. **Source/time:** exact windows, output frame rate, source-time maps, and
   required handles. Validate every requested sample against the selected
   bounds; extending a window should be an explicit user action.
2. **Geometry:** source viewport/crop, anchors, scale, translation, rotation,
   and motion envelope. Store normalized values independently of output size.
3. **Reveal:** crossover curve and optional luma, alpha, or depth input.
4. **Finish:** bounded blur, bloom, exposure, tint, or seeded distortion.

Use validated parameters and a small fixed operation order, not arbitrary user
shader code or an unrestricted node editor. Presets are curated starting values
for shared operations. Preserve old renderer versions; changing a preset or
color pipeline creates a new version instead of silently changing saved runs.

A GPU compositor could eventually improve interactive tuning. First prove the
need with one local effect and measured preview/export parity. The GL Transitions
specification is a useful reference for normalized coordinates, aspect handling,
and exact endpoints; importing a large shader catalog would not establish taste
or quality. Any later GPU path should share parameters, frame times, color
policy, and evaluation fixtures with the export path.
[GL Transitions specification](https://github.com/gl-transitions/gl-transitions).

For experimentation, add a bounded local sweep: three durations by three
strengths on one frozen pair, one transport, per-variant notes, and save/favorite.
Keep the existing hard-cut comparison and provide a beat marker or user-supplied
audio excerpt so timing can be judged in context. Three-duration sweeps and local
music audition are implemented; strength sweeps remain proposed and export
remains muted. Avoid rerunning mask/depth extraction on slider
changes. Hosted generations need a separate visible budget and explicit submit.

## AI bridge integration

Start by making external-result import durable and previewing both joins. Then
implement one provider adapter with a small capability registry. Keep endpoint
bridging distinct from reference-driven regeneration of a shot; a motion
reference does not promise preservation of original A and B.

**Recommended first experiment: Runway Dev with Seedance 2.5.** Runway added the
model in August 2026, with 4-30-second generation and subsequent 1080p support.
Its image endpoint supports positioned first/last images. Native Gen-4.5's
first-image conditioning is a different capability. This recommendation is based
on a documented integration surface, not a measured quality win on our footage.
[Runway changelog](https://docs.dev.runwayml.com/api-details/api_changelog/),
[Input contracts](https://docs.dev.runwayml.com/assets/inputs/).

At the checked rates, a four-second endpoint-only Seedance 2.5 output costs
$0.80 at 480p, $1.20 at 720p, or $2.72 at 1080p before applicable tax.
Reference video adds charges. Freeze duration/resolution and display the total
before submission; refresh prices when implementing. Veo 3.1 Fast is a useful
second comparison candidate, not a silent fallback.
[Runway pricing](https://docs.dev.runwayml.com/guides/pricing/).

**Higgsfield remains a viable experiment path.** Its official CLI documents
Seedance 2.5 and Kling 3.0 start/end-image inputs. Its public REST catalog has a
different set of routes, so do not assume a website Transition Studio button has
an equivalent generic REST endpoint. Inspect the actual model schema and retain
the concrete model/parameters in the recipe.
[Official CLI contract](https://github.com/higgsfield-ai/skills/blob/main/higgsfield-generate/SKILL.md),
[REST schema](https://docs.higgsfield.ai/docs/openapi.json).

Direct BytePlus is another option, but the published LAS contract differs in
resolution and real-face reference eligibility. Those constraints are relevant
to a film library and must be checked per provider/account.
[BytePlus generation contract](https://docs.byteplus.com/en/docs/Byteplus_LAS/video_gen_enhanced).

Implementation requirements:

- Keep generation duration separate from editorial bridge duration. Preserve
  the full original result; trim or retime only through an explicit saved edit.
- Normalize endpoint dimensions/crops/color consistently with the assembly.
  Save source-native endpoints and actual provider inputs separately. Allow the
  user to move endpoint selection within the chosen source bounds.
- Persist the request and provider task ID immediately. Record endpoint hashes,
  actual source PTS, transforms, prompt, provider/model/API version, seed,
  requested duration/resolution, cost snapshot, status, and output checksum.
  A seed is not a promise of bitwise reproducibility across provider updates.
- Submit asynchronously; avoid blind resubmission after an uncertain network
  result. Runway documents a minimum five-second polling interval. Cancellation
  must be state-aware: its task-delete API also deletes completed output.
  [API reference](https://docs.dev.runwayml.com/api.md).
- Download successful media promptly; provider output URLs are temporary.
  Preserve actual fps/dimensions and record conversion into the lab profile.
  [Runway output lifecycle](https://docs.dev.runwayml.com/assets/outputs/).
- Start with three prompt directions: a foreground pass-through, a matched
  camera whip, and an intentional world morph. Identify protected subjects and
  camera direction. Display the generated interval in the assembled preview.

Two endpoint images constrain appearance but do not specify source motion
velocity. Consequently, judge both original-A-to-generation and
generation-to-original-B at normal speed. This is an engineering inference to
test, not a model guarantee. Replacing the first and last generated frames with
the originals does not repair a motion hitch in adjacent frames.

## Acceptance and order of work

First fix the continuous whip sampler and source color contract, then add
anchored zoom and bloom/burn, together with synchronized comparison. Next add
the shared imported-matte path for subject/depth experiments. Make generated
imports durable and assemble their seams before enabling the first hosted
adapter. Automatic transition selection remains a later editor decision under
ADR-0070; new media/provider boundaries need an accepted ADR and architecture
contract update when implemented.

Use a fixed, small evaluation set: compatible and opposing pans; aligned and
misaligned faces/objects; bright/night scenes; a foreground crossing; deep and
flat scenes; noisy low-light footage; and mixed aspect ratios. Freeze source
windows, output settings, and comparison notes. Include intentionally poor pairs
to learn when an effect should not be suggested.

Technical checks should cover exact endpoint ownership, frame counts and PTS,
range/color consistency, no exposed image gaps, source-time bounds, mask
alignment, deterministic local seeds, cancellation, and artifact restoration.
Synthetic color/gradient/moving-edge clips isolate those failures. Encoding
success and image-similarity scores cannot decide whether a transition is good.

Played review should score entrance continuity, crossover, exit continuity,
subject integrity, intended energy, and usefulness versus the hard cut. Watch
normal speed first; use slow motion/frame stepping to diagnose failures. Record
which shot conditions worked and which did not. Promote only recipes supported
by saved comparisons into the future editor capability.
