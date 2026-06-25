"""Tests for pipeline/ingest/shots.py — written before implementation (TDD).

Tests:
  - Shot dataclass: fields, types
  - detect_shots: returns list[Shot], no zero-duration shot
  - Flash filter: merges shots < 0.5s into previous
  - Sub-segmentation: shots > subsegment_min_duration → sub-segments with parent_shot_id
  - Keyframe times: 1 at midpoint for <2s; 3 at 25/50/75% for >=2s
  - Integration: shot count on test_clip within ±2 of expected; no zero-duration shot
"""
from __future__ import annotations

import math
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from pipeline.config import Config


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_film(tmp_path: Path, *, duration: float = 30.0, fps: float = 30.0):
    """Return a minimal FilmRecord."""
    from pipeline.ingest.probe import FilmRecord

    asset_dir = tmp_path / "assets" / "abc123"
    asset_dir.mkdir(parents=True, exist_ok=True)
    return FilmRecord(
        film_id="abc123",
        path=tmp_path / "film.mkv",
        asset_dir=asset_dir,
        duration=duration,
        fps=fps,
        has_embedded_subs=False,
        title="Test Film",
    )


def _mock_transnetv2(scenes_frames: list[tuple[int, int]], total_frames: int):
    """Return (fake_single_pred_mock, scenes_np) for mocking TransNetV2."""
    fake_single_pred = MagicMock()
    fake_single_pred.cpu.return_value.detach.return_value.numpy.return_value = np.zeros(
        total_frames
    )
    scenes_np = np.array(scenes_frames, dtype=np.int32)
    return fake_single_pred, scenes_np


# ---------------------------------------------------------------------------
# Unit tests: Shot dataclass
# ---------------------------------------------------------------------------


def test_shot_dataclass_has_required_fields() -> None:
    """Shot has shot_id, t_start, t_end, parent_shot_id, keyframe_times."""
    from pipeline.ingest.shots import Shot

    shot = Shot(
        shot_id="abc_0000",
        t_start=0.0,
        t_end=5.0,
        parent_shot_id=None,
        keyframe_times=[2.5],
    )
    assert shot.shot_id == "abc_0000"
    assert shot.t_start == 0.0
    assert shot.t_end == 5.0
    assert shot.parent_shot_id is None
    assert shot.keyframe_times == [2.5]


def test_shot_timestamps_are_floats() -> None:
    """Shot.t_start and t_end are float."""
    from pipeline.ingest.shots import Shot

    shot = Shot(
        shot_id="x_0000",
        t_start=1.0,
        t_end=5.0,
        parent_shot_id=None,
        keyframe_times=[],
    )
    assert isinstance(shot.t_start, float)
    assert isinstance(shot.t_end, float)


# ---------------------------------------------------------------------------
# Unit tests: _compute_keyframes
# ---------------------------------------------------------------------------


def test_keyframes_short_shot_one_midpoint() -> None:
    """Shots < 2s → single keyframe at midpoint."""
    from pipeline.ingest.shots import _compute_keyframes

    kf = _compute_keyframes(0.0, 1.0, threshold=2.0)
    assert len(kf) == 1
    assert kf[0] == pytest.approx(0.5)


def test_keyframes_exactly_2s_gets_three() -> None:
    """Shots >= 2s → 3 keyframes."""
    from pipeline.ingest.shots import _compute_keyframes

    kf = _compute_keyframes(0.0, 2.0, threshold=2.0)
    assert len(kf) == 3


def test_keyframes_long_shot_at_25_50_75_percent() -> None:
    """Shots >= 2s → keyframes at 25%, 50%, 75% of [t_start, t_end]."""
    from pipeline.ingest.shots import _compute_keyframes

    kf = _compute_keyframes(10.0, 20.0, threshold=2.0)  # 10s shot starting at 10s
    assert kf[0] == pytest.approx(12.5)  # 10 + 0.25*10
    assert kf[1] == pytest.approx(15.0)  # 10 + 0.50*10
    assert kf[2] == pytest.approx(17.5)  # 10 + 0.75*10


