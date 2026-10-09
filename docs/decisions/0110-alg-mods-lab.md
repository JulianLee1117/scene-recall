# ADR-0110: Alg Mods Lab: a treatment session for tracked painted dots

- Status: Accepted
- Date: 2026-10-07
- Extends: ADR-0070 (projectless sessions on the editor worker), ADR-0051
  (shared Lab navigation)
- Supersedes: None
- Superseded by: None

## Context

The owner studied two artists whose work redraws a moving picture as marks:
alg.comp.mod (Shi-Tomasi corners moved by Lucas-Kanade flow, drawn as dots on
black) and the painter Yoon Hyup (dense flat-colour dots and dashes on a navy
ground, bigger on the lights, the scene formed by density and colour). They
asked for one Lab dedicated to such treatments, tried several (time stripes,
quadtrees, dots) on iconic shots, judged none successful, and chose to master
one: feature dots. Seven rounds of offline renders on a review page settled
the look. The owner's verdicts drove two redesigns: dots that parked at the
frame edge ("if they exit the scene they should exit the scene") replaced
per-dot corner tracking with a surface-motion model, and the bar that each
frame must read as a painting of the shot replaced texture-driven seeding with
placement by light and colour.

The rounds ran through a command-line renderer. The owner expected the
treatment under Labs and could not find it there.

## Decision

Register Alg Mods as a projectless session at `/lab/alg-mods`, entered from
Labs with the shared workspace header, on the pattern of ADR-0070. One
indexed film window of 0.2 to 12 seconds, chosen through the shared source
browser, and one treatment from the family under study, each with bounded
parameters: painted dots (styles `vivid` after Yoon Hyup and `pastel` after
alg.comp.mod), time stripes, a quadtree, or a mosaic. The workspace is a board,
scenes down and treatments across, each cell the latest render of that pair;
opening a cell plays it, exposes that treatment's controls and lists the
pair's variants. A new scene is a row, a new treatment a column, because the
owner asked that the whole family be simple to navigate and scalable to
explore, not only the one being mastered. Explicit renders enqueue
`algmods-render` jobs in the existing durable ledger and run on the editor
worker. Each job freezes the source identity (indexed first/last 4 MiB
digest, size and mtime), the window and every parameter under the renderer
version `algmods-v9`; exact completed requests are reused after their
artifacts verify, and earlier variants stay listed for comparison. Output is
a 720 by 1280 H.264 preview, optionally with the original beside it, plus a
manifest. Cancellation and cleanup reuse the worker contract; intermediates
are removed on teardown.

The renderer is `pipeline/algmods/`. Dots are paint marks on the scene's
surfaces: each moves with a dense optical-flow field (OpenCV DIS, with a
forward-backward trust map) sampled at the dot, or with the frame's global
similarity fit where the local flow is untrusted or in the border band, so
nothing freezes; a dot that crosses the frame edge leaves at once; one born in
the edge band arrives full-size, one born inside grows into place; one whose
spot has lost both local flow and texture shrinks away; a hard cut (untrusted
flow or a photometric mismatch after warping) clears and repaints the field.
Placement follows a paint-energy map (light, or saturation times value, plus
the segmented subject): dense and large on lights and inside lit areas, sparse
and muted on dark masses, nothing in the voids; a light's core whitens; a mass
takes one colour, sampled coarse and snapped by hue toward the palette measured
from Yoon Hyup's paintings. Dots never overlap at birth. Subject detection
(RF-DETR, the `measure` extra) centres the 9:16 crop, raises the subject's paint
energy and can keep it real; without it the crop is centred and that is recorded
in the receipt. OpenCV is the `algmods` extra. Neither is a default dependency.

The mosaic rebuilds the shot from other shots. Its tile bank is every indexed
keyframe reduced to an 8x5 colour block (`pipeline/algmods/tiles.py`, version
`tiles-v1`, built once with `python -m pipeline.algmods.tiles build`, stored at
`<assets>/algmods/`, backfillable from the keyframes alone). What makes it more
than a photomosaic is the choice of tiles: the whole library, the host film's own
moments (a film made of itself), or the shots a library search returns (a hand
made of hands). A search-sourced mosaic pins the shots the search returned at
request time, in the API, so the worker never searches and a re-run is exact.
Tiles can play their preview clips; the real shot can shatter into tiles and
re-form; the host film is never a tile of itself.

Where a treatment applies is a region, a small plain-data spec resolved once per
window into per-frame masks (`pipeline/lab/regions.py`): whole frame, the
segmented subject (classes, largest instance, grow or shrink), its background, or
a box, temporally smoothed. It is the Lab's reusable masking primitive: the
mosaic uses it now, the dots and the effects pass can, and an agent can write it.
Without the `measure` extra, subject regions resolve to the whole frame and the
receipt says so.

A graft is the second shared primitive (`pipeline/lab/grafts.py`): a
landmark-aligned piece of another shot set on the host, described as plain data
an agent can write: the window (a feature box in landmark units, the face, the
subject, the frame; hard or feathered), the donor (chosen by aligned structure
and tone from a pinned pool, or named), the light it takes, and its timing and
entrance (cut, grow, slide, fade). Landmarks come from the moments index (ADR-0099),
faces first (eyes and nose). Memory patches, a composite face and a face-scoped
cut-in are plans for the same renderer; a plan is authored like a phrase or placed
on beats, never generated by a metronome. The composite and patch code in
`pipeline/algmods/composite.py` supplies the landmarks, donors and feature boxes.

Alg Mods is a treatment lab, not an editor effect. It adds no effect kind to
the harness, no search model, no embedding profile, no shared evidence
derivation, no paid calls and no library-wide processing. Graduating the
treatment into an edit effect (ADR-0106 family) waits for a passage where the
owner wants it on the song clock.

A region may also be a depth layer: `near` and `far` take the nearest share of
the frame by monocular depth (`pipeline/lab/depth.py`, Depth Anything V2 small,
normalised across the shot and smoothed in time). Every per-shot treatment
(dots, stripes, quadtree) carries a `live` region through which the film shows
unchanged, hard-edged: a live layer inside a treated world (a subject dancing
inside a painting; the nearest rides live under a painted sky). The live layer
is composited after the treatment by the shared renderer, so it cannot drift
between the Lab and the CLI. People are cut by a video matte (`pipeline/lab/matte.py`,
Robust Video Matting, TorchScript, downloaded on first use) when a region names
only the person class, so a live subject has hair-level edges instead of a
detector's blob; other classes keep the detector. Depth-displaced time (each pixel from an earlier
frame by its distance) was tried and rejected; cross-scene figure composites
(limbs, pose ghosts, world cuts) likewise. The craft doc records why.

## Consequences

- The dots treatment is reachable from Labs, tunable on any indexed shot, and
  every variant is a durable, reproducible job with a receipt.
- The command-line renderer remains for batch study; the Lab and the CLI share
  one renderer module, so the look cannot drift between them.
- Time stripes and quadtrees are explorable in the Lab beside the dots and the
  mosaic. Only the dots and the mosaic have had design rounds.
- The region primitive is shared Lab code; a treatment that needs masking takes
  a region spec rather than growing its own segmentation path. Depth regions
  download a small model on first use and need the GPU for speed.
- The editor worker needs the `algmods` extra (and `measure` for subject
  detection). A worker without them fails the job with the install command.
