"""Source boundaries, human evidence accounting, and isolated CLI behavior."""

from __future__ import annotations

import copy
import json

import pytest

from pipeline.experiments.region_geometry import COORDINATE_SPACE
from pipeline.experiments.visual_rhymes import blind_packet, main, propose_document, score_evaluation, source_selection, template


def catalog():
    return {"schema_version": 1, "kind": "visual_rhymes_source_catalog", "coordinate_space": COORDINATE_SPACE, "units": {
        key: {"film_id": film, "t_start": 10, "t_end": 20, "picture": {"width": 3840, "height": 2160}, "indexed_frames": [{"frame_index": 0, "timestamp": 15, "timestamp_source": "ingest_keyframe_seek_v1"}]}
        for key, film in (("ref", "film-a"), ("candidate", "film-b"))
    }}


def selection(unit="ref", film="film-a"):
    return {"unit_id": unit, "film_id": film, "mode": "fixed", "timestamp": 15, "timestamp_source": "indexed_seek", "frame_index": 0}


def proposals():
    document = template("proposal")
    document["cases"][0]["reference"] = selection()
    document["cases"][0]["candidate"] = selection("candidate", "film-b")
    return propose_document(document, catalog())


def test_indexed_seek_is_preserved_and_never_claimed_as_decoded_pts():
    result = source_selection(selection(), catalog(), outgoing=True)
    assert result["timestamp_source"] == "indexed_seek"
    assert result["source_start"] == 14.5
    assert result["source_end"] == 15
    assert result["timestamp"] == 15
    assert result["frame_index"] == 0


def test_manual_window_keeps_chosen_instant_without_automatic_frame_search():
    raw = {**selection(), "mode": "window", "window_start": 12, "window_end": 18, "timestamp": 16.04, "timestamp_source": "operator_decoded_pts", "pts_evidence": "ffprobe-selected-pts.txt"}
    result = source_selection(raw, catalog(), outgoing=False)
    assert result["timestamp"] == 16.04
    assert result["window_start"] == 12
    assert result["window_end"] == 18
    assert result["frame_index"] is None
    assert result["source_end"] == 16.54


@pytest.mark.parametrize("patch,match", [
    ({"film_id": "wrong-film"}, "identity"),
    ({"unit_id": "missing"}, "absent"),
    ({"timestamp": 20}, "half-open"),
    ({"timestamp": 9}, "half-open"),
    ({"timestamp": 14}, "original seek"),
    ({"frame_index": True}, "catalog frame"),
    ({"mode": "window", "window_start": 9, "window_end": 16}, "window"),
    ({"mode": "window", "window_start": 11, "window_end": 15}, "window"),
    ({"timestamp_source": "operator_decoded_pts"}, "pts_evidence"),
    ({"handle_seconds": 0}, "handle"),
])
def test_invalid_or_stale_source_selection_is_rejected(patch, match):
    with pytest.raises(ValueError, match=match):
        source_selection({**selection(), **patch}, catalog(), outgoing=True)


def test_outgoing_and_incoming_handles_must_stay_in_legal_unit():
    raw = {**selection(), "timestamp_source": "operator_decoded_pts", "pts_evidence": "pts.log"}
    with pytest.raises(ValueError, match="surrounding footage"):
        source_selection({**raw, "timestamp": 10.1}, catalog(), outgoing=True)
    with pytest.raises(ValueError, match="surrounding footage"):
        source_selection({**raw, "timestamp": 19.9}, catalog(), outgoing=False)


def test_proposals_retain_source_and_both_regions_and_are_blinded_reproducibly():
    report = proposals()
    assert report["shadow_only"]
    assert report["cases"][0]["status"] == "proposed"
    assert len(report["catalog_sha256"]) == 64
    packet, key = blind_packet(report, 42)
    assert (packet, key) == blind_packet(report, 42)
    assert packet["trials"][0]["choice"] is None
    assignment = key["assignments"]["trial-001"]
    assert {assignment["A"], assignment["B"]} == {"full_frame", "region_crop"}
    assert "assignments" not in packet
    for variant in packet["trials"][0]["variants"].values():
        assert variant["reference"]["film_id"] == "film-a"
        assert variant["candidate"]["timestamp"] == 15
        assert variant["reference"]["region"]


