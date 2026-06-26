"""Tests for pipeline/ingest/media.py — written before implementation (TDD).

Tests:
  - extract_media returns None (writes to disk, no return value)
  - Keyframe files exist at asset_dir/keyframes/{shot_id}_{n}.webp for every keyframe_time
  - Preview files exist at asset_dir/previews/{shot_id}.webm for every shot
  - ffmpeg keyframe command uses correct flags: -ss, -frames:v 1, scale=1280:-1, -q:v 82
  - ffmpeg preview command uses VP9, 480p scale, CRF 35, -an
  - Preview duration capped at 4s; short shots use shot duration
  - First keyframe padded to shot.t_start + 0.1s to avoid black frames at cuts
  - Integration: actual files created from the synthetic test_clip
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pipeline.config import Config


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_film(tmp_path: Path):
    """Return a minimal FilmRecord with a real asset_dir."""
    from pipeline.ingest.probe import FilmRecord

    asset_dir = tmp_path / "assets" / "abc123"
    asset_dir.mkdir(parents=True, exist_ok=True)
    return FilmRecord(
        film_id="abc123",
        path=tmp_path / "film.mkv",
        asset_dir=asset_dir,
        duration=30.0,
        fps=30.0,
        has_embedded_subs=False,
        title="Test Film",
    )


def _make_shots(film_id: str):
    """Return two minimal Shot objects covering short and long cases."""
    from pipeline.ingest.shots import Shot

    return [
        Shot(
            shot_id=f"{film_id}_0000",
            t_start=0.0,
            t_end=10.0,
            parent_shot_id=None,
            keyframe_times=[2.5, 5.0, 7.5],  # >= 2s → 3 keyframes
        ),
        Shot(
            shot_id=f"{film_id}_0001",
            t_start=10.0,
            t_end=11.0,
            parent_shot_id=None,
            keyframe_times=[10.5],  # < 2s → 1 keyframe
        ),
    ]


def _fake_ffmpeg_creator(cmd, **kwargs):
    """Mock subprocess.run that creates a stub output file (last arg in cmd)."""
    output_path = Path(cmd[-1])
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(b"\x00")
    result = MagicMock()
    result.returncode = 0
    return result


def _capturing_ffmpeg(calls_list: list):
    """Return a mock side_effect that records calls and creates stub output files."""

    def _run(cmd, **kwargs):
        calls_list.append(list(cmd))
        output_path = Path(cmd[-1])
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"\x00")
        result = MagicMock()
        result.returncode = 0
        return result

    return _run


# ---------------------------------------------------------------------------
# Unit tests: basic interface
# ---------------------------------------------------------------------------


def test_extract_media_returns_none(tmp_path: Path, config: Config) -> None:
    """extract_media returns None (side-effect function; writes to disk)."""
    from pipeline.ingest.media import extract_media

    film = _make_film(tmp_path)
    shots = _make_shots(film.film_id)

    with patch("subprocess.run", side_effect=_fake_ffmpeg_creator):
        result = extract_media(film, shots, config)

    assert result is None


# ---------------------------------------------------------------------------
# Unit tests: output directory creation
# ---------------------------------------------------------------------------


def test_extract_media_creates_keyframe_dir(tmp_path: Path, config: Config) -> None:
    """extract_media creates asset_dir/keyframes/ before writing frames."""
    from pipeline.ingest.media import extract_media

    film = _make_film(tmp_path)
    shots = _make_shots(film.film_id)

    with patch("subprocess.run", side_effect=_fake_ffmpeg_creator):
        extract_media(film, shots, config)

    assert (film.asset_dir / "keyframes").is_dir()


def test_extract_media_creates_previews_dir(tmp_path: Path, config: Config) -> None:
    """extract_media creates asset_dir/previews/ before writing clips."""
    from pipeline.ingest.media import extract_media

    film = _make_film(tmp_path)
    shots = _make_shots(film.film_id)

    with patch("subprocess.run", side_effect=_fake_ffmpeg_creator):
        extract_media(film, shots, config)

    assert (film.asset_dir / "previews").is_dir()


# ---------------------------------------------------------------------------
# Unit tests: keyframe file creation
# ---------------------------------------------------------------------------


def test_extract_media_creates_keyframe_files(tmp_path: Path, config: Config) -> None:
    """After extract_media, a WebP file exists for every keyframe_time in every shot."""
    from pipeline.ingest.media import extract_media

    film = _make_film(tmp_path)
    shots = _make_shots(film.film_id)

    with patch("subprocess.run", side_effect=_fake_ffmpeg_creator):
        extract_media(film, shots, config)

    kf_dir = film.asset_dir / "keyframes"
    for shot in shots:
        for n in range(len(shot.keyframe_times)):
            expected = kf_dir / f"{shot.shot_id}_{n}.webp"
            assert expected.exists(), f"Missing keyframe file: {expected}"


def test_extract_media_keyframe_count_matches_shot(tmp_path: Path, config: Config) -> None:
    """Number of WebP files per shot equals len(shot.keyframe_times)."""
    from pipeline.ingest.media import extract_media

    film = _make_film(tmp_path)
    shots = _make_shots(film.film_id)

    with patch("subprocess.run", side_effect=_fake_ffmpeg_creator):
        extract_media(film, shots, config)

    kf_dir = film.asset_dir / "keyframes"
    for shot in shots:
        actual_files = sorted(kf_dir.glob(f"{shot.shot_id}_*.webp"))
        assert len(actual_files) == len(shot.keyframe_times), (
            f"Shot {shot.shot_id}: expected {len(shot.keyframe_times)} keyframe files, "
            f"got {len(actual_files)}"
        )


# ---------------------------------------------------------------------------
# Unit tests: preview file creation
# ---------------------------------------------------------------------------


def test_extract_media_creates_preview_files(tmp_path: Path, config: Config) -> None:
    """After extract_media, a WebM preview file exists for every shot."""
    from pipeline.ingest.media import extract_media

    film = _make_film(tmp_path)
    shots = _make_shots(film.film_id)

    with patch("subprocess.run", side_effect=_fake_ffmpeg_creator):
        extract_media(film, shots, config)

    preview_dir = film.asset_dir / "previews"
    for shot in shots:
        expected = preview_dir / f"{shot.shot_id}.webm"
        assert expected.exists(), f"Missing preview file: {expected}"


# ---------------------------------------------------------------------------
# Unit tests: ffmpeg keyframe command flags
# ---------------------------------------------------------------------------


def test_extract_media_keyframe_ffmpeg_flags(tmp_path: Path, config: Config) -> None:
    """ffmpeg keyframe calls use -frames:v 1, scale=1280:-1, and -q:v 82."""
    from pipeline.ingest.media import extract_media

    film = _make_film(tmp_path)
    shots = [_make_shots(film.film_id)[0]]  # 3-keyframe shot only

    calls: list[list[str]] = []
    with patch("subprocess.run", side_effect=_capturing_ffmpeg(calls)):
        extract_media(film, shots, config)

    kf_calls = [c for c in calls if c[-1].endswith(".webp")]
    assert len(kf_calls) == len(shots[0].keyframe_times)

    for cmd in kf_calls:
        cmd_str = " ".join(cmd)
        assert "scale=1280:-1" in cmd_str, f"Missing scale=1280:-1: {cmd_str}"

        assert "-frames:v" in cmd, f"Missing -frames:v: {cmd}"
        fi = cmd.index("-frames:v")
        assert cmd[fi + 1] == "1", f"Expected -frames:v 1, got -frames:v {cmd[fi+1]}"

        assert "-q:v" in cmd, f"Missing -q:v: {cmd}"
        qi = cmd.index("-q:v")
        assert cmd[qi + 1] == "82", f"Expected -q:v 82, got -q:v {cmd[qi+1]}"


def test_extract_media_keyframe_uses_fast_seek(tmp_path: Path, config: Config) -> None:
    """-ss appears before -i in keyframe commands (input seek, not output seek)."""
    from pipeline.ingest.media import extract_media

    film = _make_film(tmp_path)
    shots = [_make_shots(film.film_id)[1]]  # 1-keyframe shot

    calls: list[list[str]] = []
    with patch("subprocess.run", side_effect=_capturing_ffmpeg(calls)):
        extract_media(film, shots, config)

    kf_calls = [c for c in calls if c[-1].endswith(".webp")]
    assert len(kf_calls) == 1

    cmd = kf_calls[0]
    assert "-ss" in cmd and "-i" in cmd
    assert cmd.index("-ss") < cmd.index("-i"), (
        "-ss must appear before -i for fast input seek"
    )


def test_extract_media_keyframe_first_frame_pad(tmp_path: Path, config: Config) -> None:
    """First keyframe seek is padded to at least shot.t_start + 0.1s."""
    from pipeline.ingest.media import extract_media
    from pipeline.ingest.shots import Shot

    film = _make_film(tmp_path)
    # Keyframe at t=0.02 is before t_start + 0.1 = 0.1
    padded_shot = Shot(
        shot_id=f"{film.film_id}_0000",
        t_start=0.0,
        t_end=1.0,
        parent_shot_id=None,
        keyframe_times=[0.02],
    )

    calls: list[list[str]] = []
    with patch("subprocess.run", side_effect=_capturing_ffmpeg(calls)):
        extract_media(film, [padded_shot], config)

    kf_calls = [c for c in calls if c[-1].endswith(".webp")]
    assert len(kf_calls) == 1

    cmd = kf_calls[0]
    ss_idx = cmd.index("-ss")
    seek_t = float(cmd[ss_idx + 1])
    assert seek_t >= 0.1, (
        f"First keyframe seek {seek_t:.3f}s should be >= t_start + 0.1 = 0.1s"
    )


# ---------------------------------------------------------------------------
# Unit tests: ffmpeg preview command flags
# ---------------------------------------------------------------------------


def test_extract_media_preview_ffmpeg_flags(tmp_path: Path, config: Config) -> None:
    """ffmpeg preview calls use libvpx-vp9, scale=-1:480, -crf 35, -b:v 0, -an."""
    from pipeline.ingest.media import extract_media

    film = _make_film(tmp_path)
    shots = [_make_shots(film.film_id)[0]]

    calls: list[list[str]] = []
    with patch("subprocess.run", side_effect=_capturing_ffmpeg(calls)):
        extract_media(film, shots, config)

    preview_calls = [c for c in calls if c[-1].endswith(".webm")]
    assert len(preview_calls) == 1

    cmd = preview_calls[0]
    cmd_str = " ".join(cmd)

    assert "libvpx-vp9" in cmd_str, f"Missing libvpx-vp9: {cmd_str}"
    assert "scale=-1:480" in cmd_str, f"Missing scale=-1:480: {cmd_str}"
    assert "-an" in cmd, f"Missing -an (no audio): {cmd}"

    assert "-crf" in cmd, f"Missing -crf: {cmd}"
    crf_idx = cmd.index("-crf")
    assert cmd[crf_idx + 1] == "35", f"Expected -crf 35, got {cmd[crf_idx+1]}"

    assert "-b:v" in cmd, f"Missing -b:v: {cmd}"
    bv_idx = cmd.index("-b:v")
    assert cmd[bv_idx + 1] == "0", f"Expected -b:v 0, got {cmd[bv_idx+1]}"


def test_extract_media_preview_uses_fast_seek(tmp_path: Path, config: Config) -> None:
    """-ss appears before -i in preview commands (fast input seek)."""
    from pipeline.ingest.media import extract_media

    film = _make_film(tmp_path)
    shots = [_make_shots(film.film_id)[0]]

    calls: list[list[str]] = []
    with patch("subprocess.run", side_effect=_capturing_ffmpeg(calls)):
        extract_media(film, shots, config)

    preview_calls = [c for c in calls if c[-1].endswith(".webm")]
    assert len(preview_calls) == 1

    cmd = preview_calls[0]
    assert "-ss" in cmd and "-i" in cmd
    assert cmd.index("-ss") < cmd.index("-i"), (
        "-ss must appear before -i for fast input seek"
    )


def test_extract_media_preview_duration_capped_at_4s(tmp_path: Path, config: Config) -> None:
    """Preview -t value is at most 4s even for a 30s shot."""
    from pipeline.ingest.media import extract_media
    from pipeline.ingest.shots import Shot

    film = _make_film(tmp_path)
    long_shot = Shot(
        shot_id=f"{film.film_id}_0000",
        t_start=0.0,
        t_end=30.0,
        parent_shot_id=None,
        keyframe_times=[7.5, 15.0, 22.5],
    )

    calls: list[list[str]] = []
    with patch("subprocess.run", side_effect=_capturing_ffmpeg(calls)):
        extract_media(film, [long_shot], config)

    preview_calls = [c for c in calls if c[-1].endswith(".webm")]
    assert len(preview_calls) == 1

    cmd = preview_calls[0]
    t_idx = cmd.index("-t")
    duration = float(cmd[t_idx + 1])
    assert duration <= 4.0, f"Preview duration {duration}s exceeds 4s cap"


def test_extract_media_preview_duration_short_shot(tmp_path: Path, config: Config) -> None:
    """Preview -t value for a 2s shot equals the shot duration (< 4s cap)."""
    from pipeline.ingest.media import extract_media
    from pipeline.ingest.shots import Shot

    film = _make_film(tmp_path)
    short_shot = Shot(
        shot_id=f"{film.film_id}_0000",
        t_start=10.0,
        t_end=12.0,
        parent_shot_id=None,
        keyframe_times=[11.0],
    )

    calls: list[list[str]] = []
    with patch("subprocess.run", side_effect=_capturing_ffmpeg(calls)):
        extract_media(film, [short_shot], config)

    preview_calls = [c for c in calls if c[-1].endswith(".webm")]
    assert len(preview_calls) == 1

    cmd = preview_calls[0]
    t_idx = cmd.index("-t")
    duration = float(cmd[t_idx + 1])
    assert duration == pytest.approx(2.0), (
        f"Expected 2.0s preview for a 2s shot, got {duration}s"
    )


def test_extract_media_empty_shots_list(tmp_path: Path, config: Config) -> None:
    """extract_media handles an empty shot list without error."""
    from pipeline.ingest.media import extract_media

    film = _make_film(tmp_path)

    with patch("subprocess.run", side_effect=_fake_ffmpeg_creator) as mock_run:
        extract_media(film, [], config)

    mock_run.assert_not_called()


# ---------------------------------------------------------------------------
# Integration tests: actual ffmpeg extraction from test_clip
# ---------------------------------------------------------------------------


def test_extract_media_integration_creates_keyframes(
    test_clip: Path, config: Config
) -> None:
    """Integration: WebP keyframe files are created on disk from the synthetic test clip."""
    from pipeline.ingest.media import extract_media
    from pipeline.ingest.probe import probe_film
    from pipeline.ingest.shots import Shot

    film = probe_film(test_clip, config)
    shots = [
        Shot(
            shot_id=f"{film.film_id}_0000",
            t_start=1.0,
            t_end=6.0,
            parent_shot_id=None,
            keyframe_times=[2.25, 3.5, 4.75],
        )
    ]

    extract_media(film, shots, config)

    kf_dir = film.asset_dir / "keyframes"
    for n in range(3):
        path = kf_dir / f"{shots[0].shot_id}_{n}.webp"
        assert path.exists(), f"Missing keyframe file: {path}"
        assert path.stat().st_size > 0, f"Keyframe file is empty: {path}"


def test_extract_media_integration_creates_previews(
    test_clip: Path, config: Config
) -> None:
    """Integration: a WebM preview file is created on disk from the synthetic test clip."""
    from pipeline.ingest.media import extract_media
    from pipeline.ingest.probe import probe_film
    from pipeline.ingest.shots import Shot

    film = probe_film(test_clip, config)
    shots = [
        Shot(
            shot_id=f"{film.film_id}_0000",
            t_start=1.0,
            t_end=6.0,
            parent_shot_id=None,
            keyframe_times=[3.5],
        )
    ]

    extract_media(film, shots, config)

    preview_path = film.asset_dir / "previews" / f"{shots[0].shot_id}.webm"
    assert preview_path.exists(), f"Missing preview file: {preview_path}"
    assert preview_path.stat().st_size > 0, f"Preview file is empty: {preview_path}"
