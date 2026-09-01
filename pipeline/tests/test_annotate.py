"""Tests for pipeline/ingest/annotate.py.

All provider API calls are mocked. No real network calls are made.

Test coverage:
  - annotate_shot returns dict with caption, mood, facets, searchable_text
  - both providers return the same structured contract
  - facet values outside the vocabulary degrade to "unknown", never abort
  - dialogue lines within the shot's time range are included in searchable_text
  - dialogue lines outside the shot's time range are excluded
  - the model name is taken from config.models.annotator (not hardcoded)
  - the durable cache reuses hosted output and invalidates on input changes
"""
from __future__ import annotations

import base64
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
import openai
import pytest
from PIL import Image

from pipeline.config import Config
from pipeline.ingest.dialogue import DialogueLine
from pipeline.ingest.shots import Shot


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


FAKE_ANNOTATION: dict = {
    "caption": (
        "A tense nighttime scene with two figures silhouetted against glowing "
        "city lights. The composition is tight, emphasising isolation in the "
        "urban environment."
    ),
    "mood": ["tense", "noir", "dramatic", "nighttime"],
    "framing": "wide",
    "setting": "exterior",
    "time_of_day": "night",
    "people_count": 2,
    "energy": "calm",
    "camera_motion": "unknown",
    "palette": ["neon blue", "black"],
    "subjects": ["two silhouetted figures", "city skyline"],
    "on_screen_text": "",
}

FAKE_RESPONSE_TEXT = json.dumps(FAKE_ANNOTATION)


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
    for facet in (
        "framing",
        "setting",
        "time_of_day",
        "people_count",
        "energy",
        "camera_motion",
        "palette",
        "subjects",
        "on_screen_text",
    ):
        assert facet in result


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
# Durable annotation cache
# ---------------------------------------------------------------------------


def test_annotation_cache_reuses_hosted_response_and_rebuilds_dialogue(
    tmp_path: Path,
    config: Config,
) -> None:
    """A retry does not repay for a shot, while current dialogue stays fresh."""
    from pipeline.ingest.annotate import annotate_shot

    shot = _make_shot()
    keyframes = [_make_jpeg(tmp_path, "kf0.jpg")]
    cache_dir = tmp_path / "annotations"
    first_dialogue = [
        DialogueLine(start=12.0, end=13.0, text="First transcript.")
    ]
    retry_dialogue = [
        DialogueLine(start=12.0, end=13.0, text="Corrected transcript.")
    ]
    mock_client = _make_mock_client()

    with patch("pipeline.ingest.annotate.genai.Client", return_value=mock_client):
        first = annotate_shot(
            shot,
            keyframes,
            first_dialogue,
            config,
            cache_dir=cache_dir,
        )
        retry = annotate_shot(
            shot,
            keyframes,
            retry_dialogue,
            config,
            cache_dir=cache_dir,
        )

    assert mock_client.models.generate_content.call_count == 1
    assert first["caption"] == retry["caption"]
    assert "Corrected transcript." in retry["searchable_text"]
    assert "First transcript." not in retry["searchable_text"]

    cache_paths = list(cache_dir.glob(f"*/{shot.shot_id}.json"))
    assert len(cache_paths) == 1
    cache_path = cache_paths[0]
    cache_text = cache_path.read_text(encoding="utf-8")
    assert "Corrected transcript." not in cache_text
    assert "First transcript." not in cache_text


def test_annotation_cache_invalidates_when_model_changes(
    tmp_path: Path,
    config: Config,
) -> None:
    """Changing a hosted annotation input produces a deliberate cache miss."""
    from pipeline.ingest.annotate import annotate_shot

    shot = _make_shot()
    keyframes = [_make_jpeg(tmp_path)]
    cache_dir = tmp_path / "annotations"
    mock_client = _make_mock_client()

    with patch("pipeline.ingest.annotate.genai.Client", return_value=mock_client):
        annotate_shot(shot, keyframes, [], config, cache_dir=cache_dir)
        config.models.annotator = "gemini-other-model"
        annotate_shot(shot, keyframes, [], config, cache_dir=cache_dir)

    assert mock_client.models.generate_content.call_count == 2
    cache_paths = list(cache_dir.glob(f"*/{shot.shot_id}.json"))
    assert len(cache_paths) == 2
    assert len({path.parent.name for path in cache_paths}) == 2


