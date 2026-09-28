"""Explicit compact edit-asset encoding; no automatic high-resolution copies."""
from __future__ import annotations

FPS = 30
KEYFRAME_FRAMES = 60
_SIZES = {"draft": (854, 480), "high": (1280, 720), "export": (1920, 1080)}


def dimensions(output):
    wide, short = _SIZES[output["quality"]]
    return {"landscape": (wide, short), "portrait": (short, wide), "square": (short, short)}[output["aspect"]]


def options(quality):
    if quality not in _SIZES:
        raise ValueError("Choose Draft, Review or Export quality")
    return {"crf": "21" if quality == "draft" else "17", "preset": "fast",
            "g": str(KEYFRAME_FRAMES), "keyint_min": "1", "sc_threshold": "40", "flags": "+cgop"}


def ffmpeg_args(quality):
    values = options(quality)
    return ["-c:v", "libx264", "-preset", values["preset"], "-crf", values["crf"],
            "-g", values["g"], "-keyint_min", values["keyint_min"],
            "-sc_threshold", values["sc_threshold"], "-flags", values["flags"],
            "-pix_fmt", "yuv420p", "-threads", "2"]


def record(quality):
    return {"policy": "compact-h264-closed-gop-2s-v1", "codec": "libx264",
            "pixel_format": "yuv420p", "fps": FPS, "crf": int(options(quality)["crf"]),
            "preset": "fast", "max_keyframe_interval_frames": KEYFRAME_FRAMES,
            "closed_gop": True, "scene_cut_keyframes": True, "faststart": True,
            "purpose": "compact-sdr-edit-asset-not-a-lossless-or-hdr-master"}
