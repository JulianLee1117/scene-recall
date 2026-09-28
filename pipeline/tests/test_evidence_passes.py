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


class _FakeBatchClient:
    """Just enough of the genai client for the batch transport."""

    def __init__(self):
        from types import SimpleNamespace
        self.deleted, self.created = [], []
        client = self

        class Batches:
            def create(self, *, model, src, config):
                client.created.append(len(src))
                return SimpleNamespace(name=f"batches/{len(client.created)}", state=SimpleNamespace(name="JOB_STATE_PENDING"))

        class Files:
            def delete(self, *, name):
                client.deleted.append(name)

        self.batches, self.files = Batches(), Files()


def _batch_plan(tmp_path):
    from pathlib import Path
    from pipeline.evidence.library import FilmRef
    film = FilmRef("a" * 64, "Test Film (2000)", Path(tmp_path / "film.mkv"), 100.0, 24.0)
    chunks = u.plan_chunks(_units([2.0] * 30), max_shots=10)
    return u.FilmPlan(film=film, units=[], chunks=chunks, inputs={}, dialogue=[], contexts=("ctx", "ctx"))


def test_submit_batches_leaves_a_failed_chunk_pending_and_submits_the_rest(config, tmp_path, monkeypatch):
    from types import SimpleNamespace
    plan, client, messages = _batch_plan(tmp_path), _FakeBatchClient(), []
    monkeypatch.setattr(u, "_client", lambda: client)
    monkeypatch.setattr(u, "plan_films", lambda *args, **kwargs: ([plan], []))
    monkeypatch.setattr(u, "render_proxy", lambda film, chunk, directory: (directory / f"{chunk.index}.mp4", (directory / f"{chunk.index}.mp4").write_bytes(b"x"))[0])

    def upload(_client, path, key):
        if key.endswith(f":1:{plan.chunks[1].digest()[:10]}"):
            raise RuntimeError("upload refused: 400 INVALID_ARGUMENT")
        return SimpleNamespace(name=f"files/{path.stem}", uri=f"uri/{path.stem}")

    monkeypatch.setattr(u, "_upload", upload)
    result = u.submit_batches(config, None, [plan.film], progress=messages.append)
    assert result["submitted"] == 2 and client.created == [2]
    assert any("part 2: not submitted" in message for message in messages)
    ledger = u._read_ledger(config)
    assert [request["chunk"] for request in ledger["jobs"][0]["requests"]] == [0, 2]


def test_upload_retries_transient_failures_but_not_permanent_ones(monkeypatch, tmp_path):
    from pipeline.evidence import understanding
    calls = []
    monkeypatch.setattr(understanding, "_UPLOAD_BACKOFF", (0.0, 0.0))

    def flaky(_client, _path, _name):
        calls.append(1)
        if len(calls) < 3:
            raise RuntimeError("503 UNAVAILABLE. The service is currently unavailable.")
        return "uploaded"

    monkeypatch.setattr(understanding, "_upload_once", flaky)
    assert understanding._upload(None, tmp_path / "x.mp4", "key") == "uploaded" and len(calls) == 3
    monkeypatch.setattr(understanding, "_upload_once", lambda *_: (_ for _ in ()).throw(ValueError("bad file")))
    with pytest.raises(ValueError):
        understanding._upload(None, tmp_path / "x.mp4", "key")


def test_run_batches_survives_a_transient_cycle_failure(monkeypatch):
    cycles, messages = [], []

    def cycle(*_args, spent, **_kwargs):
        cycles.append(1)
        if len(cycles) == 1:
            raise RuntimeError("503 UNAVAILABLE")
        return spent, len(cycles) == 3

    monkeypatch.setattr(u, "_batch_cycle", cycle)
    monkeypatch.setattr(u.time, "sleep", lambda _seconds: None)
    u.run_batches(None, None, [], progress=messages.append)
    assert len(cycles) == 3 and "cycle failed" in messages[0]
    monkeypatch.setattr(u, "_batch_cycle", lambda *_a, **_k: (_ for _ in ()).throw(KeyError("bug")))
    with pytest.raises(KeyError):
        u.run_batches(None, None, [], progress=messages.append)


def _chunk_response(chunk, *, continues=False):
    import json
    first, last = chunk.shots[0]["ordinal"], chunk.shots[-1]["ordinal"]
    output = {"shots": [{"shot": shot["ordinal"], "action": "walks"} for shot in chunk.shots],
              "scenes": [{"first_shot": first, "last_shot": last, "title": f"S{chunk.index}",
                          "continues_previous": continues}],
              "iconic": []}
    return {"text": json.dumps(output), "finish_reason": "STOP", "block_reason": None, "model_version": "m",
            "usage": {}, "elapsed_s": 1.0}


def test_refused_chunk_is_closed_and_the_film_still_completes(config, tmp_path):
    from pipeline.evidence import store
    plan = _batch_plan(tmp_path)
    plan = u.FilmPlan(film=plan.film, units=[s for c in plan.chunks for s in c.shots], chunks=plan.chunks,
                      inputs={}, dialogue=[], contexts=("ctx", "ctx"))
    prod = u.producer()
    first, refused, last = plan.chunks
    u.write_receipt(config, plan, prod, first, _chunk_response(first), model="m", variant="full", transport="batch", proxy={})
    u.write_refusal(config, plan, prod, refused, "PROHIBITED_CONTENT", model="m", transport="batch", proxy={})
    u.write_receipt(config, plan, prod, last, _chunk_response(last, continues=True), model="m", variant="full",
                    transport="batch", proxy={})
    assert u.pending_chunks(config, plan, prod) == []
    assert u.finalize(config, [plan], prod, "m", progress=lambda _message: None) == 1
    data = store.read_artifact(config.paths.assets_dir, plan.film.film_id, prod)["data"]
    assert len(data["shots"]) == 20                                  # the refused chunk's 10 shots have no records
    assert [scene["title"] for scene in data["scenes"]] == ["S0", "S2"]   # no scene is stitched across the gap
    assert [chunk["context"] for chunk in data["chunks"]] == ["full", "refused", "full"]


def test_safety_finish_is_a_refusal_the_caller_can_retry_without_the_plot(monkeypatch):
    monkeypatch.setattr(u, "call_model", lambda *_args: {"text": "", "block_reason": None,
                                                         "finish_reason": "FinishReason.PROHIBITED_CONTENT"})
    with pytest.raises(u.Blocked):
        u._call_with_retry(None, "m", None, 1.0, "prompt")
