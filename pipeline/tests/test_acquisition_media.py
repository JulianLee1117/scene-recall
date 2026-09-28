"""Exercise the acquisition gate with real local media, without inference."""
import json
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from pipeline.acquisition.engine import inspect_media


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="ffmpeg required")
def test_real_video_decodes_at_all_sample_positions_and_truncated_video_fails(tmp_path):
    source = tmp_path / "fixture.mkv"
    subprocess.run([
        "ffmpeg", "-v", "error", "-nostdin", "-f", "lavfi", "-i",
        "testsrc2=size=128x96:rate=12", "-t", "4", "-c:v", "mpeg4",
        "-threads", "1", str(source),
    ], check=True, timeout=30, capture_output=True)
    checked = inspect_media(source)
    assert checked["duration"] == pytest.approx(4, abs=.1)
    assert checked["decode_samples"] == 3
    assert checked["streams"][0]["width"] == 128

    corrupt = tmp_path / "truncated.mkv"
    corrupt.write_bytes(source.read_bytes()[:32])
    with pytest.raises(ValueError, match="validation failed"):
        inspect_media(corrupt)


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="ffmpeg required")
@pytest.mark.parametrize("epoch", [0, 5])
def test_audio_outlasting_video_samples_video_bounds_and_preserves_container_duration(tmp_path, epoch):
    source = tmp_path / "audio-tail.mp4"
    subprocess.run([
        "ffmpeg", "-v", "error", "-nostdin", "-f", "lavfi", "-i",
        "testsrc2=size=128x96:rate=12:duration=4", "-f", "lavfi", "-i",
        "sine=frequency=440:duration=10", "-c:v", "mpeg4", "-c:a", "aac",
        "-threads", "1", "-output_ts_offset", str(epoch), str(source),
    ], check=True, timeout=30, capture_output=True)
    checked = inspect_media(source)
    video = next(stream for stream in checked["streams"] if stream["codec_type"] == "video")
    audio = next(stream for stream in checked["streams"] if stream["codec_type"] == "audio")
    assert float(video["duration"]) == pytest.approx(4, abs=.1)
    assert float(audio["duration"]) > float(video["duration"]) + 5
    assert checked["duration"] >= 10  # duration for subtitle validation stays unchanged
    assert checked["decode_samples"] == 3


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="ffmpeg required")
@pytest.mark.parametrize("epoch", [0, 5])
def test_matroska_audio_tail_uses_video_duration_tag_as_endpoint(tmp_path, epoch):
    source = tmp_path / "audio-tail.mkv"
    subprocess.run([
        "ffmpeg", "-v", "error", "-nostdin", "-f", "lavfi", "-i",
        "testsrc2=size=128x96:rate=12:duration=4", "-f", "lavfi", "-i",
        "sine=frequency=440:duration=10", "-c:v", "mpeg4", "-c:a", "pcm_s16le",
        "-threads", "1", "-output_ts_offset", str(epoch), str(source),
    ], check=True, timeout=30, capture_output=True)
    checked = inspect_media(source)
    video = next(stream for stream in checked["streams"] if stream["codec_type"] == "video")
    assert "duration" not in video  # reproduces the MKV-only metadata path
    assert float(video["start_time"]) == epoch
    assert video["tags"]["DURATION"] == f"00:00:{epoch + 4:02d}.000000000"
    assert checked["duration"] == pytest.approx(epoch + 10, abs=.1)
    assert checked["decode_samples"] == 3


def mocked_probe(monkeypatch, metadata, *, failed_sample=None):
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        if command[0] == "ffprobe":
            return SimpleNamespace(stdout=json.dumps(metadata).encode())
        samples = len(calls) - 1
        return SimpleNamespace(stdout=b"" if samples == failed_sample else b"x" * (64 * 64 * 3))

    monkeypatch.setattr("pipeline.acquisition.engine.subprocess.run", run)
    return calls


@pytest.mark.parametrize("origin,start,video_duration,expected", [
    (0, 0, 8, [0, 4, 6]),
    (100, 102, 8, [2, 6, 8]),
    (-5, -3, 8, [2, 6, 8]),
    (0, -2, 8, [0, 3, 4]),
    (0, 8, 8, [8, 10, 10]),
    (0, 1, 1, [1, 1.5, 1]),
    (None, None, 8, [0, 4, 6]),
])
def test_selected_first_video_uses_player_time_interval(monkeypatch, origin, start, video_duration, expected):
    metadata = {"format": {"duration": "12", "start_time": origin}, "streams": [
        {"codec_type": "audio", "duration": "12", "start_time": origin},
        {"codec_type": "video", "width": 128, "duration": video_duration, "start_time": start},
        {"codec_type": "video", "width": 128, "duration": "99", "start_time": "0"},
    ]}
    calls = mocked_probe(monkeypatch, metadata)
    checked = inspect_media(Path("fixture.mp4"))
    assert checked["duration"] == 12
    assert len(calls) == 4
    assert [float(command[command.index("-ss") + 1]) for command, _ in calls[1:]] == expected
    for command, kwargs in calls[1:]:
        assert command[command.index("-map") + 1] == "0:v:0"
        assert kwargs["timeout"] == 30 and kwargs["check"]


