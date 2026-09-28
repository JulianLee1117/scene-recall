"""Music map: the measured grid the assembly optimizer cuts to.

Beats and downbeats come from the rhythm stage (Beat This!). The map adds what
cutting needs from the audio itself:

- an onset-strength envelope (log-spectral flux at 100 Hz);
- accents: salient envelope peaks, marked on-beat within 60 ms of a beat;
- per-beat loudness and accent strength as passage percentiles;
- sections: the listening pass's segments (meaning and energy), with
  boundaries snapped to the nearest downbeat.

Everything here is measured or copied from the listening pass; nothing is
inferred about meaning. Times are absolute source-track seconds.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

MAP_CONTRACT = "music-map-v1"
RATE_HZ = 100                  # envelope sample rate
_N_FFT = 1024
_ON_BEAT_S = 0.06
_PEAK_RADIUS_S = 0.1           # at most one accent per 100 ms
_THRESHOLD_WINDOW_S = 1.0
_MIN_RISE = 0.15               # above the local mean envelope
_MIN_STRENGTH = 0.3


@dataclass
class MusicMap:
    start: float
    end: float
    beats: np.ndarray                    # beat times (absolute seconds), ascending
    downbeat: np.ndarray                 # bool per beat
    bar: np.ndarray                      # bar index per beat (0 before the first downbeat)
    beat_energy: np.ndarray              # 0..1 loudness percentile over each beat
    beat_accent: np.ndarray              # 0..1 onset strength at each beat
    accents: list[dict[str, Any]]        # {time, strength, on_beat}
    sections: list[dict[str, Any]]       # {start, end, energy, feeling}
    envelope: np.ndarray = field(repr=False)   # onset strength at RATE_HZ from ``start``
    loudness: np.ndarray = field(repr=False)   # RMS percentile at RATE_HZ from ``start``
    _accent_times: list[float] | None = field(default=None, repr=False)

    def beat_period(self) -> float:
        gaps = np.diff(self.beats)
        return float(np.median(gaps)) if len(gaps) else 0.5

    def energy_between(self, left: float, right: float) -> float:
        """Mean loudness percentile over a span of the passage."""
        a, b = self._index(left), max(self._index(left) + 1, self._index(right))
        return float(self.loudness[a:b].mean()) if b > a else 0.0

    def intensity(self, left: float, right: float) -> float:
        """How intense the music is over a span, 0..1.

        Loudness percentiles are relative to the passage (every song's loudest
        moment is 1.0), so the listening pass's absolute section energy sets
        the level and relative loudness only modulates it within the section.
        """
        middle = (left + right) / 2
        section = next((s for s in self.sections if s["start"] <= middle < s["end"]), None)
        level = section.get("energy") if section else None
        level = float(level) if isinstance(level, (int, float)) else 0.5
        return 0.65 * level + 0.35 * self.energy_between(left, right)

    def strongest_accent(self, left: float, right: float) -> dict[str, Any] | None:
        from bisect import bisect_left
        if self._accent_times is None:
            self._accent_times = [accent["time"] for accent in self.accents]
        first, last = bisect_left(self._accent_times, left), bisect_left(self._accent_times, right)
        inside = self.accents[first:last]
        return max(inside, key=lambda accent: accent["strength"]) if inside else None

    def _index(self, time: float) -> int:
        return int(np.clip(round((time - self.start) * RATE_HZ), 0, len(self.loudness)))

    def summary(self) -> dict[str, Any]:
        """Compact JSON view for prompts and receipts."""
        return {"contract": MAP_CONTRACT, "start": self.start, "end": self.end,
                "beat_period": round(self.beat_period(), 4), "beats": len(self.beats),
                "bars": int(self.bar.max()) + 1 if len(self.bar) else 0,
                "accents": len(self.accents),
                "sections": [{**section, "start": round(section["start"], 3), "end": round(section["end"], 3)}
                             for section in self.sections]}


def _bands(rate: int, bins: int, count: int = 48) -> np.ndarray:
    """Rectangular log-spaced band matrix (bins x bands), 30 Hz to Nyquist."""
    edges = np.geomspace(30.0, rate / 2, count + 1)
    frequencies = np.linspace(0, rate / 2, bins)
    matrix = np.zeros((bins, count), np.float32)
    for band in range(count):
        inside = (frequencies >= edges[band]) & (frequencies < edges[band + 1])
        if not inside.any():                      # narrow low bands: nearest bin
            inside[np.argmin(np.abs(frequencies - (edges[band] + edges[band + 1]) / 2))] = True
        matrix[inside, band] = 1.0 / inside.sum()
    return matrix


def onset_envelope(signal: np.ndarray, rate: int) -> tuple[np.ndarray, np.ndarray]:
    """Band-wise spectral flux (mean dB rise) and RMS at ``RATE_HZ`` (frame i covers time i / RATE_HZ).

    Log-spaced bands weigh a kick and a hi-hat alike, averaging within a band
    tames noise, and an 80 dB floor below the passage maximum keeps silence
    from producing onsets.
    """
    hop = max(1, int(round(rate / RATE_HZ)))
    padded = np.concatenate([np.zeros(_N_FFT // 2, np.float32), signal.astype(np.float32),
                             np.zeros(_N_FFT, np.float32)])
    count = max(1, (len(padded) - _N_FFT) // hop)
    window = np.hanning(_N_FFT).astype(np.float32)
    bands = _bands(rate, _N_FFT // 2 + 1)
    power = np.zeros((count, bands.shape[1]), np.float32)
    rms = np.zeros(count, np.float32)
    for first in range(0, count, 2048):                      # bounded memory for long passages
        last = min(count, first + 2048)
        index = np.arange(first, last)[:, None] * hop + np.arange(_N_FFT)[None, :]
        frames = padded[index]
        rms[first:last] = np.sqrt(np.mean(frames ** 2, axis=1))
        power[first:last] = (np.abs(np.fft.rfft(frames * window, axis=1)) ** 2).astype(np.float32) @ bands
    decibels = 10.0 * np.log10(power + 1e-12)
    decibels = np.maximum(decibels, decibels.max() - 80.0)
    flux = np.zeros(count, np.float32)
    flux[1:] = np.maximum(np.diff(decibels, axis=0), 0.0).mean(axis=1)
    flux = np.convolve(flux, np.array([0.25, 0.5, 0.25], np.float32), mode="same")
    scale = float(np.percentile(flux, 99)) or 1.0
    return flux / scale, rms                  # about 0..1; the loudest 1% exceed 1


def _percentile_rank(values: np.ndarray) -> np.ndarray:
    if not len(values):
        return values
    order = np.argsort(np.argsort(values, kind="stable"), kind="stable")
    return order / max(1, len(values) - 1)


def find_accents(envelope: np.ndarray, beats_relative: np.ndarray) -> list[tuple[float, float, bool]]:
    """Salient onset peaks as (relative time, strength, on_beat)."""
    if len(envelope) < 3:
        return []
    radius = max(1, int(_PEAK_RADIUS_S * RATE_HZ))
    width = max(3, int(2 * _THRESHOLD_WINDOW_S * RATE_HZ) | 1)
    local_mean = np.convolve(envelope, np.ones(width) / width, mode="same")
    candidates = np.flatnonzero((envelope > local_mean + _MIN_RISE) & (envelope > _MIN_STRENGTH))
    # Non-maximum suppression: strongest first, nothing else within the radius.
    taken = np.zeros(len(envelope), bool)
    kept = []
    for index in candidates[np.argsort(-envelope[candidates], kind="stable")]:
        if taken[max(0, index - radius):index + radius + 1].any():
            continue
        taken[index] = True
        kept.append(int(index))
    accents = []
    for index in sorted(kept):
        time = index / RATE_HZ
        on_beat = bool(len(beats_relative)) and float(np.min(np.abs(beats_relative - time))) <= _ON_BEAT_S
        accents.append((time, min(1.0, float(envelope[index])), on_beat))
    return accents


def _sections(analysis: dict[str, Any] | None, start: float, end: float, downbeats: np.ndarray) -> list[dict[str, Any]]:
    segments = [segment for segment in (analysis or {}).get("segments") or []
                if isinstance(segment, dict) and segment.get("end", 0) > segment.get("start", 0)]
    if not segments:
        return [{"start": start, "end": end, "energy": None, "feeling": ""}]
    sections = []
    for index, segment in enumerate(sorted(segments, key=lambda item: item["start"])):
        left = float(segment["start"]) if index else start
        if index and len(downbeats):
            nearest = float(downbeats[np.argmin(np.abs(downbeats - left))])
            gap = float(np.median(np.diff(downbeats))) if len(downbeats) > 1 else 2.0
            if abs(nearest - left) <= gap / 2:
                left = nearest
        sections.append({"start": max(start, left), "end": end, "energy": segment.get("energy"),
                         "feeling": str(segment.get("feeling") or "")[:300]})
    for current, following in zip(sections, sections[1:]):
        current["end"] = following["start"]
    return [section for section in sections if section["end"] - section["start"] > 1e-3]


def build(signal: np.ndarray, rate: int, passage: dict[str, float], rhythm: dict[str, Any] | None,
          analysis: dict[str, Any] | None = None) -> MusicMap:
    """Measure the map for one passage; ``signal`` starts at ``passage['start']``."""
    start, end = float(passage["start"]), float(passage["end"])
    beats = np.array(sorted(t for t in (rhythm or {}).get("beats") or [] if start <= t < end), float)
    downbeat_times = np.array(sorted(t for t in (rhythm or {}).get("downbeats") or [] if start <= t < end), float)
    envelope, rms = onset_envelope(signal, rate)
    frames = int(round((end - start) * RATE_HZ))
    envelope, rms = _fit(envelope, frames), _fit(rms, frames)
    loudness = _percentile_rank(rms).astype(np.float32)
    if not len(beats):
        # No pulse: accents alone guide cuts; a sparse pseudo-grid keeps spans bounded.
        beats = np.arange(start, end, 0.5)
    downbeat = np.array([bool(len(downbeat_times)) and float(np.min(np.abs(downbeat_times - t))) < 0.03
                         for t in beats])
    bar = np.maximum(np.cumsum(downbeat) - 1, 0) if downbeat.any() else np.arange(len(beats)) // 4
    relative = beats - start
    accents = [{"time": round(start + time, 4), "strength": round(strength, 4), "on_beat": on_beat}
               for time, strength, on_beat in find_accents(envelope, relative)]
    envelope = np.clip(envelope, 0.0, 1.0)
    beat_energy, beat_accent = np.zeros(len(beats), np.float32), np.zeros(len(beats), np.float32)
    for index, time in enumerate(beats):
        following = beats[index + 1] if index + 1 < len(beats) else end
        a, b = int((time - start) * RATE_HZ), max(int((time - start) * RATE_HZ) + 1, int((following - start) * RATE_HZ))
        beat_energy[index] = float(loudness[a:b].mean()) if b > a and a < len(loudness) else 0.0
        near = envelope[max(0, a - 5):a + 6]
        beat_accent[index] = float(near.max()) if len(near) else 0.0
    return MusicMap(start=start, end=end, beats=beats, downbeat=downbeat, bar=bar.astype(int),
                    beat_energy=beat_energy, beat_accent=beat_accent, accents=accents,
                    sections=_sections(analysis, start, end, beats[downbeat] if downbeat.any() else beats),
                    envelope=envelope, loudness=loudness)


def _fit(values: np.ndarray, length: int) -> np.ndarray:
    if len(values) >= length:
        return values[:length]
    return np.pad(values, (0, length - len(values)), mode="edge") if len(values) else np.zeros(length, np.float32)
