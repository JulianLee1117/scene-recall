"""Offline region-crop proposals and human evaluation; no search activation.

Run ``python -m pipeline.experiments.visual_rhymes --help``. The source catalog
is an operator-exported snapshot, not a browser-trusted source resolver. PTS
entered by a human is explicitly identified as such; legacy seek timestamps
are never relabeled as decoded PTS.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import random
from statistics import median
import subprocess
import sys
from typing import Any

from pipeline.experiments.region_geometry import (
    Box, COORDINATE_SPACE, NoFeasibleCrop, Picture, PROFILE_ID, finite, propose_crop,
)


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _require(value: Any, kind: type, name: str):
    if not isinstance(value, kind) or isinstance(value, bool):
        raise ValueError(f"{name} must be {kind.__name__}")
    return value


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be nonempty text")
    return value


def _version(document: dict, kind: str):
    if document.get("kind") != kind or document.get("schema_version") != 1:
        raise ValueError(f"Expected {kind} schema_version 1")


def source_selection(raw: dict, catalog: dict, *, outgoing: bool) -> dict:
    """Validate a human selection against its exported source unit and range."""
    _version(catalog, "visual_rhymes_source_catalog")
    unit_id = _text(raw.get("unit_id"), "unit_id")
    unit = catalog["units"].get(unit_id)
    if unit is None:
        raise ValueError(f"Unit {unit_id!r} is absent from the source catalog")
    if raw.get("film_id") != unit["film_id"]:
        raise ValueError("Selection film identity disagrees with its catalog unit")
    lower, upper = finite(unit["t_start"], "unit start"), finite(unit["t_end"], "unit end")
    timestamp = finite(raw.get("timestamp"), "timestamp")
    if lower < 0 or upper <= lower or not lower <= timestamp < upper:
        raise ValueError("Selected instant must remain inside the half-open source unit")
    mode = raw.get("mode")
    if mode == "fixed":
        start = end = timestamp
    elif mode == "window":
        start, end = finite(raw.get("window_start"), "window_start"), finite(raw.get("window_end"), "window_end")
        if not lower <= start < end <= upper or not start <= timestamp < end:
            raise ValueError("Selected instant and window must remain inside the source unit")
    else:
        raise ValueError("Selection mode must be fixed or window")
    provenance = raw.get("timestamp_source")
    if provenance == "indexed_seek":
        index = raw.get("frame_index")
        if isinstance(index, bool) or not isinstance(index, int) or not any(
            frame["frame_index"] == index and abs(frame["timestamp"] - timestamp) < 1e-6
            for frame in unit.get("indexed_frames", [])
        ):
            raise ValueError("Indexed selection must identify a catalog frame and its original seek timestamp")
    elif provenance == "operator_decoded_pts":
        _text(raw.get("pts_evidence"), "pts_evidence (decoder log or inspected frame evidence)")
    else:
        raise ValueError("timestamp_source must distinguish indexed_seek from operator_decoded_pts")
    handle = finite(raw.get("handle_seconds", 0.5), "handle_seconds")
    if handle <= 0 or handle > 10:
        raise ValueError("Audition handle must be greater than zero and at most ten seconds")
    clip_start, clip_end = (timestamp - handle, timestamp) if outgoing else (timestamp, timestamp + handle)
    if clip_start < lower - 1e-9 or clip_end > upper + 1e-9:
        raise ValueError("Selected instant lacks the requested surrounding footage for audition")
    return {
        "film_id": unit["film_id"], "unit_id": unit_id, "timestamp": timestamp,
        "timestamp_source": provenance, "pts_evidence": raw.get("pts_evidence"),
        "frame_index": raw.get("frame_index") if provenance == "indexed_seek" else None,
        "mode": mode, "window_start": start, "window_end": end,
        "source_start": clip_start, "source_end": clip_end,
        "picture": unit["picture"],
    }


def propose_document(document: dict, catalog: dict) -> dict:
    _version(document, "visual_rhymes_proposal_cases")
    _version(catalog, "visual_rhymes_source_catalog")
    if catalog.get("coordinate_space") != COORDINATE_SPACE:
        raise ValueError("Catalog coordinate space is incompatible")
    cases = _require(document.get("cases"), list, "cases")
    if not cases or len(cases) > 100:
        raise ValueError("Provide one to 100 bounded proposal cases")
    output = Picture(**document.get("output", {"width": 1920, "height": 1080}))
    results, seen = [], set()
    for case in cases:
        case_id = _text(case.get("id"), "case id")
        if case_id in seen:
            raise ValueError("Case IDs must be unique")
        seen.add(case_id)
        reference = source_selection(case["reference"], catalog, outgoing=True)
        candidate = source_selection(case["candidate"], catalog, outgoing=False)
        reference_region, candidate_region = Box(**case["reference_region"]), Box(**case["candidate_region"])
        row = {"id": case_id, "reference": reference, "candidate": candidate}
        try:
            proposal = propose_crop(
                Picture(**reference["picture"]), reference_region,
                Picture(**candidate["picture"]), candidate_region, output=output,
                reference_crop=Box(**case["reference_crop"]) if case.get("reference_crop") else None,
                protected_context=Box(**case["protected_context"]) if case.get("protected_context") else None,
                max_upscale=document.get("max_upscale", 2),
            )
            row.update(status="proposed", proposal=proposal.as_document())
        except NoFeasibleCrop as exc:
            row.update(status="infeasible", reason=str(exc))
        results.append(row)
    return {
        "schema_version": 1, "kind": "visual_rhymes_proposals", "shadow_only": True,
        "profile_id": PROFILE_ID, "coordinate_space": COORDINATE_SPACE,
        "catalog_sha256": digest(catalog), "cases_sha256": digest(document),
        "output": {"width": output.width, "height": output.height}, "cases": results,
        "note": "Geometry/crop feasibility only. No decoded-frame search, appearance judgment, or motion matching occurred.",
    }


def blind_packet(report: dict, seed: int) -> tuple[dict, dict]:
    """Separate condition assignment from an ungraded, reproducible A/B packet."""
    _version(report, "visual_rhymes_proposals")
    rng, trials, assignments = random.Random(seed), [], {}
    for case in report["cases"]:
        if case["status"] != "proposed":
            continue
        conditions = ["full_frame", "region_crop"]
        rng.shuffle(conditions)
        trial_id = f"trial-{len(trials) + 1:03d}"
        variants = {}
        for label, condition in zip(("A", "B"), conditions, strict=True):
            variants[label] = {
                side: {
                    **case[side],
                    "crop": case["proposal"][f"{side}_crop"] if condition == "region_crop" else None,
                    "region": case["proposal"][f"{side}_region"],
                } for side in ("reference", "candidate")
            }
        trials.append({"id": trial_id, "variants": variants, "choice": None, "transition_grade_A": None, "transition_grade_B": None, "note": ""})
        assignments[trial_id] = {"case_id": case["id"], **dict(zip(("A", "B"), conditions, strict=True))}
    return (
        {"schema_version": 1, "kind": "visual_rhymes_blind_packet", "report_sha256": digest(report), "output": report["output"], "trials": trials,
         "instructions": "Render equal-duration A-to-B cuts. Conceal crop metadata and the key from judges; label only A/B. Null means unjudged. Grade the played transition 0–3, not the thumbnail."},
        {"schema_version": 1, "kind": "visual_rhymes_blind_key", "report_sha256": digest(report), "seed": seed, "assignments": assignments},
    )


def _grade(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 3:
        raise ValueError("Human grades must be 0–3 or null")
    return value


def _ndcg(ranking: list[str], grades: dict[str, int], ideal: list[int]) -> float:
    def dcg(values):
        return sum((2**grade - 1) / math.log2(i + 2) for i, grade in enumerate(values[:10]))
    denominator = dcg(ideal)
    return dcg([grades[item] for item in ranking[:10]]) / denominator if denominator else 0.0


def score_evaluation(document: dict) -> dict:
    _version(document, "visual_rhymes_human_evaluation")
    cases = _require(document.get("cases"), list, "cases")
    rows, seen = [], set()
    for case in cases:
        case_id = _text(case.get("id"), "case id")
        if case_id in seen:
            raise ValueError("Evaluation case IDs must be unique")
        seen.add(case_id)
        rankings = [case.get("baseline_top10", []), case.get("challenger_top10", [])]
        for ranking in rankings:
            if not isinstance(ranking, list) or any(not isinstance(v, str) or not v for v in ranking) or len(ranking) != len(set(ranking)):
                raise ValueError("Rankings require unique candidate identities")
        grades = {key: _grade(value) for key, value in case.get("geometry_grades", {}).items()}
        pool = set(rankings[0][:10]) | set(rankings[1][:10])
        fully_judged = len(rankings[0]) >= 10 and len(rankings[1]) >= 10 and all(grades.get(key) is not None for key in pool)
        scores = [None, None]
        if fully_judged:
            judged = {key: grades[key] for key in pool}
            ideal = sorted(judged.values(), reverse=True)
            scores = [_ndcg(ranking, judged, ideal) for ranking in rankings]
        positives = set(case.get("known_positive_units", []))
        candidates = set(case.get("candidate_units", []))
        oracle = case.get("instant_oracle", {})
        sparse, manual = _grade(oracle.get("sparse_transition_grade")), _grade(oracle.get("hand_picked_transition_grade"))
        for prefix, grade in (("sparse", sparse), ("hand_picked", manual)):
            if grade is None:
                continue
            for side in ("reference", "candidate"):
                anchor = _require(oracle.get(f"{prefix}_{side}"), dict, f"{prefix}_{side} source anchor")
                _text(anchor.get("film_id"), "oracle film_id")
                _text(anchor.get("unit_id"), "oracle unit_id")
                if finite(anchor.get("timestamp"), "oracle timestamp") < 0:
                    raise ValueError("Oracle timestamp cannot be negative")
                if anchor.get("timestamp_source") not in ("indexed_seek", "operator_decoded_pts"):
                    raise ValueError("Oracle anchors require timestamp provenance")
                if prefix == "hand_picked" and anchor.get("timestamp_source") != "operator_decoded_pts":
                    raise ValueError("Hand-picked oracle must retain inspected decoded PTS evidence")
                if anchor.get("timestamp_source") == "operator_decoded_pts":
                    _text(anchor.get("pts_evidence"), "oracle pts_evidence")
        region = case.get("region_comparison", {})
        full, cropped = _grade(region.get("full_frame_transition_grade")), _grade(region.get("crop_transition_grade"))
        if full is not None or cropped is not None:
            packet_digest = _text(region.get("blind_packet_sha256"), "blind_packet_sha256")
            if len(packet_digest) != 64 or any(char not in "0123456789abcdef" for char in packet_digest):
                raise ValueError("Region judgments must identify the blind packet SHA-256")
        choice = case.get("blind_static_choice")
        if choice not in (None, "baseline", "challenger", "tie"):
            raise ValueError("Static blind choice must be baseline, challenger, tie, or null")
        rows.append({
            "id": case_id, "fully_judged_top10": fully_judged,
            "baseline_ndcg10": scores[0], "challenger_ndcg10": scores[1], "blind_static_choice": choice,
            "known_candidate_recall": len(positives & candidates) / len(positives) if positives else None,
            "known_candidates_missing": sorted(positives - candidates),
            "instant_oracle_gain": manual - sparse if sparse is not None and manual is not None else None,
            "crop_transition_gain": cropped - full if full is not None and cropped is not None else None,
            "strong_challenger_top10": sum((grades.get(key) or 0) >= 2 for key in rankings[1][:10]),
        })
    complete = len(rows) == 12 and all(row["fully_judged_top10"] and row["blind_static_choice"] is not None for row in rows)
    baseline = median(row["baseline_ndcg10"] for row in rows) if complete else None
    challenger = median(row["challenger_ndcg10"] for row in rows) if complete else None
    initial_ids = {"dune_tight_profile", "dune_tight_right_profile"}
    seeds = [row for row in rows if row["id"] in initial_ids]
    timings = [finite(value, "warm static latency") for value in document.get("warm_static_latency_ms", [])]
    if any(value < 0 for value in timings):
        raise ValueError("Latency cannot be negative")
    timings.sort()
    p95 = timings[math.ceil(len(timings) * 0.95) - 1] if timings else None
    manifest = document.get("static_manifest", {})
    gates = {
        "twelve_fully_judged_references": complete,
        "eight_blind_wins": complete and sum(row["blind_static_choice"] == "challenger" for row in rows) >= 8,
        "median_ndcg_improvement_20_percent": complete and baseline > 0 and challenger >= 1.2 * baseline,
        "at_most_two_regressions": complete and sum(row["challenger_ndcg10"] < row["baseline_ndcg10"] for row in rows) <= 2,
        "both_dune_seeds_hold": complete and len(seeds) == 2 and all(row["challenger_ndcg10"] >= row["baseline_ndcg10"] for row in seeds),
        "tight_profile_three_strong_matches": any(row["id"] == "dune_tight_profile" and row["strong_challenger_top10"] >= 3 for row in rows),
        "warm_static_p95_under_250ms": len(timings) >= 20 and p95 < 250,
        "operator_verified_complete_compatible_manifest": manifest.get("verified_complete_compatible") is True and bool(manifest.get("profile_id")) and bool(manifest.get("frame_generation_digest")),
    }
    return {
        "schema_version": 1, "kind": "visual_rhymes_evaluation_report", "input_sha256": digest(document),
        "shadow_only": True, "production_activation": False, "cases": rows,
        "static_gates": gates, "static_evidence_meets_gates": all(gates.values()),
        "baseline_median_ndcg10": baseline, "challenger_median_ndcg10": challenger, "warm_static_p95_ms": p95,
        "note": "Operator-supplied static evidence only. Region cropping and exact-instant refinement still require independent human decisions; this report cannot activate either. Unjudged is never zero.",
    }


def export_catalog(unit_ids: list[str]) -> dict:
    """Read only selected indexed units and verify their local source identity."""
    from lancedb.expr import col, lit
    from pipeline.config import load_config
    from pipeline.index.writer import open_db
    from pipeline.ingest.probe import _content_hash

    if not 1 <= len(unit_ids) <= 100 or len(set(unit_ids)) != len(unit_ids):
        raise ValueError("Select one to 100 unique unit IDs")
    db = open_db(load_config())
    units, films = {}, {}
    for unit_id in unit_ids:
        matches = db.open_table("units").search().where(col("unit_id") == lit(unit_id)).select(["unit_id", "film_id", "t_start", "t_end"]).limit(2).to_list()
        if len(matches) != 1:
            raise ValueError(f"Expected one published source unit for {unit_id!r}")
        unit = matches[0]
        film_id = unit["film_id"]
        if film_id not in films:
            rows = db.open_table("films").search().where(col("film_id") == lit(film_id)).limit(2).to_list()
            if len(rows) != 1:
                raise ValueError(f"Source film {film_id!r} is unavailable")
            path = Path(rows[0]["path"])
            if not path.is_file() or _content_hash(path) != film_id:
                raise ValueError(f"Source identity verification failed for {film_id!r}")
            probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_streams", "-of", "json", str(path)], check=True, capture_output=True, text=True, timeout=30)
            stream = json.loads(probe.stdout)["streams"][0]
            sar = stream.get("sample_aspect_ratio", "1:1")
            rotation = stream.get("tags", {}).get("rotate", 0)
            rotated = any(float(item.get("rotation", 0)) % 360 for item in stream.get("side_data_list", []))
            if sar not in ("1:1", "N/A") or float(rotation) % 360 or rotated:
                raise ValueError("Normalize a separately versioned display image before evaluating non-square pixels or rotated media")
            picture = Picture(stream["width"], stream["height"])
            films[film_id] = {"width": picture.width, "height": picture.height}
        frames = db.open_table("frames").search().where(col("unit_id") == lit(unit_id)).select(["frame_index", "timestamp", "timestamp_source"]).limit(100).to_list()
        units[unit_id] = {**unit, "picture": films[film_id], "indexed_frames": frames}
    return {"schema_version": 1, "kind": "visual_rhymes_source_catalog", "coordinate_space": COORDINATE_SPACE, "exported_at": datetime.now(timezone.utc).isoformat(), "source_identity_contract": "Scene Recall first/last 4 MiB source fingerprint verified at export", "units": units}


def template(kind: str) -> dict:
    if kind == "evaluation":
        return {"schema_version": 1, "kind": "visual_rhymes_human_evaluation", "cases": [{
            "id": "replace-with-frozen-reference-id", "candidate_units": [], "known_positive_units": [],
            "baseline_top10": [], "challenger_top10": [], "geometry_grades": {}, "blind_static_choice": None,
            "instant_oracle": {"sparse_reference": None, "sparse_candidate": None, "hand_picked_reference": None, "hand_picked_candidate": None, "sparse_transition_grade": None, "hand_picked_transition_grade": None},
            "region_comparison": {"blind_packet_sha256": None, "full_frame_transition_grade": None, "crop_transition_grade": None},
        }], "warm_static_latency_ms": [], "static_manifest": {"profile_id": None, "frame_generation_digest": None, "verified_complete_compatible": False}}
    selection = {"unit_id": "REPLACE_UNIT_ID", "film_id": "REPLACE_FILM_ID", "mode": "fixed", "timestamp": 1.0, "timestamp_source": "operator_decoded_pts", "pts_evidence": "REPLACE_WITH_DECODER_LOG_REFERENCE", "handle_seconds": 0.5}
    return {"schema_version": 1, "kind": "visual_rhymes_proposal_cases", "output": {"width": 1920, "height": 1080}, "max_upscale": 2, "cases": [{"id": "first-region-pair", "reference": dict(selection), "candidate": dict(selection), "reference_region": {"x": 0.25, "y": 0.25, "width": 0.5, "height": 0.5}, "candidate_region": {"x": 0.4, "y": 0.4, "width": 0.2, "height": 0.2}, "reference_crop": None, "protected_context": None}]}


def _write(path: Path, document: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        json.dump(document, handle, indent=2, allow_nan=False)
        handle.write("\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("template")
    create.add_argument("--kind", choices=["proposal", "evaluation"], default="proposal")
    catalog = commands.add_parser("catalog")
    catalog.add_argument("--unit-id", action="append", required=True)
    propose = commands.add_parser("propose")
    propose.add_argument("--catalog", type=Path, required=True)
    propose.add_argument("--cases", type=Path, required=True)
    blind = commands.add_parser("blind")
    blind.add_argument("--proposals", type=Path, required=True)
    blind.add_argument("--key", type=Path, required=True)
    blind.add_argument("--seed", type=int, default=20260911)
    score = commands.add_parser("score")
    score.add_argument("--evaluation", type=Path, required=True)
    for command in (create, catalog, propose, blind, score):
        command.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    def read(path):
        return json.loads(path.read_text(encoding="utf-8"))
    try:
        if args.output.exists():
            raise FileExistsError(f"Output already exists: {args.output}")
        if args.command == "template":
            result = template(args.kind)
        elif args.command == "catalog":
            result = export_catalog(args.unit_id)
        elif args.command == "propose":
            result = propose_document(read(args.cases), read(args.catalog))
        elif args.command == "score":
            result = score_evaluation(read(args.evaluation))
        else:
            if args.key.exists() or args.key.resolve() == args.output.resolve():
                raise FileExistsError("Blind key must be a new path separate from the judging packet")
            result, key = blind_packet(read(args.proposals), args.seed)
            _write(args.key, key)
        _write(args.output, result)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