@pytest.mark.parametrize("video_duration", [None, "N/A", "nan", "inf", "-1", "0"])
def test_unknown_or_invalid_video_duration_keeps_bounded_container_fallback(monkeypatch, video_duration):
    calls = mocked_probe(monkeypatch, {"format": {"duration": "12"}, "streams": [
        {"codec_type": "video", "width": 128, "duration": video_duration},
    ]})
    assert inspect_media(Path("fixture.mkv"))["decode_samples"] == 3
    assert [float(command[command.index("-ss") + 1]) for command, _ in calls[1:]] == [0, 6, 10]


@pytest.mark.parametrize("origin,start,tag,expected", [
    (0, 0, "02:38:09.694000000", [0, 4744.847, 9487.694]),
    (5, 5, "00:00:09.000000000", [0, 2, 2]),
    (5, 7, "00:00:11.000000000", [2, 4, 4]),
])
def test_matroska_tag_samples_selected_video_in_player_coordinates(monkeypatch, origin, start, tag, expected):
    calls = mocked_probe(monkeypatch, {"format": {
        "format_name": "matroska,webm", "duration": "9493.6", "start_time": origin,
    }, "streams": [
        {"codec_type": "audio", "tags": {"DURATION": "02:38:13.600000000"}},
        {"codec_type": "video", "width": 128, "start_time": start, "tags": {"DURATION": tag}},
        {"codec_type": "video", "width": 128, "tags": {"DURATION": "00:00:01.000000000"}},
    ]})
    assert inspect_media(Path("fixture.mkv"))["duration"] == 9493.6
    assert len(calls) == 4
    assert "stream_tags=DURATION" in calls[0][0][calls[0][0].index("-show_entries") + 1]
    assert [float(command[command.index("-ss") + 1]) for command, _ in calls[1:]] == pytest.approx(expected)
    assert all(command[command.index("-map") + 1] == "0:v:0" for command, _ in calls[1:])


@pytest.mark.parametrize("tag", [None, "N/A", "00:60:00", "00:00:60", "-00:00:08", "00:00:nan", "00:00:00", "00:00:99", "00:00:13"])
def test_invalid_or_out_of_bounds_matroska_tag_keeps_container_fallback(monkeypatch, tag):
    calls = mocked_probe(monkeypatch, {"format": {
        "format_name": "matroska,webm", "duration": "12", "start_time": "0",
    }, "streams": [
        {"codec_type": "video", "width": 128, "tags": {"DURATION": tag, "NUMBER_OF_FRAMES": "1", "BPS": "8"}},
    ]})
    assert inspect_media(Path("fixture.mkv"))["decode_samples"] == 3
    assert [float(command[command.index("-ss") + 1]) for command, _ in calls[1:]] == [0, 6, 10]


@pytest.mark.parametrize("format_name,video_duration,expected", [
    ("matroska,webm", "8", [0, 4, 6]),  # numeric duration takes precedence
    ("mov,mp4,m4a,3gp,3g2,mj2", None, [0, 6, 10]),  # arbitrary tags outside MKV are ignored
])
def test_duration_tag_is_only_matroska_fallback(monkeypatch, format_name, video_duration, expected):
    calls = mocked_probe(monkeypatch, {"format": {"format_name": format_name, "duration": "12"}, "streams": [
        {"codec_type": "video", "width": 128, "duration": video_duration, "tags": {"DURATION": "00:00:04"}},
    ]})
    assert inspect_media(Path("fixture"))["decode_samples"] == 3
    assert [float(command[command.index("-ss") + 1]) for command, _ in calls[1:]] == expected


@pytest.mark.parametrize("duration", [None, "N/A", "nan", "inf", "0", "-1"])
def test_invalid_container_duration_is_rejected_before_decode(monkeypatch, duration):
    calls = mocked_probe(monkeypatch, {"format": {"duration": duration}, "streams": [
        {"codec_type": "video", "width": 128, "duration": "8"},
    ]})
    with pytest.raises(ValueError, match="does not contain playable video"):
        inspect_media(Path("fixture.mp4"))
    assert len(calls) == 1


def test_unplayable_first_video_cannot_borrow_later_stream_metadata(monkeypatch):
    calls = mocked_probe(monkeypatch, {"format": {"duration": "12"}, "streams": [
        {"codec_type": "video", "width": 0, "duration": "8"},
        {"codec_type": "video", "width": 128, "duration": "8"},
    ]})
    with pytest.raises(ValueError, match="does not contain playable video"):
        inspect_media(Path("fixture.mp4"))
    assert len(calls) == 1


@pytest.mark.parametrize("video_start", ["20", "-20"])
def test_disjoint_video_interval_is_rejected_without_guessing(monkeypatch, video_start):
    calls = mocked_probe(monkeypatch, {"format": {"duration": "12", "start_time": "0"}, "streams": [
        {"codec_type": "video", "width": 128, "duration": "8", "start_time": video_start},
    ]})
    with pytest.raises(ValueError, match="no playable interval"):
        inspect_media(Path("fixture.mp4"))
    assert len(calls) == 1


@pytest.mark.parametrize("video_metadata", [{"duration": "8"}, {"tags": {"DURATION": "00:00:08"}}])
def test_empty_sample_fails_without_retrying_another_position(monkeypatch, video_metadata):
    calls = mocked_probe(monkeypatch, {"format": {"format_name": "matroska,webm", "duration": "12"}, "streams": [
        {"codec_type": "video", "width": 128, "start_time": "0", **video_metadata},
    ]}, failed_sample=2)
    with pytest.raises(ValueError, match="sampled position could not be decoded"):
        inspect_media(Path("fixture.mp4"))
    assert len(calls) == 3  # ffprobe, first sample, failed midpoint; no alternate seek
