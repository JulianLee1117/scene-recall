"""Optional context cannot alter retrieval, clip authority, or baseline search."""
from copy import deepcopy
import json
import textwrap

import pytest

from pipeline.context import editor
from pipeline.context.store import ContextStore


PROFILE = "sampled-narrative-context-v1"


def inputs(count=2, films=1):
    sources, projected = {}, {}
    for i in range(count):
        fid = f"{i % films + 1:064x}"
        sources[f"u{i}"] = {"film_id": fid, "t_start": float(i * 2), "t_end": float(i * 2 + 2)}
        projected[f"c{i}"] = {"film": f"f{i % films}", "t_start": float(i * 2), "t_end": float(i * 2 + 2)}
    payload = {"sources": projected, "films": {f"f{i}": {"title": f"Film {i}"} for i in range(films)},
               "slots": [{"key": "shot_0", "candidates": [{"source": alias} for alias in projected]}],
               "music_evidence": {"meaning": "leaving"}, "timing_scope": {"fixed": True}}
    return payload, sources


def lookup_result(ranges, *, status="available", text="A departure follows a disagreement."):
    aid = "a" * 64
    record = {"artifact_id": aid, "record_id": "r1", "level": "sequence", "applicability": ranges,
              "parent_refs": [], "parents": [], "claims": [{"claim_id": "claim1", "kind": "narrative",
                  "text": text, "status": "uncertain", "evidence_refs": ["e1"]}]}
    evidence = {"artifact_id": aid, "evidence_id": "e1", "kind": "dialogue", "start": 100., "end": 102.,
                "text": "I need to go.", "sha256": "e" * 64}
    return {"status": status, "manifest_generation": "m", "dependency_status": "unchecked",
            "artifact_ids": [aid], "records": [record] if status == "available" else [],
            "evidence": [evidence] if status == "available" else [],
            "ranges": [{**r, "status": status, "record_refs": [{"artifact_id": aid, "record_id": "r1"}]
                if status == "available" else [], "coverage": {"requested": [r], "covered": [r],
                    "complete": status == "available"}} for r in ranges]}


def install(monkeypatch, *, present=True, factory=lookup_result):
    calls = []
    monkeypatch.setattr(ContextStore, "has_profile", lambda self, film_id, profile_id: present)
    def identity(db, film_id):
        calls.append(film_id)
        return {"film_id": film_id}
    monkeypatch.setattr(editor, "source_identity", identity)
    monkeypatch.setattr(ContextStore, "lookup_many", lambda self, fid, identity, ranges, profile: factory(ranges))
    return calls


def test_disabled_is_exact_copy_and_does_not_read_sources(config, monkeypatch):
    monkeypatch.setattr(editor, "source_identity", lambda *_: pytest.fail("disabled context read a film"))
    payload, sources = inputs()
    result = editor.attach_context(payload, config, None, sources=sources)
    assert result == payload and result is not payload
    assert not config.paths.assets_dir.exists()


def test_enabled_preserves_offers_and_deduplicates_context(config, monkeypatch):
    calls = install(monkeypatch)
    payload, sources = inputs()
    original = deepcopy((payload, sources))
    result = editor.attach_context(payload, config, None, sources=sources, profile_id=PROFILE)
    packet = result.pop("source_context")
    assert result == payload
    assert (payload, sources) == original
    assert len(calls) == 1 and len(packet["records"]) == 1
    assert packet["sources"]["c0"]["record_ids"] == packet["sources"]["c1"]["record_ids"]
    record = next(iter(packet["records"].values()))
    assert record["claims"][0]["status"] == "uncertain"
    assert record["evidence"][0]["start"] == 100  # support is outside the selectable shot
    assert record["film"] == "f0"
    assert packet["manifests"]["f0"]["dependency_status"] == "unchecked"


def test_missing_profile_skips_content_hash_and_never_creates_work(config, monkeypatch):
    calls = install(monkeypatch, present=False)
    payload, sources = inputs()
    packet = editor.attach_context(payload, config, None, sources=sources, profile_id=PROFILE)["source_context"]
    assert calls == [] and packet["records"] == {}
    assert {row["status"] for row in packet["sources"].values()} == {"not_processed"}
    assert not config.paths.assets_dir.exists()


