"""A played and kept Match Cut must retain its selected native boundary frames."""

from copy import deepcopy
import hashlib

import av
import numpy as np
import pytest

from pipeline.lab import media as renderer
from pipeline.lab.matching import verify_boundaries
from pipeline.lab.models import ProjectDocument
from pipeline.lab.store import LabStore
from pipeline.matching import media


def make_source(directory, rate):
    path = directory / "source.mp4"
    fps = "50" if rate == "vfr" else "30" if rate == "shifted" else rate
    # Each frame has a distinct full-picture color, so a one-frame error cannot
    # pass through a tolerance intended only for encoding/resize differences.
    picture = f"nullsrc=s=320x180:r={fps}:d=4,geq=r='mod(N*41,256)':g='mod(N*73,256)':b='mod(N*97,256)'"
    options = []
    if rate == "vfr":
        picture += ",settb=1/1000,setpts=(N*0.02+floor(N/7)*0.11)/TB"
        options = ["-fps_mode", "vfr", "-enc_time_base", "1/1000", "-video_track_timescale", "1000"]
    if rate == "shifted":
        options = ["-output_ts_offset", "0.5"]
    renderer.run_process([
        "ffmpeg", "-v", "error", "-f", "lavfi", "-i", picture,
        *options, "-c:v", "libx264", "-g", "120", "-sc_threshold", "0",
        "-crf", "15", "-pix_fmt", "yuv420p", str(path),
    ])
    return path


def boundary_images(path, cut):
    result = []
    with av.open(str(path)) as container:
        for index, frame in enumerate(container.decode(video=0)):
            if index in (cut - 1, cut):
                result.append(np.asarray(frame.to_image().resize((320, 180)), dtype=float))
            if index >= cut:
                break
    return result


@pytest.mark.parametrize("rate", ["24000/1001", "25", "30", "50", "60", "vfr", "shifted"])
def test_native_boundaries_survive_preview_keep_and_export(config, tmp_path, monkeypatch, rate):
    source = make_source(tmp_path, rate)
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    monkeypatch.setattr(renderer, "resolve_film", lambda *_: {"path": str(source), "duration": 8})
    reference = media.at(source, 2.02, 0, 4)
    incoming = media.at(source, 2.34, 0, 4)
    if rate == "vfr":
        decoded = media.samples(source, 1, 2, native=True)
        assert max(sample.end - sample.time for sample in decoded) > 0.1
    if rate == "shifted":
        with av.open(str(source)) as container:
            stream = container.streams.video[0]
            assert float(stream.start_time * stream.time_base) == pytest.approx(0.5)
    document = ProjectDocument(clips=[
        {"id": "a", "film_id": "film", "source_start": media.outgoing_start(source, reference, 0, 4),
         "source_end": reference.end, "reference_time": reference.time},
        {"id": "b", "film_id": "film", "source_start": incoming.time,
         "source_end": incoming.time + 1, "reference_time": incoming.time},
    ]).model_dump(mode="json")
    original = deepcopy(document)
    store = LabStore(config.paths.state_dir)
    store.initialize()
    project = store.create_project("Timing regression", "visual-rhymes")
    kept = store.update_project(project["id"], project["revision"], document)
    restored = LabStore(config.paths.state_dir).get_project(project["id"])
    assert restored["document"] == kept["document"] == document
    rendered = []
    for mode in ("preview", "export"):
        result = renderer.render_reel({"id": mode, "snapshot": {
            "mode": mode, "experiment_id": "visual-rhymes", "document": restored["document"],
        }}, config, None, store, lambda _: None)
        manifest = result["manifest"]
        assert manifest["profile"] == renderer.MATCH_BOUNDARY_PROFILE
        assert manifest["music"] is None
        assert (manifest["width"], manifest["height"]) == ((1280, 720) if mode == "preview" else (1920, 1080))
        output = config.paths.assets_dir / "lab" / "renders" / mode / "output.mp4"
        evidence = verify_boundaries(output, manifest, None, [reference.time, incoming.time])
        assert evidence["passed"], evidence
        assert all(check["mean_pixel_error"] < 4 for check in evidence["checks"])
        rendered.append(boundary_images(output, manifest["clips"][0]["frame_count"]))
    assert all(np.abs(preview - export).mean() < 2 for preview, export in zip(*rendered))
    assert original == document == restored["document"]
    assert hashlib.sha256(source.read_bytes()).hexdigest() == source_hash


def test_music_manifest_retains_existing_sampling_and_seek_contract(config, tmp_path, monkeypatch):
    source = make_source(tmp_path, "50")
    monkeypatch.setattr(renderer, "resolve_film", lambda *_: {"path": str(source), "duration": 8})
    clip = {"id": "a", "film_id": "film", "source_start": 1.04, "source_end": 2.04, "crop": None, "frame_count": 24}
    manifest = {
        "profile": "decoded-reel-audio-fade-v3", "fps": 24, "width": 320, "height": 180,
        "duration": 1, "clips": [clip], "music": None,
    }
    commands = []
    run_process = renderer.run_process

    def capture(arguments, **kwargs):
        commands.append(arguments)
        return run_process(arguments, **kwargs)

    monkeypatch.setattr(renderer, "run_process", capture)
    renderer.render_from_manifest("legacy", manifest, config, None, None, lambda _: None)
    command = next(command for command in commands if "-vf" in command)
    assert "-seek_timestamp" not in command
    assert "fps=24," in command[command.index("-vf") + 1]
    assert ":round=" not in command[command.index("-vf") + 1]
