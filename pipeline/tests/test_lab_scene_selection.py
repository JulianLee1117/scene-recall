"""Source names and source timestamps are one constrained model decision."""
from copy import deepcopy
import json

import pytest

from pipeline.lab.scene_selection import (
    TIMED_SELECTION_CONTRACT, selection_choices, selection_manifest, selection_schema, source_aliases,
)


def _sources():
    return {
        "scene-a": {"unit_id": "scene-a", "film_id": "film-a", "t_start": 10., "t_end": 20.},
        "scene-b": {"unit_id": "scene-b", "film_id": "film-b", "t_start": 30., "t_end": 45.},
        "scene-c": {"unit_id": "scene-c", "film_id": "film-c", "t_start": 60., "t_end": 80.},
    }


def _offers():
    return [{"slot": 2, "start": 100., "duration": 3., "candidate_ids": ["scene-a", "scene-b"]},
            {"slot": 7, "start": 115., "duration": 4., "candidate_ids": ["scene-b", "scene-c"]}]


def _choice(alias="c0", start=10., *, frame=None, position=0.):
    value = {"source": None if alias is None else {alias: position if frame is not None else start}, "reason": "Use a specific visual answer"}
    if frame is not None:
        value["preferred_end_frame"] = frame
    return value


def _output():
    # Dictionary order is not authority; output follows the supplied offers.
    return {"choices": {"shot_7": _choice("c1", 30.), "shot_2": _choice()}}


def _timed():
    scope = {"passage": {"start": 53.99, "end": 60.003}, "fps": 24, "total_frames": 144}
    offers = [{"slot": 0, "start": 53.99, "duration": 3., "candidate_ids": ["scene-a", "scene-b"],
               "timing": {"end_frames": [48, 72], "min_duration": 2.}},
              {"slot": 1, "start": 56.99, "duration": 3.013, "candidate_ids": ["scene-b", "scene-c"],
               "timing": {"end_frames": [144], "min_duration": 3.013}}]
    return offers, scope


def _source_branches(schema, key):
    source = schema["properties"]["choices"]["properties"][key]["properties"]["source"]
    return source.get("anyOf", [source])


def _source_allowed(schema, key, value):
    for branch in _source_branches(schema, key):
        if branch["type"] == "null":
            if value is None:
                return True
        elif isinstance(value, dict) and set(value) == set(branch["required"]):
            alias, start = next(iter(value.items()))
            bound = branch["properties"][alias]
            if (not isinstance(start, bool) and isinstance(start, (int, float))
                    and bound["minimum"] <= start <= bound["maximum"]):
                return True
    return False


def _schema_size(schema):
    properties = enums = depth = 0

    def visit(node, current=0):
        nonlocal properties, enums, depth
        if isinstance(node, dict):
            if node.get("type") in ("object", "array"):
                current += 1
                depth = max(depth, current)
            properties += len(node.get("properties", {}))
            enums += len(node.get("enum", []))
            for value in node.values():
                visit(value, current)
        elif isinstance(node, list):
            for value in node:
                visit(value, current)
    visit(schema)
    return properties, enums, depth


def test_schema_requires_exact_sparse_shots_and_binds_aliases_to_source_ranges():
    schema = selection_schema(_offers(), _sources())
    choices = schema["properties"]["choices"]
    assert schema["type"] == "object" and schema["additionalProperties"] is False
    assert schema["required"] == ["choices"]
    assert choices["type"] == "object" and choices["additionalProperties"] is False
    assert set(choices["properties"]) == set(choices["required"]) == {"shot_2", "shot_7"}
    for shot in choices["properties"].values():
        assert shot["additionalProperties"] is False
        assert set(shot["required"]) == set(shot["properties"]) == {"source", "reason"}
    for key in choices["required"]:
        assert _source_allowed(schema, key, None)
        for branch in _source_branches(schema, key):
            if branch["type"] == "object":
                assert branch["additionalProperties"] is False
                assert len(branch["properties"]) == len(branch["required"]) == 1
    assert _source_allowed(schema, "shot_2", {"c0": 17.})
    assert not _source_allowed(schema, "shot_2", {"c0": 17.01})
    assert _source_allowed(schema, "shot_7", {"c1": 41.})
    assert not _source_allowed(schema, "shot_7", {"c1": 41.01})
    assert not _source_allowed(schema, "shot_2", {"c2": 60.})
    encoded = json.dumps(schema)
    assert "candidate_index" not in encoded and "source_start" not in encoded
    assert not any(identity in encoded for identity in _sources())