def test_keyframes_non_zero_start() -> None:
    """Keyframe positions are correct for shots not starting at 0."""
    from pipeline.ingest.shots import _compute_keyframes

    kf = _compute_keyframes(5.0, 5.5, threshold=2.0)  # 0.5s shot
    assert len(kf) == 1
    assert kf[0] == pytest.approx(5.25)


# ---------------------------------------------------------------------------
# Unit tests: _merge_flash_shots
# ---------------------------------------------------------------------------


def test_flash_filter_keeps_long_shots_unchanged() -> None:
    """Shots >= 0.5s are not merged."""
    from pipeline.ingest.shots import _merge_flash_shots

    shots = [(0.0, 5.0), (5.0, 10.0)]
    result = _merge_flash_shots(shots, threshold=0.5)
    assert len(result) == 2
    assert result[0] == pytest.approx((0.0, 5.0))
    assert result[1] == pytest.approx((5.0, 10.0))


def test_flash_filter_merges_short_shot_into_previous() -> None:
    """A shot < 0.5s is merged into the preceding shot."""
    from pipeline.ingest.shots import _merge_flash_shots

    # middle shot 5.0→5.3 is 0.3s (flash)
    shots = [(0.0, 5.0), (5.0, 5.3), (5.3, 10.0)]
    result = _merge_flash_shots(shots, threshold=0.5)
    assert len(result) == 2
    assert result[0] == pytest.approx((0.0, 5.3))
    assert result[1] == pytest.approx((5.3, 10.0))


def test_flash_filter_keeps_shot_at_threshold() -> None:
    """A shot of exactly 0.5s is NOT merged (threshold is exclusive <0.5)."""
    from pipeline.ingest.shots import _merge_flash_shots

    shots = [(0.0, 5.0), (5.0, 5.5), (5.5, 10.0)]  # middle = 0.5s
    result = _merge_flash_shots(shots, threshold=0.5)
    assert len(result) == 3


def test_flash_filter_empty_input() -> None:
    """Empty list → empty list."""
    from pipeline.ingest.shots import _merge_flash_shots

    assert _merge_flash_shots([], threshold=0.5) == []


def test_flash_filter_single_short_first_shot() -> None:
    """A short first shot (no predecessor) is kept as-is."""
    from pipeline.ingest.shots import _merge_flash_shots

    shots = [(0.0, 0.3), (0.3, 5.0)]
    result = _merge_flash_shots(shots, threshold=0.5)
    # First shot < 0.5s but no predecessor; keep it
    # Implementation may vary; the key constraint is no zero-duration shots
    assert len(result) >= 1
    for s, e in result:
        assert e > s


# ---------------------------------------------------------------------------
# Unit tests: _equal_split
# ---------------------------------------------------------------------------


def test_equal_split_30s_threshold_20s() -> None:
    """30s shot with threshold 20s → 2 equal sub-segments."""
    from pipeline.ingest.shots import _equal_split

    segs = _equal_split(0.0, 30.0, 20.0)
    assert len(segs) == 2
    assert segs[0] == pytest.approx((0.0, 15.0))
    assert segs[1] == pytest.approx((15.0, 30.0))


def test_equal_split_exact_multiple() -> None:
    """40s shot with threshold 20s → 2 sub-segments of exactly 20s each."""
    from pipeline.ingest.shots import _equal_split

    segs = _equal_split(0.0, 40.0, 20.0)
    assert len(segs) == 2
    assert segs[0][1] == pytest.approx(20.0)
    assert segs[1][1] == pytest.approx(40.0)


def test_equal_split_covers_full_range() -> None:
    """Sub-segments cover the full shot range with no gaps."""
    from pipeline.ingest.shots import _equal_split

    segs = _equal_split(5.0, 75.0, 20.0)  # 70s → ceil(70/20)=4 segs
    assert segs[0][0] == pytest.approx(5.0)
    assert segs[-1][1] == pytest.approx(75.0)
    for i in range(len(segs) - 1):
        assert segs[i][1] == pytest.approx(segs[i + 1][0])