def test_failed_annotation_does_not_create_resume_cache(
    tmp_path: Path,
    config: Config,
) -> None:
    """Only a fully validated provider response is safe to resume from."""
    from pipeline.ingest.annotate import AnnotationError, annotate_shot

    shot = _make_shot()
    keyframes = [_make_jpeg(tmp_path)]
    cache_dir = tmp_path / "annotations"
    mock_client = _make_mock_client(response_text="")

    with (
        patch("pipeline.ingest.annotate.genai.Client", return_value=mock_client),
        pytest.raises(AnnotationError, match="empty annotation"),
    ):
        annotate_shot(shot, keyframes, [], config, cache_dir=cache_dir)

    assert not (cache_dir / f"{shot.shot_id}.json").exists()


def test_corrupt_cache_refuses_to_repeat_hosted_request(
    tmp_path: Path,
    config: Config,
) -> None:
    """A damaged cache must be resolved explicitly rather than repaid blindly."""
    from pipeline.ingest.annotate import AnnotationError, annotate_shot

    shot = _make_shot()
    keyframes = [_make_jpeg(tmp_path)]
    cache_dir = tmp_path / "annotations"
    cache_dir.mkdir()
    (cache_dir / f"{shot.shot_id}.json").write_text("{broken", encoding="utf-8")
    mock_client = _make_mock_client()

    with (
        patch("pipeline.ingest.annotate.genai.Client", return_value=mock_client),
        pytest.raises(AnnotationError, match="corrupt.*refusing"),
    ):
        annotate_shot(shot, keyframes, [], config, cache_dir=cache_dir)

    mock_client.models.generate_content.assert_not_called()


# ---------------------------------------------------------------------------
# OpenAI provider
# ---------------------------------------------------------------------------


def _openai_response(
    *,
    caption: str = "Two figures cross a rain-soaked street under neon light.",
    mood: list[str] | None = None,
    status: str = "completed",
    incomplete_reason: str | None = None,
    output_text: str | None = None,
    output: list[object] | None = None,
    error: object | None = None,
    **facets: object,
) -> SimpleNamespace:
    from pipeline.ingest.annotate import _ShotAnnotation

    annotation = _ShotAnnotation(
        caption=caption,
        mood=mood if mood is not None else ["noir", "lonely", "rainy"],
        framing=facets.get("framing", "medium"),
        setting=facets.get("setting", "exterior"),
        time_of_day=facets.get("time_of_day", "night"),
        people_count=facets.get("people_count", 2),
        energy=facets.get("energy", "calm"),
        camera_motion=facets.get("camera_motion", "unknown"),
        palette=facets.get("palette", ["neon red", "black"]),
        subjects=facets.get("subjects", ["two figures", "wet street"]),
        on_screen_text=facets.get("on_screen_text", ""),
    )
    return SimpleNamespace(
        status=status,
        output=[] if output is None else output,
        output_text=(
            annotation.model_dump_json()
            if output_text is None
            else output_text
        ),
        incomplete_details=(
            SimpleNamespace(reason=incomplete_reason)
            if incomplete_reason is not None
            else None
        ),
        error=error,
    )


def _select_openai(config: Config) -> None:
    config.models.annotator_provider = "openai"
    config.models.annotator = "gpt-5.6-luna"
    config.models.annotator_image_detail = "low"
    config.models.annotator_reasoning_effort = "none"


def test_openai_annotation_cache_avoids_second_responses_call(
    tmp_path: Path,
    config: Config,
) -> None:
    """OpenAI retries reuse the same durable provider-independent cache."""
    from pipeline.ingest.annotate import annotate_shot

    _select_openai(config)
    shot = _make_shot()
    keyframe = _make_jpeg(tmp_path)
    mock_client = MagicMock()
    mock_client.responses.create.return_value = _openai_response()

    with patch(
        "pipeline.ingest.annotate._get_openai_client",
        return_value=mock_client,
    ):
        annotate_shot(
            shot,
            [keyframe],
            [],
            config,
            cache_dir=tmp_path / "annotations",
        )
        annotate_shot(
            shot,
            [keyframe],
            [],
            config,
            cache_dir=tmp_path / "annotations",
        )

    mock_client.responses.create.assert_called_once()


