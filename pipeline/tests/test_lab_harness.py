"""Editor harness v2: music map, pools and the beat-lattice assembly (no models or media)."""

from __future__ import annotations

import numpy as np
import pytest

from pipeline.lab.harness import assemble as asm
from pipeline.lab.harness import music_map
from pipeline.lab.harness.pools import Candidate, apply_evidence, gather


def _clicks(duration=8.0, period=0.5, rate=22050):
    signal = np.random.default_rng(0).normal(0, 0.002, int(duration * rate)).astype(np.float32)
    for t in np.arange(0.25, duration, period):
        index = int(t * rate)
        signal[index:index + 200] += np.hanning(200).astype(np.float32) * 0.8
    return signal, rate


def _map(duration=8.0, period=0.5, start=10.0, analysis=None):
    signal, rate = _clicks(duration, period)
    beats = [start + 0.25 + i * period for i in range(int((duration - 0.25) / period) + 1)]
    rhythm = {"beats": beats, "downbeats": beats[::4]}
    return music_map.build(signal, rate, {"start": start, "end": start + duration}, rhythm, analysis)


def test_music_map_finds_click_accents_on_the_beat_and_snaps_sections_to_downbeats():
    analysis = {"segments": [{"start": 10.0, "end": 13.9, "energy": 0.3, "feeling": "calm"},
                             {"start": 13.9, "end": 18.0, "energy": 0.8, "feeling": "driving"}]}
    m = _map(analysis=analysis)
    assert len(m.accents) >= 12 and all(accent["on_beat"] for accent in m.accents)
    assert m.beat_period() == pytest.approx(0.5)
    assert [round(s["start"], 2) for s in m.sections] == [10.0, 14.25]      # 13.9 snaps to the downbeat at 14.25
    assert m.sections[-1]["end"] == 18.0 and m.summary()["bars"] == 4
    assert m.strongest_accent(10.3, 10.9)["time"] == pytest.approx(10.75, abs=0.02)


def _candidate(unit, *, start=100.0, length=6.0, peak=None, relevance=0.8, film="f1", scene=None, cuts=(), **extra):
    return Candidate(unit_id=unit, film_id=film, film_title=film, t_start=start, t_end=start + length,
                     relevance=relevance, peak_time=peak, scene_id=scene, hidden_cuts=list(cuts), **extra)


def _pool(count=30):
    return [_candidate(f"u{i}", start=100.0 * i, length=5.0, peak=100.0 * i + 2.0, relevance=1.0 - i / 60,
                       film=f"f{i % 6}", scene=f"s{i}") for i in range(count)]


def test_assembly_covers_the_passage_with_unique_shots_within_pace_bounds():
    m = _map()
    acts = [asm.Act(10.0, 14.25, _pool(), pace="kinetic"), asm.Act(14.25, 18.0, _pool(), pace="balanced")]
    result = asm.assemble(m, acts)
    assert result[0].start == 10.0 and result[-1].end == 18.0
    assert all(a.end == b.start for a, b in zip(result, result[1:]))
    assert len({p.candidate.unit_id for p in result}) == len(result)
    for placement in result:
        low, _target, high = asm.PACE["kinetic" if placement.start < 14.25 else "balanced"]
        assert low - 1e-6 <= placement.end - placement.start <= high + 1e-6
        assert placement.candidate.t_start <= placement.source_start
        assert placement.source_end <= placement.candidate.t_end + 1e-6
    assert any(p.end == 14.25 for p in result)                     # act boundary is a cut


def test_peak_lands_on_the_spans_strongest_accent():
    m = _map()
    act = asm.Act(10.0, 18.0, [_candidate("hero", peak=103.0)], pace="balanced")
    context = asm.span_context(m, 10.25, 11.75)
    placement = asm.place(act.pool[0], 10.25, 11.75, act, {}, context)
    accent = placement.accent
    assert accent is not None
    assert placement.source_start + (accent["time"] - 10.25) == pytest.approx(103.0, abs=1e-3)
    assert placement.parts["alignment"] > 0.5


def test_windows_never_straddle_hidden_cuts_and_locked_shots_stay():
    m = _map()
    cut = _candidate("cut", start=200.0, length=6.0, peak=203.5, relevance=1.0, cuts=(202.0,))
    placement = asm.place(cut, 10.25, 11.75, asm.Act(10.0, 18.0, [cut]), {}, asm.span_context(m, 10.25, 11.75))
    assert placement.source_start >= 202.0 or placement.source_end <= 202.0
    locked = asm.Fixed(12.25, 13.25, _candidate("locked", start=500.0), 501.0)
    result = asm.assemble(m, [asm.Act(10.0, 18.0, _pool(), pace="kinetic")], fixed=[locked])
    kept = [p for p in result if p.fixed]
    assert len(kept) == 1 and (kept[0].start, kept[0].end, kept[0].source_start) == (12.25, 13.25, 501.0)
    assert all(not (p.start < 13.25 and p.end > 12.25) for p in result if not p.fixed)


