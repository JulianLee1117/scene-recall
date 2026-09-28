"""Next-scene previews preserve the full edit's decoded picture and music clock."""

from copy import deepcopy
import hashlib
from pathlib import Path

import av
import numpy as np
import pytest

from pipeline.index.writer import create_tables, open_db
from pipeline.ingest.probe import _content_hash
from pipeline.lab.media import DISPLAY_PROFILE, JobCancelled, import_track, render_manifest, render_reel, run_process
from pipeline.lab.models import ProjectDocument
from pipeline.lab.next_scene_media import preview_manifest, render_preview, sample_frames
from pipeline.lab.store import LabStore


@pytest.fixture
def media(config, tmp_path, request):
    video = tmp_path / "film.mp4"
    audio = tmp_path / "song.wav"
    sar = "3/2" if getattr(request, "param", "square") == "anamorphic" else "1"
    run_process(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=160x90:r=30:d=5",
                 "-vf", f"setsar={sar}", "-an", "-c:v", "libx264", "-g", "90", "-pix_fmt", "yuv420p", str(video)])
    run_process(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                 "aevalsrc=0.25*sin(2*PI*(220*t+70*t*t)):s=48000:d=5", str(audio)])
    source_hash = _content_hash(video)
    db = open_db(config)
    create_tables(db, vector_dim=4)
    db.open_table("films").add([{"film_id": source_hash, "title": "Source", "path": str(video), "duration": 5., "fps": 30.}])
    store = LabStore(config.paths.state_dir)
    store.initialize()
    track = import_track(store, audio, "song.wav")
    durations = [.53, 1.07, 1.31, .60]
    starts = [.17, 1.234, 2.617, .41]
    clips, slots, cursor = [], [], .137
    for index, (duration, start) in enumerate(zip(durations, starts)):
        clips.append({"id": f"clip-{index}", "film_id": source_hash, "source_start": start,
                      "source_end": start + duration, "crop": {"x": .25, "y": 0, "width": .5, "height": 1} if index == 1 else None})
        slots.append({"id": f"slot-{index}", "start": cursor, "end": cursor + duration,
                      "clip_id": f"clip-{index}", "section_index": 0})
        cursor += duration
    passage = {"start": .137, "end": cursor}
    document = ProjectDocument(track={key: track[key] for key in ("id", "name", "duration")}, passage=passage,
                               audio_fade_in_seconds=2.5, clips=clips,
                               music_timeline={"track_id": track["id"], "passage": passage, "slots": slots}).model_dump()
    return config, db, store, document, video, Path(track["path"])


def decoded_frames(path):
    with av.open(str(path)) as container:
        return [np.asarray(frame.to_image().resize((320, 180)), dtype=np.float32) for frame in container.decode(video=0)]


def decoded_audio(path):
    return np.frombuffer(run_process(["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:a:0", "-ac", "1",
                                      "-ar", "48000", "-f", "f32le", "pipe:1"]), dtype=np.float32)


@pytest.mark.parametrize("media", ["square", "anamorphic"], indirect=True)
def test_excerpt_matches_full_reel_frames_music_offset_and_original_fade(media):
    config, db, store, document, video, audio = media
    original = deepcopy(document)
    evidence = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in (video, audio)}
    full = render_reel({"id": "full", "snapshot": {"document": document, "mode": "preview", "experiment_id": "music-sketch"}},
                       config, db, store, lambda _: None)
    preview = render_preview("pair", document, "slot-1", config, db, store, lambda _: None)
    manifest = preview["manifest"]
    assert manifest["display_profile"] == full["manifest"]["display_profile"] == DISPLAY_PROFILE
    assert manifest != {key: value for key, value in manifest.items() if key != "display_profile"}
    assert preview["preview_ready"] is True
    assert [clip["frame_count"] for clip in manifest["clips"]] == [25, 32]
    assert manifest["frame_start"] == 13
    assert manifest["frame_end"] == 70
    assert manifest["music"]["start"] == document["passage"]["start"] + 13 / 24
    assert manifest["music"]["envelope_start"] == document["passage"]["start"]
    assert manifest["music"]["fade_in_seconds"] == 2.5
    assert manifest["clips"] == [
        {**clip} for clip in full["manifest"]["clips"][1:3]
    ]
    directory = config.paths.assets_dir / "lab" / "renders"
    full_picture = decoded_frames(directory / "full" / "output.mp4")
    pair_picture = decoded_frames(directory / "pair" / "output.mp4")
    assert len(full_picture) == 84
    assert len(pair_picture) == 57
    # Both boundary images and the complete excerpt match; source is 30fps,
    # placement starts off the 24fps grid, and the outgoing shot is cropped.
    assert max(float(np.abs(a - b).mean()) for a, b in zip(pair_picture, full_picture[13:70])) < .1
    with av.open(str(video)) as source:
        sar = source.streams.video[0].sample_aspect_ratio
    if sar != 1:
        # A 160x90 source with SAR 3:2 displays at 8:3, not 16:9. Its
        # half-width crop is 4:3. Check real picture bounds, not just matching
        # two equally distorted renders. Frames here are reduced to 320x180.
        visible = np.any(pair_picture[0] > 20, axis=2)
        rows, columns = np.where(visible)
        assert 38 <= columns.min() <= 42 and 277 <= columns.max() <= 282
        assert rows.min() == 0 and rows.max() == 179
        visible = np.any(pair_picture[25] > 20, axis=2)
        rows, columns = np.where(visible)
        assert columns.min() == 0 and columns.max() == 319
        assert 28 <= rows.min() <= 32 and 147 <= rows.max() <= 152
    full_audio = decoded_audio(directory / "full" / "output.mp4")
    pair_audio = decoded_audio(directory / "pair" / "output.mp4")
    expected = full_audio[13 * 2000:70 * 2000]
    count = min(expected.size, pair_audio.size)
    # AAC is independently encoded at excerpt edges. Ignore edge priming and
    # use a compression tolerance; an offset shift or restarted fade fails.
    assert np.abs(pair_audio[2400:count - 2400] - expected[2400:count - 2400]).mean() < .004
    assert np.corrcoef(pair_audio[2400:count - 2400], expected[2400:count - 2400])[0, 1] > .995
    assert document == original
    assert all(hashlib.sha256(path.read_bytes()).hexdigest() == digest for path, digest in evidence.items())


