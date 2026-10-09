# Alg Mods craft notes

What makes each treatment work, learned by rendering and looking. These are the rules a
future round, editor or agent should start from, not rediscover. The round-by-round log
with the owner's verdicts is in the reference folder
(`Videos/reference/alg.comp.mod/dots-mastery.md`); this file keeps only what held.

Code: `pipeline/algmods/` (renderer, treatments, tile bank, experiments),
`pipeline/lab/regions.py` (masking spec). Lab: `/lab/alg-mods`. ADR-0110.

## Judging

- Judge full-size stills before rendering a clip. Thumbnail strips hide every failure that
  matters (a crop that lost the subject, a sleeve tiled instead of a hand).
- The bar: each frame reads as a picture of the shot you would hang. Then: does motion
  behave the way an eye expects, is anything flickering, is anything random where the
  object has structure.
- Keep a fixed regression set and look at all of it every round. A tweak for one scene has
  broken another more than once (one colour per subject fixed the taxi, broke the dancers).

## Painted dots (after Yoon Hyup and alg.comp.mod)

Mechanics that generalised and should not be tuned per shot:
- A dot is a paint mark on a surface. It moves with a dense flow field sampled under it
  (DIS at half resolution, forward-backward trust); where local flow is untrusted or within
  3% of an edge it moves with the frame's global similarity fit. Local flow only where it
  departs from the global motion by more than 1 px (real parallax); otherwise rigid surfaces
  move as one and nothing random-walks.
- A mark that crosses the frame edge leaves. One born in the edge band arrives full-size;
  one born inside grows in over 5 frames; one whose surface is gone (both flow and texture
  lost for the grace) shrinks out. A cut (untrusted flow, or a warped-photometric mismatch
  on the raw gray over 40/255) clears and repaints.
- Never overlap: non-overlap at birth; crowded marks yield their share of the gap; marks
  squeezed below 60% for longer than the grace are dropped (shrinking surfaces).
- Fill on a hex lattice with 0.18-pitch jitter that rides the global motion; corners and
  edges first. Random scatter reads as noise on anything with structure.

Where marks go and what colour they take (the readings):
- Placement follows a paint-energy map: max(luma^0.8, saturation x value), blurred, with
  the segmented subject raised to 0.55 so the subject is always painted. Below 0.3 the
  chance falls as ((e - 0.3)/0.7)^1.75; dark texture gets 25%; natural energy below 0.15 is
  void (black stays black, including black clothes).
- Colour follows the spot's own light, never the subject boost: floor 0.12 + 0.6 x energy,
  so black trousers are dark ink and a white shirt cream. Masses sample a 10 px blur
  (coherent colour), light cores sample fine; a light core is bright against a 25 px
  surround (not merely bright: a pink wall in daylight is not hot). Subject colour is a 5 px
  blur confined to the mask. Snap toward the measured palette by chromaticity and value,
  mix 0.65.
- Size: radius 0.75% of width, +40% x energy, +60% x depth inside lit areas, size jitter
  0.12. Dots are uniform within a region and bigger on lights, not random.
- The 9:16 crop must keep the subject inside and otherwise follow the light.

Shots that suit it: lit, coloured subjects (lanterns, neon, a spotlit singer, candles,
Christmas lights, a yellow dress at dusk, fireworks, skylines). Dark figures and cars in
front of lights read as holes; one flat hue (a red stage) flattens; fine lettering
dissolves while large lettering reads. Black-and-white films get an arbitrary palette.

## Cross-film mosaic (after alg.comp.mod's video-as-pixel)

- A colour-matched photomosaic at 1,600 tiles is a texture, not an idea. The point is what
  the tiles are: the film's own moments, or the shots a search returns (a hand of hands,
  a sky of sunsets, a face of faces). Choose the set by meaning; colour is the tiebreaker.
- Tint each tile toward its host cell's tone (0.5 for scenes, 0.7 for faces). This is what
  makes the host picture emerge; without it the result is a jumble whatever the matching.
