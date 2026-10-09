"""Mosaic experiments: different uses of the effect, rendered as 9:16 clips for the review page.

    python -m pipeline.algmods.experiments stills      # one frame per experiment, fast
    python -m pipeline.algmods.experiments clips       # full clips

Each experiment is a function returning frames; shared helpers crop, segment and encode.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

from pipeline.algmods import render
from pipeline.algmods.mosaic import Mosaic, TileBank, shot_times, units_from_search, zoom_through

API = "http://127.0.0.1:8000"
OUT = Path(".tmp/algmods")
W, H = render.OUT_W, render.OUT_H


def load(title: str, start: float, end: float, *, segment: bool = False, classes: list[str] | None = None):
    film_id, path = render._resolve_film(title)
    frames = render.decode(str(path), start, end)
    masks = render.smooth_masks(render.segment(frames, classes or render.DEFAULT_CLASSES, lambda _m: None)) if segment else [None] * len(frames)
    x0, y0, cw, ch = render.crop_window(frames, masks, W, H)
    frames = [render.resize(f[y0:y0 + ch, x0:x0 + cw], W, H) for f in frames]
    masks = [None if m is None else render.resize(m[y0:y0 + ch, x0:x0 + cw].astype(np.uint8) * 255, W, H, nearest=True) > 127 for m in masks]
    return film_id, frames, masks


def bank_for(assets_dir: Path, film_id: str, *, source: str, queries: list[str] | None = None) -> TileBank:
    bank = TileBank(assets_dir, exclude_film=None if source == "self" else film_id)
    if source == "self":
        bank.restrict(bank.rows_for_film(film_id))
    elif source == "query":
        units = units_from_search(API, queries or [])
        rows = bank.rows_for_units([(u["film_id"], u["unit"]) for u in units if u["film_id"] != film_id])
        bank.restrict(rows)
    return bank


# ----- experiments: each yields (name, frames, note)

def exp_self(assets_dir, still):
    """A film made of itself: the BAR corner rebuilt from Taxi Driver's own moments, tiles playing."""
    film_id, frames, _ = load("Taxi Driver", 1686.6, 1690.4)
    bank = bank_for(assets_dir, film_id, source="self")
    m = Mosaic(bank, columns=12, moving=not still, tint=0.6, grout=1)
    out = [m.step(f) for f in (frames[:1] if still else frames)]
    return "self-taxi", out, "A film made of itself: Taxi Driver's BAR corner from Taxi Driver's own shots, 10 columns, each tile playing."


def exp_hand(assets_dir, still):
    """A hand made of hands: the outstretched hand against the sky, tiled from shots of hands; the sky stays real."""
    film_id, frames, masks = load("The Handmaiden", 9465, 9470, segment=True, classes=["person"])
    bank = bank_for(assets_dir, film_id, source="query", queries=["close-up of a hand", "hands touching", "a hand reaching", "fingers close up", "a hand on a face", "holding hands", "an open palm"])
    m = Mosaic(bank, columns=16, moving=not still, tint=0.45, grout=1)
    out = []
    for f, mask in zip(frames[:1] if still else frames, masks):
        region = None if mask is None else m.cell_region(mask, H, W, where="subject", cover=0.3)
        out.append(m.step(f, region=region))
    return "hand-of-hands", out, "A hand made of hands: the reaching hand (segmented) tiled from shots of hands, toned to the hand; the haze stays real."


def exp_face(assets_dir, still):
    """A face made of faces."""
    film_id, frames, masks = load("Top Gun", 5332.3, 5334.2, segment=True, classes=["person"])
    bank = bank_for(assets_dir, film_id, source="query", queries=["close-up of a face", "a face in shadow", "extreme close-up of eyes", "portrait of a woman", "portrait of a man", "a face lit from one side"])
    m = Mosaic(bank, columns=18, moving=not still, tint=0.7, grout=1)
    n = len(frames)
    out = []
    for i, (f, mask) in enumerate(zip(frames[:1] if still else frames, masks)):
        region = m.cell_region(mask, H, W, where="subject", cover=0.35)
        m.assign(f, region)
        # real for a third of a second, then the faces arrive from the eyes and stay
        p = 1.0 if still else min(1.0, max(0.0, (i - 10) / 24))
        out.append(f.copy() if p <= 0 else m.compose(f, region=region, progress=p, origin=(0.5, 0.42)))
    return "face-of-faces", out, "A face made of faces: the person region tiled from close-ups of faces across the library."