def test_global_aliases_are_stable_and_not_shifted_by_unoffered_retrieval_rows():
    offers, sources = _offers(), _sources()
    before = deepcopy((offers, sources))
    assert source_aliases(sources) == {"scene-a": "c0", "scene-b": "c1", "scene-c": "c2"}
    result = selection_choices(_output(), offers, {"extra": _sources()["scene-c"], **sources})
    assert [row["slot"] for row in result] == [2, 7]
    assert [row["candidate_id"] for row in result] == ["scene-a", "scene-b"]
    assert [row["source_start"] for row in result] == [10., 30.]
    assert [row["start"] for row in result] == [100., 115.]
    assert [row["duration"] for row in result] == [3., 4.]
    assert all("source" not in row for row in result)
    changed = {"choices": {"shot_2": _choice("c1", 30.), "shot_7": _choice("c2", 60.)}}
    assert [row["candidate_id"] for row in selection_choices(changed, offers, sources)] == ["scene-b", "scene-c"]
    assert (offers, sources) == before


@pytest.mark.parametrize("source", [{"c2": 60.}, {"c99": 10.}, {"scene-a": 10.}, {"0": 10.},
                                     {0: 10.}, {"c0": 10., "c1": 30.}, {}, [], "c0", True])
def test_invalid_or_unoffered_source_alias_never_becomes_a_canonical_choice(source):
    output = _output()
    output["choices"]["shot_2"]["source"] = source
    with pytest.raises(ValueError):
        selection_choices(output, _offers(), _sources())


@pytest.mark.parametrize("mutation", ["missing", "extra", "renamed", "list", "canonical-id", "old-index", "old-start", "extra-field", "extra-root"])
def test_missing_extra_or_old_wire_fields_are_rejected(mutation):
    output = _output()
    if mutation == "missing": output["choices"].pop("shot_2")
    elif mutation == "extra": output["choices"]["shot_3"] = _choice()
    elif mutation == "renamed": output["choices"]["shot_02"] = output["choices"].pop("shot_2")
    elif mutation == "list": output["choices"] = list(output["choices"].values())
    elif mutation == "canonical-id": output["choices"]["shot_2"]["candidate_id"] = "scene-c"
    elif mutation == "old-index": output["choices"]["shot_2"]["candidate_index"] = 0
    elif mutation == "old-start": output["choices"]["shot_2"]["source_start"] = 10.
    elif mutation == "extra-field": output["choices"]["shot_2"]["effect"] = "slow-motion"
    elif mutation == "extra-root": output["other"] = "ignored?"
    with pytest.raises(ValueError):
        selection_choices(output, _offers(), _sources())


@pytest.mark.parametrize("start", [None, True, False, "10", float("nan"), float("inf"), -1., 9.999, 18.])
def test_source_timestamp_is_strict_finite_and_inside_its_own_window(start):
    output = _output()
    output["choices"]["shot_2"]["source"] = {"c0": start}
    with pytest.raises(ValueError):
        selection_choices(output, _offers(), _sources())


def test_abstention_has_no_source_or_timing_to_mix():
    output = _output()
    output["choices"]["shot_2"] = _choice(None)
    result = selection_choices(output, _offers(), _sources())
    assert result[0]["candidate_id"] is None and result[0]["source_start"] is None


def test_empty_offer_allows_only_abstention_without_borrowing_other_sources():
    offers = _offers()
    offers[0]["candidate_ids"] = []
    schema = selection_schema(offers, _sources())
    assert _source_branches(schema, "shot_2") == [{"type": "null"}]
    with pytest.raises(ValueError):
        selection_choices(_output(), offers, _sources())
    output = {"choices": {"shot_2": _choice(None), "shot_7": _choice("c0", 30.)}}
    assert selection_choices(output, offers, _sources())[0]["candidate_id"] is None


