"""Bounded RGB seam compositing; no model, GPU, or whole-film frame cache.

All light and blend operations use linear Rec.709. Motion blur is a spatial
shutter approximation of the transformed composition, not source retiming.
Only the two current frames and small spatial fields are retained.
"""
from __future__ import annotations

from fractions import Fraction
import math
from pathlib import Path
import time

import av
import numpy as np
from PIL import Image

from pipeline.lab.media import JobCancelled
from pipeline.transitions.contracts import FPS, frame_count


_code = np.linspace(0, 1, 256, dtype=np.float32)
DECODE_709 = np.where(_code < .081, _code / 4.5, ((_code + .099) / 1.099) ** (1 / .45)).astype(np.float32)
_linear = np.linspace(0, 1, 65536, dtype=np.float32)
ENCODE_709 = np.rint(np.clip(np.where(_linear < .018, _linear * 4.5, 1.099 * _linear ** .45 - .099), 0, 1) * 255).astype(np.uint8)
LUMA = np.array([.2126, .7152, .0722], dtype=np.float32)


def encode_rgb(linear):
    return ENCODE_709[np.rint(np.clip(linear, 0, 1) * 65535).astype(np.uint16)]


def smooth(value):
    value = np.clip(value, 0, 1)
    return value * value * (3 - 2 * value)


def curve(value, easing):
    value = float(np.clip(value, 0, 1))
    if easing == "smooth":
        return float(smooth(value))
    if easing == "snappy":
        return 4 * value ** 3 if value < .5 else 1 - (-2 * value + 2) ** 3 / 2
    return value


def phase(progress, cut_phase):
    """Continuous time map whose half-way point is the requested cut phase."""
    return progress / cut_phase * .5 if progress <= cut_phase else .5 + (progress - cut_phase) / (1 - cut_phase) * .5


def pulse(progress, peak, attack, decay):
    # A piecewise smooth envelope reaches its exact peak even for uneven timing.
    if progress <= 0 or progress >= 1:
        return 0.
    if progress <= peak:
        return float(smooth((progress - max(0., peak - attack)) / max(1e-6, min(peak, attack))))
    return float(1 - smooth((progress - peak) / max(1e-6, min(1 - peak, decay))))


def box_blur(array, radius, axis, *, padding_mode="edge"):
    """Separable edge-extended box blur with constant work per pixel."""
    radius = int(radius)
    if radius < 1:
        return array
    padding = [(0, 0)] * array.ndim
    padding[axis] = (radius, radius)
    padded = np.pad(array, padding, mode=padding_mode)
    zero_shape = list(padded.shape)
    zero_shape[axis] = 1
    integral = np.concatenate([np.zeros(zero_shape, dtype=np.float32), np.cumsum(padded, axis=axis, dtype=np.float32)], axis=axis)
    low, high = [slice(None)] * array.ndim, [slice(None)] * array.ndim
    low[axis], high[axis] = slice(None, -(2 * radius + 1)), slice(2 * radius + 1, None)
    return (integral[tuple(high)] - integral[tuple(low)]) / (2 * radius + 1)


def motion_ease(value, easing="smooth"):
    """C2-or-better endpoints, including the legacy 'linear' motion setting."""
    u = float(np.clip(value, 0, 1))
    if easing == "snappy":
        return u ** 5 * (126 + u * (-420 + u * (540 + u * (-315 + u * 70))))
    if easing == "smooth":
        return u ** 4 * (35 + u * (-84 + u * (70 - 20 * u)))
    return u ** 3 * (10 + u * (-15 + 6 * u))


def motion_phase(progress, cut_phase, midpoint=.5):
    """One differentiable time warp; no velocity jump at an off-center cut."""
    p = float(np.clip(progress, 0, 1))
    factor = midpoint * (1 - cut_phase) / (cut_phase * (1 - midpoint))
    return factor * p / (1 + (factor - 1) * p)


def motion_bump(value, power=3):
    u = float(np.clip(value, 0, 1))
    return (4 * u * (1 - u)) ** power


