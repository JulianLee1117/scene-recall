"""Prepared context crosses the real selector boundary without gaining authority."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from pipeline.context import editor
from pipeline.context.store import ContextStore
from pipeline.index.writer import create_tables, open_db
from pipeline.ingest.probe import _content_hash
from pipeline.lab import music, music_planner
from pipeline.lab.models import MusicDirection
from pipeline.tests.selection_helpers import selector_response
from pipeline.tests.test_lab_music_evidence import _document


@pytest.fixture
def selection(config, tmp_path, monkeypatch):
    config.lab.context_profile = "context-integration-v1"
    film = tmp_path / "source.mp4"
    film.write_bytes(b"original source evidence")
    film_id = _content_hash(film)
    db = open_db(config)
    create_tables(db, vector_dim=4)
    db.open_table("films").add([{"film_id": film_id, "title": "Source", "path": str(film), "duration": 60., "fps": 24.}])
    candidate = {"film_id": film_id, "unit_id": "source-shot", "t_start": 20., "t_end": 26.,
                 "caption": "A person pauses at a doorway", "dialogue": "[]", "on_screen_text": "",
                 "mood": '["quiet"]', "energy": "calm"}
    db.open_table("units").add([candidate])
    monkeypatch.setattr(music, "retrieve_edit_candidates", lambda *_: [deepcopy(candidate)])
    document = _document()
    target = document["music_timeline"]["slots"][1]
    target["direction"] = MusicDirection(query="a hesitant departure").model_dump(mode="json")
    store = ContextStore(config.paths.assets_dir)
    source = editor.source_identity(db, film_id)
    evidence = [{"evidence_id": "earlier-line", "kind": "dialogue", "start": 12., "end": 14.,
                 "text": "I might leave.", "sha256": "e" * 64}]
    records = [{"record_id": "departure", "level": "sequence", "applicability": [{"start": 15., "end": 35.}],
                "parent_refs": [], "claims": [{"claim_id": "claim-1", "kind": "narrative", "status": "uncertain",
                    "text": "The doorway pause may follow the earlier discussion of leaving.", "evidence_refs": ["earlier-line"]}]}]
    derivation = {"prompt_id": "test-context-v1", "prompt_sha256": "b" * 64, "model_id": "fixture-model",
                  "model_revision": None, "input_sha256": "c" * 64, "input_dependencies": {"dialogue": "d" * 64}, "settings": {}}
    published = store.publish(source, evidence, records, derivation, config.lab.context_profile)
    return config, db, document, target["id"], published, store, source, evidence, records, derivation


def _choice():
    return selector_response([{"slot": 1, "candidate_id": "source-shot", "source_start": 21.,
                               "reason": "A readable pause supports this musical intention."}], ["source-shot"])


def test_real_selection_freezes_exact_context_packet_while_response_schema_stays_unchanged(selection, monkeypatch):
    config, db, document, target, published, store, source, evidence, records, derivation = selection
    requests = []

    def hosted(_config, prompt, schema, **kwargs):
        requests.append({"prompt": prompt, "payload": json.loads(prompt.split("\n", 1)[1]), "schema": deepcopy(schema)})
        return _choice()

    monkeypatch.setattr(music, "_hosted_json", hosted)
    result = music_planner.fill_timeline(deepcopy(document), config, db, lambda _: None, "with-context", [target])
    receipt_path = config.paths.assets_dir / "lab" / "requests" / "with-context-source-context-input.json"
    frozen = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt_bytes = receipt_path.read_bytes()
    manifest = json.loads(receipt_path.with_name("with-context-offers.json").read_text(encoding="utf-8"))
    metadata = manifest["source_context"]
    packet = frozen["payload"]["source_context"]
    assert frozen["payload"] == requests[0]["payload"]
    assert frozen["schema"] == requests[0]["schema"]
    assert frozen["sources"]["source-shot"]["film_id"] == source["film_id"]
    assert frozen["document"]["clips"] == [] and frozen["timing_scope"] is None
    assert metadata["artifact_ids"] == packet["artifacts"] == [published["artifact_id"]]
    assert Path(metadata["input_receipt"]) == receipt_path
    assert metadata["input_receipt_hash"] == music.digest(frozen)
    assert manifest["input_hash"] == result["analysis"]["draft"]["input_hash"] == music.digest(frozen["payload"])
    assert manifest["schema_hash"] == music.digest(frozen["schema"])
    assert manifest["prompt_hash"] == music.digest(requests[0]["prompt"])
    assert packet["sources"]["c0"]["status"] == "available"
    record = packet["records"][packet["sources"]["c0"]["record_ids"][0]]
    assert record["artifact_id"] == published["artifact_id"]
    assert record["claims"] == records[0]["claims"]
    assert record["evidence"][0]["start"] == 12. and record["evidence"][0]["sha256"] == "e" * 64
    assert frozen["payload"]["sources"]["c0"]["t_start"] == 20.  # Wider context cannot expand the legal source.
    assert editor.GUIDANCE.strip() in requests[0]["prompt"]
    assert [(clip["source_start"], clip["source_end"]) for clip in result["clips"]] == [(21., 24.)]

    profile = config.lab.context_profile
    config.lab.context_profile = None
    music_planner.fill_timeline(deepcopy(document), config, db, lambda _: None, "baseline", [target])
    assert requests[1]["schema"] == requests[0]["schema"]
    without_context = deepcopy(requests[0]["payload"])
    without_context.pop("source_context")
    assert without_context == requests[1]["payload"]

    # New derivations cannot silently rewrite the exact context this selection saw.
    updated = deepcopy(records)
    updated[0]["claims"][0]["text"] = "The relationship to the earlier line remains unknown."
    replacement = store.publish(source, evidence, updated, derivation, profile)
    assert replacement["artifact_id"] != published["artifact_id"]
    assert receipt_path.read_bytes() == receipt_bytes and metadata["input_receipt_hash"] == music.digest(frozen)


@pytest.mark.parametrize("source", [{"c99": 21.}, {"c0": 12.}])
def test_context_cannot_authorize_an_unoffered_clip_or_supporting_evidence_timestamp(selection, monkeypatch, source):
    config, db, document, target, *_ = selection
    def hosted(_config, prompt, schema, **kwargs):
        assert json.loads(prompt.split("\n", 1)[1])["source_context"]["records"]
        output = _choice()
        output["choices"]["shot_1"]["source"] = source
        return output
    monkeypatch.setattr(music, "_hosted_json", hosted)
    proposed = deepcopy(document)
    with pytest.raises(ValueError):
        music_planner.fill_timeline(proposed, config, db, lambda _: None, "invalid-choice", [target])
    assert proposed["clips"] == document["clips"] == []


def test_manual_suggestions_bypass_context_and_hosted_selection_even_when_enabled(selection, monkeypatch):
    config, db, document, target, *_ = selection
    monkeypatch.setattr(editor, "attach_context", lambda *a, **k: pytest.fail("Manual suggestions read context"))
    monkeypatch.setattr(music, "_hosted_json", lambda *a, **k: pytest.fail("Manual suggestions called a hosted model"))
    result = music_planner.fill_timeline(deepcopy(document), config, db, lambda _: None, "manual", [target], suggest_only=True)
    assert result["music_timeline"]["slots"][1]["alternatives"][0]["clip"]["unit_id"] == "source-shot"
    assert result["clips"] == document["clips"] == []
    assert not (config.paths.assets_dir / "lab" / "requests" / "manual-source-context-input.json").exists()
