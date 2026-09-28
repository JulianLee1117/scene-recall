"""Pure storage tests for the explicitly scoped narrative-context pilot."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from pipeline.context.schema import ContextArtifact, ContextSource, Evidence, TimeRange
from pipeline.context.store import ContextStore, ContextStoreError


FILM = "a" * 64
PROFILE = "pilot-v1"


def source():
    return {"film_id": FILM, "path": "V:/films/Source.mkv", "duration": 100.0,
            "fingerprint": {"film_id": FILM, "content_hash_profile": "sha256-head-tail-4MiB",
                            "content_hash": FILM, "size": 12345, "mtime_ns": 123456},
            "timeline": "source-player-seconds"}


def derivation():
    return {"prompt_id": "context-prompt-v1", "prompt_sha256": "b" * 64,
            "model_id": "model-name", "model_revision": None, "input_sha256": "c" * 64,
            "input_dependencies": {"captions": "d" * 64}, "settings": {"temperature": 0}}


def evidence():
    return [{"evidence_id": "e1", "kind": "dialogue", "start": 70.0, "end": 71.0,
             "text": "The speaker says they have met before."}]


def record(identity="local-1", start=10.0, end=20.0, *, text="They discuss a prior meeting."):
    return {"record_id": identity, "level": "local", "applicability": [{"start": start, "end": end}],
            "parent_refs": [], "claims": [{"claim_id": "c1", "kind": "narrative", "text": text,
                                           "status": "supported", "evidence_refs": ["e1"]}]}


def publish(store, identity="local-1", start=10.0, end=20.0, *, text="They discuss a prior meeting."):
    return store.publish(source(), evidence(), [record(identity, start, end, text=text)], derivation(), PROFILE)


def manifest_path(store):
    return store.root / PROFILE / FILM / "manifest.json"


def artifact_path(store, receipt):
    return store.root / PROFILE / FILM / "artifacts" / f"{receipt['artifact_id']}.json"


def test_publish_is_content_addressed_idempotent_and_citations_are_disjoint_from_applicability(tmp_path):
    store = ContextStore(tmp_path / "assets")
    first = publish(store)
    second = publish(store)
    assert first == second
    result = store.lookup(FILM, source(), 12, 14, PROFILE)
    assert result["status"] == "available"
    assert result["coverage"]["complete"] is True
    assert result["records"][0]["applicability"] == [{"start": 10.0, "end": 20.0}]
    assert result["evidence"][0]["start"] == 70
    assert result["evidence"][0]["artifact_id"] == result["records"][0]["artifact_id"]
    assert result["legal_clip_authority"] is False
    assert store.lookup(FILM, source(), 70, 71, PROFILE)["status"] == "missing"
    assert store.read_artifact(FILM, PROFILE, first["artifact_id"])["source"]["path"] == source()["path"]


def test_disjoint_windows_survive_replacement_and_historical_artifacts_stay_readable(tmp_path):
    store = ContextStore(tmp_path)
    first = publish(store, "first", 0, 10)
    other = publish(store, "second", 20, 30)
    updated = publish(store, "first", 0, 10, text="A revised cautious reading.")
    assert updated["artifact_id"] != first["artifact_id"]
    result = store.lookup(FILM, source(), 0, 30, PROFILE)
    assert result["artifact_ids"] == sorted([updated["artifact_id"], other["artifact_id"]])
    assert result["status"] == "partial"
    assert result["coverage"]["missing"] == [{"start": 10.0, "end": 20.0}]
    assert store.read_artifact(FILM, PROFILE, first["artifact_id"])["records"][0]["claims"][0]["text"] == "They discuss a prior meeting."


def test_candidate_range_order_and_duplicates_are_preserved(tmp_path):
    store = ContextStore(tmp_path)
    publish(store, "first", 0, 10)
    publish(store, "second", 20, 30)
    ranges = [{"start": 22, "end": 23}, {"start": 1, "end": 2}, {"start": 22, "end": 23}]
    result = store.lookup_many(FILM, source(), ranges, PROFILE)
    assert [{key: row[key] for key in ("start", "end")} for row in result["ranges"]] == ranges
    assert [row["record_refs"][0]["record_id"] for row in result["ranges"]] == ["second", "first", "second"]


def test_missing_context_is_not_uncertain_context_and_lookup_creates_nothing(tmp_path, monkeypatch):
    directory = tmp_path / "absent"
    store = ContextStore(directory)
    monkeypatch.setattr(Path, "rglob", lambda *_a, **_k: pytest.fail("Context must not scan all films"))
    assert not store.has_profile(FILM, PROFILE)
    assert store.lookup(FILM, source(), 1, 2, PROFILE)["status"] == "missing"
    store.validate_profile(FILM, PROFILE, derivation())
    assert not directory.exists()
    uncertain = record()
    uncertain["claims"][0].update(status="uncertain", evidence_refs=[], text="The participants' relationship is unresolved.")
    store.publish(source(), [], [uncertain], derivation(), PROFILE)
    result = store.lookup(FILM, source(), 12, 14, PROFILE)
    assert result["status"] == "available"
    assert result["records"][0]["claims"][0]["status"] == "uncertain"
    assert result["evidence"] == []


@pytest.mark.parametrize("changed", ["mtime", "size", "duration"])
def test_stale_source_never_returns_old_claims(tmp_path, changed):
    store = ContextStore(tmp_path)
    publish(store)
    current = source()
    if changed == "duration":
        current["duration"] = 101
    else:
        key = "mtime_ns" if changed == "mtime" else "size"
        current["fingerprint"][key] += 1
    result = store.lookup(FILM, current, 12, 14, PROFILE)
    assert result["status"] == "stale"
    assert result["records"] == result["evidence"] == []
    assert store.lookup(FILM, current, 40, 50, PROFILE)["status"] == "missing"


def test_verified_relocation_reuses_context_but_retains_original_provenance(tmp_path):
    store = ContextStore(tmp_path)
    receipt = publish(store)
    relocated = source()
    relocated["path"] = "V:/renamed/Source (2001).mkv"
    assert store.lookup(FILM, relocated, 12, 14, PROFILE)["status"] == "available"
    assert store.read_artifact(FILM, PROFILE, receipt["artifact_id"])["source"]["path"] != relocated["path"]


def test_dependency_freshness_is_honest_and_mismatch_is_stale(tmp_path):
    store = ContextStore(tmp_path)
    publish(store)
    assert store.lookup(FILM, source(), 12, 14, PROFILE)["dependency_status"] == "unchecked"
    matched = store.lookup(FILM, source(), 12, 14, PROFILE, input_dependencies={"captions": "d" * 64})
    assert matched["dependency_status"] == "matched"
    changed = store.lookup(FILM, source(), 12, 14, PROFILE, input_dependencies={"captions": "e" * 64})
    assert changed["status"] == changed["dependency_status"] == "stale"
    assert changed["records"] == []


@pytest.mark.parametrize("target", ["manifest", "artifact", "missing_artifact", "oversized_manifest"])
def test_corruption_is_unavailable_instead_of_missing(tmp_path, target):
    store = ContextStore(tmp_path)
    receipt = publish(store)
    if target == "missing_artifact":
        artifact_path(store, receipt).unlink()
    elif target == "oversized_manifest":
        from pipeline.context.schema import MAX_MANIFEST_BYTES
        manifest_path(store).write_bytes(b" " * (MAX_MANIFEST_BYTES + 1))
    else:
        path = manifest_path(store) if target == "manifest" else artifact_path(store, receipt)
        document = json.loads(path.read_text())
        if target == "manifest":
            document["entries"] = []
        else:
            document["records"][0]["claims"][0]["text"] = "Tampered assertion"
        path.write_text(json.dumps(document))
    result = store.lookup(FILM, source(), 12, 14, PROFILE)
    assert result["status"] == "unavailable"
    assert result["records"] == []
    assert result["warnings"]


def test_valid_window_remains_partial_when_another_artifact_is_corrupt(tmp_path):
    store = ContextStore(tmp_path)
    publish(store, "first", 0, 10)
    other = publish(store, "second", 10, 20)
    artifact_path(store, other).write_text("truncated")
    result = store.lookup(FILM, source(), 0, 20, PROFILE)
    assert result["status"] == "partial"
    assert result["coverage"]["unavailable"] == [{"start": 10.0, "end": 20.0}]
    assert [row["record_id"] for row in result["records"]] == ["first"]


@pytest.mark.parametrize("field,value", [("start", float("nan")), ("end", float("inf")),
                                         ("start", -1), ("start", True), ("start", "1"), ("end", 0)])
def test_time_bounds_reject_invalid_numbers(field, value):
    bounds = {"start": 0, "end": 10, field: value}
    with pytest.raises(ValueError):
        TimeRange.model_validate(bounds)


@pytest.mark.parametrize("invalid", ["citation", "duplicate_evidence", "duplicate_records", "duplicate_claims", "duration", "coverage"])
def test_invalid_artifact_is_rejected_before_any_write(tmp_path, invalid):
    store = ContextStore(tmp_path / "not-created")
    rows, proof = [record()], evidence()
    coverage = [{"start": 10, "end": 20}]
    if invalid == "citation":
        rows[0]["claims"][0]["evidence_refs"] = ["unknown"]
    elif invalid == "duplicate_evidence":
        proof += deepcopy(proof)
    elif invalid == "duplicate_records":
        rows += deepcopy(rows)
    elif invalid == "duplicate_claims":
        rows[0]["claims"] += deepcopy(rows[0]["claims"])
    elif invalid == "duration":
        proof[0]["end"] = 101
    else:
        coverage = [{"start": 15, "end": 20}]
    with pytest.raises(ValueError):
        store.publish(source(), proof, rows, derivation(), PROFILE, coverage=coverage)
    assert not store.root.exists()


def test_supported_claim_needs_citation_and_single_frame_evidence_is_valid(tmp_path):
    store = ContextStore(tmp_path)
    bad = record()
    bad["claims"][0]["evidence_refs"] = []
    with pytest.raises(ValueError):
        store.publish(source(), evidence(), [bad], derivation(), PROFILE)
    point = {"evidence_id": "e1", "kind": "keyframe", "start": 70.0, "end": 70.0,
             "sha256": "e" * 64, "locator": "frames/one.jpg"}
    store.publish(source(), [point], [record()], derivation(), PROFILE)
    assert store.lookup(FILM, source(), 12, 14, PROFILE)["evidence"][0]["start"] == 70


def test_optional_parents_validate_and_resolve_across_artifacts(tmp_path):
    store = ContextStore(tmp_path)
    parent = record("film-summary", 0, 100)
    parent["level"] = "film"
    first = store.publish(source(), evidence(), [parent], derivation(), PROFILE)
    child = record()
    child["parent_refs"] = ["film-summary"]
    store.publish(source(), evidence(), [child], derivation(), PROFILE)
    result = store.lookup(FILM, source(), 12, 14, PROFILE)
    found = next(row for row in result["records"] if row["record_id"] == "local-1")
    assert found["parents"] == [{"artifact_id": first["artifact_id"], "record_id": "film-summary"}]
    invalid = record("orphan", 30, 40)
    invalid["parent_refs"] = ["missing"]
    before = manifest_path(store).read_bytes()
    with pytest.raises(ValueError, match="parent"):
        store.publish(source(), evidence(), [invalid], derivation(), PROFILE)
    assert manifest_path(store).read_bytes() == before


def test_duplicate_active_record_ids_and_profile_changes_cannot_silently_mix(tmp_path):
    store = ContextStore(tmp_path)
    receipt = publish(store)
    with pytest.raises(ValueError, match="Duplicate active"):
        publish(store, "local-1", 30, 40)
    revised = derivation()
    revised["model_id"] = "other-model"
    with pytest.raises(ValueError, match="new context profile"):
        store.validate_profile(FILM, PROFILE, revised)
    with pytest.raises(ValueError, match="new context profile"):
        store.publish(source(), evidence(), [record()], revised, PROFILE)
    store.publish(source(), evidence(), [record()], revised, "other-profile")
    assert store.lookup(FILM, source(), 12, 14, PROFILE)["artifact_ids"] == [receipt["artifact_id"]]


def test_lookup_freezes_manifest_generation_during_concurrent_publication(tmp_path, monkeypatch):
    import pipeline.context.store as module

    store = ContextStore(tmp_path)
    publish(store, "first", 0, 10)
    before = publish(store, "second", 10, 20, text="Old second window.")
    original_read = module._read
    changed = False

    def publish_after_snapshot(path, maximum):
        nonlocal changed
        if path.parent.name == "artifacts" and not changed:
            changed = True
            publish(store, "second", 10, 20, text="New second window.")
        return original_read(path, maximum)

    monkeypatch.setattr(module, "_read", publish_after_snapshot)
    result = store.lookup_many(FILM, source(), [{"start": 1, "end": 2}, {"start": 11, "end": 12}], PROFILE)
    assert changed
    assert result["manifest_generation"] == before["manifest_generation"]
    assert next(row for row in result["records"] if row["record_id"] == "second")["claims"][0]["text"] == "Old second window."
    assert store.lookup(FILM, source(), 11, 12, PROFILE)["records"][0]["claims"][0]["text"] == "New second window."


def test_one_profile_cannot_mix_producers_across_films_and_other_profiles_remain(tmp_path):
    store = ContextStore(tmp_path)
    first = publish(store)
    profile_path = store.root / PROFILE / "profile.json"
    original_profile = profile_path.read_bytes()
    other_source = source()
    other_film = "f" * 64
    other_source["film_id"] = other_film
    other_source["fingerprint"].update(film_id=other_film, content_hash=other_film)
    changed = derivation()
    changed["model_id"] = "changed-model"
    with pytest.raises(ValueError, match="new context profile"):
        store.validate_profile(other_film, PROFILE, changed)
    with pytest.raises(ValueError, match="new context profile"):
        store.publish(other_source, evidence(), [record()], changed, PROFILE)
    assert not store.has_profile(other_film, PROFILE)
    store.publish(other_source, evidence(), [record()], changed, "pilot-v2")
    assert profile_path.read_bytes() == original_profile
    assert store.read_artifact(FILM, PROFILE, first["artifact_id"])["derivation"]["model_id"] == "model-name"
    assert store.lookup(FILM, source(), 12, 14, PROFILE)["status"] == "available"
    assert store.lookup(other_film, other_source, 12, 14, "pilot-v2")["status"] == "available"


@pytest.mark.parametrize("missing", [False, True])
def test_corrupt_or_missing_global_profile_is_unavailable(tmp_path, missing):
    store = ContextStore(tmp_path)
    publish(store)
    path = store.root / PROFILE / "profile.json"
    if missing:
        path.unlink()
    else:
        value = json.loads(path.read_text())
        value["profile_key"]["model_id"] = "tampered-model"
        path.write_text(json.dumps(value))
    result = store.lookup(FILM, source(), 12, 14, PROFILE)
    assert result["status"] == "unavailable"
    assert result["records"] == []
    with pytest.raises(ValueError):
        store.validate_profile(FILM, PROFILE, derivation())


def test_corrupt_json_consumes_the_lookup_read_budget(tmp_path, monkeypatch):
    import pipeline.context.store as module

    store = ContextStore(tmp_path)
    first = publish(store, "first", 0, 10)
    second = publish(store, "second", 10, 20)
    artifact_path(store, first).write_bytes(b"!" * 100)
    monkeypatch.setattr(module, "MAX_LOOKUP_BYTES", artifact_path(store, second).stat().st_size)
    result = store.lookup(FILM, source(), 0, 20, PROFILE)
    assert result["status"] == "unavailable"
    assert result["records"] == []
    assert result["coverage"]["unavailable"] == [{"start": 0.0, "end": 20.0}]


@pytest.mark.parametrize("profile", ["../escape", "/absolute", "C:/absolute", "bad/name"])
def test_unsafe_profile_cannot_escape_storage(tmp_path, profile):
    store = ContextStore(tmp_path / "absent")
    with pytest.raises(ValueError):
        store.publish(source(), evidence(), [record()], derivation(), profile)
    assert not store.root.exists()


def test_linked_artifact_is_unavailable(tmp_path):
    store = ContextStore(tmp_path)
    receipt = publish(store)
    path = artifact_path(store, receipt)
    external = tmp_path / "external.json"
    external.write_bytes(path.read_bytes())
    path.unlink()
    try:
        path.symlink_to(external)
    except OSError:
        pytest.skip("Symlink permission unavailable")
    assert store.lookup(FILM, source(), 12, 14, PROFILE)["status"] == "unavailable"
