"""dialogue.py — extract dialogue lines from a film as a list of DialogueLine.

Primary path (text subtitle available):
    Use ffmpeg to extract the first convertible text subtitle stream to an SRT
    file, then parse that SRT into a list of :class:`DialogueLine` objects.

Fallback path (no subtitles or bitmap-only subtitles):
    Use faster-whisper to transcribe audio and produce :class:`DialogueLine`
    objects from the returned segments.

Output is always saved as ``film.asset_dir / "dialogue.json"``.

Usage::

    from pipeline.ingest.dialogue import extract_dialogue, DialogueLine

    lines = extract_dialogue(film_record, config)
    # lines[0].start, lines[0].end, lines[0].text
"""

from __future__ import annotations

import hashlib
import json
import math
import subprocess
import unicodedata
from collections import Counter
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from numbers import Real
from pathlib import Path

from faster_whisper import WhisperModel, __version__ as _FASTER_WHISPER_VERSION

from pipeline.config import Config
from pipeline.ingest.probe import FilmRecord
from pipeline.ingest.subtitles import (
    external_srt_is_usable,
    parse_external_dialogue_srt,
    parse_srt,
    parse_srt_timestamp as _parse_srt_timestamp,
    read_srt_text as _read_srt_text,
)


_DIALOGUE_CONTRACT_VERSION = 2
_DIALOGUE_MANIFEST_NAME = "dialogue.manifest.json"
_SIDECAR_PROFILE_VERSION = 1
_WHISPER_PROFILE_VERSION = 2
_WHISPER_QUALITY_GATE_VERSION = 1
_WHISPER_LOOP_REJECT_AT = 16
_WHISPER_LOOP_MIN_TOKENS = 3
_WHISPER_LOOP_MIN_LETTERS = 8
_WHISPER_SPARSE_REJECT_AT = 20
_WHISPER_SPARSE_MIN_DURATION_S = 25.0
_WHISPER_SPARSE_MAX_TOKENS = 8
_WHISPER_SPARSE_MIN_GLOBAL_OCCURRENCES = 5
_TRUSTED_WHISPER_AUDIO_LANGUAGE_TAGS = {
    "en": "en",
    "eng": "en",
}
_WHISPER_TRANSCRIBE_OPTIONS: dict[str, object] = {
    "language": None,
    "task": "transcribe",
    "word_timestamps": False,
    "vad_filter": True,
    "condition_on_previous_text": False,
    # faster-whisper 1.2.1 otherwise exits language detection as soon as one
    # 30-second window exceeds its confidence threshold. A threshold of 1.0
    # makes it majority-vote across up to five VAD-filtered speech windows.
    "language_detection_threshold": 1.0,
    "language_detection_segments": 5,
}


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

    A usable, non-trivial canonical English SRT sidecar is preferred when
    present. Otherwise, an FFmpeg-convertible embedded text subtitle stream is
    extracted to
    ``film.asset_dir/subs.srt`` and parsed. If neither exists, faster-whisper
    transcribes the audio track. Bitmap subtitles such as PGS require OCR, so
    they intentionally take the audio fallback.

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
    source = _dialogue_source(film, config)
    if source["kind"] == "sidecar_srt":
        sidecar = _sidecar_subtitle_path(film)
        if sidecar is None:  # The source changed between resolution and use.
            raise RuntimeError("dialogue sidecar disappeared during ingestion")
        print(f"[dialogue] using external subtitles: {sidecar.name}", flush=True)
        lines = _parse_external_srt(_read_srt_text(sidecar))
    elif film.text_subtitle_stream_index is not None:
        lines = _extract_via_ffmpeg(film, film.text_subtitle_stream_index)
    else:
        lines = _extract_via_whisper(film, config)

    _save_json(lines, film.asset_dir / "dialogue.json")
    _save_manifest(source, film.asset_dir / _DIALOGUE_MANIFEST_NAME)
    return lines


def dialogue_cache_is_current(film: FilmRecord, config: Config) -> bool:
    """Return whether cached dialogue matches its raw/model-versioned source."""
    dialogue_path = film.asset_dir / "dialogue.json"
    manifest_path = film.asset_dir / _DIALOGUE_MANIFEST_NAME
    if not dialogue_path.is_file() or not manifest_path.is_file():
        return False
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False
    return manifest == _dialogue_source(film, config)