def test_missing_authoritative_source_rejects_instead_of_using_another_candidate():
    sources = _sources()
    del sources["scene-a"]
    with pytest.raises(ValueError):
        selection_schema(_offers(), sources)
    with pytest.raises(ValueError):
        selection_choices(_output(), _offers(), sources)


def test_timed_choices_use_actual_previous_cut_and_exact_audio_endpoint():
    offers, scope = _timed()
    output = {"choices": {"shot_0": _choice(frame=48), "shot_1": _choice("c2", 60., frame=144)}}
    result = selection_choices(output, offers, _sources(), scope)
    assert [row["candidate_id"] for row in result] == ["scene-a", "scene-c"]
    assert [row["start"] for row in result] == pytest.approx([53.99, 55.99])
    assert [row["duration"] for row in result] == pytest.approx([2., 4.013])
    assert result[-1]["start"] + result[-1]["duration"] == pytest.approx(60.003)


def test_timed_schema_keeps_one_cut_enum_per_shot_outside_source_branches():
    offers, scope = _timed()
    original = deepcopy(offers)
    schema = selection_schema(offers, _sources(), scope)
    for offer in offers:
        shot = schema["properties"]["choices"]["properties"][f"shot_{offer['slot']}"]
        assert "preferred_end_frame" in shot["required"]
        assert shot["properties"]["preferred_end_frame"]["enum"] == offer["timing"]["end_frames"]
    assert _schema_size(schema)[1] == 3
    assert offers == original


def test_short_source_changes_a_neighbor_preference_before_resolving_its_start():
    offers, scope = _timed()
    sources = _sources()
    sources["scene-b"]["t_end"] = 33.5
    schema = selection_schema(offers, sources, scope)
    assert _source_allowed(schema, "shot_1", {"c1": 1.})
    assert not _source_allowed(schema, "shot_1", {"c1": 30.})
    output = {"choices": {"shot_0": _choice(frame=48), "shot_1": _choice("c1", frame=144, position=1.)}}
    before = deepcopy((output, offers, sources, scope))
    result = selection_choices(output, offers, sources, scope)
    assert [row["end_frame"] for row in result] == [72, 144]
    assert [row["preferred_end_frame"] for row in result] == [48, 144]
    assert result[1]["duration"] == pytest.approx(3.013)
    assert result[1]["source_start"] == pytest.approx(33.5 - 3.013)
    assert result[1]["source_start"] + result[1]["duration"] == pytest.approx(33.5)
    assert [row["candidate_id"] for row in result] == ["scene-a", "scene-b"]
    assert (output, offers, sources, scope) == before


def test_jointly_infeasible_sources_fail_without_substitution_or_abstention():
    offers, scope = _timed()
    sources = _sources()
    sources["scene-a"]["t_end"] = 12.5
    sources["scene-b"]["t_end"] = 33.5
    output = {"choices": {"shot_0": _choice(frame=48), "shot_1": _choice("c1", frame=144)}}
    before = deepcopy((output, offers, sources, scope))
    with pytest.raises(ValueError, match="cannot fit.*shot 2"):
        selection_choices(output, offers, sources, scope)
    assert (output, offers, sources, scope) == before