def test_unjudged_evaluation_never_passes_or_turns_null_into_zero():
    result = score_evaluation(template("evaluation"))
    assert result["production_activation"] is False
    assert result["static_evidence_meets_gates"] is False
    assert result["cases"][0]["baseline_ndcg10"] is None
    assert result["cases"][0]["instant_oracle_gain"] is None


def acceptance_document():
    document = template("evaluation")
    cases = []
    for index in range(12):
        item = copy.deepcopy(document["cases"][0])
        item["id"] = ["dune_tight_profile", "dune_tight_right_profile"][index] if index < 2 else f"case-{index}"
        item["baseline_top10"] = [f"negative-{i}" for i in range(7)] + [f"positive-{i}" for i in range(3)]
        item["challenger_top10"] = list(reversed(item["baseline_top10"]))
        item["geometry_grades"] = {identity: 3 if identity.startswith("positive") else 0 for identity in item["baseline_top10"]}
        item["blind_static_choice"] = "challenger"
        item["known_positive_units"] = ["known-a", "known-b"]
        item["candidate_units"] = ["known-a"]
        item["instant_oracle"].update(sparse_transition_grade=1, hand_picked_transition_grade=3)
        for side in ("reference", "candidate"):
            item["instant_oracle"][f"sparse_{side}"] = selection()
            item["instant_oracle"][f"hand_picked_{side}"] = {**selection(), "timestamp_source": "operator_decoded_pts", "pts_evidence": "inspected-pts.log"}
        item["region_comparison"].update(blind_packet_sha256="a" * 64, full_frame_transition_grade=1, crop_transition_grade=2)
        cases.append(item)
    document["cases"] = cases
    document["warm_static_latency_ms"] = [100] * 20
    document["static_manifest"] = {"profile_id": "test-profile", "frame_generation_digest": "generation-digest", "verified_complete_compatible": True}
    return document


def test_static_gates_do_not_activate_retrieval_and_report_failure_dimensions_separately():
    result = score_evaluation(acceptance_document())
    assert all(result["static_gates"].values())
    assert result["static_evidence_meets_gates"]
    assert result["production_activation"] is False
    row = result["cases"][0]
    assert row["known_candidate_recall"] == .5
    assert row["known_candidates_missing"] == ["known-b"]
    assert row["instant_oracle_gain"] == 2
    assert row["crop_transition_gain"] == 1


def test_partial_pool_grades_and_slow_latency_fail_static_gates():
    document = acceptance_document()
    document["cases"][0]["geometry_grades"]["positive-0"] = None
    document["warm_static_latency_ms"] = [100] * 18 + [251, 251]
    result = score_evaluation(document)
    assert result["cases"][0]["baseline_ndcg10"] is None
    assert result["static_gates"]["twelve_fully_judged_references"] is False
    assert result["static_gates"]["warm_static_p95_under_250ms"] is False


def test_played_cut_judgments_require_retained_source_and_packet_evidence():
    document = acceptance_document()
    document["cases"][0]["instant_oracle"]["hand_picked_candidate"] = None
    with pytest.raises(ValueError, match="source anchor"):
        score_evaluation(document)
    document = acceptance_document()
    document["cases"][0]["region_comparison"]["blind_packet_sha256"] = None
    with pytest.raises(ValueError, match="blind_packet"):
        score_evaluation(document)


def test_cli_templates_and_scoring_never_overwrite_evidence(tmp_path):
    source = tmp_path / "evaluation.json"
    report = tmp_path / "score.json"
    assert main(["template", "--kind", "evaluation", "--output", str(source)]) == 0
    original = source.read_bytes()
    assert main(["template", "--output", str(source)]) == 2
    assert source.read_bytes() == original
    assert main(["score", "--evaluation", str(source), "--output", str(report)]) == 0
    assert json.loads(report.read_text())["production_activation"] is False
