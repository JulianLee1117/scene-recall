"""Tests for pipeline/ingest/dialogue.py — written before implementation (TDD).

Primary path: ffmpeg extracts embedded subtitle stream → SRT → DialogueLine list.
Fallback path: faster-whisper transcription when no embedded subs.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pipeline.config import Config


# ---------------------------------------------------------------------------
# SRT sample data
# ---------------------------------------------------------------------------

SRT_TWO_LINES = """\
1
00:00:01,000 --> 00:00:03,500
Hello world

2
00:00:05,000 --> 00:00:07,000
Foo bar
baz

"""

SRT_WITH_TAGS = """\
1
00:00:01,000 --> 00:00:02,000
<i>Italic text</i>

2
00:00:03,000 --> 00:00:04,000
<b>Bold</b> and <font color="red">red</font>

"""

SRT_WITH_ENTITIES = """\
1
00:00:01,000 --> 00:00:02,000
Hello &amp; world

2
00:00:03,000 --> 00:00:04,000
&lt;not a tag&gt;

"""


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _seconds_to_srt(s: float) -> str:
    """Convert float seconds to SRT timestamp string HH:MM:SS,mmm."""
    h = int(s // 3600)
    m = int((s % 3600) // 60)
    sec = int(s % 60)
    ms = int(round((s - int(s)) * 1000))
    return f"{h:02d}:{m:02d}:{sec:02d},{ms:03d}"


def _usable_external_srt(prefix: str = "Ordinary dialogue") -> str:
    blocks = []
    for index in range(1, 9):
        blocks.append(
            f"{index}\n"
            f"{_seconds_to_srt(index * 2 - 1)} --> {_seconds_to_srt(index * 2)}\n"
            f"{prefix} line {index} has several spoken words.\n"
        )
    return "\n".join(blocks) + "\n"


def _promo_only_srt() -> str:
    promotions = (
        "Official YIFY movies site: YTS.MX",
        "Downloaded from www.OpenSubtitles.org",
        "Support us and become a VIP member",
        "Remove all ads at https://example.invalid",
    )
    return "\n".join(
        f"{index}\n"
        f"{_seconds_to_srt(index * 2 - 1)} --> {_seconds_to_srt(index * 2)}\n"
        f"{text}\n"
        for index, text in enumerate(promotions, start=1)
    ) + "\n"


def _repeated_srt(text: str, count: int = 16) -> str:
    return "\n".join(
        f"{index}\n"
        f"{_seconds_to_srt(index * 2 - 1)} --> {_seconds_to_srt(index * 2)}\n"
        f"{text}\n"
        for index in range(1, count + 1)
    ) + "\n"


def _make_film(
    tmp_path: Path,
    *,
    has_embedded_subs: bool = True,
    text_subtitle_stream_index: int | None = 0,
    primary_audio_language_tag: str | None = None,
):
    """Return a minimal FilmRecord pointing into tmp_path."""
    from pipeline.ingest.probe import FilmRecord

    asset_dir = tmp_path / "assets" / "abc123"
    asset_dir.mkdir(parents=True, exist_ok=True)
    return FilmRecord(
        film_id="abc123",
        path=tmp_path / "film.mkv",
        asset_dir=asset_dir,
        duration=30.0,
        fps=24.0,
        has_embedded_subs=has_embedded_subs,
        title="Test Film",
        text_subtitle_stream_index=(
            text_subtitle_stream_index if has_embedded_subs else None
        ),
        primary_audio_language_tag=primary_audio_language_tag,
    )


def _fake_ffmpeg_writer(srt_content: str, asset_dir: Path):
    """Return a side_effect callable that writes *srt_content* to asset_dir/subs.srt."""

    def _run(cmd, **kwargs):
        (asset_dir / "subs.srt").write_text(srt_content, encoding="utf-8")
        m = MagicMock()
        m.returncode = 0
        return m

    return _run


# ---------------------------------------------------------------------------
# Unit tests: DialogueLine dataclass
# ---------------------------------------------------------------------------


def test_dialogue_line_has_correct_fields() -> None:
    """DialogueLine is a dataclass with start, end, text fields."""
    from pipeline.ingest.dialogue import DialogueLine

    line = DialogueLine(start=1.0, end=2.5, text="Hello world")
    assert line.start == 1.0
    assert line.end == 2.5
    assert line.text == "Hello world"


def test_dialogue_line_start_end_are_floats() -> None:
    """DialogueLine.start and .end are float64 (float in Python)."""
    from pipeline.ingest.dialogue import DialogueLine

    line = DialogueLine(start=0.0, end=99.999, text="x")
    assert isinstance(line.start, float)
    assert isinstance(line.end, float)


# ---------------------------------------------------------------------------
# Unit tests: _parse_srt_timestamp
# ---------------------------------------------------------------------------


def test_parse_srt_timestamp_zero() -> None:
    """00:00:00,000 → 0.0 seconds."""
    from pipeline.ingest.dialogue import _parse_srt_timestamp

    assert _parse_srt_timestamp("00:00:00,000") == pytest.approx(0.0)


def test_parse_srt_timestamp_one_second() -> None:
    """00:00:01,000 → 1.0 seconds."""
    from pipeline.ingest.dialogue import _parse_srt_timestamp

    assert _parse_srt_timestamp("00:00:01,000") == pytest.approx(1.0)


def test_parse_srt_timestamp_fractional() -> None:
    """00:00:01,500 → 1.5 seconds."""
    from pipeline.ingest.dialogue import _parse_srt_timestamp

    assert _parse_srt_timestamp("00:00:01,500") == pytest.approx(1.5)


def test_parse_srt_timestamp_full() -> None:
    """01:02:03,456 → 3723.456 seconds."""
    from pipeline.ingest.dialogue import _parse_srt_timestamp

    assert _parse_srt_timestamp("01:02:03,456") == pytest.approx(3723.456)


# ---------------------------------------------------------------------------
# Unit tests: _parse_srt
# ---------------------------------------------------------------------------


def test_parse_srt_count() -> None:
    """_parse_srt with two-entry SRT returns exactly two DialogueLines."""
    from pipeline.ingest.dialogue import _parse_srt

    lines = _parse_srt(SRT_TWO_LINES)
    assert len(lines) == 2


def test_parse_srt_timestamps() -> None:
    """_parse_srt extracts correct start/end timestamps."""
    from pipeline.ingest.dialogue import _parse_srt

    lines = _parse_srt(SRT_TWO_LINES)
    assert lines[0].start == pytest.approx(1.0)
    assert lines[0].end == pytest.approx(3.5)
    assert lines[1].start == pytest.approx(5.0)
    assert lines[1].end == pytest.approx(7.0)


def test_parse_srt_single_line_text() -> None:
    """Single-line subtitle text is stored verbatim."""
    from pipeline.ingest.dialogue import _parse_srt

    lines = _parse_srt(SRT_TWO_LINES)
    assert lines[0].text == "Hello world"


def test_parse_srt_multiline_text_joined() -> None:
    """Multi-line subtitle text is joined with a space."""
    from pipeline.ingest.dialogue import _parse_srt

    lines = _parse_srt(SRT_TWO_LINES)
    assert lines[1].text == "Foo bar baz"


def test_parse_srt_strips_italic_tags() -> None:
    """<i>…</i> formatting tags are stripped from subtitle text."""
    from pipeline.ingest.dialogue import _parse_srt

    lines = _parse_srt(SRT_WITH_TAGS)
    assert lines[0].text == "Italic text"


def test_parse_srt_strips_mixed_tags() -> None:
    """<b> and <font> tags are stripped, leaving only the text."""
    from pipeline.ingest.dialogue import _parse_srt

    lines = _parse_srt(SRT_WITH_TAGS)
    assert lines[1].text == "Bold and red"


def test_parse_srt_decodes_html_entities() -> None:
    """&amp; is decoded to & in subtitle text."""
    from pipeline.ingest.dialogue import _parse_srt

    lines = _parse_srt(SRT_WITH_ENTITIES)
    assert lines[0].text == "Hello & world"


def test_parse_srt_decodes_lt_gt_entities() -> None:
    """&lt; and &gt; are decoded to < and > respectively."""
    from pipeline.ingest.dialogue import _parse_srt

    lines = _parse_srt(SRT_WITH_ENTITIES)
    assert lines[1].text == "<not a tag>"


# ---------------------------------------------------------------------------
# Integration tests: extract_dialogue — primary path (embedded subs)
# ---------------------------------------------------------------------------


def test_extract_dialogue_returns_list(config: Config, tmp_path: Path) -> None:
    """extract_dialogue returns a list when film has embedded subs."""
    from pipeline.ingest.dialogue import extract_dialogue

    film = _make_film(tmp_path, has_embedded_subs=True)

    with patch("subprocess.run", side_effect=_fake_ffmpeg_writer(SRT_TWO_LINES, film.asset_dir)):
        result = extract_dialogue(film, config)

    assert isinstance(result, list)


def test_extract_dialogue_returns_dialogue_line_instances(config: Config, tmp_path: Path) -> None:
    """Every element in the result is a DialogueLine."""
    from pipeline.ingest.dialogue import extract_dialogue, DialogueLine

    film = _make_film(tmp_path, has_embedded_subs=True)

    with patch("subprocess.run", side_effect=_fake_ffmpeg_writer(SRT_TWO_LINES, film.asset_dir)):
        result = extract_dialogue(film, config)

    assert all(isinstance(line, DialogueLine) for line in result)


def test_extract_dialogue_count_within_tolerance(config: Config, tmp_path: Path) -> None:
    """extract_dialogue on a clip with known subtitle count returns a list within ±2 lines."""
    from pipeline.ingest.dialogue import extract_dialogue

    EXPECTED = 5

    srt_entries = []
    for i in range(1, EXPECTED + 1):
        start = _seconds_to_srt(float(i * 2 - 1))
        end = _seconds_to_srt(float(i * 2))
        srt_entries.append(f"{i}\n{start} --> {end}\nLine {i}\n")
    srt_content = "\n".join(srt_entries) + "\n"

    film = _make_film(tmp_path, has_embedded_subs=True)

    with patch("subprocess.run", side_effect=_fake_ffmpeg_writer(srt_content, film.asset_dir)):
        result = extract_dialogue(film, config)

    assert abs(len(result) - EXPECTED) <= 2


def test_extract_dialogue_saves_dialogue_json(config: Config, tmp_path: Path) -> None:
    """extract_dialogue writes dialogue.json to film.asset_dir."""
    from pipeline.ingest.dialogue import extract_dialogue

    film = _make_film(tmp_path, has_embedded_subs=True)

    with patch("subprocess.run", side_effect=_fake_ffmpeg_writer(SRT_TWO_LINES, film.asset_dir)):
        extract_dialogue(film, config)

    json_path = film.asset_dir / "dialogue.json"
    assert json_path.exists(), "dialogue.json was not created"


def test_external_sidecar_precedes_embedded_subtitles_and_is_cache_scoped(
    config: Config,
    tmp_path: Path,
) -> None:
    from pipeline.ingest.dialogue import (
        dialogue_cache_is_current,
        extract_dialogue,
    )

    film = _make_film(tmp_path, has_embedded_subs=True)
    sidecar = film.path.with_name(film.path.stem + ".en.srt")
    sidecar.write_text(_usable_external_srt(), encoding="utf-8")

    with (
        patch("pipeline.ingest.dialogue.subprocess.run") as ffmpeg,
        patch("pipeline.ingest.dialogue.WhisperModel") as whisper,
    ):
        lines = extract_dialogue(film, config)

    assert len(lines) == 8
    assert lines[0].text == "Ordinary dialogue line 1 has several spoken words."
    ffmpeg.assert_not_called()
    whisper.assert_not_called()
    assert dialogue_cache_is_current(film, config)

    sidecar.write_text(_usable_external_srt("Changed dialogue"), encoding="utf-8")
    assert not dialogue_cache_is_current(film, config)


def test_external_sidecar_filters_promos_from_derived_dialogue_only(
    config: Config,
    tmp_path: Path,
) -> None:
    from pipeline.ingest.dialogue import (
        dialogue_cache_is_current,
        extract_dialogue,
    )

    film = _make_film(tmp_path, has_embedded_subs=True)
    sidecar = film.path.with_name(film.path.stem + ".en.srt")
    raw_sidecar = _promo_only_srt() + _usable_external_srt()
    sidecar.write_text(raw_sidecar, encoding="utf-8")

    with (
        patch("pipeline.ingest.dialogue.subprocess.run") as ffmpeg,
        patch("pipeline.ingest.dialogue.WhisperModel") as whisper,
    ):
        lines = extract_dialogue(film, config)

    assert len(lines) == 8
    assert all("Ordinary dialogue" in line.text for line in lines)
    assert sidecar.read_text(encoding="utf-8") == raw_sidecar
    assert len(
        json.loads(
            (film.asset_dir / "dialogue.json").read_text(encoding="utf-8")
        )
    ) == 8
    ffmpeg.assert_not_called()
    whisper.assert_not_called()

    manifest_path = film.asset_dir / "dialogue.manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["derivation_profile"] == {
        "profile_version": 1,
        "exclude_promotional_cues": True,
    }
    manifest.pop("derivation_profile")
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    assert not dialogue_cache_is_current(film, config)


def test_trivial_promo_sidecar_is_ignored_and_invalidates_cached_dialogue(
    config: Config,
    tmp_path: Path,
) -> None:
    from pipeline.ingest.dialogue import (
        _file_sha256,
        dialogue_cache_is_current,
        extract_dialogue,
    )
    from pipeline.ingest.subtitles import external_srt_is_usable

    film = _make_film(tmp_path, has_embedded_subs=False)
    sidecar = film.path.with_name(film.path.stem + ".en.srt")
    sidecar.write_text(_promo_only_srt(), encoding="utf-8")
    assert not external_srt_is_usable(sidecar)

    (film.asset_dir / "dialogue.json").write_text(
        json.dumps([{"start": 0.0, "end": 1.0, "text": "Stale promo"}]),
        encoding="utf-8",
    )
    (film.asset_dir / "dialogue.manifest.json").write_text(
        json.dumps(
            {
                "contract_version": 2,
                "kind": "sidecar_srt",
                "filename": sidecar.name,
                "sha256": _file_sha256(sidecar),
            }
        ),
        encoding="utf-8",
    )
    assert not dialogue_cache_is_current(film, config)

    segment = MagicMock(start=0.0, end=2.0, text=" Real speech")
    info = MagicMock()
    info.duration = None
    fake_model = MagicMock()
    fake_model.transcribe.return_value = (iter([segment]), info)
    with patch("pipeline.ingest.dialogue.WhisperModel", return_value=fake_model):
        lines = extract_dialogue(film, config)

    assert [line.text for line in lines] == ["Real speech"]
    assert sidecar.exists(), "rejected raw evidence must not be deleted"
    manifest = json.loads(
        (film.asset_dir / "dialogue.manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["kind"] == "whisper"


def test_dialogue_json_is_valid_list_of_dicts(config: Config, tmp_path: Path) -> None:
    """dialogue.json contains a JSON array of objects with start/end/text keys."""
    from pipeline.ingest.dialogue import extract_dialogue

    film = _make_film(tmp_path, has_embedded_subs=True)

    with patch("subprocess.run", side_effect=_fake_ffmpeg_writer(SRT_TWO_LINES, film.asset_dir)):
        extract_dialogue(film, config)

    data = json.loads((film.asset_dir / "dialogue.json").read_text(encoding="utf-8"))
    assert isinstance(data, list)
    assert len(data) == 2
    for entry in data:
        assert "start" in entry
        assert "end" in entry
        assert "text" in entry


def test_dialogue_json_timestamps_match(config: Config, tmp_path: Path) -> None:
    """dialogue.json timestamps match the parsed DialogueLine values."""
    from pipeline.ingest.dialogue import extract_dialogue

    film = _make_film(tmp_path, has_embedded_subs=True)

    with patch("subprocess.run", side_effect=_fake_ffmpeg_writer(SRT_TWO_LINES, film.asset_dir)):
        result = extract_dialogue(film, config)

    data = json.loads((film.asset_dir / "dialogue.json").read_text(encoding="utf-8"))
    assert data[0]["start"] == pytest.approx(result[0].start)
    assert data[0]["end"] == pytest.approx(result[0].end)
    assert data[0]["text"] == result[0].text


# ---------------------------------------------------------------------------
# Integration tests: extract_dialogue — fallback path (whisper)
# ---------------------------------------------------------------------------


def test_extract_dialogue_fallback_returns_list(config: Config, tmp_path: Path) -> None:
    """extract_dialogue with has_embedded_subs=False invokes whisper and returns a list."""
    from pipeline.ingest.dialogue import extract_dialogue, DialogueLine

    film = _make_film(tmp_path, has_embedded_subs=False)

    fake_segment = MagicMock()
    fake_segment.start = 0.0
    fake_segment.end = 2.0
    fake_segment.text = " Hello whisper"

    fake_model = MagicMock()
    fake_model.transcribe.return_value = ([fake_segment], MagicMock())

    with patch("pipeline.ingest.dialogue.WhisperModel", return_value=fake_model):
        result = extract_dialogue(film, config)

    assert isinstance(result, list)
    assert len(result) == 1
    assert isinstance(result[0], DialogueLine)
    assert result[0].text == "Hello whisper"


def test_extract_dialogue_fallback_uses_config_model(config: Config, tmp_path: Path) -> None:
    """extract_dialogue fallback instantiates WhisperModel with the configured model name."""
    from pipeline.ingest.dialogue import extract_dialogue

    film = _make_film(tmp_path, has_embedded_subs=False)

    fake_segment = MagicMock()
    fake_segment.start = 0.0
    fake_segment.end = 1.0
    fake_segment.text = " Word"

    fake_model = MagicMock()
    fake_model.transcribe.return_value = ([fake_segment], MagicMock())

    with patch("pipeline.ingest.dialogue.WhisperModel") as MockWhisper:
        MockWhisper.return_value = fake_model
        extract_dialogue(film, config)

    # First positional arg to WhisperModel(...) must be config.models.whisper
    called_model = MockWhisper.call_args[0][0]
    assert called_model == config.models.whisper


def test_whisper_transcription_profile_invalidates_legacy_cache(
    config: Config,
    tmp_path: Path,
) -> None:
    from faster_whisper import __version__ as faster_whisper_version

    from pipeline.ingest.dialogue import _dialogue_source, dialogue_cache_is_current

    film = _make_film(tmp_path, has_embedded_subs=False)
    (film.asset_dir / "dialogue.json").write_text("[]", encoding="utf-8")
    legacy_source = {
        "contract_version": 2,
        "kind": "whisper",
        "film_id": film.film_id,
        "model": config.models.whisper,
        "transcription_profile": {
            "profile_version": 1,
            "engine": "faster-whisper",
            "engine_version": faster_whisper_version,
            "options": {
                "language": None,
                "task": "transcribe",
                "word_timestamps": False,
                "vad_filter": True,
                "condition_on_previous_text": False,
                "language_detection_threshold": 1.0,
                "language_detection_segments": 5,
            },
        },
    }
    (film.asset_dir / "dialogue.manifest.json").write_text(
        json.dumps(legacy_source),
        encoding="utf-8",
    )

    assert not dialogue_cache_is_current(film, config)
    current_source = _dialogue_source(film, config)
    assert current_source["transcription_profile"] == {
        "profile_version": 2,
        "engine": "faster-whisper",
        "engine_version": faster_whisper_version,
        "options": {
            "language": None,
            "task": "transcribe",
            "word_timestamps": False,
            "vad_filter": True,
            "condition_on_previous_text": False,
            "language_detection_threshold": 1.0,
            "language_detection_segments": 5,
        },
        "quality_gate": {
            "gate_version": 1,
            "consecutive_exact_repeat": {
                "reject_at": 16,
                "minimum_tokens": 3,
                "minimum_letters": 8,
            },
            "repeated_sparse_long_segment": {
                "reject_at": 20,
                "minimum_duration_seconds": 25.0,
                "maximum_tokens": 8,
                "minimum_global_occurrences": 5,
            },
        },
    }

    (film.asset_dir / "dialogue.manifest.json").write_text(
        json.dumps(current_source),
        encoding="utf-8",
    )
    assert dialogue_cache_is_current(film, config)


def test_trusted_english_audio_tag_invalidates_auto_detect_cache(
    config: Config,
    tmp_path: Path,
) -> None:
    from pipeline.ingest.dialogue import _dialogue_source, dialogue_cache_is_current

    film = _make_film(tmp_path, has_embedded_subs=False)
    (film.asset_dir / "dialogue.json").write_text("[]", encoding="utf-8")
    auto_detect_source = _dialogue_source(film, config)
    (film.asset_dir / "dialogue.manifest.json").write_text(
        json.dumps(auto_detect_source),
        encoding="utf-8",
    )
    assert dialogue_cache_is_current(film, config)

    film.primary_audio_language_tag = "eng"
    assert not dialogue_cache_is_current(film, config)
    hinted_source = _dialogue_source(film, config)
    profile = hinted_source["transcription_profile"]
    assert profile["options"]["language"] == "en"
    assert profile["language_hint"] == {
        "source": "primary_audio_stream_tag",
        "tag": "eng",
    }


def test_whisper_uses_trusted_english_primary_audio_tag(
    config: Config,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from pipeline.ingest.dialogue import extract_dialogue

    film = _make_film(
        tmp_path,
        has_embedded_subs=False,
        primary_audio_language_tag="ENG",
    )
    fake_model = MagicMock()
    fake_model.transcribe.return_value = ([], MagicMock(language="en"))

    with patch("pipeline.ingest.dialogue.WhisperModel", return_value=fake_model):
        extract_dialogue(film, config)

    assert fake_model.transcribe.call_args.kwargs["language"] == "en"
    output = capsys.readouterr().out
    assert (
        "[dialogue] using primary audio language hint: en (tag: ENG)"
        in output
    )
    assert "[dialogue] detected language:" not in output
    manifest = json.loads(
        (film.asset_dir / "dialogue.manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["transcription_profile"]["language_hint"] == {
        "source": "primary_audio_stream_tag",
        "tag": "ENG",
    }


@pytest.mark.parametrize("language_tag", [None, "", "und", "deu", "kor"])
def test_whisper_keeps_auto_detection_for_untrusted_audio_tags(
    config: Config,
    tmp_path: Path,
    language_tag: str | None,
) -> None:
    from pipeline.ingest.dialogue import extract_dialogue

    film = _make_film(
        tmp_path,
        has_embedded_subs=False,
        primary_audio_language_tag=language_tag,
    )
    fake_model = MagicMock()
    fake_model.transcribe.return_value = ([], MagicMock())

    with patch("pipeline.ingest.dialogue.WhisperModel", return_value=fake_model):
        extract_dialogue(film, config)

    assert fake_model.transcribe.call_args.kwargs["language"] is None
    manifest = json.loads(
        (film.asset_dir / "dialogue.manifest.json").read_text(encoding="utf-8")
    )
    assert "language_hint" not in manifest["transcription_profile"]


def test_whisper_gate_rejects_exact_repeat_at_normalized_threshold() -> None:
    from pipeline.ingest.dialogue import (
        DialogueLine,
        _whisper_transcript_rejection,
    )

    variants = ("I LOST MY CAR!!!", "\uff29 lost\tmy\u2014car")
    lines = [
        DialogueLine(
            start=float(index),
            end=float(index + 1),
            text=variants[index % len(variants)],
        )
        for index in range(16)
    ]

    assert _whisper_transcript_rejection(lines[:15]) is None
    assert _whisper_transcript_rejection(lines) == (
        "exact line repeated 16 consecutive times: 'i lost my car'"
    )


def test_whisper_gate_rejects_repeated_sparse_segments_at_threshold() -> None:
    from pipeline.ingest.dialogue import (
        DialogueLine,
        _whisper_transcript_rejection,
    )

    lines = [
        DialogueLine(
            start=float(index * 30),
            end=float(index * 30 + 25),
            text="Thank you!",
        )
        for index in range(20)
    ]

    assert _whisper_transcript_rejection(lines[:19]) is None
    assert _whisper_transcript_rejection(lines) == (
        "20 repeated sparse segments lasted at least 25s"
    )


def test_rejected_whisper_transcript_is_logged_and_saved_empty(
    config: Config,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from pipeline.ingest.dialogue import extract_dialogue

    film = _make_film(tmp_path, has_embedded_subs=False)
    segments = [
        MagicMock(
            start=float(index),
            end=float(index + 1),
            text=" I lost my car!",
        )
        for index in range(16)
    ]
    info = MagicMock(duration=16.0, language="en")
    fake_model = MagicMock()
    fake_model.transcribe.return_value = (iter(segments), info)

    with patch("pipeline.ingest.dialogue.WhisperModel", return_value=fake_model):
        result = extract_dialogue(film, config)

    assert result == []
    assert json.loads(
        (film.asset_dir / "dialogue.json").read_text(encoding="utf-8")
    ) == []
    assert (
        "[dialogue] rejected Whisper transcript: exact line repeated "
        "16 consecutive times: 'i lost my car'; continuing without dialogue"
    ) in capsys.readouterr().out


@pytest.mark.parametrize("subtitle_source", ["external", "embedded"])
def test_whisper_gate_does_not_apply_to_subtitle_sources(
    config: Config,
    tmp_path: Path,
    subtitle_source: str,
) -> None:
    from pipeline.ingest.dialogue import extract_dialogue

    film = _make_film(
        tmp_path,
        has_embedded_subs=subtitle_source == "embedded",
    )
    repeated_subtitles = _repeated_srt("The same actual subtitle words")
    if subtitle_source == "external":
        film.path.with_name(film.path.stem + ".en.srt").write_text(
            repeated_subtitles,
            encoding="utf-8",
        )
        ffmpeg_effect = None
    else:
        ffmpeg_effect = _fake_ffmpeg_writer(repeated_subtitles, film.asset_dir)

    with (
        patch("pipeline.ingest.dialogue.subprocess.run", side_effect=ffmpeg_effect),
        patch("pipeline.ingest.dialogue.WhisperModel") as whisper,
    ):
        result = extract_dialogue(film, config)

    assert len(result) == 16
    assert all(line.text == "The same actual subtitle words" for line in result)
    whisper.assert_not_called()


def test_extract_dialogue_fallback_saves_dialogue_json(config: Config, tmp_path: Path) -> None:
    """extract_dialogue fallback also saves dialogue.json."""
    from pipeline.ingest.dialogue import extract_dialogue

    film = _make_film(tmp_path, has_embedded_subs=False)

    fake_segment = MagicMock()
    fake_segment.start = 0.0
    fake_segment.end = 2.0
    fake_segment.text = " Test"

    fake_model = MagicMock()
    fake_model.transcribe.return_value = ([fake_segment], MagicMock())

    with patch("pipeline.ingest.dialogue.WhisperModel", return_value=fake_model):
        extract_dialogue(film, config)

    assert (film.asset_dir / "dialogue.json").exists()


def test_extract_dialogue_reports_bounded_whisper_progress(
    config: Config,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The ingest log advances while faster-whisper's lazy iterator is consumed."""
    from pipeline.ingest.dialogue import extract_dialogue

    film = _make_film(tmp_path, has_embedded_subs=False)
    segments = [
        MagicMock(start=0.0, end=5.0, text=" first"),
        MagicMock(start=5.0, end=12.0, text=" second"),
        MagicMock(start=12.0, end=27.0, text=" third"),
        MagicMock(start=27.0, end=98.0, text=" fourth"),
    ]
    info = MagicMock()
    info.duration = 100.0
    info.language = "ko"
    fake_model = MagicMock()
    fake_model.transcribe.return_value = (iter(segments), info)

    with patch("pipeline.ingest.dialogue.WhisperModel", return_value=fake_model):
        extract_dialogue(film, config)

    progress = capsys.readouterr().out.splitlines()
    assert progress == [
        f"[dialogue] loading Whisper model ({config.models.whisper})",
        "[dialogue] detected language: ko",
        "[dialogue] transcribing audio: 0%",
        "[dialogue] transcribing audio: 10%",
        "[dialogue] transcribing audio: 20%",
        "[dialogue] transcribing audio: 90%",
        "[dialogue] transcribing audio: 100%",
    ]