@pytest.mark.parametrize("status", ["stale", "unavailable", "uncertain", "not_processed"])
def test_lookup_status_is_not_converted_to_a_negative_match(config, monkeypatch, status):
    install(monkeypatch, factory=lambda ranges: lookup_result(ranges, status=status))
    payload, sources = inputs()
    result = editor.attach_context(payload, config, None, sources=sources, profile_id=PROFILE)
    assert result["slots"] == payload["slots"]
    assert result["source_context"]["sources"]["c0"]["status"] == status
    assert result["source_context"]["records"] == {}


def test_corrupt_citations_remove_that_films_partial_evidence(config, monkeypatch):
    def broken(ranges):
        result = lookup_result(ranges)
        result["evidence"] = []
        return result
    install(monkeypatch, factory=broken)
    payload, sources = inputs()
    packet = editor.attach_context(payload, config, None, sources=sources, profile_id=PROFILE)["source_context"]
    assert packet["records"] == {} and packet["artifacts"] == []
    assert all(row["status"] == "unavailable" for row in packet["sources"].values())


def test_film_and_character_budgets_do_not_change_offers(config, monkeypatch):
    calls = install(monkeypatch)
    payload, sources = inputs(count=4, films=4)
    result = editor.attach_context(payload, config, None, sources=sources, profile_id=PROFILE)
    assert len(calls) == 3
    assert result["source_context"]["sources"]["c3"]["status"] == "budget_limited"
    assert result["sources"] == payload["sources"]
    install(monkeypatch, factory=lambda ranges: lookup_result(ranges, text="a" * 30000))
    result = editor.attach_context(payload, config, None, sources=sources, profile_id=PROFILE)
    assert result["source_context"]["truncated"]
    assert editor._size(result["source_context"]) <= editor.MAX_CONTEXT_CHARS
    assert result["source_context"]["records"] == {}


def test_source_count_and_metadata_budgets_are_bounded(config, monkeypatch):
    install(monkeypatch)
    payload, sources = inputs(count=150)
    packet = editor.attach_context(payload, config, None, sources=sources, profile_id=PROFILE)["source_context"]
    assert packet["truncated"]
    assert len(packet["sources"]) <= editor.MAX_SOURCES
    assert editor._size(packet) <= editor.MAX_CONTEXT_CHARS


def test_changed_offered_range_is_a_hard_binding_error(config, monkeypatch):
    install(monkeypatch)
    payload, sources = inputs()
    payload["sources"]["c0"]["t_end"] = 999.
    with pytest.raises(ValueError, match="aliases or legal ranges"):
        editor.attach_context(payload, config, None, sources=sources, profile_id=PROFILE)


def test_context_dependencies_change_selection_input_hash(config, monkeypatch):
    from pipeline.lab.music import digest
    install(monkeypatch)
    payload, sources = inputs()
    first = editor.attach_context(payload, config, None, sources=sources, profile_id=PROFILE)
    install(monkeypatch, factory=lambda ranges: lookup_result(ranges, text="The reason for departure is unknown."))
    second = editor.attach_context(payload, config, None, sources=sources, profile_id=PROFILE)
    assert digest(first) != digest(second)
    assert first["sources"] == second["sources"]


def test_packing_keeps_nearby_narrative_observation_and_uncertainty():
    result = lookup_result([{"start": 0., "end": 30.}])
    row = result["records"][0]
    aid = row["artifact_id"]
    evidence = {}
    row["claims"] = []
    for index in range(10):
        eid = f"e{index}"
        evidence[(aid, eid)] = {"evidence_id": eid, "kind": "caption", "start": float(index * 10),
                               "end": float(index * 10 + 2), "text": "A retained evidence excerpt. " * 30}
        row["claims"].append({"claim_id": f"claim{index}", "kind": "narrative" if index % 2 else "observed",
                              "text": f"Claim {index}", "status": "supported", "evidence_refs": [eid]})
    row["claims"].append({"claim_id": "uncertainty", "kind": "narrative", "text": "The motive is unknown.",
                           "status": "uncertain", "evidence_refs": []})
    record = editor._record(row, evidence, "f0", [{"start": 50., "end": 52.}])
    assert len(record["claims"]) == editor.MAX_CLAIMS_PER_RECORD
    assert record["claims_omitted"] == 7
    assert {claim["claim_id"] for claim in record["claims"]} == {"claim3", "claim4", "claim5", "uncertainty"}
    assert all(item["excerpt_truncated"] for item in record["evidence"])
    assert row["claims"][-1]["text"] == "The motive is unknown."


