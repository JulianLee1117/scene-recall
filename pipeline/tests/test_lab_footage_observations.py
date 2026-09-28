"""Short source observations retain authoritative bounds and sparse PTS evidence."""

from copy import deepcopy
import hashlib
import json
import math
import os

from PIL import Image
import pytest

from pipeline.index.writer import create_tables, open_db
from pipeline.ingest.probe import _content_hash
from pipeline.lab import footage_observations as observations, music, next_scene_media
from pipeline.lab.media import JobCancelled, run_process


@pytest.fixture
def source(config, tmp_path, monkeypatch):
    path = tmp_path / "film.mp4"
    path.write_bytes(b"unchanged source bytes")
    film_id = _content_hash(path)
    db = open_db(config)
    create_tables(db, vector_dim=4)
    db.open_table("films").add([{"film_id": film_id, "title": "Do not infer this film's plot",
        "path": str(path), "duration": 12., "fps": 24.}])
    db.open_table("units").add([{"unit_id": "unit-1", "film_id": film_id,
        "t_start": 1., "t_end": 10., "caption": "Do not inherit this imagined action"}])
    window = {"film_id": film_id, "unit_id": "unit-1", "source_start": 1., "source_end": 3.}
    requested = []

    def sample(path, requested_time, lower, upper, crop, cancelled):
        if cancelled():
            raise JobCancelled("Cancelled")
        requested.append(requested_time)
        # Match the real sampler's nearest wholly contained 24fps interval.
        start = min(max(math.ceil(lower * 24) / 24, round(requested_time * 24) / 24),
                    math.floor(upper * 24 - 1) / 24)
        return next_scene_media._DisplaySample(start, start + 1 / 24, Image.new("RGB", (160, 90), "gray"))

    monkeypatch.setattr(next_scene_media, "_sample_at", sample)
    calls = []

    def hosted(config, prompt, schema, **kwargs):
        calls.append({"prompt": prompt, "schema": schema, **kwargs})
        ids = schema["$defs"]["ObservedChange"]["properties"]["start_sample_id"]["enum"]
        return {"summary": "A person beside a tree, then only foliage.", "events": [{
            "start_sample_id": ids[0], "end_sample_id": ids[-1],
            "before": "A person is visible beside a tree.", "after": "Only foliage is visible.",
            "completion": "visible" if len(ids) > 1 else "uncertain", "evidence_ids": [ids[0], ids[-1]]
                if len(ids) > 1 else [ids[0]]}],
            "uncertainty": "Sparse samples cannot distinguish leaving the frame from an abrupt image change."}

    monkeypatch.setattr(music, "_hosted_json", hosted)
    return config, db, path, window, calls, requested


def inspect(source, **kwargs):
    config, db, _, window, _, _ = source
    return observations.inspect_window(window, config, db, "test-inspect", lambda _: None, **kwargs)


def cache_path(source, result):
    return source[0].paths.assets_dir / "lab" / "footage-observations" / f"{result['artifact_id']}.json"


def test_observations_are_neutral_bracketed_private_and_reusable(source):
    config, db, path, window, calls, requested = source
    original = deepcopy(window), path.read_bytes()
    result = inspect(source)
    frames = result["frames"]
    assert result["contract"] == observations.CONTRACT
    assert len(frames) == len(calls[0]["images"]) == 5
    assert requested == [1., 1.5, 2., 2.5, math.nextafter(3., 1.)]
    assert frames[-1]["timestamp"] == pytest.approx(71 / 24)
    assert frames[-1]["frame_end"] == pytest.approx(3.)
    assert all(set(frame) == {"id", "timestamp", "frame_end", "sha256"} for frame in frames)
    assert result["events"][0]["start"] == frames[0]["timestamp"]
    assert result["events"][0]["end"] == frames[-1]["frame_end"]
    assert calls[0]["operation"] == "inspect"
    assert "Do not infer this film's plot" not in calls[0]["prompt"]
    assert "Do not inherit this imagined action" not in calls[0]["prompt"]
    assert window["film_id"] not in calls[0]["prompt"]
    assert window["unit_id"] not in calls[0]["prompt"]
    repeated = inspect(source)
    assert repeated == {**result, "cache_reused": True}
    assert len(calls) == 1 and len(requested) == 5
    assert (window, path.read_bytes()) == original
    assert "path" not in json.dumps(result)