def _dialogue_source(film: FilmRecord, config: Config) -> dict[str, object]:
    """Describe the exact evidence/model contract used to derive dialogue."""
    sidecar = _sidecar_subtitle_path(film)
    if sidecar is not None:
        return {
            "contract_version": _DIALOGUE_CONTRACT_VERSION,
            "kind": "sidecar_srt",
            "filename": sidecar.name,
            "sha256": _file_sha256(sidecar),
            "derivation_profile": {
                "profile_version": _SIDECAR_PROFILE_VERSION,
                "exclude_promotional_cues": True,
            },
        }
    if film.text_subtitle_stream_index is not None:
        return {
            "contract_version": _DIALOGUE_CONTRACT_VERSION,
            "kind": "embedded_text",
            "film_id": film.film_id,
            "stream_index": film.text_subtitle_stream_index,
        }
    return {
        "contract_version": _DIALOGUE_CONTRACT_VERSION,
        "kind": "whisper",
        "film_id": film.film_id,
        "model": config.models.whisper,
        "transcription_profile": _whisper_transcription_profile(film),
    }


def _whisper_transcription_profile(film: FilmRecord) -> dict[str, object]:
    """Return the exact model options and evidence used for one film."""
    options = _whisper_transcribe_options(film)
    profile: dict[str, object] = {
        "profile_version": _WHISPER_PROFILE_VERSION,
        "engine": "faster-whisper",
        "engine_version": _FASTER_WHISPER_VERSION,
        "options": options,
        "quality_gate": _whisper_quality_gate_profile(),
    }
    if options["language"] is not None:
        profile["language_hint"] = {
            "source": "primary_audio_stream_tag",
            "tag": film.primary_audio_language_tag,
        }
    return profile


def _whisper_transcribe_options(film: FilmRecord) -> dict[str, object]:
    """Resolve model options without trusting ambiguous container metadata."""
    options = dict(_WHISPER_TRANSCRIBE_OPTIONS)
    tag = film.primary_audio_language_tag
    if isinstance(tag, str):
        options["language"] = _TRUSTED_WHISPER_AUDIO_LANGUAGE_TAGS.get(
            tag.strip().casefold()
        )
    return options


def _whisper_quality_gate_profile() -> dict[str, object]:
    """Return the exact fail-closed transcript gate recorded in manifests."""
    return {
        "gate_version": _WHISPER_QUALITY_GATE_VERSION,
        "consecutive_exact_repeat": {
            "reject_at": _WHISPER_LOOP_REJECT_AT,
            "minimum_tokens": _WHISPER_LOOP_MIN_TOKENS,
            "minimum_letters": _WHISPER_LOOP_MIN_LETTERS,
        },
        "repeated_sparse_long_segment": {
            "reject_at": _WHISPER_SPARSE_REJECT_AT,
            "minimum_duration_seconds": _WHISPER_SPARSE_MIN_DURATION_S,
            "maximum_tokens": _WHISPER_SPARSE_MAX_TOKENS,
            "minimum_global_occurrences": (
                _WHISPER_SPARSE_MIN_GLOBAL_OCCURRENCES
            ),
        },
    }


