"""Tests for supported local devices and provider-aware credential checks."""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from pipeline.config import Config


def test_check_cuda_accepts_supported_cpu_fallback(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from pipeline import check_env

    fake_torch = SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: False))
    monkeypatch.setitem(sys.modules, "torch", fake_torch)

    check_env.check_cuda()

    output = capsys.readouterr()
    assert "local models will use CPU" in output.out
    assert not output.err


def test_check_cuda_reports_available_device(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from pipeline import check_env

    fake_torch = SimpleNamespace(cuda=SimpleNamespace(
        is_available=lambda: True,
        get_device_name=lambda index: "Test GPU",
    ))
    monkeypatch.setitem(sys.modules, "torch", fake_torch)

    check_env.check_cuda()

    assert "CUDA: Test GPU" in capsys.readouterr().out


def test_check_cuda_requires_installed_pytorch(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from pipeline import check_env

    monkeypatch.setitem(sys.modules, "torch", None)

    with pytest.raises(SystemExit) as failure:
        check_env.check_cuda()

    assert failure.value.code == 1
    assert "torch is not installed — run: uv sync --dev" in capsys.readouterr().err


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
