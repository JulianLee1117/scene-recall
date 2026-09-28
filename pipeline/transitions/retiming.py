"""Bounded native-PTS source retiming, separate from the visual seam compositor.

The user chooses source windows; a monotonic speed integral changes their output
duration without borrowing footage outside those windows. Flow is local CPU
motion compensation within each source's ramp, never between unrelated shots.
"""
from __future__ import annotations

from fractions import Fraction
import math
import logging
from pathlib import Path
import time

import av
import numpy as np

from pipeline.lab.media import JobCancelled, run_process
from pipeline.transitions.contracts import FPS, frame_count

STEPS = 2048
POLICY = "source-speed-integral-2048-v1"
SAMPLING_POLICY = "output-frame-time-inverse-speed-integral-fixed30fps-v3"
MAX_SCRATCH_BYTES = 1024 ** 3
_LOG = logging.getLogger(__name__)


def _ease(value, curve):
    if curve == "snappy":
        return 4 * value ** 3 if value < .5 else 1 - (-2 * value + 2) ** 3 / 2
    return value * value * (3 - 2 * value)


def plan(clip, retime, *, outgoing):
    """Integrate reciprocal speed over source time; this also drives UI timing.

    Trapezoids use exactly 2048 equal SOURCE-time intervals within the span.
    Outside the span speed is 1. Output frame count rounds half ties up.
    """
    duration = clip["source_end"] - clip["source_start"]
    active = retime["mode"] != "off"
    span = min(retime["span"], duration) if active else 0.
    start = duration - span if outgoing else 0.
    source, output = [0.], [0.]
    if start > 0:
        source.append(start)
        output.append(start)
    elapsed, previous = start, 1.
    for index in range(STEPS + 1) if active else ():
        u = index / STEPS
        weight = 1 - abs(2 * u - 1) if retime["mode"] == "pulse" else u if outgoing else 1 - u
        reciprocal = 1 / (1 + (retime["speed"] - 1) * _ease(weight, retime["curve"]))
        if index:
            elapsed += span / STEPS * (previous + reciprocal) / 2
            source.append(start + u * span)
            output.append(elapsed)
        previous = reciprocal
    if not active:
        source, output = [0., duration], [0., duration]
    elif not outgoing:
        source.append(duration)
        output.append(elapsed + duration - span)
    # Avoid floating-point accumulation moving a mathematical half-frame tie.
    continuous = duration if not active or retime["speed"] == 1 else output[-1]
    count = max(2, frame_count(continuous))
    return {"policy": POLICY, "source_duration": duration, "continuous_duration": continuous,
            "frame_count": count, "output_duration": count / FPS,
            "ramp_source_start": start, "ramp_source_end": start + span,
            "source_knots": source, "output_knots": output}


def _timestamps(path, origin, check, *, limit=4096):
    with av.open(str(path)) as container:
        stream = container.streams.video[0]
        times = []
        for packet in container.demux(stream):
            check()
            if packet.pts is not None:
                times.append(float(packet.pts * stream.time_base) - origin)
                if len(times) > limit:
                    raise ValueError("This source window exceeds the 4096-native-frame retiming budget")
    return sorted(set(times))


