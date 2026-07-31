"""Tests for provider-aware annotation credential checks."""

from __future__ import annotations

import pytest

from pipeline.config import Config


def test_check_annotator_key_accepts_selected_openai_key(
    config: Config,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from pipeline import check_env

    config.models.annotator_provider = "openai"
    monkeypatch.setattr(check_env, "load_config", lambda: config)
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    check_env.check_annotator_key()

    assert "OPENAI_API_KEY is set for openai" in capsys.readouterr().out


def test_check_annotator_key_accepts_selected_gemini_key(
    config: Config,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from pipeline import check_env

    config.models.annotator_provider = "gemini"
    monkeypatch.setattr(check_env, "load_config", lambda: config)
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    check_env.check_annotator_key()

    assert "GEMINI_API_KEY is set for gemini" in capsys.readouterr().out


def test_check_annotator_key_rejects_missing_selected_key(
    config: Config,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from pipeline import check_env

    config.models.annotator_provider = "openai"
    monkeypatch.setattr(check_env, "load_config", lambda: config)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    with pytest.raises(SystemExit):
        check_env.check_annotator_key()

    assert "OPENAI_API_KEY is not set" in capsys.readouterr().err
