"""Durable local editor and ingestion workers: python -m pipeline.lab.worker."""

from __future__ import annotations

import argparse
import os
import signal
import sys
import threading
import time
from pathlib import Path

from pipeline.lab.job_roles import WORKER_ROLES
from pipeline.lab.worker_runtime import RELOAD_EXIT_CODE, source_changed, source_fingerprint, supervise, supervise_roles, watch_parent


def execute_job(job, config, db, store, *, ingest_runner=None, role="all"):
    from filelock import Timeout
    from pipeline.ingest.locks import global_ingest_lock
    from pipeline.lab.media import JobCancelled, render_reel, validate_sources
    from pipeline.lab.resources import editorial_lock, editor_config
    from pipeline.lab.index_snapshot import acquire_editor_snapshot, publication_read

    canonical_db = db

    def cancelled():
        return store.is_cancelled(job["id"])

    def progress(message):
        if cancelled():
            raise JobCancelled("Job cancelled")
        store.progress(job["id"], message)

    try:
        if role == "editor":
            from pipeline.lab.job_roles import role_for_kind
            if role_for_kind(job["kind"]) != "editor":
                raise ValueError("This job requires the ingestion/GPU worker")
            config = editor_config(config)
        progress(f"Starting {job['kind']}")
        if role == "editor" and job["kind"] in {"generate", "plan", "draft", "next-scene"}:
            db = acquire_editor_snapshot(config, db, progress, cancelled)
        if job["kind"] == "prepare-search-features":
            from pipeline.index.search_features import prepare_batch
            from pipeline.index.search_storage import SearchStorageFull
            previous = job.get("result") or {}
            try:
                batch = prepare_batch(config, db, job["snapshot"]["search_features"],
                                      previous.get("cursor", ""), cancelled=cancelled)
            except (SearchStorageFull, Timeout) as exc:
                return store.continue_search_features(job["id"], previous, error=str(exc))
            result = {**batch, "written": previous.get("written", 0) + batch["written"],
                      "composition_written": previous.get("composition_written", 0) + batch.get("composition_written", 0),
                      "processed": previous.get("processed", 0) + batch.get("processed", 0)}
            progress(f"Prepared search features for {result['processed']} frames")
            if batch["done"]:
                return store.finish(job["id"], result=result)
            return store.continue_search_features(job["id"], result)
        if job["kind"] == "fit-search-composition":
            from pipeline.index.composition_build import fit_profiles
            from pipeline.index.search_features import preparation_request
            from pipeline.index.writer import published_film_ids
            profiles = fit_profiles(config, db, progress=progress, cancelled=cancelled)
            queued = []
            for profile in profiles:
                for film_id in sorted(published_film_ids(db)):
                    progress(f"Queuing compact features for {film_id}")
                    request = preparation_request(config, db, film_id, composition_profile=profile.profile_id)
                    queued.append(store.enqueue_search_features(request)["id"])
            return store.finish(job["id"], result={"profiles": [profile.profile_id for profile in profiles],
                                                   "preparation_jobs": queued, "promoted": False})
        if job["kind"] == "backfill-temporal":
            from pipeline.ingest.backfill_temporal import backfill_temporal
            from pipeline.ingest.shots import SHORT_SHOT_SAMPLING_PROFILE
            options = job["snapshot"]["temporal_backfill"]
            if options["sampling_profile"] != SHORT_SHOT_SAMPLING_PROFILE:
                raise ValueError("Queued sampling profile changed; inspect and submit a fresh backfill plan")

            def evidence_progress(value):
                if isinstance(value, dict):
                    stage = str(value.get("stage", "Refreshing shot evidence"))
                    detail = f" {value['completed']}/{value['total']}" if "completed" in value and "total" in value else ""
                    progress(stage + detail)
                else:
                    progress(value)

            result = backfill_temporal(config, film_id=options["film_id"], unit_ids=options["unit_ids"],
                                       batch_size=options["batch_size"], progress=evidence_progress, cancelled=cancelled)
            if result.get("cancelled"):
                raise JobCancelled("Evidence refresh cancelled")
            if result.get("semantic_text", {}).get("status") == "deferred":
                detail = result["semantic_text"].get("error", "semantic text is unavailable")
                return store.finish(job["id"], result=result, error=(
                    f"Shot evidence is preserved, but semantic text refresh was deferred: {detail}. "
                    f"Run `uv run python -m pipeline.cli index-text --film-id {options['film_id']}` "
                    "or retry the exact --unit-id selection."
                ))
            return store.finish(job["id"], result=result)
        if job["kind"] == "ingest":
            if ingest_runner is None:
                from pipeline.api.main import _run_ingest_subprocess
                ingest_runner = _run_ingest_subprocess
            # The CLI process still owns its existing global GPU/ingest lock.
            ingest_runner(Path(job["snapshot"]["path"]), progress)
            return store.finish(job["id"])
        if job["kind"] == "render":
            result = render_reel(job, config, db, store, progress, cancelled)
            return store.finish(job["id"], result=result)
        if job["kind"] == "transition-render":
            from pipeline.transitions.jobs import run
            result = run(job, config, db, store, progress, cancelled)
            return store.finish(job["id"], result=result)
        if job["kind"] == "algmods-render":
            from pipeline.algmods.jobs import run
            result = run(job, config, db, store, progress, cancelled)
            return store.finish(job["id"], result=result)
        if job["kind"] == "transition-bridge":
            from pipeline.transitions.bridges import run
            result = run(job, config, db, store, progress, cancelled)
            return store.finish(job["id"], result=result)
        if job["kind"] == "transition-generate":
            from pipeline.transitions.generation import run
            result = run(job, config, db, store, progress, cancelled)
            return store.finish(job["id"], result=result)
        if job["kind"] == "match-preview":
            from pipeline.lab.matching import preview_run
            result = preview_run(job, config, db, store, progress, cancelled)
            return store.finish(job["id"], result=result)
        if job["kind"] == "match":
            from pipeline.lab.matching import run
            with global_ingest_lock(config.paths.assets_dir):
                result = run(job, config, db, store, progress, cancelled)
            return store.finish(job["id"], result=result)
        if job["kind"] in {"match-search", "match-search-preview"}:
            from pipeline.matching.jobs import run, preview_run
            if job["kind"] == "match-search":
                with global_ingest_lock(config.paths.assets_dir):
                    result = run(job, config, db, store, progress, cancelled)
            else:
                result = preview_run(job, config, db, store, progress, cancelled)
            return store.finish(job["id"], result=result)
        if job["kind"] == "next-scene":
            from pipeline.lab.next_scene import run
            with editorial_lock(config, role):
                result = run(job, config, db, store, progress, cancelled)
            return store.finish(job["id"], result=result)
        if job["kind"] == "next-scene-preview":
            from pipeline.lab.next_scene import adjusted_candidate, proposal_document
            from pipeline.lab.next_scene_media import render_preview
            source = job["snapshot"]["next_scene_preview"]
            candidate = adjusted_candidate(source["document"], source["scope"], source["candidate"], db, source["adjustments"])
            document = proposal_document(source["document"], source["scope"], source["candidate"], db, source["adjustments"])
            rendered = render_preview(f"{job['id']}-{candidate['id']}", document, source["scope"]["anchor_slot_id"], config, db, store, progress, cancelled)
            candidate = {**candidate, **rendered, "preview_ready": True,
                         "preview_url": f"/lab/jobs/{job['id']}/next-scenes/{candidate['id']}/preview"}
            return store.finish(job["id"], result={"next_scene_job_id": source["next_scene_job_id"], "candidate": candidate})
        if job["kind"] == "plan":
            from pipeline.lab.direction_planner import run_direction_job
            # Text-only planning needs neither source playback nor the GPU. The
            # exact slots and source selections are copied unchanged and guarded
            # again by the transactional revision/lock checks in finish().
            document = run_direction_job(job, config, db, progress)
            return store.finish(job["id"], document=document)
        from pipeline.lab.music import run_music_job
        # The CPU editor can overlap ingestion. Legacy all-in-one execution
        # retains its GPU gate; hosted listening/planning never holds an index lock.
        try:
            with editorial_lock(config, role):
                result = {}
                if job["kind"] == "generate":
                    from pipeline.lab.generation import run_generate_job
                    proposal, result = run_generate_job(job, config, db, progress)
                    if proposal is None:
                        progress(result.get("message", "The current edit is unchanged"))
                        return store.finish(job["id"], result=result)
                else:
                    proposal = run_music_job(job, config, db, progress)
                    if job["kind"] == "rhythm":
                        rhythm = proposal.get("rhythm") or {}
                        count = len(rhythm.get("beats", []))
                        result = {"beat_count": count, "placeholder_count": len(proposal["music_timeline"]["slots"]),
                                  "timing_contract": (rhythm.get("timing_suggestions") or {}).get("contract"),
                                  "message": (f"{count} beat guides ready. Generate edit plans pacing from the music."
                                              if count and proposal["music_timeline"].get("provisional_timing") else
                                              f"{count} beat guides ready. Your existing cuts stay in place."
                                              if count else "Beat detection is unavailable. Generate edit can still plan from listening, or add cuts manually; no beat guides were invented.")}
                    elif job.get("snapshot", {}).get("suggest_only"):
                        selected_id = job["snapshot"]["slot_ids"][0]
                        slot = next(item for item in proposal["music_timeline"]["slots"] if item["id"] == selected_id)
                        count = len(slot["alternatives"])
                        result = {"suggest_only": True, "requested_slot_ids": [selected_id], "candidate_count": count,
                                  "message": (f"{count} scene options ready. Choose Use scene to place one. Your current edit is unchanged."
                                              if count else "No fitting scenes found. Edit the search or shorten the slot; your current scene is kept.")}
        except Timeout as exc:
            raise RuntimeError("Another ingestion process owns the shared resources; retry this job after it finishes") from exc
        document = proposal.get("document", proposal)
        # Retained, unplaced selections are durable user state. Validate every
        # new/changed source and every placed source, without requiring an old
        # unused source to be present in today's derived index.
        original = {(clip["film_id"], clip["source_start"], clip["source_end"])
                    for clip in job["document"]["clips"]}
        timeline = document.get("music_timeline")
        validate_ids = ({slot["clip_id"] for slot in timeline["slots"] if slot.get("clip_id")} |
                        {clip["id"] for clip in document["clips"]
                         if (clip["film_id"], clip["source_start"], clip["source_end"]) not in original}) if timeline else None
        with publication_read(canonical_db):
            validate_sources(document, canonical_db, store, clip_ids=validate_ids)
        progress("Saving the completed edit")
        return store.finish(job["id"], result=result, document=document)
    except JobCancelled:
        store.cancel(job["id"])
        return store.finish(job["id"], error="Job cancelled")
    except Exception as exc:
        return store.finish(job["id"], error=str(exc) or type(exc).__name__,
                            **({"result": job.get("result")} if job["kind"] == "prepare-search-features" else {}))