def sample_targets(timing, clip, retime, *, outgoing, native_times):
    """Align the cut-facing native endpoint across its ramp, not in one jump.

    The integral and rounded frame count stay unchanged. Only output times inside
    the existing ramp are adjusted; the normal-speed region keeps its frame
    clock. A quintic correction has zero first/second derivatives at its edges.
    Very compressed, poorly sampled regions use a monotone cubic with limited
    slopes instead of allowing a reversal. No extra source footage is read.
    """
    grid = np.arange(timing["frame_count"], dtype=np.float64) / FPS
    source_knots, output_knots = timing["source_knots"], timing["output_knots"]
    origin = clip["source_start"]
    nominal = np.interp(grid, output_knots, source_knots) + origin
    mapped = grid.copy()
    record = {"policy": "ramp-local-endpoint-time-correction-v1", "applied": False,
              "side": "outgoing" if outgoing else "incoming",
              "output_time_shift_seconds": 0., "max_source_shift_seconds": 0.}
    if retime["mode"] != "off" and retime["speed"] != 1:
        begin, end = timing["ramp_source_start"], timing["ramp_source_end"]
        full_anchor = float(np.interp(begin if outgoing else end, source_knots, output_knots))
        anchor = full_anchor
        if retime["mode"] == "pulse":
            anchor = float(np.interp((begin + end) / 2, source_knots, output_knots))
            # Keep the pulse peak unchanged when its cut-facing half has room
            # to carry the correction. Sub-two-interval halves need the full
            # ramp; their discrete peak cannot be treated as an exact speed.
            if (grid[-1] - anchor if outgoing else anchor) < 2 / FPS:
                anchor = full_anchor
        native_endpoint = native_times[-1] if outgoing else native_times[0]
        desired = float(np.interp(native_endpoint - origin, source_knots, output_knots))
        x0, x1 = (anchor, float(grid[-1])) if outgoing else (0., anchor)
        y0, y1 = (anchor, desired) if outgoing else (desired, anchor)
        if x1 > x0 + 1e-12 and y1 > y0 + 1e-12:
            region = (grid >= x0) & (grid <= x1)
            u = np.clip((grid[region] - x0) / (x1 - x0), 0., 1.)
            secant = (y1 - y0) / (x1 - x0)
            if secant >= 7 / 15:
                ease = u ** 3 * (u * (u * 6 - 15) + 10)
                change = desired - (float(grid[-1]) if outgoing else 0.)
                mapped[region] = grid[region] + change * (ease if outgoing else 1 - ease)
                curve = "quintic-zero-boundary-derivatives"
            else:
                # A quintic would run backward if the native endpoint consumes
                # most of this short ramp. Equal Hermite tangents <=3*secant
                # guarantee a monotone curve, including its midpoint.
                slope = min(1., 3 * secant)
                mapped[region] = (y0 + (y1 - y0) * (3 * u ** 2 - 2 * u ** 3)
                                  + (x1 - x0) * slope * (2 * u ** 3 - 3 * u ** 2 + u))
                curve = "monotone-cubic-limited-boundary-slopes"
            record.update(applied=bool(np.any(np.abs(mapped - grid) > 1e-12)), curve=curve,
                          output_region=[x0, x1], integral_time_region=[y0, y1],
                          output_time_shift_seconds=desired - (float(grid[-1]) if outgoing else 0.),
                          output_intervals=(x1 - x0) * FPS)
    targets = np.clip(np.interp(mapped, output_knots, source_knots) + origin,
                      native_times[0], native_times[-1])
    targets[0], targets[-1] = native_times[0], native_times[-1]
    if np.any(np.diff(targets) < -1e-9):
        raise ValueError("Endpoint-aligned source timing must remain monotonic")
    record["max_source_shift_seconds"] = float(np.max(np.abs(targets - np.clip(nominal, native_times[0], native_times[-1]))))
    return targets, record


def cut_receipt(plans, retime, request, overlap, *, sampled_sources=None):
    """Report what survives the overlap rather than promise a visible edge peak."""
    rows = []
    for index, timing in enumerate(plans):
        if not overlap:
            position = timing["frame_count"] - 1 if index == 0 else 0
        else:
            position = (timing["frame_count"] - overlap if index == 0 else 0) + request["recipe"]["cut_phase"] * (overlap - 1)
        output_phase = position / FPS
        source = float(np.interp(output_phase, timing["output_knots"], timing["source_knots"]))
        span = timing["ramp_source_end"] - timing["ramp_source_start"]
        speed = 1.
        if retime["mode"] != "off" and span and timing["ramp_source_start"] <= source <= timing["ramp_source_end"]:
            u = (source - timing["ramp_source_start"]) / span
            weight = 1 - abs(2 * u - 1) if retime["mode"] == "pulse" else u if index == 0 else 1 - u
            speed = 1 + (retime["speed"] - 1) * _ease(weight, retime["curve"])
        clip = request["outgoing" if index == 0 else "incoming"]
        source_time, grid_speed = clip["source_start"] + source, speed
        samples = sampled_sources[index].get("samples", []) if sampled_sources and index < len(sampled_sources) else []
        actual_grid = len(samples) == timing["frame_count"] and len(samples) > 1
        if actual_grid:
            targets = np.asarray([row["source_target"] for row in samples], dtype=np.float64)
            source_time = float(np.interp(position, np.arange(len(targets)), targets))
            grid_speed = float(np.interp(position, np.arange(len(targets) - 1) + .5, np.diff(targets) * FPS))
        rows.append({"side": "outgoing" if index == 0 else "incoming",
                     "source_time": source_time, "nominal_speed": speed,
                     "sample_grid_speed": grid_speed,
                     "speed_measurement": "actual-source-target-grid" if actual_grid else "nominal-speed-integral"})
    return {"requested_edge_or_peak_speed": retime["speed"], "at_picture_cut": rows,
            "note": "Overlap can hide the source-edge speed peak; shorten the overlap or widen the source span."}


