"""Compare one frozen scene-selection request with source context off and on.

Preparation is dry by default. Explicit execution uses frozen responses or at
most two hosted requests, without retrieval, listening, rendering or project writes.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time

from pipeline.experiments.music_edit import digest, hosted_budget
from pipeline.lab.scene_selection import selection_choices, selection_manifest, selection_schema
from pipeline.lab.selection_prompt import build_selection_prompt


CONTRACT = "frozen-context-selection-comparison-v1"
VARIANTS = ("context-off", "context-on")


def _write(path, value):
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8")


def _budget(allow_hosted, maximum):
    if isinstance(maximum, bool) or not isinstance(maximum, int) or not 0 <= maximum <= 2:
        raise ValueError("A context comparison permits zero to two hosted calls")
    if bool(maximum) != bool(allow_hosted):
        raise ValueError("Hosted execution needs explicit permission and a positive call budget")


def _frozen(value):
    if not isinstance(value, dict):
        raise ValueError("Supply a frozen selection JSON object")
    # Production context receipts already freeze the treatment. Derive only the
    # exact no-context baseline; do not refresh the profile or query an index.
    if ("context_on_payload" not in value and isinstance(value.get("payload"), dict)
            and "source_context" in value["payload"]):
        value = deepcopy(value)
        value["context_on_payload"] = deepcopy(value["payload"])
        value["payload"].pop("source_context")
    required = {"payload", "schema", "offers", "sources", "timing_scope", "context_on_payload"}
    if not required <= set(value):
        raise ValueError("Freeze payload, schema, offers, sources, timing_scope and context_on_payload")
    # JSON roundtripping also rejects non-finite numbers and detaches all inputs.
    result = json.loads(json.dumps(value, allow_nan=False))
    payload, treatment = result["payload"], result["context_on_payload"]
    if not isinstance(payload, dict) or not isinstance(treatment, dict) or "source_context" in payload:
        raise ValueError("The baseline must be a selection payload without source_context")
    context = treatment.get("source_context")
    if not isinstance(context, dict) or not context:
        raise ValueError("Supply the frozen context-on payload, including source_context")
    if {key: item for key, item in treatment.items() if key != "source_context"} != payload:
        raise ValueError("Context variants may differ only by source_context")
    offers, sources, scope = result["offers"], result["sources"], result["timing_scope"]
    inspection = bool(payload.get("footage_inspection"))
    schema = selection_schema(offers, sources, scope, inspection=inspection)
    if result["schema"] != schema:
        raise ValueError("Frozen schema no longer matches the deterministic selection authority")
    manifest = selection_manifest(offers, sources, scope, inspection=inspection)
    if payload.get("response_contract") != manifest["contract"]:
        raise ValueError("Frozen response contract does not match selection authority")
    aliases = manifest["alias_to_source"]
    if set(payload.get("sources", {})) != set(aliases):
        raise ValueError("Frozen source aliases do not match the offered union")
    film_aliases = {}
    for alias, identity in aliases.items():
        actual, source = payload["sources"][alias], sources[identity]
        expected = {key: item for key, item in source.items()
                    if key not in {"unit_id", "film_id", "film_title"}}
        if {key: item for key, item in actual.items() if key != "film"} != expected:
            raise ValueError("Frozen source descriptions or legal ranges disagree with authority")
        film_alias = actual.get("film")
        if film_alias not in payload.get("films", {}):
            raise ValueError("Frozen source film alias is unavailable")
        if film_aliases.setdefault(source["film_id"], film_alias) != film_alias:
            raise ValueError("One source film cannot have different aliases")
    if len(set(film_aliases.values())) != len(film_aliases):
        raise ValueError("Different source films cannot share an alias")
    slots = payload.get("slots", [])
    if len(slots) != len(offers):
        raise ValueError("Frozen shots do not match selection authority")
    inverse = {identity: alias for alias, identity in aliases.items()}
    for slot, offer in zip(slots, offers):
        if (slot.get("key") != f"shot_{offer['slot']}"
                or any(slot.get(key) != offer[key] for key in ("slot", "start", "duration"))
                or [row["source"] for row in slot.get("candidates", [])]
                != [inverse[identity] for identity in offer["candidate_ids"]]
                or slot.get("timing") != offer.get("timing")):
            raise ValueError("Frozen shot offers or timing disagree with authority")
    projected_scope = ({key: item for key, item in scope.items()
                        if key not in {"nominal", "track_id", "boundary_frames"}} if scope else None)
    if payload.get("timing_scope") != projected_scope:
        raise ValueError("Frozen timing scope disagrees with authority")
    if not set(context.get("sources", {})) <= set(aliases):
        raise ValueError("Context cannot introduce new source aliases")
    return result


def factual_audit(payload):
    """Expose supplied claims and citations; citation presence is not truth."""
    context = payload["source_context"]
    rows = []
    for alias, record in context.get("records", {}).items():
        evidence = record.get("evidence", [])
        evidence = list(evidence.values()) if isinstance(evidence, dict) else evidence
        by_id = {item["evidence_id"]: item for item in evidence}
        source_aliases = [key for key, value in context.get("sources", {}).items()
                          if alias in value.get("record_ids", [])]
        for claim in record.get("claims", []):
            references = claim.get("evidence_refs", [])
            rows.append({"record_alias": alias, "record_id": record.get("record_id"),
                "artifact_id": record.get("artifact_id"), "film": record.get("film"),
                "source_aliases": source_aliases, "level": record.get("level"),
                "claims_omitted": record.get("claims_omitted"),
                "applicability": deepcopy(record.get("applicability", [])),
                "claim": deepcopy(claim),
                "evidence": [deepcopy(by_id[key]) for key in references if key in by_id],
                "unresolved_evidence_refs": [key for key in references if key not in by_id],
                "human_review_status": "pending", "factual_grade": None, "review_notes": None})
    return {"rows": rows, "profile_id": context.get("profile_id"),
            "packet_truncated": context.get("truncated"), "packet_limits": deepcopy(context.get("limits")),
            "manifests": deepcopy(context.get("manifests", {})),
            "coverage": deepcopy(context.get("sources", {})),
            "artifacts": deepcopy(context.get("artifacts", [])),
            "limits": "Claims and citations are supplied model evidence, not verified truth. Applicability does not expand selectable footage."}


def prepare_run(value):
    frozen = _frozen(value)
    payloads = dict(zip(VARIANTS, (frozen["payload"], frozen["context_on_payload"])))
    prompts = {name: build_selection_prompt(payload) for name, payload in payloads.items()}
    return {"contract": CONTRACT, "status": "dry-run", "created_at": datetime.now(timezone.utc).isoformat(),
        "source_input_sha256": digest(value), "frozen_input_sha256": digest(frozen),
        "schema_sha256": digest(frozen["schema"]), "variants": list(VARIANTS), "frozen": frozen,
        "prompts": prompts,
        "input_hashes": {name: {"payload_sha256": digest(payload), "prompt_sha256": digest(prompts[name])}
                         for name, payload in payloads.items()},
        "authority_sha256": digest({key: frozen[key] for key in ("offers", "sources", "timing_scope")}),
        "factual_audit": factual_audit(frozen["context_on_payload"]),
        "human_review": {"status": "pending", "creative_preference": None, "factual_preference": None,
                         "played_variants": [], "notes": None},
        "limits": "One frozen selector pair. No retrieval, listening, saved-project changes or automatic rendering. Mechanical validity and model reasons do not establish factual or creative improvement."}


def played_material(frozen, choices):
    """Create ordinary source-backed clip rows without applying them to a project."""
    from pipeline.lab.models import ClipSelection
    clips, slots = [], []
    for choice in choices:
        identity = choice["candidate_id"]
        clip = None
        if identity is not None:
            source = frozen["sources"][identity]
            clip = ClipSelection(id="comparison-" + digest(choice)[:20], film_id=source["film_id"],
                unit_id=identity, title=source.get("caption", "")[:200], source_start=choice["source_start"],
                source_end=choice["source_start"] + choice["duration"]).model_dump(mode="json")
            clips.append(clip)
        slots.append({"slot": choice["slot"], "start": choice["start"],
                      "end": choice["start"] + choice["duration"],
                      "clip_id": clip["id"] if clip else None, "reason": choice["reason"]})
    document = frozen.get("document") or {}
    return {"contract": "frozen-selection-playback-material-v1", "clips": clips, "slots": slots,
            "track": deepcopy(document.get("track")), "passage": deepcopy(frozen["payload"]["passage"]),
            "time_base": "source-track-seconds", "playback_status": "pending", "rendered_file": None,
            "limits": "Selector-only clip material, not an applied project or render. Existing neighbors and replacement-retention policies have not been assembled. Source media has not been revalidated or played."}


def _changes(first, second):
    fields = ("candidate_id", "source_start", "start", "duration")
    return [{"slot": left["slot"], "before": {key: left[key] for key in fields},
             "after": {key: right[key] for key in fields}}
            for left, right in zip(first, second) if any(left[key] != right[key] for key in fields)]


def write_preparation(run, output):
    output = Path(output)
    _write(output / "frozen-input.json", run["frozen"])
    _write(output / "selection-schema.json", run["frozen"]["schema"])
    _write(output / "factual-audit.json", run["factual_audit"])
    for name, key in zip(VARIANTS, ("payload", "context_on_payload")):
        _write(output / f"{name}-payload.json", run["frozen"][key])
        (output / f"{name}-prompt.txt").write_text(run["prompts"][name], encoding="utf-8")
    _write(output / "run.json", run)


def execute_run(run, config, output, *, allow_hosted=False, maximum=0, frozen_outputs=None):
    """Run exactly these prompts, or validate supplied outputs, with no retries."""
    from pipeline.lab import music
    _budget(allow_hosted, maximum)
    output = Path(output)
    saved = json.loads((output / "run.json").read_text(encoding="utf-8")) if (output / "run.json").exists() else None
    if saved is not None and (saved.get("status") != "dry-run"
            or saved.get("frozen_input_sha256") != run["frozen_input_sha256"]):
        raise ValueError("This pair has already executed or differs from the saved comparison; use a new comparison directory")
    if run["status"] != "dry-run" or any((output / f"{name}-{suffix}.json").exists()
            for name in VARIANTS for suffix in ("receipt", "output")):
        raise ValueError("This pair has already executed; use a new comparison directory")
    if frozen_outputs is not None and (not isinstance(frozen_outputs, dict)
            or set(frozen_outputs) != set(VARIANTS) or allow_hosted):
        raise ValueError("Frozen outputs must contain both variants and cannot enable hosted calls")
    frozen = _frozen(run["frozen"])
    if digest(frozen) != run["frozen_input_sha256"]:
        raise ValueError("The prepared frozen inputs changed before execution")
    for name in VARIANTS:
        if digest(run["prompts"][name]) != run["input_hashes"][name]["prompt_sha256"]:
            raise ValueError("The prepared prompt changed before execution")
    write_preparation(run, output)
    reports, selected = [], {}
    run.update(status="running", measurements=reports, hosted_allowed=allow_hosted, hosted_limit=maximum)
    with hosted_budget(allow_hosted=allow_hosted, maximum=maximum) as calls:
        try:
            for name in VARIANTS:
                started = time.perf_counter()
                report = {"variant": name, "status": "running", **run["input_hashes"][name],
                          "schema_sha256": run["schema_sha256"], "output_origin": "frozen" if frozen_outputs is not None else "hosted"}
                reports.append(report)
                call_start = len(calls)
                try:
                    raw = (deepcopy(frozen_outputs[name]) if frozen_outputs is not None else
                           music._hosted_json(config, run["prompts"][name], deepcopy(frozen["schema"]),
                               receipt_path=output / f"{name}-receipt.json", operation="context-comparison"))
                    report["output_sha256"] = digest(raw)
                    _write(output / f"{name}-output.json", raw)
                    choices = selection_choices(raw, frozen["offers"], frozen["sources"], frozen["timing_scope"],
                                                inspection=bool(frozen["payload"].get("footage_inspection")))
                    selected[name] = choices
                    _write(output / f"{name}-choices.json", choices)
                    _write(output / f"{name}-played.json", played_material(frozen, choices))
                    report.update(status="completed", choices_sha256=digest(choices),
                                  selected_count=sum(row["candidate_id"] is not None for row in choices),
                                  gap_slots=[row["slot"] for row in choices if row["candidate_id"] is None])
                except Exception as error:
                    report.update(status="failed", error=str(error))
                    raise
                finally:
                    report.update(seconds=time.perf_counter() - started, hosted_calls=deepcopy(calls[call_start:]))
                    receipt = output / f"{name}-receipt.json"
                    if receipt.is_file():
                        report.update(receipt_file=receipt.name, receipt_file_sha256=hashlib.sha256(receipt.read_bytes()).hexdigest())
            run.update(status="completed", changed_slots=_changes(selected[VARIANTS[0]], selected[VARIANTS[1]]))
        except Exception as error:
            run.update(status="failed", error=str(error))
            raise
        finally:
            run.update(hosted_calls=deepcopy(calls), frozen_input_unchanged=digest(frozen) == run["frozen_input_sha256"])
            _write(output / "run.json", run)
    return run


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="Frozen payloads, schema, source offers and timing authority")
    parser.add_argument("--out", type=Path, required=True, help="New private artifact directory")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--allow-hosted", action="store_true")
    parser.add_argument("--max-hosted-calls", type=int, default=0, help="Shared ceiling for the pair, at most two")
    parser.add_argument("--outputs", type=Path, help="Replay JSON with context-off/context-on responses; requires --execute")
    parser.add_argument("--config", type=Path)
    args = parser.parse_args(argv)
    try:
        _budget(args.allow_hosted, args.max_hosted_calls)
    except ValueError as error:
        parser.error(str(error))
    if (args.allow_hosted or args.outputs) and not args.execute:
        parser.error("Hosted calls or frozen-output replay require --execute")
    if args.outputs and args.allow_hosted:
        parser.error("Frozen-output replay cannot enable hosted calls")
    value = json.loads(args.input.read_text(encoding="utf-8"))
    run = prepare_run(value)
    args.out.mkdir(parents=True, exist_ok=False)
    run.update(source_file=str(args.input.resolve()), source_file_sha256=hashlib.sha256(args.input.read_bytes()).hexdigest(),
               hosted_allowed=args.allow_hosted, hosted_limit=args.max_hosted_calls)
    write_preparation(run, args.out)
    if args.execute:
        config = None
        if args.allow_hosted:
            from dotenv import load_dotenv
            from pipeline.config import load_config
            load_dotenv()
            config = load_config(args.config)
        responses = json.loads(args.outputs.read_text(encoding="utf-8")) if args.outputs else None
        execute_run(run, config, args.out, allow_hosted=args.allow_hosted,
                    maximum=args.max_hosted_calls, frozen_outputs=responses)
    print(json.dumps({"status": run["status"], "run": str((args.out / "run.json").resolve())}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