- Columns 12-18 at 720 wide. Close-ups (faces, hands, suns) stay identifiable down to about
  40 px; wide shots do not read below about 70 px. The host needs 16-18 columns for a face.
- Filter the set: no flat frames (block std < 0.035), no opening titles or end credits
  (first 150 s, last 420 s), no captions that say title, logo, credits, text; crop letterbox
  bars out of tiles; one tile per shot within a 3-cell window; ties within 25% broken at
  random so a gradient is a crowd, not wallpaper.
- Where to tile: tile the subject when the subject is the concept (face of faces, hand of
  hands) and keep the rest real; keep the subject real when the subject is the meaning (a
  silhouette) and tile around it, composited by its soft mask so the outline is true.
- Persistence: hold 8 frames, swap when the picture under a tile drifts by 0.1; moving
  tiles (each cell plays its preview clip) make the surface alive without swaps.
- Arrivals: real for a bar, shatter in from an origin on a beat with a per-cell pop, play,
  re-form. Tiles should never just be there; the picture should arrive.
- Failed: zooming through a tile (stiff, low-res landing); a dark skyline from dark tiles
  (nothing to see); the Taxi Driver corner from itself at 10 columns (too dark, too coarse;
  at 12 with tint it becomes a moody texture, not a legible picture); a hand that is not in
  the 9:16 frame at that moment.
- Coherence at a paused frame needs three things at once: a host that is a bold
  light-shape (a fireball, a lit face against dark, a neon sign on black), a tile set tight
  in meaning AND colour (fires for fire), and the host's light carried into every tile
  (tone transfer: luma mean and contrast from the host cell, a share of its colour). A flat
  gradient (a sunset sky) cannot be made coherent: tiles have no structure to follow, so
  their own content wins and the result is a gallery. Keep gradients real; tile the shape.
- Work at the full frame at native resolution (``aspect=native``). The 9:16 crop takes a
  quarter of a widescreen frame and upscales it 1.6x: structure and sharpness are gone before
  a tile is placed.
- Cells should follow the picture (``layout=quad``): large where the host is flat, small along
  its edges. In flat cells the tile takes the host colour almost entirely (there is no
  structure to preserve); in detailed cells it keeps its own hue.
- Near-black frames are never tiles; flat tiles are fine for flat host cells (a plain sky
  for a plain sky), so only near-featureless tiles are dropped (block std < 0.012).
- A search-sourced set must be pinned at request time so a re-run is exact; the worker
  never searches.

## Composite face (feature-aligned mosaic)

The intelligent version of "a face of faces": cells anchored to the features, each filled
by the same region of a different face warped so its eyes and nose sit on the host's.
- Landmarks come from the moments index (COCO keypoints: nose, eyes), for the host per
  frame and for every donor keyframe. No new detector. About one in five library
  instants shows a usable face; a shot that is only a dark face (Apocalypse Now) has no
  pose and cannot host.
- Cells are in face units (interoculars from the eye midpoint, down from the eye line):
  each eye, the bridge, the nose, the mouth and the chin are single cells so the feature
  a donor gives is whole; cheeks, temples and brow are larger. Sizes follow the face, not
  the luma; this is what "intelligent cell size" means here.
- Donors: only big faces (interocular at or above 6% of the keyframe width), level eyes,
  captions that say face, close-up, eyes, portrait or profile. A donor may be enlarged at
  most 2.5x. The donor's frame must cover the cell (coverage warp), else the cell gets
  reflected garbage.
- Choice per cell, once per clip at 480p: structure distance of the aligned donor's luma
  against the host cell (both normalised, so light-invariant) plus 6x a chroma term (skin
  must meet skin); a donor is used once; pick at random among the best three. Then tone
  transfer (light 0.85, colour 0.35) and a one-pixel hairline of the real picture.
- The faces follow the head: the similarity transform is recomputed per frame from the
  host's interpolated landmarks; the assignment never changes within a clip.