def test_nocturne_rapid_paperwork_source_fits_by_moving_only_its_offered_exit():
    # Job04a656fe part1 shot12: 22 requested output frames in an18-frame source.
    # The archived offer already contained frame255, a feasible earlier exit.
    sources = {
        "preceding": {"film_id": "a", "t_start": 10., "t_end": 30.},
        "paperwork": {"film_id": "b", "t_start": 6921.623041666666, "t_end": 6922.373791666666},
        "following": {"film_id": "c", "t_start": 40., "t_end": 50.},
    }
    scope = {"passage": {"start": 53.99, "end": 65.99}, "fps": 24, "total_frames": 288}
    offers = [
        {"slot": 10, "start": 53.99, "duration": 10., "candidate_ids": ["preceding"],
         "timing": {"end_frames": [229, 240, 250], "min_duration": 229 / 24}},
        {"slot": 11, "start": 63.99, "duration": 22 / 24, "candidate_ids": ["paperwork"],
         "timing": {"end_frames": [251, 255, 262, 272], "min_duration": 1 / 24}},
        {"slot": 12, "start": 53.99 + 262 / 24, "duration": 26 / 24, "candidate_ids": ["following"],
         "timing": {"end_frames": [288], "min_duration": 16 / 24}},
    ]
    output = {"choices": {"shot_10": _choice(frame=240), "shot_11": _choice("c1", frame=262),
                           "shot_12": _choice("c2", frame=288)}}
    assert sources["paperwork"]["t_start"] + 22 / 24 > sources["paperwork"]["t_end"]
    result = selection_choices(output, offers, sources, scope)
    assert [row["candidate_id"] for row in result] == list(sources)
    assert [row["end_frame"] for row in result] == [240, 255, 288]
    assert result[1]["source_start"] == sources["paperwork"]["t_start"]
    assert result[1]["duration"] == pytest.approx(.625)
    assert result[1]["source_start"] + result[1]["duration"] <= sources["paperwork"]["t_end"]


@pytest.mark.parametrize("position", [0., .5, 1.])
def test_normalized_position_resolves_only_after_the_actual_duration(position):
    offers, scope = _timed()
    output = {"choices": {"shot_0": _choice(frame=48, position=position), "shot_1": _choice("c2", frame=144)}}
    result = selection_choices(output, offers, _sources(), scope)
    assert result[0]["source_start"] == 10. + position * 8.
    assert [row["end_frame"] for row in result] == [48, 144]


@pytest.mark.parametrize("position", [-.001, 1.001, True, "0.5", float("nan"), float("inf"), 6921.623041666666])
def test_timed_positions_reject_outside_values_and_old_absolute_timestamps(position):
    offers, scope = _timed()
    output = {"choices": {"shot_0": _choice(frame=48, position=position), "shot_1": _choice("c2", frame=144)}}
    with pytest.raises(ValueError):
        selection_choices(output, offers, _sources(), scope)


def test_old_authoritative_cut_field_is_not_a_preference_compatibility_path():
    offers, scope = _timed()
    output = {"choices": {"shot_0": _choice(frame=48), "shot_1": _choice("c2", frame=144)}}
    output["choices"]["shot_0"]["end_frame"] = output["choices"]["shot_0"].pop("preferred_end_frame")
    with pytest.raises(ValueError):
        selection_choices(output, offers, _sources(), scope)


@pytest.mark.parametrize("frame", [47, 144, True, "48", 48.0])
def test_timed_choice_rejects_another_slots_cut_or_noninteger_frame(frame):
    offers, scope = _timed()
    output = {"choices": {"shot_0": _choice(frame=frame), "shot_1": _choice("c1", 30., frame=144)}}
    with pytest.raises(ValueError):
        selection_choices(output, offers, _sources(), scope)


def test_reported_frame_1184_remains_unavailable_in_the_source_bound_contract():
    allowed = [1176, 1189, 1200, 1204, 1219, 1234, 1235]
    offer = {"slot": 15, "start": 48., "duration": 2., "candidate_ids": [],
             "timing": {"end_frames": allowed, "min_duration": 1 / 24}}
    scope = {"passage": {"start": 0., "end": 90.}, "fps": 24, "total_frames": 2160}
    schema = selection_schema([offer], {}, scope)
    assert schema["properties"]["choices"]["properties"]["shot_15"]["properties"]["preferred_end_frame"]["enum"] == allowed
    with pytest.raises(ValueError):
        selection_choices({"choices": {"shot_15": _choice(None, frame=1184)}}, [offer], {}, scope)


