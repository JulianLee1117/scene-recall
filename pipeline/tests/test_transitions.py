"""Real media and durable-session checks for the standalone transition renderer."""
from copy import deepcopy
import json
from pathlib import Path
import shutil

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from pydantic import ValidationError

from pipeline.ingest.probe import _content_hash
from pipeline.lab.job_roles import role_for_kind
from pipeline.lab.media import JobCancelled, probe_media, run_process
from pipeline.lab.store import LabStore
from pipeline.transitions import jobs
from pipeline.transitions.api import router
from pipeline.transitions.contracts import RENDERER_VERSION, RenderRequest, catalog
from pipeline.transitions.media import endpoint

_OUTPUT_DIMENSIONS = jobs.dimensions


def test_prism_push_default_is_on_its_control_step():
    recipe = next(row for row in catalog()["recipes"] if row["id"] == "prism-push")
    control = next(row for row in recipe["control_specs"] if row["key"] == "zoom_amount")
    steps = (recipe["defaults"]["zoom_amount"] - control["min"]) / control["step"]
    assert steps == pytest.approx(round(steps))


@pytest.fixture
def sources(config, monkeypatch):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("FFmpeg is required for transition rendering")
    root = config.paths.films_dir
    root.mkdir(parents=True)
    films = {}
    for name, picture in [("a", "color=black:s=192x108:r=30,drawbox=x=0:y=0:w=96:h=108:color=white:t=fill"),
                          ("b", "color=red:s=192x108:r=30")]:
        path = root / f"{name}.mkv"
        run_process(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-f", "lavfi",
                     "-i", picture, "-t", "2", "-c:v", "libx264", "-threads", "1", str(path)])
        identity = _content_hash(path)
        films[identity] = {"film_id": identity, "path": str(path), "duration": 2., "title": name}
    monkeypatch.setattr(jobs, "resolve_film", lambda _db, identity: films[identity])
    # Exercise real encoding/filters at small dimensions to keep the test bounded.
    monkeypatch.setattr(jobs, "dimensions", lambda _output: (192, 108))
    ids = list(films)
    request = RenderRequest(outgoing={"film_id": ids[0], "source_start": .1, "source_end": 1.3},
                            incoming={"film_id": ids[1], "source_start": .2, "source_end": 1.4},
                            recipe={"id": "luma-reveal", "duration": .6, "easing": "linear"}).model_dump(mode="json")
    store = LabStore(config.paths.state_dir, config.paths.assets_dir)
    store.initialize()
    return request, films, store


def _render(config, sources, identity):
    request, _films, store = sources
    request = deepcopy(request)
    request["recipe"]["id"] = identity
    job = store.enqueue_transition_render(jobs.freeze(request, None))
    claimed = store.claim(role="editor")
    assert claimed["id"] == job["id"]
    result = jobs.run(claimed, config, None, store, lambda _: None, lambda: False)
    store.finish(job["id"], result=result)
    return store.get_job(job["id"], private=True)


@pytest.mark.parametrize("identity", [row["id"] for row in catalog()["recipes"]])
def test_every_recipe_produces_verified_real_media_and_cleans_intermediates(config, sources, identity):
    job = _render(config, sources, identity)
    result = job["result"]
    assert result["duration"] == pytest.approx(2.4 if identity == "hard-cut" else 1.8)
    assert result["transition_start"] == pytest.approx(1.2 if identity == "hard-cut" else .6)
    root = jobs.output_root(config, job["id"])
    assert {path.name for path in root.iterdir()} == {"output.mp4", "manifest.json", "frame-a.jpg", "frame-b.jpg"}
    for kind in ("video", "manifest", "a", "b"):
        assert jobs.artifact(config, job, kind).is_file()
    manifest = json.loads((root / "manifest.json").read_text())
    assert manifest["audio"] == "muted"
    assert manifest["request"] == job["snapshot"]["transition_render"]["request"]
    assert "path" not in manifest["sources"][0]
    assert 1.2 <= manifest["source_frame_times"][0] < 1.3
    assert .2 <= manifest["source_frame_times"][1] < .24
    assert job["project_id"] is None and job["base_revision"] is None
    assert not sources[2].list_projects()


