"""A joint proposal owns choices, never source authority or the saved input."""
from copy import deepcopy
import json

import pytest

from pipeline.experiments.assembly_sequence import (
    assembly_music_evidence, assembly_schema, assemble_document, build_assembly_payload,
    build_assembly_prompt, fixed_baseline_document, fixed_baseline_payload, validate_assembly_input,
)
from pipeline.lab.models import ProjectDocument


def _document(*, start=53.99, duration=30.):
    passage = {"start": start, "end": start + duration}
    document = ProjectDocument(track={"id": "track", "name": "Frozen song", "duration": start + duration},
        passage=passage, editor_direction={"instruction": "Develop distance through blue images.",
            "ranges": [{"id": "detail", "start": start + 10, "end": start + 16, "instruction": "Let one image breathe."}]}).model_dump(mode="json")
    document["analysis"] = {"provenance": {"track": "track", "passage": passage}, "summary": "Repeated notes gather tension.",
        "segments": [{"start": start, "end": start + duration, "feeling": "Tension", "energy": .7,
                      "query": "OLD QUERY", "imagery": "OLD IMAGE", "search_facet": "all"}],
        "edit_beats": [{"start": start, "end": start + duration, "query": "OLD CUT"}]}
    document["rhythm"] = {"provenance": {"track": "track", "passage": passage},
        "beats": [start + i for i in range(int(duration))], "downbeats": [start, start + 4],
        "markers": [start + 3], "marker_source": "derived", "intensity": [.2, .8],
        "waveform_start": start, "waveform_end": start + duration}
    document["music_timeline"] = {"track_id": "track", "passage": passage, "slots": [
        {"id": f"old-{i}", "start": start + i * duration / 3, "end": start + (i + 1) * duration / 3,
         "section_index": 0, "direction": {"query": f"Old intention {i}"}, "direction_source": "ai"} for i in range(3)]}
    document["visual_plan"] = {"arc": "OLD ARC", "motifs": "OLD MOTIFS", "source": "ai"}
    return ProjectDocument.model_validate(document).model_dump(mode="json")


def _catalog(count=8):
    return {f"unit-{i}": {"unit_id": f"unit-{i}", "film_id": f"film-{i // 2}", "film_title": f"Film {i // 2}",
            "t_start": i * 100., "t_end": i * 100. + 40., "caption": f"Distinct visible image {i}",
            "metadata": {"palette": ["blue"], "camera_motion": "uncertain"},
            "query_evidence": [{"query_id": "q1", "rank": i + 1, "intention_ids": ["opening"],
                "reason": "A relation of distance", "evidence": {"matched_frame_timestamp": i * 100. + 2}}]}
            for i in range(count)}


def _capabilities():
    return {"facets": [{"facet": facet, "text_available": facet not in {"words", "composition"},
        "source_available": False, "evidence": "Sparse indexed evidence"}
        for facet in ("all", "scene", "words", "look", "mood", "composition")], "unsupported": ["verified movement"]}


def _regions(document):
    start, end = document["passage"]["start"], document["passage"]["end"]
    return [{"id": "opening", "start": start, "end": start + 15, "intention": "Accumulate restrained tension"},
            {"id": "response", "start": start + 15, "end": end, "intention": "Let the relation open without resolving it"}]


def _output(frames=(240, 480, 720)):
    return {"shots": [{"source": {f"c{i}": .5}, "end_frame": frame,
                       "reason": f"Use distinct image {i} as a response to its neighbor"}
                      for i, frame in enumerate(frames)], "discovery_needs": []}


def _need(facet="all"):
    return {"reason": "Need a visible distant pair for a meaningful contrast", "search_plan": {
        "clauses": [{"kind": "text", "facet": facet, "text": "two people far apart", "reference_id": None}],
        "unverified_requirements": []}}


