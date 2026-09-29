"""Tests for quote scoring and the prior/scene ordering policy."""

from __future__ import annotations

import pytest

from pipeline.evidence.textnorm import normalize_line
from pipeline.search import priors
from pipeline.search.quotes import score_window


def test_quote_scoring_prefers_the_exact_line_over_word_overlap():
    query = normalize_line("You talkin' to me?").split()
    assert score_window(query, normalize_line("You talking to me?").split()) == pytest.approx(1.0)
    assert score_window(query, normalize_line("Well, who the hell else are you talking to?").split()) < 0.8
    # Same words, wrong order: incidental.
    assert score_window(query, normalize_line("Me? Talking to you?").split()) < 0.8


def _evidence(fame: float, craft: float, *, iconic: bool = False, scene: str | None = None) -> dict:
    return {"fame_library": fame, "craft": craft, "iconic": iconic, "distinctiveness": 0.5, "scene_id": scene}


def test_balanced_priors_lift_iconic_shots_only_within_a_bounded_window():
    items = [f"u{i}" for i in range(60)]
    evidence = {item: _evidence(0.1, 0.5) for item in items}
    evidence["u12"] = _evidence(1.0, 0.9, iconic=True)       # relevant and famous: should rise
    evidence["u55"] = _evidence(1.0, 0.9, iconic=True)       # famous but barely relevant: must not reach the top
    ranked = priors.rerank(items, evidence, preset="balanced", specificity=0.0, unit_id=str)
    assert ranked.index("u12") < 12
    assert ranked.index("u55") > 20


def test_priors_settle_near_ties_but_cannot_overrule_a_clearly_stronger_match():
    ordinary, iconic = _evidence(0.44, 0.7), _evidence(0.89, 0.85, iconic=True)
    lift = lambda relevance, evidence: relevance * priors.multiplier(evidence, preset="balanced", specificity=0.0)
    # The judge clearly prefers the ordinary shot: fame must not reverse that ...
    assert lift(0.97, ordinary) > lift(0.75, iconic)
    # ... but between two equally good matches, the iconic one leads.
    assert lift(0.95, iconic) > lift(0.97, ordinary)
    assert priors.multiplier(None, preset="famous", specificity=0.0) == 1.0
    assert priors.multiplier(iconic, preset="famous", specificity=1.0) == 1.0


def test_specific_queries_ignore_priors_and_gems_demote_iconic_shots():
    items = ["a", "b", "c"]
    evidence = {"a": _evidence(0.1, 0.3), "b": _evidence(0.2, 0.9), "c": _evidence(1.0, 0.9, iconic=True)}
    assert priors.rerank(items, evidence, preset="famous", specificity=1.0, unit_id=str) == items
    assert priors.rerank(items, evidence, preset="famous", specificity=0.0, unit_id=str)[0] == "c"
    gems = priors.rerank(items, evidence, preset="gems", specificity=0.0, unit_id=str)
    assert gems[0] == "b" and gems[-1] == "c"
    # No evidence: neutral, order kept.
    assert priors.rerank(items, {}, preset="gems", specificity=0.0, unit_id=str) == items


def test_scene_grouping_keeps_the_best_shot_and_attaches_alternatives():
    evidence = {"a": _evidence(0, 0.5, scene="s1"), "b": _evidence(0, 0.5, scene="s1"), "c": _evidence(0, 0.5)}
    attached: dict[str, list[str]] = {}
    grouped = priors.group_by_scene(["a", "c", "b"], evidence, unit_id=str,
                                    attach=lambda rep, other: attached.setdefault(rep, []).append(other))
    assert grouped == ["a", "c"] and attached == {"a": ["b"]}


