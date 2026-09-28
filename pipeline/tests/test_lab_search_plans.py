"""Executable editor recipes, immutable references and query-scoped evidence."""
from copy import deepcopy
import json
from unittest.mock import MagicMock

import pytest

from pipeline.lab import direction_planner, music, music_planner, search_plan
from pipeline.lab.models import ClipSelection, GeneratedSearchPlan, MusicDirection
from pipeline.search import capabilities, recipe
from pipeline.tests.selection_helpers import selector_response
from pipeline.tests.test_lab_direction_planner import _answer, _job
from pipeline.tests.test_lab_music_evidence import _document
from pipeline.tests.test_search_recipe import _chain, _result
from pipeline.tests.test_lab import db  # noqa: F401


def _caps(config):
    result = capabilities.search_capabilities(config, object())
    for facet in result["facets"]:
        facet["text_available"] = facet["facet"] != "composition"
        facet["source_available"] = facet["facet"] != "all"
    return result


def _text(facet, text):
    return {"kind": "text", "facet": facet, "text": text, "reference_id": None}


def _source(facet, identity):
    return {"kind": "source", "facet": facet, "text": None, "reference_id": identity}


def _direction(clauses):
    return {**MusicDirection(query="a visible image").model_dump(),
            "search_plan": {"clauses": clauses, "unverified_requirements": ["Review action completion in the source preview"]}}


def _reference_db(document, tmp_path):
    source = tmp_path / "frame.jpg"; source.write_bytes(b"test-reference-path-only")
    document["clips"] = [ClipSelection(id="anchor", film_id="film", unit_id="unit", source_start=20, source_end=23).model_dump()]
    document["music_timeline"]["slots"][0]["clip_id"] = "anchor"
    frame_rows = [{"unit_id": "unit", "film_id": "film", "frame_index": index, "timestamp": timestamp, "path": str(source)}
                  for index, timestamp in enumerate([20.1, 21.5, 22.8, 23.1])]
    unit = {"unit_id": "unit", "film_id": "film", "t_start": 20, "t_end": 24, "caption": "A figure beside a doorway",
            "dialogue": "[]", "on_screen_text": "", "mood": '["quiet"]', "energy": "calm"}
    frames, units = MagicMock(), MagicMock()
    frames.search.return_value = _chain(frame_rows); units.search.return_value = _chain([unit])
    db = MagicMock(); db.list_tables.return_value.tables = ["units", "frames"]
    db.open_table.side_effect = lambda name: frames if name == "frames" else units
    return db


@pytest.mark.parametrize("clauses", [
    [_text("composition", "same framing")],
    [_source("all", "ref-invented")],
    [_text("scene", "one"), _text("scene", "two")],
    [_text("scene", "one"), _text("look", "two"), _text("mood", "three"), _text("words", "four")],
    [{"kind": "text", "facet": "scene", "text": "one", "reference_id": "not-null"}],
    [{"kind": "text", "facet": "motion", "text": "moving left", "reference_id": None}],
])
def test_invalid_recipes_never_reach_an_adapter(clauses):
    with pytest.raises(ValueError):
        GeneratedSearchPlan(clauses=clauses, unverified_requirements=[])


def test_capability_readiness_uses_real_profile_checks_and_separates_spatial(config, monkeypatch):
    db = MagicMock(); db.list_tables.return_value.tables = ["units", "frames"]
    db.open_table.return_value.count_rows.return_value = 2
    ready = MagicMock(return_value=None); monkeypatch.setattr(capabilities, "resolve_ready_text_profile", ready)
    monkeypatch.setattr(capabilities, "require_visual_encoder_profile", lambda *_: None)
    catalog = capabilities.search_capabilities(config, db)
    by_facet = {row["facet"]: row for row in catalog["facets"]}
    assert by_facet["scene"]["text_available"] is False
    assert by_facet["look"]["text_available"] is True and by_facet["composition"]["source_available"] is True
    ready.return_value = object(); config.models.visual_encoder = "siglip2_so400m"
    by_facet = {row["facet"]: row for row in capabilities.search_capabilities(config, db)["facets"]}
    assert by_facet["scene"]["text_available"] is True and by_facet["composition"]["source_available"] is False
    assert by_facet["look"]["text_available"] is True
    assert all(row["label"] for row in by_facet.values())
    assert capabilities.search_capabilities(config, object())["facets"][1]["text_available"] is None


