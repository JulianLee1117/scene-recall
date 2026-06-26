"""Tests for pipeline/ingest/annotate.py — written before implementation (TDD).

All Gemini API calls are mocked.  No real network calls are made.

Test coverage:
  - annotate_shot returns dict with required keys
  - searchable_text is a non-empty string
  - caption and mood are parsed correctly from the Gemini response
  - dialogue lines within the shot's time range are included in searchable_text
  - dialogue lines outside the shot's time range are excluded
  - the model name is taken from config.models.annotator (not hardcoded)
  - _parse_response handles the Mood: prefix correctly
"""
from __future__ import annotations

import base64
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

from pipeline.config import Config
from pipeline.ingest.dialogue import DialogueLine
from pipeline.ingest.shots import Shot


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


FAKE_RESPONSE_TEXT = (
    "A tense nighttime scene with two figures silhouetted against glowing city lights. "
    "The composition is tight, emphasising isolation in the urban environment.\n"
    "Mood: tense, noir, dramatic, nighttime"
)


def _make_jpeg(parent: Path, name: str = "frame.jpg") -> Path:
    """Write a tiny solid-colour JPEG and return its path."""
    img = Image.new("RGB", (64, 64), color=(80, 90, 100))
    p = parent / name
    img.save(p, format="JPEG")
    return p


def _make_shot(t_start: float = 10.0, t_end: float = 20.0) -> Shot:
    return Shot(
        shot_id="film_0001",
        t_start=t_start,
        t_end=t_end,
        parent_shot_id=None,
        keyframe_times=[12.5, 15.0, 17.5],
    )


def _make_mock_client(response_text: str = FAKE_RESPONSE_TEXT) -> MagicMock:
    """Return a mock genai.Client whose generate_content returns response_text."""
    mock_response = MagicMock()
    mock_response.text = response_text

    mock_models = MagicMock()
    mock_models.generate_content.return_value = mock_response

    mock_client = MagicMock()
    mock_client.models = mock_models

    return mock_client


# ---------------------------------------------------------------------------
# annotate_shot — basic contract
# ---------------------------------------------------------------------------


def test_annotate_shot_returns_required_keys(tmp_path: Path, config: Config) -> None:
    """annotate_shot returns a dict with 'caption', 'mood', and 'searchable_text'."""
    from pipeline.ingest.annotate import annotate_shot

    kf = [_make_jpeg(tmp_path, f"kf{i}.jpg") for i in range(3)]
    shot = _make_shot()
    mock_client = _make_mock_client()

    with patch("pipeline.ingest.annotate.genai.Client", return_value=mock_client):
        result = annotate_shot(shot, kf, [], config)

    assert isinstance(result, dict)
    assert "caption" in result
    assert "mood" in result
    assert "searchable_text" in result


def test_annotate_shot_searchable_text_is_nonempty(tmp_path: Path, config: Config) -> None:
    """searchable_text must be a non-empty string."""
    from pipeline.ingest.annotate import annotate_shot

    kf = [_make_jpeg(tmp_path, "kf0.jpg")]
    shot = _make_shot()
    mock_client = _make_mock_client()

    with patch("pipeline.ingest.annotate.genai.Client", return_value=mock_client):
        result = annotate_shot(shot, kf, [], config)

    assert isinstance(result["searchable_text"], str)
    assert len(result["searchable_text"]) > 0


def test_annotate_shot_caption_is_string(tmp_path: Path, config: Config) -> None:
    """caption must be a non-empty string."""
    from pipeline.ingest.annotate import annotate_shot

    kf = [_make_jpeg(tmp_path, "kf0.jpg")]
    shot = _make_shot()
    mock_client = _make_mock_client()

    with patch("pipeline.ingest.annotate.genai.Client", return_value=mock_client):
        result = annotate_shot(shot, kf, [], config)

    assert isinstance(result["caption"], str)
    assert len(result["caption"]) > 0


