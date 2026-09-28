"""Reproducible played-cut ablations, separate from human editorial acceptance.

Example (writes only to the requested output directory, never Lab projects)::

    python -m pipeline.experiments.match_effectiveness draft --cohort ID --out cases.json
    python -m pipeline.experiments.match_effectiveness run --cases cases.json --out run --case motion-01
    python -m pipeline.experiments.match_effectiveness score --run run/run.json --review run/review.json --out scores.json

Draft references are deliberately unreviewed. Inspect source moments, set subject
regions/scenario labels and exclude every development shot before ``freeze``.
The freeze command records the human who approved the reference selection, not
human judgments of the resulting cuts. Grade only actual rendered playback.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from contextlib import contextmanager, ExitStack
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import platform
import time
from unittest.mock import patch

from pipeline.matching import cohort

VARIANTS = ("legacy-fixed", "current-fixed", "current-nearby")


def _stamp():
    return datetime.now(timezone.utc).isoformat()


def _check_document(document, kind):
    if document.get("schema_version") != 1 or document.get("kind") != kind:
        raise ValueError(f"Expected version 1 {kind}")


def validate_cases(document):
    _check_document(document, "match_effectiveness_cases")
    identities, units = set(), set()
    development = set(document.get("development_unit_ids", []))
    for case in document["cases"]:
        if case["id"] in identities or case["unit_id"] in units:
            raise ValueError("References must have unique case and shot identities")
        if case["unit_id"] in development:
            raise ValueError("Review and development shots must be disjoint")
        identities.add(case["id"])
        units.add(case["unit_id"])
        if case["category"] not in {"visual", "motion"}:
            raise ValueError("Unknown review category")
        if case["focus"] not in {"image", "camera", "subject"}:
            raise ValueError("Review cases need an explicit matching focus")
        if (case["focus"] == "image") != (case["category"] == "visual"):
            raise ValueError("Visual and movement case categories must match their focus")
        start, timestamp, end = (case[key] for key in ("source_start", "reference_time", "source_end"))
        if not all(math.isfinite(x) for x in (start, timestamp, end)) or not 0 <= start <= timestamp < end:
            raise ValueError("Reference timestamp must remain inside its source shot")
        if case.get("region") is not None:
            from pipeline.lab.models import Crop

            Crop.model_validate(case["region"])
    if document.get("status") == "frozen":
        if not document.get("reviewer") or not document.get("frozen_at"):
            raise ValueError("Frozen cases require human reference approval provenance")
        expected = cohort.digest({k: v for k, v in document.items() if k != "frozen_sha256"})
        if document.get("frozen_sha256") != expected:
            raise ValueError("Frozen case selection changed; create a separate study")
        for case in document["cases"]:
            if not case.get("scenario"):
                raise ValueError("Inspect and label every frozen reference scenario")
            if case["focus"] == "subject" and case.get("region") is None:
                raise ValueError("Subject ablations need a human selected region for the fixed-region baseline")


def draft_cases(subset, previous_review=None):
    """Choose without inspecting scores; leave subject region/semantic labels unset."""
    previous_review = previous_review or {}
    development = set(previous_review.get("development_unit_ids", []))
    rows = {row["unit_id"]: row for row in subset["units"]}
    selected, used = [], set()
    seeds = [row["unit_id"] for row in previous_review.get("visual", [])]
    # Reserve enough motion rows; prefer the historical visual references first.
    order = seeds + sorted(rows, key=lambda key: (key in subset["motion_unit_ids"], cohort.digest(key)))
    for identity in order:
        if identity in development or identity in used or identity not in rows:
            continue
        used.add(identity)
        selected.append(("visual", identity))
        if len(selected) == 12:
            break
    motion_ids = sorted(subset["motion_unit_ids"], key=cohort.digest)
    for identity in motion_ids:
        if identity in development or identity in used:
            continue
        used.add(identity)
        selected.append(("motion", identity))
        if len(selected) == 36:
            break
    if len(selected) != 36 or sum(kind == "visual" for kind, _ in selected) != 12:
        raise ValueError("The cohort needs 12 visual and 24 distinct non-development motion references")
    counts, cases = defaultdict(int), []
    for category, identity in selected:
        counts[category] += 1
        row = rows[identity]
        frames = sorted([frame for frame in subset["frames"] if frame["unit_id"] == identity], key=lambda frame: frame["frame_index"])
        timestamp = frames[0]["timestamp"] if category == "visual" and frames else (row["t_start"] + row["t_end"]) / 2
        cases.append({
            "id": f"{category}-{counts[category]:02}", "category": category,
            "focus": "image" if category == "visual" else ("camera" if counts[category] <= 8 else "subject"),
            "unit_id": identity, "film_id": row["film_id"], "description": row.get("caption", ""),
            "source_start": row["t_start"], "source_end": row["t_end"], "reference_time": timestamp,
            "region": None, "scenario": None,
        })
    document = {
        "schema_version": 1, "kind": "match_effectiveness_cases", "status": "draft-unreviewed",
        "cohort_id": subset["id"], "cohort_sha256": cohort.digest(subset),
        "selection": "historical-visual-then-hash-heldout-v1", "development_unit_ids": sorted(development),
        "instructions": "Inspect each source, label its scenario and adjust focus/region/time before freezing. Captions and draft focus assignments are not verified action labels. No human cut grades are inferred.",
        "cases": cases,
    }
    validate_cases(document)
    return document


def freeze_cases(document, reviewer):
    validate_cases(document)
    if document.get("status") == "frozen" or not reviewer.strip():
        raise ValueError("Freeze a reviewed draft once, with the approving person's name")
    result = {**deepcopy(document), "status": "frozen", "reviewer": reviewer.strip(), "frozen_at": _stamp()}
    result["frozen_sha256"] = cohort.digest(result)
    validate_cases(result)
    return result


def request_for(case, variant, cohort_id):
    from pipeline.lab.models import ProjectDocument

    if variant not in VARIANTS:
        raise ValueError("Unknown matching ablation")
    clip = {"id": "reference", **{key: case[key] for key in ("unit_id", "film_id", "source_start", "source_end", "reference_time")}, "region": case.get("region")}
    document = ProjectDocument(clips=[clip]).model_dump(mode="json")
    options = {
        "cohort_id": cohort_id, "reference_clip_id": "reference", "allow_reframing": False,
        "mode": "image" if case["focus"] == "image" else "movement",
        "movement": "subject" if case["focus"] == "subject" else "camera",
    }
    if variant != "legacy-fixed":
        options.update(focus=case["focus"], timing="nearby" if variant == "current-nearby" else "fixed")
    return document, options


@contextmanager
def _instrument():
    """Sequential offline runner only; never patches the live worker process."""
    from pipeline.matching import media, motion

    costs = defaultdict(lambda: {"calls": 0, "seconds": 0.0})

    def wrapper(key, function):
        def invoke(*args, **kwargs):
            began = time.perf_counter()
            try:
                return function(*args, **kwargs)
            finally:
                costs[key]["calls"] += 1
                costs[key]["seconds"] += time.perf_counter() - began
        return invoke

    with ExitStack() as stack:
        for owner, attribute, name in (
            (motion.Flow, "__init__", "flow_model_init"), (motion.Flow, "pair", "flow_inference"),
            (motion, "summarize", "camera_separation"), (media, "samples", "source_decode"),
            (cohort, "verify", "source_verification"),
        ):
            stack.enter_context(patch.object(owner, attribute, wrapper(name, getattr(owner, attribute))))
        yield costs


def _memory_start():
    import torch

    if torch.cuda.is_available():
        torch.cuda.synchronize()
        torch.cuda.reset_peak_memory_stats()


def _memory_result():
    import torch

    result = {"cuda_peak_allocated_bytes": None, "cuda_peak_reserved_bytes": None, "process_rss_bytes_at_end": None}
    if torch.cuda.is_available():
        torch.cuda.synchronize()
        result.update(cuda_peak_allocated_bytes=torch.cuda.max_memory_allocated(), cuda_peak_reserved_bytes=torch.cuda.max_memory_reserved())
    try:
        import psutil

        result["process_rss_bytes_at_end"] = psutil.Process().memory_info().rss
    except ImportError:
        pass
    return result


def run_cases(config, db, cases, output, *, variants=VARIANTS, case_ids=None, previews=True):
    from pipeline.lab.matching import render_candidate
    from pipeline.matching import service

    validate_cases(cases)
    subset = cohort.load(config, cases["cohort_id"])
    if cohort.digest(subset) != cases["cohort_sha256"]:
        raise ValueError("Case cohort content changed")
    if not variants or len(set(variants)) != len(variants) or set(variants) - set(VARIANTS):
        raise ValueError("Choose unique known ablation variants")
    selected = [case for case in cases["cases"] if case_ids is None or case["id"] in case_ids]
    if not selected or (case_ids is not None and set(case_ids) != {case["id"] for case in selected}):
        raise ValueError("Unknown or empty case selection")
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    # Matching reads prepared assets; only the renderer gets a scratch assets root.
    render_config = replace(config, paths=replace(config.paths, assets_dir=output))
    source_paths = [Path(__file__), *sorted((Path(__file__).parents[1] / "matching").glob("*.py"))]
    run = {
        "schema_version": 1, "kind": "match_effectiveness_run", "created_at": _stamp(),
        "cases_sha256": cohort.digest(cases), "cases": deepcopy(cases), "variants": list(variants),
        "runtime": {"python": platform.python_version(), "platform": platform.platform()},
        "source_sha256": {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in source_paths},
        "quality_status": "ungraded", "measurements": [],
        "performance_note": "Sequential process; first request includes process/model cold costs. Later requests may reuse OS/CUDA caches. RSS is end-of-request, not peak. Timers are inclusive and may overlap. No p95 claim from a small sample.",
    }
    for case in selected:
        # Counterbalance execution order deterministically by reference, independently of scores.
        order = sorted(variants, key=lambda name: cohort.digest([case["id"], name]))
        for variant in order:
            document, options = request_for(case, variant, cases["cohort_id"])
            row = {"case_id": case["id"], "variant": variant, "order": len(run["measurements"]), "options": options, "progress": [], "candidates": []}
            began = time.perf_counter()

            def progress(message):
                row["progress"].append({"at_seconds": time.perf_counter() - began, "message": message})
                print(f"{case['id']} / {variant}: {message}", flush=True)

            _memory_start()
            try:
                with _instrument() as costs:
                    options["profile_id"] = service.validate_request(config, db, document, options)
                    result = service.find(config, db, document, options, progress)
                row.update(status="completed", matching_seconds=time.perf_counter() - began, component_costs=dict(costs), result=result, **_memory_result())
                row["candidates"] = result["candidates"][:3]
                if previews:
                    for index, candidate in enumerate(row["candidates"]):
                        identity = cohort.digest([case["id"], variant])[:24]
                        render_candidate(identity, document, candidate, render_config, db, None, progress, lambda: False)
                        candidate["pair_path"] = f"lab/renders/{identity}-{candidate['id']}-proposed/output.mp4"
                        candidate["original_pair_path"] = candidate["pair_path"] if candidate.get("original_is_proposed") else f"lab/renders/{identity}-{candidate['id']}-original/output.mp4"
                        candidate["pair_sha256"] = hashlib.sha256((output / candidate["pair_path"]).read_bytes()).hexdigest()
                        if index == 0:
                            row["first_rendered_seconds"] = time.perf_counter() - began
                        if candidate.get("preview_ready") and "first_playable_seconds" not in row:
                            row["first_playable_seconds"] = time.perf_counter() - began
                    row["rendered_top3_seconds"] = time.perf_counter() - began if row["candidates"] else None
                    row["verified_previews"] = sum(bool(candidate.get("preview_ready")) for candidate in row["candidates"])
                    row["top3_playable_seconds"] = row["rendered_top3_seconds"] if row["candidates"] and row["verified_previews"] == len(row["candidates"]) else None
            except (ValueError, OSError, RuntimeError, TimeoutError) as error:
                row.update(status="unavailable", error=f"{type(error).__name__}: {error}", **_memory_result())
            row["elapsed_seconds"] = time.perf_counter() - began
            run["measurements"].append(row)
            # Keep completed work if a later diagnostic fails; never overwrite prior runs.
            partial = output / "run.partial.json"
            partial.write_text(json.dumps(run, indent=2, allow_nan=False), encoding="utf-8")
    run["source_changed_during_run"] = [path.name for path in source_paths if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest() != run["source_sha256"][path.name]]
    run["run_sha256"] = cohort.digest(run)
    cohort.write_new(output / "run.json", run)
    cohort.write_new(output / "review.json", review_template(run))
    return run


def _run_digest(run):
    _check_document(run, "match_effectiveness_run")
    validate_cases(run["cases"])
    expected = cohort.digest({k: v for k, v in run.items() if k != "run_sha256"})
    if run.get("run_sha256") != expected:
        raise ValueError("Recorded run changed; human judgments cannot be carried across altered proposals")
    return expected


def review_template(run):
    identity = _run_digest(run)
    groups = defaultdict(list)
    for measurement in run["measurements"]:
        token = cohort.digest([identity, measurement["case_id"], measurement["variant"]])[:16]
        candidates = []
        for rank, candidate in enumerate(measurement["candidates"], 1):
            candidates.append({
                "rank": rank, "candidate_token": cohort.digest([token, candidate["id"]])[:16],
                "pair_path": candidate.get("pair_path"), "preview_verified": bool(candidate.get("preview_ready")),
                "pair_sha256": candidate.get("pair_sha256"),
                "played": False, "grade": None, "timing_grade": None, "note": "",
            })
        groups[measurement["case_id"]].append({"variant_token": token, "available": measurement["status"] == "completed", "candidates": candidates})
    return {
        "schema_version": 1, "kind": "match_effectiveness_review", "run_sha256": identity,
        "reviewer": None,
        "instructions": "Play every pair before grading: 0 unusable, 1 weak, 2 useful, 3 strong. Grade timing separately. Leave unseen pairs null. Variant names and scores are hidden here; avoid run.json until review is complete. Prefer a variant only after watching all available alternatives; tie is allowed.",
        "cases": [{"case_id": case_id, "variants": sorted(variants, key=lambda row: row["variant_token"]), "preferred_variant": None} for case_id, variants in groups.items()],
    }


def score_review(run, review):
    """Null stays unknown; absent/unavailable top-three cannot produce a quality pass."""
    _check_document(review, "match_effectiveness_review")
    identity = _run_digest(run)
    if review.get("run_sha256") != identity:
        raise ValueError("Review belongs to a different immutable run")
    expected = review_template(run)
    if [case["case_id"] for case in review["cases"]] != [case["case_id"] for case in expected["cases"]]:
        raise ValueError("Review cases changed")
    names = {(row["case_id"], cohort.digest([identity, row["case_id"], row["variant"]])[:16]): row["variant"] for row in run["measurements"]}
    category = {case["id"]: case["category"] for case in run["cases"]["cases"]}
    totals = defaultdict(lambda: {"references": 0, "judged_top3": 0, "no_result_references": 0, "useful_top3": 0, "preferred": 0})
    judged_any, all_judged = False, True
    paired = defaultdict(lambda: {"judged_pairs": 0, "useful_top3": 0, "preferred_over_baseline": 0, "regressions": 0})
    for actual, original in zip(review["cases"], expected["cases"]):
        if len(actual["variants"]) != len(original["variants"]):
            raise ValueError("Review variants changed")
        variants_complete, case_grades = True, {}
        for variant, frozen in zip(actual["variants"], original["variants"]):
            if variant["variant_token"] != frozen["variant_token"] or len(variant["candidates"]) != len(frozen["candidates"]):
                raise ValueError("Review proposal identity or ranking changed")
            name = names[(actual["case_id"], variant["variant_token"])]
            total = totals[(category[actual["case_id"]], name)]
            total["references"] += 1
            grades, timing_complete = [], True
            for candidate, source in zip(variant["candidates"], frozen["candidates"]):
                for key in ("rank", "candidate_token", "pair_path", "pair_sha256", "preview_verified"):
                    if candidate[key] != source[key]:
                        raise ValueError("Reviewed preview differs from the recorded proposal")
                for key in ("grade", "timing_grade"):
                    value = candidate.get(key)
                    if value is not None and (type(value) is not int or not 0 <= value <= 3):
                        raise ValueError("Human grades must be null or integers from 0 to 3")
                    if value is not None and (candidate.get("played") is not True or not source["pair_path"] or not source["pair_sha256"] or not source["preview_verified"]):
                        raise ValueError("A human grade requires played, boundary-verified media")
                grades.append(candidate.get("grade"))
                timing_complete &= candidate.get("timing_grade") is not None
                judged_any |= any(candidate.get(key) is not None for key in ("grade", "timing_grade"))
            # Successful abstention is a known retrieval miss, not an invented
            # human grade. Operational/model failures remain unknown.
            complete = frozen["available"] and timing_complete and all(grade is not None for grade in grades)
            variants_complete &= complete
            if complete:
                case_grades[name] = max(grades, default=-1)
                total["judged_top3"] += bool(grades)
                total["no_result_references"] += not grades
                total["useful_top3"] += any(grade >= 2 for grade in grades)
        preferred = actual.get("preferred_variant")
        if preferred is not None:
            judged_any = True
            if not variants_complete:
                raise ValueError("A preference requires grading every variant's played top-three")
            if preferred != "tie":
                name = names.get((actual["case_id"], preferred))
                if name is None:
                    raise ValueError("Preferred variant is not in this case")
                totals[(category[actual["case_id"]], name)]["preferred"] += 1
        if "legacy-fixed" in case_grades:
            for name, grade in case_grades.items():
                if name == "legacy-fixed":
                    continue
                comparison = paired[(category[actual["case_id"]], name)]
                comparison["judged_pairs"] += 1
                comparison["useful_top3"] += grade >= 2
                comparison["regressions"] += grade < case_grades["legacy-fixed"]
                comparison["preferred_over_baseline"] += names.get((actual["case_id"], preferred)) == name
        all_judged &= variants_complete and preferred is not None
    if judged_any and not str(review.get("reviewer") or "").strip():
        raise ValueError("Human grades require a named reviewer")
    frozen = run["cases"].get("status") == "frozen"
    counts = {kind: sum(category[case["case_id"]] == kind for case in review["cases"]) for kind in ("visual", "motion")}
    return {
        "schema_version": 1, "kind": "match_effectiveness_scores", "run_sha256": identity,
        "review_sha256": cohort.digest(review), "reference_selection_frozen": frozen,
        "quality_status": "complete-human-review" if all_judged and review["cases"] else ("partial-human-review" if judged_any else "ungraded"),
        "eligible_for_acceptance_review": frozen and all_judged and not run.get("source_changed_during_run") and counts == {"visual": 12, "motion": 24},
        "acceptance": "pending-editorial-decision", "reference_counts": counts,
        "metrics": [{"category": kind, "variant": name, **value, "useful_top3_rate_among_judged_or_empty": value["useful_top3"] / (value["judged_top3"] + value["no_result_references"]) if value["judged_top3"] + value["no_result_references"] else None} for (kind, name), value in sorted(totals.items())],
        "paired_baseline_comparisons": [{"category": kind, "variant": name, **value,
            "proposed_motion_thresholds_met": (value["useful_top3"] >= 18 and value["preferred_over_baseline"] >= 16 and value["regressions"] <= 3) if frozen and kind == "motion" and value["judged_pairs"] == 24 and all_judged else None,
        } for (kind, name), value in sorted(paired.items())],
    }


def verify_run_media(run, base):
    """Bind imported judgments to the exact rendered files, not just their paths."""
    _run_digest(run)
    base = Path(base).resolve()
    for measurement in run["measurements"]:
        for candidate in measurement["candidates"]:
            if not candidate.get("pair_path"):
                continue
            path = (base / candidate["pair_path"]).resolve()
            if not path.is_relative_to(base) or not path.is_file():
                raise ValueError("A reviewed pair is missing or outside this run")
            if hashlib.sha256(path.read_bytes()).hexdigest() != candidate.get("pair_sha256"):
                raise ValueError("Rendered review media changed")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    draft = sub.add_parser("draft")
    draft.add_argument("--cohort", required=True)
    draft.add_argument("--out", type=Path, required=True)
    freeze = sub.add_parser("freeze")
    freeze.add_argument("--cases", type=Path, required=True)
    freeze.add_argument("--reviewer", required=True)
    freeze.add_argument("--out", type=Path, required=True)
    run = sub.add_parser("run")
    run.add_argument("--cases", type=Path, required=True)
    run.add_argument("--out", type=Path, required=True)
    run.add_argument("--variant", action="append", choices=VARIANTS)
    run.add_argument("--case", action="append")
    run.add_argument("--no-previews", action="store_true")
    score = sub.add_parser("score")
    score.add_argument("--run", type=Path, required=True)
    score.add_argument("--review", type=Path, required=True)
    score.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.action in {"draft", "run"}:
        from pipeline.config import load_config

        config = load_config()
    if args.action == "draft":
        subset = cohort.load(config, args.cohort)
        previous = cohort.cohort_path(config, args.cohort) / "review-proposed.json"
        result = draft_cases(subset, cohort.read(previous) if previous.exists() else None)
    elif args.action == "freeze":
        result = freeze_cases(cohort.read(args.cases), args.reviewer)
    elif args.action == "run":
        from pipeline.index.writer import open_db

        run_cases(config, open_db(config), cohort.read(args.cases), args.out, variants=args.variant or VARIANTS, case_ids=args.case, previews=not args.no_previews)
        return
    else:
        run = cohort.read(args.run)
        verify_run_media(run, args.run.parent)
        result = score_review(run, cohort.read(args.review))
    cohort.write_new(args.out, result)
    print(str(args.out), flush=True)


if __name__ == "__main__":
    main()