def exp_anthology(assets_dir, still):
    """A sky made of sunsets: the silhouette stays real, the sky around it is other films' sunsets."""
    film_id, frames, masks = load("Lawrence of Arabia", 4397, 4403, segment=True, classes=["person"])
    bank = bank_for(assets_dir, film_id, source="query", queries=["sunset over the horizon", "sunset sky, orange and pink", "dusk sky over the desert", "the sun low over the sea", "golden hour sky", "sunrise over a plain"])
    from pipeline.lab import regions
    m = Mosaic(bank, columns=12, moving=not still, tint=0.6, grout=1)
    out = []
    for f, mask in zip(frames[:1] if still else frames, masks):
        m.assign(f)
        out.append(m.compose(f, keep=regions.soft(mask, 2)))
    return "anthology-lawrence", out, "A sky of sunsets: the silhouette stays real; every tile around it is another film's sunset, 12 columns, tiles playing."


def exp_reveal(assets_dir, still):
    """The real shot shatters into its mosaic and re-forms, on a 120 bpm grid."""
    film_id, frames, _ = load("Oppenheimer", 6988, 6994)
    bank = bank_for(assets_dir, film_id, source="all")
    m = Mosaic(bank, columns=12, moving=not still, tint=0.5, grout=1)
    n = len(frames)
    beat = int(round(render.FPS * 0.5))                 # 120 bpm
    out = []
    for i, f in enumerate(frames[:1] if still else frames):
        m.assign(f)
        # bar 1 real, bar 2 shatter in over one beat, bars 3-4 mosaic, then re-form over one beat
        if i < 4 * beat:
            p = 0.0
        elif i < 5 * beat:
            p = (i - 4 * beat) / beat
        elif i < 9 * beat:
            p = 1.0
        else:
            p = max(0.0, 1 - (i - 9 * beat) / beat)
        out.append(m.compose(f, progress=min(p, 1.0) if p > 0 else -1, origin=(0.5, 0.45)) if p > 0 else f.copy())
    if still:
        out = [m.compose(frames[0], progress=0.6, origin=(0.5, 0.45))]
    return "reveal-fireball", out, "The arrival: the real fireball shatters into tiles from the centre on a beat, plays as a mosaic for two bars, and re-forms."