def test_openai_annotation_uses_responses_structured_output(
    tmp_path: Path,
    config: Config,
) -> None:
    """OpenAI receives at most three low-detail data-URL keyframes."""
    from pipeline.ingest.annotate import _ShotAnnotation, annotate_shot

    _select_openai(config)
    keyframes = [_make_jpeg(tmp_path, f"kf{i}.jpg") for i in range(4)]
    mock_client = MagicMock()
    mock_client.responses.create.return_value = _openai_response()

    with patch(
        "pipeline.ingest.annotate._get_openai_client",
        return_value=mock_client,
    ):
        result = annotate_shot(_make_shot(), keyframes, [], config)

    kwargs = mock_client.responses.create.call_args.kwargs
    assert kwargs["model"] == "gpt-5.6-luna"
    assert kwargs["reasoning"] == {"effort": "none"}
    assert kwargs["store"] is False
    assert kwargs["max_output_tokens"] == 800
    response_format = kwargs["text"]["format"]
    assert response_format["type"] == "json_schema"
    assert response_format["name"] == "shot_annotation"
    assert response_format["strict"] is True
    assert response_format["schema"] == _ShotAnnotation.model_json_schema()

    content = kwargs["input"][0]["content"]
    images = [part for part in content if part["type"] == "input_image"]
    assert len(images) == 3
    assert all(part["detail"] == "low" for part in images)
    assert all(
        part["image_url"].startswith("data:image/jpeg;base64,")
        for part in images
    )
    assert base64.b64decode(images[0]["image_url"].split(",", 1)[1])
    assert result["caption"].startswith("Two figures")
    assert result["mood"] == ["noir", "lonely", "rainy"]


def test_openai_annotation_appends_overlapping_dialogue(
    tmp_path: Path,
    config: Config,
) -> None:
    """Provider-independent searchable text retains matching dialogue."""
    from pipeline.ingest.annotate import annotate_shot

    _select_openai(config)
    keyframe = _make_jpeg(tmp_path)
    dialogue = [
        DialogueLine(start=12.0, end=13.0, text="Meet me after midnight."),
        DialogueLine(start=30.0, end=31.0, text="Outside the shot."),
    ]
    mock_client = MagicMock()
    mock_client.responses.create.return_value = _openai_response()

    with patch(
        "pipeline.ingest.annotate._get_openai_client",
        return_value=mock_client,
    ):
        result = annotate_shot(_make_shot(), [keyframe], dialogue, config)

    assert "Meet me after midnight." in result["searchable_text"]
    assert "Outside the shot." not in result["searchable_text"]


def test_openai_unusable_output_is_retried_once_then_succeeds(
    tmp_path: Path,
    config: Config,
) -> None:
    """A truncated/invalid response costs one retry, not the whole film."""
    from pipeline.ingest.annotate import annotate_shot

    _select_openai(config)
    bad = _openai_response(
        status="incomplete",
        incomplete_reason="max_output_tokens",
        output_text='{"caption":"truncated',
    )
    mock_client = MagicMock()
    mock_client.responses.create.side_effect = [bad, _openai_response()]

    with patch(
        "pipeline.ingest.annotate._get_openai_client",
        return_value=mock_client,
    ):
        result = annotate_shot(_make_shot(), [_make_jpeg(tmp_path)], [], config)

    assert mock_client.responses.create.call_count == 2
    assert result["caption"].startswith("Two figures")
    first, second = mock_client.responses.create.call_args_list
    assert first.kwargs["max_output_tokens"] == 800
    # Text-dense shots (credit rolls) truncate at the normal budget; the
    # retry must escalate or it deterministically fails the same way.
    assert second.kwargs["max_output_tokens"] == 3000
    assert (
        first.kwargs["input"][0]["content"][0]["text"]
        == second.kwargs["input"][0]["content"][0]["text"]
    )


def test_openai_content_filter_retries_once_without_ocr(
    tmp_path: Path,
    config: Config,
) -> None:
    """A filtered OCR response gets one bounded no-transcription fallback."""
    from pipeline.ingest.annotate import _PROMPT, annotate_shot

    _select_openai(config)
    keyframes = [_make_jpeg(tmp_path, f"kf{i}.jpg") for i in range(3)]
    filtered = _openai_response(
        status="incomplete",
        incomplete_reason="content_filter",
        output_text='{"caption":"partial',
    )
    fallback = _openai_response(
        caption="A static title card presents a dictionary-style definition.",
        on_screen_text="",
    )
    mock_client = MagicMock()
    mock_client.responses.create.side_effect = [filtered, fallback]

    with patch(
        "pipeline.ingest.annotate._get_openai_client",
        return_value=mock_client,
    ):
        result = annotate_shot(_make_shot(), keyframes, [], config)

    assert mock_client.responses.create.call_count == 2
    first, second = mock_client.responses.create.call_args_list
    assert first.kwargs["max_output_tokens"] == 800
    assert second.kwargs["max_output_tokens"] == 800

    first_content = first.kwargs["input"][0]["content"]
    second_content = second.kwargs["input"][0]["content"]
    assert first_content[0]["text"] == _PROMPT
    fallback_prompt = second_content[0]["text"]
    assert fallback_prompt.startswith(_PROMPT)
    assert "do not quote" in fallback_prompt.lower()
    assert "on_screen_text" in fallback_prompt
    assert "empty" in fallback_prompt.lower()
    assert first_content[1:] == second_content[1:]
    assert result["caption"].startswith("A static title card")
    assert result["on_screen_text"] == ""