@pytest.mark.parametrize("media", ["anamorphic"], indirect=True)
def test_manual_incoming_moment_and_crop_match_full_edit_picture_and_music(media):
    from PIL import Image

    config, db, store, document, video, audio = media
    original = deepcopy(document)
    evidence = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in (video, audio)}
    incoming = document["clips"][2]
    duration = incoming["source_end"] - incoming["source_start"]
    incoming.update(source_start=2.8, source_end=2.8 + duration,
                    crop={"x": .375, "y": .2, "width": .5, "height": .6})
    full = render_reel({"id": "manual-full", "snapshot": {"document": document, "mode": "preview", "experiment_id": "music-sketch"}},
                       config, db, store, lambda _: None)
    preview = render_preview("manual-pair", document, "slot-1", config, db, store, lambda _: None)
    manifest = preview["manifest"]
    assert manifest["clips"] == full["manifest"]["clips"][1:3]
    assert manifest["clips"][1]["crop"] == incoming["crop"]
    assert (manifest["frame_start"], manifest["frame_end"]) == (13, 70)
    assert manifest["music"]["start"] == document["passage"]["start"] + 13 / 24
    assert document["music_timeline"] == original["music_timeline"]
    assert document["clips"][:2] == original["clips"][:2]
    assert document["clips"][3:] == original["clips"][3:]

    directory = config.paths.assets_dir / "lab" / "renders"
    full_picture = decoded_frames(directory / "manual-full" / "output.mp4")
    pair_picture = decoded_frames(directory / "manual-pair" / "output.mp4")
    assert len(full_picture) == 84 and len(pair_picture) == 57
    assert max(float(np.abs(a - b).mean()) for a, b in zip(pair_picture, full_picture[13:70])) < .1

    # Independently decode the chosen source moment and crop in Pillow. This
    # catches a shared renderer ignoring the source-in, crop position or SAR,
    # which comparing two renders using that same engine would not detect.
    with av.open(str(video)) as source:
        frame = next(frame for frame in source.decode(video=0) if float(frame.time) >= 2.8 - 1e-7)
        assert abs(float(frame.time) - 2.8) < 1e-7
        expected = Image.new("RGB", (320, 180))
        # Coded 80x54 at SAR 3:2 displays as 120x54, fitting 320x144.
        expected.paste(frame.to_image().crop((60, 18, 140, 72)).resize((320, 144), Image.Resampling.BICUBIC), (0, 18))
    actual = pair_picture[25]
    assert float(np.abs(actual - np.asarray(expected, dtype=np.float32)).mean()) < 5
    rows, columns = np.where(np.any(actual > 20, axis=2))
    assert columns.min() == 0 and columns.max() == 319
    assert 16 <= rows.min() <= 20 and 159 <= rows.max() <= 164

    full_audio = decoded_audio(directory / "manual-full" / "output.mp4")
    pair_audio = decoded_audio(directory / "manual-pair" / "output.mp4")
    expected_audio = full_audio[13 * 2000:70 * 2000]
    count = min(expected_audio.size, pair_audio.size)
    assert np.abs(pair_audio[2400:count - 2400] - expected_audio[2400:count - 2400]).mean() < .004
    assert np.corrcoef(pair_audio[2400:count - 2400], expected_audio[2400:count - 2400])[0, 1] > .995
    assert all(hashlib.sha256(path.read_bytes()).hexdigest() == digest for path, digest in evidence.items())


