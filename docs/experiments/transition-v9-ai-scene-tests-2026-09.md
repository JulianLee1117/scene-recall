# Transition v9: monitor workflow and AI scene tests

Recorded during the **2026-09-16** local session; download receipts use UTC on
September 17. This is experiment evidence, not a new architecture contract.
[ADR-0076](../decisions/0076-camera-whip-and-program-monitor-timeline.md) records
the accepted local changes.

## Local workflow and motion

One program/source monitor now sits above a continuous A → B timeline. Clicking
either clip inspects it and pauses transition playback; four clip-edge handles
trim the sources and the overlap handle changes transition duration. The monitor
distinguishes working footage from a saved result needing an update. Saved
versions, downloads and comparison controls remain available without dominating
the main editing loop.

`transitions-rgb-v9` replaces the adjacent-picture whip with **Camera whip**:
modest full-frame movement in one screen direction, protected crop and a short
crossover near the blur peak. The 12-frame starter uses .65 exposure, .55 shutter
softness and .04 cut blend. This does not change source retiming or add shake,
depth reconstruction or motion-matched AI conditioning.

Before the final integration fixes: **3,069 backend tests passed, 11 skipped;
481 frontend tests passed; TypeScript and production build passed.** The subsequent download
transport fix passed **53 focused tests**. Full-suite counts and build output are
retained in `.tmp/transition-v9-checks/backend-full.log`, `frontend-final.log` and
`build-final.log`. These establish implementation checks, not general creative
quality. Final verification passed all **139 transition frontend tests**, the
TypeScript production build, 54 focused duration/generation/model checks and
92 bridge/framing checks across focused runs. The latest full web run recorded
447 passes and 11 failures in separate music/dialogue test harnesses referencing
new `dialogueTransport`/`dialogueAudio` helpers; those unrelated files were not
changed here. Latest logs: `frontend-transition-release.log`, `frontend-release.log`
and `build-release.log` in the same temporary checks directory.

## WAN 3: rooftop-to-street dive

The first paid experiment used two native endpoints: *Kill Bill Vol. 1* rooftop
footage (3933.679750–3936.098833s) and *Fallen Angels* street footage
(4762.582240–4765.582240s). Parent render:
`754ca73b-a56b-4d76-adbb-cb79d79869ec`. The prompt requested a continuous forward
dive between buildings into the neon street, with acceleration and a clean
landing. WAN 3 was explicitly submitted for **2 seconds at 720p**; the provider
confirmed **20 credits ($0.20)**, matching the reviewed estimate.

Generation `853cd9d2-eb12-4344-9188-fd8ead736418`, provider task
`056c087a-bf38-4198-a832-3597dd952cfd`, succeeded remotely at **05:39:29 UTC**.
Local download failed at **05:42:21 UTC**, leaving no original in the job folder.
DNS returned eight IPv6 addresses before four IPv4 addresses. IPv6 connection
timed out; the same signed URL returned HTTP 200 over verified IPv4 TLS in 0.304s.
The old downloader's body deadline did not bound pre-header connection attempts.

The existing output was recovered over IPv4 in 3.822s, without another generation
request. It is a video-only H.264 MP4, **1280×720, 30 fps, 60 frames, 2.000s,
3,867,748 bytes**. SHA-256:
`9fca804b1d9fbbe62946fb1ac229fafd82cde3964006cc21ca9eb5f300d4c314`.
The [recovery receipt](../../.tmp/transition-v9-checks/wan-recovery-receipt.json)
retains generation/task IDs, preparation and parent-manifest hashes, network
diagnosis and media verification. It contains no signed URL or credential.
The failed generation ledger entry and provider receipt were preserved.

The recovered original was explicitly imported as a **0.6-second** bridge:
`6f85a33b-7f3d-430c-81b8-55a18f117926`. Its complete A → bridge → B audition lasts
6.033s, with the bridge at 2.433–3.033s. Independent review of the original found
an internal hard cut between reviewed frames **18 and 19**, rather than a
continuous camera dive. **Rejected for that intended transition.** Compressing
the duration does not establish a continuous move. Retain it as a negative case,
not evidence that every WAN result will behave this way. Contact sheets are in
`.tmp/transition-v9-checks/wan-independent-{contact,swap,turn}.jpg`.

## H3 Max: archway passage

A second source pair uses an amber corridor from *Raise the Red Lantern*
(3486.6–3487.8s) and dunes from *Dune: Part Two*
(2692.627438–2694.627438s). Prepared parent:
`81298587-ad91-4826-b27a-82667163afc2`. The prompt asks for a physical doorway
approach, brief sunlight occlusion and forward travel into the desert.

The explicit request used **H3 Max, 5 seconds, 768p, seed 42 and prompt expansion
disabled**. Generation `099573b3-8eaf-402c-ba30-8808f3d9d1d4` succeeded remotely
at a confirmed **40 credits ($0.40)**. Its healthy downloaded original is H.264,
**1344×768, 24 fps, 124 frames, 5.166667s of video**, with an AAC tail bringing
container duration to 5.184s, and **3,881,273 bytes**. The original remains in its
generation job folder and did not need a separate download recovery.

