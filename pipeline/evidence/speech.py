"""Speech activity measured from a film's own audio (Silero VAD).

Used as the timing reference for subtitle synchronization and later as audio
evidence (speech vs. silence per shot). The model ships with faster-whisper,
so no extra dependency or download is needed.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import numpy as np

from pipeline.evidence import store


SAMPLE_RATE = 16000
PRODUCER = store.Producer(
    kind="audio",
    name="silero-vad",
    version=1,
    settings={"sample_rate": SAMPLE_RATE, "threshold": 0.5, "min_speech_ms": 150,
              "min_silence_ms": 300, "speech_pad_ms": 60, "stream": "ffmpeg-default-audio", "downmix": "mono"},
)


def decode_audio(path: Path) -> np.ndarray:
    """Decode the default audio stream to 16 kHz mono float32."""
    command = ["ffmpeg", "-nostdin", "-v", "error", "-threads", "4", "-i", str(path), "-vn", "-sn", "-dn",
               "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "f32le", "pipe:1"]
    completed = subprocess.run(command, capture_output=True, check=False)
    if completed.returncode != 0 or not completed.stdout:
        raise RuntimeError(f"audio decode failed: {completed.stderr.decode('utf-8', 'replace')[-300:]}")
    return np.frombuffer(completed.stdout, dtype=np.float32)


def detect_speech(audio: np.ndarray) -> list[tuple[float, float]]:
    from faster_whisper.vad import VadOptions, get_speech_timestamps
    settings = PRODUCER.settings
    options = VadOptions(threshold=settings["threshold"], min_speech_duration_ms=settings["min_speech_ms"],
                         min_silence_duration_ms=settings["min_silence_ms"], speech_pad_ms=settings["speech_pad_ms"])
    stamps = get_speech_timestamps(audio, options, sampling_rate=SAMPLE_RATE)
    return [(round(row["start"] / SAMPLE_RATE, 3), round(row["end"] / SAMPLE_RATE, 3)) for row in stamps]


def speech_intervals(config: Any, film: Any, *, compute: bool = True) -> list[tuple[float, float]] | None:
    """Cached speech intervals for a film, computing them on first use."""
    inputs = {"source": store.digest([str(film.path), film.duration])}
    artifact = store.read_artifact(config.paths.assets_dir, film.film_id, PRODUCER, inputs=inputs)
    if artifact is not None:
        return [tuple(pair) for pair in artifact["data"]["intervals"]]
    if not compute:
        return None
    intervals = detect_speech(decode_audio(film.path))
    speech_seconds = sum(end - start for start, end in intervals)
    data = {"intervals": intervals, "speech_seconds": round(speech_seconds, 1),
            "density": round(speech_seconds / film.duration, 4) if film.duration else None}
    store.write_artifact(config.paths.assets_dir, film.film_id, PRODUCER, data, inputs=inputs, compress=True)
    return intervals