def test_generated_recipes_require_verified_availability_and_offered_reference(config):
    caps = _caps(config)
    with pytest.raises(ValueError, match="reference"):
        search_plan.bind_generated_direction(_direction([_source("composition", "invented")]), [], caps)
    caps["facets"][1]["text_available"] = None
    with pytest.raises(ValueError, match="not verified ready"):
        search_plan.bind_generated_direction(_direction([_text("scene", "a door")]), [], caps)
    caps["facets"][3]["text_available"] = False
    with pytest.raises(ValueError, match="unavailable"):
        search_plan.bind_generated_direction(MusicDirection(query="red light", search_facet="look").model_dump(), [], caps)


def test_reference_anchor_frozen_to_trim_and_not_offered_from_replacement_targets(config, tmp_path):
    document = _document(); db = _reference_db(document, tmp_path); caps = _caps(config)
    refs = search_plan.offered_references(document, db, caps)
    assert len(refs) == 1 and refs[0]["timestamp"] == 21.5 and refs[0]["frame_index"] == 1
    assert "composition" in refs[0]["available_facets"] and "words" not in refs[0]["available_facets"]
    assert search_plan.offered_references(document, db, caps, exclude_slot_ids=[document["music_timeline"]["slots"][0]["id"]]) == []
    bound = search_plan.bind_generated_direction(_direction([_source("composition", refs[0]["reference_id"])]), refs, caps)
    resolved = search_plan.resolve_search(bound, refs, caps, 3)
    assert resolved["references"][0]["clip_id"] == "anchor" and resolved["min_duration"] == 3
    document["clips"][0].update(source_start=20.2, source_end=23.2)
    changed = search_plan.offered_references(document, db, caps)
    assert changed[0]["reference_id"] != refs[0]["reference_id"]
    with pytest.raises(ValueError, match="reference"):
        search_plan.resolve_search(bound, changed, caps, 3)
    forged = deepcopy(bound); forged["search_plan"]["references"][0]["timestamp"] = 22
    with pytest.raises(ValueError, match="reference changed"):
        search_plan.resolve_search(forged, refs, caps, 3)


@pytest.mark.parametrize("crop", [None, {}, {"x": 0., "y": 0., "width": 1., "height": 1.}])
def test_full_frame_crop_keeps_existing_indexed_visual_references(config, tmp_path, crop):
    document = _document(); db = _reference_db(document, tmp_path); caps = _caps(config)
    document["clips"][0]["crop"] = crop
    refs = search_plan.offered_references(document, db, caps)
    assert len(refs) == 1 and {"look", "composition"} <= set(refs[0]["available_facets"])
    assert "context_note" not in refs[0]


def test_cropped_anchor_blocks_stale_visual_recipes_but_retains_whole_shot_semantic_context(config, tmp_path):
    document = _document(); db = _reference_db(document, tmp_path); caps = _caps(config)
    refs = search_plan.offered_references(document, db, caps)
    identity = refs[0]["reference_id"]
    visual = [search_plan.bind_generated_direction(_direction([_source(facet, identity)]), refs, caps)
              for facet in ("look", "composition")]
    semantic = search_plan.bind_generated_direction(_direction([_source("scene", identity)]), refs, caps)
    document["clips"][0]["crop"] = {"x": .1, "y": .2, "width": .5, "height": .5}
    cropped_refs = search_plan.offered_references(document, db, caps)
    assert cropped_refs[0]["reference_id"] == identity
    assert {"look", "composition"}.isdisjoint(cropped_refs[0]["available_facets"])
    assert {"scene", "mood"} <= set(cropped_refs[0]["available_facets"])
    assert "whole source shot" in cropped_refs[0]["context_note"]
    for direction in visual:
        with pytest.raises(ValueError, match="unavailable reference"):
            search_plan.resolve_search(direction, cropped_refs, caps, 3)
    assert search_plan.resolve_search(semantic, cropped_refs, caps, 3) == search_plan.resolve_search(semantic, refs, caps, 3)


