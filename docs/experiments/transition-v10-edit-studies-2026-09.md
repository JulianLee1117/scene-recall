# Transition v10: two music edit studies

Recorded **2026-09-17**. These are local creative studies, not new Lab presets or
an architecture change. Each treatment has an original-footage comparison with
identical source timing, cuts and music.

## Deliverables and shared timing

Four **1280×720, 30fps, 338-frame (11.2667s)** MP4s are packaged under
`C:/Users/julia/Videos/cinema-assets/lab/transition-studies/2026-09-17-reviewed/`:

- `01-dj-print-accent.mp4` and `01-dj-original.mp4`
- `02-winter-match-cut.mp4` and `02-walk-original.mp4`

All use the existing imported **Starjunk 95 — LX-777 (Lunar Data)** passage
**8.500000–19.766667s**. Final music packaging uses the same **−4dB** headroom for
both treatments and their comparisons; measured decoded peak is **0.758698**.
The cached Beat This! guides suggest
roughly 170 BPM; chosen downbeats map to edit frames 42, 85, 127, 169, 211, 254 and
296. These are editorial guides, not sample-accurate onset claims. Frame numbers
below are zero-based. Moving clips retain 1× timing; no overlap changes duration.

## DJ: a brief copper print accent

The sequence stays with the DJ and equipment in *La Haine*. A rear view at
2464.5s introduces the room, then the scratch detail uses source
**2470.816667–2472.250000s**. Its last displayed frame, edit frame 84, anchors the
generated copper print. Copper appears only on **frames 85–86**; the unchanged
anchor returns on **87–88**, followed by wider real performance at **89**.
Later cuts at 169, 211 and 296 give the performance room to read. Source moments
are editorially reordered, not presented as an originally consecutive action.

Two scratch variants were generated with the built-in image tool. Silver was
rejected because it added edge/focus chatter without a distinct graphic purpose.
The baseline substitutes the unchanged anchor for copper while preserving the
same four-frame hold. Frame review supports copper as an **optional accent**;
similar treatment is achievable procedurally. This does not justify a production
AI transition capability by itself.

## Walking: a synchronized winter switch

Context from *The Worst Person in the World* leads into a two-second rear walk,
source **7007.5–7009.5s**. The original occupies edit **frames 224–253**. At
**254 (8.4667s)**, the edit switches to the corresponding second of Aleph's winter
version, occupying **254–283**. Source time continues from 7008.5s with **zero
offset**: the walk does not restart or repeat a stride. A real twilight lake shot
at 7025.0–7026.8s closes the sequence from frame 284.

A generated static winter reference guided the street geometry, composition and
weather. Together with copper and silver, this batch produced **three built-in
image-generation outputs**. Aleph received the original walking video, the winter
guide and instructions to preserve the person, footfalls, camera and street while
changing weather. The returned video was already 60 frames at 30fps; its second
half was scaled from 1080p to 720p without changing time. The hard switch uses no
blur or flash to hide the join.

Frame review makes this the **clearer creative candidate**, with minor subject
redrawing still visible. Equal duration and a preservation prompt do not guarantee
identity or motion fidelity. The one-second winter interval is intentional; this
study does not establish a sustained, continuous winter environment.

## Cost, verification and limits

Runway Aleph 2 task `10943671-0608-4e78-b680-2012e7badf50` consumed **56 actual
credits ($0.56)**, the sole Runway request in this batch. The prior v9 batch's
100 credits is separate; built-in image outputs are not Runway charges. Current
[Aleph model documentation](https://docs.dev.runwayml.com/guides/models/),
[input contract](https://docs.dev.runwayml.com/assets/inputs/) and
[pricing](https://docs.dev.runwayml.com/guides/pricing/) support video plus
text/image conditioning and 28 credits/second with a 56-credit minimum.

Every frame of both 338-frame treatment/baseline pairs was decoded. Only DJ
frames **85–86** and walking frames **254–283** differed; all other decoded frames
matched. Every video timestamp equals `frame / 30`; each pair's decoded audio is
identical. The four final MP4s total **18,423,699 bytes**. Source and edit specs,
verification, private provider receipts and review contacts remain under
`.tmp/transition-v10-research/`.

Image prompt notes and selected asset paths are in
[image-prompts.md](../../.tmp/transition-v10-research/image-prompts.md).

Review was frame-based. Browser playback of local files was blocked, so **played
visual and listening acceptance remains unverified**. Raw films/music were
preserved. No production presets, heavy model weights, ingestion restart or new
editor capability resulted from this study. An earlier −2dB export still peaked
at 1.026 and was rejected. Automatic approval review blocked removal of its
incomplete preview in the older `2026-09-17/` folder; it and temporary working
renders remain retained. Only the `2026-09-17-reviewed/` files are final. Public
documentation contains no signed URLs or credentials.
