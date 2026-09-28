"""Offline playback cache and source-preservation regressions."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from filelock import FileLock

from pipeline.ingest import playback
from pipeline.ingest.probe import FilmRecord


def _film(tmp_path: Path) -> FilmRecord:
    source = tmp_path / "Film.mkv"
    source.write_bytes(b"immutable source")
    return FilmRecord("film-id", source, tmp_path / "assets", 6.0, 24.0, False, "Film")


def _metadata(video_codec="h264", audio_codec="eac3") -> dict:
    return {"format": {"start_time": "0", "duration": "6"}, "streams": [
        {"index": 0, "codec_type": "video", "codec_name": video_codec,
         "start_time": "0", "avg_frame_rate": "24/1"},
        {"index": 1, "codec_type": "audio", "codec_name": audio_codec,
         "start_time": "0", "tags": {"language": "eng"}},
    ]}


def _fake_preparation(monkeypatch: pytest.MonkeyPatch, metadata: dict | None = None) -> list:
    commands = []
    monkeypatch.setattr(playback, "_probe_media", lambda _path: metadata or _metadata())
    monkeypatch.setattr(playback, "_validate_output", lambda *_args: {"samples": [0, 3, 4]})

    def run(command, *, timeout):
        commands.append((command, timeout))
        if command[-1] == "-version":
            return subprocess.CompletedProcess(command, 0, b"ffmpeg version test\n", b"")
        Path(command[-1]).write_bytes(b"derived video")
        return subprocess.CompletedProcess(command, 0, b"", b"")

    monkeypatch.setattr(playback, "_run", run)
    return commands


def test_preparation_publishes_unique_video_and_quick_lookup_reuses_it(tmp_path, monkeypatch):
    film = _film(tmp_path)
    source_bytes = film.path.read_bytes()
    commands = _fake_preparation(monkeypatch)
    output = playback.prepare_playback(film)
    assert output is not None and output.name.startswith("video-")
    manifest = json.loads((output.parent / "manifest.json").read_text())
    assert manifest["filename"] == output.name
    assert manifest["audio"] == {"index": 1, "codec": "eac3", "language": "eng"}
    assert manifest["ffmpeg_version"] == "ffmpeg version test"
    assert playback.lookup_playback(film.path, film.asset_dir) == output
    assert playback.prepare_playback(film) == output
    assert len(commands) == 2
    assert film.path.read_bytes() == source_bytes
    monkeypatch.setattr(playback, "_run", lambda *_a, **_k: pytest.fail("lookup launched a media process"))
    monkeypatch.setattr(playback, "_probe_media", lambda *_a: pytest.fail("lookup probed media"))
    assert playback.lookup_playback(film.path, film.asset_dir) == output


@pytest.mark.parametrize("changed", ["source_size", "source_mtime", "source_inode", "output", "profile"])
def test_lookup_rejects_changed_source_or_derivative(tmp_path, monkeypatch, changed):
    film = _film(tmp_path)
    _fake_preparation(monkeypatch)
    output = playback.prepare_playback(film)
    if changed == "source_size":
        film.path.write_bytes(b"a changed film")
    elif changed == "source_mtime":
        snapshot = film.path.stat()
        os.utime(film.path, ns=(snapshot.st_atime_ns, snapshot.st_mtime_ns + 1_000_000_000))
    elif changed == "source_inode":
        snapshot = film.path.stat()
        replacement = film.path.with_suffix(".replacement")
        replacement.write_bytes(film.path.read_bytes())
        os.utime(replacement, ns=(snapshot.st_atime_ns, snapshot.st_mtime_ns))
        replacement.replace(film.path)
    elif changed == "output":
        output.write_bytes(b"truncated")
    else:
        monkeypatch.setattr(playback, "PROFILE", "future-profile")
    assert playback.lookup_playback(film.path, film.asset_dir) is None


@pytest.mark.parametrize("invalid", ["missing", "partial", "malformed", "oversized", "traversal", "absolute", "version"])
def test_lookup_rejects_partial_or_untrusted_manifests(tmp_path, monkeypatch, invalid):
    film = _film(tmp_path)
    _fake_preparation(monkeypatch)
    output = playback.prepare_playback(film)
    manifest_path = output.parent / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    if invalid == "missing":
        manifest_path.unlink()
    elif invalid == "partial":
        output.unlink()
    elif invalid == "malformed":
        manifest_path.write_text("{not-json")
    elif invalid == "oversized":
        manifest_path.write_bytes(b" " * (playback._MAX_MANIFEST_BYTES + 1))
    else:
        if invalid == "version":
            manifest["version"] = 999
        else:
            manifest["filename"] = "../outside.mp4" if invalid == "traversal" else str(tmp_path / "outside.mp4")
        manifest_path.write_text(json.dumps(manifest))
    assert playback.lookup_playback(film.path, film.asset_dir) is None


def test_lookup_rejects_linked_output(tmp_path, monkeypatch):
    film = _film(tmp_path)
    _fake_preparation(monkeypatch)
    output = playback.prepare_playback(film)
    outside = tmp_path / "outside.mp4"
    outside.write_bytes(output.read_bytes())
    output.unlink()
    try:
        output.symlink_to(outside)
    except OSError:
        pytest.skip("symlink permission is unavailable")
    assert playback.lookup_playback(film.path, film.asset_dir) is None


def test_rebuild_preserves_previously_published_immutable_video(tmp_path, monkeypatch):
    film = _film(tmp_path)
    _fake_preparation(monkeypatch)
    old = playback.prepare_playback(film)
    old_bytes = old.read_bytes()
    (old.parent / "manifest.json").write_text("stale receipt")
    new = playback.prepare_playback(film)
    assert new != old
    assert old.read_bytes() == old_bytes
    assert playback.lookup_playback(film.path, film.asset_dir) == new


def test_default_audio_and_non_cover_video_are_selected_for_encoding(tmp_path, monkeypatch):
    film = _film(tmp_path)
    meta = _metadata("hevc")
    meta["streams"][1]["index"] = 3
    meta["streams"][1]["disposition"] = {"default": 1}
    meta["streams"].insert(0, {"index": 5, "codec_type": "video", "codec_name": "mjpeg",
                               "disposition": {"attached_pic": 1}})
    meta["streams"].insert(2, {"index": 2, "codec_type": "audio", "codec_name": "aac"})
    commands = _fake_preparation(monkeypatch, meta)
    playback.prepare_playback(film)
    command, timeout = commands[-1]
    assert [command[i + 1] for i, token in enumerate(command) if token == "-map"] == ["0:0", "0:3"]
    assert command[command.index("-c:v") + 1] == "copy"
    assert command[command.index("-c:a") + 1] == "aac"
    assert command[command.index("-tag:v") + 1] == "hvc1"
    assert "language=eng" in command and "-shortest" not in command
    assert command[command.index("-protocol_whitelist") + 1] == "file,pipe"
    assert all(command[i + 1] == "2" for i, token in enumerate(command) if token == "-threads")
    assert timeout == 1800


@pytest.mark.parametrize(("video", "audio"), [
    ("h264", "aac"), ("hevc", "mp3"), ("h264", "opus"), ("hevc", "vorbis"), ("vp9", "eac3"),
])
def test_browser_audio_and_unsupported_video_do_not_encode(tmp_path, monkeypatch, video, audio):
    film = _film(tmp_path)
    commands = _fake_preparation(monkeypatch, _metadata(video, audio))
    assert playback.prepare_playback(film) is None
    assert commands == []
    assert not list(film.asset_dir.rglob("manifest.json"))


@pytest.mark.parametrize("source_changes", [False, True])
def test_failed_encoding_never_publishes_and_source_change_is_fatal(tmp_path, monkeypatch, source_changes):
    film = _film(tmp_path)
    _fake_preparation(monkeypatch)

    def run(command, *, timeout):
        if command[-1] == "-version":
            return subprocess.CompletedProcess(command, 0, b"ffmpeg test", b"")
        Path(command[-1]).write_bytes(b"partial conversion")
        if source_changes:
            film.path.write_bytes(b"different source")
        raise playback.PlaybackPreparationError("encoder failed")

    monkeypatch.setattr(playback, "_run", run)
    expected = playback.PlaybackSourceChanged if source_changes else playback.PlaybackPreparationError
    with pytest.raises(expected):
        playback.prepare_playback(film)
    assert not list(film.asset_dir.rglob("manifest.json"))
    assert not list(film.asset_dir.rglob("*.mp4"))


def test_source_changed_during_validation_does_not_publish(tmp_path, monkeypatch):
    film = _film(tmp_path)
    _fake_preparation(monkeypatch)

    def validate(*_args):
        film.path.write_bytes(b"source replaced during validation")
        return {"samples": []}

    monkeypatch.setattr(playback, "_validate_output", validate)
    with pytest.raises(playback.PlaybackSourceChanged):
        playback.prepare_playback(film)
    assert not list(film.asset_dir.rglob("manifest.json"))
    assert not list(film.asset_dir.rglob("*.mp4"))


def test_playback_lock_prevents_competing_preparation(tmp_path, monkeypatch):
    film = _film(tmp_path)
    commands = _fake_preparation(monkeypatch)
    directory = film.asset_dir / "playback" / playback.PROFILE
    directory.mkdir(parents=True)
    with FileLock(directory / ".prepare.lock", timeout=0, preserve_lock_file=True):
        with pytest.raises(playback.PlaybackPreparationError):
            playback.prepare_playback(film)
    assert commands == []


def test_configured_playback_root_prepares_per_film_and_legacy_stays_usable(tmp_path, monkeypatch):
    film = _film(tmp_path)
    commands = _fake_preparation(monkeypatch)
    root = tmp_path / "separate-drive" / "playback"
    output = playback.prepare_playback(film, root)
    assert output.parent == root / film.film_id / playback.PROFILE
    assert playback.lookup_playback(film.path, film.asset_dir, root, film_id=film.film_id) == output
    assert playback.lookup_playback(film.path, film.asset_dir) is None
    assert playback.prepare_playback(film, root) == output
    assert len(commands) == 2


def test_configured_lookup_falls_back_to_valid_legacy_without_reencoding(tmp_path, monkeypatch):
    film = _film(tmp_path)
    commands = _fake_preparation(monkeypatch)
    legacy = playback.prepare_playback(film)
    root = tmp_path / "separate-drive" / "playback"
    assert playback.lookup_playback(film.path, film.asset_dir, root, film_id=film.film_id) == legacy
    assert playback.prepare_playback(film, root) == legacy
    assert len(commands) == 2
    assert not root.exists()


@pytest.mark.parametrize("offset", [-5, 5])
def test_real_conversion_preserves_nonzero_container_timeline(tmp_path, offset):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("ffmpeg tools unavailable")
    source = tmp_path / "offset.ts"
    subprocess.run([
        "ffmpeg", "-v", "error", "-nostdin", "-n", "-f", "lavfi", "-i",
        "testsrc2=size=160x90:rate=24:duration=4", "-itsoffset", "0.2", "-f", "lavfi", "-i",
        "sine=frequency=800:sample_rate=48000:duration=4", "-map", "0:v", "-map", "1:a",
        "-c:v", "libx264", "-threads", "2", "-g", "24", "-c:a", "eac3", "-b:a", "192k",
        "-metadata:s:a:0", "language=eng", "-disposition:a:0", "default",
        "-output_ts_offset", str(offset), "-avoid_negative_ts", "disabled",
        "-muxdelay", "0", "-muxpreload", "0", str(source),
    ], check=True, capture_output=True, timeout=30)
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    meta = playback._probe_media(source)
    assert abs(float(meta["format"]["start_time"])) > 4
    film = FilmRecord("synthetic", source, tmp_path / "assets", float(meta["format"]["duration"]), 24, False, "Synthetic")
    output = playback.prepare_playback(film)
    assert output is not None and playback.lookup_playback(source, film.asset_dir) == output
    assert hashlib.sha256(source.read_bytes()).hexdigest() == source_hash
    manifest = json.loads((output.parent / "manifest.json").read_text())
    assert len(manifest["validation"]["samples"]) == 3
    assert all(abs(sample["source_video_pts"] - sample["output_video_pts"]) <= manifest["validation"]["frame_tolerance"]
               for sample in manifest["validation"]["samples"])
    derived = playback._probe_media(output)
    _, original_audio = playback._selected_streams(meta)
    _, derived_audio = playback._selected_streams(derived)
    original_offset = float(original_audio["start_time"]) - float(meta["format"]["start_time"])
    derived_offset = float(derived_audio["start_time"]) - float(derived["format"]["start_time"])
    assert original_offset > 0.15
    assert abs(original_offset - derived_offset) < 0.1
    assert derived_audio["tags"]["language"] == "eng"