def test_eight_second_budget_and_single_frame_windows(source):
    source[3].update(source_end=9.)
    result = inspect(source)
    assert len(result["frames"]) == len(source[4][0]["images"]) == 17
    source[3].update(source_end=1. + 1 / 24)
    short = inspect(source)
    assert len(short["frames"]) == 1
    assert short["events"][0]["completion"] == "uncertain"


@pytest.mark.parametrize("change", [
    {"source_start": .99}, {"source_end": 10.01}, {"source_end": 9.01},
    {"source_end": 1.}, {"source_start": float("nan")}, {"source_end": float("inf")},
    {"source_start": True}, {"source_start": "1"}, {"unit_id": "missing"},
    {"film_id": "another-film"}, {"crop": {"x": .9, "width": .5}},
    {"prompt": "untrusted selection instruction"},
])
def test_invalid_authority_or_window_precedes_sampling_or_hosted(source, change):
    source[3].update(change)
    with pytest.raises(ValueError):
        inspect(source)
    assert not source[4] and not source[5]


def test_canonical_authority_is_revalidated_even_on_cache_hit(source):
    result = inspect(source)
    source[1].open_table("units").update(where="unit_id = 'unit-1'", values={"t_end": 2.})
    with pytest.raises(ValueError, match="authoritative"):
        inspect(source)
    assert len(source[4]) == 1
    assert cache_path(source, result).exists()


@pytest.mark.parametrize("changed", ["window", "crop", "model", "provider", "settings", "prompt", "sampling", "source", "unit"])
def test_cache_identity_changes_with_source_sampling_or_model_contract(source, monkeypatch, changed):
    first = inspect(source)
    config, db, path, window, calls, _ = source
    if changed == "window":
        window["source_end"] = 3.25
    elif changed == "crop":
        window["crop"] = {"x": .25, "y": 0, "width": .5, "height": 1}
    elif changed == "model":
        config.lab.planner_model = "different-vision-model"
    elif changed == "provider":
        config.lab.music_provider = "gemini"
        with pytest.raises(observations.InspectionUnavailable, match="OpenAI"):
            inspect(source)
        assert len(calls) == 1
        return
    elif changed == "settings":
        monkeypatch.setattr(music, "PLANNER_SETTINGS", {**music.PLANNER_SETTINGS, "reasoning_effort": "medium"})
    elif changed == "prompt":
        monkeypatch.setattr(observations, "PROMPT", observations.PROMPT + " Recheck visible states.")
    elif changed == "sampling":
        monkeypatch.setattr(observations, "SAMPLING_PROFILE", "changed-sampling-profile")
    elif changed == "source":
        before = path.stat()
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000_000))
    elif changed == "unit":
        db.open_table("units").update(where="unit_id = 'unit-1'", values={"t_start": .5})
    second = inspect(source)
    assert second["artifact_id"] != first["artifact_id"]
    assert not second["cache_reused"] and len(calls) == 2


@pytest.mark.parametrize("corruption", ["image", "manifest-list", "frame-list", "timestamp", "path", "output", "unknown-sample"])
def test_corrupt_cache_cannot_be_reused_or_trigger_a_hosted_retry(source, corruption):
    result = inspect(source)
    metadata = next((source[0].paths.assets_dir / "lab" / "footage-observations" / "frames").glob("*/samples.json"))
    if corruption in {"image", "manifest-list", "frame-list", "timestamp", "path"}:
        cached = json.loads(metadata.read_text())
        if corruption == "image":
            (metadata.parent / "sample-0.jpg").write_bytes(b"changed image")
        elif corruption == "manifest-list":
            metadata.write_text("[]")
        else:
            if corruption == "frame-list":
                cached["frames"][0] = []
            elif corruption == "timestamp":
                cached["frames"][0]["timestamp"] = True
            elif corruption == "path":
                cached["frames"][0]["file"] = "../sample-0.jpg"
            cached["frames_hash"] = music.digest(cached["frames"])
            music.write_json(metadata, cached)
    else:
        path = cache_path(source, result)
        cached = json.loads(path.read_text())
        if corruption == "output":
            cached["output"]["summary"] = "Modified description"
        else:
            cached["output"]["events"][0]["evidence_ids"].append("sample-999")
            cached["output_hash"] = music.digest(cached["output"])
        music.write_json(path, cached)
    with pytest.raises(observations.InspectionUnavailable, match="integrity|validation"):
        inspect(source)
    assert len(source[4]) == 1


