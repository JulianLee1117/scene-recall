"""Discovery keeps source authority, bounded breadth and failure provenance."""
from copy import deepcopy
import json

import lancedb
import pytest

from pipeline.experiments import assembly_discovery as discovery
from pipeline.lab.models import ProjectDocument


def document():
    return ProjectDocument(track={"id": "song", "name": "Music", "duration": 90.},
        passage={"start": 10., "end": 40.}, fps=24).model_dump(mode="json")


def capabilities():
    return {"version": "existing-search-adapters-v1", "facets": [
        {"facet": name, "text_available": name != "composition", "source_available": name != "all"}
        for name in ("all", "scene", "words", "look", "mood", "composition")]}


def plan(text, facet="scene"):
    return {"clauses": [{"kind": "text", "facet": facet, "text": text, "reference_id": None}],
            "unverified_requirements": []}


def intent(*plans):
    return {"intentions": [{"id": "intro", "start": 10., "end": 20., "intention": "Tense enclosed images"},
                            {"id": "release", "start": 20., "end": 40., "intention": "Open blue spaces"}],
            "recipes": [{"intention_ids": ["intro", "release"], "reason": f"Visual role {index}", "search_plan": value}
                        for index, value in enumerate(plans or [plan("water"), plan("sky")])]}


def row(identity, film="film", start=0., end=10., **fields):
    return {"unit_id": identity, "film_id": film, "t_start": start, "t_end": end,
            "caption": f"Canonical {identity}", "framing": "wide", **fields}


@pytest.fixture
def library(tmp_path, monkeypatch):
    databases = []

    def create(query_rows, *, unit_rows=None, films=None):
        db = lancedb.connect(str(tmp_path / f"index-{len(databases)}"))
        databases.append(db)
        unique = {item["unit_id"]: item for values in query_rows.values() for item in values
                  if isinstance(item, dict) and item.get("unit_id") and item.get("film_id")}
        canonical = list(unique.values()) if unit_rows is None else unit_rows
        db.create_table("units", data=[{key: value for key, value in item.items() if key in
            {"unit_id", "film_id", "t_start", "t_end", "caption", "framing"}} for item in canonical])
        if films is None:
            path = tmp_path / "film.mkv"
            path.write_bytes(b"source-presence-fixture")
            films = [{"film_id": identity, "title": f"Title {identity}", "duration": 10000., "path": str(path)}
                     for identity in {item["film_id"] for item in canonical}]
        db.create_table("films", data=films)
        calls = []

        def search(resolved, current, config, actual_db):
            assert actual_db is db
            calls.append({"resolved": deepcopy(resolved), "film_ids": list(current["film_ids"])})
            key = resolved["clauses"][0]["text"]
            value = query_rows.get(key, [])
            if isinstance(value, Exception):
                raise value
            return deepcopy(value)

        monkeypatch.setattr("pipeline.lab.search_plan.execute_search", search)
        return db, calls

    return create


def test_intent_contract_is_broad_bounded_and_capability_checked():
    value = discovery.validate_discovery_intent(intent(), document(), capabilities())
    assert value["contract"] == discovery.INTENT_CONTRACT
    assert discovery.discovery_schema()["properties"]["recipes"]["maxItems"] == 6
    assert discovery.discovery_need_schema()["additionalProperties"] is False
    bad = intent()
    bad["shots"] = []
    with pytest.raises(ValueError):
        discovery.validate_discovery_intent(bad, document(), capabilities())
    unavailable = capabilities()
    unavailable["facets"][1]["text_available"] = None
    with pytest.raises(ValueError, match="not verified ready"):
        discovery.validate_discovery_intent(intent(), document(), unavailable)


@pytest.mark.parametrize("change", [
    lambda value: value["intentions"][0].update(start=0.),
    lambda value: value["intentions"][1].update(start=19.),
    lambda value: value["intentions"][1].update(id="intro"),
    lambda value: value["recipes"][0].update(intention_ids=["missing"]),
    lambda value: value["recipes"].append(deepcopy(value["recipes"][0])),
])
def test_rejects_invalid_region_or_recipe_authority(change):
    value = intent()
    change(value)
    with pytest.raises(ValueError):
        discovery.validate_discovery_intent(value, document(), capabilities())