def test_joint_allows_offbeat_burst_and_hold_across_soft_regions_without_mutation():
    document, catalog = _document(), _catalog()
    catalog["unit-0"]["t_end"] = .5  # Useful insert, too short for every old fixed slot.
    before = deepcopy((document, catalog))
    payload = build_assembly_payload(document, catalog, _capabilities(), regions=_regions(document), discovery_enabled=True)
    output = _output((7, 8, 401, 720))
    result, diagnostic = assemble_document(document, output, catalog)
    slots = result["music_timeline"]["slots"]
    assert [row["end_frame"] for row in diagnostic["choices"]] == [7, 8, 401, 720]
    assert slots[2]["start"] < payload["intentions"][0]["end"] < slots[2]["end"]
    assert slots[0]["end"] not in payload["music_evidence"]["measured"]["beats"]
    assert slots[1]["end"] - slots[1]["start"] == pytest.approx(1 / 24)
    assert slots[0]["start"] == document["passage"]["start"]
    assert slots[-1]["end"] == document["passage"]["end"]
    assert all(a["end"] == b["start"] for a, b in zip(slots, slots[1:]))
    assert len(slots) == 4 and all(slot["clip_id"] for slot in slots)
    assert result["editor_direction"] == document["editor_direction"]
    assert (document, catalog) == before
    assert assemble_document(document, output, catalog)[0] == result


def test_positions_resolve_only_after_actual_duration_and_keep_exact_audio_endpoint():
    document, catalog = _document(duration=29.99), _catalog()
    output = _output()
    for shot, position in zip(output["shots"], [0., .5, 1.]):
        shot["source"][next(iter(shot["source"]))] = position
    result, diagnostic = assemble_document(document, output, catalog)
    assert result["clips"][0]["source_start"] == 0
    assert result["clips"][1]["source_start"] == pytest.approx(115.)
    assert result["clips"][2]["source_end"] == pytest.approx(240.)
    assert result["music_timeline"]["slots"][-1]["end"] == document["passage"]["end"]
    assert diagnostic["choices"][-1]["source_end"] - diagnostic["choices"][-1]["source_start"] == pytest.approx(9.99)


@pytest.mark.parametrize("fault", ["last", "duplicate-cut", "reverse", "float-frame", "bool-frame", "unknown-source",
    "repeat", "null", "multiple-sources", "nan", "inf", "bool-position", "string-position", "position-range",
    "short-source", "overlap", "extra-field", "empty-reason", "too-many", "missing-shots"])
def test_invalid_joint_output_fails_without_repair_or_input_changes(fault):
    document, catalog, output = _document(), _catalog(72), _output()
    if fault == "last": output["shots"][-1]["end_frame"] = 719
    elif fault == "duplicate-cut": output["shots"][1]["end_frame"] = 240
    elif fault == "reverse": output["shots"][1]["end_frame"] = 100
    elif fault == "float-frame": output["shots"][0]["end_frame"] = 240.
    elif fault == "bool-frame": output["shots"][0]["end_frame"] = True
    elif fault == "unknown-source": output["shots"][0]["source"] = {"c999": .5}
    elif fault == "repeat": output["shots"][1]["source"] = {"c0": 1.}
    elif fault == "null": output["shots"][0]["source"] = None
    elif fault == "multiple-sources": output["shots"][0]["source"] = {"c0": 0., "c1": 1.}
    elif fault in {"nan", "inf"}: output["shots"][0]["source"] = {"c0": float(fault)}
    elif fault == "bool-position": output["shots"][0]["source"] = {"c0": True}
    elif fault == "string-position": output["shots"][0]["source"] = {"c0": "0.5"}
    elif fault == "position-range": output["shots"][0]["source"] = {"c0": 1.1}
    elif fault == "short-source": catalog["unit-0"]["t_end"] = 2
    elif fault == "overlap": catalog["unit-1"].update(t_start=0., t_end=40.)
    elif fault == "extra-field": output["shots"][0]["source_start"] = 0.
    elif fault == "empty-reason": output["shots"][0]["reason"] = "   "
    elif fault == "too-many": output = _output((*range(1, 65), 720))
    elif fault == "missing-shots": output["shots"] = []
    before_document, before_catalog = deepcopy(document), deepcopy(catalog)
    with pytest.raises(ValueError):
        assemble_document(document, output, catalog, candidate_limit=72)
    assert document == before_document and catalog == before_catalog