@pytest.mark.parametrize("change", [
    {"completion": "complete"}, {"end_sample_id": "sample-999"},
    {"start_sample_id": "sample-4", "end_sample_id": "sample-0"},
    {"end_sample_id": "sample-0", "evidence_ids": ["sample-0"]},
    {"evidence_ids": ["sample-1", "sample-4"]},
    {"evidence_ids": ["sample-0", "sample-0", "sample-4"]},
    {"start_sample_id": "sample-1", "evidence_ids": ["sample-0", "sample-1", "sample-4"]},
])
def test_invalid_model_completion_or_evidence_is_optional_failure_not_repaired(source, monkeypatch, change):
    hosted = music._hosted_json
    def invalid(*args, **kwargs):
        output = hosted(*args, **kwargs)
        output["events"][0].update(change)
        return output
    monkeypatch.setattr(music, "_hosted_json", invalid)
    with pytest.raises(observations.InspectionUnavailable, match="validation"):
        inspect(source)
    assert len(source[4]) == 1
    assert not list((source[0].paths.assets_dir / "lab" / "footage-observations").glob("*.json"))


def test_provider_unavailable_is_optional_and_never_retried(source, monkeypatch):
    calls = []
    def unavailable(*args, **kwargs):
        calls.append(True)
        raise music.MusicUnavailable("Vision service unavailable")
    monkeypatch.setattr(music, "_hosted_json", unavailable)
    with pytest.raises(observations.InspectionUnavailable, match="Vision service unavailable"):
        inspect(source)
    assert len(calls) == 1


def test_decode_deadline_is_bounded_and_never_reaches_hosted(source, monkeypatch):
    monkeypatch.setattr(observations, "DECODE_SECONDS", -1)
    with pytest.raises(observations.InspectionUnavailable, match="samples are unavailable"):
        inspect(source)
    assert not source[4] and not source[5]


@pytest.mark.parametrize("when", ["before", "decode", "hosted", "cached"])
def test_cancellation_propagates_without_observation_commit(source, monkeypatch, when):
    state = {"cancelled": False}
    if when == "cached":
        inspect(source)
        state["cancelled"] = True
    elif when == "before":
        state["cancelled"] = True
    elif when == "decode":
        sampler = next_scene_media._sample_at
        def cancel_decode(*args):
            state["cancelled"] = True
            return sampler(*args)
        monkeypatch.setattr(next_scene_media, "_sample_at", cancel_decode)
    elif when == "hosted":
        hosted = music._hosted_json
        def cancel_hosted(*args, **kwargs):
            output = hosted(*args, **kwargs)
            state["cancelled"] = True
            return output
        monkeypatch.setattr(music, "_hosted_json", cancel_hosted)
    with pytest.raises(JobCancelled):
        inspect(source, cancelled=lambda: state["cancelled"])
    assert len(source[4]) == (1 if when in {"cached", "hosted"} else 0)
    if when != "cached":
        assert not list((source[0].paths.assets_dir / "lab" / "footage-observations").glob("*.json"))


def test_source_changed_during_hosted_request_is_authority_failure(source, monkeypatch):
    hosted = music._hosted_json
    def change_source(*args, **kwargs):
        result = hosted(*args, **kwargs)
        source[2].write_bytes(b"different source footage")
        return result
    monkeypatch.setattr(music, "_hosted_json", change_source)
    with pytest.raises(ValueError, match="changed during"):
        inspect(source)
    assert not list((source[0].paths.assets_dir / "lab" / "footage-observations").glob("*.json"))


