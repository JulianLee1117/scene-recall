"""Editor harness v2: music map, pools and the beat-lattice assembly (no models or media)."""

from __future__ import annotations

from dataclasses import replace

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
    assert sorted(calls) == [("night", "gems"), ("rain", "gems")]
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
         "fame": "anchor", "pace": "rapid", "moves": []}]})
    payload = {"pacing_preference": "balanced",
               "sections": [{"listening_query": "quiet window", "feeling": "calm"}, {"listening_query": "", "feeling": "driving"}]}
    acts = resolve_acts(concept, m, payload)
    assert [(a["intent"], a["planned"]) for a in acts] == [("calm", False), ("chase", True)]
    assert acts[0]["queries"] == ["quiet window"] and acts[0]["pace"] == "balanced"
    assert acts[1]["pace"] == "rapid"                                         # faster than the preference is free
    from pipeline.lab.harness.concept import floor_pace
    assert floor_pace("patient", "rapid") == "kinetic" and floor_pace("kinetic", "rapid") == "kinetic"
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


def test_transitions_penalize_letterbox_and_grade_jumps_between_films():
    scope = _candidate("scope", film="a", aspect=2.39, grade=(0.3, 0.4, 0.05))
    academy = _candidate("academy", film="b", aspect=1.33, grade=(0.3, 0.4, 0.05))
    graded = _candidate("graded", film="c", aspect=2.39, grade=(0.6, 0.1, -0.1))
    same = _candidate("same", film="a", aspect=1.33)
    first = asm.Placement(10.0, 11.0, scope, 0.0, 1.0)
    _, parts = asm.transition(first, asm.Placement(11.0, 12.0, academy, 0.0, 1.0))
    assert parts["aspect"] == pytest.approx(-0.12) and "grade" in parts and parts["grade"] == 0
    _, parts = asm.transition(first, asm.Placement(11.0, 12.0, graded, 0.0, 1.0))
    assert "aspect" not in parts or parts["aspect"] == 0 and parts["grade"] == pytest.approx(-0.12)
    _, parts = asm.transition(first, asm.Placement(11.0, 12.0, same, 0.0, 1.0))
    assert "aspect" not in parts                                        # one film's own shots never pay for it


def test_footage_setting_bounds_act_fame_and_reaches_editor_searches(monkeypatch):
    from pipeline.lab import music, search_plan
    from pipeline.lab.harness.concept import within_preference
    from pipeline.lab.models import PlannerSettings
    assert PlannerSettings().footage == "balanced"
    assert within_preference("fresh", "famous") == "any" and within_preference("anchor", "gems") == "any"
    assert within_preference("anchor", "famous") == "anchor" and within_preference("fresh", "balanced") == "fresh"
    seen = []
    monkeypatch.setattr(music, "retrieve_edit_candidates", lambda *args, preset="balanced": seen.append(preset) or [])
    resolved = {"clauses": [{"kind": "text", "facet": "all", "text": "rain", "reference_id": None}], "references": []}
    search_plan.execute_search(resolved, {"planner_settings": {"footage": "gems"}, "film_ids": []}, None, None)
    search_plan.execute_search(resolved, {"film_ids": []}, None, None)
    assert seen == ["gems", "balanced"]


def test_editor_windows_stay_on_the_picture_the_evidence_describes():
    dissolve = _candidate("dissolve", start=100.0, length=12.0, peak=108.2, focus=(106.0, 112.0))
    assert asm._segments(dissolve) == [(106.0, 112.0)]
    m = _map()
    placement = asm.place(dissolve, 10.0, 13.0, asm.Act(10.0, 18.0, [dissolve]), {}, asm.span_context(m, 10.0, 13.0))
    assert placement.source_start >= 106.0 - 1e-6           # never back across the dissolve
    row = _candidate("row", start=100.0, length=12.0)
    apply_evidence(row, {"focus_start": 99.0, "focus_end": 106.5})
    assert row.focus == (100.0, 106.5)                       # clamped to the shot