def test_luma_reveal_uses_source_brightness_and_stale_bytes_cannot_be_served(config, sources):
    job = _render(config, sources, "luma-reveal")
    path = jobs.artifact(config, job, "video")
    pixels = run_process(["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", "0.9", "-i", str(path),
                          "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"])
    def pixel(x, y):
        index = (y * 192 + x) * 3
        return tuple(pixels[index:index + 3])
    left, right = pixel(40, 50), pixel(150, 50)
    assert left[0] > 200 and left[1] < 30 and left[2] < 30, left
    assert max(right) < 30, right
    path.write_bytes(path.read_bytes() + b"changed")
    with pytest.raises(ValueError, match="artifact changed"):
        jobs.artifact(config, job, "video")


def test_changed_source_fails_before_render_and_active_dedup_does_not_create_projects(config, sources):
    request, films, store = sources
    frozen = jobs.freeze(request, None)
    first = store.enqueue_transition_render(frozen)
    assert store.enqueue_transition_render(frozen)["id"] == first["id"]
    assert role_for_kind(first["kind"]) == "editor"
    job = store.claim(role="editor")
    original = Path(next(iter(films.values()))["path"])
    original.write_bytes(original.read_bytes() + b"changed")
    with pytest.raises(ValueError, match="source film.*changed"):
        jobs.run(job, config, None, store, lambda _: None, lambda: False)
    assert not jobs.output_root(config, job["id"]).exists()
    assert not store.list_projects()


def test_cancellation_removes_partial_encodes(config, sources):
    request, _films, store = sources
    job = store.enqueue_transition_render(jobs.freeze(request, None))
    job = store.claim(role="editor")
    with pytest.raises(JobCancelled):
        jobs.run(job, config, None, store, lambda _: None, lambda: True)
    root = jobs.output_root(config, job["id"])
    assert not list(root.glob("*.mp4"))


@pytest.mark.parametrize("change", [
    {"outgoing": {"film_id": "f", "source_start": -1, "source_end": 1}},
    {"incoming": {"film_id": "f", "source_start": 0, "source_end": 13}},
    {"recipe": {"id": "whip-pan", "duration": 1.2}},
    {"recipe": {"id": "whip-pan", "intensity": float("nan")}},
    {"recipe": {"id": "unsupported-provider"}},
    {"outgoing": {"film_id": "f", "source_start": 0, "source_end": 1, "path": "C:/secret"}},
])
def test_rejects_unbounded_or_nonfinite_or_client_path_requests(change):
    body = {"outgoing": {"film_id": "f", "source_start": 0, "source_end": 1},
            "incoming": {"film_id": "g", "source_start": 0, "source_end": 1}, **change}
    with pytest.raises(ValidationError):
        RenderRequest.model_validate(body)


def test_api_lists_restorable_requests_and_cancels_without_mutating_projects(config, sources):
    request, _films, store = sources
    app = FastAPI()
    app.include_router(router)
    app.state.config, app.state.lab, app.state.db = config, store, None
    client = TestClient(app)
    assert client.get("/lab/transitions/recipes").status_code == 200
    assert client.get("/lab/transitions/renders").json() == {"renders": []}
    assert not store.list_projects()
    response = client.post("/lab/transitions/renders", json=request)
    assert response.status_code == 200, response.text
    job = response.json()
    assert job["request"] == request and "snapshot" not in job
    assert job["source_titles"] == {"outgoing": "a", "incoming": "b"}
    assert "path" not in json.dumps(job)
    recent = client.get("/lab/transitions/renders?limit=1").json()["renders"]
    assert [row["id"] for row in recent] == [job["id"]]
    assert recent[0]["source_titles"] == job["source_titles"]
    assert client.get(f"/lab/transitions/renders/{job['id']}/video").status_code == 409
    cancelled = client.post(f"/lab/transitions/renders/{job['id']}/cancel").json()
    assert cancelled["status"] == "cancelled" and cancelled["result"] is None
    assert not store.list_projects()