def exp_zoom(assets_dir, still):
    """Zoom through one tile into the shot behind it, then that shot becomes a mosaic."""
    film_id, frames, _ = load("Lawrence of Arabia", 4397, 4400)
    bank = bank_for(assets_dir, film_id, source="query", queries=["silhouette against the sunset", "a lone figure against a bright sky", "sunset over the horizon"])
    m = Mosaic(bank, columns=10, moving=False)
    mosaic_frames = [m.step(f) for f in frames]
    rows, cols, cw, ch = m.grid(H, W)
    # the target: the cell nearest the figure (lower left third) that has a tile with a preview
    r, c = int(rows * 0.68), int(cols * 0.12)
    index = int(m.assigned[r, c])
    film, unit, _k = bank.unit_of(index)
    times = shot_times(assets_dir, film, unit)
    target = []
    if times:
        import lancedb
        from pipeline.config import load_config
        films = lancedb.connect(str(load_config().paths.assets_dir / "db")).open_table("films").to_pandas()
        path = films[films.film_id == film].iloc[0].path
        t0 = times[0] + 0.2
        tf = render.decode(str(path), t0, t0 + 3.0)
        h, w = tf[0].shape[:2]; cw2 = int(round(h * 9 / 16 / 2)) * 2; x0 = (w - cw2) // 2
        target = [render.resize(f[:, x0:x0 + cw2], W, H) for f in tf]
    zoom = zoom_through(mosaic_frames, (r, c), (rows, cols, cw, ch), target, blend=6)
    # then the shot we landed in becomes a mosaic of its own (same set), revealed from the centre
    after = []
    if target:
        m2 = Mosaic(bank, columns=10)
        for i, f in enumerate(target[6:]):
            m2.assign(f)
            after.append(m2.compose(f, progress=min(1.0, i / 30), origin=(0.5, 0.5)))
    out = zoom + after
    if still:
        out = [zoom[len(zoom) // 2]]
    return "zoom-through", out, "Zoom through: push into one tile until it fills the frame, land in that shot at full quality, and watch it turn into a mosaic of its own."


def exp_city(assets_dir, still):
    """A city made of cities: the skyline across the river, tiled from other films' skylines at night."""
    film_id, frames, _ = load("No Country for Old Men", 15, 21)
    bank = bank_for(assets_dir, film_id, source="query", queries=["city skyline at night", "skyscrapers lit at night", "aerial view of a city at night", "city lights across the water"])
    m = Mosaic(bank, columns=14, moving=not still, tint=0.55, grout=1)
    out = [m.step(f) for f in (frames[:1] if still else frames)]
    return "city-of-cities", out, "A city made of cities: the skyline across the river rebuilt from other films' skylines, toned to the shot, tiles playing."


def exp_composite(assets_dir, still, host=("Top Gun", 5332.3, 5334.2)):
    """A composite face: the host face rebuilt from other faces, feature on feature, following the head."""
    from pipeline.algmods.composite import CompositeFace, donor_faces, load_landmarks
    from pipeline.config import load_config
    from pipeline.lab import regions
    title, a, b = host
    film_id, path = render._resolve_film(title)
    frames = render.decode(str(path), a, a + 0.1 if still else b, height=1080)
    h, w = frames[0].shape[:2]
    lm = load_landmarks(load_config())
    shots = json.load(open(Path(assets_dir) / film_id / "shots.json", encoding="utf-8"))["shots"]
    unit_id = next(s["shot_id"] for s in shots if s["t_start"] <= a < s["t_end"])
    units = units_from_search(API, ["close-up of a face", "a face in shadow", "extreme close-up of eyes", "portrait of a woman", "portrait of a man", "a face lit from one side", "a face looking at the camera", "close-up of a face, one side lit"])
    donors = donor_faces(assets_dir, lm, units, exclude_film=film_id)
    comp = CompositeFace(donors)
    out = []
    last = None
    n = len(frames)
    for i, f in enumerate(frames):
        pts = lm.pixels(film_id, unit_id, a + i / render.FPS, w, h) or last
        if pts is None:
            out.append(f.copy()); continue
        last = pts
        p = None if still else min(1.0, max(0.0, (i - 6) / 20))          # real for a beat, then the faces arrive
        out.append(f.copy() if (p is not None and p <= 0) else comp.step(f, pts, progress=p))
    return "composite-face", out, "A composite face: eyes, nose, mouth, cheeks and brow each from a different face in the library, aligned to the host's own eyes and nose and following the head."


def exp_patches(assets_dir, still, host=("Phantom Thread", 7103.0, 7110.0), gender=("woman", "girl", "her", "she", "female")):
    """Memory patches: one feature at a time from another face, a hard-edged patch on a beat, then gone."""
    from pipeline.algmods.composite import PatchFace, donor_faces, load_landmarks, patch_schedule
    from pipeline.config import load_config
    title, a, b = host
    film_id, path = render._resolve_film(title)
    frames = render.decode(str(path), a, a + 0.1 if still else b, height=1080)
    h, w = frames[0].shape[:2]
    lm = load_landmarks(load_config())
    shots = json.load(open(Path(assets_dir) / film_id / "shots.json", encoding="utf-8"))["shots"]
    unit_id = next(s["shot_id"] for s in shots if s["t_start"] <= a < s["t_end"])
    units = units_from_search(API, ["close-up of a woman's face", "a woman looking at the camera", "portrait of a woman", "a woman's face in shadow", "extreme close-up of a woman's eyes", "a girl's face close up"])
    units = [u for u in units if any(g in str(u.get("caption", "")).lower() for g in gender)]
    donors = donor_faces(assets_dir, lm, units, exclude_film=film_id)
    pf = PatchFace(donors)
    n = len(frames)
    events = patch_schedule(n) if not still else [(0, 1, "mouth"), (0, 1, "eye-l")]
    used: set[int] = set()
    chosen: dict[int, int] = {}
    out = []
    for i, f in enumerate(frames):
        pts = lm.pixels(film_id, unit_id, a + i / render.FPS, w, h)
        frame = f.copy()
        if pts is None:
            out.append(frame); continue
        for k, (start, length, feature) in enumerate(events):
            if not (start <= i < start + length):
                continue
            if k not in chosen:
                pick = pf.choose(f, pts, "eyes" if feature == "cutin" else feature, used)
                if pick is None:
                    continue
                chosen[k] = pick; used.add(pick)
            frame = pf.cutin(frame, pts, chosen[k]) if feature == "cutin" else pf.patch(frame, pts, feature, chosen[k])
        out.append(frame)
    return "memory-patches", out, "Memory patches: one feature at a time from another woman's face, a hard-edged patch set exactly on the host's, held for a few frames on the beat; now and then a whole other face flashes through, aligned."


def exp_grafts(assets_dir, still, host=("Phantom Thread", 7103.0, 7110.0), gender=("woman", "girl", "her", "she", "female")):
    """Grafts from a hand-authored plan: a phrase, not a metronome; face-scoped cut-ins; varied entrances."""
    from pipeline.algmods.composite import donor_faces, load_landmarks
    from pipeline.config import load_config
    from pipeline.lab.grafts import Graft, GraftPlan, GraftRenderer
    title, a, b = host
    film_id, path = render._resolve_film(title)
    frames = render.decode(str(path), a, a + 0.1 if still else b, height=1080)
    h, w = frames[0].shape[:2]
    lm = load_landmarks(load_config())
    shots = json.load(open(Path(assets_dir) / film_id / "shots.json", encoding="utf-8"))["shots"]
    unit_id = next(s["shot_id"] for s in shots if s["t_start"] <= a < s["t_end"])
    units = units_from_search(API, ["close-up of a woman's face", "a woman looking at the camera", "portrait of a woman", "a woman's face in shadow", "extreme close-up of a woman's eyes", "a girl's face close up"])
    units = [u for u in units if any(g in str(u.get("caption", "")).lower() for g in gender)]
    donors = donor_faces(assets_dir, lm, units, exclude_film=film_id)
    # The phrase (120 bpm, 15 frames a beat): a bar of her; three quick eye swaps on eighths; a long
    # grafted mouth that fades in; a rest; the whole face of someone else, in her framing, on the
    # downbeat; both eyes growing in; a hard nose; silence to the end.
    plan = GraftPlan(grafts=[
        Graft(window="eye-l", start=45, hold=5, enter="cut"),
        Graft(window="eye-r", start=52, hold=5, enter="cut"),
        Graft(window="eye-l", start=60, hold=12, enter="grow", ramp=4, leave="fade"),
        Graft(window="mouth", start=90, hold=40, enter="fade", ramp=8, leave="fade", light=.85, colour=.3, feather=6),
        Graft(window="face", start=135, hold=12, enter="cut", leave="fade", ramp=5, light=.7, colour=.2, feather=10),
        Graft(window="eyes", start=165, hold=20, enter="grow", ramp=6, leave="cut", light=.6),
        Graft(window="nose", start=190, hold=6, enter="cut"),
    ])
    r = GraftRenderer(donors)
    out = r.render(frames, lambda i: lm.pixels(film_id, unit_id, a + i / render.FPS, w, h), plan)
    if still:
        out = [r.apply(frames[0], lm.pixels(film_id, unit_id, a, w, h), plan.grafts[4], r.choose(frames[0], lm.pixels(film_id, unit_id, a, w, h), "face"), 0.5)]
    (OUT / "x-grafts-plan.json").write_text(plan.model_dump_json(indent=2), encoding="utf-8")
    return "grafts-phrase", out, "Grafts from a written plan: three quick eye swaps, a long grafted mouth fading in, a rest, another woman's whole face in her framing on the downbeat, both eyes growing in, a hard nose."


def _temporal_host(assets_dir, title, a, b, still, classes, region_kind="subject", largest=True, box=None):
    """Frames and per-frame subject masks; ``box`` (x, y, w, h fractions) limits the person mask to one figure."""
    from pipeline.lab import regions
    film_id, path = render._resolve_film(title)
    frames = render.decode(str(path), a, a + 0.6 if still else b, height=1080)
    if not classes:
        return film_id, frames, [None] * len(frames)
    masks = regions.resolve(frames, {"kind": region_kind, "classes": classes, "largest": False, "dilate": 6})
    if box is not None:
        bx = regions.resolve(frames, {"kind": "box", "x": box[0], "y": box[1], "w": box[2], "h": box[3]})
        masks = [None if m is None else (m & b) for m, b in zip(masks, bx)]
    return film_id, frames, masks


def exp_beat(assets_dir, still, track="Dracula"):
    """Beat time on The Matrix: a man stands still while the crowd streams; the world only advances on beats."""
    from pipeline.algmods.temporal import beat_frames, beat_time, beats_from_rhythm_cache
    film_id, frames, masks = _temporal_host(assets_dir, "The Matrix", 7727.0, 7735.0, still, ["person"], box=(0.0, 0.2, 0.2, 0.8))
    beats, downs, info = beats_from_rhythm_cache(assets_dir, track)
    bf = beat_frames(beats, fps=render.FPS, frames=len(frames), offset=0.0)
    out = beat_time(frames, masks, bf, mode="world", feather=4)
    return "beat-world-matrix", out if not still else [out[min(len(out) - 1, 12)]], f"Two clocks: the man moves live, the street behind him only advances on the beats of {info['track']}."


def exp_beat_subject(assets_dir, still, track="Dracula"):
    """The inverse on Bicycle Thieves: the crowd runs live, the main figure jumps forward only on beats."""
    from pipeline.algmods.temporal import beat_frames, beat_time, beats_from_rhythm_cache
    film_id, frames, masks = _temporal_host(assets_dir, "Bicycle Thieves", 5011.0, 5016.0, still, ["person"], largest=True)
    beats, downs, info = beats_from_rhythm_cache(assets_dir, track)
    bf = beat_frames(beats, fps=render.FPS, frames=len(frames))
    out = beat_time(frames, masks, bf, mode="subject", feather=4)
    return "beat-subject-thieves", out if not still else [out[min(len(out) - 1, 12)]], f"The inverse: the crowd runs live; the main figure is frozen and jumps forward on the beats of {info['track']}."


def exp_echo(assets_dir, still):
    """Motion echo on Climax's overhead dancer: only the fast limbs leave a trail."""
    from pipeline.algmods.temporal import MotionEcho
    film_id, frames, masks = _temporal_host(assets_dir, "Climax", 2474.0, 2481.0, still, ["person"], box=(0.28, 0.0, 0.44, 1.0))
    echo = MotionEcho(threshold=3.0, decay=0.9)
    out = [echo.step(f, m) for f, m in zip(frames, masks)]
    return "echo-climax", out if not still else [out[-1]], "Only the fast parts of the dancer leave a trail; the body stays solid."


def exp_echo_spin(assets_dir, still):
    from pipeline.algmods.temporal import MotionEcho
    film_id, frames, masks = _temporal_host(assets_dir, "Frances Ha", 4665.5, 4671.0, still, ["person"], largest=True)
    echo = MotionEcho(threshold=3.0, decay=0.9)
    out = [echo.step(f, m) for f, m in zip(frames, masks)]
    return "echo-frances", out if not still else [out[-1]], "The spin: arms leave trails, the torso stays."


def exp_wipe(assets_dir, still, track="Dracula"):
    """Time slice on the Shibuya scramble: a seam of time sweeps across on each downbeat."""
    from pipeline.algmods.temporal import beat_frames, beats_from_rhythm_cache, time_slice
    film_id, frames, _ = _temporal_host(assets_dir, "Lost in Translation", 2196.1, 2199.7, still, None)
    beats, downs, info = beats_from_rhythm_cache(assets_dir, track)
    bf = beat_frames(downs or beats, fps=render.FPS, frames=len(frames))
    sweeps = [(b, min(len(frames), b + 12)) for b in bf] or [(0, len(frames))]
    out = time_slice(frames, offset=24, sweeps=sweeps, direction="left-to-right", feather=3)
    return "wipe-scramble", out if not still else [out[min(len(out) - 1, 6)]], f"A seam of time crosses the crossing on each downbeat of {info['track']}: behind it the crowd is 0.8 s ahead."


def exp_wipe_waves(assets_dir, still):
    from pipeline.algmods.temporal import time_slice
    film_id, frames, _ = _temporal_host(assets_dir, "Kwaidan", 4991.0, 4999.0, still, None)
    n = len(frames)
    out = time_slice(frames, offset=36, sweeps=[(10, 70), (100, 160), (190, 230)], direction="bottom-to-top", feather=4)
    return "wipe-waves", out if not still else [out[min(n - 1, 40)]], "The sea 1.2 s out of step with itself, the seam rising through the frame."


def exp_echo_set(assets_dir, still):
    """Echo regression set: a dance, a sword swing, boxers, a spin."""
    from pipeline.algmods.temporal import MotionEcho
    hosts = [("echo-killbill", "Kill Bill Vol 1", 400.9, 404.2, (0.0, 0.0, 1.0, 1.0)), ("echo-gladiator", "Gladiator", 9354.7, 9357.5, (0.0, 0.0, 1.0, 1.0)),
             ("echo-lahaine", "La Haine", 4239.0, 4246.0, (0.0, 0.0, 1.0, 1.0)), ("echo-metropolis", "Metropolis", 5552.8, 5555.6, (0.0, 0.0, 1.0, 1.0))]
    results = []
    for name, title, a, b, box in hosts:
        try:
            film_id, frames, masks = _temporal_host(assets_dir, title, a, b, still, ["person"], box=box)
        except SystemExit as exc:
            print("skip", title, exc); continue
        echo = MotionEcho(threshold=3.0, decay=0.9)
        out = [echo.step(f, m) for f, m in zip(frames, masks)]
        render.encode(out, OUT / f"x-{name}.mp4") if not still else None
        results.append(name)
    return "echo-set", [frames[-1]] if still else [], "Echo regression set rendered: " + ", ".join(results)


EXPERIMENTS = [exp_echo_set, exp_beat, exp_beat_subject, exp_echo, exp_echo_spin, exp_wipe, exp_wipe_waves, exp_grafts, exp_patches, exp_composite, exp_face, exp_anthology, exp_hand, exp_reveal, exp_self]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["stills", "clips"])
    parser.add_argument("--only", default=None)
    args = parser.parse_args(argv)
    from pipeline.config import load_config
    assets_dir = load_config().paths.assets_dir
    OUT.mkdir(parents=True, exist_ok=True)
    notes = {}
    for fn in EXPERIMENTS:
        if args.only and args.only not in fn.__name__:
            continue
        name, frames, note = fn(assets_dir, args.mode == "stills")
        notes[name] = note
        if args.mode == "stills":
            from PIL import Image
            Image.fromarray(frames[0]).save(OUT / f"x-{name}.png")
            print(name, "still", len(frames))
        else:
            render.encode(frames, OUT / f"x-{name}.mp4")
            print(name, "frames", len(frames))
    (OUT / "x-notes.json").write_text(json.dumps(notes, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