def whip_motion(progress, cut_phase, easing, rebound):
    u = motion_phase(progress, cut_phase, .43)
    tail = motion_bump((u - .72) / .28)
    return motion_ease(u / .86, easing) + .035 * rebound * tail, u, tail


def fractional_motion_blur(array, radius, axis):
    """Continuous-width reflected box integration avoids whole-pixel jumps."""
    whole = int(max(0, radius))
    fraction = max(0., radius - whole)
    narrow = box_blur(array, whole, axis, padding_mode="reflect")
    return narrow if fraction < 1e-6 else narrow * (1 - fraction) + box_blur(array, whole + 1, axis, padding_mode="reflect") * fraction


def crop_center(value, scale):
    """C2 soft limit for tracking near the available captured-frame boundary."""
    low, high = .5 / scale, 1 - .5 / scale
    margin = (high - low) * .04
    if margin < 1e-12:
        return .5
    def shoulder(distance):
        u = float(np.clip(distance / margin, 0, 1))
        return margin * u ** 3 * (6 + u * (-8 + 3 * u))
    if value < low + margin:
        return low + shoulder(value - low)
    if value > high - margin:
        return high - shoulder(high - value)
    return value


def resize_float(array, width, height):
    if array.ndim == 2:
        return np.asarray(Image.fromarray(array.astype(np.float32)).resize((width, height), Image.Resampling.BILINEAR), dtype=np.float32)
    return np.stack([resize_float(array[..., channel], width, height) for channel in range(array.shape[-1])], axis=-1)