def test_fractional_timing_uses_shared_half_up_rounding_and_tolerates_point_two_seconds():
    from pipeline.transitions.contracts import SourceClip, frame_count
    SourceClip(film_id="source", source_start=10., source_end=10.2)
    assert frame_count(.35) == 11
    assert frame_count(.25) == 8
    with pytest.raises(ValidationError, match="overlap must be shorter"):
        RenderRequest(outgoing={"film_id": "source", "source_start": 0, "source_end": .36},
                      incoming={"film_id": "source", "source_start": 0, "source_end": .36},
                      recipe={"duration": .35})


def test_endpoint_frames_remain_inside_fractional_24fps_source_windows(tmp_path):
    path = tmp_path / "24fps.mkv"
    run_process(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-f", "lavfi",
                 "-i", "testsrc2=s=192x108:r=24", "-t", "2", "-c:v", "libx264", "-threads", "1", str(path)])
    # end-1/30=0.9667 would seek to frame1.0, which lies outside this clip.
    clip = {"source_start": .021, "source_end": 1.}
    a_time = endpoint(path, clip, outgoing=True, destination=tmp_path / "a.jpg")
    b_time = endpoint(path, clip, outgoing=False, destination=tmp_path / "b.jpg")
    assert .95 < a_time < 1.
    assert .021 <= b_time < .06


def test_worker_dispatch_and_explicit_video_download(config, sources):
    from pipeline.lab.worker import execute_job
    request, _films, store = sources
    store.enqueue_transition_render(jobs.freeze(request, None))
    finished = execute_job(store.claim(role="editor"), config, None, store)
    assert finished["status"] == "completed", finished.get("error")
    app = FastAPI()
    app.include_router(router)
    app.state.config, app.state.lab = config, store
    client = TestClient(app)
    url = f"/lab/transitions/renders/{finished['id']}/video"
    inline = client.get(url)
    assert inline.status_code == 200 and "content-disposition" not in inline.headers
    download = client.get(url + "?download=1")
    assert download.status_code == 200 and download.headers["content-disposition"].startswith("attachment;")
    assert download.content == inline.content


def test_source_trim_can_extend_shot_hints_but_not_cross_film_ids(config, sources, monkeypatch):
    from pipeline.lab import media as lab_media
    request, _films, _store = sources
    request["outgoing"]["unit_id"] = "hint"
    shot = {"film_id": request["outgoing"]["film_id"], "t_start": .5, "t_end": .7}
    monkeypatch.setattr(lab_media, "resolve_unit", lambda _db, _id: shot)
    assert jobs.freeze(request, None)["request"]["outgoing"]["source_start"] == .1
    shot["film_id"] = "wrong-source"
    with pytest.raises(ValueError, match="different source film"):
        jobs.freeze(request, None)


def test_missing_shot_hint_does_not_invalidate_unchanged_film_and_time_anchors(config, sources, monkeypatch):
    from pipeline.lab import media as lab_media
    request, _films, store = sources
    request["outgoing"]["unit_id"] = "replaceable-shot-hint"
    monkeypatch.setattr(lab_media, "resolve_unit", lambda _db, _id: {"film_id": request["outgoing"]["film_id"]})
    frozen = jobs.freeze(request, None)
    queued = store.enqueue_transition_render(frozen)

    def missing(_db, _identity):
        raise ValueError("The reference shot is no longer in the published index")

    monkeypatch.setattr(lab_media, "resolve_unit", missing)
    assert jobs.freeze(request, None) == frozen
    assert store.enqueue_transition_render(jobs.freeze(request, None))["id"] == queued["id"]
    jobs._verify(frozen, None)
    claimed = store.claim(role="editor")
    result = jobs.run(claimed, config, None, store, lambda _: None, lambda: False)
    assert result["duration"] == pytest.approx(1.8)


