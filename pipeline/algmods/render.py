"""Offline renders of one library window with one mod, framed 9:16 around the subject.

    uv run --extra measure python -m pipeline.algmods render --film "Le Samourai" \
        --start 5406.4 --end 5419 --mod stripes --out .tmp/algmods/samourai-stripes.mp4

Frames are decoded with ffmpeg at the output rate, scaled to the working height,
then cropped to the output aspect around the segmented subject (RF-DETR, the
same ``Segmenter`` the effects pass uses; needs the ``measure`` extra) or the
frame centre. ``--classes`` picks the COCO classes. No audio. Output is H.264
for the dailies page.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from fractions import Fraction
from pathlib import Path
from typing import Any, Callable

import numpy as np
from PIL import Image

from pipeline.algmods import mods

OUT_W, OUT_H, FPS = 720, 1280, 30
WORK_H = 1280
DEFAULT_CLASSES = ["person", "car", "truck", "bus", "train", "boat", "motorcycle"]


def decode(path: str, start: float, end: float, *, fps: int = FPS, height: int = WORK_H) -> list[np.ndarray]:
    """RGB frames of ``[start, end)`` scaled to ``height`` (display aspect preserved, even width)."""
    probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height,sample_aspect_ratio",
                            "-of", "json", path], capture_output=True, check=True).stdout
    stream = json.loads(probe)["streams"][0]
    sar = stream.get("sample_aspect_ratio") or "1:1"
    num, den = (int(p) for p in sar.split(":")) if ":" in sar else (1, 1)
    dar = stream["width"] * (num / den if num and den else 1.0) / stream["height"]
    width = int(round(height * dar / 2)) * 2
    count = max(1, int(round((end - start) * fps)))
    vf = f"scale={width}:{height},setsar=1,fps={fps}"
    raw = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-ss", f"{start:.4f}", "-i", path,
                          "-map", "0:v:0", "-an", "-vf", vf, "-frames:v", str(count), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         capture_output=True, check=True).stdout
    size = width * height * 3
    return [np.frombuffer(raw[i * size:(i + 1) * size], np.uint8).reshape(height, width, 3) for i in range(len(raw) // size)]


def resize(image: np.ndarray, width: int, height: int, *, nearest: bool = False) -> np.ndarray:
    pil = Image.fromarray(image)
    return np.asarray(pil.resize((width, height), Image.NEAREST if nearest else Image.LANCZOS))


def crop_window(frames: list[np.ndarray], masks: list[np.ndarray | None], out_w: int, out_h: int) -> tuple[int, int, int, int]:
    """A fixed (x0, y0, w, h) crop of the working frames with the output aspect, centred on the subject where known."""
    h, w = frames[0].shape[:2]
    crop_h = h
    crop_w = int(round(h * out_w / out_h / 2)) * 2
    if crop_w > w:
        crop_w, crop_h = w, int(round(w * out_h / out_w / 2)) * 2
    # The crop frames the composition: the subject when it carries the shot, otherwise the light.
    # A passer-by in a wide night exterior must not drag the crop off the lit gate.
    # Frame the composition: as close to the light as the subject allows. The subject (its median
    # bounding box over the clip) must stay inside the crop; within that freedom, centre on the light.
    lx, ly = light_centre(frames)
    boxes = [_bbox(m) for m in masks if m is not None and m.any()]
    x0 = int(np.clip(lx * w - crop_w / 2, 0, w - crop_w))
    y0 = int(np.clip(ly * h - crop_h / 2, 0, h - crop_h))
    if boxes:
        bx0, by0, bx1, by1 = (float(np.median([b[i] for b in boxes])) for i in range(4))
        if bx1 - bx0 <= crop_w:
            x0 = int(np.clip(x0, bx1 - crop_w, bx0))
        else:
            x0 = int(np.clip((bx0 + bx1) / 2 - crop_w / 2, 0, w - crop_w))
        if by1 - by0 <= crop_h:
            y0 = int(np.clip(y0, by1 - crop_h, by0))
        else:
            y0 = int(np.clip((by0 + by1) / 2 - crop_h / 2, 0, h - crop_h))
        x0 = int(np.clip(x0, 0, w - crop_w)); y0 = int(np.clip(y0, 0, h - crop_h))
    return x0, y0, crop_w, crop_h


def _bbox(mask: np.ndarray) -> tuple[int, int, int, int]:
    ys, xs = np.nonzero(mask)
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def light_centre(frames: list[np.ndarray], step: int = 10) -> tuple[float, float]:
    """Centroid (fractions) of the light-and-colour energy over the clip; the frame centre when there is none."""
    sample = frames[::max(1, len(frames) // 12)]
    energy = np.zeros(sample[0].shape[:2], np.float64)
    for f in sample:
        x = f.astype(np.float32) / 255.0
        mx, mn = x.max(axis=2), x.min(axis=2)
        sat = np.where(mx > 1e-3, (mx - mn) / np.maximum(mx, 1e-3), 0.0)
        energy += np.maximum(x.mean(axis=2) ** 0.8, sat * mx)
    energy = np.clip(energy / len(sample) - 0.25, 0, None) ** 2        # only what is clearly lit counts
    h, w = energy.shape
    total = float(energy.sum())
    if total <= 1e-6:
        return 0.5, 0.5
    ys, xs = np.mgrid[0:h, 0:w]
    return float((energy * xs).sum() / total / w), float((energy * ys).sum() / total / h)


def segment(frames: list[np.ndarray], classes: list[str], progress: Callable[[str], None]) -> list[np.ndarray | None]:
    from pipeline.lab.effects import Segmenter
    seg = Segmenter()
    masks: list[np.ndarray | None] = []
    for i, frame in enumerate(frames):
        if i % 30 == 0:
            progress(f"segmenting {i + 1}/{len(frames)}")
        m = seg.mask(frame, classes)
        masks.append(m.astype(bool) if m.any() else None)
    return masks


def smooth_masks(masks: list[np.ndarray | None], window: int = 3) -> list[np.ndarray | None]:
    """Temporal majority over a small window; frames without a mask borrow their neighbours'."""
    out: list[np.ndarray | None] = []
    for i in range(len(masks)):
        lo, hi = max(0, i - window // 2), min(len(masks), i + window // 2 + 1)
        group = [m for m in masks[lo:hi] if m is not None]
        if not group:
            out.append(None)
            continue
        vote = np.mean([m.astype(np.float32) for m in group], axis=0)
        out.append(vote >= 0.5)
    return out


def apply_mod(mod: str, frames: list[np.ndarray], masks: list[np.ndarray | None], params: dict[str, Any],
              progress: Callable[[str], None], cancelled: Callable[[], bool] = lambda: False) -> list[np.ndarray]:
    h, w = frames[0].shape[:2]
    out: list[np.ndarray] = []
    if mod == "stripes":
        count = int(params.get("count", 48))
        max_lag = int(params.get("max_lag", 24))
        pattern = str(params.get("pattern", "interleave"))
        feather = int(params.get("feather", 3))
        vanish = params.get("vanish")                      # "x,y" fractions, "auto", or None
        direction = params.get("direction", "1,0")
        if vanish == "auto":
            from pipeline.algmods.temporal import vanishing_point
            vp, spread = vanishing_point(frames, masks)
            progress(f"vanishing point {vp} (spread {spread:.2f})")
            vanish = f"{vp[0]:.3f},{vp[1]:.3f}" if vp else None
        if vanish:
            vx, vy = (float(v) for v in str(vanish).split(","))
            ids = mods.stripe_ids(h, w, count=count, vanish=(vx, vy))
        else:
            dx, dy = (float(v) for v in str(direction).split(","))
            ids = mods.stripe_ids(h, w, count=count, direction=(dx, dy))
        if pattern == "blocks":
            vp = tuple(float(v) for v in str(vanish).split(",")) if vanish else None
            dxy = tuple(float(v) for v in str(direction).split(","))
            first = next((m for m in masks if m is not None), None)
            lags = mods.block_lags(h, w, vanish=vp, direction=dxy, bands=count, blocks=int(params.get("blocks", 24)),
                                   max_lag=max_lag, mask=first)
        else:
            lags = mods.stripe_lags(ids, count=count, max_lag=max_lag, pattern=pattern)
        ramp = int(params.get("ramp", 24))                # frames over which the lag grows from 0
        history: list[np.ndarray] = []
        whole = params.get("whole_frame", "0") != "0"
        for i, frame in enumerate(frames):
            if i % 30 == 0:
                progress(f"stripes {i + 1}/{len(frames)}")
                if cancelled():
                    raise InterruptedError("render cancelled")
            history.append(frame)
            history = history[-(max_lag + 1):]
            grow = min(1.0, i / max(ramp, 1))
            out.append(mods.time_stripes(history, None if whole else masks[i], (lags * grow).astype(np.int32), feather=feather))
    elif mod == "dots":
        style = str(params.get("style", "pastel"))          # pastel (alg.comp.mod) | vivid (Yoon Hyup)
        preset = ({"density": 3000, "radius_frac": 0.0075, "soften": 0.5, "palette": mods.YNHP_PALETTE,
                   "palette_mix": 0.65, "size_jitter": 0.12, "bright_boost": 0.4, "background": mods.YNHP_GROUND}
                  if style == "vivid" else
                  {"density": 3000, "radius_frac": 0.0065, "soften": 0.9, "palette": None,
                   "palette_mix": 0.0, "size_jitter": 0.0, "bright_boost": 0.0, "background": (0, 0, 0)})
        tracker = mods.DotTracker(density=float(params.get("density", preset["density"])),
                                  radius_frac=float(params.get("radius_frac", preset["radius_frac"])),
                                  quality=float(params.get("quality", 0.001)), lift=float(params.get("lift", 0.30)),
                                  keep=float(params.get("keep", 0.50)), sat_gain=float(params.get("sat_gain", 0.32)),
                                  soften=float(params.get("soften", preset["soften"])), clahe=params.get("clahe", "1") != "0",
                                  palette=preset["palette"], palette_mix=float(params.get("palette_mix", preset["palette_mix"])),
                                  size_jitter=float(params.get("size_jitter", preset["size_jitter"])),
                                  bright_boost=float(params.get("bright_boost", preset["bright_boost"])),
                                  spacing=float(params.get("spacing", 0.0)), fill=params.get("fill", "1") != "0", grace=int(params.get("grace", 4)),
                                  paint_floor=float(params.get("paint_floor", 0.3)), texture_density=float(params.get("texture_density", 0.25)),
                                  mass_blur=float(params.get("mass_blur", 10.0)), dim_power=float(params.get("dim_power", 1.75)),
                                  subject_energy=float(params.get("subject_energy", 0.55)),
                                  thick_boost=float(params.get("thick_boost", 0.6)), fill_jitter=float(params.get("fill_jitter", 0.18)),
                                  dim_floor=float(params.get("dim_floor", 0.15)),
                                  fade_in=int(params.get("fade_in", 5)), fade_out=int(params.get("fade_out", 4)),
                                  palette_spread=float(params.get("palette_spread", 0.6 if style == "vivid" else 0.0)),
                                  flat_boost=float(params.get("flat_boost", 0.25 if style == "vivid" else 0.0)),
                                  flat_thin=float(params.get("flat_thin", 0.5 if style == "vivid" else 0.0)),
                                  flat_gap=float(params.get("flat_gap", 1.5 if style == "vivid" else 0.0)),
                                  dash=float(params.get("dash", 1.2 if style == "vivid" else 0.0)),
                                  background=_ground(str(params.get("ground", "scene" if style == "vivid" else "black")),
                                                     preset["background"], frames[0]))
        protect = str(params.get("protect", "none"))        # none (his default) | subject
        for i, frame in enumerate(frames):
            if i % 30 == 0:
                progress(f"dots {i + 1}/{len(frames)}")
                if cancelled():
                    raise InterruptedError("render cancelled")
            keep = masks[i] if protect != "none" else None
            out.append(tracker.step(frame, keep, detect_mask=None if keep is None else ~keep, subject=masks[i]))
    elif mod == "quadtree":
        period = float(params.get("period", 2.0))          # seconds per reveal cycle
        mode = str(params.get("mode", "reveal"))           # reveal | hold
        threshold = float(params.get("threshold", 18.0))
        for i, frame in enumerate(frames):
            if i % 60 == 0:
                progress(f"quadtree {i + 1}/{len(frames)}")
                if cancelled():
                    raise InterruptedError("render cancelled")
            if mode == "hold":
                out.append(mods.quadtree(frame, threshold=threshold, min_size=int(params.get("min_size", 6))))
            else:
                phase = (i / FPS) % period / period
                out.append(mods.quadtree_reveal(frame, phase, coarse=float(params.get("coarse", 64)),
                                                fine=float(params.get("fine", 2))))
    elif mod == "mosaic":
        from pipeline.algmods.mosaic import Mosaic, TileBank
        from pipeline.lab import regions
        assets = Path(params.get("assets_dir") or _assets_dir())
        source = str(params.get("source", "library"))
        host = params.get("host_film")
        bank = TileBank(assets, exclude_film=None if source == "self" else host)
        if source == "self":
            bank.restrict(bank.rows_for_film(host))
        elif source == "search":
            units = params.get("tile_units")
            if units is None:
                # the CLI: resolve the queries now (a Lab job has them pinned in tile_units)
                from pipeline.algmods.mosaic import units_from_search
                queries = params.get("queries") or []
                if isinstance(queries, str):
                    queries = [q.strip() for q in queries.split(",") if q.strip()]
                units = [(u["film_id"], u["unit"]) for u in units_from_search("http://127.0.0.1:8000", queries)]
            bank.restrict(bank.rows_for_units([(str(f), int(u)) for f, u in units]))
        spec = params.get("region") or {"kind": "all"}
        if isinstance(spec, str):
            spec = {"kind": spec}
        region_masks = regions.resolve(frames, spec, progress=progress)
        reveal = str(params.get("reveal", "none"))
        n = len(frames)
        if str(params.get("layout", "grid")) == "quad":
            from pipeline.algmods.mosaic import QuadMosaic
            from pipeline.lab.regions import soft
            quad = QuadMosaic(bank, min_size=max(16, w // 60), max_size=max(96, w // 8), threshold=float(params.get("threshold", 10.0)),
                              light=float(params.get("light", 0.9)), colour=float(params.get("colour", 0.3)), grout=1,
                              moving=_flag(params.get("moving", True)), hold=int(params.get("hold", 8)), drift=float(params.get("drift", 0.10)))
            mode = str(spec.get("kind", "all"))
            quad.layout(frames, region_masks[0] if (mode == "subject" and region_masks[0] is not None) else None)
            for i, frame in enumerate(frames):
                if i % 15 == 0:
                    progress(f"mosaic {i + 1}/{n}")
                    if cancelled():
                        raise InterruptedError("render cancelled")
                keep = soft(region_masks[i], 3) if (mode == "background" and region_masks[i] is not None) else None
                if mode == "background" and keep is not None:
                    keep = 1.0 - keep                      # background region: keep the subject real
                p = None if reveal == "none" else (min(1.0, i / max(1, int(n * 0.35))) if reveal == "in" else
                     (min(1.0, i / max(1, int(n * 0.3))) if i < n * 0.7 else max(0.0, 1.0 - (i - n * 0.7) / max(1, int(n * 0.3)))))
                out.append(frame.copy() if (p is not None and p <= 0) else quad.step(frame, keep=keep, progress=p, origin=(0.5, 0.45)))
            return out
        mosaic = Mosaic(bank, columns=int(params.get("columns", 12)), drift=float(params.get("drift", 0.10)),
                        hold=int(params.get("hold", 8)), moving=_flag(params.get("moving", True)))
        for i, frame in enumerate(frames):
            if i % 15 == 0:
                progress(f"mosaic {i + 1}/{n}")
                if cancelled():
                    raise InterruptedError("render cancelled")
            region = None if region_masks[i] is None else mosaic.cell_region(region_masks[i], h, w, where="subject", cover=0.35)
            mosaic.assign(frame, region)
            if reveal == "none":
                p = None
            elif reveal == "in":
                p = min(1.0, i / max(1, int(n * 0.35)))
            else:
                p = min(1.0, i / max(1, int(n * 0.3))) if i < n * 0.7 else max(0.0, 1.0 - (i - n * 0.7) / max(1, int(n * 0.3)))
            if p is not None and p <= 0:
                out.append(frame.copy())
            else:
                out.append(mosaic.compose(frame, region=region, progress=p, origin=(0.5, 0.45)))
    else:
        raise ValueError(f"unknown mod {mod!r}")
    return out


def encode(frames: list[np.ndarray], target: Path, *, fps: int = FPS, crf: int = 22) -> None:
    import av
    target.parent.mkdir(parents=True, exist_ok=True)
    h, w = frames[0].shape[:2]
    with av.open(str(target), "w") as writer:
        stream = writer.add_stream("libx264", rate=fps)
        stream.width, stream.height, stream.pix_fmt = w, h, "yuv420p"
        stream.time_base = Fraction(1, fps)
        stream.options = {"crf": str(crf), "preset": "medium", "movflags": "+faststart"}
        for frame in frames:
            packet = av.VideoFrame.from_ndarray(np.ascontiguousarray(frame), format="rgb24")
            for p in stream.encode(packet):
                writer.mux(p)
        for p in stream.encode():
            writer.mux(p)


def render(path: str, start: float, end: float, mod: str, out: Path, *, params: dict[str, Any] | None = None,
           classes: list[str] | None = None, progress: Callable[[str], None] = print,
           side_by_side: bool = False, subject: bool = True,
           cancelled: Callable[[], bool] = lambda: False) -> dict[str, Any]:
    """Render ``[start, end)`` of ``path`` with ``mod`` to ``out``. ``subject=False`` skips segmentation
    (centre crop, no protect). ``cancelled`` is polled between stages and every 30 treated frames."""
    params = params or {}
    classes = classes or DEFAULT_CLASSES
    progress(f"decoding {Path(path).name} {start:.2f}-{end:.2f}")
    native = str(params.get("aspect", "portrait")) == "native"
    frames = decode(path, start, end, height=1080 if native else WORK_H)
    if cancelled():
        raise InterruptedError("render cancelled")
    needs_masks = subject and mod in ("stripes", "dots")
    masks = smooth_masks(segment(frames, classes, progress)) if needs_masks else [None] * len(frames)
    if cancelled():
        raise InterruptedError("render cancelled")
    if native:
        # the full frame at its own size: a wide composition keeps its structure and sharpness
        h0, w0 = frames[0].shape[:2]
        w0 -= w0 % 2
        frames = [f[:, :w0] for f in frames]
        masks = [None if m is None else m[:, :w0] for m in masks]
        progress(f"applying {mod} to {len(frames)} frames at {w0}x{h0}")
        treated = apply_mod(mod, frames, masks, params, progress, cancelled)
        treated = _live(frames, treated, params, progress)
        if side_by_side:
            treated = [np.concatenate([a, b], axis=0) for a, b in zip(frames, treated)]
        encode(treated, out)
        return {"out": str(out), "frames": len(treated), "mask_coverage": None, "crop": [0, 0, w0, h0]}
    x0, y0, cw, ch = crop_window(frames, masks, OUT_W, OUT_H)
    frames = [resize(f[y0:y0 + ch, x0:x0 + cw], OUT_W, OUT_H) for f in frames]
    masks = [None if m is None else resize(m[y0:y0 + ch, x0:x0 + cw].astype(np.uint8) * 255, OUT_W, OUT_H, nearest=True) > 127
             for m in masks]
    progress(f"applying {mod} to {len(frames)} frames")
    treated = apply_mod(mod, frames, masks, params, progress, cancelled)
    treated = _live(frames, treated, params, progress)
    if side_by_side:
        treated = [np.concatenate([a, b], axis=1) for a, b in zip(frames, treated)]
    encode(treated, out)
    coverage = float(np.mean([m.mean() if m is not None else 0.0 for m in masks])) if needs_masks else None
    return {"out": str(out), "frames": len(treated), "mask_coverage": coverage, "crop": [x0, y0, cw, ch]}


def _ground(ground: str, preset, frame) -> tuple[int, int, int]:
    """The dots' ground: the style's own, true black, or the scene's shadow colour (``scene``)."""
    if ground == "scene":
        return mods.scene_ground(frame)
    if ground == "black":
        return (0, 0, 0)
    return tuple(preset)


def _live(frames, treated, params, progress):
    """The film shows through the treatment's ``live`` region (subject, nearest depth, ...)."""
    spec = params.get("live")
    if not spec or (isinstance(spec, dict) and spec.get("kind", "none") == "none"):
        return treated
    from pipeline.lab.regions import keep_live
    progress("live layer")
    return keep_live(frames, treated, spec, progress=progress)


def _flag(value) -> bool:
    return value not in (False, 0, "0", "false", "False", "", None)


def _assets_dir():
    from pipeline.config import load_config
    return load_config().paths.assets_dir


def _resolve_film(title: str) -> tuple[str, str]:
    import lancedb
    from pipeline.config import load_config
    config = load_config()
    films = lancedb.connect(str(config.paths.assets_dir / "db")).open_table("films").to_pandas()
    bare = films.title.str.replace(r"\s*\(\d{4}\).*$", "", regex=True).str.lower()
    hit = films[bare == title.lower()]                       # exact title ("Her" is not "Hereditary")
    if not len(hit):
        hit = films[films.title.str.lower().str.startswith(title.lower())]
    if not len(hit):
        hit = films[films.title.str.lower().str.contains(title.lower())]
    if len(hit) != 1:
        raise SystemExit(f"{len(hit)} films match {title!r}")
    row = hit.iloc[0]
    return row.film_id, row.path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m pipeline.algmods")
    sub = parser.add_subparsers(dest="command", required=True)
    r = sub.add_parser("render")
    r.add_argument("--film", required=True, help="film title prefix")
    r.add_argument("--start", type=float, required=True)
    r.add_argument("--end", type=float, required=True)
    r.add_argument("--mod", choices=["stripes", "dots", "quadtree", "mosaic"], required=True)
    r.add_argument("--out", type=Path, required=True)
    r.add_argument("--param", action="append", default=[], help="key=value (repeatable)")
    r.add_argument("--classes", default=None, help="comma-separated COCO classes for the subject mask")
    r.add_argument("--side-by-side", action="store_true")
    args = parser.parse_args(argv)
    params = dict(p.split("=", 1) for p in args.param)
    film_id, path = _resolve_film(args.film)
    params.setdefault("host_film", film_id)                 # a mosaic never tiles a film with itself
    result = render(path, args.start, args.end, args.mod, args.out, params=params,
                    classes=args.classes.split(",") if args.classes else None, side_by_side=args.side_by_side)
    print(json.dumps(result))
    return 0