def dense_lookup(ranges):
    """Independent windows with enough legitimate evidence to require packing."""
    result = {"manifest_generation": "m", "dependency_status": "unchecked", "records": [], "evidence": [], "ranges": []}
    for part in ranges:
        aid = f"{int(part['start']) + 1:064x}"
        claims = [{"claim_id": "uncertainty", "kind": "narrative", "text": "The person's motive is unknown.",
                   "status": "uncertain", "evidence_refs": []}]
        for index in range(3):
            refs = [f"e{index}-{j}" for j in range(4)]
            claims.append({"claim_id": f"claim{index}", "kind": "observed" if index == 1 else "narrative",
                           "text": f"Complete claim {index}: " + "visible encounter " * (90 if index == 2 else 30),
                           "status": "supported", "evidence_refs": refs})
            for eid in refs:
                result["evidence"].append({"artifact_id": aid, "evidence_id": eid, "kind": "dialogue",
                    "start": part["start"], "end": part["end"], "sha256": "e" * 64,
                    "text": "A timestamped excerpt describing this encounter. " * 12})
        record = {"artifact_id": aid, "record_id": "r1", "level": "sequence", "applicability": [part],
                  "parent_refs": [], "parents": [], "claims": claims}
        result["records"].append(record)
        result["ranges"].append({**part, "status": "available", "record_refs": [{"artifact_id": aid, "record_id": "r1"}],
                                 "coverage": {"requested": [part], "covered": [part], "complete": True}})
    return result


def test_fair_packing_preserves_six_candidates_and_complete_claims(config, monkeypatch):
    install(monkeypatch, factory=dense_lookup)
    payload, sources = inputs(count=6, films=3)
    original = deepcopy((payload, sources))
    with monkeypatch.context() as full_budget:
        full_budget.setattr(editor, "MAX_CONTEXT_CHARS", 100000)
        full = editor.attach_context(payload, config, None, sources=sources, profile_id=PROFILE)["source_context"]
    assert editor._size(full) > editor.MAX_CONTEXT_CHARS
    result = editor.attach_context(payload, config, None, sources=sources, profile_id=PROFILE)
    packet = result.pop("source_context")
    assert result == payload and (payload, sources) == original
    assert editor._size(packet) <= editor.MAX_CONTEXT_CHARS
    assert not packet["truncated"]
    assert len(packet["records"]) == len(packet["artifacts"]) == 6
    assert all(row["status"] == "available" and len(row["record_ids"]) == 1 for row in packet["sources"].values())
    assert any(row["claims_omitted"] for row in packet["records"].values())
    for label, record in packet["records"].items():
        authority = full["records"][label]
        assert 2 <= len(record["claims"]) <= editor.MAX_CLAIMS_PER_RECORD
        assert record["claims_omitted"] == len(authority["claims"]) - len(record["claims"])
        assert record["claims"][0] == authority["claims"][0]  # uncited uncertainty survives
        assert all(claim in authority["claims"] for claim in record["claims"])  # no rewritten/truncated prose
        assert record["applicability"] == authority["applicability"]
        citations = {ref for claim in record["claims"] for ref in claim["evidence_refs"]}
        assert {row["evidence_id"] for row in record["evidence"]} == citations
        originals = {row["evidence_id"]: row for row in authority["evidence"]}
        for evidence in record["evidence"]:
            original_evidence = originals[evidence["evidence_id"]]
            assert (evidence["kind"], evidence["start"], evidence["end"]) == (
                original_evidence["kind"], original_evidence["start"], original_evidence["end"])
            if "sha256" not in evidence:
                assert record["evidence_hashes_in_artifact"]
            if "text" not in evidence:
                assert evidence["excerpt_omitted"]
            elif evidence["text"] != original_evidence["text"]:
                assert evidence["excerpt_truncated"]


def test_fair_packing_is_not_changed_by_candidate_order(config, monkeypatch):
    install(monkeypatch, factory=dense_lookup)
    payload, sources = inputs(count=6, films=3)
    first = editor.attach_context(payload, config, None, sources=sources, profile_id=PROFILE)["source_context"]
    reversed_payload = deepcopy(payload)
    reversed_payload["sources"] = {f"c{i}": row for i, row in enumerate(reversed(list(payload["sources"].values())))}
    reversed_sources = dict(reversed(list(sources.items())))
    second = editor.attach_context(reversed_payload, config, None, sources=reversed_sources, profile_id=PROFILE)["source_context"]
    assert first["records"] == second["records"]
    assert first["artifacts"] == second["artifacts"]
    assert all(row["record_ids"] for row in second["sources"].values())