class _Sampler:
    """Monotonic two-frame bracket, independent of source frame rate or VFR."""
    def __init__(self, path, origin, check):
        self.container = av.open(str(path))
        self.frames = iter(self.container.decode(video=0))
        self.origin, self.check = origin, check
        self.left = self.right = self._next()

    def _next(self):
        self.check()
        frame = next(self.frames, None)
        if frame is None:
            return None
        pixels = frame.reformat(format="rgb24", src_colorspace="ITU709", dst_colorspace="ITU709").to_ndarray()
        return float(frame.pts * frame.time_base) - self.origin, pixels

    def at(self, target, *, blend=False):
        while self.right is not None and self.right[0] < target:
            self.left, self.right = self.right, self._next()
        left, right = self.left, self.right or self.left
        if left is None:
            raise ValueError("The native source window has no frames")
        weight = max(0., min(1., (target - left[0]) / max(1e-12, right[0] - left[0])))
        if blend and left[0] != right[0]:
            from pipeline.transitions.compositor import DECODE_709, encode_rgb
            pixels = encode_rgb(DECODE_709[left[1]] * (1 - weight) + DECODE_709[right[1]] * weight)
        else:
            pixels = right[1] if weight >= .5 else left[1]
        return pixels, {"bracket_pts": [left[0], right[0]], "weight": weight if blend else int(weight >= .5)}

    def close(self):
        self.container.close()