def test_last_rounded_output_frame_must_still_have_a_real_frame_of_source_time():
    with pytest.raises(ValueError, match="less than one output frame"):
        assemble_document(_document(duration=29.99), _output((719, 720)), _catalog())


def test_distinct_nonoverlapping_shots_from_same_film_are_permitted():
    document, diagnostic = assemble_document(_document(), _output(), _catalog())
    assert document["clips"][0]["film_id"] == document["clips"][1]["film_id"]
    assert diagnostic["selected_count"] == 3


def test_sixty_four_shots_and_expanded_pool_are_ceilings_not_targets():
    result, _ = assemble_document(_document(), _output((*range(1, 64), 720)), _catalog(72), candidate_limit=72)
    assert len(result["music_timeline"]["slots"]) == 64
    with pytest.raises(ValueError, match="48 frozen"):
        build_assembly_payload(_document(), _catalog(49), _capabilities())
    with pytest.raises(ValueError, match="72 frozen"):
        assemble_document(_document(), _output(), _catalog(73), candidate_limit=72)
    initial = build_assembly_payload(_document(), _catalog(48), _capabilities())
    expanded = build_assembly_payload(_document(), _catalog(72), _capabilities(), candidate_limit=72, previous_draft=_output())
    assert {key: expanded["sources"][key] for key in initial["sources"]} == initial["sources"]
    assert expanded["previous_draft"] == _output()


def test_joint_schema_offers_only_source_aliases_and_any_integer_frame():
    payload = build_assembly_payload(_document(), _catalog(), _capabilities(), discovery_enabled=True)
    schema = assembly_schema(payload)
    shot = schema["$defs"]["AssemblyShot"]
    assert shot["properties"]["end_frame"]["type"] == "integer"
    assert shot["properties"]["end_frame"]["maximum"] == 720
    assert "enum" not in shot["properties"]["end_frame"]
    assert [list(branch["properties"]) for branch in shot["properties"]["source"]["anyOf"]] == [[f"c{i}"] for i in range(8)]
    assert schema["properties"]["shots"]["maxItems"] == 64
    assert schema["properties"]["discovery_needs"]["maxItems"] == 2
    payload["max_discovery_needs"] = 0
    assert assembly_schema(payload)["properties"]["discovery_needs"]["maxItems"] == 0


def test_music_projection_does_not_leak_prior_shot_prescriptions():
    document = _document()
    before = deepcopy(document)
    evidence = assembly_music_evidence(document)
    text = json.dumps(evidence)
    assert "OLD" not in text and "old-0" not in text
    assert "slots" not in evidence["measured"]["relative_rms"]
    assert "markers" not in evidence["measured"]
    assert evidence["measured"]["beats"] == document["rhythm"]["beats"]
    assert evidence["audio_interpretation"]["segments"][0]["feeling"] == "Tension"
    assert evidence["editor_direction"]["ranges"][0]["instruction"] == "Let one image breathe."
    document["music_timeline"]["slots"][0]["direction"]["query"] = "A DIFFERENT OLD QUERY"
    document["analysis"]["edit_beats"] = []
    assert assembly_music_evidence(document) == evidence
    assert before["analysis"]["edit_beats"]
    payload = build_assembly_payload(before, _catalog(), _capabilities())
    assert payload["sources"]["c0"]["query_evidence"] == _catalog()["unit-0"]["query_evidence"]
    prompt = build_assembly_prompt(payload)
    assert "ANY legal interior output frame" in prompt
    assert json.loads(prompt.split("\n", 1)[1]) == payload


def test_discovery_needs_only_use_verified_adapters_with_one_bounded_followup():
    output = _output()
    output["discovery_needs"] = [_need()]
    _, diagnostic = assemble_document(_document(), output, _catalog(), capabilities=_capabilities(), discovery_enabled=True)
    assert diagnostic["discovery_needs"] == [_need()]
    with pytest.raises(ValueError, match="not enabled"):
        assemble_document(_document(), output, _catalog(), capabilities=_capabilities())
    output["discovery_needs"] = [_need("words")]
    with pytest.raises(ValueError, match="not verified ready"):
        assemble_document(_document(), output, _catalog(), capabilities=_capabilities(), discovery_enabled=True)
    output["discovery_needs"] = [_need()] * 3
    with pytest.raises(ValueError):
        assemble_document(_document(), output, _catalog(), capabilities=_capabilities(), discovery_enabled=True)