@pytest.mark.parametrize("stage", ["sampling", "hosted", "invalid-output"])
@pytest.mark.parametrize("change", ["modified", "missing"])
def test_optional_failure_cannot_hide_changed_source_authority(source, monkeypatch, stage, change):
    def fail(*args, **kwargs):
        if change == "modified":
            source[2].write_bytes(b"changed source footage")
        else:
            source[2].rename(source[2].with_name("moved-film.mp4"))
        if stage == "invalid-output":
            return {"invalid": "model output"}
        if stage == "sampling":
            raise ValueError("Decoder could not read the source")
        raise music.MusicUnavailable("Provider failed")
    if stage == "sampling":
        monkeypatch.setattr(next_scene_media, "_sample_at", fail)
    else:
        monkeypatch.setattr(music, "_hosted_json", fail)
    with pytest.raises(ValueError, match="Source footage (changed|became unavailable)"):
        inspect(source)
    assert not list((source[0].paths.assets_dir / "lab" / "footage-observations").glob("*.json"))


def test_changed_source_after_successful_sampling_never_reaches_hosted(source, monkeypatch):
    sample = observations._sample
    def changed(*args, **kwargs):
        result = sample(*args, **kwargs)
        source[2].write_bytes(b"changed source footage")
        return result
    monkeypatch.setattr(observations, "_sample", changed)
    with pytest.raises(ValueError, match="Source footage changed"):
        inspect(source)
    assert not source[4]


def test_real_decode_retains_actual_pts_endpoint_and_display_crop(config, tmp_path, monkeypatch):
    path = tmp_path / "native.mp4"
    run_process(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
        "testsrc2=s=800x450:r=30:d=9", "-an", "-c:v", "libx264", "-threads", "1", "-pix_fmt", "yuv420p", str(path)])
    fingerprint = hashlib.sha256(path.read_bytes()).hexdigest()
    film_id = _content_hash(path)
    db = open_db(config)
    create_tables(db, vector_dim=4)
    db.open_table("films").add([{"film_id": film_id, "title": "Native", "path": str(path), "duration": 9., "fps": 30.}])
    db.open_table("units").add([{"unit_id": "native", "film_id": film_id, "t_start": 0., "t_end": 9.}])
    calls = []
    def hosted(config, prompt, schema, **kwargs):
        calls.append(kwargs)
        for frame in kwargs["images"]:
            with Image.open(frame["path"]) as picture:
                assert picture.format == "JPEG"
                assert picture.size == ((640, 360) if len(calls) == 1 else (400, 450))
        return {"summary": "Colored test pattern.", "events": [], "uncertainty": "Sparse samples cannot establish continuous movement."}
    monkeypatch.setattr(music, "_hosted_json", hosted)
    window = {"film_id": film_id, "unit_id": "native", "source_start": .01, "source_end": 8.01}
    result = observations.inspect_window(window, config, db, "real-pts", lambda _: None)
    assert len(result["frames"]) == 17
    assert result["frames"][0]["timestamp"] == pytest.approx(1 / 30)
    assert result["frames"][-1]["timestamp"] == pytest.approx(239 / 30)
    assert result["frames"][-1]["frame_end"] == pytest.approx(8.)
    assert all(abs(frame["timestamp"] * 30 - round(frame["timestamp"] * 30)) < 1e-7 for frame in result["frames"])
    window.update(source_end=1.01, crop={"x": .25, "y": 0, "width": .5, "height": 1})
    cropped = observations.inspect_window(window, config, db, "real-crop", lambda _: None)
    assert len(cropped["frames"]) == 3
    assert cropped["window"]["crop"] == window["crop"]
    assert cropped["artifact_id"] != result["artifact_id"]
    assert hashlib.sha256(path.read_bytes()).hexdigest() == fingerprint
    assert len(calls) == 2