def test_legacy_direction_still_calls_existing_single_query_helper(config, monkeypatch):
    direction = MusicDirection(query="sunlight", search_facet="all").model_dump()
    resolved = search_plan.resolve_search(direction, [], capabilities.search_capabilities(config, object()), 3)
    called = MagicMock(return_value=[]); monkeypatch.setattr(music, "retrieve_edit_candidates", called)
    search_plan.execute_search(resolved, {"film_ids": ["chosen-film"]}, config, object())
    assert called.call_args.args == ("sunlight", called.call_args.args[1], config, ["chosen-film"])
    assert resolved["references"] == [] and len(resolved["clauses"]) == 1


@pytest.mark.parametrize("clauses", [None, [_text("scene", "face")],
                                  [_text("scene", "face"), _text("mood", "quiet")]])
def test_text_only_suggestions_skip_unrelated_placed_reference_reads(config, tmp_path, monkeypatch, clauses):
    document = _document(); db = _reference_db(document, tmp_path); caps = _caps(config)
    slots = document["music_timeline"]["slots"]
    refs = search_plan.offered_references(document, db, caps)
    # A saved source recipe elsewhere in the edit must not add work to this target.
    slots[2]["direction"] = search_plan.bind_generated_direction(
        _direction([_source("look", refs[0]["reference_id"])]), refs, caps)
    slots[1]["direction"] = (MusicDirection(query="face").model_dump() if clauses is None else
                              search_plan.bind_generated_direction(_direction(clauses), [], caps))
    before = deepcopy(document)
    forbidden = MagicMock(side_effect=AssertionError("Text-only suggestions do not read reference anchors or choose footage"))
    monkeypatch.setattr(music_planner, "offered_references", forbidden)
    monkeypatch.setattr(music, "_hosted_json", forbidden)
    monkeypatch.setattr(music_planner, "search_capabilities", lambda *_: caps)
    retrieve = MagicMock(return_value=[{"unit_id": "face-shot", "film_id": "film", "t_start": 40., "t_end": 46.,
                                       "caption": "A face in a doorway", "matched_frame_timestamp": 43.}])
    monkeypatch.setattr(music_planner, "execute_search", retrieve)
    result = music_planner.fill_timeline(document, config, db, lambda _: None, "text-options", [slots[1]["id"]], suggest_only=True)
    resolved = retrieve.call_args.args[0]
    assert resolved["clauses"] == (clauses or [_text("all", "face")])
    assert resolved["references"] == [] and retrieve.call_count == 1
    option = slots[1]["alternatives"][0]["clip"]
    assert option["unit_id"] == "face-shot" and option["source_end"] - option["source_start"] == 3
    assert result["clips"] == before["clips"] and slots[2] == before["music_timeline"]["slots"][2]
    forbidden.assert_not_called()