def test_annotate_shot_mood_is_list(tmp_path: Path, config: Config) -> None:
    """mood must be a list of strings."""
    from pipeline.ingest.annotate import annotate_shot

    kf = [_make_jpeg(tmp_path, "kf0.jpg")]
    shot = _make_shot()
    mock_client = _make_mock_client()

    with patch("pipeline.ingest.annotate.genai.Client", return_value=mock_client):
        result = annotate_shot(shot, kf, [], config)

    assert isinstance(result["mood"], list)
    assert all(isinstance(kw, str) for kw in result["mood"])


# ---------------------------------------------------------------------------
# annotate_shot — response parsing
# ---------------------------------------------------------------------------


def test_annotate_shot_parses_caption(tmp_path: Path, config: Config) -> None:
    """caption contains the paragraph before the Mood: line."""
    from pipeline.ingest.annotate import annotate_shot

    kf = [_make_jpeg(tmp_path, "kf0.jpg")]
    shot = _make_shot()
    mock_client = _make_mock_client()

    with patch("pipeline.ingest.annotate.genai.Client", return_value=mock_client):
        result = annotate_shot(shot, kf, [], config)

    # The caption should NOT include the Mood: line
    assert "Mood:" not in result["caption"]
    # Should contain something from the fake response paragraph
    assert "tense" in result["caption"].lower() or "city" in result["caption"].lower()


def test_annotate_shot_parses_mood_keywords(tmp_path: Path, config: Config) -> None:
    """mood is parsed from comma-separated keywords after 'Mood:'."""
    from pipeline.ingest.annotate import annotate_shot

    kf = [_make_jpeg(tmp_path, "kf0.jpg")]
    shot = _make_shot()
    mock_client = _make_mock_client()

    with patch("pipeline.ingest.annotate.genai.Client", return_value=mock_client):
        result = annotate_shot(shot, kf, [], config)

    assert "tense" in result["mood"]
    assert "noir" in result["mood"]
    assert "dramatic" in result["mood"]
    assert "nighttime" in result["mood"]


def test_annotate_shot_searchable_text_contains_caption(tmp_path: Path, config: Config) -> None:
    """searchable_text starts with the caption."""
    from pipeline.ingest.annotate import annotate_shot

    kf = [_make_jpeg(tmp_path, "kf0.jpg")]
    shot = _make_shot()
    mock_client = _make_mock_client()

    with patch("pipeline.ingest.annotate.genai.Client", return_value=mock_client):
        result = annotate_shot(shot, kf, [], config)

    assert result["searchable_text"].startswith(result["caption"])


# ---------------------------------------------------------------------------
# annotate_shot — dialogue filtering
# ---------------------------------------------------------------------------


def test_annotate_shot_dialogue_in_range_included(tmp_path: Path, config: Config) -> None:
    """Dialogue lines overlapping the shot's time range appear in searchable_text."""
    from pipeline.ingest.annotate import annotate_shot

    kf = [_make_jpeg(tmp_path, "kf0.jpg")]
    shot = _make_shot(t_start=10.0, t_end=20.0)

    # Line fully inside the shot
    line_inside = DialogueLine(start=12.0, end=15.0, text="Hello darkness my old friend")
    # Line overlapping the start
    line_overlap_start = DialogueLine(start=8.0, end=11.0, text="Overlap at start")
    # Line overlapping the end
    line_overlap_end = DialogueLine(start=19.0, end=22.0, text="Overlap at end")

    dialogue = [line_inside, line_overlap_start, line_overlap_end]
    mock_client = _make_mock_client()

    with patch("pipeline.ingest.annotate.genai.Client", return_value=mock_client):
        result = annotate_shot(shot, kf, dialogue, config)

    assert "Hello darkness my old friend" in result["searchable_text"]
    assert "Overlap at start" in result["searchable_text"]
    assert "Overlap at end" in result["searchable_text"]