- Alignment with a uniform grid and random donors did not read; alignment with feature
  cells and matched donors did. The grid was the problem, not the warping.

## Memory patches (after ohnohanajo, reel DPwyDmTEaA4)

Her piece: one small hard-edged rectangle of a childhood photo set exactly on one feature of
her present face (mouth, one eye, brow), one at a time, held a few frames, then gone;
between them the real face or a full childhood cut-in. Sparse, rhythmic, a sticker, never a
grid. The owner's verdict on the composite grid: borders touching in a perfect square
looked wrong, a woman's eyes on a man looked off, everything arrived at once and tracked
too long. The patches answer all four.
- Feature boxes in face units (eye line at y = 0, down positive): eye (-1.2..-0.3 / 0.3..1.2,
  -0.38..0.38), eyes (-1.25..1.25, -0.42..0.42), nose (-0.45..0.45, 0.3..1.0), mouth
  (-0.72..0.72, 1.0..1.6), brow (-1.3..1.3, -1.1..-0.4). A mouth box at 1.45..2.2 lands on
  the chin; checked on a still before any clip.
- Donors: same gender as the host by caption words, big faces, level eyes; the warped
  eyes and nose must land within 0.1 interocular of the host's (alignment tolerance), else
  the donor is skipped. Chosen per patch by aligned luma structure plus skin tone.
- Schedule on a beat grid (120 bpm): a bar of real face first; each patch holds 6-12
  frames; a quarter of beats rest; every fifth event is a full aligned cut-in of the whole
  donor face for 0.6 beat. A donor is used once.
- Light: half the host's light into the patch, none of its colour (it should read as a
  sticker, not a graft).

## Grafts (the reusable primitive under the face pieces)

`pipeline/lab/grafts.py`. A graft is four decisions, each plain data an agent can write:
window (a feature box in landmark units, the face ellipse, the subject mask, or the frame;
hard or feathered), donor (chosen by aligned structure and tone from a pinned pool, or
named), light (how much of the host's light and colour it takes: a sticker takes half the
light and no colour; a graft takes most of both), timing (start, hold, enter and leave:
cut, grow, slide, fade; ramps). Memory patches, the composite face and face-scoped cut-ins
are all plans for the same renderer.
- The owner's notes on round 6: a full-screen cut-in "covers the whole screen" (window
  should be `face`, the donor in the host's framing); entrances all alike read as "novice
  iMovie" (vary enter/leave and ramps, use `grow` and `fade` as well as `cut`); the same
  intermittent frequency is not tasteful (write a phrase: quick swaps, a long hold, a rest,
  a hit; or `schedule_on_beats` with a pattern whose holds are in beats).
- A plan should be authored like a bar of music, not generated by a metronome.
- A graft is a sticker: the donor is warped once, where the graft is placed, and afterwards
  the whole patch (content and edge together) rides the host's motion by one similarity from
  the placement landmarks to the current ones, with the landmarks damped (0.6 of the previous
  frame) against detector jitter. Re-warping the donor every frame made the content swim
  inside a steady window, which the owner saw at once.
- The `face` window is the face oval (1.3 x 1.7 interoculars, centred 0.55 below the eye
  line), never the face box: on a close-up the box is 40% of the frame and a cut-in reads
  as a full replacement. Check a window's share of the frame before trusting it.

## Temporal remix (time stripes, motion echo, time slice, beat time)

`pipeline/algmods/temporal.py` plus the stripes mod in `mods.py`. Every pixel is the shot's
own, so coherence is free; the work is shot choice and the mask.
- Choose shots from the measure pass, not search: camera `dominant == static`, one main
  subject of class train/car/person with size 0.05-0.6 of the frame and lateral `dx` above
  0.01 (stripes); a big person with high motion energy and at most two people (echo); high
  motion energy with no dominant subject (time slice). The CSV of 229k measured shots is
  cheap to filter; search captions were wrong for this half the time.