Local assembly rejected the video under the generic 150ms duration tolerance.
The subsequent fix permits **up to 250ms of overrun for H3 only**, retaining the
full actual video duration at 1× and recording the allowance in the receipt.
Other models and all underruns retain the 150ms bound. This is a measured lab
acceptance policy, **not a vendor timing guarantee**. Its focused tests passed
**54 checks**.

Frame review found a continuous arch passage with no internal cut, minor ornament
drift and an invented terrace. An explicit **1-second** import,
`7c6e0dc8-f916-4fc8-82b4-22991da68004`, completed as a **4.2-second** audition with
the bridge at **1.2–2.2s**. Its output is 460,197 bytes; total retained files are
5,100,139 bytes with zero scratch bytes. Frame inspection then caught artificial
7px sidebars during the fitted H3 segment. A bounded correction now uses centered
Fill when both source clips use Fill and ordinary display geometry is within a
2% aspect mismatch; other cases retain Fit. The manifest records that decision.

Browser workflow verification used **Adjust timing → 1s → Save & render both joins**, preserving
the saved prompt/model/seed and creating final audition
`c8af082a-a5f7-4eb2-8905-10271701ae9d`. It keeps the full 5.184s original interval,
plays the bridge at 1.2–2.2s in a 4.2s edit, and uses 465,005 output bytes with
5,104,672 total retained bytes and zero scratch. This retains the corrected local
audition; the prior result remains unchanged. A corrected eight-frame join check
confirmed the transient sidebars are gone at both boundaries
(`.tmp/transition-v9-checks/h3-short-corrected-join-contact.jpg`).
Frame evidence supports a coherent
passage but does not establish perfect velocity matching at either join.
Request,
quote, attempt and job receipts are under
`.tmp/transition-v9-checks/h3-archway-*.json`.

**User creative verdict: rejected as a recommended edit transition.** The user
found the doorway passage very unnatural and not usable as part of an edit,
despite its technical continuity. The candidate remains experimental evidence;
successful generation, continuous geometry and functioning local assembly do
**not** establish editorial acceptance. This verdict concerns this result, not
the capabilities of every AI transition.

Future acceptance must judge the transition with its neighboring clips and
music, at the intended edit speed, checking camera speed, lens and perspective
consistency across both joins—not just whether the generated bridge connects
its endpoint images.

## H3 Max: sand veil — experimental soft dissolve

The third and final test uses the same corridor/desert pair and **5s, 768p, seed
42, prompt expansion disabled**. It requests a steady camera and one foreground
gust of golden sand that covers the view before clearing onto dunes. This tests
occlusion as an alternative to the archway's camera travel. The prompt is retained
in `.tmp/transition-v9-checks/h3-sand-request.json`.

Generation `32ccef73-f22a-4fe9-895d-a8f564385a54` completed generation, download
and local assembly automatically, with **40 credits ($0.40)** confirmed. Its
5.184s original was accepted by the bounded H3 policy at original speed. The
8.4s audition contains the bridge at 1.2–6.4s. Original/output sizes are
3,377,040/931,912 bytes; retained total is 5,244,129 bytes with zero scratch.

The [frame contact](../../.tmp/transition-v9-checks/h3-sand-contact.jpg) shows a
wind plume followed by partial transparency: walls remain visible while dunes
appear. It is an **experimental soft dissolve**, not a clean opaque sand veil,
and is less convincing than the doorway passage. No additional paid iteration
was made. All three provider receipts confirm **100 credits ($1.00) total**.
No auto-billing setting was changed.

## Vendor contract and storage follow-up

Current primary Runway documentation was checked for the
[model catalog](https://docs.dev.runwayml.com/guides/models/),
[pricing](https://docs.dev.runwayml.com/guides/pricing/) and
[output lifecycle](https://docs.dev.runwayml.com/assets/outputs/). The published
rates are 10 credits/second for WAN 3 at 720p and 8 credits/second for H3 Max at
768p, with credits priced at $0.01. Successful tasks expose temporary output URLs;
Runway requires applications to download and retain the media. Provider success
therefore remains separate from successful local retention and visual acceptance.
The broader [AI options note](ai-transition-options-2026-09.md) records model-specific
input controls and limits.

The starter prompts also draw on Runway's current
[image-to-video guide](https://help.runwayml.com/hc/en-us/articles/48324313115155-Image-to-Video-Prompting-Guide),
[camera terms and examples](https://help.runwayml.com/hc/en-us/articles/47313504791059-Camera-Terms-Prompts-Examples)
and [prompting introduction](https://help.runwayml.com/hc/en-us/articles/46182941379347-Introduction-to-Prompting).
These favor a clear motion event, concrete camera behavior and a sequence that
fits the chosen duration. Their demonstrated model is Gen-4.5: applying this
guidance to hosted H3 or WAN is a **prompt-design inference**, not proof of model
transfer or a native transition feature. The three reviewed outputs above are
individual observations, not a controlled model ranking.

Six old transition test-fixture trees were verified as inactive and contained
inside the repository, with no symlinks or junctions and only internal synthetic
hardlink fixtures. Their unique file bytes total **1,382,818,342 (~1.288 GiB)**.
Automatic approval review blocked the guarded cleanup before execution.
`.tmp/cleanup-old-transition-fixtures.ps1` was prepared for the user but **not
run**. Saved local renders, imported originals, bridge parents, current QA and raw
films remain retained; the failed cleanup is not counted as reclaimed storage.