class _Matcher:
    """A stand-in for ``matchcuts.CutMatcher``: every cut into ``favourite`` matches perfectly."""

    strength = 1.0

    def __init__(self, favourite):
        self.favourite = favourite
        self.calls = 0

    def row(self, unit_id, time):
        return unit_id

    def scores(self, out_row, in_rows):
        self.calls += 1
        return np.array([1.0 if row == self.favourite else 0.0 for row in in_rows])


def test_a_measured_match_cut_wins_the_following_span():
    m = _map()
    pool = _pool(12)
    plain = asm.assemble(m, [asm.Act(10.0, 18.0, pool, pace="kinetic")], boundaries=[10.0, 12.0, 14.0, 16.0, 18.0])
    favourite = next(unit for unit in (c.unit_id for c in pool) if unit not in {p.candidate.unit_id for p in plain})
    matcher = _Matcher(favourite)
    matched = asm.assemble(m, [asm.Act(10.0, 18.0, pool, pace="kinetic")], boundaries=[10.0, 12.0, 14.0, 16.0, 18.0],
                           matcher=matcher)
    assert matcher.calls > 0 and favourite in [p.candidate.unit_id for p in matched[1:]]
    index = [p.candidate.unit_id for p in matched].index(favourite)
    _, parts = asm.transition(matched[index - 1], matched[index], 1.0, matcher.strength)
    assert parts["match"] == 1.0 and "eye_trace" not in parts
    assert "matches the previous shot" in asm.reason(matched[index], matched[index - 1], matcher)


def test_a_section_takes_any_pace_and_moves_split_it_on_the_beat():
    from pipeline.lab.harness.concept import Concept, resolve_acts, section_shape
    m = _map(analysis={"segments": [{"start": 10.0, "end": 14.0, "energy": 0.3, "feeling": "calm"},
                                    {"start": 14.0, "end": 18.0, "energy": 0.8, "feeling": "driving"}]})
    concept = Concept.model_validate({"concept": "c", "motifs": [], "acts": [
        {"section": 0, "intent": "stillness", "queries": ["empty street at dawn"], "fame": "any", "pace": "patient",
         "moves": [{"kind": "flash", "start": 1.3, "end": 2.2, "query": "faces in quick succession"},
                   {"kind": "flash", "start": 1.6, "end": 2.0, "query": ""},       # overlaps the first: dropped
                   {"kind": "hold", "start": 3.0, "end": 3.4, "query": ""}]},      # too short: dropped
        {"section": 1, "intent": "chase", "queries": ["car chase at night"], "fame": "any", "pace": "rapid",
         "moves": [{"kind": "hold", "start": 1.85, "end": 3.65, "query": "a man stares out to sea"}]}]})
    payload = {"pacing_preference": "patient", "sections": [{"listening_query": "", "feeling": "calm"},
                                                            {"listening_query": "", "feeling": "driving"}]}
    acts = resolve_acts(concept, m, payload)
    assert [(a["pace"], a["move"]) for a in acts] == [
        ("patient", None), ("flash", "flash"), ("patient", None), ("rapid", None), ("hold", "hold")]
    flash, hold = acts[1], acts[4]
    assert (flash["start"], flash["end"]) == (11.25, 12.25)                    # snapped to beats
    assert flash["queries"] == ["faces in quick succession", "empty street at dawn"]
    assert hold["end"] == 18.0                                                 # the sliver to the section end joins it
    patient = Concept.model_validate({"concept": "c", "motifs": [], "acts": [
        {"section": 0, "intent": "still", "queries": ["sea"], "fame": "any", "pace": "patient",
         "moves": [{"kind": "hold", "start": 1.25, "end": 3.75, "query": ""}]}]})
    held = resolve_acts(patient, m, payload)[0]
    assert (held["pace"], held["start"], held["end"]) == ("hold", 10.0, 14.25)  # 1.25 s and 0.5 s are under a patient shot
    assert all(a["end"] == b["start"] for a, b in zip(acts, acts[1:]))
    assert acts[0]["start"] == 10.0 and acts[-1]["end"] == 18.0
    shape = section_shape(m, 10.0, 14.25)                                      # what the planner sees per section
    assert len(shape["loudness_per_second"]) == 5 and all(0 <= t < 4.25 for t in shape["accents_s"])