# ---------------------------------------------------------------------------
# Unit tests: detect_shots with mocked TransNetV2
# ---------------------------------------------------------------------------


def test_detect_shots_returns_list_of_shot(tmp_path: Path, config: Config) -> None:
    """detect_shots returns a list[Shot]."""
    from pipeline.ingest.shots import detect_shots, Shot

    film = _make_film(tmp_path, duration=10.0, fps=10.0)
    total_frames = 100  # 10s * 10fps
    fake_single_pred, scenes_np = _mock_transnetv2([(0, 99)], total_frames)

    with patch("transnetv2_pytorch.TransNetV2") as MockCls:
        instance = MockCls.return_value
        instance.predict_video.return_value = (MagicMock(), fake_single_pred, MagicMock())
        MockCls.predictions_to_scenes.return_value = scenes_np

        result = detect_shots(film, config)

    assert isinstance(result, list)
    assert all(isinstance(s, Shot) for s in result)


def test_detect_shots_no_zero_duration_mocked(tmp_path: Path, config: Config) -> None:
    """No shot returned by detect_shots has t_end <= t_start (mocked model)."""
    from pipeline.ingest.shots import detect_shots

    film = _make_film(tmp_path, duration=10.0, fps=10.0)
    total_frames = 100
    fake_single_pred, scenes_np = _mock_transnetv2([(0, 99)], total_frames)

    with patch("transnetv2_pytorch.TransNetV2") as MockCls:
        instance = MockCls.return_value
        instance.predict_video.return_value = (MagicMock(), fake_single_pred, MagicMock())
        MockCls.predictions_to_scenes.return_value = scenes_np

        result = detect_shots(film, config)

    for shot in result:
        assert shot.t_end > shot.t_start, (
            f"Shot {shot.shot_id} has zero or negative duration: "
            f"{shot.t_start}-{shot.t_end}"
        )


def test_detect_shots_sub_segments_have_parent_shot_id(
    tmp_path: Path, config: Config
) -> None:
    """Sub-segments from a long shot (>threshold) carry a parent_shot_id."""
    from pipeline.ingest.shots import detect_shots

    # 30s shot, threshold=20 → sub-segmented
    film = _make_film(tmp_path, duration=30.0, fps=10.0)
    total_frames = 300
    fake_single_pred, scenes_np = _mock_transnetv2([(0, 299)], total_frames)

    with patch("transnetv2_pytorch.TransNetV2") as MockCls:
        instance = MockCls.return_value
        instance.predict_video.return_value = (MagicMock(), fake_single_pred, MagicMock())
        MockCls.predictions_to_scenes.return_value = scenes_np

        result = detect_shots(film, config)

    # All output shots come from sub-segmentation → parent_shot_id must be set
    assert len(result) >= 1
    assert all(s.parent_shot_id is not None for s in result), (
        "Every shot from a sub-segmented scene must carry a parent_shot_id"
    )


def test_detect_shots_short_scene_no_parent_id(tmp_path: Path, config: Config) -> None:
    """A short shot (< subsegment_min_duration) has parent_shot_id = None."""
    from pipeline.ingest.shots import detect_shots

    # 5s shot at 10fps → not sub-segmented (threshold=20)
    film = _make_film(tmp_path, duration=5.0, fps=10.0)
    total_frames = 50
    fake_single_pred, scenes_np = _mock_transnetv2([(0, 49)], total_frames)

    with patch("transnetv2_pytorch.TransNetV2") as MockCls:
        instance = MockCls.return_value
        instance.predict_video.return_value = (MagicMock(), fake_single_pred, MagicMock())
        MockCls.predictions_to_scenes.return_value = scenes_np

        result = detect_shots(film, config)

    assert len(result) == 1
    assert result[0].parent_shot_id is None