def test_rejects_invented_reference_and_unsupported_operation():
    source_plan = {"clauses": [{"kind": "source", "facet": "composition", "text": None, "reference_id": "invented"}],
                   "unverified_requirements": []}
    with pytest.raises(ValueError, match="unavailable reference"):
        discovery.validate_discovery_needs([{"reason": "Need matching framing", "search_plan": source_plan}], capabilities())
    with pytest.raises(ValueError):
        discovery.validate_discovery_needs([{"reason": "Need movement", "search_plan": plan("spinning", "motion")}], capabilities())
    reference = {"reference_id": "real", "clip_id": "clip", "film_id": "film", "unit_id": "unit",
                 "frame_index": 0, "timestamp": 2., "source_start": 0., "source_end": 5.,
                 "available_facets": ["composition"]}
    source_plan["clauses"][0]["reference_id"] = "real"
    assert discovery.validate_discovery_needs([{"reason": "Match framing", "search_plan": source_plan}],
                                              capabilities(), [reference])


def test_all_returned_rows_survive_while_catalog_interleaves_queries(library, config):
    rows = {"water": [row(f"a-{i}", matched_text=f"water evidence {i}") for i in range(60)],
            "sky": [row(f"b-{i}", matched_text=f"sky evidence {i}") for i in range(48)]}
    db, calls = library(rows)
    original = document()
    before = deepcopy(original)
    result = discovery.discover_candidates(intent(), original, config, db, capabilities=capabilities())
    assert original == before
    assert [len(query["rows"]) for query in result["queries"]] == [48, 48]
    assert len(result["sources"]) == 96 and len(result["catalog"]) == 48
    assert list(result["catalog"])[:6] == ["a-0", "b-0", "a-1", "b-1", "a-2", "b-2"]
    assert {source["film_id"] for source in result["catalog"].values()} == {"film"}
    assert result["catalog"]["a-0"]["metadata"]["framing"] == "wide"
    assert calls[0]["resolved"]["min_duration"] == 1 / 24


def test_deduplicates_source_identity_without_overwriting_query_evidence(library, config):
    rows = {"water": [row("same", matched_text="Water match", matched_frame_index=1, matched_frame_timestamp=1.)],
            "sky": [row("same", caption="Public result caption", matched_text="Sky match", matched_frame_index=2,
                        matched_frame_timestamp=2.)]}
    db, _ = library(rows, unit_rows=[row("same", caption="Authoritative caption")])
    result = discovery.discover_candidates(intent(), document(), config, db, capabilities=capabilities())
    source = result["catalog"]["same"]
    assert source["caption"] == "Authoritative caption"
    assert [e["evidence"]["matched_frame_timestamp"] for e in source["query_evidence"]] == [1., 2.]
    assert [e["evidence"]["matched_text"] for e in source["query_evidence"]] == ["Water match", "Sky match"]


def test_exclusion_ledger_keeps_scope_media_duration_reuse_and_invalid_rows(library, config, tmp_path):
    playable = tmp_path / "valid.mkv"
    playable.write_bytes(b"fixture")
    rows = {"water": [row("valid"), row("outside", film="other"), row("missing", film="missing"),
                      row("over", end=100.), row("short", end=.01), row("reused", start=20., end=25.),
                      row("overlap", start=30., end=35.), {"caption": "malformed"}], "sky": []}
    films = [{"film_id": "film", "title": "Film", "duration": 80., "path": str(playable)},
             {"film_id": "other", "title": "Other", "duration": 80., "path": str(playable)},
             {"film_id": "missing", "title": "Missing", "duration": 80., "path": str(tmp_path / "absent.mkv")}]
    db, calls = library(rows, films=films)
    current = {**document(), "film_ids": ["film", "missing"]}
    previous = [{"film_id": "film", "unit_id": "reused", "source_start": 20., "source_end": 21.},
                {"film_id": "film", "unit_id": "another", "source_start": 34., "source_end": 37.}]
    result = discovery.discover_candidates(intent(), current, config, db, capabilities=capabilities(), previous_sources=previous)
    assert list(result["catalog"]) == ["valid"]
    assert [value["exclusion"] for value in result["queries"][0]["rows"]] == [None, "outside-film-scope",
        "source-media-unavailable", "outside-source-film", "less-than-one-output-frame", "previous-footage",
        "previous-footage", "invalid-source"]
    assert calls[0]["film_ids"] == ["film", "missing"]