def run_worker(config, *, once=False, stop=None, db=None, reload_fingerprint=None, role="all"):
    from pipeline.lab.store import LabStore
    from pipeline.lab.worker_control import WorkerControl
    from pipeline.lab.worker_locks import worker_lock

    store = LabStore(config.paths.state_dir, config.paths.assets_dir)
    store.initialize()
    stop = stop or threading.Event()
    reload_requested = False
    with worker_lock(store.root, role), WorkerControl(store, role, stop) as control:
        interrupted = store.recover_interrupted(role)
        if interrupted:
            print(f"Marked {interrupted} abandoned job(s) interrupted; no automatic replay.", flush=True)
        from pipeline.index.writer import open_db
        db = db if db is not None else open_db(config)
        version = f", source {reload_fingerprint[:12]}" if reload_fingerprint else ""
        print(f"[{role}] Lab worker ready: {store.path} (PID {os.getpid()}{version})", flush=True)
        warmup = None
        if role == "editor" and not once and os.environ.get("SCENE_RECALL_SKIP_WARMUP") != "1":
            from pipeline.lab.search_warmup import EditorSearchWarmup
            warmup = EditorSearchWarmup()

        def warmup_should_yield():
            if stop.is_set() or control.check_stop():
                return True
            if reload_fingerprint is not None and source_changed(reload_fingerprint):
                return True
            from pipeline.lab.job_roles import kinds_for_role
            kinds = kinds_for_role("editor")
            with store.connection() as con:
                return con.execute(
                    f"SELECT 1 FROM jobs WHERE kind IN ({','.join('?' for _ in kinds)}) "
                    "AND status='queued' AND cancel_requested=0 LIMIT 1", kinds,
                ).fetchone() is not None

        next_cleanup = 0.0
        while not stop.is_set():
            if control.check_stop():
                break
            changed = reload_fingerprint is not None and source_changed(reload_fingerprint)
            if control.check_stop():
                break  # A requested drain wins over reload and another claim.
            if changed:
                reload_requested = True
                break
            job = store.claim(role, owner_token=control.token)
            if job is not None:
                control.set_job(job["id"])
                try:
                    execute_job(job, config, db, store, role=role)
                finally:
                    control.set_job(None)
            if once:
                break
            if job is None:
                if role in {"editor", "all"} and time.monotonic() >= next_cleanup:
                    from pipeline.lab.cleanup import maintain_storage
                    maintain_storage(store)
                    next_cleanup = time.monotonic() + 300
                if warmup is not None:
                    warmup.step(config, db, warmup_should_yield)
                stop.wait(0.5)
    if stop.is_set():
        return 0
    if reload_requested:
        print(f"[{role}] Python source changed; restarting before claiming another job.", flush=True)
        return RELOAD_EXIT_CODE


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--role", choices=(*WORKER_ROLES, "all"), help="Run one lane; default starts editor and ingest. 'all' is serial compatibility mode")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="Process at most one queued job")
    mode.add_argument("--reload", action="store_true", help="Restart with fresh Python code between jobs (local development)")
    mode.add_argument("--reload-child", action="store_true", help=argparse.SUPPRESS)
    mode.add_argument("--status", action="store_true", help="Show worker activity and queued jobs")
    mode.add_argument("--stop", action="store_true", help="Stop the selected workers after their active jobs finish")
    parser.add_argument("--worker-child", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    if args.config:
        # CLI ingest children inherit the identical config, including relative paths.
        os.environ["CINEMA_CONFIG"] = str(args.config.resolve())
    if args.worker_child and (args.once or args.reload or args.reload_child or args.status or args.stop):
        parser.error("An internal worker child cannot be combined with another mode")
    if not (args.once or args.status or args.stop or args.reload_child or args.worker_child):
        def command(role):
            value = [sys.executable, "-m", "pipeline.lab.worker", "--role", role,
                     "--reload-child" if args.reload else "--worker-child"]
            if args.config:
                value += ["--config", str(args.config.resolve())]
            return value

        if args.role is None:
            return supervise_roles({role: command(role) for role in WORKER_ROLES}, stop, reload=args.reload)
        if args.reload:
            return supervise(command(args.role), stop, role=args.role)

    # Capture before importing configuration, schemas or any job implementation.
    # The reload launcher itself imports only stdlib and the small runtime helper.
    fingerprint = source_fingerprint() if args.reload_child else None
    if args.reload_child or args.worker_child:
        watch_parent(stop)
    role = args.role or "all"
    if role == "editor" and not (args.status or args.stop):
        from pipeline.lab.resources import configure_editor_process
        configure_editor_process()
    from dotenv import load_dotenv
    from filelock import Timeout
    from pipeline.config import load_config

    load_dotenv()
    config = load_config(args.config)
    if args.status or args.stop:
        from pipeline.lab.store import LabStore
        from pipeline.lab.worker_control import format_worker_status, request_worker_stop, worker_status
        store = LabStore(config.paths.state_dir)
        if args.stop:
            request_worker_stop(store, role=args.role)
        status = worker_status(store)
        if args.role in WORKER_ROLES:
            status["workers"] = [row for row in status["workers"] if row["role"] == args.role]
        print(format_worker_status(status), flush=True)
        return 0
    try:
        return run_worker(config, once=args.once, stop=stop, reload_fingerprint=fingerprint, role=role) or 0
    except Timeout:
        parser.exit(1, f"Another {role} or legacy Lab worker already owns this state directory.\n")


if __name__ == "__main__":
    raise SystemExit(main())
