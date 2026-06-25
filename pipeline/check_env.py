"""Environment pre-flight checks for the cinema-search pipeline.

Run with::

    python -m pipeline.check_env

Asserts:
- CUDA is visible (torch.cuda.is_available())
- ffmpeg is on PATH
- GEMINI_API_KEY is set in the environment
- assets_dir from config.yaml is writable

Exits 0 on success, 1 on the first failure.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

from pipeline.config import load_config


def check_cuda() -> None:
    """Assert that at least one CUDA device is visible."""
    try:
        import torch
    except ImportError:
        _fail("torch is not installed — run: uv add torch torchvision "
              "--index https://download.pytorch.org/whl/cu128")

    if not torch.cuda.is_available():
        _fail(
            "CUDA not available. "
            "Verify CUDA Toolkit 12.8 + driver are installed and that "
            "torch was installed from the cu128 index."
        )
    device_name = torch.cuda.get_device_name(0)
    _ok(f"CUDA: {device_name}")


def check_ffmpeg() -> None:
    """Assert that ffmpeg (and ffprobe) are on PATH."""
    for binary in ("ffmpeg", "ffprobe"):
        if shutil.which(binary) is None:
            _fail(
                f"{binary} not found on PATH. "
                "Install via: winget install Gyan.FFmpeg"
            )
    _ok("ffmpeg + ffprobe on PATH")


def check_gemini_key() -> None:
    """Assert that GEMINI_API_KEY is set."""
    key = os.environ.get("GEMINI_API_KEY", "")
    if not key:
        _fail(
            "GEMINI_API_KEY is not set. "
            "Add it to your shell profile or a .env file."
        )
    _ok("GEMINI_API_KEY is set")


def check_assets_dir() -> None:
    """Assert that assets_dir from config.yaml is writable."""
    try:
        cfg = load_config()
    except FileNotFoundError as exc:
        _fail(str(exc))

    assets_dir: Path = cfg.paths.assets_dir
    assets_dir.mkdir(parents=True, exist_ok=True)

    try:
        with tempfile.NamedTemporaryFile(dir=assets_dir, delete=True):
            pass
    except OSError as exc:
        _fail(f"assets_dir '{assets_dir}' is not writable: {exc}")

    _ok(f"assets_dir writable: {assets_dir}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ok(msg: str) -> None:
    print(f"  [OK]  {msg}")


def _fail(msg: str) -> None:
    print(f"  [FAIL] {msg}", file=sys.stderr)
    sys.exit(1)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    print("cinema-search environment check")
    print("=" * 40)
    check_cuda()
    check_ffmpeg()
    check_gemini_key()
    check_assets_dir()
    print("=" * 40)
    print("All checks passed.")


if __name__ == "__main__":
    main()
