"""Private, bounded discovery/assembly orchestration; no production job writes."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import time

from pipeline.experiments.music_edit import digest, document_changes, document_metrics, frozen_document, hosted_budget, _write

CONTRACT = "frozen-discovery-assembly-comparison-v1"
VARIANTS = ["fixed-slots", "joint-assembly", "expanded-discovery"]
MAX_HOSTED_CALLS = 4


def prepare_run(value):
    document = frozen_document(value)
    from pipeline.lab.generation import _analysis_is_current
    from pipeline.experiments.assembly_sequence import validate_assembly_input
    if not _analysis_is_current(document):
        raise ValueError("Assembly requires valid frozen listening evidence; this runner never listens")
    document = validate_assembly_input(document, require_timeline=True)
    return {"contract": CONTRACT, "status": "dry-run", "stage": "assembly",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "source_input_sha256": digest(value), "frozen_document_sha256": digest(document),
            "source_project_id": value.get("id") if "document" in value else None,
            "source_revision": value.get("revision") if "document" in value else None,
            "variants": list(VARIANTS), "metrics_before": document_metrics(document),
            "human_preference": None, "document": document,
            "limits": {"hosted_calls": 4, "initial_recipes": 6, "followup_recipes": 2,
                       "results_per_recipe": 48, "initial_catalog": 48, "expanded_catalog": 72,
                       "max_shots": 64, "max_passage_seconds": 45},
            "evaluation_note": "Fixed slots are a controlled replay with the shared pool, not the historical render. "
                               "Catalog coverage, model reasons and technical validity are not creative quality judgments."}


class FrozenTracks:
    """Expose only the registered music needed by the existing renderer."""

    def __init__(self, config, document):
        identity = document["track"]["id"]
        database = (config.paths.state_dir / "lab" / "lab.sqlite3").resolve()
        with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute("SELECT * FROM tracks WHERE id=?", (identity,)).fetchone()
        if row is None or not Path(row["path"]).is_file():
            raise ValueError("The frozen imported music is unavailable; no hosted requests were sent")
        if abs(row["duration"] - document["track"]["duration"]) > .001:
            raise ValueError("The frozen imported music duration has changed")
        with Path(row["path"]).open("rb") as source:
            if hashlib.file_digest(source, "sha256").hexdigest() != identity:
                raise ValueError("The imported music bytes changed; restore the frozen source before comparison")
        self.track = dict(row)

    def get_track(self, identity):
        if identity != self.track["id"]:
            raise ValueError("The renderer requested music outside the frozen comparison")
        return deepcopy(self.track)


def render_variant(document, config, db, tracks, output, variant, progress):
    from pipeline.lab.media import render_manifest, render_from_manifest
    # Keep derived media inside this new run directory, never beside production jobs.
    private_config = replace(config, paths=replace(config.paths, assets_dir=output / "media"))
    manifest = render_manifest(document, db, tracks, mode="preview")
    _write(output / f"{variant}-render-manifest.json", manifest)
    render_from_manifest(variant, manifest, private_config, db, tracks, progress)
    return str(output / "media" / "lab" / "renders" / variant / "output.mp4")


def query_summary(state):
    """Offer query coverage, not selectable identities outside the catalog."""
    result = []
    catalog = set(state["catalog"])
    for query in state["queries"]:
        rows = query["rows"]
        eligible = {row["unit_id"] for row in rows if row["eligible"]}
        exclusions = {}
        for row in rows:
            if not row["eligible"]:
                reason = row.get("exclusion") or "unavailable"
                exclusions[reason] = exclusions.get(reason, 0) + 1
        result.append({"id": query["id"], **deepcopy(query["recipe"]), "returned_count": len(rows),
                       "eligible_unique_count": len(eligible), "catalog_count": len(eligible & catalog),
                       "retained_outside_catalog_count": len(eligible - catalog), "exclusions": exclusions,
                       "error": query.get("error"),
                       "limit_note": "Only the bounded retrieved prefix was inspected; counts do not establish library absence."})
    return result


def execute_run(run, value, config, db, output, *, allow_hosted=False, maximum=0):
    from pipeline.experiments import assembly_discovery as discovery, assembly_sequence as sequence
    from pipeline.lab import music
    from pipeline.search.capabilities import search_capabilities

    if isinstance(maximum, bool) or not isinstance(maximum, int) or not 0 <= maximum <= MAX_HOSTED_CALLS:
        raise ValueError("Assembly permits at most four hosted calls")
    prepare_run(value)  # Revalidate before any service work or paid request.
    controls = {"footage_inspection": False}
    if hasattr(config.lab, "context_profile"):
        controls["context_profile"] = None
    config = replace(config, lab=replace(config.lab, **controls))
    document = deepcopy(run["document"])
    original_digest = digest(document)
    output = Path(output).resolve()
    reports, events = [], []
    run.update(status="running", measurements=reports, progress=events,
               experiment_runtime_controls=controls,
               configuration_sha256=digest(json.loads(json.dumps(asdict(config), default=str))),
               model={"provider": config.lab.music_provider, "model": config.lab.planner_model},
               snapshot_versions=deepcopy(getattr(db, "versions", {})))

    def progress(stage, message):
        event = {"stage": stage, "message": str(message), "at": datetime.now(timezone.utc).isoformat()}
        events.append(event)
        _write(output / "progress.json", event)
        run["active_stage"] = stage
        run["hosted_calls"] = deepcopy(calls)
        _write(output / "run.json", run)
        print(f"[{stage}] {message}", flush=True)

    def request(stage, payload, prompt, schema):
        _write(output / f"{stage}-payload.json", payload)
        _write(output / f"{stage}-schema.json", schema)
        (output / f"{stage}-prompt.txt").write_text(prompt, encoding="utf-8")
        response = music._hosted_json(config, prompt, schema, receipt_path=output / f"{stage}-receipt.json",
                                     progress=lambda message: progress(stage, message),
                                     operation="plan" if stage == "discovery" else "draft")
        _write(output / f"{stage}-response.json", response)
        return response

    def record_arm(variant, proposal, calls):
        started, call_start = time.perf_counter(), len(calls)
        report = {"variant": variant, "status": "running"}
        reports.append(report)
        try:
            result, diagnostics = proposal()
            _write(output / f"{variant}-document.json", result)
            _write(output / f"{variant}-diagnostics.json", diagnostics)
            report.update(status="assembled", diagnostics=diagnostics, metrics=document_metrics(result),
                          changes_from_frozen=document_changes(document, result), document_sha256=digest(result))
            report["render"] = render_variant(result, config, db, tracks, output, variant,
                                              lambda message: progress(variant, message))
            report["status"] = "completed"
            return result, diagnostics
        except KeyboardInterrupt:
            report.update(status="cancelled", error="Interrupted by operator")
            raise
        except Exception as error:
            report.update(status="render-failed" if report["status"] == "assembled" else "failed",
                          error=str(error), error_type=type(error).__name__)
            progress(variant, str(error))
            # Retain valid documents/receipts, but do not call again to repair an arm.
            return None
        finally:
            report.update(seconds=time.perf_counter() - started, hosted_calls=deepcopy(calls[call_start:]))
            run["hosted_calls"] = deepcopy(calls)
            _write(output / "run.json", run)

    with hosted_budget(allow_hosted=allow_hosted, maximum=maximum) as calls:
        run["hosted_calls"] = calls
        try:
            tracks = FrozenTracks(config, document)
            capabilities = search_capabilities(config, db)
            _write(output / "capabilities.json", capabilities)
            payload = discovery.discovery_payload(document, capabilities)
            response = request("discovery", payload, discovery.discovery_prompt(payload), discovery.discovery_schema())
            intent = discovery.validate_discovery_intent(response, document, capabilities)
            _write(output / "discovery-intent.json", intent)
            state = discovery.discover_candidates(intent, document, config, db, capabilities=capabilities,
                                                  progress=lambda message: progress("discovery", message),
                                                  checkpoint=lambda state: _write(output / "initial-ledger.json", state))
            _write(output / "initial-ledger.json", state)
            _write(output / "initial-catalog.json", state["catalog"])
            catalog, regions = state["catalog"], intent["intentions"]
            if not catalog:
                raise ValueError("Bounded discovery produced no eligible footage; this does not establish library absence")

            def baseline():
                bundle = sequence.fixed_baseline_payload(document, catalog, capabilities, regions=regions)
                _write(output / "fixed-slots-offers.json", bundle["manifest"])
                response = request("fixed-slots", bundle["payload"], bundle["prompt"], bundle["schema"])
                return sequence.fixed_baseline_document(document, response, catalog, bundle)

            record_arm("fixed-slots", baseline, calls)

            def joint(catalog, state, *, expanded=False, previous=None):
                stage = "expanded-discovery" if expanded else "joint-assembly"
                payload = sequence.build_assembly_payload(document, catalog, capabilities, regions=regions,
                          discovery_enabled=not expanded, previous_draft=previous, candidate_limit=72 if expanded else 48)
                payload["discovery_queries"] = query_summary(state)
                response = request(stage, payload, sequence.build_assembly_prompt(payload), sequence.assembly_schema(payload))
                return sequence.assemble_document(document, response, catalog, capabilities=capabilities,
                           discovery_enabled=not expanded, candidate_limit=72 if expanded else 48)

            initial = record_arm("joint-assembly", lambda: joint(catalog, state), calls)
            if initial is None:
                reports.append({"variant": "expanded-discovery", "status": "skipped", "reason": "No valid initial assembly"})
            else:
                result, diagnostics = initial
                needs = diagnostics.get("discovery_needs", [])
                if not needs:
                    reports.append({"variant": "expanded-discovery", "status": "skipped",
                                    "reason": "The assembler requested no further discovery; no extra call was made"})
                else:
                    try:
                        expanded = discovery.expand_candidates(state, needs, document, config, db, capabilities=capabilities,
                                     progress=lambda message: progress("expansion", message),
                                     checkpoint=lambda state: _write(output / "expanded-ledger.json", state))
                        _write(output / "expanded-ledger.json", expanded)
                        _write(output / "expanded-catalog.json", expanded["catalog"])
                        if list(expanded["catalog"]) == list(catalog):
                            reports.append({"variant": "expanded-discovery", "status": "skipped",
                                            "reason": "The bounded follow-up found no additional eligible candidates",
                                            "discovery_needs": needs})
                        else:
                            previous = json.loads((output / "joint-assembly-response.json").read_text(encoding="utf-8"))
                            record_arm("expanded-discovery", lambda: joint(expanded["catalog"], expanded, expanded=True,
                                                                         previous=previous), calls)
                    except Exception as error:
                        reports.append({"variant": "expanded-discovery", "status": "failed", "error": str(error)})
            successes = sum(report["status"] == "completed" for report in reports)
            failures = sum(report["status"] in {"failed", "render-failed"} for report in reports)
            run["status"] = "partial" if successes and failures else "failed" if failures else "completed"
        except BaseException as error:
            run.update(status="cancelled" if isinstance(error, KeyboardInterrupt) else "failed", error=str(error))
            raise
        finally:
            run.update(hosted_calls=deepcopy(calls), frozen_input_unchanged=digest(document) == original_digest)
            _write(output / "run.json", run)
    return run