def test_detect_shots_shot_id_format(tmp_path: Path, config: Config) -> None:
    """shot_id follows '{film_id}_{index:04d}' format."""
    from pipeline.ingest.shots import detect_shots

    film = _make_film(tmp_path, duration=5.0, fps=10.0)
    total_frames = 50
    fake_single_pred, scenes_np = _mock_transnetv2([(0, 49)], total_frames)

    with patch("transnetv2_pytorch.TransNetV2") as MockCls:
        instance = MockCls.return_value
        instance.predict_video.return_value = (MagicMock(), fake_single_pred, MagicMock())
        MockCls.predictions_to_scenes.return_value = scenes_np

        result = detect_shots(film, config)

    assert len(result) >= 1
    for shot in result:
        assert shot.shot_id.startswith(film.film_id + "_"), (
            f"shot_id {shot.shot_id!r} must start with '{film.film_id}_'"
        )
        suffix = shot.shot_id[len(film.film_id) + 1:]
        assert len(suffix) == 4 and suffix.isdigit(), (
            f"shot_id suffix {suffix!r} must be exactly 4 digits"
        )


def test_detect_shots_keyframe_times_populated(tmp_path: Path, config: Config) -> None:
    """Every shot has at least one keyframe time."""
    from pipeline.ingest.shots import detect_shots

    film = _make_film(tmp_path, duration=10.0, fps=10.0)
    total_frames = 100
    fake_single_pred, scenes_np = _mock_transnetv2([(0, 99)], total_frames)

    with patch("transnetv2_pytorch.TransNetV2") as MockCls:
        instance = MockCls.return_value
        instance.predict_video.return_value = (MagicMock(), fake_single_pred, MagicMock())
        MockCls.predictions_to_scenes.return_value = scenes_np

        result = detect_shots(film, config)

    for shot in result:
        assert len(shot.keyframe_times) >= 1, (
            f"Shot {shot.shot_id} has no keyframe_times"
        )


# ---------------------------------------------------------------------------
# Integration tests: detect_shots on real test_clip
# ---------------------------------------------------------------------------


def test_detect_shots_integration_returns_shots(test_clip: Path, config: Config) -> None:
    """detect_shots returns a non-empty list of Shot objects for the 30s test clip."""
    from pipeline.ingest.shots import detect_shots, Shot
    from pipeline.ingest.probe import probe_film

    film = probe_film(test_clip, config)
    result = detect_shots(film, config)

    assert isinstance(result, list)
    assert len(result) >= 1
    assert all(isinstance(s, Shot) for s in result)


def test_detect_shots_count_on_test_clip(test_clip: Path, config: Config) -> None:
    """Shot count on the 30s SMPTE test clip is within ±2 of expected (2).

    Expected: TransNetV2 detects 1 scene in the static clip; with
    subsegment_min_duration=20 that scene is split into 2 sub-segments.
    """
    from pipeline.ingest.shots import detect_shots
    from pipeline.ingest.probe import probe_film

    EXPECTED = 2

    film = probe_film(test_clip, config)
    result = detect_shots(film, config)

    assert abs(len(result) - EXPECTED) <= 2, (
        f"Expected ~{EXPECTED} shots (±2), got {len(result)}"
    )


def test_detect_shots_no_zero_duration_on_clip(test_clip: Path, config: Config) -> None:
    """No shot has t_end <= t_start on the real test clip."""
    from pipeline.ingest.shots import detect_shots
    from pipeline.ingest.probe import probe_film

    film = probe_film(test_clip, config)
    result = detect_shots(film, config)

    for shot in result:
        assert shot.t_end > shot.t_start, (
            f"Shot {shot.shot_id} has zero or negative duration: "
            f"{shot.t_start:.3f}-{shot.t_end:.3f}"
        )


def test_detect_shots_keyframes_on_clip(test_clip: Path, config: Config) -> None:
    """All shots from the real test clip have valid keyframe_times."""
    from pipeline.ingest.shots import detect_shots
    from pipeline.ingest.probe import probe_film

    film = probe_film(test_clip, config)
    result = detect_shots(film, config)

    for shot in result:
        assert len(shot.keyframe_times) >= 1
        for kf in shot.keyframe_times:
            assert shot.t_start <= kf <= shot.t_end, (
                f"Keyframe {kf:.3f} is outside shot [{shot.t_start:.3f}, {shot.t_end:.3f}]"
            )