def test_fill_mode_keeps_every_cut_and_avoids_the_same_scene_back_to_back():
    m = _map()
    boundaries = [10.0, 11.0, 12.5, 14.0, 16.0, 18.0]
    pool = [_candidate(f"a{i}", start=100.0 * i, relevance=1.0, scene="same") for i in range(4)] + \
           [_candidate(f"b{i}", start=1000.0 + 100.0 * i, relevance=0.9, scene=f"other{i}") for i in range(4)]
    result = asm.assemble(m, [asm.Act(10.0, 18.0, pool)], boundaries=boundaries)
    assert [p.start for p in result] + [result[-1].end] == boundaries
    scenes = [p.candidate.scene_id for p in result]
    assert all(not (a == b == "same") for a, b in zip(scenes, scenes[1:]))
    options = asm.alternatives(result, [asm.Act(10.0, 18.0, pool)], m)
    assert all(option.candidate.unit_id not in {p.candidate.unit_id for p in result} for row in options for option in row)
    assert asm.reason(result[1], result[0])


def test_pools_merge_queries_by_best_rank_and_attach_evidence():
    rows = {"night": [{"unit_id": "x", "film_id": "f", "t_start": 1.0, "t_end": 4.0},
                      {"unit_id": "y", "film_id": "f", "t_start": 5.0, "t_end": 9.0}],
            "rain": [{"unit_id": "y", "film_id": "f", "t_start": 5.0, "t_end": 9.0}]}
    calls = []

    def search(query, db, config, **kwargs):
        calls.append((query, kwargs["preset"]))
        return rows[query]

    class NoTables:
        def table_names(self, **_):
            return []

    pools = gather(NoTables(), None, [{"queries": ["night", "rain"], "fame": "fresh"}], search=search)
    assert calls == [("night", "gems"), ("rain", "gems")]
    assert [c.unit_id for c in pools[0]] == ["y", "x"] and pools[0][1].relevance == 1.0
    assert pools[0][0].relevance > 1.0                             # found by both queries
    assert pools[0][0].queries == ["night", "rain"]
    candidate = _candidate("z", start=10.0, length=4.0)
    apply_evidence(candidate, {"scene_id": "s", "peak_time": 12.0, "camera": "pan_left", "camera_reliability": 0.9,
                               "camera_segments": '[[10.0, 14.0, "pan_left"]]', "subject_size": 0.001,
                               "subject": '{"class": "toothbrush", "center": [0.5, 0.5]}', "iconic": True})
    assert candidate.peak_time == 12.0 and candidate.camera_at(10.0, 11.0) == "pan_left"
    assert candidate.subject_start is None                          # tiny odd detections are not subjects


def test_concept_keeps_one_act_per_section_and_falls_back_to_listening():
    from pipeline.lab.harness.concept import Concept, resolve_acts
    m = _map(analysis={"segments": [{"start": 10.0, "end": 14.0, "energy": 0.3, "feeling": "calm"},
                                    {"start": 14.0, "end": 18.0, "energy": 0.8, "feeling": "driving"}]})
    concept = Concept.model_validate({"concept": "c", "motifs": [], "acts": [
        {"section": 1, "intent": "chase", "queries": ["car chase at night", "car chase at night", "running feet"],
         "fame": "anchor", "pace": "rapid"}]})
    payload = {"pacing_preference": "balanced",
               "sections": [{"listening_query": "quiet window", "feeling": "calm"}, {"listening_query": "", "feeling": "driving"}]}
    acts = resolve_acts(concept, m, payload)
    assert [(a["intent"], a["planned"]) for a in acts] == [("calm", False), ("chase", True)]
    assert acts[0]["queries"] == ["quiet window"] and acts[0]["pace"] == "balanced"
    assert acts[1]["queries"] == ["car chase at night", "running feet"]
    assert acts[0]["end"] == acts[1]["start"]


def test_review_applies_offered_unique_swaps_only(monkeypatch, config):
    from pipeline.lab import music as hosted
    from pipeline.lab.harness import review as rv
    chosen = [asm.Placement(10.0 + i, 11.0 + i, _candidate(f"c{i}"), 100.0, 1.0) for i in range(3)]
    options = [[asm.Placement(10.0, 11.0, _candidate("alt0"), 0.0, 0.5)],
               [asm.Placement(11.0, 12.0, _candidate("c2"), 0.0, 0.5)],        # already used in slot 2
               []]
    monkeypatch.setattr(hosted, "_hosted_json", lambda *_a, **_k: {"notes": "n", "swaps": [
        {"slot": 0, "option": 1, "reason": "rhyme"}, {"slot": 1, "option": 1, "reason": "dup"},
        {"slot": 2, "option": 1, "reason": "missing"}]})
    concept = {"concept": "c", "motifs": [], "acts": [{"start": 10.0, "end": 13.0, "intent": "i"}]}
    document = {"passage": {"start": 10.0, "end": 13.0}, "editor_direction": None, "brief": ""}
    result, receipt = rv.review(document, concept, chosen, options, config, "job", lambda _m: None)
    assert [p.candidate.unit_id for p in result] == ["alt0", "c1", "c2"]
    assert [row["slot"] for row in receipt["applied"]] == [0]
    assert {row["why"] for row in receipt["skipped"]} == {"shot already used", "not offered"}