def test_nocturne_mixed_source_timestamp_is_rejected_by_schema_and_runtime():
    sources = {"chosen-scene": {"film_id": "film-a", "t_start": 3004.5015, "t_end": 3008.7140416666666},
               "other-scene": {"film_id": "film-b", "t_start": 1597.7628333333332, "t_end": 1601.6417083333333}}
    offers = [{"slot": 12, "start": 93.99, "duration": 3.75, "candidate_ids": list(sources)}]
    schema = selection_schema(offers, sources)
    assert not _source_allowed(schema, "shot_12", {"c0": 1597.8})
    assert _source_allowed(schema, "shot_12", {"c1": 1597.8})
    with pytest.raises(ValueError, match="timing outside"):
        selection_choices({"choices": {"shot_12": _choice("c0", 1597.8)}}, offers, sources)


@pytest.mark.parametrize("count,timed,expected_properties,expected_enums", [(100, False, 2701, 0), (32, True, 897, 224)])
def test_full_offers_fit_provider_schema_budgets_without_losing_candidates(count, timed, expected_properties, expected_enums):
    offers, sources = [], {}
    for index in range(count):
        identities = [f"source-{index}-{candidate}" for candidate in range(24)]
        sources.update({identity: {"film_id": identity, "t_start": 0., "t_end": 20.} for identity in identities})
        offers.append({"slot": index, "start": index * 3., "duration": 3., "candidate_ids": identities,
                       "timing": {"end_frames": list(range((index + 1) * 72 - 6, (index + 1) * 72 + 1)), "min_duration": 2.}})
    scope = {"passage": {"start": 0., "end": count * 3.}, "fps": 24, "total_frames": count * 72} if timed else None
    schema = selection_schema(offers, sources, scope)
    assert len(schema["properties"]["choices"]["required"]) == count
    assert _schema_size(schema) == (expected_properties, expected_enums, 4)
    assert all(len(_source_branches(schema, f"shot_{index}")) == 25 for index in range(count))
    output = {"choices": {f"shot_{index}": _choice(f"c{index * 24}", 0., frame=(index + 1) * 72 if timed else None) for index in range(count)}}
    assert len(selection_choices(output, offers, sources, scope)) == count


@pytest.mark.parametrize("count,timed", [(0, False), (101, False), (0, True), (33, True)])
def test_selection_schema_rejects_unsupported_scope_size(count, timed):
    offers = [{"slot": index, "start": index * 3., "duration": 3., "candidate_ids": ["scene-a"],
               "timing": {"end_frames": [(index + 1) * 72], "min_duration": 2.}} for index in range(count)]
    with pytest.raises(ValueError):
        selection_schema(offers, _sources(), {"fps": 24} if timed else None)


def test_future_oversized_offers_are_rejected_before_a_hosted_request():
    sources = {f"source-{index}": {"film_id": "film", "t_start": 0., "t_end": 20.} for index in range(50)}
    offers = [{"slot": index, "start": index * 3., "duration": 3., "candidate_ids": list(sources)} for index in range(100)]
    with pytest.raises(ValueError, match="schema limits"):
        selection_schema(offers, sources)


def test_duplicate_offered_slots_cannot_silently_collapse_required_decisions():
    offers = [_offers()[0], deepcopy(_offers()[0])]
    with pytest.raises(ValueError):
        selection_schema(offers, _sources())
    with pytest.raises(ValueError):
        selection_choices({"choices": {"shot_2": _choice()}}, offers, _sources())


def test_manifest_preserves_the_exact_alias_map_source_bounds_and_timing():
    offers, scope = _timed()
    sources = _sources()
    manifest = selection_manifest(offers, sources, scope)
    assert manifest["contract"] == TIMED_SELECTION_CONTRACT
    assert manifest["alias_to_source"] == {"c0": "scene-a", "c1": "scene-b", "c2": "scene-c"}
    assert manifest["sources"]["scene-a"] == {"film_id": "film-a", "t_start": 10., "t_end": 20.}
    offers[0]["candidate_ids"].clear()
    scope["passage"]["start"] = 0.
    sources["scene-a"]["t_start"] = 0.
    assert manifest["shots"]["shot_0"]["candidate_ids"] == ["scene-a", "scene-b"]
    assert manifest["timing_scope"]["passage"]["start"] == 53.99
    assert manifest["sources"]["scene-a"]["t_start"] == 10.
