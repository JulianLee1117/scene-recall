"""Tests for the understanding, measurement and synthesis evidence passes (no GPU or network)."""

from __future__ import annotations

import numpy as np
import pytest

from pipeline.evidence import measure, synthesis, understanding as u


def _units(durations: list[float]) -> list[dict]:
    units, t = [], 0.0
    for index, duration in enumerate(durations):
        units.append({"unit_id": f"film:{index:05d}", "t_start": t, "t_end": t + duration})
        t += duration
    return units


def test_plan_chunks_respects_shot_and_time_limits_and_numbers_shots_film_wide():
    chunks = u.plan_chunks(_units([2.0] * 250), max_shots=100, max_seconds=150)
    assert [len(chunk.shots) for chunk in chunks] == [75, 75, 75, 25]
    assert chunks[1].shots[0]["ordinal"] == 76
    assert chunks[0].fps == 2.0                                   # median shot under 3 s
    assert u.plan_chunks(_units([8.0] * 10))[0].fps == 1.0


def test_validate_chunk_normalizes_shots_scenes_and_peaks():
    chunk = u.plan_chunks(_units([4.0] * 40))[0]
    chunk = u.Chunk(0, chunk.shots[10:20])                        # shots S11-S20, chunk starts at 40 s
    output = {
        "shots": [
            {"shot": 11, "characters": ["Neo", "neo", "Trinity"], "action": "runs", "peak": "00:01.5", "fame": 5, "craft": 2,
             "camera": "pan"},
            {"shot": 11, "action": "duplicate is ignored"},
            {"shot": 12, "peak": "00:30.0", "fame": 1, "camera": "spin"},        # peak outside the shot
            {"shot": 99, "action": "unknown shot is ignored"},
        ] + [{"shot": n, "action": "x"} for n in range(13, 21)],
        "scenes": [
            {"first_shot": 13, "last_shot": 15, "title": "B"},
            {"first_shot": 11, "last_shot": 12, "title": "A", "continues_previous": True},
            {"first_shot": 18, "last_shot": 19, "title": "C"},              # gap S16-S17 closes into B
        ],
        "iconic": [{"shot": 11, "description": "the moment"}, {"shot": 7, "description": "outside chunk"}],
    }
    result, issues = u.validate_chunk(chunk, output)
    first = result["shots"]["film:00010"]
    assert first["characters"] == ["Neo", "Trinity"] and first["fame"] == 3 and first["camera_hint"] == "pan"
    assert first["peak_time"] == pytest.approx(41.5)
    second = result["shots"]["film:00011"]
    assert second["peak_time"] is None and second["camera_hint"] == "unclear"
    assert [(s["first_shot"], s["last_shot"], s["title"]) for s in result["scenes"]] == [(11, 12, "A"), (13, 17, "B"), (18, 20, "C")]
    assert result["scenes"][0]["continues_previous"] is True
    assert [row["unit_id"] for row in result["iconic"]] == ["film:00010"]
    assert issues == []


def test_merge_chunks_joins_continued_scenes_and_assigns_scene_indexes():
    units = _units([5.0] * 6)
    chunks = u.plan_chunks(units, max_shots=3)
    receipts = []
    for chunk, scenes in zip(chunks, [
        [{"first_shot": 1, "last_shot": 2, "title": "Opening"}, {"first_shot": 3, "last_shot": 3, "title": "Chase"}],
        [{"first_shot": 4, "last_shot": 5, "title": "Chase cont.", "continues_previous": True},
         {"first_shot": 6, "last_shot": 6, "title": "Aftermath"}],
    ]):
        output = {"shots": [{"shot": s["ordinal"], "action": "a"} for s in chunk.shots], "scenes": scenes, "iconic": []}
        result, _issues = u.validate_chunk(chunk, output)
        receipts.append({"chunk": chunk.index, "result": result})
    merged = u.merge_chunks(units, receipts)
    assert [(s["title"], s["first_shot"], s["last_shot"]) for s in merged["scenes"]] == [
        ("Opening", 1, 2), ("Chase", 3, 5), ("Aftermath", 6, 6)]
    assert merged["scenes"][1]["t_start"] == 10.0 and merged["scenes"][1]["t_end"] == 25.0
    assert [merged["shots"][unit["unit_id"]]["scene"] for unit in units] == [0, 0, 1, 1, 1, 2]


def test_cost_counts_thinking_tokens_and_batch_discount():
    usage = {"prompt_token_count": 1_000_000, "candidates_token_count": 100_000, "thoughts_token_count": 100_000}
    assert u.cost_usd("gemini-3.8-flash", usage) == pytest.approx(0.75 + 0.75)
    assert u.cost_usd("gemini-3.8-flash", usage, batch=True) == pytest.approx(0.75)
    assert u.cost_usd("unknown-model", usage) == 0.0


def test_pair_label_reads_image_motion_as_opposite_camera_motion():
    assert measure.pair_label(np.array([-0.05, 0.0, 0.0, 0.0]), True) == "pan_right"
    assert measure.pair_label(np.array([0.0, 0.05, 0.0, 0.0]), True) == "tilt_up"
    assert measure.pair_label(np.array([0.0, 0.0, 0.05, 0.0]), True) == "push_in"
    assert measure.pair_label(np.array([0.0, 0.0, -0.05, 0.0]), True) == "pull_out"
    assert measure.pair_label(np.array([0.001, 0.0, 0.0, 0.0]), True) == "static"
    assert measure.pair_label(np.array([0.3, 0.0, 0.0, 0.0]), False) == "unknown"