@pytest.mark.parametrize("change", [None, "trimmed", "forged", "target_is_anchor"])
def test_source_suggestions_still_refresh_and_validate_reference_authority(config, tmp_path, monkeypatch, change):
    document = _document(); db = _reference_db(document, tmp_path); caps = _caps(config)
    refs = search_plan.offered_references(document, db, caps)
    target = document["music_timeline"]["slots"][0 if change == "target_is_anchor" else 1]
    target["direction"] = search_plan.bind_generated_direction(
        _direction([_text("scene", "face"), _source("look", refs[0]["reference_id"])]), refs, caps)
    if change == "trimmed":
        document["clips"][0].update(source_start=20.2, source_end=23.2)
    elif change == "forged":
        target["direction"]["search_plan"]["references"][0]["timestamp"] = 22.
    offered = MagicMock(wraps=search_plan.offered_references)
    retrieve = MagicMock(return_value=[])
    monkeypatch.setattr(music_planner, "offered_references", offered)
    monkeypatch.setattr(music_planner, "search_capabilities", lambda *_: caps)
    monkeypatch.setattr(music_planner, "execute_search", retrieve)
    if change is None:
        music_planner.fill_timeline(document, config, db, lambda _: None, "source-options", [target["id"]], suggest_only=True)
        assert retrieve.call_args.args[0]["references"] == target["direction"]["search_plan"]["references"]
        assert retrieve.call_count == 1
    else:
        with pytest.raises(ValueError, match="reference"):
            music_planner.fill_timeline(document, config, db, lambda _: None, "stale-source", [target["id"]], suggest_only=True)
        retrieve.assert_not_called()
    offered.assert_called_once_with(document, db, caps, exclude_slot_ids=[target["id"]])


def test_combined_recipe_uses_real_clause_dispatch_fusion_and_film_scope(config, monkeypatch):
    calls = []
    def semantic(query, views, db, _config, **kwargs):
        calls.append((query, views, kwargs))
        result = _result("shared"); result.update(t_start=20., t_end=26., matched_text=query, matched_text_view=views[0])
        return [result]
    monkeypatch.setattr(recipe, "search_semantic_views", semantic)
    monkeypatch.setattr(recipe, "apply_recipe_result_preferences", lambda rows, *a, **k: rows)
    caps = _caps(config)
    bound = search_plan.bind_generated_direction(_direction([_text("scene", "a train platform"), _text("mood", "uneasy anticipation")]), [], caps)
    resolved = search_plan.resolve_search(bound, [], caps, 3)
    rows = search_plan.execute_search(resolved, {"film_ids": ["chosen-film"]}, config, object())
    assert [call[1] for call in calls] == [("caption",), ("mood",)]
    assert all(call[2]["film_ids"] == ("chosen-film",) for call in calls)
    assert [item["facet"] for item in rows[0]["matches"]] == ["scene", "mood"]


def test_source_recipe_executes_stable_identity_and_preserves_explicit_film_scope(config, tmp_path, monkeypatch):
    document = _document(); db = _reference_db(document, tmp_path); caps = _caps(config)
    refs = search_plan.offered_references(document, db, caps)
    bound = search_plan.bind_generated_direction(_direction([_source("look", refs[0]["reference_id"]), _text("mood", "calm")]), refs, caps)
    resolved = search_plan.resolve_search(bound, refs, caps, 3)
    called = MagicMock(return_value=recipe.SearchRecipeExecution(results=[], source_evidence=[]))
    monkeypatch.setattr(search_plan, "execute_search_recipe", called)
    search_plan.execute_search(resolved, {"film_ids": ["other-film"]}, config, db)
    clauses = called.call_args.args[0]
    assert clauses[0].source == recipe.SourceReference("unit", 1)
    assert called.call_args.kwargs["film_ids"] == ["other-film"]
    assert called.call_args.kwargs["_preserve_visual_alternatives"] is True


