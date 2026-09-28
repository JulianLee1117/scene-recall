"""Synchronize downloaded subtitles to a film using a timed speech reference.

The reference is any audio-timed transcript of the same film (normally the
Whisper segments already produced at ingest). Both are reduced to binary
speech masks; FFT cross-correlation finds the best frame-rate scale and global
offset, then windowed refinement follows edits that differ between releases.
Confidence is reported, never assumed:

* ``lift``        — overlap precision divided by the reference speech density
                    (1.0 means no better than chance);
* ``prominence``  — how far the chosen offset stands out from other offsets;
* ``text_agreement`` — for same-language references, shared words near each cue.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import re
from typing import Iterable, Sequence

import numpy as np


RESOLUTION = 0.1                  # seconds per mask bin
MAX_GLOBAL_SHIFT = 300.0          # seconds
WINDOW_SECONDS = 360.0
WINDOW_MAX_SHIFT = 8.0            # seconds, relative to the global solution
WINDOW_MIN_CUES = 12
SCALES = (1.0, 25 / 23.976, 23.976 / 25, 24 / 25, 25 / 24, 24 / 23.976, 23.976 / 24, 1001 / 1000, 1000 / 1001)
_STOPWORDS = frozenset("the and you that for are was but not with have this what your from they his her she him".split())

Cue = tuple[float, float, str]


@dataclass
class SyncResult:
    scale: float
    offset: float
    windows: list[tuple[float, float, float]] = field(default_factory=list)  # (start, end, extra shift)
    precision_before: float = 0.0
    precision_after: float = 0.0
    recall_after: float = 0.0
    density: float = 0.0
    prominence: float = 0.0
    text_agreement: float | None = None

    @property
    def lift(self) -> float:
        return self.precision_after / self.density if self.density > 0 else 0.0

    def as_dict(self) -> dict:
        data = asdict(self)
        data["lift"] = round(self.lift, 4)
        return data


def speech_mask(intervals: Iterable[tuple[float, float]], bins: int) -> np.ndarray:
    mask = np.zeros(bins, dtype=np.float32)
    for start, end in intervals:
        left = max(0, int(start / RESOLUTION))
        right = min(bins, int(np.ceil(end / RESOLUTION)))
        if right > left:
            mask[left:right] = 1.0
    return mask


def _correlate(reference: np.ndarray, moving: np.ndarray, max_shift: int) -> tuple[np.ndarray, np.ndarray]:
    """Overlap for every shift of *moving* within ±max_shift bins (shift>0 moves later)."""
    size = 1 << int(np.ceil(np.log2(len(reference) + len(moving) + 1)))
    spectrum = np.fft.rfft(reference, size) * np.conj(np.fft.rfft(moving, size))
    circular = np.fft.irfft(spectrum, size)
    shifts = np.arange(-max_shift, max_shift + 1)
    return shifts, circular[shifts % size]


def _transform(cues: Sequence[Cue], scale: float, offset: float) -> list[Cue]:
    return [(start * scale + offset, end * scale + offset, text) for start, end, text in cues]


def _precision(reference: np.ndarray, cues: Sequence[Cue]) -> float:
    mask = speech_mask(((s, e) for s, e, _t in cues), len(reference))
    total = float(mask.sum())
    return float((mask * reference).sum() / total) if total else 0.0


def align(reference: Sequence[Cue], subtitle: Sequence[Cue], duration: float) -> SyncResult:
    """Find scale, global offset and windowed shifts mapping *subtitle* onto *reference*."""
    if not reference or not subtitle or duration <= 0:
        raise ValueError("alignment needs a reference, a subtitle and a positive duration")
    bins = int(np.ceil(duration / RESOLUTION)) + 1
    ref_mask = speech_mask(((s, e) for s, e, _t in reference), bins)
    density = float(ref_mask.mean())
    max_shift = int(MAX_GLOBAL_SHIFT / RESOLUTION)
    best: tuple[float, float, float, float] | None = None  # (score, scale, offset, prominence)
    for scale in SCALES:
        moving = speech_mask(((s * scale, e * scale) for s, e, _t in subtitle), bins)
        total = float(moving.sum())
        if total == 0:
            continue
        shifts, overlap = _correlate(ref_mask, moving, max_shift)
        index = int(np.argmax(overlap))
        score = float(overlap[index] / total)
        spread = float(overlap.std()) or 1.0
        prominence = float((overlap[index] - np.median(overlap)) / spread)
        candidate = (score, scale, float(shifts[index] * RESOLUTION), prominence)
        if best is None or candidate[0] > best[0] + 1e-9:
            best = candidate
    if best is None:
        raise ValueError("subtitle has no timed speech")
    _score, scale, offset, prominence = best
    result = SyncResult(scale=scale, offset=offset, density=density, prominence=prominence,
                        precision_before=_precision(ref_mask, subtitle))
    globally = _transform(subtitle, scale, offset)
    result.windows = _refine_windows(ref_mask, globally)
    synced = apply_windows(globally, result.windows)
    result.precision_after = _precision(ref_mask, synced)
    sub_mask = speech_mask(((s, e) for s, e, _t in synced), bins)
    result.recall_after = float((sub_mask * ref_mask).sum() / max(1.0, float(ref_mask.sum())))
    return result


def _refine_windows(ref_mask: np.ndarray, cues: Sequence[Cue]) -> list[tuple[float, float, float]]:
    if not cues:
        return []
    windows: list[list[Cue]] = [[]]
    window_start = cues[0][0]
    for cue in cues:
        if cue[0] - window_start > WINDOW_SECONDS and len(windows[-1]) >= WINDOW_MIN_CUES:
            windows.append([])
            window_start = cue[0]
        windows[-1].append(cue)
    limit = int(WINDOW_MAX_SHIFT / RESOLUTION)
    shifts = []
    for group in windows:
        if len(group) < WINDOW_MIN_CUES:
            shifts.append(0.0)
            continue
        moving = speech_mask(((s, e) for s, e, _t in group), len(ref_mask))
        total = float(moving.sum())
        offsets, overlap = _correlate(ref_mask, moving, limit)
        zero = float(overlap[limit] / total) if total else 0.0
        index = int(np.argmax(overlap))
        best = float(overlap[index] / total) if total else 0.0
        shifts.append(float(offsets[index] * RESOLUTION) if best >= zero + 0.08 else 0.0)
    # A single outlier window is more likely noise than a re-edit.
    smoothed = [float(np.median(shifts[max(0, i - 1): i + 2])) for i in range(len(shifts))]
    result = []
    for group, shift in zip(windows, smoothed):
        result.append((group[0][0], group[-1][1], shift))
    return result


def apply_windows(cues: Sequence[Cue], windows: Sequence[tuple[float, float, float]]) -> list[Cue]:
    if not windows:
        return list(cues)
    out = []
    for start, end, text in cues:
        shift = 0.0
        for w_start, w_end, w_shift in windows:
            if start >= w_start - 1e-6:
                shift = w_shift
            if start <= w_end:
                break
        out.append((start + shift, end + shift, text))
    return out


def apply(cues: Sequence[Cue], result: SyncResult) -> list[Cue]:
    """Apply a sync result; cues are clipped to non-negative, strictly ordered times."""
    moved = apply_windows(_transform(cues, result.scale, result.offset), result.windows)
    fixed = []
    for start, end, text in sorted(moved, key=lambda cue: cue[0]):
        start = max(start, 0.0)
        end = max(end, start + 0.2)
        fixed.append((round(start, 3), round(end, 3), text))
    return fixed


def _words(text: str) -> set[str]:
    return {word for word in re.findall(r"[a-z']{3,}", text.casefold()) if word not in _STOPWORDS}


def text_agreement(reference: Sequence[Cue], synced: Sequence[Cue], *, slack: float = 1.5, sample: int = 400) -> float | None:
    """Mean share of each subtitle cue's words found in nearby reference text."""
    starts = np.array([cue[0] for cue in reference])
    step = max(1, len(synced) // sample)
    scores = []
    for start, end, text in synced[::step]:
        words = _words(text)
        if len(words) < 2:
            continue
        lo = int(np.searchsorted(starts, start - 30.0))
        nearby: set[str] = set()
        for ref_start, ref_end, ref_text in reference[lo:]:
            if ref_start > end + slack:
                break
            if ref_end >= start - slack:
                nearby |= _words(ref_text)
        scores.append(len(words & nearby) / len(words))
    return float(np.mean(scores)) if scores else None


def is_mostly_ascii(cues: Sequence[Cue], sample: int = 400) -> bool:
    letters = [ch for _s, _e, text in cues[:sample] for ch in text if ch.isalpha()]
    return bool(letters) and sum(ch.isascii() for ch in letters) / len(letters) > 0.99
