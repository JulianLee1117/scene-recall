"""Frozen music-editor comparisons, separate from subjective played acceptance.

The default command prepares a dry comparison under the requested output path.
Execution and hosted requests require explicit CLI switches. This runner never
uses the project API, saves a project, listens to music or changes source evidence.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time
from unittest.mock import patch


CONTRACT = "frozen-music-edit-comparison-v1"


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def frozen_document(value):
    """Normalize a private copy; retain the original input's identity separately."""
    from pipeline.lab.models import ProjectDocument
    if not isinstance(value, dict):
        raise ValueError("A frozen input must be a JSON object")
    document = value.get("document", value)
    if not isinstance(document, dict):
        raise ValueError("A frozen input must contain a project document")
    result = ProjectDocument.model_validate(deepcopy(document)).model_dump(mode="json")
    if not result.get("track"):
        raise ValueError("A comparison needs an imported track and selected passage")
    return result


def document_metrics(document):
    from pipeline.lab.pacing import timing_diagnostics
    slots = (document.get("music_timeline") or {}).get("slots", [])
    by_id = {clip["id"]: clip for clip in document["clips"]}
    placed = [by_id[slot["clip_id"]] for slot in slots if slot.get("clip_id")]
    return {"positions": len(slots), "placed": len(placed),
            "gaps": [slot["id"] for slot in slots if not slot.get("clip_id")],
            "distinct_units": len({clip.get("unit_id") for clip in placed if clip.get("unit_id")}),
            "timing": timing_diagnostics(slots) if slots else None}


def document_changes(before, after):
    """Compare source choices by authority, ignoring generated clip UUIDs."""
    def rows(document):
        by_id = {clip["id"]: clip for clip in document["clips"]}
        result = []
        for slot in (document.get("music_timeline") or {}).get("slots", []):
            clip = by_id.get(slot.get("clip_id"))
            source = ({key: clip.get(key) for key in
                       ("film_id", "unit_id", "source_start", "source_end", "crop")} if clip else None)
            result.append({"id": slot["id"], "start": slot["start"], "end": slot["end"], "source": source})
        return result
    left, right = rows(before), rows(after)
    changes = []
    for index in range(max(len(left), len(right))):
        old = left[index] if index < len(left) else None
        new = right[index] if index < len(right) else None
        if old is None or new is None or any(old[key] != new[key] for key in ("start", "end", "source")):
            changes.append({"position": index + 1, "before": old, "after": new})
    return {"slot_ids_preserved": [row["id"] for row in left] == [row["id"] for row in right],
            "position_count_change": len(right) - len(left), "changed_positions": changes}


def flag_coverage(document, known_failures, flagged_ids, inspected_ids):
    """Coverage of supplied examples only; unlabelled shots are not negatives."""
    valid = {slot["id"] for slot in (document.get("music_timeline") or {}).get("slots", [])}
    known = []
    for case in known_failures:
        if (not isinstance(case, dict) or set(case) != {"slot_id", "reason"}
                or case["slot_id"] not in valid or not isinstance(case["reason"], str)
                or not case["reason"].strip() or case["slot_id"] in known):
            raise ValueError("Known failures need unique existing slot IDs and a supplied reason")
        known.append(case["slot_id"])
    flagged, inspected = set(flagged_ids), set(inspected_ids)
    if not flagged <= valid or not inspected <= valid:
        raise ValueError("Inspection diagnostics refer to slots outside the frozen input")
    return {"known_failure_count": len(known), "flagged_known_failures": sorted(set(known) & flagged),
            "unflagged_known_failures": sorted(set(known) - flagged),
            "inspected_known_failures": sorted(set(known) & inspected),
            "flagged_unlabelled_slots": sorted(flagged - set(known)),
            "precision": None,
            "note": "Supplied known failures are examples, not an exhaustive labelled test set. No precision or creative success is inferred."}


class HostedBudgetExceeded(RuntimeError):
    pass