@pytest.mark.parametrize("rate,start,end", [("24", .021, .381), ("24000/1001", .513, 1.843)])
def test_fractional_source_rates_and_short_windows_render_at_real_draft_resolution(config, sources, monkeypatch, rate, start, end):
    from pipeline.transitions.contracts import frame_count
    request, films, store = sources
    path = config.paths.films_dir / "fractional.mkv"
    run_process(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-f", "lavfi",
                 "-i", f"testsrc2=s=192x108:r={rate}", "-t", "2", "-c:v", "libx264", "-threads", "1", str(path)])
    identity = _content_hash(path)
    films[identity] = {"film_id": identity, "path": str(path), "duration": 2., "title": "Fractional source"}
    request["outgoing"].update(film_id=identity, source_start=start, source_end=end)
    request["recipe"].update(id="whip-pan", duration=.15, easing="snappy", direction="up")
    request["output"]["aspect"] = "portrait"
    monkeypatch.setattr(jobs, "dimensions", _OUTPUT_DIMENSIONS)
    store.enqueue_transition_render(jobs.freeze(request, None))
    result = jobs.run(store.claim(role="editor"), config, None, store, lambda _: None, lambda: False)
    assert (result["width"], result["height"]) == (480, 854)
    assert result["duration"] == pytest.approx((frame_count(end - start) + 36 - frame_count(.15)) / 30)


@pytest.mark.parametrize("recipe_id", ["hard-cut", "cross-dissolve"])
def test_different_source_aspects_and_anamorphic_pixels_share_square_pixel_output(config, sources, monkeypatch, recipe_id):
    request, films, store = sources
    for side, size, sar in [("outgoing", "384x164", "1"), ("incoming", "320x240", "8/9")]:
        path = config.paths.films_dir / f"different-aspect-{side}.mkv"
        run_process(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-f", "lavfi",
                     "-i", f"testsrc2=s={size}:r=30,setsar={sar}", "-t", "2", "-c:v", "libx264",
                     "-threads", "1", str(path)])
        identity = _content_hash(path)
        films[identity] = {"film_id": identity, "path": str(path), "duration": 2., "title": side}
        request[side]["film_id"] = identity
    request["recipe"].update(id=recipe_id, duration=.3)
    # 854x480 fit rounds each different display ratio differently. A setsar
    # before scale alone leaves incompatible SARs and concat fails on this pair.
    monkeypatch.setattr(jobs, "dimensions", _OUTPUT_DIMENSIONS)
    queued = store.enqueue_transition_render(jobs.freeze(request, None))
    result = jobs.run(store.claim(role="editor"), config, None, store, lambda _: None, lambda: False)
    store.finish(queued["id"], result=result)
    job = store.get_job(queued["id"], private=True)
    probe = probe_media(jobs.artifact(config, job, "video"))
    video = next(row for row in probe["streams"] if row["codec_type"] == "video")
    assert (video["width"], video["height"], video["sample_aspect_ratio"]) == (854, 480, "1:1")
    manifest = json.loads(jobs.artifact(config, job, "manifest").read_text())
    assert manifest["renderer_version"] == RENDERER_VERSION
    assert manifest["sample_aspect_ratio"] == "1:1"


