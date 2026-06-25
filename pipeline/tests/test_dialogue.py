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


def _make_film(tmp_path: Path, *, has_embedded_subs: bool = True):
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
