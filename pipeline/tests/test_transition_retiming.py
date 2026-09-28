"""Native-time speed maps and real bounded sampling, separate from visual blur."""
from copy import deepcopy
from fractions import Fraction
import json

import av
import numpy as np
import pytest
from pydantic import ValidationError

from pipeline.lab.media import JobCancelled, probe_media, run_process
from pipeline.transitions import jobs, retiming
from pipeline.transitions.contracts import Retime, RenderRequest, frame_count
from pipeline.tests.test_transitions import sources as sources


def options(**kwargs):
    return Retime(**kwargs).model_dump()


@pytest.mark.parametrize("mode,speed", [("rush", 3), ("slow-hit", .4), ("pulse", 3)])
@pytest.mark.parametrize("curve", ["smooth", "snappy"])
def test_mapping_is_monotonic_symmetric_and_keeps_fixed_source_range(mode, speed, curve):
    clip = {"source_start": 10., "source_end": 12.}
    request = options(mode=mode, speed=speed, span=.8, curve=curve)
    a, b = [retiming.plan(clip, request, outgoing=side) for side in (True, False)]
    assert a["continuous_duration"] == pytest.approx(b["continuous_duration"])
    assert a["continuous_duration"] < 2 if speed > 1 else a["continuous_duration"] > 2
    for result in (a, b):
        assert result["source_knots"][0] == 0 and result["source_knots"][-1] == 2
        assert np.diff(result["source_knots"]).min() >= 0
        assert np.diff(result["output_knots"]).min() >= 0
        assert result["frame_count"] == frame_count(result["continuous_duration"])


def test_mode_constraints_and_overlap_use_retimed_output_duration():
    for change in ({"mode": "rush", "speed": .5}, {"mode": "slow-hit", "speed": 2},
                   {"mode": "off", "speed": 2}, {"mode": "off", "interpolation": "flow"},
                   {"mode": "pulse", "speed": float("nan")}):
        with pytest.raises(ValidationError):
            Retime(**change)
    body = {"outgoing": {"film_id": "a", "source_start": 0, "source_end": 1},
            "incoming": {"film_id": "b", "source_start": 0, "source_end": 1},
            "recipe": {"duration": .8}, "retime": {"mode": "rush", "speed": 4, "span": 1}}
    with pytest.raises(ValidationError, match="retimed clips"):
        RenderRequest.model_validate(body)
    body["retime"] = {"mode": "slow-hit", "speed": .5, "span": .5}
    assert RenderRequest.model_validate(body)
    body["retime"]["span"] = 1.5
    with pytest.raises(ValidationError, match="span.*fit"):
        RenderRequest.model_validate(body)


def test_speed_one_keeps_exact_half_frame_rounding():
    for outgoing in (True, False):
        result = retiming.plan({"source_start": 0., "source_end": .35}, options(mode="rush", span=.2), outgoing=outgoing)
        assert result["continuous_duration"] == .35 and result["frame_count"] == 11


@pytest.mark.parametrize("outgoing", [True, False])
def test_real_speed_one_retains_every_native_frame_without_global_rescaling(tmp_path, outgoing):
    source = tmp_path / "cadence.nut"
    levels = np.arange(30) * 7 + 20
    with av.open(str(source), "w") as container:
        stream = container.add_stream("ffv1", rate=30)
        stream.width, stream.height, stream.pix_fmt = 64, 48, "bgr0"
        for index, level in enumerate(levels):
            frame = av.VideoFrame.from_ndarray(np.full((48, 64, 3), level, dtype=np.uint8), format="rgb24")
            frame.pts, frame.time_base = index, Fraction(1, 30)
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    video = next(v for v in probe_media(source)["streams"] if v["codec_type"] == "video")
    spatial, _ = jobs.spatial_color_filter(64, 48, {"fit": "fill"}, video)
    output = tmp_path / "cadence.mp4"
    receipt = retiming.prepare_clip(source, output, {"source_start": 0., "source_end": 1.},
                                    options(mode="rush", speed=1, span=.5), outgoing=outgoing,
                                    width=64, height=48, spatial=spatial, video=video)
    assert receipt["frame_count"] == 30
    samples = receipt["samples"]
    selected = [row["native"]["bracket_pts"][row["native"]["weight"]] for row in samples]
    assert selected == pytest.approx(np.arange(30) / 30)
    assert [row["source_target"] for row in samples] == pytest.approx(np.arange(30) / 30)
    with av.open(str(output)) as movie:
        rendered = [frame.to_ndarray(format="rgb24").mean() for frame in movie.decode(video=0)]
    # Each gray level encodes its frame number: no missing or duplicated cadence.
    assert np.abs(np.asarray(rendered) - levels).max() < 3


