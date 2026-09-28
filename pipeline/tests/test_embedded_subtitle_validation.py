"""Content-gated embedded dialogue, raw evidence and cache lineage regressions."""
from dataclasses import replace
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from pipeline.ingest import dialogue
from pipeline.tests.test_dialogue import _full_embedded_srt, _make_film, _fake_ffmpeg_writer


@pytest.fixture(autouse=True)
def no_real_models(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("A subtitle unit test must not load a real model")

    monkeypatch.setattr(dialogue, "WhisperModel", forbidden)


def _extract(tmp_path, config, monkeypatch, *, rejected=False):
    film = _make_film(tmp_path, text_subtitle_stream_index=2, primary_audio_language_tag="eng")
    if rejected:
        film.duration = 9493.6
    raw = _full_embedded_srt()
    ffmpeg = MagicMock(side_effect=_fake_ffmpeg_writer(raw, film.asset_dir))
    monkeypatch.setattr(dialogue.subprocess, "run", ffmpeg)
    model = MagicMock()
    model.transcribe.side_effect = lambda *args, **kwargs: (
        [SimpleNamespace(start=1.0, end=3.0, text=" Actual English speech from the audio")],
        SimpleNamespace(duration=film.duration, language="en", language_probability=1.0),
    )
    constructor = MagicMock(return_value=model)
    monkeypatch.setattr(dialogue, "WhisperModel", constructor)
    lines = dialogue.extract_dialogue(film, config)
    return film, raw, lines, ffmpeg, constructor, model


def test_unflagged_sparse_english_falls_back_and_preserves_raw_evidence(tmp_path, config, monkeypatch):
    film, raw, lines, ffmpeg, constructor, model = _extract(tmp_path, config, monkeypatch, rejected=True)
    assert [line.text for line in lines] == ["Actual English speech from the audio"]
    assert (film.asset_dir / "subs.srt").read_text(encoding="utf-8") == raw
    manifest = json.loads((film.asset_dir / "dialogue.manifest.json").read_text())
    assert manifest["kind"] == "whisper"
    receipt = manifest["rejected_embedded_subtitles"]
    assert receipt["film_id"] == film.film_id
    assert receipt["stream_index"] == 2
    assert receipt["duration"] == film.duration
    assert receipt["validation"]["automatic_eligible"] is False
    assert "too sparse" in " ".join(receipt["validation"]["reasons"])
    assert len(receipt["validation"]["sha256"]) == 64
    assert model.transcribe.call_args.kwargs["language"] == "en"
    assert dialogue.dialogue_cache_is_current(film, config)
    assert dialogue.dialogue_cache_is_current(film, config)
    ffmpeg.assert_called_once()
    constructor.assert_called_once()


def test_full_english_is_accepted_and_cached_without_whisper(tmp_path, config, monkeypatch):
    film, raw, lines, ffmpeg, constructor, _ = _extract(tmp_path, config, monkeypatch)
    assert len(lines) == 40
    constructor.assert_not_called()
    assert (film.asset_dir / "subs.srt").read_text(encoding="utf-8") == raw
    manifest = json.loads((film.asset_dir / "dialogue.manifest.json").read_text())
    assert manifest["kind"] == "embedded_text"
    assert manifest["subtitle_validation"]["validation"]["automatic_eligible"] is True
    assert dialogue.dialogue_cache_is_current(film, config)
    config.models.whisper = "different-model"
    assert dialogue.dialogue_cache_is_current(film, config)
    assert dialogue.extract_dialogue(film, config) == lines
    ffmpeg.assert_called_once()
    constructor.assert_not_called()


def test_fallback_model_change_reuses_validation_without_extracting_again(tmp_path, config, monkeypatch):
    film, _, _, ffmpeg, constructor, _ = _extract(tmp_path, config, monkeypatch, rejected=True)
    assert dialogue.dialogue_cache_is_current(film, config)
    config.models.whisper = "different-model"
    assert not dialogue.dialogue_cache_is_current(film, config)
    dialogue.extract_dialogue(film, config)
    ffmpeg.assert_called_once()
    assert constructor.call_count == 2
    assert constructor.call_args.args == ("different-model",)
    assert dialogue.dialogue_cache_is_current(film, config)


@pytest.mark.parametrize("rejected", [False, True])
@pytest.mark.parametrize("changed", ["film", "stream", "duration", "srt", "srt_missing", "receipt_missing", "receipt_corrupt", "receipt_decision", "receipt_oversized", "gate_profile", "validator_profile"])
def test_embedded_dependency_changes_invalidate_and_reextract(tmp_path, config, monkeypatch, rejected, changed):
    film, _, _, ffmpeg, _, _ = _extract(tmp_path, config, monkeypatch, rejected=rejected)
    receipt_path = film.asset_dir / dialogue._EMBEDDED_VALIDATION_RECEIPT_NAME
    if changed == "film":
        film = replace(film, film_id="different-source")
    elif changed == "stream":
        film = replace(film, text_subtitle_stream_index=3)
    elif changed == "duration":
        film = replace(film, duration=film.duration + .1)
    elif changed == "srt":
        with (film.asset_dir / "subs.srt").open("a", encoding="utf-8") as handle:
            handle.write("\nchanged bytes\n")
    elif changed == "srt_missing":
        (film.asset_dir / "subs.srt").unlink()
    elif changed == "receipt_missing":
        receipt_path.unlink()
    elif changed == "receipt_corrupt":
        receipt_path.write_text("{broken json")
    elif changed == "receipt_decision":
        receipt = json.loads(receipt_path.read_text())
        receipt["validation"]["automatic_eligible"] = not receipt["validation"]["automatic_eligible"]
        receipt_path.write_text(json.dumps(receipt))
    elif changed == "receipt_oversized":
        receipt_path.write_text(" " * (dialogue._MAX_EMBEDDED_VALIDATION_RECEIPT_BYTES + 1))
    elif changed == "gate_profile":
        monkeypatch.setattr(dialogue, "_EMBEDDED_VALIDATION_PROFILE_VERSION", 2)
    elif changed == "validator_profile":
        monkeypatch.setattr(dialogue, "SUBTITLE_VALIDATION_PROFILE", "future-validator")
    before = {p.name: p.stat().st_mtime_ns for p in film.asset_dir.iterdir()}
    assert not dialogue.dialogue_cache_is_current(film, config)
    assert before == {p.name: p.stat().st_mtime_ns for p in film.asset_dir.iterdir()}
    dialogue.extract_dialogue(film, config)
    assert ffmpeg.call_count == 2
    assert dialogue.dialogue_cache_is_current(film, config)


@pytest.mark.parametrize("raw", ["", "malformed", "1\n00:00:01,000 --> 00:00:02,000\n\ufffd corrupt\n"])
def test_invalid_embedded_text_is_not_parsed_before_fallback(tmp_path, config, monkeypatch, raw):
    film = _make_film(tmp_path, text_subtitle_stream_index=2, primary_audio_language_tag="eng")
    monkeypatch.setattr(dialogue.subprocess, "run", _fake_ffmpeg_writer(raw, film.asset_dir))
    parser = MagicMock(side_effect=AssertionError("Rejected text must not reach the permissive parser"))
    monkeypatch.setattr(dialogue, "_parse_srt", parser)
    monkeypatch.setattr(dialogue, "_extract_via_whisper", lambda *_: [])
    dialogue.extract_dialogue(film, config)
    parser.assert_not_called()
    assert (film.asset_dir / "subs.srt").read_text(encoding="utf-8") == raw
    manifest = json.loads((film.asset_dir / "dialogue.manifest.json").read_text())
    assert manifest["kind"] == "whisper"
    assert dialogue.dialogue_cache_is_current(film, config) is bool(raw)


def test_pending_receipt_never_counts_as_cached_evidence(tmp_path, config):
    film = _make_film(tmp_path, text_subtitle_stream_index=2)
    source = dialogue._dialogue_source(film, config)
    assert source["kind"] == "embedded_pending"
    (film.asset_dir / "dialogue.json").write_text("[]")
    (film.asset_dir / "dialogue.manifest.json").write_text(json.dumps(source))
    assert not dialogue.dialogue_cache_is_current(film, config)


def test_failed_whisper_does_not_publish_mislabelled_dialogue(tmp_path, config, monkeypatch):
    film = _make_film(tmp_path, text_subtitle_stream_index=2)
    film.duration = 9493.6
    raw = _full_embedded_srt()
    monkeypatch.setattr(dialogue.subprocess, "run", _fake_ffmpeg_writer(raw, film.asset_dir))
    monkeypatch.setattr(dialogue, "_extract_via_whisper", MagicMock(side_effect=RuntimeError("local failure")))
    with pytest.raises(RuntimeError, match="local failure"):
        dialogue.extract_dialogue(film, config)
    assert (film.asset_dir / "subs.srt").read_text(encoding="utf-8") == raw
    assert (film.asset_dir / dialogue._EMBEDDED_VALIDATION_RECEIPT_NAME).is_file()
    assert not (film.asset_dir / "dialogue.manifest.json").exists()
    assert not (film.asset_dir / "dialogue.json").exists()
