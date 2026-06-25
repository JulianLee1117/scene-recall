"""dialogue.py — extract dialogue lines from a film as a list of DialogueLine.

Primary path (has_embedded_subs=True):
    Use ffmpeg to extract the first subtitle stream to an SRT file, then parse
    that SRT into a list of :class:`DialogueLine` objects.

Fallback path (has_embedded_subs=False):
    Use faster-whisper with word timestamps to transcribe audio and produce
    :class:`DialogueLine` objects from the returned segments.

Output is always saved as ``film.asset_dir / "dialogue.json"``.

Usage::

    from pipeline.ingest.dialogue import extract_dialogue, DialogueLine

    lines = extract_dialogue(film_record, config)
    # lines[0].start, lines[0].end, lines[0].text
"""

from __future__ import annotations

import html
import json
import re
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

from faster_whisper import WhisperModel

from pipeline.config import Config
from pipeline.ingest.probe import FilmRecord


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class DialogueLine:
    """A single unit of transcribed or extracted dialogue."""

    start: float   # Start time in seconds (float64)
    end: float     # End time in seconds (float64)
    text: str      # Cleaned text content


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def extract_dialogue(film: FilmRecord, config: Config) -> list[DialogueLine]:
    """Extract all dialogue from *film* and return as a :class:`DialogueLine` list.

    If *film* has an embedded subtitle stream, ffmpeg extracts it to
    ``film.asset_dir/subs.srt`` which is then parsed.  Otherwise, faster-whisper
    transcribes the audio track.

    The result is also serialised to ``film.asset_dir/dialogue.json`` as a list
    of ``{"start": float, "end": float, "text": str}`` dicts.

    Parameters
    ----------
    film:
        Populated :class:`FilmRecord` from :func:`~pipeline.ingest.probe.probe_film`.
    config:
        Pipeline configuration.  ``config.models.whisper`` selects the
        faster-whisper model when the fallback path is taken.

    Returns
    -------
    list[DialogueLine]
        Dialogue lines in chronological order.
    """
    if film.has_embedded_subs:
        lines = _extract_via_ffmpeg(film)
    else:
        lines = _extract_via_whisper(film, config)

    _save_json(lines, film.asset_dir / "dialogue.json")
    return lines


# ---------------------------------------------------------------------------
# Primary path helpers
# ---------------------------------------------------------------------------


def _extract_via_ffmpeg(film: FilmRecord) -> list[DialogueLine]:
    """Extract the first subtitle stream with ffmpeg and parse the resulting SRT."""
    srt_path = film.asset_dir / "subs.srt"
    cmd = [
        "ffmpeg",
        "-y",
        "-i", str(film.path),
        "-map", "0:s:0",
        "-f", "srt",
        str(srt_path),
    ]
    subprocess.run(cmd, capture_output=True, check=True)
    return _parse_srt(srt_path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Fallback path helpers
# ---------------------------------------------------------------------------


def _extract_via_whisper(film: FilmRecord, config: Config) -> list[DialogueLine]:
    """Transcribe *film* with faster-whisper and return segment-level DialogueLines."""
    model = WhisperModel(config.models.whisper, device="auto", compute_type="default")
    segments, _info = model.transcribe(str(film.path), word_timestamps=False)

    lines: list[DialogueLine] = []
    for seg in segments:
        text = seg.text.strip()
        if text:
            lines.append(DialogueLine(start=float(seg.start), end=float(seg.end), text=text))

    return lines


# ---------------------------------------------------------------------------
# SRT parsing
# ---------------------------------------------------------------------------

# Matches the two-timestamp line: "HH:MM:SS,mmm --> HH:MM:SS,mmm"
_TIMECODE_RE = re.compile(
    r"(\d{2}:\d{2}:\d{2},\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2},\d{3})"
)

# Matches any HTML/SRT tag, e.g. <i>, </b>, <font color="red">
_TAG_RE = re.compile(r"<[^>]+>")


def _parse_srt(text: str) -> list[DialogueLine]:
    """Parse *text* (SRT format) into a list of :class:`DialogueLine`.

    - Strips SRT formatting tags (``<i>``, ``<b>``, ``<font …>``, etc.)
    - Decodes HTML entities (``&amp;``, ``&lt;``, etc.)
    - Joins multi-line subtitle text with a single space
    """
    lines: list[DialogueLine] = []

    # Split on blank lines to get individual subtitle blocks
    blocks = re.split(r"\n\s*\n", text.strip())

    for block in blocks:
        block = block.strip()
        if not block:
            continue

        block_lines = block.splitlines()
        # Find the timecode line (may appear after the sequence number)
        timecode_idx = None
        for i, line in enumerate(block_lines):
            if _TIMECODE_RE.match(line.strip()):
                timecode_idx = i
                break

        if timecode_idx is None:
            continue  # Malformed block — skip

        m = _TIMECODE_RE.match(block_lines[timecode_idx].strip())
        if m is None:
            continue
        start = _parse_srt_timestamp(m.group(1))
        end = _parse_srt_timestamp(m.group(2))

        # Text lines follow the timecode
        raw_text_lines = block_lines[timecode_idx + 1 :]
        cleaned = " ".join(
            html.unescape(_TAG_RE.sub("", line)).strip()
            for line in raw_text_lines
            if line.strip()
        ).strip()

        if cleaned:
            lines.append(DialogueLine(start=start, end=end, text=cleaned))

    return lines


def _parse_srt_timestamp(ts: str) -> float:
    """Convert an SRT timestamp ``HH:MM:SS,mmm`` to seconds as a float.

    Parameters
    ----------
    ts:
        Timestamp string in the form ``HH:MM:SS,mmm``.

    Returns
    -------
    float
        Total seconds, including fractional milliseconds.
    """
    # ts has the form "HH:MM:SS,mmm"
    h_m_s, ms_str = ts.split(",")
    h, m, s = h_m_s.split(":")
    total = int(h) * 3600 + int(m) * 60 + int(s) + int(ms_str) / 1000.0
    return float(total)


# ---------------------------------------------------------------------------
# Serialisation
# ---------------------------------------------------------------------------


def _save_json(lines: list[DialogueLine], path: Path) -> None:
    """Serialise *lines* to *path* as a JSON array."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps([asdict(line) for line in lines], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