def test_cut_receipt_uses_the_output_frame_clock():
    clip = {"source_start": 10., "source_end": 12.}
    request = {"outgoing": clip, "incoming": clip, "recipe": {"cut_phase": .5}}
    speed = options(mode="rush", speed=3, span=.8)
    plans = [retiming.plan(clip, speed, outgoing=side) for side in (True, False)]
    rows = retiming.cut_receipt(plans, speed, request, 6)["at_picture_cut"]
    for index, (row, timing) in enumerate(zip(rows, plans)):
        position = (timing["frame_count"] - 6 if index == 0 else 0) + 2.5
        expected = np.interp(position / 30, timing["output_knots"], timing["source_knots"]) + 10.
        assert row["source_time"] == pytest.approx(expected)
        assert row["sample_grid_speed"] == row["nominal_speed"]


@pytest.mark.parametrize("offset,start,interpolation", [(5, .1, "nearest"), (5, 1.1, "nearest"),
                                                        (-1, .1, "nearest"), (-1, 1.1, "nearest"),
                                                        (5, .1, "flow"), (-1, .1, "flow")])
def test_container_origin_translation_preserves_the_native_source_window(tmp_path, offset, start, interpolation):
    source = tmp_path / "offset.mkv"
    run_process(["ffmpeg", "-v", "error", "-nostdin", "-f", "lavfi", "-i", "testsrc2=s=64x48:r=30:d=3",
                 "-vf", f"setpts=PTS+({offset})/TB", "-fps_mode", "passthrough", "-avoid_negative_ts", "disabled",
                 "-c:v", "ffv1", str(source)])
    raw = probe_media(source)
    video = next(v for v in raw["streams"] if v["codec_type"] == "video")
    origin = float(raw["format"]["start_time"])
    clip = {"source_start": start, "source_end": start + .8}
    with av.open(str(source)) as movie:
        actual = [float(frame.pts * frame.time_base) - origin for frame in movie.decode(video=0)]
    expected = [pts for pts in actual if start - 1e-8 <= pts < start + .8 - 1e-8]
    spatial, _ = jobs.spatial_color_filter(64, 48, {"fit": "fill"}, video)
    receipt = retiming.prepare_clip(source, tmp_path / "prepared.mp4", clip,
                                    options(mode="slow-hit", speed=.5, span=.5, interpolation=interpolation),
                                    outgoing=True, width=64, height=48, spatial=spatial, video=video, origin=origin)
    assert receipt["container_origin"] == origin
    assert receipt["native_pts"] == pytest.approx(expected, abs=1e-7)
    assert receipt["samples"][0]["source_target"] == pytest.approx(expected[0])
    assert receipt["samples"][-1]["source_target"] == pytest.approx(expected[-1])
    if interpolation == "flow":
        assert any(row["method"] == "ffmpeg-minterpolate-mci-nearest" for row in receipt["samples"])


@pytest.fixture
def native_source(tmp_path):
    path = tmp_path / "native.nut"
    run_process(["ffmpeg", "-v", "error", "-nostdin", "-f", "lavfi", "-i", "testsrc2=s=128x72:r=60",
                 "-t", "2", "-c:v", "ffv1", "-threads", "1", str(path)])
    video = next(v for v in probe_media(path)["streams"] if v["codec_type"] == "video")
    return path, video


def prepare(native_source, tmp_path, *, interpolation="nearest", cancelled=lambda:False):
    path, video = native_source
    spatial, _ = jobs.spatial_color_filter(128, 72, {"fit": "fill"}, video)
    clip = {"source_start": .1, "source_end": 1.7}
    request = options(mode="slow-hit", speed=.5, span=.6, interpolation=interpolation)
    output = tmp_path / f"{interpolation}.mp4"
    result = retiming.prepare_clip(path, output, clip, request, outgoing=True, width=128, height=72,
                                   spatial=spatial, video=video, cancelled=cancelled)
    return output, result