def _sidecar_subtitle_path(film: FilmRecord) -> Path | None:
    """Resolve a usable canonical English SRT beside the immutable film."""
    stem = film.path.stem
    for suffix in (".en.srt", ".eng.srt", ".english.srt", ".srt"):
        candidate = film.path.with_name(stem + suffix)
        if external_srt_is_usable(candidate):
            return candidate.resolve()
    return None


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _save_manifest(source: dict[str, object], path: Path) -> None:
    path.write_text(
        json.dumps(source, indent=2, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Primary path helpers
# ---------------------------------------------------------------------------


def _extract_via_ffmpeg(
    film: FilmRecord,
    stream_index: int,
) -> list[DialogueLine]:
    """Extract one text subtitle stream and parse the resulting SRT."""
    srt_path = film.asset_dir / "subs.srt"
    cmd = [
        "ffmpeg",
        "-y",
        "-i", str(film.path),
        "-map", f"0:{stream_index}",
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
    print(
        f"[dialogue] loading Whisper model ({config.models.whisper})",
        flush=True,
    )
    model = WhisperModel(config.models.whisper, device="auto", compute_type="default")
    options = _whisper_transcribe_options(film)
    language_hint = options["language"]
    if isinstance(language_hint, str):
        print(
            "[dialogue] using primary audio language hint: "
            f"{language_hint} (tag: {film.primary_audio_language_tag})",
            flush=True,
        )
    segments, info = model.transcribe(
        str(film.path),
        **options,
    )
    detected_language = getattr(info, "language", None)
    if (
        language_hint is None
        and isinstance(detected_language, str)
        and detected_language
    ):
        print(f"[dialogue] detected language: {detected_language}", flush=True)
    duration = _valid_duration(getattr(info, "duration", None))
    if duration is None:
        print("[dialogue] transcribing audio", flush=True)
    else:
        print("[dialogue] transcribing audio: 0%", flush=True)

    lines: list[DialogueLine] = []
    reported_progress = 0
    for seg in segments:
        text = seg.text.strip()
        if text:
            lines.append(DialogueLine(start=float(seg.start), end=float(seg.end), text=text))

        progress = _coarse_progress(getattr(seg, "end", None), duration)
        if progress is not None and progress > reported_progress:
            reported_progress = progress
            print(f"[dialogue] transcribing audio: {progress}%", flush=True)

    if duration is None:
        print("[dialogue] transcription complete", flush=True)
    else:
        print("[dialogue] transcribing audio: 100%", flush=True)

    rejection = _whisper_transcript_rejection(lines)
    if rejection is not None:
        print(
            f"[dialogue] rejected Whisper transcript: {rejection}; "
            "continuing without dialogue",
            flush=True,
        )
        return []
    return lines


def _whisper_transcript_rejection(
    lines: Sequence[DialogueLine],
) -> str | None:
    """Return a structural hallucination reason, or ``None`` when accepted."""
    normalized = [_normalize_whisper_text(line.text) for line in lines]

    previous = ""
    run_length = 0
    for text in normalized:
        run_length = run_length + 1 if text and text == previous else 1
        previous = text
        if (
            run_length >= _WHISPER_LOOP_REJECT_AT
            and len(text.split()) >= _WHISPER_LOOP_MIN_TOKENS
            and sum(character.isalpha() for character in text)
            >= _WHISPER_LOOP_MIN_LETTERS
        ):
            return (
                f"exact line repeated {run_length} consecutive times: "
                f"{text!r}"
            )

    occurrences = Counter(text for text in normalized if text)
    repeated_sparse_count = sum(
        1
        for line, text in zip(lines, normalized)
        if text
        and line.end - line.start >= _WHISPER_SPARSE_MIN_DURATION_S
        and len(text.split()) <= _WHISPER_SPARSE_MAX_TOKENS
        and occurrences[text] >= _WHISPER_SPARSE_MIN_GLOBAL_OCCURRENCES
    )
    if repeated_sparse_count >= _WHISPER_SPARSE_REJECT_AT:
        return (
            f"{repeated_sparse_count} repeated sparse segments lasted at least "
            f"{_WHISPER_SPARSE_MIN_DURATION_S:g}s"
        )
    return None


def _normalize_whisper_text(text: str) -> str:
    """Normalize punctuation, whitespace, width, and case for exact loops."""
    normalized = unicodedata.normalize("NFKC", text).casefold()
    return " ".join(
        "".join(character if character.isalnum() else " " for character in normalized)
        .split()
    )


def _valid_duration(value: object) -> float | None:
    """Return a positive finite duration reported by faster-whisper, if any."""
    if isinstance(value, bool) or not isinstance(value, Real):
        return None
    duration = float(value)
    return duration if duration > 0 and math.isfinite(duration) else None


def _coarse_progress(segment_end: object, duration: float | None) -> int | None:
    """Map a segment timestamp to a bounded 10% progress bucket."""
    if (
        duration is None
        or isinstance(segment_end, bool)
        or not isinstance(segment_end, Real)
    ):
        return None
    end = float(segment_end)
    if not math.isfinite(end):
        return None
    return min(90, max(0, int(100 * end / duration) // 10 * 10))


# ---------------------------------------------------------------------------
# SRT parsing
# ---------------------------------------------------------------------------

def _parse_srt(text: str) -> list[DialogueLine]:
    """Parse *text* (SRT format) into a list of :class:`DialogueLine`.

    - Strips SRT formatting tags (``<i>``, ``<b>``, ``<font …>``, etc.)
    - Decodes HTML entities (``&amp;``, ``&lt;``, etc.)
    - Joins multi-line subtitle text with a single space
    """
    return [
        DialogueLine(start=cue.start, end=cue.end, text=cue.text)
        for cue in parse_srt(text)
    ]


def _parse_external_srt(text: str) -> list[DialogueLine]:
    """Parse replaceable sidecar dialogue without promotional release cues."""
    return [
        DialogueLine(start=cue.start, end=cue.end, text=cue.text)
        for cue in parse_external_dialogue_srt(text)
    ]


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