def test_generation_binds_recipe_and_invalid_reference_never_populates_cache(config, monkeypatch):
    document = _document(); ids = [slot["id"] for slot in document["music_timeline"]["slots"]]
    answer = _answer(ids)
    for row in answer["directions"]:
        row["direction"]["search_plan"] = _direction([_text("scene", "a doorway"), _text("mood", "quiet")])["search_plan"]
    monkeypatch.setattr(direction_planner, "search_capabilities", lambda *_: _caps(config))
    monkeypatch.setattr(music, "_hosted_json", lambda *a, **k: answer)
    result = direction_planner.run_direction_job(_job(document), config, object(), lambda _: None)
    assert all(len(slot["resolved_search"]["clauses"]) == 2 for slot in result["music_timeline"]["slots"])
    before = len(list((config.paths.assets_dir / "lab" / "direction-plans").glob("*.json")))
    answer["directions"][0]["direction"]["search_plan"] = _direction([_source("composition", "invented")])["search_plan"]
    document["brief"] = "changed input"
    with pytest.raises(ValueError, match="reference"):
        direction_planner.run_direction_job(_job(document), config, object(), lambda _: None)
    assert len(list((config.paths.assets_dir / "lab" / "direction-plans").glob("*.json"))) == before
    assert "search_plan" not in music.AudioInterpretation.model_json_schema()["$defs"]["EditBeat"]["properties"]


def test_same_unit_retains_different_query_evidence_and_duration_filter(config, monkeypatch):
    document = _document(); slots = document["music_timeline"]["slots"]
    for index, slot in enumerate(slots):
        slot["direction"] = MusicDirection(query=f"query-{index}").model_dump()
    def retrieve(query, *_args):
        index = int(query[-1])
        return [{"unit_id": "same", "film_id": "film", "t_start": 20., "t_end": 26., "caption": "A figure waiting",
                 "framing": "medium", "matched_text": f"evidence-{index}", "matched_text_view": "caption",
                 "matched_frame_timestamp": 21. + index, "debug": {"channels": {"txt": {"rank": index + 1}}}},
                {"unit_id": "too-short", "film_id": "film", "t_start": 30., "t_end": 31., "caption": "A brief flash"}]
    monkeypatch.setattr(music, "retrieve_edit_candidates", retrieve)
    def hosted(_config, prompt, *_args, **_kwargs):
        payload = json.loads(prompt.split("\n", 1)[1])
        assert "matched_text" not in payload["sources"]["c0"]
        assert payload["sources"]["c0"]["metadata"]["framing"] == "medium"
        assert len(payload["sources"]) == 1, "The too-short source must not reach the selector"
        for index, slot in enumerate(payload["slots"]):
            candidate = slot["candidates"][0]
            assert candidate["source"] == "c0"
            assert candidate["evidence"]["matched_text"] == f"evidence-{index}"
            suggested = candidate["evidence"]["suggested_source_start"]
            assert 20 <= suggested <= 23 and suggested <= 21 + index <= suggested + 3
        return selector_response([{"slot": index, "candidate_id": "same", "source_start": 21., "reason": "Readable gesture"} for index in range(3)], ["same"])
    monkeypatch.setattr(music, "_hosted_json", hosted)
    result = music_planner.fill_timeline(document, config, object(), lambda _: None, "job")
    # The source is still offered with separate evidence for each query, but
    # automatic assembly cannot replay the same footage to fill later slots.
    assert [slot["alternatives"][0]["search_evidence"]["matched_text"] for slot in result["music_timeline"]["slots"]] == ["evidence-0", "evidence-1", "evidence-2"]
    assert slots[0]["search_evidence"] == slots[0]["alternatives"][0]["search_evidence"]
    assert all(slot["clip_id"] is None and "repeats clip 1" in slot["search_error"] for slot in slots[1:])
    assert result["analysis"]["draft"]["repetition_count"] == 2