def test_timeline_is_a_valid_project_edit_with_a_ui_timing_receipt():
    from pipeline.lab.harness.run import build_timeline, timing_receipt
    from pipeline.lab.models import ProjectDocument
    m = _map()
    acts_spec = [{"start": 10.0, "end": 18.0, "intent": "i", "queries": ["q"], "fame": "any", "pace": "kinetic"}]
    pool = _pool()
    for candidate in pool:
        candidate.queries = ["q"]
    placements = asm.assemble(m, [asm.Act(10.0, 18.0, pool, pace="kinetic")])
    options = asm.alternatives(placements, [asm.Act(10.0, 18.0, pool, pace="kinetic")], m)
    document = {"track": {"id": "t", "name": "Song", "duration": 60.0}, "passage": {"start": 10.0, "end": 18.0},
                "fps": 24, "analysis": {"segments": []}, "clips": []}
    slots, clips = build_timeline(document, placements, options, acts_spec)
    document.update(clips=clips, music_timeline={"track_id": "t", "passage": {"start": 10.0, "end": 18.0},
                                                 "slots": slots, "provisional_timing": None})
    ProjectDocument.model_validate(document)
    assert all(slot["direction_source"] == "ai" and slot["resolved_search"]["min_duration"] > 0 for slot in slots)
    assert all(1 <= len(slot["alternatives"]) <= 6 for slot in slots)
    receipt = timing_receipt(document, slots, acts_spec, "id")
    assert receipt["end_frames"][-1] == 192 and len(receipt["end_frames"]) == len(slots)
    assert receipt["end_frames"] == sorted(set(receipt["end_frames"]))


def test_critique_issues_become_bans_and_pace_scales():
    from pipeline.lab.harness.critique import PACE_STEP, Critique, constraints
    placements = [asm.Placement(10.0, 11.0, _candidate("a"), 0.0, 1.0), asm.Placement(11.0, 12.5, _candidate("b"), 0.0, 1.0),
                  asm.Placement(12.5, 14.0, _candidate("c"), 0.0, 1.0, fixed=True)]
    acts = [asm.Act(10.0, 12.0, []), asm.Act(12.0, 14.0, [])]
    critique = Critique.model_validate({"summary": "s", "issues": [
        {"start": 1.2, "end": 1.4, "kind": "weak_shot", "note": "dull"},
        {"start": 2.8, "end": 3.2, "kind": "repetitive", "note": "locked shot is never banned"},
        {"start": 0.1, "end": 0.5, "kind": "too_fast", "note": "busy"},
        {"start": 3.0, "end": 3.5, "kind": "off_beat", "note": "recorded only"}]})
    rules = constraints(critique, placements, acts, origin=10.0)
    assert rules["banned"] == ["b"]
    assert rules["pace_scales"] == [PACE_STEP, 1.0]


def test_dark_stretches_come_from_sampled_brightness_and_windows_avoid_them():
    from pipeline.evidence.measure import dark_spans
    frames = [[100.0 + i * 0.25, 50.0, 0.4 if i < 8 or i > 13 else 0.01, []] for i in range(20)]
    spans = dark_spans(frames)
    assert spans == [[101.875, 103.375]]                     # 6 dark samples at 0.25 s, widened by half a spacing
    assert dark_spans([[0.0, 1.0, 0.01, []], [0.25, 1.0, 0.5, []]]) == []   # a single dark sample is not a fade
    fade = _candidate("fade", start=100.0, length=5.0, peak=101.0, dark_spans=[(101.875, 103.375)])
    assert asm._segments(fade) == [(100.0, 101.875), (103.375, 105.0)]
    m = _map()
    placement = asm.place(fade, 10.25, 11.75, asm.Act(10.0, 18.0, [fade]), {}, asm.span_context(m, 10.25, 11.75))
    assert placement.source_end <= 101.875 + 1e-6              # 1.5 s fits only before the fade


def test_fill_keeps_placed_neighbours_and_gives_user_searches_their_own_pool():
    m = _map()
    boundaries = [10.0, 11.0, 12.5, 14.0, 16.0, 18.0]
    placed = asm.Fixed(12.5, 14.0, _candidate("placed", start=900.0), 900.5)
    user_pool = [_candidate(f"user{i}", start=2000.0 + 100 * i, relevance=0.5) for i in range(3)]
    act = asm.Act(10.0, 18.0, _pool(), pace="balanced")
    result = asm.assemble(m, [act], fixed=[placed], boundaries=boundaries,
                          overrides={(14.0, 16.0): asm.Act(14.0, 16.0, user_pool)})
    assert [round(p.start, 2) for p in result] == boundaries[:-1]
    kept = result[2]
    assert kept.fixed and kept.candidate.unit_id == "placed" and kept.source_start == 900.5
    assert result[3].candidate.unit_id.startswith("user")            # the slot's own search, not the act pool
    assert all(not p.candidate.unit_id.startswith("user") for i, p in enumerate(result) if i != 3)