def test_packet_budget_bounds_actual_ascii_escaped_selector_json(config, monkeypatch):
    def multilingual(ranges):
        result = dense_lookup(ranges)
        for row in result["records"]:
            for claim in row["claims"]:
                if claim["evidence_refs"]:
                    claim["text"] = "人物互相看着对方。" * 12
        for row in result["evidence"]:
            row["text"] = "人物说话然后离开房间。" * 25
        return result
    install(monkeypatch, factory=multilingual)
    payload, sources = inputs(count=6, films=3)
    packet = editor.attach_context(payload, config, None, sources=sources, profile_id=PROFILE)["source_context"]
    actual_prompt_json = json.dumps(packet, separators=(",", ":"))
    assert editor._size(packet) == len(actual_prompt_json) <= editor.MAX_CONTEXT_CHARS
    assert not packet["truncated"] and len(packet["records"]) == 6
    assert all(row["record_ids"] for row in packet["sources"].values())
    assert any("人物" in claim["text"] for row in packet["records"].values() for claim in row["claims"])


def test_compacted_record_keeps_evidence_identity_without_prose_or_duplicate_hashes():
    result = dense_lookup([{"start": 0., "end": 2.}])
    row = result["records"][0]
    evidence = {(item["artifact_id"], item["evidence_id"]): item for item in result["evidence"]}
    # Give three short, fully cited claims priority so all fit once supporting
    # excerpts and redundant hashes move back to the immutable artifact.
    for claim in row["claims"]:
        claim["text"] = "The person's motive is unknown." if claim["claim_id"] == "uncertainty" else "A complete observation."
    full = editor._record(row, evidence, "f0", row["applicability"])
    compact = deepcopy(full)
    compact["evidence_hashes_in_artifact"] = True
    for item in compact["evidence"]:
        item.pop("text")
        item.pop("excerpt_truncated", None)
        item.pop("sha256")
        item["excerpt_omitted"] = True
    bounded = editor._record(row, evidence, "f0", row["applicability"], max_chars=editor._size(compact))
    assert bounded == compact
    assert bounded["claims"] == row["claims"]
    assert full["evidence"][0]["sha256"] == "e" * 64  # full/nonbudget case retains provenance inline


@pytest.mark.parametrize("compacted", [False, True])
def test_record_wide_uncertainty_precedes_earlier_uncited_detail(compacted):
    result = dense_lookup([{"start": 0., "end": 2.}])
    row = result["records"][0]
    warning = {"claim_id": "uncertainty", "kind": "narrative", "status": "uncertain", "evidence_refs": [],
               "text": "Frames conflict with the captions; identities and the wider event remain uncertain."}
    row["claims"][0] = {**warning, "claim_id": "claim-0", "text": "One person's name is unknown."}
    row["claims"].append(warning)
    evidence = {(item["artifact_id"], item["evidence_id"]): item for item in result["evidence"]}
    cap = None
    if compacted:
        smallest = {**deepcopy(row), "claims": [warning], "claims_omitted": 4, "film": "f0", "evidence": []}
        cap = editor._size(smallest)
    record = editor._record(row, evidence, "f0", row["applicability"], max_chars=cap)
    assert warning in record["claims"]
    assert all(claim["claim_id"] != "claim-0" for claim in record["claims"])
    assert record["claims_omitted"] == 5 - len(record["claims"])
    assert len(record["claims"]) == (1 if compacted else 4)


@pytest.mark.parametrize("profile", ["", "../profile", True, 12, "a/b", "x" * 101])
def test_profile_identifier_is_validated(config, profile):
    payload, sources = inputs()
    with pytest.raises(ValueError, match="profile"):
        editor.attach_context(payload, config, None, sources=sources, profile_id=profile)


def test_config_context_is_explicit_and_defaults_off(tmp_path):
    from pipeline.config import load_config
    from pipeline.tests.test_config import MINIMAL_CONFIG
    path = tmp_path / "config.yaml"
    base = textwrap.dedent(MINIMAL_CONFIG)
    path.write_text(base, encoding="utf-8")
    assert load_config(path).lab.context_profile is None
    path.write_text(base + "\nlab:\n  context_profile: sampled-narrative-context-v1\n", encoding="utf-8")
    assert load_config(path).lab.context_profile == PROFILE
    path.write_text(base + "\nlab:\n  context_profile: ../oops\n", encoding="utf-8")
    with pytest.raises(ValueError, match="context_profile"):
        load_config(path)
