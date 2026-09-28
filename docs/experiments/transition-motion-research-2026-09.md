# Motion-led transition experiments

Reviewed **2026-09-16**. This is an experiment brief, not a ranking of social-media
popularity or a change to the architecture contract. Primary creator tutorials,
vendor examples and current API schemas establish that these techniques exist.
They do not establish how often editors use them, or how reliably an external
model will reproduce them on this film library. No hosted generation, model
download, account purchase or new dependency was used for this research.

## RGB-v8: reference-led motion and interaction refinement

The user supplied [Inspired Island — D I C A P R I O 2](https://www.youtube.com/watch?v=aHlRPACcX3g)
and its local 1080p file. Inspection used the supplied file's actual presentation
timestamps and bounded contact sheets; the original was not modified or copied
into the Lab. This is a chosen aesthetic reference, not evidence of current
TikTok/Instagram popularity.

- **5.756–6.173s:** clear outgoing face, fast leftward exit/stretch by 5.839s,
  incoming bar shot from the right by 5.923–6.006s, then a clean landing. The
  landing appears mostly monotonic, with no conspicuous spring oscillation.
- **19.895–20.103s:** legible outgoing room, strong horizontal smear around
  19.937s, blurred incoming gunman by 19.978s, crisp incoming shot by 20.103s.
  The strongest blur occupies about four native frames / 0.17 seconds.
- **20.520–20.854s:** a short blurred push-in inside the same gunman shot. This
  is a shot-level accent, not a new-scene transition.
- **26.2–27s and 31.9–33s:** title/ink compositions and colored layered panels
  need masking/compositing or broader editorial choreography. They are outside
  the two-shot recipe boundary.

The useful motion target is readable footage around a brief concentrated burst,
then a sharp landing. A large bounce is not the defining ingredient. Source
choice and matching direction matter as much as the curve. Layered effects in
this reference must not be advertised as something a single Lab transition can
reproduce.

Adobe's current guidance supports the underlying craft: the speed graph controls
acceleration/deceleration, Motion Tile can mirror adjacent tiles, and shutter
angle models exposure relative to frame rate. Those concepts inform the local
implementation; they do not establish equivalence to After Effects, Resolve
Speed Warp or a neural optical-flow model.
[Adobe speed controls](https://helpx.adobe.com/after-effects/desktop/animate-in-after-effects/speed-between-keyframes/speed.html),
[Adobe Motion Tile](https://helpx.adobe.com/my_en/after-effects/desktop/apply-effects-and-animation-presets/list-of-effects/stylize-effects.html),
[Adobe time effects](https://helpx.adobe.com/my_en/after-effects/desktop/apply-effects-and-animation-presets/list-of-effects/time-effects.html).

Actual fixes in RGB-v8:

- Continuous midpoint warping and polynomial travel replace velocity corners.
  Zoom uses log scale, a small single recovery, crop-bounded anchor travel and
  mirrored edge protection. Default rebound .15 is restrained, not a large bounce.
- Exposure follows the actual overlap frame interval. Continuous whip convolution
  and adaptive radial samples concentrate smear where travel is fastest. A
  gap-aware prefilter removes separated high-contrast highlight trails without
  changing sharp endpoint pixels.
- A real 2s / 30fps Rush 4× fixture formerly ended with a roughly 7× source jump
  after a single-frame endpoint overwrite. Ramp-local monotonic correction keeps
  its 52-frame output and native endpoints while distributing that correction.
  Actual source-target intervals are reported separately from the nominal curve.
- Bounded cloned end context lets local MCI serve the final interior slow-motion
  targets rather than falling back to several repeated native frames. It does
  not expand the selected raw source window.
- Direct trim handles, on-picture reframing and a duration slider replace the
  primary numeric/grid interactions. Local/AI modes preserve drafts, saved history
  starts collapsed, and the frozen AI source pair is explicit.

Same-input day/night contact sheets show a stronger central whip smear and clean
resolution, while the zoom retains faces outside its brief motion burst. Final
480p render timings using already-normalized clips were about 3.1–3.3s for whip
and 11.0–11.7s for zoom. A 12-frame 1080p seam took about 7.5s / 54.4s respectively,
excluding normalization and encoding. Draft is the practical experiment setting;
1080p remains an explicit export. Playback uses a compact baked MP4 and does not
rerun this compositor.

Durable Lab acceptance examples: `de6a9f8b-ae16-4bc5-a235-ba083c8feb30` (9f Rush
3× whip, 720p, 4.10s, 4,377,264 output bytes) and
`2ebd6e02-4e75-4ab4-81a9-f930d1ac801e` (12f original-speed zoom, 480p, 4.40s,
638,190 output bytes). The whip was played against the earlier matching 9f/720p
Rush version on the shared transport, and its handoff/landing inspected. Native
stills remain attached during framing; editing one source keeps the other still.
No paid AI output has been generated on this installation: Runway setup is still
missing, and prompt/model options remain unverified on this user's footage.

Final validation: 3,063 backend tests passed, 11 skipped (three existing
deprecation warnings); 445 frontend tests, TypeScript and production build passed.
After the final narrow-caption adjustment, all 12 source interaction tests and
the production build passed again. Browser checks covered direct trim/framing,
native still preservation, synchronized earlier/new whip playback, zoom playback,
390px layout without horizontal overflow, and AI preparation preserving the
working local effect. `bd278a72-4fa8-4bdb-83e6-a2d1785412b3` is the resulting local
AI-ready hard cut; it is not generated AI video. All three owned render jobs
completed with zero scratch bytes. Logs are in `.tmp/transition-v8-checks/`.
Automatic approval review blocked deletion of the two new temporary pytest
directories (`tv8full`, `tv8focus`), leaving approximately 235 MiB of test fixtures;
the deletion was not retried through another mechanism.

## What is current, and what is established craft?

Higgsfield's **August 1, 2026 Cinema Studio help article** now describes Studio
4.0 using Seedance 2.5, with distinct controls for camera movement, optics,
lighting and editing tempo. Its Studio 3.0 section separately lists speed-ramp
choices. The practical lesson is to separate *where the camera travels* from
*how time accelerates*. Studio's reference, extension and editing workflows are
different operations; their UI controls are not evidence that our endpoint-frame
API supports the same inputs. [Cinema Studio documentation](https://higgsfield.ai/creator-hub/help-center/tools/how-do-i-use-cinema-studio)

The current Higgsfield gallery includes Column Wipe, Flying Cam, Hole, Hand,
Display and Portal transitions. These are specific vendor examples of motivated
occlusion and camera journeys, not a measured trend chart. The gallery's
existence also does not establish an equivalent public API effect identifier.
[Effects gallery](https://higgsfield.ai/collection/effects),
[Column Wipe example](https://higgsfield.ai/motion/0b316480-075a-4999-bfde-c9710c0c6804)

Current publication does not make an editing technique new. Adobe published
Karen X. Cheng's speed-ramp lesson on **February 23, 2026**: time remapping,
easing and preview iteration are the concrete mechanisms. Its custom-transition
lesson, published **July 21, 2026**, teaches animated masks and shape layers.
Both support a flexible lab with explicit timing and geometry rather than a
large collection of opaque presets.
[Karen X. Cheng lesson](https://www.adobe.com/learn/after-effects/web/the-unlock-karen-x-cheng-on-going-viral-and-optimizing-for-fun),
[Adobe custom masks](https://www.adobe.com/learn/after-effects/web/create-custom-transitions)

## Realizable patterns

The experiment settings below are proposed lab starting points, not vendor
benchmarks. Compare each against the same pair's hard cut before adding layers.

### Carry motion across the cut

Choose clips with compatible screen direction, action and subject scale. Speed
up toward the outgoing cut, keep the incoming movement travelling the same way,
then release the acceleration. A short directional smear can conceal the peak;
it cannot create missing source motion. Start with the speed change alone, then
add the local whip. Compare a gentle and sharp curve at the same affected span.

Record a monotonic output-time → source-time map, exact source windows and the
interpolation method. Do not silently extend trims to obtain handles. Nearest
sampling, blending and motion interpolation change the appearance of slow
sections; they are separate from the speed curve. Resolve 21 documents separate
frame-position/playback-speed curves and these rendering choices, with Speed
Warp as a Studio feature. Its Smooth Cut example repairs nearby interview jump
cuts; it is not evidence for morphing unrelated scenes.
[Resolve Edit documentation, retrieved September 16](https://www.blackmagicdesign.com/au/products/davinciresolve/edit)

**Failure checks:** duplicated cadence, a cut between opposite velocities,
interpolation tearing at hands/hair, slowing that exposes insufficient source
frames, or a brief reversal in the time map. Inspect with the music and muted.

### Hide the scene change behind an object

A foreground column, hand, jacket or doorway can cover the image while the
incoming scene replaces the background. The cover's direction and scale should
be believable in both clips. A procedural wipe approximates this effect; an
object-bound version needs a supplied/tracked matte and its own asset contract.

Cinecom's **June 2, 2021** punch tutorial is a concrete older creator example:
track and feather a fist mask, offset a glowing duplicate, expand the cover,
then combine lens distortion and a small scale bounce. Its **February 23, 2022**
masking lesson similarly uses moving foreground subjects to conceal a reveal.
These are established mechanisms still useful for designing experiments.
[Punch transition](https://www.cinecom.net/adobe-premiere-pro-tutorials/video-effects/punch-transition/),
[Foreground masking](https://www.cinecom.net/adobe-premiere-pro-tutorials/video-effects/5-masking-tricks/)

**Experiment:** use a short cover near the visual cut; compare hard vs feathered
cover before adding glow. AI prompts should name the specific cover and route.
Reject covers that reverse, appear from nowhere, or stretch the person to fill
the frame. Do not add automatic segmentation until a reviewed pair shows why a
manual/procedural solution is insufficient.

### Fly through a space, rather than through a solid subject

Window, doorway and gap transitions work best when the destination has a
plausible line of travel and comparable camera height. Prompt one path, the
occlusion/fast middle, and the landing. An unrelated destination can be an
intentional surreal reveal; it should not be described as recovered physical
camera motion. The current Higgsfield examples motivate this experiment, but
do not establish endpoint or velocity fidelity.

**Experiment:** compare a forward path with a lateral occlusion on the same
pair. Describe observed A movement and desired B movement in plain language.
Reject unexplained camera orbits, horizon flips, solid-object penetration,
duplicate people and a frozen final second. Two stills cannot reveal either
clip's velocity, so our direct adapter needs user-written motion notes.

### Match an anchor before asking for a morph

Align a silhouette, doorway, eye line or circular object before requesting a
transformation. The shared anchor should stay readable while surroundings
change. A human-to-human identity transformation needs deliberate creative
intent; endpoint similarity alone does not justify promising identity
preservation. This is a proposed experiment, not a validated model capability.

Higgsfield's **August 13, 2026 prompting guide** separates first-frame blocking,
camera, physics and lighting. Its multi-shot examples explicitly request cuts;
that part is unsuitable for a continuous bridge. Borrow the specificity, while
asking for one shot and one transformation. The vendor's own repeat-generation
claims are not a benchmark on our clips.
[Seedance 2.5 prompting guide](https://higgsfield.ai/blog/seedance-2-5-prompting-guide)

**Failure checks:** anchor drift, face/body substitutions, repeated objects,
rubbery anatomy, or new intermediate scenes. Compare reframing first; another
generation is not always the cheapest correction.

### Focus, light and lens energy around a short cut

Film Impact's current Focus Blur page describes point/area/tilt-shift control
and bokeh treatment. Its Radial Blur page exposes center, strength, direction,
offset, chromatic and exposure controls. These support restrained focus-pull
and radial/prism experiments. A local 2D blur remains a stylization, not a depth
reconstruction or physically accurate refocus.
[Focus Blur](https://www.filmimpact.com/premiere-pro-effects/lights-%26-blurs-fx/focus-blur-fx),
[Radial Blur](https://www.filmimpact.com/premiere-pro-transitions/lights-and-blurs/radial-blur-impacts)

The **2025.2 changelog** documents RGB displacement with per-channel/falloff
controls, localized Dissolve/Luma Fade, and rolling-shutter controls on Push and
Roll. These suggest two useful variations: anchor the luma reveal near a
selected highlight, and make chromatic displacement decay away from the burst
instead of ghosting the entire picture. The version label is 2025.2; this is not
claimed as a September 2026 invention.
[Film Impact changelog](https://www.filmimpact.com/changelog/version-2025-2)

**Experiment:** use a narrow energy envelope, change the picture near its peak,
and return fully to the original source. Test blur alone before exposure or
chromatic effects. Keep skin and shadow structure visible except at the brief
peak. Check exit exposure and portrait framing; reject a long double exposure
or a second unmotivated flash. A visible light source makes the bloom more
plausible than uniform white.

## Current provider contract and its limits

The fresh [Runway OpenAPI schema](https://docs.dev.runwayml.com/openapi.json)
documents `seedance2_5` image-to-video keyframes via `promptImage` positions
`first`/`last`; keyframe and unpositioned reference modes cannot be mixed. It
accepts 4–30 seconds, a 15,000-character prompt, uint32 seed and explicit pixel
ratios. The current 480p landscape/portrait values are `854:480` / `480:854`.
The image endpoint has no video-reference field. Task acceptance requires an
`estimatedCost` object; final task cost is separate.

Schema drift matters: the **August 7** launch changelog still names `864:496`
sentinels, while the current schema names literal output dimensions. The lab
follows the current schema. The same changelog dates 1080p support to
**August 15, 2026**. A live account smoke test remains necessary; no paid
request has verified the adapter's model quality or account behavior.
[Runway dated changelog](https://docs.dev.runwayml.com/api-details/api_changelog/)

The same changelog announces **H3 Max on September 3, 2026**, with optional
last-frame guidance, 5–15 seconds, and 5/8 credits per second at 480p/768p.
This is a newer, cheaper candidate for a later controlled comparison, not
evidence that it makes better transitions. It needs a distinct model/pricing
contract; the current adapter must never silently substitute it.

Runway's current rate is 20/30/68 credits per output second at 480p/720p/1080p,
with an 80-credit minimum and $0.01 per credit before tax. Four-second still
requests therefore quote 80/120/272 credits. Input/reference video separately
adds 10/15/34 credits per second; image/audio references are free.
[Runway pricing, checked September 16](https://docs.dev.runwayml.com/guides/pricing/)

Our adapter intentionally uses 4–8 seconds, silent output, two normalized JPEGs,
a reviewed quote and a maximum local estimate of 300 credits. It does not
silently compress that full generated duration into a short transition. The
original is retained; a user can explicitly import/trim/retime it afterward.
Native endpoint downloads are separate from provider inputs, which use the same
30-fps segment edges and framing/color normalization as local bridge assembly.

### Can video tails and keyframes be combined?

For Seedance 2.5, the v2v schema accepts `promptVideo`, additional video
references and ordinary image `references`, but those images have no endpoint
position field. Modes are reference, extend and edit. A-tails plus B-heads can
therefore be investigated as *loose motion references*, not advertised as exact
two-sided gap filling. `aleph2` instead accepts an input video and up to five
timed guidance images, with optional edit ranges. Those are distinct contracts.
[Current OpenAPI](https://docs.dev.runwayml.com/openapi.json)

Runway's Edit Studio guidance describes Aleph as transforming existing footage,
including motion guidance and ranged edits. A possible future test is an
explicit rough A-tail → placeholder → B-head clip, with guidance images around
the middle. This is an inference about an experiment the API could express,
not documentation that it will invent a seamless bridge while preserving both
tails. Source windows, edited ranges, uploads, costs and original outputs would
need a separate receipt and approval boundary.
[Runway Edit Studio](https://help.runwayml.com/hc/en-us/articles/51683104370451-Creating-with-Edit-Studio)

Higgsfield remains a manual export/import option here. Its preset gallery and
Studio features do not establish a tested API contract in this lab. Imported
provider/model/prompt notes remain user-supplied, not provider-verified.

## Neural interpolation is a separate quality experiment

The local flow option uses the installed CPU FFmpeg motion-compensated
interpolator. FFmpeg documents duplicate, blend and MCI modes; MCI uses block
motion compensation, with adaptive weighting and variable-size block options.
It is neither a learned video model nor a guarantee of clean slow motion.
[FFmpeg minterpolate](https://ffmpeg.org/ffmpeg-filters.html#minterpolate)

Primary research gives better candidate baselines without proving a universal
winner on these clips:

- **RIFE, ECCV 2022:** arbitrary-timestep interpolation. Its official repository
  reports GPU throughput on a 2080 Ti and points to Practical-RIFE for newer
  practical variants; that speed claim cannot be transferred to this CPU worker.
  [RIFE authors' implementation](https://github.com/hzwer/ECCV2022-RIFE)
- **GIMM-VFI, NeurIPS 2024:** continuous motion modeling between adjacent frames
  at arbitrary timesteps. It is relevant to nonuniform sampling in a speed
  curve, but is not a semantic transition generator between unrelated films.
  [Authors' project](https://gseancdat.github.io/projects/GIMMVFI)
- **BiM-VFI, CVPR 2025:** directly studies accelerating/decelerating and changing
  direction, where time-to-location ambiguity can blur interpolation. Published
  benchmark gains need reproduction on our motion/occlusion fixtures.
  [Paper](https://openaccess.thecvf.com/content/CVPR2025/html/Seo_BiM-VFI_Bidirectional_Motion_Field-Guided_Frame_Interpolation_for_Video_with_Non-uniform_CVPR_2025_paper.html)
- **FILM, ECCV 2022:** a large-motion interpolation reference, but its official
  repository was archived on October 14, 2025. It should not be presented as a
  newly maintained 2026 dependency. [Google Research repository](https://github.com/google-research/frame-interpolation)

**Next gate:** first retain a real failed MCI example, with source PTS, output
mapping and decoded problem frames. Compare nearest/blend/MCI on the identical
slow region. Only if a visible failure remains should a separately scoped
neural evaluation select a pinned checkpoint, license, hash, device and runtime
budget. Include thin edges, occlusion, faces, camera blur, flashes and hard
cuts; measure latency/memory and inspect motion, not only PSNR. No automatic
model download or GPU service is justified by the word “latest.”

## Lab changes and evaluation sequence

The AI handoff now offers six explicit starters: whip, occlusion, camera flight,
focus pull, light bridge and match morph. Each has pair suitability and a join
check. Motion notes are manually applied to the editable prompt, so changing a
starter or the current source pair never silently replaces custom writing.
The UI says precisely what direct generation receives: two normalized stills
and the prompt, without source video or the audition music.

Source retiming stays local for this pass. Import/quote/generation reject a
retimed parent until conditioning and bridge assembly share the evaluated time
map. Render a **Speed off** version to use AI bridges. The guard does not
change completed historical artifacts, native frame downloads, prompt text,
saved-job inspection or cancellation/reconciliation of existing requests.

For useful results, keep a fixed source pair, baseline, framing and seed. Vary
one control, watch the cut or both generated joins at normal speed, then inspect
slowly. Record the specific failure: cadence, direction, anatomy, anchor,
exposure, timing, or cost. A small image difference at a join does not establish
velocity continuity. Preserve the full generated original and distinguish a
good-looking middle from a bridge that actually enters and exits cleanly.

## Implemented review — RGB-v6

[ADR-0072](../decisions/0072-native-time-speed-ramps-and-interpolation.md)
records the final local contract. Shared Rush, Slow hit and Pulse controls alter
native source time; recipe blur and lens movement remain separate. Source
windows stay fixed. Sampling runs on actual output timestamps `i / 30`, with
explicit first/last native-frame locks and per-frame receipts. An earlier v5
trial used an inclusive interval and could drop/duplicate interior frames even
at nominal 1×. The v6 correction has a real unique-frame cadence regression;
v5 trial artifacts remain readable as earlier renders.

The final review retained nine durable v6 jobs: a high-quality speed-driven whip,
its same-effect Speed-off comparison, its same-speed hard-cut comparison, two
experimental looks, three identical-window slowdown sampling comparisons, and
a speed pulse. Their actual decoded frame counts match their manifests. Native
endpoint evidence is unchanged between methods. Positive/negative container
origins, early/seeked windows, cancellation, ownership, bounded preparation and
AI retimed-parent rejection also have focused coverage.

Useful saved examples:

- [High-quality Rush whip](http://localhost:3000/lab/transitions?render=730ec3fe-4525-4d78-869f-328c9e930b57):
  9 frames, Rush 3×, 1.2 source seconds. The overlap means actual picture-change
  speeds are about 2.30× on A and 2.53× on B; the receipt exposes this instead of
  implying that both visible clips reach 3× at the switch.
  [Same effect without speed](http://localhost:3000/lab/transitions?render=31720f5a-5890-4813-bbaa-4b4ed7448576)
  and [hard cut at the same speed](http://localhost:3000/lab/transitions?render=6c3bddb7-1e42-44e9-bb63-43b5dae8e7c0)
  isolate those choices. Both were created and checked through the browser.
- [Defocus bridge](http://localhost:3000/lab/transitions?render=e1788b72-e597-4e08-b147-4f2c800db1bc):
  9-frame daylight face-pair experiment, Speed off. A brief softening conceals
  the change and quickly returns facial detail. It remains a flat-image blur,
  not recovered depth or physically accurate bokeh.
- [Prism push with Rush](http://localhost:3000/lab/transitions?render=f4c0c0d2-25b3-4b55-b93c-8bee07f02a8b):
  9-frame night-street experiment, 1.25× push and restrained channel separation.
  The geometry is more conspicuous on faces; keep it in Experimental and compare
  a lower strength before increasing the distortion.
- [Optical-flow slowdown](http://localhost:3000/lab/transitions?render=3c255c79-b3f0-4108-b4d5-954affa114bd),
  [frame blending](http://localhost:3000/lab/transitions?render=1b0fc2dd-b560-4fe2-bb1b-cec7f5cc93b1),
  and [captured-frame sampling](http://localhost:3000/lab/transitions?render=98740fd6-b2d0-4d80-8a20-fdd8acba38f6):
  same daylight windows, 0.5× edge speed and 0.8-second source span, all 164
  output frames. Played flow/blend comparison and inspected face/edge strips
  showed no obvious new large distortion on this pair. This low-motion example
  does not establish quality for hands, hair occlusion or fast camera motion.
  Flow remains optional, and the history/comparison labels name the method.
- [Speed pulse](http://localhost:3000/lab/transitions?render=b5d947d1-ba1b-4257-8c0c-1a20f126c497):
  hard-cut reference, 2.5× peak over 0.8 source seconds, without a visual effect.

Four additional local v6 motion/effect renders took 5.70–12.56 seconds including
native preparation at draft resolution; the flow/defocus combination was the
slowest. This is a local measurement on a small pair, not a production latency
promise. Receipts and full time mappings are in `.tmp/transition-retime-v6/`;
durable-job checks, frame strips and validation logs are in
`.tmp/transition-motion-review/`.

The final full backend run passed **2,901 tests**, with 11 skips and three
dependency deprecation warnings. All **414 frontend tests**, TypeScript checks
and the production build passed. Browser review checked saved speed restoration,
both comparison actions, prompt preservation until explicit starter application,
retimed-parent AI guards and a 390-pixel layout. Reviewed examples were favorited
locally. Hosted output quality still needs an API key and a separately reviewed
generation; no paid generation or film upload was performed in this pass.