def test_a_flash_cuts_in_frames_and_a_hold_is_one_shot():
    m = _map()
    pool = _pool()
    acts = [asm.Act(10.0, 12.25, pool, pace="patient"), asm.Act(12.25, 13.25, pool, pace="flash"),
            asm.Act(13.25, 14.25, pool, pace="balanced"), asm.Act(14.25, 18.0, pool, pace="hold")]
    result = asm.assemble(m, acts)
    flash = [p for p in result if 12.25 <= p.start < 13.25]
    assert len(flash) >= 3 and all(asm.FLASH[0] - 1e-6 <= p.end - p.start <= asm.FLASH[2] + 1e-6 for p in flash)
    held = [p for p in result if p.start >= 14.25]
    assert len(held) == 1 and (held[0].start, held[0].end) == (14.25, 18.0)
    short = [_candidate(f"s{i}", start=100.0 * i, length=2.5, relevance=1.0, film=f"f{i % 4}") for i in range(12)]
    split = [p for p in asm.assemble(m, [asm.Act(14.25, 18.0, short, pace="hold")])]
    assert len(split) == 2 and all(p.end - p.start >= 1.5 - 1e-6 for p in split)   # nothing covers 3.75 s


def test_pools_run_each_shared_search_once():
    calls = []

    def search(query, db, config, **kwargs):
        calls.append(query)
        return [{"unit_id": query, "film_id": "f", "t_start": 1.0, "t_end": 4.0}]

    class NoTables:
        def table_names(self, **_):
            return []

    acts = [{"queries": ["night", "rain"], "fame": "any"}, {"queries": ["flash", "night", "rain"], "fame": "any"}]
    pools = gather(NoTables(), None, acts, search=search)
    assert sorted(calls) == ["flash", "night", "rain"]
    assert [c.unit_id for c in pools[1]] == ["flash", "night", "rain"]


def test_cast_leads_each_pool_in_order_and_each_shot_is_cast_once():
    from pipeline.lab.harness.cast import Cast, apply_cast
    pool_a, pool_b = _pool(6), [_candidate(f"b{i}", start=900.0 + 10 * i) for i in range(3)]
    acts = [asm.Act(10.0, 14.25, pool_a), asm.Act(14.25, 18.0, pool_b + [pool_a[5]])]
    keys = {f"a0-{i}": c for i, c in enumerate(pool_a)} | {f"a1-{i}": c for i, c in enumerate(acts[1].pool)} | {
        "k0": _candidate("key", start=5000.0)}
    parsed = Cast.model_validate({"notes": "n", "acts": [
        {"act": 0, "shots": ["a0-3", "k0", "a0-5", "nope"], "peak": "k0", "reason": "build"},
        {"act": 1, "shots": ["a1-3", "a1-0"], "peak": "", "reason": "a1-3 is a0-5, already cast"}]})
    result, receipt = apply_cast(acts, parsed, keys)
    first = result[0].pool
    assert [c.unit_id for c in first[:3]] == ["u3", "key", "u5"] and [c.cast_rank for c in first[:3]] == [0, 1, 2]
    assert first[1].cast_peak and not first[0].cast_peak and first[3].cast_rank is None
    assert [c.unit_id for c in result[1].pool if c.cast_rank is not None] == ["b0"]
    assert "u5" not in [c.unit_id for c in result[1].pool]                    # cast shots leave other acts' pools
    assert receipt["acts"][0] == {"act": 0, "cast": ["u3", "key", "u5"], "peak": "key"}


def test_assembly_keeps_cast_order_and_prefers_cast_shots():
    m = _map()
    pool = _pool(20)
    cast = [replace(pool[i], cast_rank=rank, cast_peak=rank == 2) for rank, i in enumerate((15, 9, 4, 12))]
    result = asm.assemble(m, [asm.Act(10.0, 18.0, cast + [c for c in pool if c not in cast], pace="balanced")])
    ranks = [p.candidate.cast_rank for p in result if p.candidate.cast_rank is not None]
    assert len(ranks) >= 3 and ranks == sorted(ranks)


