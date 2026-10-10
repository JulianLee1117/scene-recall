"""Lightweight parsing and quality checks for external SRT evidence."""

from __future__ import annotations

import html
import hashlib
import math
import re
import unicodedata
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


# Hearing-impaired tracks carry sound and speaker cues that are not dialogue: "[ALARM BEEPS]",
# "(sighs)", "[MILES] Hey, dad.", "♪ ♪". Brackets and parentheses are removed from a cue's text and
# a cue with nothing spoken left is dropped. Up to a quarter of the lines of a popular SDH track
# are such cues (Spider-Verse 18%, The Green Knight 32%).
_SOUND_CUE_RE = re.compile(r"\[[^\]]*\]|\([^)]*\)|♪+")
_LEADING_DASHES_RE = re.compile(r"^(?:[-–—]\s*){2,}")      # "- [GASPS] - What?" -> "- What?"
_TRAILING_DASH_RE = re.compile(r"\s*[-–—]$")
_NOTHING_SPOKEN_RE = re.compile(r"[-–—\s.,!?]*")


def strip_sound_cues(text: str) -> str:
    """``text`` without hearing-impaired sound or speaker cues; empty when nothing is spoken."""
    cleaned = re.sub(r"\s+", " ", _SOUND_CUE_RE.sub(" ", text)).strip()
    cleaned = _TRAILING_DASH_RE.sub("", _LEADING_DASHES_RE.sub("- ", cleaned)).strip()
    return "" if _NOTHING_SPOKEN_RE.fullmatch(cleaned) else cleaned


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
    """Parse derived dialogue: known promotional cues excluded, hearing-impaired sound cues
    stripped, cues with nothing spoken dropped.

    Filtering happens only in the returned derivation. The caller's raw SRT
    text and the source file it came from remain untouched.
    """
    cues = []
    for cue in parse_srt(text):
        if _PROMOTIONAL_SUBTITLE_RE.search(cue.text):
            continue
        spoken = strip_sound_cues(cue.text)
        if spoken:
            cues.append(SrtCue(start=cue.start, end=cue.end, text=spoken))
    return cues


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


# Intake and versioned embedded-text checks share this stricter gate. Parsing
# already-selected canonical external evidence retains its separate content floor.
SUBTITLE_VALIDATION_PROFILE = "external-srt-intake-v1"
_STRICT_TIMECODE_RE = re.compile(
    r"(\d{2}:([0-5]\d):([0-5]\d),\d{3})\s*-->\s*"
    r"(\d{2}:([0-5]\d):([0-5]\d),\d{3})"
    r"(?:\s+X1:\d+\s+X2:\d+\s+Y1:\d+\s+Y2:\d+)?"
)
_ENGLISH_WORDS = frozenset("""
    the and you your that this these those what where when why who which
    would could should have has had does did don't doesn't didn't isn't aren't
    wasn't weren't won't can't couldn't wouldn't shouldn't they're you're
    we're we've they've i've i'll he'll she'll you'll we'll there's that's
    it's know think want with from because about here there their them they
    are were was been not but for just said says how very really something
    nothing someone everything please yes let's going come back get got
""".split())


@dataclass(frozen=True, slots=True)
class SubtitleValidation:
    """Reproducible file checks, not proof of audio sync or translation accuracy."""

    valid: bool
    automatic_eligible: bool
    reasons: tuple[str, ...]
    sha256: str | None = None
    cue_count: int = 0
    word_count: int = 0
    first_start: float | None = None
    last_end: float | None = None
    occupied_sections: int = 0
    english_sections: int = 0
    excerpt: str = ""
    profile: str = SUBTITLE_VALIDATION_PROFILE

    @property
    def summary(self) -> str:
        if self.automatic_eligible:
            return "Passes English, subtitle format and film coverage checks. Audio synchronization is not verified."
        return " ".join(self.reasons)


