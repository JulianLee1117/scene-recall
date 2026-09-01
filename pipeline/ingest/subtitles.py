"""Lightweight parsing and quality checks for external SRT evidence."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from pathlib import Path


_MAX_EXTERNAL_SRT_BYTES = 16 * 1024 * 1024
_MIN_EXTERNAL_DIALOGUE_CUES = 8
_MIN_EXTERNAL_DIALOGUE_WORDS = 24
_MAX_EXTERNAL_SRT_EXCERPT_CUES = 3
_MAX_EXTERNAL_SRT_EXCERPT_CHARS = 240
_TIMECODE_RE = re.compile(
    r"(\d{2}:\d{2}:\d{2},\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2},\d{3})"
)
_TAG_RE = re.compile(r"<[^>]+>")
_SUBTITLE_WORD_RE = re.compile(r"[^\W\d_]+(?:['’][^\W\d_]+)*")
_PROMOTIONAL_SUBTITLE_RE = re.compile(
    r"(?:https?://|www\.|\b(?:yts|yify|opensubtitles|subscene)\b|"
    r"\b(?:downloaded|provided|uploaded)\s+(?:from|by)\b|"
    r"\bsubtitles?\s+(?:by|from)\b|\bsupport\s+us\b|"
    r"\bvip\s+member\b|\bremove\s+(?:all\s+)?ads\b|"
    r"\badvertise\s+(?:your\s+)?(?:product|brand)\b)",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class SrtCue:
    start: float
    end: float
    text: str


@dataclass(frozen=True, slots=True)
class ExternalSrtInspection:
    """A safe, ephemeral summary of a usable external subtitle."""

    cue_count: int
    word_count: int
    excerpt: str


def read_srt_text(path: Path) -> str:
    """Decode a modern or common legacy SRT without mutating it."""
    data = path.read_bytes()
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        # Older release sidecars commonly use Windows-1252.
        return data.decode("cp1252")


def parse_srt(text: str) -> list[SrtCue]:
    """Parse SRT text into cleaned, timestamped cues."""
    cues: list[SrtCue] = []
    for block in re.split(r"\n\s*\n", text.strip()):
        block_lines = block.strip().splitlines()
        if not block_lines:
            continue

        timecode_match: re.Match[str] | None = None
        timecode_index = 0
        for index, line in enumerate(block_lines):
            if match := _TIMECODE_RE.match(line.strip()):
                timecode_match = match
                timecode_index = index
                break
        if timecode_match is None:
            continue

        cleaned = " ".join(
            html.unescape(_TAG_RE.sub("", line)).strip()
            for line in block_lines[timecode_index + 1 :]
            if line.strip()
        ).strip()
        if cleaned:
            cues.append(
                SrtCue(
                    start=parse_srt_timestamp(timecode_match.group(1)),
                    end=parse_srt_timestamp(timecode_match.group(2)),
                    text=cleaned,
                )
            )
    return cues


def parse_external_dialogue_srt(text: str) -> list[SrtCue]:
    """Parse derived sidecar dialogue, excluding known promotional cues.

    Filtering happens only in the returned derivation. The caller's raw SRT
    text and the source file it came from remain untouched.
    """
    return [
        cue
        for cue in parse_srt(text)
        if not _PROMOTIONAL_SUBTITLE_RE.search(cue.text)
    ]


def parse_srt_timestamp(timestamp: str) -> float:
    """Convert ``HH:MM:SS,mmm`` to seconds."""
    hours_minutes_seconds, milliseconds = timestamp.split(",")
    hours, minutes, seconds = hours_minutes_seconds.split(":")
    return float(
        int(hours) * 3600
        + int(minutes) * 60
        + int(seconds)
        + int(milliseconds) / 1000.0
    )


def inspect_external_srt(path: Path) -> ExternalSrtInspection | None:
    """Inspect a usable external SRT without mutating or persisting it.

    This is deliberately a very low content floor, not a completeness or
    language classifier. It rejects malformed, oversized, promo-only, and
    trivial files that are unsafe to prefer over an embedded track or Whisper.
    Promotional cues are ignored so an otherwise complete subtitle remains
    usable and the original sidecar is never modified.
    """
    try:
        if path.is_symlink() or not path.is_file():
            return None
        size = path.stat().st_size
        if size <= 0 or size > _MAX_EXTERNAL_SRT_BYTES:
            return None
        dialogue_cues = parse_external_dialogue_srt(read_srt_text(path))
    except (OSError, UnicodeError, ValueError):
        return None

    if len(dialogue_cues) < _MIN_EXTERNAL_DIALOGUE_CUES:
        return None
    word_count = sum(
        len(_SUBTITLE_WORD_RE.findall(cue.text)) for cue in dialogue_cues
    )
    if word_count < _MIN_EXTERNAL_DIALOGUE_WORDS:
        return None

    excerpt = " · ".join(
        cue.text for cue in dialogue_cues[:_MAX_EXTERNAL_SRT_EXCERPT_CUES]
    )
    if len(excerpt) > _MAX_EXTERNAL_SRT_EXCERPT_CHARS:
        excerpt = excerpt[: _MAX_EXTERNAL_SRT_EXCERPT_CHARS - 1].rstrip() + "…"
    return ExternalSrtInspection(
        cue_count=len(dialogue_cues),
        word_count=word_count,
        excerpt=excerpt,
    )


def external_srt_is_usable(path: Path) -> bool:
    """Return whether an external SRT contains minimally useful dialogue."""
    return inspect_external_srt(path) is not None