def glow(array, spread):
    """Two low-resolution halos; bounded scratch space at export resolution."""
    h, w = array.shape[:2]
    small = resize_float(array, max(16, w // 6), max(16, h // 6))
    radius = max(1, round(min(small.shape[:2]) * (.015 + .11 * spread)))
    narrow = box_blur(box_blur(small, radius, 0), radius, 1)
    broad = box_blur(box_blur(narrow, radius * 2, 0), radius * 2, 1)
    return resize_float(.65 * narrow + .35 * broad, w, h)


def defocus(array, radius):
    """Continuous-radius three-box soft focus; not a depth or aperture simulation."""
    if radius <= 1e-6:
        return array
    whole = int(radius)
    fraction = float(radius - whole)
    result = array
    for _ in range(3):
        for axis in (0, 1):
            narrow = box_blur(result, whole, axis)
            result = narrow if fraction < 1e-6 else narrow * (1 - fraction) + box_blur(result, whole + 1, axis) * fraction
    return result


def sample(array, x, y):
    """Bilinear normalized sampling with mirrored edges, never exposed black gaps."""
    h, w = array.shape[:2]
    x = 1 - np.abs(np.mod(x, 2) - 1)
    y = 1 - np.abs(np.mod(y, 2) - 1)
    px, py = x * (w - 1), y * (h - 1)
    ix, iy = px.astype(np.int32), py.astype(np.int32)
    # NumPy promotes float32 minus int32 to float64. Keep interpolation in the
    # declared working precision to halve the hot path's scratch bandwidth.
    fx, fy = (px - ix.astype(np.float32))[..., None], (py - iy.astype(np.float32))[..., None]
    x1, y1 = np.minimum(ix + 1, w - 1), np.minimum(iy + 1, h - 1)
    return ((array[iy, ix] * (1 - fx) + array[iy, x1] * fx) * (1 - fy)
            + (array[y1, ix] * (1 - fx) + array[y1, x1] * fx) * fy).astype(np.float32)


class Compositor:
    def __init__(self, width, height, recipe, *, overlap_frames=None):
        self.width, self.height, self.recipe = width, height, recipe
        self.overlap_frames = overlap_frames if overlap_frames is not None else max(3, frame_count(recipe["duration"]))
        self.x, self.y = np.meshgrid(np.linspace(0, 1, width, dtype=np.float32), np.linspace(0, 1, height, dtype=np.float32))
        rng = np.random.default_rng(recipe["seed"])
        self.noise = np.zeros((height, width), dtype=np.float32)
        # Smooth seeded two-dimensional texture, fixed through time. Plane-wave
        # noise produced repeated bright bars along the narrowed burn edge.
        for rows, weight in ((3, .55), (6, .27), (12, .13), (28, .05)):
            columns = max(3, round(rows * width / height))
            field = rng.uniform(-1, 1, (rows, columns)).astype(np.float32)
            field = np.asarray(Image.fromarray(field).resize((width, height), Image.Resampling.BICUBIC))
            self.noise += weight * field
        self.noise -= self.noise.mean()
        self.noise /= max(.1, float(np.max(np.abs(self.noise))))
        self.frozen_afterimage = None
        self.afterimage_frame = None

    def render(self, a_rgb, b_rgb, progress, *, frame_index=None):
        # Endpoint pixels pass through exactly; retained afterimage state is separate.
        if progress <= 0:
            if self.recipe["id"] == "afterimage-flash":
                # A three-frame seam can cut before its first interior frame.
                # Retain the endpoint even though its pixels pass through exactly.
                self.frozen_afterimage = DECODE_709[a_rgb].copy()
                self.afterimage_frame = frame_index
            return a_rgb.copy()
        if progress >= 1:
            return b_rgb.copy()
        a, b = DECODE_709[a_rgb], DECODE_709[b_rgb]
        r = self.recipe
        identity, strength = r["id"], r["intensity"]
        t = curve(phase(progress, r["cut_phase"]), r["easing"])
        half = max(.018, r["softness"] * .5)
        mix = float(smooth((progress - r["cut_phase"] + half) / (2 * half)))
        base = a * (1 - mix) + b * mix
        energy = pulse(progress, r["peak_phase"], r["attack"], r["decay"])
        tint = np.array([1 + .16 * r["warmth"], 1 - .035 * abs(r["warmth"]), 1 - .25 * r["warmth"]], dtype=np.float32)
        if identity == "cross-dissolve":
            result = a * (1 - t) + b * t
        elif identity == "whip-pan":
            result = self._whip(a, b, progress, mix)
        elif identity == "crash-zoom":
            result = self._zoom(a, b, progress, mix)
        elif identity == "focus-pull":
            radius = min(self.width, self.height) * .045 * strength * energy
            result = defocus(base, radius)
            if r["spread"] > 0 and energy > 0:
                threshold = float(DECODE_709[round(r["threshold"] * 255)])
                highlights = np.maximum(base - threshold, 0) / max(.03, 1 - threshold)
                result = self._light(result, glow(highlights, .3) * energy * r["spread"] * .65)
        elif identity == "prism-push":
            result = self._prism(a, b, mix, energy)
        elif identity == "soft-wipe":
            coordinate = self.x if r["direction"] in {"left", "right"} else self.y
            if r["direction"] in {"right", "down"}:
                coordinate = 1 - coordinate
            matte = smooth((coordinate + t * (1 + 2 * r["softness"]) - 1) / r["softness"])[..., None]
            result = a * (1 - matte) + b * matte
        elif identity == "luma-reveal":
            source = a if r["luma_source"] == "outgoing" else b
            luminance = source @ LUMA
            if r["luma_order"] == "dark":
                luminance = 1 - luminance
            matte = smooth((t * (1 + r["softness"]) - (1 - luminance)) / r["softness"])[..., None]
            result = a * (1 - matte) + b * matte
        elif identity == "highlight-bloom":
            threshold = float(DECODE_709[round(r["threshold"] * 255)])
            highlights = np.maximum(base - threshold, 0) / max(.03, 1 - threshold)
            halo = glow(highlights, r["spread"])
            result = self._light(base, energy * strength * (highlights * .5 + halo * 2.4) * tint)
        elif identity == "film-burn":
            coordinate = self.x if r["direction"] in {"left", "right"} else self.y
            if r["direction"] in {"left", "up"}:
                coordinate = 1 - coordinate
            boundary = 1.35 * t - .18 + self.noise * .24 * r["texture"]
            width = .05 + .3 * r["spread"]
            local_width = width * (1 + self.noise * .2 * r["texture"])
            front = np.exp(-((coordinate - boundary) / local_width) ** 2)
            distance = np.maximum(boundary - coordinate, 0)
            behind = smooth(distance / width) * np.exp(-distance / width)
            # A leak has a local hot edge and a short tail. A constant pedestal
            # lifted every shadow into orange fog, even far from that edge.
            spatial = (front * 1.2 + behind * .3) * (1 + self.noise * .23 * r["texture"])
            hot = np.stack([spatial * 1.4, spatial ** 1.4 * .73, spatial ** 2 * .36], axis=-1)
            result = self._light(base, energy * strength * hot * tint * 2.1)
        elif identity == "lens-sweep":
            horizontal = r["direction"] in {"left", "right"}
            coordinate, cross = (self.x, self.y) if horizontal else (self.y, self.x)
            if r["direction"] in {"left", "up"}:
                coordinate = 1 - coordinate
            center = -.15 + 1.3 * t
            width = .012 + .065 * r["spread"]
            color = []
            for shift in (-1, 0, 1):
                displaced = coordinate - center + shift * .026 * r["chromatic"]
                transverse = cross - r["anchor_ay"]
                core = np.exp(-(displaced / width) ** 2 - (transverse / (width * .7)) ** 2)
                # An anamorphic streak follows the light's travel axis; it is
                # localized around the anchor rather than a full-height wipe.
                streak = np.exp(-np.abs(displaced) / (width * 5)) * np.exp(-(transverse / (.012 + .018 * r["spread"])) ** 2) * .32
                halo = np.exp(-(displaced / (width * 4)) ** 2 - (transverse / (width * 3)) ** 2) * .28
                ghost_center = 1 - center
                ghost_radius = .018 + .025 * r["chromatic"]
                travel_aspect = self.width / self.height if horizontal else self.height / self.width
                ring_distance = np.sqrt(((coordinate - ghost_center) * travel_aspect) ** 2 + (cross - (1 - r["anchor_ay"])) ** 2)
                ring = np.exp(-((ring_distance - ghost_radius * 2) / ghost_radius) ** 2) * .1 * r["chromatic"]
                color.append(core * .8 + streak + halo + ring)
            result = self._light(base, np.stack(color, axis=-1) * energy * strength * 2.8 * tint)
        elif identity == "shutter-flash":
            second_peak = min(.94, r["peak_phase"] + r["pulse_gap"])
            hit = energy + r["second_pulse"] * pulse(progress, second_peak, r["attack"] * .65, r["decay"] * 1.4)
            # Exposure preserves scene texture; a small lifted toe supplies the flash character.
            result = self._light(base, strength * hit * tint * (base * 1.5 + .07))
        elif identity == "afterimage-flash":
            if progress <= r["cut_phase"] or self.frozen_afterimage is None:
                self.frozen_afterimage = a.copy()
                self.afterimage_frame = frame_index
            tail = max(0., 1 - (progress - r["cut_phase"]) / max(.01, 1 - r["cut_phase"])) ** 2 if progress > r["cut_phase"] else 0
            displacement = .009 + r["spread"] * .025
            dx, dy = (displacement, 0) if r["direction"] in {"left", "right"} else (0, displacement)
            if r["direction"] in {"right", "down"}:
                dx, dy = -dx, -dy
            ghost = sample(self.frozen_afterimage, self.x + dx, self.y + dy)
            if r["chromatic"]:
                for channel, offset in ((0, 1 + r["chromatic"]), (2, 1 - r["chromatic"])):
                    ghost[..., channel] = sample(self.frozen_afterimage, self.x + dx * offset, self.y + dy * offset)[..., channel]
            result = base * (1 - r["ghost"] * tail * .55) + ghost * r["ghost"] * tail * .55
            result = self._light(result, strength * energy * (base + .1) * tint)
        elif identity == "light-flash":
            result = self._light(base, strength * energy * tint * (base * 1.2 + .12))
        elif identity == "dip-to-black":
            result = base * (1 - energy * strength)
        else:
            raise ValueError(f"Unsupported overlap recipe: {identity}")
        return encode_rgb(result)

    @staticmethod
    def _light(base, illumination):
        # Exposure-like energy with a bounded highlight shoulder. No hard white overlay.
        illumination = np.maximum(illumination, 0)
        return base + (1 - base) * (-np.expm1(-illumination))

    def _exposure(self, progress):
        # Intensity 0..1 corresponds to a 0..360-degree shutter. Close exposure
        # smoothly at the locked seam endpoints rather than snap a blur away.
        step = 1 / max(2, self.overlap_frames - 1)
        taper = motion_ease(min(progress, 1 - progress) / step, "linear")
        half = .5 * self.recipe["intensity"] * step * taper
        return max(0., progress - half), min(1., progress + half)

    def _whip_pose(self, progress, outgoing):
        """Whole-frame camera move, with captured borders inside a temporary crop."""
        r = self.recipe
        position, u, tail = whip_motion(progress, r["cut_phase"], r["easing"], r["rebound"])
        movement = position - .035 * r["rebound"] * tail
        # Each shot travels in the same screen direction. At the crossover
        # its offset is +/-12% of the captured frame, not a one-viewport panel.
        base = .48 * (movement ** 2 if outgoing else (1 - movement) ** 2)
        rebound = 0. if outgoing else .0084 * r["rebound"] * tail
        offset = base if outgoing else -base + rebound
        # Summing the nonnegative terms covers the late rebound without an
        # absolute-value cusp when its offset crosses the neutral position.
        cover = base + rebound
        zoom = (1 + r["overscan"] * motion_bump(u)) / max(.02, 1 - 2 * cover)
        return offset, zoom

    def _whip(self, a, b, progress, mix):
        r = self.recipe
        horizontal = r["direction"] in {"left", "right"}
        axis, size = (1, self.width) if horizontal else (0, self.height)
        sign = 1 if r["direction"] in {"left", "up"} else -1
        start, end = self._exposure(progress)
        along = self.x if horizontal else self.y
        across = self.y if horizontal else self.x
        def view(frame, outgoing):
            offset, zoom = self._whip_pose(progress, outgoing)
            poses = [self._whip_pose(value, outgoing) for value in np.linspace(start, end, 5)]
            travel = [pose[0] for pose in poses]
            radius = size * (max(travel) - min(travel)) * .5
            uniform = fractional_motion_blur(frame, radius, axis)
            soft = fractional_motion_blur(fractional_motion_blur(frame, radius * .5, axis), radius * .5, axis)
            blurred = uniform * (1 - r["spread"]) + soft * r["spread"]
            coordinate = .5 + sign * offset + (along - .5) / zoom
            cross = .5 + (across - .5) / zoom
            return sample(blurred, coordinate, cross) if horizontal else sample(blurred, cross, coordinate)
        # A short full-frame crossover replaces the old translating A/B strip.
        # There is no moving boundary with two legible adjacent picture panels.
        if mix <= 0:
            return view(a, True)
        if mix >= 1:
            return view(b, False)
        return view(a, True) * (1 - mix) + view(b, False) * mix

    def _zoom_pose(self, progress):
        r = self.recipe
        u = motion_phase(progress, r["cut_phase"])
        power = {"linear": 3, "smooth": 4, "snappy": 5}[r["easing"]]
        movement = motion_bump(u, power)
        # Logarithmic scale gives consistent perceived zoom speed. A single
        # tiny late push settles without shrinking below the captured frame.
        tail = motion_bump((u - .84) / .16)
        scale = math.exp(math.log(r["zoom_amount"]) * movement + .035 * r["rebound"] * tail)
        scale *= 1 + r["overscan"] * motion_bump(u)
        return scale, movement

    def _zoom(self, a, b, progress, mix):
        r = self.recipe
        start, end = self._exposure(progress)
        def view(frame, ax, ay):
            def pose(point):
                scale, movement = self._zoom_pose(point)
                dest_x, dest_y = ax + (.5 - ax) * movement, ay + (.5 - ay) * movement
                # Extreme edge anchors cannot be centered without inventing
                # half an image. Limit tracking to the available crop instead.
                center_x = crop_center(ax + (.5 - dest_x) / scale, scale)
                center_y = crop_center(ay + (.5 - dest_y) / scale, scale)
                return scale, center_x, center_y
            def coverage(poses):
                # Largest source-pixel gap at any corner bounds the gap at
                # every interior point of these affine transforms.
                corners = np.array([[(cx + x / scale) * (self.width - 1),
                                     (cy + y / scale) * (self.height - 1)]
                                    for scale, cx, cy in poses for x, y in
                                    ((-.5, -.5), (.5, -.5), (-.5, .5), (.5, .5))])
                corners = corners.reshape(len(poses), 4, 2)
                return 0. if len(poses) == 1 else float(np.sqrt(np.sum(np.diff(corners, axis=0) ** 2, axis=-1)).max())
            taps = np.linspace(start, end, 9) if end - start > 1e-7 else [progress]
            poses = [pose(point) for point in taps]
            gap = coverage(poses)
            if gap > 1.25:
                taps = np.linspace(start, end, 17)
                poses = [pose(point) for point in taps]
                gap = coverage(poses)
            elif gap * max(1, len(poses) - 1) < .2:
                # No perceptible shutter travel: avoid nine identical samples.
                taps, poses = [progress], [pose(progress)]
                gap = 0.
            # Finite shutter taps can otherwise turn point lights into dotted
            # spokes. Filter only enough to bridge their remaining pixel gap;
            # the scalar bound and separable kernels keep work bounded.
            radius = max(0., (gap - 1) * .5)
            if radius > 0:
                frame = fractional_motion_blur(fractional_motion_blur(frame, radius, 1), radius, 0)
            weights = [1 - r["spread"] * .85 * abs(2 * index / max(1, len(taps) - 1) - 1) for index in range(len(taps))]
            result = np.zeros_like(frame)
            for (scale, center_x, center_y), weight in zip(poses, weights):
                result += sample(frame, center_x + (self.x - .5) / scale, center_y + (self.y - .5) / scale) * weight
            return result / sum(weights)
        # The short picture crossover deliberately leaves just one visible
        # source during most of the zoom. Avoid sampling an invisible branch.
        if mix <= 0:
            return view(a, r["anchor_ax"], r["anchor_ay"])
        if mix >= 1:
            return view(b, r["anchor_bx"], r["anchor_by"])
        aa = view(a, r["anchor_ax"], r["anchor_ay"])
        bb = view(b, r["anchor_bx"], r["anchor_by"])
        return aa * (1 - mix) + bb * mix

    def _prism(self, a, b, mix, energy):
        """A bounded 2D lens-like push; no inferred depth or cross-shot motion."""
        r = self.recipe
        aspect = self.width / self.height
        scale = 1 + (r["zoom_amount"] - 1) * energy

        def view(frame, ax, ay):
            dx, dy = self.x - ax, self.y - ay
            farthest = (max(ax, 1 - ax) * aspect) ** 2 + max(ay, 1 - ay) ** 2
            distance = ((dx * aspect) ** 2 + dy ** 2) / max(farthest, 1e-6)
            bend = 1 + .6 * r["intensity"] * energy * distance
            divisor = scale * bend
            if r["chromatic"] <= 0 or energy <= 0:
                return sample(frame, ax + dx / divisor, ay + dy / divisor)
            result = np.empty_like(frame)
            for channel, sign in enumerate((-1, 0, 1)):
                # A small edge-weighted offset leaves the anchor neutral.
                color_scale = divisor * (1 + sign * .035 * r["chromatic"] * energy * distance)
                result[..., channel] = sample(frame, ax + dx / color_scale, ay + dy / color_scale)[..., channel]
            return result

        if mix <= 0:
            return view(a, r["anchor_ax"], r["anchor_ay"])
        if mix >= 1:
            return view(b, r["anchor_bx"], r["anchor_by"])
        return view(a, r["anchor_ax"], r["anchor_ay"]) * (1 - mix) + view(b, r["anchor_bx"], r["anchor_by"]) * mix


def render_clips(paths, destination: Path, counts, overlap, recipe, *, width, height, quality, cancelled, progress):
    """Stream normalized clips through the seam; retain no decoded clip arrays."""
    from pipeline.transitions import encoding
    deadline = time.monotonic() + 300
    compositor = Compositor(width, height, recipe, overlap_frames=overlap) if overlap else None
    total, output_index = sum(counts) - overlap, 0
    def check():
        if cancelled():
            raise JobCancelled("Transition render cancelled")
        if time.monotonic() > deadline:
            raise ValueError("Transition compositing exceeded its five-minute budget; try Draft quality")
    with av.open(str(paths[0])) as ca, av.open(str(paths[1])) as cb, av.open(str(destination), "w", options={"movflags": "+faststart"}) as output:
        out = output.add_stream("libx264", rate=FPS, options=encoding.options(quality))
        out.width, out.height, out.pix_fmt = width, height, "yuv420p"
        out.codec_context.thread_count = 2
        out.codec_context.sample_aspect_ratio = Fraction(1, 1)
        out.codec_context.color_primaries = 1
        out.codec_context.color_trc = 1
        out.codec_context.colorspace = 1
        out.codec_context.color_range = 1
        a_frames, b_frames = iter(ca.decode(video=0)), iter(cb.decode(video=0))
        def take(frames):
            check()
            try:
                return next(frames)
            except StopIteration as exc:
                raise ValueError("Normalized source has fewer frames than its quantized window") from exc
        def rgb(frame):
            return frame.reformat(format="rgb24", src_colorspace="ITU709", dst_colorspace="ITU709").to_ndarray()
        def write(frame):
            nonlocal output_index
            check()
            frame.pts, frame.time_base = output_index, Fraction(1, FPS)
            for packet in out.encode(frame):
                output.mux(packet)
            output_index += 1
            if output_index % 30 == 0:
                progress(f"Compositing {output_index}/{total} frames")
        for _ in range(counts[0] - overlap):
            write(take(a_frames).reformat(format="yuv420p", src_colorspace="ITU709", dst_colorspace="ITU709"))
        for index in range(overlap):
            frame = compositor.render(rgb(take(a_frames)), rgb(take(b_frames)), index / max(1, overlap - 1), frame_index=index)
            encoded = av.VideoFrame.from_ndarray(frame, format="rgb24").reformat(format="yuv420p", src_colorspace="ITU709", dst_colorspace="ITU709")
            write(encoded)
        for _ in range(counts[1] - overlap):
            write(take(b_frames).reformat(format="yuv420p", src_colorspace="ITU709", dst_colorspace="ITU709"))
        for packet in out.encode():
            output.mux(packet)
    return {"engine": "streaming-pyav-numpy-rgb", "working_space": "linear-rec709", "motion_blur": "spatial-composition-shutter-approximation",
            "endpoint_policy": "first-seam-frame-A-last-seam-frame-B-exact-before-output-encoding",
            "edge_policy": "mirrored-sampling-with-motion-overscan-and-crop-bounded-anchor-tracking",
            "motion_policy": "whole-frame-camera-whip-short-crossover-v1" if recipe["id"] == "whip-pan"
            else "c2-travel-log-zoom-single-rebound-frame-timed-shutter-v1",
            "camera_whip": {"motion_source": "procedural-2d-camera-path",
                            "composition": "whole-frame-crossover-no-adjacent-picture-strip",
                            "source_translation_at_cut": .12,
                            "edge_policy": "temporary-sourcewise-crop-covers-translation-and-rebound",
                            "cut_blend_phase_span": max(.036, recipe["softness"]),
                            "source_motion_matching": False} if recipe["id"] == "whip-pan" else None,
            "motion_shutter": {"angle_degrees": recipe["intensity"] * 360, "phase_frame_step": 1 / max(2, overlap - 1),
                               "endpoint_exposure_taper": True,
                               **({"directional_sampling": "per-source-spatial-kernel-at-frame-timed-offset-travel"}
                                  if recipe["id"] == "whip-pan" else {"max_radial_samples": 17,
                                  "radial_sampling": "adaptive-taps-with-gap-aware-prefilter"})}
            if recipe["id"] in {"whip-pan", "crash-zoom"} else None,
            "afterimage_overlap_frame": compositor.afterimage_frame if compositor else None,
            "afterimage_normalized_outgoing_frame": (counts[0] - overlap + compositor.afterimage_frame)
            if compositor and compositor.afterimage_frame is not None else None,
            "pyav_version": av.__version__, "numpy_version": np.__version__, "frame_count": output_index,
            "encoding": encoding.record(quality)}