def validate_external_srt(path: Path, duration: float | None = None) -> SubtitleValidation:
    """Check the entire bounded file; uncertain evidence never auto-selects.

    A missing duration is useful for cheap inventory previews, but cannot pass
    the automatic gate. Lexical and distribution thresholds deliberately abstain
    on sparse or mixed-language dialogue; they are not calibrated probabilities.
    """
    digest = None

    def invalid(reason: str) -> SubtitleValidation:
        return SubtitleValidation(False, False, (reason,), sha256=digest)

    try:
        if path.is_symlink() or not path.is_file():
            return invalid("Subtitle is not an ordinary file.")
        with path.open("rb") as handle:
            data = handle.read(_MAX_EXTERNAL_SRT_BYTES + 1)
        if not data or len(data) > _MAX_EXTERNAL_SRT_BYTES:
            return invalid("Subtitle is empty or too large.")
        digest = hashlib.sha256(data).hexdigest()
        try:
            content = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            content = data.decode("cp1252")
    except (OSError, UnicodeError):
        return invalid("Subtitle could not be read or decoded.")
    if any((ord(c) < 32 and c not in "\t\r\n") or c == "\ufffd"
           or 0x7f <= ord(c) <= 0x9f for c in content):
        return invalid("Subtitle contains corrupt text or control characters.")
    cues = []
    for block in re.split(r"\n\s*\n", content.replace("\r\n", "\n").replace("\r", "\n").strip()):
        lines = block.strip().splitlines()
        if lines and lines[0].strip().isdigit():
            lines = lines[1:]
        match = _STRICT_TIMECODE_RE.fullmatch(lines[0].strip()) if lines else None
        if match is None or len(lines) < 2 or any("-->" in line for line in lines[1:]):
            return invalid("Subtitle contains a malformed cue or timestamp.")
        start, end = parse_srt_timestamp(match[1]), parse_srt_timestamp(match[4])
        if end <= start:
            return invalid("Subtitle contains an empty or reversed time interval.")
        # Parse only after strict validation, sharing the existing text cleaning.
        parsed = parse_srt(block)
        if len(parsed) != 1:
            return invalid("Subtitle contains an empty or malformed text cue.")
        cues.append(parsed[0])
    if duration is not None and math.isfinite(duration) and duration > 0:
        if any(cue.end > duration + 2.0 for cue in cues):
            return invalid("Subtitle timestamps extend beyond the film.")
    else:
        duration = None
    dialogue = [cue for cue in cues if not _PROMOTIONAL_SUBTITLE_RE.search(cue.text)]
    words = [_SUBTITLE_WORD_RE.findall(cue.text.lower().replace("’", "'")) for cue in dialogue]
    word_count = sum(map(len, words))
    if len(dialogue) < _MIN_EXTERNAL_DIALOGUE_CUES or word_count < _MIN_EXTERNAL_DIALOGUE_WORDS:
        return invalid("Subtitle contains too little dialogue after removing release advertisements.")
    reasons = []
    first = min(cue.start for cue in dialogue)
    last = max(cue.end for cue in dialogue)
    # Ordinary overlaps can be intentional. Disorder or frequent overlaps need
    # review; a few simultaneous speakers should not disqualify a full track.
    if any(b.start < a.start for a, b in zip(cues, cues[1:])):
        reasons.append("Subtitle cues are out of order.")
    if sum(b.start < a.end for a, b in zip(cues, cues[1:])) > max(3, len(cues) * .05):
        reasons.append("Many subtitle cues overlap.")
    if any(cue.end - cue.start > 30 for cue in dialogue):
        reasons.append("Some dialogue cues stay on screen unusually long.")
    normalized = {re.sub(r"\W+", " ", cue.text.casefold()).strip() for cue in dialogue}
    if len(normalized) < len(dialogue) * .5:
        reasons.append("Too much subtitle text repeats to select it automatically.")
    english_sections = 0
    # Test separate portions, not just the opening excerpt or an English label.
    for section in range(3):
        lo, hi = len(dialogue) * section // 3, len(dialogue) * (section + 1) // 3
        tokens = [word for cue_words in words[lo:hi] for word in cue_words]
        letters = [c for cue in dialogue[lo:hi] for c in cue.text if c.isalpha()]
        latin = sum("LATIN" in unicodedata.name(c, "") for c in letters)
        hits = [token for token in tokens if token in _ENGLISH_WORDS]
        if (len(tokens) >= 40 and letters and latin / len(letters) >= .95
                and len(hits) / len(tokens) >= .18 and len(set(hits)) >= 8):
            english_sections += 1
    if english_sections != 3:
        reasons.append("English dialogue could not be established throughout the file.")
    occupied = 0
    if duration is None:
        reasons.append("Film duration will be checked when adding the film.")
    else:
        occupied = len({min(9, int(cue.start / duration * 10)) for cue in dialogue})
        if (first > duration * .15 or last < duration * .8
                or last - first < duration * .7 or occupied < 8):
            reasons.append("Dialogue does not cover enough of the film for automatic selection.")
        if len(dialogue) < max(30, duration / 60) or word_count < max(120, duration / 60 * 8):
            reasons.append("Dialogue is too sparse to rule out a partial or forced-only track.")
    excerpt = " · ".join(cue.text for cue in dialogue[:_MAX_EXTERNAL_SRT_EXCERPT_CUES])
    if len(excerpt) > _MAX_EXTERNAL_SRT_EXCERPT_CHARS:
        excerpt = excerpt[:_MAX_EXTERNAL_SRT_EXCERPT_CHARS - 1].rstrip() + "…"
    return SubtitleValidation(
        True, not reasons, tuple(reasons), digest, len(dialogue), word_count,
        first, last, occupied, english_sections, excerpt,
    )