@contextmanager
def hosted_budget(*, allow_hosted, maximum):
    """Instrument only this standalone process; count before sending a request."""
    from pipeline.lab import music
    if isinstance(maximum, bool) or not isinstance(maximum, int) or not 0 <= maximum <= 5:
        raise ValueError("A bounded comparison permits zero to five hosted calls")
    original, calls = music._hosted_json, []
    def invoke(*args, **kwargs):
        if not allow_hosted or len(calls) >= maximum:
            raise HostedBudgetExceeded("Hosted work is disabled or this comparison's explicit call budget is exhausted")
        receipt = kwargs.get("receipt_path")
        record = {"operation": kwargs.get("operation", "plan"),
                  "receipt": str(receipt) if receipt else None, "status": "running"}
        calls.append(record)
        started = time.perf_counter()
        try:
            value = original(*args, **kwargs)
            record["status"] = "completed"
            return value
        except BaseException as error:
            record.update(status="cancelled" if isinstance(error, KeyboardInterrupt) else "failed", error=str(error))
            raise
        finally:
            record["seconds"] = time.perf_counter() - started
            if receipt and Path(receipt).is_file():
                try:
                    saved = json.loads(Path(receipt).read_text(encoding="utf-8"))
                    record.update({key: saved[key] for key in ("provider", "model", "usage", "request_id") if key in saved})
                except (OSError, ValueError, TypeError) as error:
                    record["receipt_error"] = str(error)
    with patch.object(music, "_hosted_json", invoke):
        yield calls


def prepare_run(value, stage, known_failures=()):
    if stage == "assembly":
        if known_failures:
            raise ValueError("Known inspection failures do not apply to assembly comparisons")
        from pipeline.experiments.assembly_comparison import prepare_run as prepare_assembly
        return prepare_assembly(value)
    if stage not in {"timing", "inspection"}:
        raise ValueError("Choose a timing or inspection comparison")
    document = frozen_document(value)
    if not document.get("analysis"):
        raise ValueError("Supply frozen listening evidence; this evaluation never listens")
    if stage == "inspection" and not (document.get("music_timeline") or {}).get("slots"):
        raise ValueError("Inspection compares an existing frozen arrangement")
    coverage = flag_coverage(document, known_failures, [], [])
    return {"contract": CONTRACT, "status": "dry-run", "stage": stage,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "source_input_sha256": digest(value), "frozen_document_sha256": digest(document),
            "source_project_id": value.get("id") if "document" in value else None,
            "source_revision": value.get("revision") if "document" in value else None,
            "variants": (["beats-on", "beats-off"] if stage == "timing" else ["inspection-off", "inspection-on"]),
            "metrics_before": document_metrics(document), "known_failures": deepcopy(list(known_failures)),
            "coverage": coverage, "human_preference": None,
            "limits": "Frozen private comparison, no listening or saved-project writes. Mechanical changes and model observations are not creative-quality judgments.",
            "document": document}


def timing_scopes(document, *, beat_guides):
    """Audit the same per-batch cut offers that selection would receive."""
    from pipeline.lab.long_audio import analysis_for_passage, rhythm_for_passage
    from pipeline.lab.pacing import batch_slots
    from pipeline.lab.source_timing import initial_timing_scope
    from pipeline.lab.timeline import section_for
    result = []
    for group in batch_slots(document["music_timeline"]["slots"]):
        passage = {"start": group[0]["start"], "end": group[-1]["end"]}
        part = deepcopy(document)
        part.update(passage=passage, clips=[], analysis=analysis_for_passage(document["analysis"], passage),
                    rhythm=rhythm_for_passage(document.get("rhythm") or {}, passage))
        part["music_timeline"] = {"track_id": part["track"]["id"], "passage": passage, "slots": deepcopy(group)}
        for slot in part["music_timeline"]["slots"]:
            slot["section_index"] = section_for(slot["start"], slot["end"], part["analysis"]["segments"])
        result.append(initial_timing_scope(part, beat_guides=beat_guides))
    return result


def inject_inspection_hints(contexts, hints):
    """Explicit fixtures affect only review hints, never sources or cut authority."""
    if (not isinstance(hints, dict) or any(not isinstance(value, str)
            or value not in {"none", "action_timing", "visual_fit"} for value in hints.values())):
        raise ValueError("Injected hints must map frozen slot IDs to none, action_timing or visual_fit")
    result = deepcopy(contexts)
    ledgers = [row for context in result for row in context.get("ledger", [])]
    identities = {row["slot_id"] for row in ledgers}
    if not set(hints) <= identities:
        raise ValueError("Injected hints must identify slots in the frozen selector ledger")
    for row in ledgers:
        if row["slot_id"] in hints:
            row["inspection_hint"] = deepcopy(hints[row["slot_id"]])
    return result


def _write(path, value):
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8")


def _inspection_ids(diagnostics, name):
    """Use the coordinator's recorded scope; never infer flags from changed clips."""
    values = diagnostics.get(name)
    if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
        raise ValueError(f"Inspection diagnostics require a list of {name}")
    return values