- Stripes, what his train actually is (read from his frame at full size, not the sheet):
  thin bands ALONG the train (lines through the vanishing point, converging with the
  perspective), and within each band the lag changes ALONG its length in blocks, so a
  carriage is shredded into pieces that each arrive at a different time. A fan centred on a
  point with one lag per wedge is a sunburst, not motion (the owner: "a cheap effect of one
  point with lines coming out"). Use `pattern=blocks`: a (band, piece) lag table, ~30% of
  pieces on time, pieces roughly square on the subject, hard edges.
- Stripes: the vanishing point is found from the subject's own flow (least-squares
  intersection of motion lines inside the mask; parallel lines mean lateral motion, use
  bands across it instead). 80-90 fan stripes, lag 14-16 frames interleaved, ramp in over
  20-30 frames, feather 2 px. Works on trains and locomotives at full frame; the subject
  mask must be the vehicle class only or the fan eats the platform.
- Stripes need the right geometry and speed: a subject crossing LATERALLY (parallel bands)
  or receding with the vanishing point far outside its body; a train coming at the camera
  puts the vanishing point inside the train and any band scheme becomes a dartboard. The
  crossing must be fast: lag displacement = speed x lag, so at 0.12 frame-widths/s a 1.2 s
  lag moves a piece one carriage; slower than ~0.1 widths/s nothing reads.
- Motion echo: trail only where flow speed exceeds ~3 px/frame inside the subject mask,
  decay 0.9, the live subject always on top. Reads on a spin with still torso (Frances Ha);
  too subtle on an overhead dancer whose whole body moves. Passes on Kill Bill's dance,
  Metropolis's veiled dancer and a La Haine punch; fails handheld with the figure filling
  the frame (Gladiator). Wants a static camera, subject under ~40% of the frame, limbs much
  faster than the torso.
- Time slice: a straight seam moving at constant speed reads as a VHS tracking line (owner),
  whatever the offset. Dropped unless the seam follows a shape in the picture or a beat.
- Beat time: mechanically a frozen half that jumps on beat frames from the Lab's rhythm
  cache (`beats_from_rhythm_cache`); visually it needs a quiet background and one clear
  subject. A busy street with the subject small fails even when the jumps are measured.
- Regions: with several people, limit the person mask to one figure with a box; "largest"
  alone picked the wrong one in a crowd.

## Regions (masking)

A region is a plain spec resolved once per window into per-frame masks: whole frame, the
segmented subject (classes, largest only, grow/shrink), its background, or a box. There is
no hand class: a hand needs a box placed on it, which is what a masking tool is for. Masks
are temporally voted over 3 frames so a one-frame miss does not flash.

## Pre-render checks worth automating

- Subject coverage: below 3% the crop must still keep the subject; above 60% a
  "subject only" region is nearly the whole frame.
- Tile set size: fewer than 200 usable tiles will repeat visibly at 12+ columns.
- Host luminance: a mostly-dark host needs a set with dark detailed tiles, or it will not read.
- Lettering: estimate stroke width against dot radius or cell width before promising it.

## Cross-scene figure work (assembly, gesture memory, world cuts) — what failed and why

Tried in October 2026; the modules were removed after the owner's verdict. Skeletons come
from the moments index (`pipeline/matching/moments`) if ever needed again. Do not repeat these shapes.

- A figure rebuilt from other shots' body parts (limb capsules, bone-aligned donors) reads as
  pieces pasted on a body. There is no reason the eye can see for which piece goes where, so it
  reads as a drag-on collage however good the alignment is.
- A pose-aligned ghost of another scene over the figure is a double exposure. The selection
  (same pose elsewhere in the film) is invisible in the result, so the depth is not on screen.
- "She stays, the film cuts around her" is legible only when tone and grain match (Metropolis,
  black and white, same stock): the cathedral-of-sins cut passed. In a colour film a figure lit
  for a dark bedroom standing in a sunlit garden is a cutout, which is the cheap look exactly.
- The moments index matches pose, not identity. The "other self" is often another actor in the
  same stance. That can be a true rhyme (Freder mirroring Maria) but it cannot be promised.