def test_unavailable_footage_outside_pair_does_not_block_preview(media):
    config, db, store, document, _, _ = media
    document["clips"][0]["film_id"] = "unavailable-outside-pair"
    with pytest.raises(ValueError, match="unavailable"):
        render_manifest(document, db, store)
    manifest = preview_manifest(document, "slot-1", db, store)
    assert [clip["id"] for clip in manifest["clips"]] == ["clip-1", "clip-2"]
    assert manifest["frame_start"] == 13


def test_preview_requires_two_placed_adjacent_shots_and_valid_source_identity(media):
    _, db, store, document, video, _ = media
    with pytest.raises(ValueError, match="adjacent"):
        preview_manifest(document, "slot-3", db, store)
    empty = deepcopy(document)
    empty["music_timeline"]["slots"][2]["clip_id"] = None
    with pytest.raises(ValueError, match="Both adjacent"):
        preview_manifest(empty, "slot-1", db, store)
    video.write_bytes(video.read_bytes() + b"changed")
    with pytest.raises(ValueError, match="footage changed"):
        preview_manifest(document, "slot-1", db, store)


def test_preview_rejects_changed_original_music(media):
    _, db, store, document, _, audio = media
    audio.write_bytes(audio.read_bytes() + b"changed")
    with pytest.raises(ValueError, match="music changed"):
        preview_manifest(document, "slot-1", db, store)


def test_inspection_is_timestamped_cropped_bounded_and_cached(media, monkeypatch):
    config, db, _, document, _, _ = media
    anchor, offer = document["clips"][1:3]
    result = sample_frames(anchor, [offer], config, db)
    assert len(result["images"]) == 6
    assert all(Path(image["path"]).is_file() for image in result["images"])
    for window, descriptor in zip(result["windows"], (anchor, offer)):
        assert len(window["frames"]) == 3
        assert all(descriptor["source_start"] <= frame["timestamp"] < descriptor["source_end"] for frame in window["frames"])
        assert all(abs(frame["timestamp"] * 30 - round(frame["timestamp"] * 30)) < 1e-6 for frame in window["frames"])
    from PIL import Image
    with Image.open(result["images"][0]["path"]) as image:
        assert image.size == (80, 90)
    monkeypatch.setattr("pipeline.lab.next_scene_media._sample_at", lambda *args, **kwargs: pytest.fail("Valid cache must avoid decoding"))
    assert sample_frames(anchor, [offer], config, db) == result
    with pytest.raises(ValueError, match="six"):
        sample_frames(anchor, [offer] * 7, config, db)
    Path(result["images"][0]["path"]).write_bytes(b"corrupted")
    with pytest.raises(ValueError, match="cache"):
        sample_frames(anchor, [offer], config, db)


def test_inspection_cache_identity_includes_the_actual_window_and_crop(media):
    config, db, _, document, _, _ = media
    anchor = document["clips"][1]
    first = sample_frames(anchor, [], config, db)
    uncropped = sample_frames({**anchor, "crop": None}, [], config, db)
    shifted = sample_frames({**anchor, "source_start": anchor["source_start"] + .1}, [], config, db)
    paths = {result["images"][0]["path"] for result in (first, uncropped, shifted)}
    assert len(paths) == 3


@pytest.mark.parametrize("media", ["anamorphic"], indirect=True)
def test_inspection_normalizes_display_shape_without_relaxing_match_cuts(media):
    from PIL import Image
    from pipeline.matching.media import at
    config, db, _, document, video, _ = media
    anchor, offer = document["clips"][1:3]
    with pytest.raises(ValueError, match="Non-square"):
        at(video, anchor["source_start"], anchor["source_start"], anchor["source_end"])
    result = sample_frames(anchor, [offer], config, db)
    assert result["profile"] == "next-scene-three-pts-display-cropped-jpeg640-v2"
    for window, source, size in zip(result["windows"], (anchor, offer), ((120, 90), (240, 90))):
        assert all(source["source_start"] <= frame["timestamp"] < frame["frame_end"] <= source["source_end"] for frame in window["frames"])
        assert all(abs(frame["timestamp"] * 30 - round(frame["timestamp"] * 30)) < 1e-6 for frame in window["frames"])
        for frame in window["frames"]:
            with Image.open(frame["path"]) as image:
                assert image.size == size


def test_preview_and_inspection_cancel_before_creating_outputs(media):
    config, db, store, document, _, _ = media
    with pytest.raises(JobCancelled):
        render_preview("cancelled", document, "slot-1", config, db, store, lambda _: None, lambda: True)
    with pytest.raises(JobCancelled):
        sample_frames(document["clips"][1], [], config, db, lambda: True)
    assert not (config.paths.assets_dir / "lab" / "renders" / "cancelled").exists()
    assert not (config.paths.assets_dir / "lab" / "next-scene-frames").exists()