def test_source_changes_fail_with_complete_previous_query_checkpoint(library, config):
    rows = {"water": [row("same")], "sky": [row("same", end=11.), row("remaining")]}
    db, _ = library(rows, unit_rows=[row("same")])
    checkpoints = []
    with pytest.raises(ValueError, match="Canonical source identity changed"):
        discovery.discover_candidates(intent(), document(), config, db, capabilities=capabilities(), checkpoint=checkpoints.append)
    failed = checkpoints[-1]
    assert failed["status"] == "failed" and failed["queries"][0]["status"] == "completed"
    assert failed["queries"][1]["rows"][0]["row"]["t_end"] == 11.
    assert failed["queries"][1]["rows"][1]["row"]["unit_id"] == "remaining"
    assert failed["queries"][1]["status"] == "failed"
    assert list(failed["catalog"]) == ["same"]
    assert failed["search_execution_count"] == 2


def test_hydration_rejects_result_missing_from_canonical_index(library, config):
    rows = {"water": [row("invented")], "sky": []}
    db, _ = library(rows, unit_rows=[row("real")])
    with pytest.raises(ValueError, match="no longer exists"):
        discovery.discover_candidates(intent(), document(), config, db, capabilities=capabilities())


def test_identical_recipe_revisits_ledger_without_search_and_preserves_catalog(library, config):
    rows = {"water": [row(f"a-{i}") for i in range(48)], "sky": [row(f"b-{i}") for i in range(48)]}
    db, calls = library(rows)
    original = discovery.discover_candidates(intent(), document(), config, db, capabilities=capabilities())
    before = deepcopy(original)
    expanded = discovery.expand_candidates(original, [{"reason": "Need more water imagery", "search_plan": plan("water")}],
        document(), config, db, capabilities=capabilities())
    assert original == before
    assert len(calls) == 2 and expanded["search_request_count"] == 3 and expanded["search_execution_count"] == 2
    assert len(expanded["catalog"]) == 72
    assert list(expanded["catalog"])[:48] == list(original["catalog"])
    assert all(expanded["catalog"][key] == value for key, value in original["catalog"].items())
    assert list(expanded["catalog"])[48:] == [f"a-{i}" for i in range(24, 48)]
    assert expanded["followups"][0]["revisits"][0]["basis"] == "exact-recipe"


def test_empty_followup_can_revisit_exact_shared_clause_but_not_unrelated_rows(library, config):
    combined = plan("water")
    combined["clauses"].append(plan("calm", "mood")["clauses"][0])
    rows = {"water": [row(f"a-{i}") for i in range(48)], "sky": [row(f"b-{i}") for i in range(48)]}
    db, calls = library(rows)
    original = discovery.discover_candidates(intent(combined, plan("sky")), document(), config, db, capabilities=capabilities())
    rows["water"] = []
    expanded = discovery.expand_candidates(original, [{"reason": "More water", "search_plan": plan("water")}],
        document(), config, db, capabilities=capabilities())
    assert len(calls) == 3 and len(expanded["queries"][-1]["rows"]) == 0
    assert len(expanded["catalog"]) == 72
    assert expanded["followups"][0]["revisits"][0]["basis"] == "shared-executable-clause"
    unrelated = discovery.expand_candidates(original, [{"reason": "Need an empty street", "search_plan": plan("street")}],
        document(), config, db, capabilities=capabilities())
    assert unrelated["catalog"] == original["catalog"]
    assert unrelated["followups"][0]["revisits"] == []