- Pose matches ignore framing: gate by figure extent (0.65 to 1.5 of the host's) or the donor
  world is blown up three times. Gate the torso similarity to under 25 degrees of rotation and
  over 92 percent frame coverage, or worlds flip and leave black wedges.
- The donor's own figure never hides fully under the host's; inpainting the slivers smears.
  Films with burnt-in subtitles (The Zone of Interest) leak text through donor frames; the
  bottom-band white-on-dark check catches the picked frame but not the rest of the clip.
- The owner's verdict on all three: "these just look like cheap effects u can drag on in a video
  editor. there's no depth to it." Depth has to be visible in the frame, not in the provenance.

## Live layer: film inside a painting (passes, October 2026)

The dots pass on their own scenes; they pass harder when part of the frame stays film.
`live` is a region (subject, background, near, far) composited after the treatment with a
1.5 px edge. Judged at full size:

- La La Land hilltop dance 2148-2152, live subject: the couple dancing inside a painted night.
  PASS. The eye reads it in one frame: real people, painted world.
- Before Sunrise Prater 1836-1840, live nearest quarter by depth: the sign, the coaster and the
  lit kiosks stay film under a painted sky and wheel. PASS. Depth makes the split physical.
- Lawrence of Arabia silhouette 4397-4401, live subject: a real silhouette against a painted
  sunset. PASS, the strongest of the three.
- Rear Window brick 2975.5-2979, live nearest fifth: the nearest fifth is only the dark window
  frame, so nothing is gained. FAIL. Pick the live layer for what it keeps, not by a number.
- Taxi Driver neon 2643.6-2647.6, live subject: the figures are too small to matter. Weak.
- The Godfather gate 3700-3706: no subject found; sparse dots only. FAIL.

Rules: the live region must hold something worth keeping (a face, a figure, a lit object) and
cover roughly a tenth to a third of the frame; a hard edge, never a feathered blend (a soft
band reads as a see-through overlay, which the owner rejects); subject regions for people,
depth regions for scenes without a subject.

## Depth (`pipeline/lab/depth.py`)

Depth Anything V2 small: a dozen 720p frames in under a second on the GPU, 0.35 GB, flicker
half a percent of range frame to frame, sharp silhouettes even on 1927 stock. Normalise across
the shot, not per frame, or bands drift. Depth-displaced time (far pixels from older frames, removed) was judged on a forward car POV (Vertigo: nothing visible but a seam), a
diagonal steam train (The Handmaiden: carriages shift a hair, a slab of old smoke against the
sky) and a hallway walk (a double-exposed face). Not an effect; a train's depth runs along its
length, so depth bands are the 2D shred's bands with mask errors added.

## Dots round 10: a background worth looking at (October 2026)

The owner on the live-layer clips: the dots behind the figure were "all kinda similar, not
that compelling or distinct", the masks imperfect, the ground "some shade of dark blue".
Four changes, each judged before and after at full size on La La Land, Lawrence, the Prater,
the fireworks and the No Country skyline:

- Colour spread (`palette_spread` 0.6): a dot draws among the nearest three palette colours
  with weights by distance, and a candidate more than 0.03 away in the chromaticity measure is
  out. A sky is cobalt, lavender and sky blue interleaved, which is his skies. Without the cut
  a sunset sky got green dots; one stray green per frame still slips through and is accepted.
- Bigger marks on flat areas (`flat_boost` 0.6): radius grows by up to 60 percent where the
  local gradient is low, so a sky gets big marks and a lit façade small ones. Size jitter back
  to 0.35 now that marks never overlap.
- Dashes (`dash` 1.2): where the structure tensor is coherent (coherence above 0.45, weighted
  by gradient strength so flat areas never dash) the mark is an ellipse of the same area,
  stretched along the edge tangent. Ferris spokes, firework rays, a horizon of lights and water
  reflections all turn into dashes along their own direction; a plain sky stays round.
- Ground (`ground` scene): the median of the frame's darkest quarter, lifted to value 0.09 with
  most of its saturation removed. A warm film gets a warm near-black, a blue dusk a blue one,
  a neutral scene a grey one. Navy and black remain as choices. A ground at value 0.11 with
  added saturation read as a coloured card; keep it a hint.

Masks: people are cut by Robust Video Matting (`pipeline/lab/matte.py`) when a region names
only the person class. The detector's instance mask on the La La Land dancers was a blob
around the man's legs; the matte follows hair and shoes and is stable across frames. The live
layer composites with the matte's own alpha and no extra blur.

## Dots round 11: open skies (October 2026)

Owner on round 10: "the background in a lot of this just looks like a gradient of dense large
dots", a matte outline around subjects "ruins the impressionistic illusion". Findings:

- The dot count is set by the non-overlap packing, not by the candidate count. Dropping 70
  percent of lattice sites on a flat area changed nothing, because the survivors still packed
  the same area. What opens a sky is `flat_gap`: marks on a flat surface keep an extra distance
  (1.5 times their combined radii at full flatness), so a sky is a few marks on the ground.
  Lawrence's sky went from 461 marks in the top third to 105 and now reads as a painting.
- Detail must be measured on a grain-free image (blur 2 px before the gradient) on an absolute
  scale (gradient 0.04 per px is fully detailed). A scene-relative scale called a smooth sunset
  sky detailed because nothing else in the frame had edges.
- The matte halo was a sliver of real background inside the soft alpha: harden the matte
  (transition narrowed to a quarter, eroded 2 px) before compositing. No halo on the dancers.
- A tiny live figure (Lawrence, a twentieth of the frame) still fails: the marks cluster around
  the silhouette and read as an outline. Keep the live layer for figures that take a tenth of
  the frame or more; a small figure is better as a gap in the paint.

## Painted masses (October 2026): the dots were the wrong grammar

Owner on round 11: ellipses are interesting but "it no longer feels like the original
sophisticated Yoon Hyup paintings", subject colours indistinguishable from the background, and
"how after all this testing u dont have visual capability of seeing if its visually working".
The fault: judging each render against the previous render and my own rules instead of
against a painting in the same sheet. Beside a painting, every dots render fails the same way.

What his night paintings are, read from eight: objects, not texture. Each lit mass (a facade,
a sign, a reflection) is one flat palette colour. Marks are pills in rows along the mass's own
direction (horizontal window rows, vertical towers, wavy verticals for reflections). Black is
left black. Unlit towers are navy silhouettes with their windows bright. Single lights are
bright round dots. Nothing is a gradient and nothing samples a pixel.

A per-frame prototype (removed) built that: lit mask (value above 0.07 after a 2.5 px
blur, floor raised to the frame's 40th percentile), lights closed into implied masses (a tower
behind its windows), k-means on Lab colour plus position split into connected parts, one
colour per mass (bright palette if 12 percent of it is lit, else navy/steel/grey-purple,
never brown or green), one direction from the structure tensor snapped to horizontal or
vertical, pill rows (width 1.1 percent of frame width, pitch 1.45 widths, lengths log-normal
around 2.2 widths), pills navy between windows and bright on them (dilated value at pill
scale), thin masses (tubes, script) follow their local direction. Pass: No Country skyline.
Fail: Belmore neon, the wall behind the tubes splits the script's colour. Not yet moving: the
masses must be carried by the tracker (lattice riding the flow) before a clip exists.

Rule for every round from here: the judging sheet holds a reference painting at the same
scale. Rules I wrote are not the reference.

## Close of the exploration (October 2026)

Owner on the painted masses: "so dense and straight and evenly spaced it looks robotic", colours
not compelling, "to get this good we'd need a tuned model", "these experiments are not really
going anywhere". Exploration paused. What held: dots round 9 (vivid), shredded train and car,
motion echo, the live layer as a control. The hand-built approach to a painter's look tops out
at dots round 9; a learned model is the next step if the look is ever wanted.
