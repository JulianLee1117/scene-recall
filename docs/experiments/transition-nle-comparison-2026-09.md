# Transition Lab: Resolve and Premiere comparison

Research checked **2026-09-16** against official vendor documentation and the
current repository. This is a research and optimization note, not the architecture
contract or a claim of visual parity. No Resolve/Premiere application benchmark or
paid generation was performed. The implementation snapshot includes the validated
v7 encoding/storage polish; measured results and remaining limits are below.

The lab can provide a useful, focused transition audition workflow without
replicating a full NLE. The most transferable NLE practices are precise source
timing, separate control over motion interpolation, reusable rendered results,
explicit preview/export quality, and clear ownership of temporary versus retained
media. Keeping additional finished variants necessarily consumes storage; the
realistic target is **no unnecessary copies or abandoned scratch**, with visible
retained sizes and deliberate retention decisions.

## What the current NLE documentation establishes

### Speed and transition control

- **Premiere:** Time Remapping supports multiple speed keyframes within a clip
  and Bezier handles for the rate of change. Adobe advises proxies or lower
  resolution previews for demanding remaps. This is more general than the lab's
  three bounded speed shapes. Source updated **2026-01-07**:
  [Time Remapping](https://helpx.adobe.com/premiere/desktop/edit-projects/change-clip-speed/change-clip-speed-and-duration-using-time-remapping.html).
- **Premiere:** Frame Sampling repeats/removes captured frames, Frame Blending
  combines neighbors, and Optical Flow synthesizes intermediate motion. These
  are categories of behavior, not interchangeable implementations. Source updated
  **2026-01-07**:
  [time interpolation](https://helpx.adobe.com/premiere/desktop/edit-projects/change-clip-speed/apply-time-interpolation-methods-to-adjust-clip-speed.html).
- **Resolve 21:** The Edit page documents graphical retime curves for both frame
  position and playback speed, nearest-frame/frame-blend/optical-flow choices,
  and Studio's Speed Warp. It also documents effect keyframes and custom easing.
  Its Smooth Cut is intended to conceal nearby jump cuts, such as interview
  edits; that is a different task from generating a journey between unrelated
  scenes. Undated current product documentation, retrieved **2026-09-16**:
  [Resolve Edit](https://www.blackmagicdesign.com/products/davinciresolve/edit).
- **Premiere source handles:** Extra footage outside the visible In/Out points
  can supply a transition without shortening the edit. Insufficient media can
  produce repeated boundary frames and a warning. Source updated **2026-01-07**:
  [clip handles](https://helpx.adobe.com/premiere/desktop/add-video-effects/apply-video-transitions/clip-handles-settings.html).
  Separately, its newer *visual transition handles* let an editor drag a clip edge
  control to apply and resize a transition; these UI handles should not be
  confused with extra source footage. Source updated **2026-08-18**:
  [visual transition controls](https://helpx.adobe.com/premiere/desktop/add-video-effects/apply-video-transitions/video-transitions-using-clip-handles.html).

### Playback, proxies and effects rendering

- **Premiere:** Mercury uses the GPU for supported effects, color conversion,
  scaling and other image processing, while the CPU still does substantial work.
  GPU effect processing is distinct from codec decoding. Merely selecting a
  hardware encoder would not accelerate the lab's NumPy compositor or FFmpeg
  motion estimation. Source updated **2026-03-05**:
  [GPU acceleration](https://helpx.adobe.com/premiere/desktop/get-started/download-and-install/mercury-playback-engine-gpu-accelerated-in-premiere.html).
- **Premiere previews:** Rendering a sequence stores processed effects for later
  playback. Unchanged preview portions can be reused, including during export
  when explicitly selected. Preview codec/quality still matters. Source updated
  **2026-01-07**:
  [preview reuse](https://helpx.adobe.com/premiere/desktop/render-and-export/render-sequences-for-playback/use-preview-files-when-rendering.html).
- **Resolve:** Blackmagic's currently linked **Resolve 20 Beginner's Guide**, p.
  98, distinguishes Off, Smart and User render caching: attempt live processing,
  automatically cache demanding work, or explicitly choose clips. It documents
  clearing all, unused or selected timeline caches. The live product is Resolve
  21, but the [official training page](https://www.blackmagicdesign.com/products/davinciresolve/training)
  still links version 20 books on the research date; these procedural details
  are attributed to that book, not relabeled as a verified 21 manual.
  [Beginner's Guide](https://documents.blackmagicdesign.com/UserManuals/DaVinci-Resolve-20-Beginners-Guide.pdf?_v=1757574013000).
- **Resolve proxy media:** The current collaboration page describes automatically
  linked H.264, H.265 or ProRes proxy files generated from watched source folders.
  This creates additional media; it does not make effect rendering free.
  Undated, retrieved **2026-09-16**:
  [Blackmagic Proxy Generator](https://www.blackmagicdesign.com/products/davinciresolve/collaboration).
- **Resolve quality separation:** The **20 Colorist Guide**, pp. 375 and 442,
  separates proxy, optimized-media and render-cache format settings. Delivery can
  use optimized media, proxies or cached images, but the guide recommends doing
  so when their quality is suitable for the final result. Lowering timeline
  playback resolution or cache quality is a separate responsiveness choice.
  [Colorist Guide](https://documents.blackmagicdesign.com/UserManuals/DaVinci-Resolve-20-Colorist-Guide.pdf?_v=1757574010000).

### Storage and delivery

- **Premiere media-cache eviction is narrow:** Age and size policies apply to
  `.pek`, `.cfa` and `.ims` cache files in specified cache subfolders. This is not
  automatic deletion of every render, proxy or export. Source updated
  **2026-01-07**:
  [automatic cache management](https://helpx.adobe.com/premiere/desktop/troubleshooting/media-issues/automatically-manage-your-media-cache-files.html).
- **Proxies have a separate lifecycle:** Detaching a Premiere proxy removes its
  association, not the file, because another project may reference it. By
  default, export uses full-resolution media even when proxy playback is enabled;
  proxy export is an explicit choice. Both sources updated **2026-01-07**:
  [detach proxies](https://helpx.adobe.com/premiere/desktop/organize-media/ingest-proxy-workflow/detach-proxies.html),
  [export proxies](https://helpx.adobe.com/premiere/desktop/organize-media/ingest-proxy-workflow/export-proxies.html).
- **Export quality is an independent contract:** Premiere exposes frame size,
  rate, format, depth and rendering choices; using previews can affect quality
  according to their format. Source updated **2026-08-18**:
  [export settings](https://helpx.adobe.com/premiere/desktop/render-and-export/export-files/overview-of-export-settings.html).
- **Current AI context:** Premiere's **26.5** release notes, updated
  **2026-09-09**, describe a Generative Media Tool that inserts generated video
  and sound as editable sequence clips, plus expanded XAVC-I smart preview
  rendering. The lab should not be positioned as uniquely adding generation to
  an editor. This release note alone does not establish exact endpoint or
  source-motion conditioning guarantees.
  [Premiere release notes](https://helpx.adobe.com/premiere/desktop/whats-new/release-notes.html).

## Repository comparison

The following claims come from local code, not vendor documentation.

**Already represented:** The lab offers 15 bounded local recipes, framing,
source-window adjustments, beat-derived durations, neighboring timing variants,
same-pair comparisons, hard-cut and Speed-off baselines, native endpoint stills,
and a saved recipe/manifest. Rush, Slow hit and Pulse independently remap each
source before the visual overlap. Their selected source windows remain fixed,
and output duration changes. Native timestamp mapping, endpoint locks and a
30-fps output grid are explicit. This supports useful fast-edit experiments
without arbitrary timeline automation.

**Interpolation has similar choices, not demonstrated parity:** Captured-frame
sampling, linear-light blending and local CPU FFmpeg `minterpolate` cover three
useful modes. Flow is bounded to each clip's ramp and context, rather than
estimating motion across unrelated scenes. It is classical motion compensation,
not Resolve Speed Warp, Premiere's proprietary flow or a neural VFI model.
Occlusion, fine texture, motion blur and lighting changes still need played
review. See the separate
[motion and neural-interpolation research](transition-motion-research-2026-09.md).

**Timing semantics differ:** The lab overlaps the end of selected A with the
start of selected B, shortening their combined duration by that overlap. It does
not automatically borrow extra footage outside the saved windows to hold a
timeline cut fixed. AI bridge assembly instead inserts a third interval between
the clips. Future editor integration must explicitly choose overlap, extra
handles or insertion; treating these as the same operation would shift music
sync or duplicate footage.

**Playback currently uses baked media:** Effect changes require a render before
they become the played result. Browser video playback and approximate seeking
are not a live GPU effect graph or a frame-accurate NLE transport. Existing
seam-aligned comparison and local music audition are useful; audition audio is
not in the exported MP4. The readiness-aware transport polish is intended to
wait for decoded media before starting the shared clock, not to promise
sample-accurate synchronization across independent browser players.

**Current optimization direction:** Exact active requests are deduplicated.
Completed local renders can be reused after source/profile, manifest, video and
endpoint checks. The v7 output policy adds deliberate Draft/Review/Export sizes
(480/720/1080 short edge, orientation-aware), compact H.264, a maximum two-second
closed GOP and fast-start metadata. Export is requested explicitly. These are
SDR Rec.709, 30-fps, 8-bit 4:2:0 assets; 1080p does not make them lossless, HDR,
alpha-capable or native editable NLE effects.

**Storage ownership:** Completed outputs and bridge originals are retained job
artifacts. Temporary prepared clips, native/flow NUTs and output partials are
cleaned; the ledger reconciles eligible leftovers and old orphan UUID roots.
Provider receipt partials remain diagnostic evidence, not generic scratch.
New measurements distinguish output/original/retained/scratch bytes. Free-space
and scratch checks are monitored ceilings, not filesystem reservations. The
per-source retimer still limits native/flow scratch to 1 GiB and four minutes.
Distinct deliberately retained variants can continue growing storage.

Code anchors:
[contracts](../../pipeline/transitions/contracts.py),
[retiming](../../pipeline/transitions/retiming.py),
[jobs and artifact verification](../../pipeline/transitions/jobs.py),
[encoding](../../pipeline/transitions/encoding.py),
[storage guard](../../pipeline/transitions/storage.py),
[cleanup](../../pipeline/lab/cleanup.py),
[transport](../../web/features/transitions/transport.ts),
[comparison player](../../web/features/transitions/ComparisonPlayer.tsx).

## Recommended order and acceptance checks

1. **Make the existing rendered audition dependable.** Keep the saved result
   playing while controls or new jobs change. Wait for both comparison videos
   after seeking, preserve the music cue, stop cleanly on buffering/error, and
   cancel stale transport operations. Verify cold load, repeated seam loops,
   frame steps, hidden-tab return and rapid variant changes on actual media.
   Track dropped frames and seek readiness separately from render wall time.
2. **Reuse finished local work before adding more persistent caches.** Identical
   source identities, windows, framing, timing, quality and renderer must return
   the verified saved artifact. Changing any of those must invalidate reuse.
   Missing/corrupt files must produce fresh local work rather than a broken
   preview. Paid generation is a different lifecycle: a preserved request
   identity must never silently submit another charge.
3. **Keep Draft the exploration path and make delivery deliberate.** Audition
   several small variants, then render the chosen source recipe at export size.
   Never upscale a draft and label it a new source render. Validate endpoint
   framing, exact output cadence, color tags and join timing at export size.
   Compare compact output against a reference for halos, banding and fine detail;
   smaller files alone are not a quality pass.
4. **Expose storage without silently evicting creative work.** Reclaim owned
   scratch after success, failure and cancellation; retain originals, receipts
   and completed outputs until a deliberate lifecycle action applies. If users
   accumulate many discarded variants, the next justified feature is a reviewed
   retained-results deletion/retention control showing reclaimable bytes and
   dependencies. Browser-only favorites cannot safely serve as server-side
   eviction protection. No whole-film proxy copies are needed for this pass.
5. **Measure before adding a new codec or GPU path.** Compare Draft, Review and
   Export on the same selected footage: preparation time, interpolation time,
   compositor time, output bytes, peak scratch and cold/warm playback. A shorter
   GOP trades some compression for seek access; it is not all-intra media or a
   zero-latency guarantee. Add source-proxy reuse, a mezzanine export or GPU
   rendering only after a concrete bottleneck and a bounded storage/quality
   experiment establish that benefit.
6. **Gate editor integration on timing and quality, not preset count.** Carry
   source-time maps and explicit overlap/handle semantics into the editor's
   timeline contract. Keep source evidence unchanged. Validate music alignment
   and duration after several consecutive transitions. Arbitrary speed curves,
   mask tracking and higher-quality neural interpolation are separate extensions,
   not prerequisites for proving these bounded recipes useful.

No claim here ranks the lab above either NLE, certifies proprietary algorithm
equivalence, or promises storage will stay constant while new results are saved.

## Implementation review — September 16

Accepted behavior is recorded in
[ADR-0073](../decisions/0073-compact-transition-edit-assets-and-playback.md).
The full backend suite passed **2,923 tests**, with 11 skips and three dependency
deprecation warnings. All **423 frontend tests**, TypeScript and the production
build passed. Focused regressions cover exact completed reuse, corrupt/missing
artifacts, cancellation/ledger rechecks, low space before provider submission,
receipt preservation, byte accounting, seek/buffer hold and cached-video readiness.

The read-only before audit found **40 saved transition jobs / 70.5 MiB**, including
one import and two historical failed jobs, with **zero leftover scratch** and no
exact completed duplicate groups. There was no reason to delete retained work.
An explicit 1080p export invoked again through the browser returned the same
completed ID and a reuse notice. Paid generations and imports retain their
separate ownership; this reuse path never makes another provider call.

Two saved export checks:

- [1080p Rush whip](http://localhost:3000/lab/transitions?render=6359ce56-d3a4-4ed9-a371-fc0e2e79da42):
  1920×1080, 123 frames / 4.10 seconds, **9,149,123 bytes (8.7 MiB)** for the MP4,
  9,946,569 bytes including its endpoints and receipt. About **30.84 seconds**
  worker time during concurrent validation; zero retained scratch.
- [Portrait defocus check](http://localhost:3000/lab/transitions?render=486545d6-e470-4573-af7f-b448261174c6):
  1080×1920, 135 frames / 4.50 seconds, **5,151,855 bytes (4.9 MiB)** for the MP4,
  5,698,610 bytes including evidence. About **24.28 seconds** worker time; zero
  retained scratch. This checks export geometry, not a preferred portrait crop:
  the original Fill setting tightly crops these close-ups and merits reframing.

Both preserve the parent native endpoint timestamps and JPEG hashes, exact
output frame counts and cadence, Rec.709 tags and square pixels. Every decoded
keyframe gap is at most 60 frames. The encoding regression also verifies that
MP4 fast-start metadata precedes media data and that seeking starts near the
requested position. Visual seam strips were inspected at export size; no new
recipe or timing change was introduced by this encoding pass.

A three-example codec experiment compared the prior fast settings, medium
encoding with one/two-second GOPs, and the chosen fast/two-second GOP. Against
the same decoded saved references, the chosen profile added about **1.1–9.3%**
bytes (roughly 42–50 KiB here), while aggregate SSIM changed by less than 0.0001
from the re-encoded baseline. Those small-sample metrics are compression checks,
not proof of taste or equivalence to a source master. Medium encoding did not
offer a consistent enough gain to justify its added work. Preserving quality
and avoiding duplicate renders are the storage choices; shorter GOPs are a seek
access tradeoff, not a compression saving.

Browser checks confirmed deferred film loading under endpoint stills: both
source videos initially had no attached URL. Deliberately playing A attached
only A, played its selected window and stopped; B stayed unloaded. The exported
and 720p review panes played together with observed readyState 4 and media times
within one millisecond on the sampled check. Frame stepping left both at the
same timestamp. A cached pane becoming ready after a seek initially left Play
disabled; the discovered event-handling gap was fixed and covered by a regression.
The final 390-pixel layout had no horizontal overflow. This is a browser smoke
check, not a sustained dropped-frame, audio-clock or NLE performance benchmark.

Evidence lives in `.tmp/transition-nle-review/` (codec receipts, export inspection,
frame strips and test/build logs), with the initial storage inventory in
`.tmp/tstorage/audit-before.json`. A separate attempt to remove generated pytest
fixture directories was blocked by automatic approval review with only the
reason “blocked by policy”; no fixture deletion ran. About **686 MiB** remains
in the explicitly measured `.tmp/tmotionfull`, `.tmp/tm6full`, `.tmp/tr6`,
`.tmp/tr9`, `.tmp/tnle1` and `.tmp/tnlefull` test directories. These are development
fixtures, separate from retained transition assets and the working runtime
scratch cleanup. Original media, AI receipts and saved experiments were preserved.