def test_openai_content_filter_twice_stops_after_two_calls(
    tmp_path: Path,
    config: Config,
) -> None:
    """A filtered fallback fails explicitly without starting a third request."""
    from pipeline.ingest.annotate import AnnotationError, annotate_shot

    _select_openai(config)
    filtered = _openai_response(
        status="incomplete",
        incomplete_reason="content_filter",
        output_text='{"caption":"partial',
    )
    mock_client = MagicMock()
    mock_client.responses.create.side_effect = [filtered, filtered]

    with (
        patch(
            "pipeline.ingest.annotate._get_openai_client",
            return_value=mock_client,
        ),
        pytest.raises(AnnotationError, match=r"content[_ -]filter"),
    ):
        annotate_shot(_make_shot(), [_make_jpeg(tmp_path)], [], config)

    assert mock_client.responses.create.call_count == 2


def test_openai_content_filter_fallback_rejects_transcribed_text(
    tmp_path: Path,
    config: Config,
) -> None:
    """A no-OCR fallback cannot cache text it was instructed not to reproduce."""
    from pipeline.ingest.annotate import AnnotationError, annotate_shot

    _select_openai(config)
    filtered = _openai_response(
        status="incomplete",
        incomplete_reason="content_filter",
        output_text='{"caption":"partial',
    )
    unsafe_fallback = _openai_response(on_screen_text="Filtered title text")
    mock_client = MagicMock()
    mock_client.responses.create.side_effect = [filtered, unsafe_fallback]

    with (
        patch(
            "pipeline.ingest.annotate._get_openai_client",
            return_value=mock_client,
        ),
        pytest.raises(AnnotationError, match="no-transcription"),
    ):
        annotate_shot(
            _make_shot(),
            [_make_jpeg(tmp_path)],
            [],
            config,
            cache_dir=tmp_path / "annotations",
        )

    assert mock_client.responses.create.call_count == 2
    assert not list((tmp_path / "annotations").glob("**/*.json"))


def test_openai_content_filter_fallback_uses_distinct_durable_cache(
    tmp_path: Path,
    config: Config,
) -> None:
    """The no-OCR result is prompt-scoped and reused without another API call."""
    from pipeline.ingest.annotate import (
        _PROMPT_SHA256,
        _annotation_cache_identity,
        _annotation_profile_id,
        annotate_shot,
    )

    _select_openai(config)
    shot = _make_shot()
    keyframes = [_make_jpeg(tmp_path)]
    cache_dir = tmp_path / "annotations"
    filtered = _openai_response(
        status="incomplete",
        incomplete_reason="content_filter",
        output_text='{"caption":"partial',
    )
    mock_client = MagicMock()
    mock_client.responses.create.side_effect = [filtered, _openai_response()]

    with patch(
        "pipeline.ingest.annotate._get_openai_client",
        return_value=mock_client,
    ):
        first = annotate_shot(
            shot,
            keyframes,
            [],
            config,
            cache_dir=cache_dir,
        )
        cached = annotate_shot(
            shot,
            keyframes,
            [],
            config,
            cache_dir=cache_dir,
        )

    assert first == cached
    assert mock_client.responses.create.call_count == 2
    cache_paths = list(cache_dir.glob(f"*/{shot.shot_id}.json"))
    assert len(cache_paths) == 1
    payload = json.loads(cache_paths[0].read_text(encoding="utf-8"))
    identity = payload["identity"]
    assert identity["request_variant"] == "content_filter_no_ocr_v1"
    assert identity["prompt_sha256"] != _PROMPT_SHA256

    primary_identity = _annotation_cache_identity(keyframes, config, "openai")
    assert cache_paths[0].parent.name != _annotation_profile_id(primary_identity)