def prepare_clip(source, destination, clip, retime, *, outgoing, width, height, spatial,
                 video, origin=0., cancelled=lambda: False, progress=lambda _: None):
    """Normalize native frames, then retime to a bounded fixed-30fps clip.

    Native intermediates are disk-backed and removed on success, error or cancel.
    Only two frame brackets plus a timestamp list are kept in process memory.
    """
    started = time.monotonic()
    deadline = started + 240
    timing = plan(clip, retime, outgoing=outgoing)
    destination = Path(destination)
    native = destination.with_suffix(".native.nut")
    dense = destination.with_suffix(".flow.nut")
    readers = []
    def check():
        if cancelled():
            raise JobCancelled("Source speed preparation cancelled")
        if time.monotonic() > deadline:
            raise ValueError("Source speed preparation exceeded four minutes; use nearest or blend sampling")
        if sum(path.stat().st_size for path in (native, dense) if path.exists()) > MAX_SCRATCH_BYTES:
            raise ValueError("Source speed preparation exceeded its 1 GiB scratch budget; shorten the source window or use Draft quality")
    def command(arguments, budget=180):
        check()
        return run_process(arguments, cancelled=lambda: check() or False, timeout=min(budget, max(1, deadline - time.monotonic())))
    try:
        begin, end = clip["source_start"] + origin, clip["source_end"] + origin
        # Decode ahead from a safe earlier point; seeking exactly next to a cut
        # can otherwise select its later keyframe by DTS instead of display PTS.
        preseek = max(0., clip["source_start"] - 1.)
        # Some demuxers cannot seek to negative absolute timestamps and jump
        # forward to the first positive keyframe. Decode from the beginning
        # when this bounded preroll lies at/before absolute zero instead.
        if preseek + origin <= 0:
            preseek = 0.
        seek = ["-ss", str(preseek)] if preseek else []
        command(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-copyts",
                 *seek, "-t", str(clip["source_end"] - preseek + .1), "-i", str(source),
                 "-map", "0:v:0", "-an", "-vf",
                 f"trim=start={begin:.9f}:end={end:.9f},setpts=PTS-({origin:.9f})/TB,{spatial}",
                 "-fps_mode", "passthrough", "-enc_time_base", video.get("time_base", "1/90000"),
                 "-c:v", "ffv1", "-level", "3", "-threads", "2", "-frames:v", "4097",
                 "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709", "-color_range", "tv", str(native)])
        # A fixed origin translation retains native cadence in source-player
        # seconds and avoids asking the NUT muxer to preserve negative PTS.
        native_times = _timestamps(native, 0., check)
        if not native_times or native_times[0] < clip["source_start"] - 1e-5 or native_times[-1] >= clip["source_end"] + 1e-5:
            raise ValueError("Native source timing differs from the selected window")
        scratch_peak = native.stat().st_size
        native_reader = _Sampler(native, 0., check)
        readers.append(native_reader)
        flow_reader, flow_times, flow_rate, flow_end_hold = None, [], None, 0.
        ramp_start = clip["source_start"] + timing["ramp_source_start"]
        ramp_end = clip["source_start"] + timing["ramp_source_end"]
        if retime["interpolation"] == "flow":
            flow_rate = min(120, max(60, math.ceil(FPS / min(1., retime["speed"]))))
            context_start = max(clip["source_start"], ramp_start - .25)
            context_end = min(clip["source_end"], ramp_end + .25)
            # minterpolate needs look-ahead to emit its last real intervals.
            # Supply bounded copies of the retained final frame, never footage
            # outside the selected window. Targets remain <= native_times[-1].
            flow_end_hold = .25 if context_end >= clip["source_end"] - 1e-9 else 0.
            end_context = f"tpad=stop_mode=clone:stop_duration={flow_end_hold}," if flow_end_hold else ""
            progress("Interpolating the source speed region with local CPU motion compensation")
            command(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-copyts",
                     "-i", str(native), "-map", "0:v:0", "-an", "-vf",
                     f"trim=start={context_start:.9f}:end={context_end:.9f},"
                     f"{end_context}"
                     f"minterpolate=fps={flow_rate}:mi_mode=mci:mc_mode=aobmc:me_mode=bidir:vsbmc=1:scd=fdiff:scd_threshold=10,format=yuv444p",
                     "-fps_mode", "passthrough", "-enc_time_base", f"1/{flow_rate}",
                     "-c:v", "ffv1", "-level", "3", "-threads", "2", str(dense)])
            flow_times = _timestamps(dense, 0., check)
            if len(flow_times) < 2:
                raise ValueError("This speed region has too few native frames for flow; choose nearest or blend")
            flow_reader = _Sampler(dense, 0., check)
            readers.append(flow_reader)
            scratch_peak += dense.stat().st_size
        count = timing["frame_count"]
        # Frame timestamps are n / FPS. Including the exclusive window end
        # would stretch every sample and even skip a frame at nominal 1x.
        # Endpoint alignment stays inside the ramp rather than globally
        # stretching the frame clock or snapping its last source target.
        targets, correction = sample_targets(timing, clip, retime, outgoing=outgoing, native_times=native_times)
        samples = []
        with av.open(str(destination), "w") as output:
            stream = output.add_stream("libx264", rate=FPS, options={"crf": "14", "preset": "fast"})
            stream.width, stream.height, stream.pix_fmt = width, height, "yuv420p"
            stream.codec_context.thread_count = 2
            stream.codec_context.sample_aspect_ratio = Fraction(1, 1)
            stream.codec_context.color_primaries = stream.codec_context.color_trc = stream.codec_context.colorspace = 1
            stream.codec_context.color_range = 1
            for index, target in enumerate(targets):
                check()
                interior = 0 < index < count - 1
                in_ramp = ramp_start <= target <= ramp_end
                blend = interior and in_ramp and retime["interpolation"] == "blend"
                pixels, native_sample = native_reader.at(float(target), blend=blend)
                method = "linear-rec709-frame-blend" if blend else "native-nearest"
                flow_sample = None
                if interior and in_ramp and flow_reader and flow_times[0] <= target <= flow_times[-1]:
                    pixels, flow_sample = flow_reader.at(float(target))
                    method = "ffmpeg-minterpolate-mci-nearest"
                frame = av.VideoFrame.from_ndarray(pixels, format="rgb24").reformat(format="yuv420p", src_colorspace="ITU709", dst_colorspace="ITU709")
                frame.pts, frame.time_base = index, Fraction(1, FPS)
                for packet in stream.encode(frame):
                    output.mux(packet)
                samples.append({"output_frame": index, "output_time": index / FPS, "source_target": float(target),
                                "native": native_sample, "method": method, **({"flow": flow_sample} if flow_sample else {})})
                if index % 30 == 0:
                    progress(f"Retiming source frame {index + 1}/{count}")
            for packet in stream.encode():
                output.mux(packet)
        return {"policy": POLICY, "request": retime, "side": "outgoing" if outgoing else "incoming",
                "source_window": [clip["source_start"], clip["source_end"]], "native_pts": native_times,
                "native_time_base": video.get("time_base"), "container_origin": origin,
                "native_pts_space": "source-player-seconds-raw-pts-minus-container-origin",
                "source_duration": timing["source_duration"], "continuous_duration": timing["continuous_duration"],
                "output_duration": count / FPS, "frame_count": count,
                "ramp_source_window": [ramp_start, ramp_end],
                "endpoint_policy": "first-and-last-native-frame-in-window-locked",
                "sampling_policy": SAMPLING_POLICY, "endpoint_correction": correction,
                "interpolation_scope": "ramp-only-native-nearest-outside-and-at-endpoints",
                "flow": {"engine": "ffmpeg-minterpolate-mci-aobmc-bidir", "dense_fps": flow_rate,
                         "scene_detection": "fdiff-threshold10", "context_source_seconds": .25,
                         "end_hold_context_seconds": flow_end_hold,
                         "end_hold_context_source": "last-retained-native-frame",
                         "not_neural": True} if flow_reader else None,
                "samples": samples, "seconds": round(time.monotonic() - started, 3), "scratch_bytes_peak": scratch_peak}
    finally:
        for reader in readers:
            reader.close()
        for path in (native, dense):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                _LOG.warning("Could not remove transition retiming intermediate %s", path)