def test_a_flash_cuts_on_a_steady_subdivision():
    m = _map()                                                                 # 0.5 s beats: two shots per beat
    assert asm.flash_division(m) == 2
    result = asm.assemble(m, [asm.Act(10.0, 12.25, _pool(), pace="patient"), asm.Act(12.25, 14.25, _pool(), pace="flash"),
                              asm.Act(14.25, 18.0, _pool(), pace="balanced")])
    flash = [round(p.end - p.start, 3) for p in result if 12.25 <= p.start < 14.25]
    assert flash == [0.25] * 8


def test_track_shape_reads_the_whole_song_in_four_second_steps(tmp_path):
    import wave
    from pipeline.lab.harness.song import track_shape
    rate = 8000
    quiet = np.full(8 * rate, 0.01, np.float32)
    loud = np.sin(np.linspace(0, 2 * np.pi * 440 * 8, 8 * rate)).astype(np.float32) * 0.5
    path = tmp_path / "song.wav"
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes((np.concatenate([quiet, loud]) * 32767).astype(np.int16).tobytes())
    shape = track_shape(path)
    assert len(shape) == 4 and shape[2] == 0 and shape[3] == 0 and shape[0] < -20


def test_treatment_chooses_settings_only_when_asked(monkeypatch, config):
    from pipeline.lab import music as hosted
    from pipeline.lab.harness import song
    from pipeline.lab.harness.concept import concept_payload
    calls = []

    def answer(_config, prompt, schema, **kwargs):
        calls.append(kwargs["operation"])
        if kwargs["operation"] == "profile":
            return {"recognized": True, "artist": "Prospa", "title": "Will You Be Mine", "genre": "UK rave", "scene_and_era": "2020s",
                    "sound": "breakbeats", "lyric_reading": "a plea", "mood": "euphoric", "shape": "build then drop",
                    "cultural_use": "", "uncertainty": ""}
        return {"style": "Rave memory", "idea": "i", "pace": "rapid", "pace_shape": "s", "footage": "gems", "look": "l",
                "match_cuts": "many", "cutting": "c", "impact": "p", "avoid": "a"}

    monkeypatch.setattr(hosted, "_hosted_json", answer)
    monkeypatch.setattr(song, "track_shape", lambda _path: [-10, 0])

    class Store:
        def __init__(self, _root):
            pass

        def get_track(self, _id):
            return {"name": "Prospa - Will You Be Mine.wav", "duration": 8.0, "path": "unused"}

    monkeypatch.setattr("pipeline.lab.store.LabStore", Store)
    m = _map()
    document = {"track": {"id": "t"}, "passage": {"start": 10.0, "end": 18.0}, "editor_direction": None, "brief": "",
                "planner_settings": {"pacing": "patient", "footage": "balanced", "match_cuts": "some"}}
    kept = song.treat(document, m, config, "job", lambda _m: None, film_titles=[])
    assert kept["settings"]["pacing"] == "patient" and calls == ["profile", "treatment"]
    chosen = song.treat({**document, "planner_settings": {**document["planner_settings"], "auto": True}}, m, config, "job2",
                        lambda _m: None, film_titles=[])
    assert (chosen["settings"]["pacing"], chosen["settings"]["footage"], chosen["settings"]["match_cuts"]) == ("rapid", "gems", "many")
    assert calls == ["profile", "treatment", "treatment"]                       # the profile is cached per track and excerpt
    payload = concept_payload(document, m, [], None, chosen)
    assert payload["treatment"]["style"] == "Rave memory" and payload["song_profile"]["genre"] == "UK rave"


def test_pools_leave_out_captioned_and_dissolving_shots():
    from pipeline.lab.harness.pools import describes_dissolve, reads_as_text, usable
    assert reads_as_text("Long live the fighters!") and reads_as_text("Witch!") and reads_as_text("WARNER BROS. PICTURES")
    assert reads_as_text("《居場所を探し続けて》")
    assert not reads_as_text("TAXI") and not reads_as_text("Hotel") and not reads_as_text("")
    assert describes_dissolve(_candidate("d", action="Dissolve between apartment scenes"))
    assert not describes_dissolve(_candidate("f", action="The scene fades to black"))
    assert not usable(_candidate("s", on_screen_text="I need your guidance with\nthe two foreigners"))
    assert usable(_candidate("ok", action="Paul walks the ridge", on_screen_text="EXIT"))