def test_openai_annotation_rejects_missing_output_text(
    tmp_path: Path,
    config: Config,
) -> None:
    """A malformed or incomplete response cannot become a blank DB row."""
    from pipeline.ingest.annotate import AnnotationError, annotate_shot

    _select_openai(config)
    response = _openai_response(output_text="")
    mock_client = MagicMock()
    mock_client.responses.create.return_value = response

    with (
        patch(
            "pipeline.ingest.annotate._get_openai_client",
            return_value=mock_client,
        ),
        pytest.raises(AnnotationError),
    ):
        annotate_shot(_make_shot(), [_make_jpeg(tmp_path)], [], config)


def test_openai_annotation_surfaces_refusal(
    tmp_path: Path,
    config: Config,
) -> None:
    """A provider refusal is explicit rather than silently indexed."""
    from pipeline.ingest.annotate import AnnotationError, annotate_shot

    _select_openai(config)
    response = _openai_response()
    response.output = [
        SimpleNamespace(
            type="message",
            content=[
                SimpleNamespace(type="refusal", refusal="Cannot process image.")
            ],
        )
    ]
    mock_client = MagicMock()
    mock_client.responses.create.return_value = response

    with (
        patch(
            "pipeline.ingest.annotate._get_openai_client",
            return_value=mock_client,
        ),
        pytest.raises(AnnotationError, match="refused"),
    ):
        annotate_shot(_make_shot(), [_make_jpeg(tmp_path)], [], config)

    mock_client.responses.create.assert_called_once()


def test_openai_annotation_surfaces_quota_error_code(
    tmp_path: Path,
    config: Config,
) -> None:
    """Quota failures are actionable when surfaced by the ingest command."""
    from pipeline.ingest.annotate import AnnotationError, annotate_shot

    _select_openai(config)
    response = httpx.Response(
        429,
        request=httpx.Request("POST", "https://api.openai.com/v1/responses"),
        headers={"x-request-id": "req_test"},
    )
    error = openai.RateLimitError(
        "quota exceeded",
        response=response,
        body={"type": "insufficient_quota", "code": "insufficient_quota"},
    )
    mock_client = MagicMock()
    mock_client.responses.create.side_effect = error

    with (
        patch(
            "pipeline.ingest.annotate._get_openai_client",
            return_value=mock_client,
        ),
        pytest.raises(AnnotationError, match="insufficient_quota"),
    ):
        annotate_shot(_make_shot(), [_make_jpeg(tmp_path)], [], config)

    mock_client.responses.create.assert_called_once()


def test_gemini_annotation_failure_is_not_silenced(
    tmp_path: Path,
    config: Config,
) -> None:
    """Gemini errors also abort instead of returning empty annotations."""
    from pipeline.ingest.annotate import AnnotationError, annotate_shot

    mock_client = _make_mock_client()
    mock_client.models.generate_content.side_effect = RuntimeError("API down")

    with (
        patch("pipeline.ingest.annotate.genai.Client", return_value=mock_client),
        pytest.raises(AnnotationError, match="Gemini annotation failed"),
    ):
        annotate_shot(_make_shot(), [_make_jpeg(tmp_path)], [], config)


def test_gemini_empty_annotation_is_rejected(
    tmp_path: Path,
    config: Config,
) -> None:
    """An empty provider response cannot become a blank DB row."""
    from pipeline.ingest.annotate import AnnotationError, annotate_shot

    mock_client = _make_mock_client(response_text="")

    with (
        patch("pipeline.ingest.annotate.genai.Client", return_value=mock_client),
        pytest.raises(AnnotationError, match="empty annotation response"),
    ):
        annotate_shot(_make_shot(), [_make_jpeg(tmp_path)], [], config)


def test_annotation_rejects_invalid_mood_count(
    tmp_path: Path,
    config: Config,
) -> None:
    """The shared provider contract requires two to four mood keywords."""
    from pipeline.ingest.annotate import AnnotationError, annotate_shot

    _select_openai(config)
    mock_client = MagicMock()
    mock_client.responses.create.return_value = _openai_response(mood=["lonely"])

    with (
        patch(
            "pipeline.ingest.annotate._get_openai_client",
            return_value=mock_client,
        ),
        pytest.raises(AnnotationError, match="expected 2-4"),
    ):
        annotate_shot(_make_shot(), [_make_jpeg(tmp_path)], [], config)