def execute_run(run, value, config, db, output, *, allow_hosted=False, maximum=0,
                cached_baseline=False, injected_hints=None):
    """Call existing private stages; never enqueue work or persist a project."""
    if run["stage"] == "assembly":
        from pipeline.experiments.assembly_comparison import execute_run as execute_assembly
        return execute_assembly(run, value, config, db, output, allow_hosted=allow_hosted, maximum=maximum)
    from dataclasses import replace
    import uuid
    from pipeline.lab.generation import _analysis_is_current, _regeneration_document
    from pipeline.lab.timing_planner import run_timing_job
    document = deepcopy(run["document"])
    if not _analysis_is_current(document):
        raise ValueError("Frozen listening is missing, stale or invalid; no listening is performed by this runner")
    original_digest = digest(document)
    contexts = deepcopy(value.get("contexts", []))
    if run["stage"] == "inspection" and not contexts:
        raise ValueError("Inspection replay requires frozen selector contexts from an inspection-input receipt")
    if injected_hints:
        contexts = inject_inspection_hints(contexts, injected_hints)
    reports = []
    run.update(status="running", measurements=reports, injected_hint_slot_ids=sorted(injected_hints or {}),
               automatic_hints_unchanged=not bool(injected_hints),
               contexts_sha256=digest(contexts) if contexts else None)
    with hosted_budget(allow_hosted=allow_hosted, maximum=maximum) as calls:
        try:
            for index, variant in enumerate(run["variants"]):
                started, messages = time.perf_counter(), []
                job_id = "eval-" + variant + "-" + uuid.uuid4().hex[:12]
                def progress(message):
                    messages.append(str(message))
                    _write(output / "progress.json", {"variant": variant, "message": str(message)})
                call_start = len(calls)
                progress("Running frozen comparison")
                if run["stage"] == "timing":
                    private = _regeneration_document(document)
                    job = {"id": job_id, "kind": "plan", "document": private, "snapshot": {"document": private}}
                    if index == 0 and cached_baseline:
                        with hosted_budget(allow_hosted=False, maximum=0):
                            result = run_timing_job(job, config, progress, beat_guides=variant == "beats-on")
                    else:
                        result = run_timing_job(job, config, progress, beat_guides=variant == "beats-on")
                    diagnostics = deepcopy(result["direction_plan"]["timing_plan"])
                    scopes = timing_scopes(result, beat_guides=variant == "beats-on")
                    _write(output / f"{variant}-cut-offers.json", scopes)
                elif variant == "inspection-on":
                    from pipeline.lab.footage_review import inspect_edit
                    variant_config = replace(config, lab=replace(config.lab, footage_inspection=True))
                    result, diagnostics = inspect_edit(deepcopy(document), variant_config, db, progress,
                        job_id, contexts=deepcopy(contexts))
                else:
                    result = deepcopy(document)
                    diagnostics = {"status": "disabled", "flagged_slot_ids": [], "inspected_slot_ids": [],
                                   "reviewed_slot_ids": [],
                                   "note": "Frozen provisional selection, with no footage inspection."}
                _write(output / f"{variant}-document.json", result)
                _write(output / f"{variant}-diagnostics.json", diagnostics)
                report = {"variant": variant, "status": "completed", "seconds": time.perf_counter() - started,
                    "hosted_calls": deepcopy(calls[call_start:]), "metrics": document_metrics(result),
                    "changes_from_frozen": document_changes(document, result), "diagnostics": diagnostics,
                    "progress": messages, "document_sha256": digest(result)}
                if run["stage"] == "inspection":
                    report["coverage"] = flag_coverage(document, run["known_failures"],
                        _inspection_ids(diagnostics, "flagged_slot_ids"), _inspection_ids(diagnostics, "inspected_slot_ids"))
                    reviewed = _inspection_ids(diagnostics, "reviewed_slot_ids")
                    # A completed observation and a completed editorial review are different coverage.
                    checked = flag_coverage(document, run["known_failures"], [], reviewed)
                    report["coverage"]["reviewed_known_failures"] = checked["inspected_known_failures"]
                reports.append(report)
                assert digest(document) == original_digest, "A comparison mutated its frozen input"
                _write(output / "run.json", run)
            first = json.loads((output / f"{run['variants'][0]}-document.json").read_text(encoding="utf-8"))
            second = json.loads((output / f"{run['variants'][1]}-document.json").read_text(encoding="utf-8"))
            run.update(status="completed", between_variants=document_changes(first, second))
        except Exception as error:
            run.update(status="failed", error=str(error))
            raise
        finally:
            run.update(hosted_calls=deepcopy(calls), frozen_input_unchanged=digest(document) == original_digest)
            _write(output / "run.json", run)
    return run


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("timing", "inspection", "assembly"))
    parser.add_argument("--input", type=Path, required=True, help="Frozen document/project or inspection-input receipt")
    parser.add_argument("--out", type=Path, required=True, help="New directory for private comparison artifacts")
    parser.add_argument("--known-failures", type=Path, help="Optional JSON [{slot_id, reason}] for flag-coverage diagnostics")
    parser.add_argument("--inject-hints", type=Path, help="Inspection only: explicit fixture hints keyed by frozen slot ID")
    parser.add_argument("--execute", action="store_true", help="Execute existing stages; cache misses remain blocked unless hosted work is allowed")
    parser.add_argument("--allow-hosted", action="store_true", help="Explicitly allow hosted requests during execution")
    parser.add_argument("--max-hosted-calls", type=int, default=0, help="Shared request ceiling; at most 4 for assembly, 5 otherwise")
    parser.add_argument("--cached-baseline", action="store_true", help="Timing only: require beats-on baseline to be cached")
    parser.add_argument("--config", type=Path, help="Optional repository configuration")
    args = parser.parse_args(argv)
    if args.allow_hosted and (not args.execute or not 1 <= args.max_hosted_calls <= 5):
        parser.error("--allow-hosted needs --execute and --max-hosted-calls between 1 and 5")
    if not args.allow_hosted and args.max_hosted_calls:
        parser.error("A positive hosted budget requires --allow-hosted")
    if args.stage == "assembly" and args.max_hosted_calls > 4:
        parser.error("Assembly comparisons permit at most four hosted calls")
    if args.known_failures and args.stage == "assembly":
        parser.error("Known failure labels apply only to inspection comparisons")
    if args.inject_hints and args.stage != "inspection":
        parser.error("Hint injection applies only to inspection comparisons")
    if args.cached_baseline and args.stage != "timing":
        parser.error("A cached baseline applies only to timing comparisons")
    value = json.loads(args.input.read_text(encoding="utf-8"))
    known = json.loads(args.known_failures.read_text(encoding="utf-8")) if args.known_failures else []
    hints = json.loads(args.inject_hints.read_text(encoding="utf-8")) if args.inject_hints else None
    run = prepare_run(value, args.stage, known)
    args.out.mkdir(parents=True, exist_ok=False)
    output = args.out.resolve()
    run.update(source_file=str(args.input.resolve()), source_file_sha256=hashlib.sha256(args.input.read_bytes()).hexdigest(),
               hosted_allowed=args.allow_hosted, hosted_limit=args.max_hosted_calls)
    _write(output / "frozen-input.json", value)
    if args.stage == "timing":
        from pipeline.lab.generation import _regeneration_document
        from pipeline.lab.timing_planner import timing_payload
        private = _regeneration_document(run["document"])
        for enabled, variant in ((True, "beats-on"), (False, "beats-off")):
            _write(output / f"{variant}-timing-payload.json", timing_payload(private, beat_guides=enabled))
    _write(output / "run.json", run)
    if args.execute:
        def startup_progress(message):
            event = {"stage": "preflight", "message": str(message), "at": datetime.now(timezone.utc).isoformat()}
            run.update(status="starting", active_stage="preflight")
            _write(output / "progress.json", event)
            _write(output / "run.json", run)
            print(message, flush=True)
        try:
            startup_progress("Preparing the frozen comparison; no AI requests started")
            from pipeline.lab.resources import configure_editor_process, editor_config
            configure_editor_process()
            from dotenv import load_dotenv
            load_dotenv()
            from pipeline.config import load_config
            config, db = editor_config(load_config(args.config)), None
            if args.stage in {"inspection", "assembly"}:
                import lancedb
                from pipeline.lab.index_snapshot import acquire_editor_snapshot
                db = acquire_editor_snapshot(config, lancedb.connect(str(config.paths.assets_dir / "db")), startup_progress, lambda: False)
                run["snapshot_versions"] = db.versions
            execute_run(run, value, config, db, output, allow_hosted=args.allow_hosted, maximum=args.max_hosted_calls,
                        cached_baseline=args.cached_baseline, injected_hints=hints)
        except BaseException as error:
            run.update(status="cancelled" if isinstance(error, KeyboardInterrupt) else "failed", error=str(error))
            _write(output / "run.json", run)
            raise
    print(json.dumps({"status": run["status"], "run": str(output / "run.json")}))
    return 1 if run["status"] in {"partial", "failed", "cancelled"} else 0


if __name__ == "__main__":
    raise SystemExit(main())
