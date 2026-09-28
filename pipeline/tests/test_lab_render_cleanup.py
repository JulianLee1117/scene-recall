"""Render work files live only until encoding has stopped, including failures."""

import json
from pathlib import Path
import stat
from types import SimpleNamespace

import pytest

from pipeline.lab import media


@pytest.fixture
def setup(config, monkeypatch):
    manifest = {
        "fps": 24, "width": 320, "height": 180, "duration": 2., "music": None,
        "clips": [{"gap": True, "frame_count": 24},
                  {"film_id": "film", "source_start": 1., "source_end": 2.,
                   "frame_count": 24, "crop": None}],
    }
    directory = config.paths.assets_dir / "lab" / "renders" / "job"
    monkeypatch.setattr(media, "resolve_film", lambda *_: {"path": "source.mp4"})
    monkeypatch.setattr(media, "probe_media", lambda _: {
        "streams": [{"codec_type": "video", "nb_frames": "48", "duration": "2"}],
    })
    return config, manifest, directory


def fake_encoder(monkeypatch, *, failure=None):
    def run(arguments, **kwargs):
        output = Path(arguments[-1])
        output.write_bytes(b"encoded")
        if output.name == failure:
            raise ValueError("encoder failed")
        return b""
    monkeypatch.setattr(media, "run_process", run)


def assert_no_intermediates(directory):
    assert not (directory / "clips.txt").exists()
    assert not (directory / "output.partial.mp4").exists()
    assert not (directory / "clip-000.mp4").exists()
    assert not (directory / "clip-001.mp4").exists()


def test_success_preserves_published_output_manifest_and_unrelated_files(setup, monkeypatch):
    config, manifest, directory = setup
    directory.mkdir(parents=True)
    unrelated = directory / "clip-999.mp4"
    unrelated.write_bytes(b"not part of this invocation")
    fake_encoder(monkeypatch)

    result = media.render_from_manifest("job", manifest, config, None, None, lambda _: None)

    assert result == {"manifest": manifest}
    assert (directory / "output.mp4").read_bytes() == b"encoded"
    assert json.loads((directory / "manifest.json").read_text()) == manifest
    assert unrelated.read_bytes() == b"not part of this invocation"
    assert_no_intermediates(directory)


@pytest.mark.parametrize("failure", ["clip-001.mp4", "output.partial.mp4", "validation"])
def test_failure_cleans_partial_files_and_keeps_existing_output(setup, monkeypatch, failure):
    config, manifest, directory = setup
    directory.mkdir(parents=True)
    (directory / "output.mp4").write_bytes(b"previous completed output")
    fake_encoder(monkeypatch, failure=failure)
    if failure == "validation":
        monkeypatch.setattr(media, "probe_media", lambda _: {
            "streams": [{"codec_type": "video", "nb_frames": "47", "duration": "2"}],
        })

    with pytest.raises(ValueError):
        media.render_from_manifest("job", manifest, config, None, None, lambda _: None)

    assert_no_intermediates(directory)
    assert (directory / "output.mp4").read_bytes() == b"previous completed output"
    assert json.loads((directory / "manifest.json").read_text()) == manifest


@pytest.mark.parametrize("cancel_target", ["clip-001.mp4", "output.partial.mp4"])
def test_cancellation_waits_for_encoder_before_cleanup(setup, monkeypatch, cancel_target):
    config, manifest, directory = setup
    processes = []

    class Process:
        def __init__(self, arguments, **kwargs):
            self.output = Path(arguments[-1])
            self.output.write_bytes(b"unfinished subprocess output")
            self.returncode = None
            self.killed = False
            self.stopped = False
            processes.append(self)

        def poll(self):
            return self.returncode

        def kill(self):
            self.killed = True

        def communicate(self, timeout=None):
            self.stopped = True
            self.returncode = -1 if self.killed else 0
            return b"", b""

    unlink = Path.unlink

    def checked_unlink(path, *args, **kwargs):
        assert processes and all(process.stopped for process in processes)
        return unlink(path, *args, **kwargs)

    monkeypatch.setattr(media.subprocess, "Popen", Process)
    monkeypatch.setattr(Path, "unlink", checked_unlink)
    with pytest.raises(media.JobCancelled, match="cancelled"):
        media.render_from_manifest("job", manifest, config, None, None, lambda _: None,
                                   cancelled=lambda: processes[-1].output.name == cancel_target)

    assert processes[-1].killed and processes[-1].stopped
    assert_no_intermediates(directory)
    assert (directory / "manifest.json").is_file()
    assert not (directory / "output.mp4").exists()


@pytest.mark.parametrize("fails", [False, True])
def test_cleanup_error_is_logged_without_masking_render_result(setup, monkeypatch, caplog, fails):
    config, manifest, directory = setup
    failure = RuntimeError("original encode failure")

    def run(arguments, **kwargs):
        output = Path(arguments[-1])
        output.write_bytes(b"encoded")
        if fails and output.name == "output.partial.mp4":
            raise failure
        return b""

    unlink = Path.unlink

    def locked_unlink(path, *args, **kwargs):
        if path.name == "clip-000.mp4":
            raise PermissionError("held by another process")
        return unlink(path, *args, **kwargs)

    monkeypatch.setattr(media, "run_process", run)
    monkeypatch.setattr(Path, "unlink", locked_unlink)
    if fails:
        with pytest.raises(RuntimeError) as caught:
            media.render_from_manifest("job", manifest, config, None, None, lambda _: None)
        assert caught.value is failure
    else:
        assert media.render_from_manifest("job", manifest, config, None, None, lambda _: None) == {"manifest": manifest}
        assert (directory / "output.mp4").is_file()
    assert "Could not remove render intermediate" in caplog.text
    assert (directory / "clip-000.mp4").is_file()
    assert not (directory / "clip-001.mp4").exists()
    assert not (directory / "clips.txt").exists()
    assert not (directory / "output.partial.mp4").exists()


@pytest.mark.parametrize("identity", ["../other", "nested/job", "..", "job:alternate", "C:\\outside", ""])
def test_unsafe_identity_is_rejected_before_any_render_write(setup, identity):
    config, manifest, _ = setup
    with pytest.raises(ValueError, match="identity"):
        media.render_from_manifest(identity, manifest, config, None, None, lambda _: None)
    assert not config.paths.assets_dir.exists()


def test_cleanup_skips_reparse_point_instead_of_following_it(tmp_path, monkeypatch, caplog):
    directory = tmp_path / "job"
    directory.mkdir()
    path = directory / "clip-000.mp4"
    path.write_bytes(b"link target must survive")
    lstat = Path.lstat

    def reparse_lstat(candidate, *args, **kwargs):
        if candidate == path:
            return SimpleNamespace(st_mode=stat.S_IFREG | 0o600, st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT)
        return lstat(candidate, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", reparse_lstat)
    media._cleanup_render_intermediates(directory, [path])
    assert path.read_bytes() == b"link target must survive"
    assert "Could not remove render intermediate" in caplog.text


def test_cleanup_rejects_file_outside_job_directory(tmp_path, caplog):
    directory = tmp_path / "job"
    directory.mkdir()
    outside = tmp_path / "clip-000.mp4"
    outside.write_bytes(b"unrelated")
    media._cleanup_render_intermediates(directory, [outside])
    assert outside.read_bytes() == b"unrelated"
    assert "escaped its job directory" in caplog.text
