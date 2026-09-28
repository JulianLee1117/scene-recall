"""Playable compact edit assets: dimensions, timing, keyframes and fast start."""
import struct
import shutil

import av
import pytest

from pipeline.lab.media import run_process
from pipeline.transitions import encoding
from pipeline.transitions.compositor import render_clips
from pipeline.transitions.contracts import Recipe, RenderRequest


@pytest.mark.parametrize("quality,wide,short", [("draft", 854, 480), ("high", 1280, 720), ("export", 1920, 1080)])
def test_explicit_edit_asset_sizes_preserve_aspect_and_resolution(quality, wide, short):
    for aspect, expected in (("landscape", (wide, short)), ("portrait", (short, wide)), ("square", (short, short))):
        request = RenderRequest(outgoing={"film_id": "a", "source_start": 0, "source_end": 2},
                                incoming={"film_id": "b", "source_start": 0, "source_end": 2},
                                output={"quality": quality, "aspect": aspect})
        assert encoding.dimensions(request.output.model_dump()) == expected


def atoms(path):
    with path.open("rb") as source:
        while header := source.read(8):
            size, kind = struct.unpack(">I4s", header)
            if size == 1:
                size = struct.unpack(">Q", source.read(8))[0]
                header = b"x" * 16
            yield kind
            if size == 0:
                break
            source.seek(size - len(header), 1)


def test_export_encode_preserves_cadence_color_and_bounded_random_access(tmp_path):
    if not shutil.which("ffmpeg"):
        pytest.skip("FFmpeg is required for the playable-media check")
    source = tmp_path / "source.mp4"
    run_process(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-f", "lavfi",
                 "-i", "testsrc2=s=192x108:r=30", "-t", "3", "-c:v", "libx264", "-threads", "1", str(source)])
    output = tmp_path / "export.mp4"
    receipt = render_clips([source, source], output, [90, 90], 0,
                           Recipe(id="hard-cut").model_dump(), width=192, height=108,
                           quality="export", cancelled=lambda: False, progress=lambda _: None)
    with av.open(str(output)) as video:
        stream = video.streams.video[0]
        frames = list(video.decode(video=0))
        assert len(frames) == 180
        assert [round(float(frame.pts * frame.time_base) * 30) for frame in frames] == list(range(180))
        keys = [index for index, frame in enumerate(frames) if frame.key_frame]
        assert keys[0] == 0 and max(b - a for a, b in zip(keys, [*keys[1:], len(frames)])) <= 60
        assert stream.codec_context.name == "h264" and stream.pix_fmt == "yuv420p"
        assert stream.codec_context.color_primaries == stream.codec_context.color_trc == stream.codec_context.colorspace == 1
        assert stream.sample_aspect_ratio == 1
    top_level = list(atoms(output))
    assert top_level.index(b"moov") < top_level.index(b"mdat")
    assert receipt["encoding"]["crf"] == 17
    assert receipt["encoding"]["max_keyframe_interval_frames"] == 60
    # A demuxer can begin decoding close to an arbitrary trim point.
    with av.open(str(output)) as video:
        video.seek(int(4.8 * av.time_base), backward=True, any_frame=False)
        decoded = next(video.decode(video=0))
        first_time = float(decoded.pts * decoded.time_base)
        assert 2.8 <= first_time <= 4.8