@pytest.mark.parametrize("fault", ["lock", "scope", "bounds", "unit-id", "bool-bounds", "duration"])
def test_preflight_rejects_protected_or_invalid_frozen_inputs(fault):
    document, catalog = _document(), _catalog()
    if fault == "lock":
        document["clips"] = [{"id": "locked", "film_id": "film-0", "source_start": 0., "source_end": 10., "locked": True}]
        document["music_timeline"]["slots"][0]["clip_id"] = "locked"
    elif fault == "scope": document["film_ids"] = ["film-0"]
    elif fault == "bounds": catalog["unit-0"]["t_start"] = -1.
    elif fault == "unit-id": catalog["unit-0"]["unit_id"] = "impostor"
    elif fault == "bool-bounds": catalog["unit-0"]["t_start"] = True
    elif fault == "duration": document = _document(duration=46.)
    with pytest.raises(ValueError):
        build_assembly_payload(document, catalog, _capabilities())
    with pytest.raises(ValueError):
        fixed_baseline_payload(document, catalog, _capabilities())


def test_fixed_baseline_uses_current_selector_and_preserves_exact_slots_and_queries():
    document, catalog = _document(), _catalog()
    catalog["unit-0"]["t_end"] = 2.
    bundle = fixed_baseline_payload(document, catalog, _capabilities(), regions=_regions(document))
    assert all("unit-0" not in offer["candidate_ids"] for offer in bundle["offers"])
    assert bundle["manifest"]["alias_to_source"]["c0"] == "unit-1"
    assert bundle["payload"]["sources"]["c0"]["query_evidence"] == catalog["unit-1"]["query_evidence"]
    output = {"choices": {f"shot_{i}": {"source": {f"c{i}": (i + 1) * 100.}, "reason": f"Grounded choice {i}"} for i in range(3)}}
    before = deepcopy((document, catalog, bundle))
    result, diagnostic = fixed_baseline_document(document, output, catalog, bundle)
    for old, new in zip(document["music_timeline"]["slots"], result["music_timeline"]["slots"]):
        assert {key: new[key] for key in ("id", "start", "end", "direction", "direction_source")} == {
            key: old[key] for key in ("id", "start", "end", "direction", "direction_source")}
    assert diagnostic["remaining_gaps"] == 0 and diagnostic["selected_count"] == 3
    assert (document, catalog, bundle) == before


def test_fixed_baseline_retains_explicit_abstention_and_rejects_reused_footage():
    document, catalog = _document(), _catalog()
    bundle = fixed_baseline_payload(document, catalog, _capabilities())
    output = {"choices": {f"shot_{i}": {"source": None, "reason": "No supported footage for this intention"} for i in range(3)}}
    result, diagnostic = fixed_baseline_document(document, output, catalog, bundle)
    assert diagnostic["remaining_gaps"] == 3 and not result["clips"]
    for i in range(2):
        output["choices"][f"shot_{i}"]["source"] = {"c0": i * 10.}
    with pytest.raises(ValueError, match="reused"):
        fixed_baseline_document(document, output, catalog, bundle)


def test_fixed_baseline_rejects_offer_authority_changed_after_freezing():
    document, catalog = _document(), _catalog()
    bundle = fixed_baseline_payload(document, catalog, _capabilities())
    catalog["unit-0"]["t_end"] += 1.
    with pytest.raises(ValueError, match="offers changed"):
        fixed_baseline_document(document, {}, catalog, bundle)


def test_preflight_accepts_pilot_limit_and_requires_baseline_before_discovery():
    document = _document(duration=45.)
    assert validate_assembly_input(document, require_timeline=True) == document
    document["music_timeline"] = None
    assert validate_assembly_input(document)["music_timeline"] is None
    with pytest.raises(ValueError, match="frozen timeline"):
        validate_assembly_input(document, require_timeline=True)