def test_unknown_annotation_provider_is_rejected(
    tmp_path: Path,
    config: Config,
) -> None:
    """Provider typos fail loudly before making any API call."""
    from pipeline.ingest.annotate import AnnotationError, annotate_shot

    config.models.annotator_provider = "other"

    with pytest.raises(AnnotationError, match="Unknown annotator provider"):
        annotate_shot(_make_shot(), [_make_jpeg(tmp_path)], [], config)


# ---------------------------------------------------------------------------
# Typed facets
# ---------------------------------------------------------------------------


def test_facets_round_trip_from_gemini(tmp_path: Path, config: Config) -> None:
    """The structured Gemini response's facets survive into the result."""
    from pipeline.ingest.annotate import annotate_shot

    mock_client = _make_mock_client()

    with patch("pipeline.ingest.annotate.genai.Client", return_value=mock_client):
        result = annotate_shot(_make_shot(), [_make_jpeg(tmp_path)], [], config)

    assert result["framing"] == "wide"
    assert result["setting"] == "exterior"
    assert result["time_of_day"] == "night"
    assert result["people_count"] == 2
    assert result["energy"] == "calm"
    assert result["camera_motion"] == "unknown"
    assert result["palette"] == ["neon blue", "black"]
    assert result["subjects"] == ["two silhouetted figures", "city skyline"]
    assert result["on_screen_text"] == ""


def test_validate_annotation_coerces_out_of_vocabulary_facets() -> None:
    """A drifting enum degrades to unknown instead of wasting the paid call."""
    from pipeline.ingest.annotate import _validate_annotation

    raw = {
        "caption": "A shot.",
        "mood": ["calm", "warm"],
        "framing": "dutch angle",
        "setting": "EXTERIOR",
        "time_of_day": None,
        "people_count": "several",
        "energy": "explosive",
        "camera_motion": "crane",
        "palette": ["red", "", 3, "blue", "green", "gold"],
    }
    result = _validate_annotation(raw, "test")

    assert result["framing"] == "unknown"
    assert result["setting"] == "exterior"  # case-normalized, in vocabulary
    assert result["time_of_day"] == "unknown"
    assert result["people_count"] is None
    assert result["energy"] == "unknown"
    assert result["camera_motion"] == "unknown"
    assert result["palette"] == ["red", "blue", "green"]


def test_validate_annotation_clamps_people_count() -> None:
    from pipeline.ingest.annotate import _validate_annotation

    base = {"caption": "A shot.", "mood": ["calm", "warm"]}
    assert _validate_annotation({**base, "people_count": -3}, "t")["people_count"] == 0
    assert _validate_annotation({**base, "people_count": 500}, "t")["people_count"] == 99
    assert _validate_annotation({**base, "people_count": 7}, "t")["people_count"] == 7


def test_v1_annotation_cache_is_a_clean_miss(
    tmp_path: Path,
    config: Config,
) -> None:
    """Pre-facet cache entries re-request instead of failing or half-loading."""
    from pipeline.ingest.annotate import annotate_shot

    shot = _make_shot()
    cache_dir = tmp_path / "annotations"
    cache_dir.mkdir()
    (cache_dir / f"{shot.shot_id}.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "shot_id": shot.shot_id,
                "identity": {"provider": "gemini"},
                "caption": "old cached caption",
                "mood": ["old", "cached"],
            }
        ),
        encoding="utf-8",
    )
    mock_client = _make_mock_client()

    with patch("pipeline.ingest.annotate.genai.Client", return_value=mock_client):
        result = annotate_shot(
            shot,
            [_make_jpeg(tmp_path)],
            [],
            config,
            cache_dir=cache_dir,
        )

    mock_client.models.generate_content.assert_called_once()
    assert result["caption"] != "old cached caption"
    # The incompatible legacy payload is retained; the current response is
    # written into its immutable model/prompt profile directory.
    rewritten_paths = list(cache_dir.glob(f"*/{shot.shot_id}.json"))
    assert len(rewritten_paths) == 1
    rewritten = json.loads(rewritten_paths[0].read_text(encoding="utf-8"))
    assert rewritten["schema_version"] == 2
    assert rewritten["annotation"]["framing"] == "wide"
    legacy = json.loads(
        (cache_dir / f"{shot.shot_id}.json").read_text(encoding="utf-8")
    )
    assert legacy["schema_version"] == 1