def test_camera_segments_smooth_single_frame_flicker_and_flag_handheld():
    steady = [np.array([-0.05, 0.0, 0.0, 0.0])] * 12
    steady[5] = np.array([0.0, 0.0, 0.0, 0.0])                 # one static pair inside a pan
    result = measure.camera_segments([i / 6 for i in range(12)], steady, [True] * 12, 1 / 6)
    assert result["dominant"] == "pan_right" and len(result["segments"]) == 1 and result["moving"] == 1.0
    rng = np.random.default_rng(0)
    shaky = [np.array([*rng.normal(0, 0.012, 2), 0.0, 0.0]) for _ in range(24)]
    assert measure.camera_segments([i / 6 for i in range(24)], shaky, [True] * 24, 1 / 6)["dominant"] == "handheld"
    assert measure.camera_segments([0.0, 0.2], [np.zeros(4)] * 2, [False, False], 0.2)["dominant"] == "unknown"


def test_main_subject_track_prefers_people_and_reports_direction():
    frames = [
        [("person", 0.9, (0.10, 0.30, 0.20, 0.60)), ("car", 0.9, (0.0, 0.0, 0.9, 0.9))],
        [("person", 0.9, (0.30, 0.30, 0.40, 0.60))],
        [("person", 0.9, (0.50, 0.30, 0.60, 0.60)), ("person", 0.8, (0.05, 0.1, 0.1, 0.2))],
    ]
    subject = measure.track_main_subject(frames)
    assert subject["class"] == "person" and subject["direction"] == "right"
    growing = [[("person", 0.9, (0.4, 0.4, 0.5, 0.5))], [("person", 0.9, (0.3, 0.3, 0.7, 0.7))]]
    assert measure.track_main_subject(growing)["direction"] == "toward"
    assert measure.track_main_subject([[], []]) is None


def test_align_quotes_finds_famous_lines_across_subtitle_cues():
    dialogue = [
        {"start": 10.0, "end": 12.0, "text": "You talkin' to me?"},
        {"start": 50.0, "end": 52.0, "text": "I'm gonna make him an offer"},
        {"start": 52.2, "end": 54.0, "text": "he can't refuse."},
        {"start": 70.0, "end": 72.0, "text": "Nice weather today."},
    ]
    hits = synthesis.align_quotes(["I'm gonna make him an offer he can't refuse.", "Here's looking at you, kid."], dialogue,
                                  min_score=0.72)
    assert len(hits) == 1 and hits[0]["start"] == 50.0 and hits[0]["end"] == 54.0


def test_distinctiveness_ranks_the_outlier_highest():
    rng = np.random.default_rng(1)
    base = rng.normal(size=16)
    vectors = np.array([base + rng.normal(scale=0.05, size=16) for _ in range(20)] + [rng.normal(size=16)])
    scores = synthesis.distinctiveness(vectors, neighbors=5)
    assert int(np.argmax(scores)) == 20


def test_content_box_crops_symmetric_letterbox_bars_only():
    frame = np.full((360, 640, 3), 120, dtype=np.uint8)
    frame[:40] = 0
    frame[-40:] = 0
    y0, y1, x0, x1 = measure.content_box(frame)
    assert (y0, y1, x0, x1) == (40, 320, 0, 640)
    stats = measure.Models(".", device="cpu").frame_stats(__import__("torch").from_numpy(frame[None]))
    assert stats["brightness"][0] == pytest.approx(120 / 255, abs=0.01)   # bars excluded
    assert stats["sharpness"][0] == pytest.approx(0.0, abs=1e-3)           # the bar edge is not detail
    night = np.full((360, 640, 3), 120, dtype=np.uint8)
    night[:120] = 0                                   # dark sky: one-sided, not a letterbox
    assert measure.content_box(night) == (0, 360, 0, 640)


def test_slow_sustained_moves_accumulate_into_a_label():
    times = [i / 6 for i in range(120)]                       # a 20 s shot
    creeping = [np.array([0.0, 0.0, 0.012, 0.0])] * 120       # 1.2%/s zoom: under the per-pair threshold
    result = measure.camera_segments(times, creeping, [True] * 120, 1 / 6)
    assert result["dominant"] == "push_in" and result["slow"] is True
    drifting = [np.array([-0.009, 0.0, 0.0, 0.0])] * 120       # image drifts left: the camera pans right
    assert measure.camera_segments(times, drifting, [True] * 120, 1 / 6)["dominant"] == "pan_right"
    rng = np.random.default_rng(2)
    noise = [np.array([0.0, 0.0, value, 0.0]) for value in rng.normal(0, 0.004, 120)]
    assert measure.camera_segments(times, noise, [True] * 120, 1 / 6)["dominant"] == "static"
    series = [[t, *camera, 0.01, 1] for t, camera in zip(times, creeping)]
    assert measure.camera_from_series(series)["dominant"] == "push_in"