@pytest.mark.parametrize("reported_duration", [None, 0.0, -1.0, float("nan")])
def test_extract_dialogue_handles_invalid_whisper_duration(
    config: Config,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    reported_duration: float | None,
) -> None:
    """Missing or unusable duration metadata falls back to stage-level progress."""
    from pipeline.ingest.dialogue import extract_dialogue

    film = _make_film(tmp_path, has_embedded_subs=False)
    segment = MagicMock(start=0.0, end=2.0, text=" line")
    info = MagicMock()
    info.duration = reported_duration
    fake_model = MagicMock()
    fake_model.transcribe.return_value = (iter([segment]), info)

    with patch("pipeline.ingest.dialogue.WhisperModel", return_value=fake_model):
        extract_dialogue(film, config)

    progress = capsys.readouterr().out.splitlines()
    assert progress[-2:] == [
        "[dialogue] transcribing audio",
        "[dialogue] transcription complete",
    ]


def test_extract_dialogue_maps_selected_text_subtitle_stream(
    config: Config,
    tmp_path: Path,
) -> None:
    """Extraction maps the probed text stream, not the first subtitle stream."""
    from pipeline.ingest.dialogue import extract_dialogue

    film = _make_film(tmp_path, text_subtitle_stream_index=3)
    with patch(
        "subprocess.run",
        side_effect=_fake_ffmpeg_writer(SRT_TWO_LINES, film.asset_dir),
    ) as run:
        extract_dialogue(film, config)

    command = run.call_args.args[0]
    assert command[command.index("-map") + 1] == "0:3"


def test_extract_dialogue_uses_whisper_for_bitmap_only_subtitles(
    config: Config,
    tmp_path: Path,
) -> None:
    """PGS-only films use audio transcription rather than invalid SRT conversion."""
    from pipeline.ingest.dialogue import extract_dialogue

    film = _make_film(tmp_path, text_subtitle_stream_index=None)
    fake_model = MagicMock()
    fake_model.transcribe.return_value = ([], MagicMock())

    with (
        patch("pipeline.ingest.dialogue.WhisperModel", return_value=fake_model),
        patch("subprocess.run") as run,
    ):
        extract_dialogue(film, config)

    run.assert_not_called()
    fake_model.transcribe.assert_called_once_with(
        str(film.path),
        language=None,
        task="transcribe",
        word_timestamps=False,
        vad_filter=True,
        condition_on_previous_text=False,
        language_detection_threshold=1.0,
        language_detection_segments=5,
    )