def test_expansion_budget_is_cumulative_and_does_not_fill_unused_initial_capacity(library, config):
    rows = {"water": [row("a")], "sky": [row("b")], "new": [row(f"new-{i}") for i in range(48)]}
    db, calls = library(rows)
    original = discovery.discover_candidates(intent(), document(), config, db, capabilities=capabilities())
    expanded = discovery.expand_candidates(original, [{"reason": "New role", "search_plan": plan("new")}],
        document(), config, db, capabilities=capabilities())
    assert len(expanded["catalog"]) == 26
    again = discovery.expand_candidates(expanded, [{"reason": "Revisit sky", "search_plan": plan("sky")}],
        document(), config, db, capabilities=capabilities())
    assert len(again["catalog"]) == 26 and again["followup_count"] == 2
    call_count = len(calls)
    with pytest.raises(ValueError, match="budget"):
        discovery.expand_candidates(again, [{"reason": "Extra", "search_plan": plan("extra")}],
            document(), config, db, capabilities=capabilities())
    assert len(calls) == call_count


def test_query_failure_checkpoints_error_and_never_changes_input_state(library, config):
    rows = {"water": [row("a")], "sky": [row("b")]}
    db, _ = library(rows)
    original = discovery.discover_candidates(intent(), document(), config, db, capabilities=capabilities())
    before = deepcopy(original)
    rows["failed"] = RuntimeError("adapter unavailable")
    checkpoints = []
    with pytest.raises(RuntimeError, match="adapter unavailable"):
        discovery.expand_candidates(original, [{"reason": "Missing role", "search_plan": plan("failed")}],
            document(), config, db, capabilities=capabilities(), checkpoint=checkpoints.append)
    assert original == before
    failed = checkpoints[-1]
    assert failed["queries"][-1]["error"] == "RuntimeError: adapter unavailable"
    assert failed["catalog"] == original["catalog"]
    assert failed["followups"][-1]["need"]["reason"] == "Missing role"
    json.dumps(failed, allow_nan=False)


def test_six_initial_and_two_new_queries_are_the_complete_budget(library, config):
    rows = {f"query-{index}": [row(f"unit-{index}")] for index in range(8)}
    db, calls = library(rows)
    original = discovery.discover_candidates(intent(*(plan(f"query-{i}") for i in range(6))),
        document(), config, db, capabilities=capabilities())
    expanded = discovery.expand_candidates(original, [{"reason": f"Missing role {i}", "search_plan": plan(f"query-{i}")}
        for i in (6, 7)], document(), config, db, capabilities=capabilities())
    assert len(calls) == expanded["search_request_count"] == expanded["search_execution_count"] == 8
    assert list(expanded["catalog"]) == [f"unit-{i}" for i in range(8)]


def test_expansion_cannot_change_frozen_film_scope_or_exclusions(library, config):
    db, calls = library({"water": [row("a")], "sky": []})
    original = discovery.discover_candidates(intent(), document(), config, db, capabilities=capabilities())
    with pytest.raises(ValueError, match="frozen"):
        discovery.expand_candidates(original, [{"reason": "New", "search_plan": plan("new")}],
            {**document(), "film_ids": ["different"]}, config, db, capabilities=capabilities())
    assert len(calls) == 2


def test_discovery_evidence_uses_shared_neutral_projection_without_previous_cuts():
    value = document()
    value["editor_direction"] = {"instruction": "Keep blue night imagery", "ranges": []}
    value["visual_plan"] = {"source": "ai", "arc": "OLD STORY", "motifs": ["OLD MOTIF"]}
    value["analysis"] = {"summary": "Measured song", "provenance": {"track": "song", "passage": value["passage"]},
                         "edit_beats": [{"query": "OLD SHOT"}],
                         "segments": [{"start": 10., "end": 40., "feeling": "tense", "imagery": "OLD IMAGE",
                                      "query": "OLD SEARCH", "search_facet": "scene"}]}
    payload = discovery.discovery_payload(value, capabilities())
    assert payload["references"] == []
    encoded = json.dumps(payload)
    for old in ("OLD STORY", "OLD MOTIF", "OLD SHOT", "OLD IMAGE", "OLD SEARCH"):
        assert old not in encoded
    assert "Keep blue night imagery" in encoded
    assert payload["music_evidence"]["audio_interpretation"]["summary"] == "Measured song"
    assert payload["music_evidence"]["projection_contract"] == "assembly-music-without-previous-cuts-v1"