def test_decorate_prefers_hero_unless_the_visual_match_found_the_shot():
    evidence = {"hero_path": "f/evidence/hero/p/u.webp", "hero_time": 12.5, "iconic": True, "gem": False,
                "action": "Neo dodges bullets.", "characters": '["Neo"]'}
    text_led = {"unit_id": "u", "keyframe_url": "/media/keyframe/u/1",
                "debug": {"channels": {"txt": {"rank": 1}, "img": {"rank": 30}}}, "matched_frame_url": "/media/keyframe/u/1"}
    priors.decorate(text_led, evidence, None)
    assert text_led["thumbnail_url"].startswith("/media/hero/u?t=12.500")
    assert text_led["keyframe_url"] == "/media/keyframe/u/1"          # the source-drag frame is unchanged
    assert text_led["badges"] == ["iconic"] and text_led["characters"] == ["Neo"]
    image_led = {"unit_id": "u", "keyframe_url": "/media/keyframe/u/2",
                 "debug": {"channels": {"img": {"rank": 1}, "txt": {"rank": 40}}}, "matched_frame_url": "/media/keyframe/u/2"}
    priors.decorate(image_led, evidence, None)
    assert image_led["thumbnail_url"] == "/media/keyframe/u/2"


def test_presets_are_validated():
    assert priors.validate_preset(None) == "balanced"
    with pytest.raises(ValueError):
        priors.validate_preset("popular")


def test_query_signals_parse_names_scale_camera_time_and_colour():
    from pipeline.search import signals

    vocab = signals.Vocabulary(key=(), phrases={
        "agent smith": [("character", "matrix", "agent smith")], "smith": [("character", "other", "caleb smith")],
        "trinity": [("character", "matrix", "trinity")], "keanu reeves": [("character", "matrix", "neo")],
        "matrix": [("film", "matrix", "matrix")]}, longest=2)
    parsed = signals.parse("Close-up of Keanu Reeves as Agent Smith hunts him, slow push in at night", vocab)
    assert parsed.characters == {("matrix", "neo"), ("matrix", "agent smith")}      # longest match claims "smith"
    assert parsed.scale == "close_up" and parsed.camera == {"push_in"} and parsed.time == "night"
    assert signals.parse("black and white rain in the matrix", vocab).black_and_white
    assert signals.parse("black and white rain in the matrix", vocab).films == {"matrix"}
    assert not signals.parse("a man walks down a street", vocab)


def test_query_signals_lift_satisfying_evidence_and_leave_unknowns_neutral():
    from pipeline.search import signals

    parsed = signals.Signals(characters={("matrix", "trinity")}, scale="close_up")
    units = {"a": {"film_id": "matrix", "framing": "wide"}, "b": {"film_id": "matrix", "framing": "close_up"},
             "c": {"film_id": "other", "framing": "unknown"}}
    evidence = {"b": {"characters": '["Trinity"]'}}
    assert signals.multiplier(parsed, units["b"], evidence["b"]) > 2.0
    assert signals.multiplier(parsed, units["a"], None) < 1.0
    assert signals.multiplier(parsed, units["c"], None) == 1.0
    assert parsed.named_films() == {"matrix"}                               # a character names its film


def test_decorate_carries_the_focus_span_and_keys_a_replaced_preview():
    evidence = {"focus_start": 3775.1, "focus_end": 3781.07, "preview_path": "f/evidence/hero/p/u.mp4"}
    result = {"unit_id": "u", "preview_url": "/media/preview/u", "keyframe_url": "/media/keyframe/u/1"}
    priors.decorate(result, evidence, None)
    assert (result["focus_start"], result["focus_end"]) == (3775.1, 3781.07)
    assert result["preview_url"] == "/media/preview/u?focus=3775.10"       # a new clip must not hit the old cache
    plain = {"unit_id": "v", "preview_url": "/media/preview/v", "keyframe_url": "/media/keyframe/v/1"}
    priors.decorate(plain, {"focus_start": 1.0, "focus_end": 2.0}, None)
    assert plain["preview_url"] == "/media/preview/v"                       # the ingest clip still serves
