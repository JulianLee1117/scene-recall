"""Endpoint motion continuity on the actual output grid and bounded flow tails."""
from fractions import Fraction

import av
import numpy as np
import pytest

from pipeline.lab.media import probe_media
from pipeline.transitions import jobs, retiming
from pipeline.transitions.contracts import Retime


def native_grid(clip, fps):
    return np.arange(np.ceil(clip["source_start"] * fps), np.ceil(clip["source_end"] * fps)) / fps


@pytest.mark.parametrize("mode,speed", [("rush", 4), ("pulse", 4), ("slow-hit", .25)])
@pytest.mark.parametrize("outgoing", [True, False])
@pytest.mark.parametrize("fps", [30, 60])
@pytest.mark.parametrize("span", [.1, .5, 1.])
@pytest.mark.parametrize("curve", ["smooth", "snappy"])
def test_alignment_is_monotone_local_and_preserves_windows_and_frame_counts(mode, speed, outgoing, fps, span, curve):
    clip = {"source_start": .013, "source_end": 2.013}
    settings = Retime(mode=mode, speed=speed, span=span, curve=curve).model_dump()
    timing = retiming.plan(clip, settings, outgoing=outgoing)
    native = native_grid(clip, fps)
    targets, receipt = retiming.sample_targets(timing, clip, settings, outgoing=outgoing, native_times=native)
    grid = np.arange(timing["frame_count"]) / 30
    nominal = np.clip(np.interp(grid, timing["output_knots"], timing["source_knots"]) + clip["source_start"], native[0], native[-1])
    nominal[0], nominal[-1] = native[0], native[-1]
    ramp = np.interp([timing["ramp_source_start"], timing["ramp_source_end"]], timing["source_knots"], timing["output_knots"])
    outside = (grid < ramp[0]) | (grid > ramp[1])
    assert targets[outside] == pytest.approx(nominal[outside], abs=1e-12)
    assert len(targets) == timing["frame_count"]
    assert targets[0] == native[0] and targets[-1] == native[-1]
    assert np.diff(targets).min() >= 0
    assert np.all((targets >= clip["source_start"]) & (targets < clip["source_end"]))
    assert receipt["policy"] == "ramp-local-endpoint-time-correction-v1"


@pytest.mark.parametrize("outgoing", [True, False])
def test_pulse_alignment_leaves_its_other_half_unchanged_when_resolved(outgoing):
    clip = {"source_start": .01, "source_end": 2.01}
    settings = Retime(mode="pulse", speed=4, span=1).model_dump()
    timing = retiming.plan(clip, settings, outgoing=outgoing)
    native = native_grid(clip, 60)
    targets, correction = retiming.sample_targets(timing, clip, settings, outgoing=outgoing, native_times=native)
    grid = np.arange(timing["frame_count"]) / 30
    nominal = np.clip(np.interp(grid, timing["output_knots"], timing["source_knots"]) + clip["source_start"], native[0], native[-1])
    nominal[0], nominal[-1] = native[0], native[-1]
    peak_source = (timing["ramp_source_start"] + timing["ramp_source_end"]) / 2
    peak_time = np.interp(peak_source, timing["source_knots"], timing["output_knots"])
    unchanged = grid <= peak_time if outgoing else grid >= peak_time
    assert correction["applied"]
    assert targets[unchanged] == pytest.approx(nominal[unchanged], abs=1e-12)


@pytest.mark.parametrize("outgoing", [True, False])
def test_undersampled_short_ramp_retains_real_endpoints_without_reverse_or_extra_frames(outgoing):
    clip = {"source_start": 0., "source_end": .2}
    settings = Retime(mode="rush", speed=4, span=.1).model_dump()
    timing = retiming.plan(clip, settings, outgoing=outgoing)
    targets, _ = retiming.sample_targets(timing, clip, settings, outgoing=outgoing, native_times=[0., .1])
    assert len(targets) == timing["frame_count"]
    assert targets[0] == 0 and targets[-1] == .1
    assert np.diff(targets).min() >= 0


def test_sparse_native_frames_use_monotone_limited_slopes_instead_of_reversing():
    clip = {"source_start": .026, "source_end": 2.026}
    settings = Retime(mode="slow-hit", speed=.25, span=.2).model_dump()
    timing = retiming.plan(clip, settings, outgoing=False)
    native = native_grid(clip, 5)
    targets, receipt = retiming.sample_targets(timing, clip, settings, outgoing=False, native_times=native)
    assert receipt["curve"] == "monotone-cubic-limited-boundary-slopes"
    assert targets[0] == native[0] and targets[-1] == native[-1]
    assert np.diff(targets).min() >= 0
    normal = np.arange(len(targets)) / 30 > timing["output_knots"][-2]
    # Source frames are sparse, but the fractional source target clock still
    # recovers to nominal1x outside the ramp; only captured pixels may repeat.
    normal[-1] = False
    grid = np.arange(len(targets)) / 30
    expected = np.interp(grid, timing["output_knots"], timing["source_knots"]) + clip["source_start"]
    expected = np.clip(expected, native[0], native[-1])
    assert targets[normal] == pytest.approx(expected[normal])


@pytest.mark.parametrize("fps", [30, 60])
def test_four_x_rush_has_no_seven_x_final_target_snap(fps):
    clip = {"source_start": 0., "source_end": 2.}
    settings = Retime(mode="rush", speed=4, span=.5).model_dump()
    timing = retiming.plan(clip, settings, outgoing=True)
    native = native_grid(clip, fps)
    targets, _ = retiming.sample_targets(timing, clip, settings, outgoing=True, native_times=native)
    assert timing["frame_count"] == 52, "Keep the existing duration contract"
    speed = np.diff(targets) * 30
    assert 3.5 < speed[-1] < 4.1
    assert speed[-1] - speed[-2] < 1
    assert np.diff(speed[-6:]).min() > 0, "The rush should build into its endpoint"