def test_full_and_limited_sources_normalize_to_matching_rec709_pixels(tmp_path):
    import numpy as np
    from pipeline.transitions.jobs import spatial_color_filter
    values = []
    for source_range in ("tv", "pc"):
        path = tmp_path / f"{source_range}.mp4"
        run_process(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=s=192x108:r=30",
                     "-vf", f"scale=in_range=tv:out_range={source_range},setparams=colorspace=bt709:color_trc=bt709:color_primaries=bt709:range={'full' if source_range == 'pc' else 'limited'}", "-frames:v", "1", "-c:v", "libx264", "-crf", "0", "-pix_fmt", "yuv444p",
                     "-color_range", source_range, "-colorspace", "bt709", "-color_trc", "bt709", "-color_primaries", "bt709", str(path)])
        video = probe_media(path)["streams"][0]
        vf, receipt = spatial_color_filter(192, 108, video=video)
        values.append(np.frombuffer(run_process(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(path), "-vf", vf,
                                               "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]), dtype=np.uint8).astype(float))
        assert receipt["source_range"] == ("full" if source_range == "pc" else "limited")
        assert receipt["missing_tag_assumptions"] == []
    assert np.abs(values[0] - values[1]).mean() < 1.2


def test_hdr_tagged_source_is_explicitly_tonemapped_and_fit_fill_crop_work(tmp_path):
    import numpy as np
    from pipeline.transitions.jobs import spatial_color_filter
    path = tmp_path / "pq.mp4"
    run_process(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=s=192x108:r=30",
                 "-vf", "format=yuv444p10le,setparams=colorspace=bt2020nc:color_trc=smpte2084:color_primaries=bt2020:range=limited",
                 "-frames:v", "1", "-c:v", "libx264", "-crf", "0", "-pix_fmt", "yuv444p10le", "-colorspace", "bt2020nc",
                 "-color_trc", "smpte2084", "-color_primaries", "bt2020", "-color_range", "tv", str(path)])
    video = probe_media(path)["streams"][0]
    vf, receipt = spatial_color_filter(192, 108, video=video)
    pixels = np.frombuffer(run_process(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(path), "-vf", vf,
                                       "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]), dtype=np.uint8)
    assert receipt["hdr_tonemap"] == "mobius-0.3-npl100-desat2"
    assert 10 < pixels.mean() < 245
    pattern = "color=red:s=300x180:r=30,drawbox=x=100:y=0:w=100:h=180:color=green:t=fill,drawbox=x=200:y=0:w=100:h=180:color=blue:t=fill"
    outputs = {}
    for fit, anchor in (("fit", .5), ("fill", 0), ("fill", 1)):
        vf, _ = spatial_color_filter(108, 192, {"fit": fit, "anchor_x": anchor})
        values = np.frombuffer(run_process(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", pattern,
                                           "-vf", vf, "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]), dtype=np.uint8).reshape(192, 108, 3)
        outputs[fit, anchor] = values
    assert outputs["fit", .5][5, 54].max() < 3
    assert outputs["fill", 0][96, 54, 0] > 200
    assert outputs["fill", 1][96, 54, 2] > 200


def test_cancellation_during_compositing_closes_output_and_removes_partial_media(config, sources):
    request, _films, store = sources
    request["recipe"]["id"] = "crash-zoom"
    store.enqueue_transition_render(jobs.freeze(request, None))
    job = store.claim(role="editor")
    stopped = False
    def progress(message):
        nonlocal stopped
        if message.startswith("Compositing"):
            stopped = True
    with pytest.raises(JobCancelled):
        jobs.run(job, config, None, store, progress, lambda: stopped)
    assert stopped
    assert not list(jobs.output_root(config, job["id"]).glob("*.mp4"))


def test_old_renderer_queue_requires_new_request_before_writing_any_files(config, sources):
    request, _films, store = sources
    frozen = jobs.freeze(request, None)
    store.enqueue_transition_render(frozen)
    job = store.claim(role="editor")
    job["snapshot"]["transition_render"]["renderer_version"] = "transitions-ffmpeg-v2"
    with pytest.raises(ValueError, match="renderer or source changed"):
        jobs.run(job, config, None, store, lambda _: None, lambda: False)
    assert not jobs.output_root(config, job["id"]).exists()