@pytest.mark.parametrize("interpolation", ["nearest", "blend", "flow"])
def test_native_cadence_precedes_sampling_endpoints_locked_and_scratch_removed(native_source, tmp_path, interpolation):
    output, receipt = prepare(native_source, tmp_path, interpolation=interpolation)
    assert len(receipt["native_pts"]) == 96, "60fps native frames must not be discarded by a preliminary30fps conversion"
    assert np.diff(receipt["native_pts"]) == pytest.approx(np.full(95, 1 / 60))
    samples = receipt["samples"]
    assert samples[0]["source_target"] == receipt["native_pts"][0]
    assert samples[-1]["source_target"] == receipt["native_pts"][-1]
    assert samples[0]["method"] == samples[-1]["method"] == "native-nearest"
    assert all(.1 <= row["source_target"] < 1.7 for row in samples)
    assert np.diff([row["source_target"] for row in samples]).min() >= 0
    with av.open(str(output)) as movie:
        assert len(list(movie.decode(video=0))) == receipt["frame_count"]
    assert receipt["output_duration"] > 1.6
    if interpolation == "blend":
        assert any(row["method"] == "linear-rec709-frame-blend" for row in samples)
    if interpolation == "flow":
        assert receipt["flow"]["dense_fps"] == 60
        assert any(row["method"] == "ffmpeg-minterpolate-mci-nearest" for row in samples)
    assert not list(tmp_path.glob("*.native.nut")) and not list(tmp_path.glob("*.flow.nut"))


def test_cancelled_native_preparation_removes_owned_scratch(native_source, tmp_path):
    with pytest.raises(JobCancelled):
        prepare(native_source, tmp_path, cancelled=lambda:True)
    assert not list(tmp_path.glob("*.native.nut"))
    assert not (tmp_path / "nearest.mp4").exists()


def test_locked_scratch_cleanup_warns_without_masking_a_valid_result(native_source, tmp_path, monkeypatch, caplog):
    from pathlib import Path
    unlink = Path.unlink
    def locked(path, *args, **kwargs):
        if path.name == "nearest.native.nut":
            raise PermissionError("locked")
        return unlink(path, *args, **kwargs)
    monkeypatch.setattr(Path, "unlink", locked)
    output, receipt = prepare(native_source, tmp_path)
    assert output.exists() and receipt["frame_count"] > 0
    assert "Could not remove transition retiming intermediate" in caplog.text


def test_native_frame_budget_rejects_instead_of_silently_truncating(native_source, tmp_path):
    path, _ = native_source
    with pytest.raises(ValueError, match="4096-native-frame"):
        retiming._timestamps(path, 0, lambda:None, limit=3)


def test_scratch_budget_stops_subprocess_and_cleans_partial_files(native_source, tmp_path, monkeypatch):
    monkeypatch.setattr(retiming, "MAX_SCRATCH_BYTES", 1)
    with pytest.raises(ValueError, match="scratch budget"):
        prepare(native_source, tmp_path)
    assert not list(tmp_path.glob("*.native.nut"))


def test_real_durable_ramp_changes_timing_without_changing_native_source_endpoints(config, sources):
    body, _films, store = sources
    body = deepcopy(body)
    body["recipe"].update(id="whip-pan", duration=.2)
    body["retime"] = options(mode="rush", speed=3, span=.6)
    queued = store.enqueue_transition_render(jobs.freeze(body, None))
    job = store.claim(role="editor")
    result = jobs.run(job, config, None, store, lambda _:None, lambda:False)
    store.finish(queued["id"], result=result)
    job = store.get_job(queued["id"], private=True)
    manifest = json.loads(jobs.artifact(config, job, "manifest").read_text())
    assert result["duration"] < 2.2
    assert 1.2 <= result["frame_a_time"] < 1.3
    assert .2 <= result["frame_b_time"] < .24
    assert manifest["retiming"]["source_windows_unchanged"]
    assert len(manifest["retiming"]["sources"]) == 2
    assert all(row["nominal_speed"] < 3 for row in result["retiming"]["visible_cut"]["at_picture_cut"])
    assert not list(jobs.output_root(config, queued["id"]).glob("*.nut"))