@pytest.mark.parametrize("overlap", [0, 9])
def test_visible_cut_receipt_measures_the_corrected_sample_grid(overlap):
    clip = {"source_start": 0., "source_end": 2.}
    settings = Retime(mode="rush", speed=4, span=.5).model_dump()
    plans = [retiming.plan(clip, settings, outgoing=side) for side in (True, False)]
    targets = [retiming.sample_targets(plan, clip, settings, outgoing=side, native_times=native_grid(clip, 30))[0]
               for plan, side in zip(plans, (True, False))]
    sources = [{"samples": [{"source_target": float(value)} for value in target]} for target in targets]
    request = {"outgoing": clip, "incoming": clip, "recipe": {"cut_phase": .5}}
    result = retiming.cut_receipt(plans, settings, request, overlap, sampled_sources=sources)
    for index, row in enumerate(result["at_picture_cut"]):
        position = (len(targets[index]) - overlap if index == 0 else 0) + .5 * (overlap - 1) if overlap else len(targets[index]) - 1 if index == 0 else 0
        expected = np.interp(position, np.arange(len(targets[index]) - 1) + .5, np.diff(targets[index]) * 30)
        assert row["sample_grid_speed"] == pytest.approx(expected)
        assert row["source_time"] == pytest.approx(np.interp(position, np.arange(len(targets[index])), targets[index]))
        assert row["speed_measurement"] == "actual-source-target-grid"
    assert result["at_picture_cut"][0]["sample_grid_speed"] != pytest.approx(result["at_picture_cut"][0]["nominal_speed"])


@pytest.fixture(params=[30, 60])
def moving_source(tmp_path, request):
    fps = request.param
    path = tmp_path / "motion.nut"
    with av.open(str(path), "w") as movie:
        stream = movie.add_stream("ffv1", rate=fps)
        stream.width, stream.height, stream.pix_fmt = 128, 72, "bgr0"
        for index in range(int(2.1 * fps)):
            image = np.full((72, 128, 3), 16, dtype=np.uint8)
            x = round(8 + index / fps * 40)
            image[22:50, x:x + 8] = 220
            frame = av.VideoFrame.from_ndarray(image, format="rgb24")
            frame.pts, frame.time_base = index, Fraction(1, fps)
            for packet in stream.encode(frame):
                movie.mux(packet)
        for packet in stream.encode():
            movie.mux(packet)
    video = next(row for row in probe_media(path)["streams"] if row["codec_type"] == "video")
    spatial, _ = jobs.spatial_color_filter(128, 72, {"fit": "fill"}, video)
    return path, video, spatial, fps


def centroids(path):
    with av.open(str(path)) as movie:
        return [np.where(frame.to_ndarray(format="rgb24").mean(axis=2) > 100)[1].mean()
                for frame in movie.decode(video=0)]


@pytest.mark.parametrize("outgoing", [True, False])
def test_encoded_rush_keeps_cut_motion_and_native_endpoint_ownership(moving_source, tmp_path, outgoing):
    source, video, spatial, fps = moving_source
    clip = {"source_start": 0. if outgoing else .013, "source_end": 2. if outgoing else 2.013}
    settings = Retime(mode="rush", speed=4, span=.5).model_dump()
    destination = tmp_path / "rush.mp4"
    receipt = retiming.prepare_clip(source, destination, clip, settings, outgoing=outgoing,
                                    width=128, height=72, spatial=spatial, video=video)
    samples = receipt["samples"]
    assert samples[0]["source_target"] == receipt["native_pts"][0]
    assert samples[-1]["source_target"] == receipt["native_pts"][-1]
    assert receipt["frame_count"] == 52
    assert receipt["endpoint_correction"]["applied"]
    assert samples[0]["method"] == samples[-1]["method"] == "native-nearest"
    positions = centroids(destination)
    step = positions[-1] - positions[-2] if outgoing else positions[1] - positions[0]
    assert 3 <= step <= 6.5, "The 40px/s fixture should move about 5px at a 4x/30fps cut, not snap about10px"
    assert not list(tmp_path.glob("*.native.nut"))


def test_flow_keeps_final_real_intervals_and_does_not_freeze_before_endpoint(moving_source, tmp_path):
    source, video, spatial, _fps = moving_source
    clip = {"source_start": 0., "source_end": 2.}
    destination = tmp_path / "slow.mp4"
    receipt = retiming.prepare_clip(source, destination, clip,
                                    Retime(mode="slow-hit", speed=.25, span=.5, interpolation="flow").model_dump(),
                                    outgoing=True, width=128, height=72, spatial=spatial, video=video)
    samples = receipt["samples"]
    assert all(row["method"] == "ffmpeg-minterpolate-mci-nearest" for row in samples[-10:-1])
    assert samples[-1]["method"] == "native-nearest"
    assert np.diff([row["source_target"] for row in samples[-10:]]).min() > 0
    assert samples[-1]["source_target"] == receipt["native_pts"][-1] < 2
    assert receipt["flow"]["end_hold_context_seconds"] == .25
    assert receipt["flow"]["end_hold_context_source"] == "last-retained-native-frame"
    assert np.ptp(centroids(destination)[-6:]) >= 1, "Real generated pixels must keep moving before the native endpoint"
    assert not list(tmp_path.glob("*.flow.nut"))