def test_annotate_shot_dialogue_outside_range_excluded(tmp_path: Path, config: Config) -> None:
    """Dialogue lines entirely outside the shot's time range are excluded."""
    from pipeline.ingest.annotate import annotate_shot

    kf = [_make_jpeg(tmp_path, "kf0.jpg")]
    shot = _make_shot(t_start=10.0, t_end=20.0)

    # Line entirely before the shot
    line_before = DialogueLine(start=2.0, end=9.0, text="Before the shot")
    # Line entirely after the shot
    line_after = DialogueLine(start=21.0, end=25.0, text="After the shot")

    dialogue = [line_before, line_after]
    mock_client = _make_mock_client()

    with patch("pipeline.ingest.annotate.genai.Client", return_value=mock_client):
        result = annotate_shot(shot, kf, dialogue, config)

    assert "Before the shot" not in result["searchable_text"]
    assert "After the shot" not in result["searchable_text"]


def test_annotate_shot_searchable_text_with_dialogue(tmp_path: Path, config: Config) -> None:
    """searchable_text = caption + dialogue texts joined by spaces."""
    from pipeline.ingest.annotate import annotate_shot

    kf = [_make_jpeg(tmp_path, "kf0.jpg")]
    shot = _make_shot(t_start=10.0, t_end=20.0)
    line = DialogueLine(start=12.0, end=14.0, text="I am your father")

    mock_client = _make_mock_client()

    with patch("pipeline.ingest.annotate.genai.Client", return_value=mock_client):
        result = annotate_shot(shot, kf, [line], config)

    # searchable_text = f"{caption} {' '.join(dialogue_texts)}"
    expected = f"{result['caption']} I am your father"
    assert result["searchable_text"] == expected


# ---------------------------------------------------------------------------
# annotate_shot — uses config model name
# ---------------------------------------------------------------------------


def test_annotate_shot_uses_model_from_config(tmp_path: Path, config: Config) -> None:
    """generate_content is called with the model from config.models.annotator."""
    from pipeline.ingest.annotate import annotate_shot

    kf = [_make_jpeg(tmp_path, "kf0.jpg")]
    shot = _make_shot()
    mock_client = _make_mock_client()

    with patch("pipeline.ingest.annotate.genai.Client", return_value=mock_client):
        annotate_shot(shot, kf, [], config)

    call_kwargs = mock_client.models.generate_content.call_args
    assert call_kwargs.kwargs["model"] == config.models.annotator


# ---------------------------------------------------------------------------
# _parse_response — unit tests for the parsing helper
# ---------------------------------------------------------------------------


def test_parse_response_basic() -> None:
    """_parse_response extracts caption and mood from a well-formed response."""
    from pipeline.ingest.annotate import _parse_response

    text = "A dark and stormy shot.\nMood: dark, stormy, dramatic"
    caption, mood = _parse_response(text)

    assert caption == "A dark and stormy shot."
    assert mood == ["dark", "stormy", "dramatic"]


def test_parse_response_multiline_caption() -> None:
    """_parse_response handles a multi-line caption."""
    from pipeline.ingest.annotate import _parse_response

    text = "Line one of caption.\nLine two of caption.\nMood: sad, quiet"
    caption, mood = _parse_response(text)

    assert "Line one of caption." in caption
    assert "Line two of caption." in caption
    assert mood == ["sad", "quiet"]


def test_parse_response_no_mood_line() -> None:
    """_parse_response returns empty mood list when Mood: line is absent."""
    from pipeline.ingest.annotate import _parse_response

    text = "Just a paragraph with no mood line."
    caption, mood = _parse_response(text)

    assert caption == "Just a paragraph with no mood line."
    assert mood == []


def test_parse_response_mood_keywords_stripped() -> None:
    """_parse_response strips whitespace from each mood keyword."""
    from pipeline.ingest.annotate import _parse_response

    text = "Caption.\nMood:  happy ,  bright ,  light "
    _caption, mood = _parse_response(text)

    assert mood == ["happy", "bright", "light"]