def test_actual_result_projection_is_hydrated_from_lance_before_selection(config, db, monkeypatch):
    import pyarrow as pa
    from pipeline.search.retrieve import _clause_results_from_rows
    from pipeline.tests.test_retrieve import _make_unit_row
    raw = _make_unit_row("source", "film", t_start=20., t_end=26., framing="wide", mood='["quiet"]',
                         parent_shot_id="long-take", palette='["blue"]', img_vec=[0.] * 4, txt_vec=[0.] * 4)
    table = db.open_table("units")
    table.add(pa.Table.from_pylist([raw], schema=table.schema))
    projected = _clause_results_from_rows([raw], channel="txt", mode="semantic_view", result_limit=1)
    assert "framing" not in projected[0] and "parent_shot_id" not in projected[0]
    monkeypatch.setattr(music, "retrieve_edit_candidates", lambda *_: projected)
    def hosted(_config, prompt, *_args, **_kwargs):
        payload = json.loads(prompt.split("\n", 1)[1])
        candidate = payload["sources"]["c0"]
        assert candidate["metadata"]["framing"] == "wide"
        assert candidate["metadata"]["mood"] == ["quiet"] and candidate["metadata"]["palette"] == ["blue"]
        assert candidate["parent_shot_id"] == "long-take"
        return selector_response([{"slot": index, "candidate_id": "source", "source_start": 21., "reason": "A sustained wide image"} for index in range(3)], ["source"])
    monkeypatch.setattr(music, "_hosted_json", hosted)
    music_planner.fill_timeline(_document(), config, db, lambda _: None, "job")
    offered = {"source": music_planner._candidate(projected[0])}
    offered["source"]["t_end"] = 27
    with pytest.raises(ValueError, match="ranges changed"):
        music_planner._hydrate_metadata(offered, db)


def test_planner_explains_missing_preceding_reference_without_substituting_following(config, monkeypatch):
    document = _document(); slots = document["music_timeline"]["slots"]
    document["clips"] = [ClipSelection(id="preceding", film_id="previous-film", source_start=20, source_end=23).model_dump(),
                         ClipSelection(id="following", film_id="next-film", source_start=40, source_end=43).model_dump()]
    slots[0]["clip_id"], slots[2]["clip_id"] = "preceding", "following"
    target = slots[1]["id"]
    slots[1]["direction"] = MusicDirection(query="Use the preceding shot's framing").model_dump()
    following = {"reference_id": "ref-following", "clip_id": "following", "film_id": "next-film", "unit_id": "next-unit",
                 "frame_index": 1, "timestamp": 41.5, "source_start": 40, "source_end": 43, "available_facets": ["composition"]}
    monkeypatch.setattr(direction_planner, "search_capabilities", lambda *_: _caps(config))
    monkeypatch.setattr(direction_planner, "offered_references", lambda *a, **k: [following])
    def hosted(_config, prompt, *_args, **_kwargs):
        context = json.loads(prompt.split("\n", 1)[1])
        previous, current, after = context["timeline"]
        assert [previous["timeline_position"], current["timeline_position"], after["timeline_position"]] == [1, 2, 3]
        assert previous["selected_source"]["reference_ids"] == []
        assert "No available indexed reference inside this selected source interval" in previous["selected_source"]["reference_availability"]
        assert after["selected_source"]["reference_ids"] == ["ref-following"]
        assert context["offered_references"][0]["slot_id"] == slots[2]["id"]
        assert context["offered_references"][0]["timeline_position"] == 3
        assert "do not silently substitute another neighbor" in prompt
        assert "put the missing requested reference in unverified_requirements" in prompt
        answer = _answer([target])
        answer["directions"][0]["direction"]["search_plan"] = {"clauses": [_text("scene", "a quiet figure in a narrow doorway")],
            "unverified_requirements": ["The requested preceding shot has no indexed reference inside its selected interval; its framing still needs source review"]}
        return answer
    monkeypatch.setattr(music, "_hosted_json", hosted)
    result = direction_planner.run_direction_job(_job(document, [target]), config, object(), lambda _: None)
    plan = result["music_timeline"]["slots"][1]["resolved_search"]
    assert plan["references"] == [] and "preceding shot" in plan["unverified_requirements"][0]
    assert result["clips"] == document["clips"]
    assert result["direction_plan"]["contract"] == direction_planner.DIRECTION_PLAN_CONTRACT
