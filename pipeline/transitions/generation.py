"""One explicitly authorized Runway task, its owned media, and a seam audition.

An existing execution/submission receipt is never replayed. Interrupted or
uncertain tasks require reconciliation, not a new automatic paid submission.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import time
import uuid

from pydantic import Field, model_validator

from pipeline.lab.media import JobCancelled, _check_render_directory, _check_render_file, run_process
from pipeline.transitions import bridges, jobs, providers
from pipeline.transitions.contracts import FPS, frame_count
from pipeline.transitions.storage import Guard

GENERATION_VERSION = "runway-transition-generation-v1"
POLL_SECONDS = 5
MAX_TASK_SECONDS = 20 * 60


class GenerateRequest(providers.QuoteRequest):
    request_id: str
    parent_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    seed: int | None = Field(default=None, ge=0, le=4294967295, strict=True)
    max_credits: int = Field(ge=1, le=300, strict=True)
    price_version: str
    confirm_spend: bool = Field(strict=True)

    @model_validator(mode="after")
    def authorize(self):
        if str(uuid.UUID(self.request_id)) != self.request_id:
            raise ValueError("Invalid generation request identity")
        if not self.confirm_spend:
            raise ValueError("Explicitly confirm the quoted generation spend")
        self.seed = providers.seed_value(self.model, self.seed)
        if self.price_version != providers.model_spec(self.model)["price_version"]:
            raise ValueError("The generation price changed; review a fresh quote")
        providers.RunwayRequest(**{key: getattr(self, key) for key in
                                   ("model", "prompt", "duration", "resolution", "seed", "max_credits", "prompt_expansion_mode")})
        return self


def _read(path):
    _check_render_file(path)
    return json.loads(path.read_text(encoding="utf-8"))


def _owned(config, proposal):
    root = jobs.output_root(config, proposal["job_id"])
    _check_render_directory(root)
    saved = _read(root / "preparation.json")
    if (jobs.sha256(root / "preparation.json") != proposal["preparation_sha256"]
            or saved != {key: value for key, value in proposal.items() if key != "preparation_sha256"}
            or proposal["request"]["request_id"] != proposal["job_id"]):
        raise ValueError("The frozen generation preparation changed or belongs to another job")
    for name, digest in proposal["preparation"]["artifacts"].items():
        if name not in {"frame-a.jpg", "frame-b.jpg", "provider-a.jpg", "provider-b.jpg", "parent-manifest.json"}:
            raise ValueError("Unexpected generation input artifact")
        _check_render_file(root / name)
        if jobs.sha256(root / name) != digest:
            raise ValueError("A frozen generation endpoint or parent receipt changed")
    return root


def _verify(config, proposal, db):
    bridges._check_source_timing(proposal["source_snapshot"]["request"])
    root = _owned(config, proposal)
    current = jobs.freeze(proposal["source_snapshot"]["request"], db)
    model = proposal["request"].get("model", providers.DEFAULT_MODEL)
    expected_provider = {"provider": "runway", "model": model, "api_version": providers.API_VERSION}
    ratio = providers.input_ratio(model, proposal["request"]["resolution"], proposal["source_snapshot"]["request"]["output"]["aspect"])
    if (current != proposal["source_snapshot"]
            or proposal["preparation"]["bridge_version"] != bridges.BRIDGE_VERSION
            or proposal["preparation"]["provider"] != expected_provider
            or proposal["preparation"]["provider_ratio"] != ratio):
        raise ValueError("The generation source pair or normalization/assembly version changed; review a fresh quote")
    return root


def prepare(body, config, db, store):
    """Prepare endpoint files locally; this function never contacts a provider."""
    request = GenerateRequest.model_validate(body).model_dump(mode="json")
    if not os.environ.get(providers.KEY_ENV, "").strip():
        raise ValueError(f"Configure {providers.KEY_ENV} on the server before generation")
    root = jobs.output_root(config, request["request_id"])
    _check_render_directory(root)
    if root.exists():
        if not (root / "preparation.json").exists():
            raise ValueError("This request has an incomplete preparation; reconcile it before submitting another request")
        proposal = _read(root / "preparation.json")
        proposal["preparation_sha256"] = jobs.sha256(root / "preparation.json")
        if providers.request_identity(proposal["request"]) != providers.request_identity(request):
            raise ValueError("This request identity already belongs to a different generation")
        _verify(config, proposal, db)
        return proposal
    parent = store.get_job(request["parent_render_id"], private=True)
    source, manifest = bridges._check_parent(parent, config, db)
    if request["parent_manifest_sha256"] != parent["result"]["manifest_sha256"]:
        raise ValueError("The parent receipt differs from the reviewed quote")
    aspect = source["request"]["output"]["aspect"]
    width, height = map(int, providers.input_ratio(request["model"], request["resolution"], aspect).split(":"))
    root.mkdir(parents=True, exist_ok=False)
    names = ("frame-a.jpg", "frame-b.jpg", "provider-a.jpg", "provider-b.jpg", "parent-manifest.json",
             "preparation.json", "preparation.json.partial")
    try:
        normalization = []
        for index, side in enumerate(("a", "b")):
            (root / f"frame-{side}.jpg").write_bytes(jobs.artifact(config, parent, side).read_bytes())
            clip = source["request"][("outgoing", "incoming")[index]]
            path = Path(source["sources"][index]["path"])
            stream = next(row for row in bridges._probe(path)["streams"] if row["codec_type"] == "video")
            spatial, record = jobs.spatial_color_filter(width, height, clip.get("framing"), stream)
            duration = clip["source_end"] - clip["source_start"]
            count = frame_count(duration)
            selected = count - 1 if index == 0 else 0
            vf = bridges._filter(spatial, duration, count) + f",select=eq(n\\,{selected})"
            run_process(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                         "-protocol_whitelist", "file,pipe", "-ss", str(clip["source_start"]), "-i", str(path),
                         "-map", "0:v:0", "-an", "-vf", vf, "-frames:v", "1", "-q:v", "2",
                         "-threads", "2", str(root / f"provider-{side}.jpg")], timeout=120)
            normalization.append({**record, "source_start": clip["source_start"], "selected_frame": selected,
                                  "fps": FPS, "width": width, "height": height,
                                  "policy": "same-30fps-source-segment-framing-as-assembly-at-provider-resolution-v1"})
        (root / "parent-manifest.json").write_bytes(jobs.artifact(config, parent, "manifest").read_bytes())
        if jobs.sha256(root / "parent-manifest.json") != request["parent_manifest_sha256"]:
            raise ValueError("The source receipt changed while preparing generation")
        if jobs.freeze(source["request"], db)["sources"] != source["sources"]:
            raise ValueError("The source pair changed while preparing generation")
        proposal = {"job_id": request["request_id"], "generation_version": GENERATION_VERSION,
                    "request": request, "source_snapshot": source,
                    "parent_manifest_sha256": request["parent_manifest_sha256"],
                    "preparation": {"artifacts": {name: jobs.sha256(root / name) for name in names[:5]},
                                    "bridge_version": bridges.BRIDGE_VERSION,
                                    "provider": {"provider": "runway", "model": request["model"], "api_version": providers.API_VERSION},
                                    "normalization": normalization, "source_frame_times": manifest["source_frame_times"],
                                    "provider_ratio": providers.input_ratio(request["model"], request["resolution"], aspect)}}
        providers._atomic(root / "preparation.json", proposal)
        return {**proposal, "preparation_sha256": jobs.sha256(root / "preparation.json")}
    except BaseException:
        _check_render_directory(root)
        for name in names:
            _check_render_file(root / name)
            (root / name).unlink(missing_ok=True)
        root.rmdir()
        raise


def _public_receipt(proposal, receipt):
    """An allowlist; signed URLs and arbitrary provider error bodies stay private."""
    result = {"schema_version": 1, "job_id": proposal["job_id"], **proposal["preparation"]["provider"],
              "generation_version": proposal["generation_version"], "request": proposal["request"],
              "preparation_sha256": proposal["preparation_sha256"],
              "state": receipt.get("state", "not_submitted"),
              "provenance_status": "provider-task-verified" if receipt.get("state") == "succeeded" else "pending-provider-verification"}
    for key in ("task_id", "api_version", "price_version", "created_at", "accepted_at", "last_polled_at",
                "request_sha256", "inputs", "cancellation_requested", "cancellation_confirmed", "cancellation_reason",
                "cancel_observed_status", "download_manifest_sha256"):
        if key in receipt:
            result[key] = receipt[key]
    task = receipt.get("task") or {}
    for source, dest in ((receipt.get("estimated_cost"), "estimated_credits"),
                         (task.get("cost"), "actual_credits")):
        if isinstance(source, dict) and isinstance(source.get("credits"), (int, float)) and providers.math_is_finite(source["credits"]):
            result[dest] = source["credits"]
    return result


def _submission(root, proposal=None):
    path = root / "provider-task.json"
    value = _read(path) if path.exists() else {}
    if value and proposal is not None:
        expected = {key: proposal["request"][key] for key in ("prompt", "duration", "resolution", "seed", "max_credits")}
        expected["model"] = proposal["request"].get("model", providers.DEFAULT_MODEL)
        expected["prompt_expansion_mode"] = proposal["request"].get("prompt_expansion_mode")
        expected["aspect"] = proposal["source_snapshot"]["request"]["output"]["aspect"]
        inputs = [{"position": position, "sha256": proposal["preparation"]["artifacts"][f"provider-{side}.jpg"]}
                  for side, position in (("a", "first"), ("b", "last"))]
        if (providers.request_identity(value.get("request", {})) != providers.request_identity(expected) or value.get("inputs") != inputs
                or any(value.get(key) != field for key, field in proposal["preparation"]["provider"].items())
                or value.get("price_version") != proposal["request"]["price_version"]):
            raise ValueError("The provider submission receipt does not belong to this generation")
    return value


def _save_public(root, proposal):
    providers._atomic(root / "provider-public.json", _public_receipt(proposal, _submission(root, proposal)))


def _cancel(client, root, proposal, reason):
    receipt = _submission(root, proposal)
    if not receipt.get("task_id"):
        return
    if receipt.get("cancellation_confirmed"):
        return
    if receipt.get("task", {}).get("status") in {"SUCCEEDED", "FAILED", "CANCELLED"}:
        return
    receipt.update(cancellation_requested=True, cancellation_reason=reason, cancellation_confirmed=False)
    providers._atomic(root / "provider-task.json", receipt)
    try:
        # Status retrievals, including the cancellation preflight, are spaced
        # by at least the provider's five-second polling interval.
        recent = float(receipt.get("last_polled_at") or receipt.get("accepted_at") or 0)
        remaining = POLL_SECONDS - (time.time() - recent)
        if remaining > 0:
            time.sleep(min(POLL_SECONDS, remaining))
        outcome = client.cancel(receipt["task_id"])
        receipt["cancellation_confirmed"] = bool(outcome.get("cancelled"))
        receipt["cancel_observed_status"] = outcome.get("status")
        if receipt["cancellation_confirmed"]:
            receipt["state"] = "cancelled"
    except Exception:
        # A failed cancellation is not proof that billing or generation stopped.
        receipt["cancellation_confirmed"] = False
    providers._atomic(root / "provider-task.json", receipt)
    _save_public(root, proposal)


def _wait(cancelled, deadline):
    next_poll = min(time.monotonic() + POLL_SECONDS, deadline)
    while time.monotonic() < next_poll:
        if cancelled():
            raise JobCancelled("AI bridge generation cancelled; inspect the provider receipt for remote cancellation")
        time.sleep(min(.25, max(0, next_poll - time.monotonic())))
    if time.monotonic() >= deadline:
        raise ValueError("Runway exceeded the twenty-minute task budget; cancellation will be attempted")


def _assembly(proposal, root):
    request = proposal["request"]
    media = bridges._media_info(root / "original.media")
    # A live five-second H3 Max request returned 124 frames at 24fps (5.1667s)
    # plus an AAC tail, for a 5.184s container. This narrow lab tolerance is
    # observed compatibility, not a promised hosted-model frame-grid contract.
    # Keep the complete original at its actual speed; larger mismatches remain
    # recoverable as originals for an explicitly trimmed/re-timed import.
    maximum_overrun = .25 if request.get("model", providers.DEFAULT_MODEL) == "h3_max" else .15
    difference = media["duration"] - request["duration"]
    if difference < -.15 - 1e-9 or difference > maximum_overrun + 1e-9:
        raise ValueError("Runway output duration differs from the explicitly requested generation")
    selected = media["duration"]
    imported = bridges.BridgeImportRequest(parent_render_id=request["parent_render_id"], trim_end=selected,
        playback_duration=selected, provenance={"provider": "runway", "model": proposal["preparation"]["provider"]["model"],
                                               "prompt": request["prompt"], "seed": request["seed"]}).model_dump(mode="json")
    receipt = {"schema_version": 1, "job_id": proposal["job_id"], "bridge_version": bridges.BRIDGE_VERSION,
               "name": f"runway-{proposal['job_id']}.mp4", "sha256": jobs.sha256(root / "original.media"),
               "size": (root / "original.media").stat().st_size, "media": media,
               "duration_validation": {"policy": "measured-original-duration-bounds-v1",
                                       "requested_seconds": request["duration"], "actual_seconds": media["duration"],
                                       "maximum_underrun_seconds": .15, "maximum_overrun_seconds": maximum_overrun,
                                       "preserved_at_original_speed": True},
               "provenance_status": "provider-task-verified", "request": imported,
               "parent_manifest_sha256": proposal["parent_manifest_sha256"],
               "frame_a_sha256": proposal["preparation"]["artifacts"]["frame-a.jpg"],
               "frame_b_sha256": proposal["preparation"]["artifacts"]["frame-b.jpg"],
               "source_frame_times": proposal["preparation"]["source_frame_times"]}
    providers._atomic(root / "input.json", receipt)
    assembly = {"job_id": proposal["job_id"], "bridge_version": bridges.BRIDGE_VERSION, "request": imported,
                "source_snapshot": proposal["source_snapshot"], "input": receipt,
                "input_manifest_sha256": jobs.sha256(root / "input.json")}
    providers._atomic(root / "assembly.json", assembly)
    return assembly


def _downloaded(root, proposal, receipt):
    saved = _read(root / "download.json")
    original = root / "original.media"
    _check_render_file(original)
    if (jobs.sha256(root / "download.json") != receipt.get("download_manifest_sha256")
            or receipt.get("task", {}).get("status") != "SUCCEEDED" or saved["task_id"] != receipt.get("task_id")
            or saved["job_id"] != proposal["job_id"] or saved["preparation_sha256"] != proposal["preparation_sha256"]
            or jobs.sha256(original) != saved["sha256"] or original.stat().st_size != saved["size"]):
        raise ValueError("The saved generated original changed or belongs to another task")
    return original


def run(job, config, db, store, progress, cancelled):
    proposal = job["snapshot"]["transition_generation"]
    if proposal["job_id"] != job["id"] or proposal["generation_version"] != GENERATION_VERSION:
        raise ValueError("This generation belongs to another job or implementation version")
    request = GenerateRequest.model_validate(proposal["request"])
    root = _verify(config, proposal, db)
    cancelled = Guard(root, cancelled, kind="transition-generate")
    if cancelled():
        raise JobCancelled("AI bridge generation cancelled before submission")
    # This exclusive durable marker also blocks replay before the POST receipt.
    started = root / "generation-started.json"
    _check_render_file(started)
    try:
        with started.open("x", encoding="utf-8") as stream:
            json.dump({"job_id": job["id"], "started_at": time.time()}, stream)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError:
        raise ValueError("This generation was already started; reconcile its provider receipt instead of replaying") from None
    client = providers.RunwayClient()
    deadline = time.monotonic() + MAX_TASK_SECONDS
    try:
        if cancelled():
            raise JobCancelled("AI bridge generation cancelled before submission")
        progress("Submitting one explicitly authorized Runway generation")
        body = {key: getattr(request, key) for key in ("model", "prompt", "duration", "resolution", "seed", "max_credits", "prompt_expansion_mode")}
        body["aspect"] = proposal["source_snapshot"]["request"]["output"]["aspect"]
        client.submit(body, root / "provider-a.jpg", root / "provider-b.jpg", root / "provider-task.json")
        _save_public(root, proposal)
        while True:
            _wait(cancelled, deadline)
            receipt = client.poll_receipt(root / "provider-task.json")
            _save_public(root, proposal)
            status = receipt["task"]["status"]
            progress(f"Runway generation: {status.lower()}")
            if status == "SUCCEEDED":
                break
            if status == "CANCELLED":
                raise JobCancelled("Runway reports that the generation was cancelled")
            if status == "FAILED":
                raise ValueError("Runway generation failed; inspect its task receipt and provider account")
        if cancelled():
            raise JobCancelled("AI bridge generation cancelled before download")
        output = receipt["task"].get("output")
        if not isinstance(output, list) or len(output) != 1 or not isinstance(output[0], str):
            raise ValueError("Runway returned an unexpected number of output videos")
        progress("Saving the generated original before its download URL expires")
        temporary = root / "original.partial"
        _check_render_file(root / "original.media")
        try:
            client.download(output[0], temporary, cancelled=cancelled)
        except (OSError, ValueError):
            raise ValueError("The generated original could not be downloaded safely; reconcile the retained provider task") from None
        temporary.replace(root / "original.media")
        # Preserve recovery access before local assembly or source checks can
        # fail. This receipt never contains the expiring signed download URL.
        downloaded = {"job_id": job["id"], "task_id": receipt["task_id"],
                      "preparation_sha256": proposal["preparation_sha256"],
                      "sha256": jobs.sha256(root / "original.media"),
                      "size": (root / "original.media").stat().st_size,
                      "media": bridges._media_info(root / "original.media")}
        providers._atomic(root / "download.json", downloaded)
        receipt["download_manifest_sha256"] = jobs.sha256(root / "download.json")
        providers._atomic(root / "provider-task.json", receipt)
        _downloaded(root, proposal, receipt)
        _verify(config, proposal, db)
        assembly = _assembly(proposal, root)
        public = _public_receipt(proposal, receipt)
        generation = {"generation_version": GENERATION_VERSION, "request": proposal["request"],
                      "preparation_sha256": proposal["preparation_sha256"],
                      "preparation": proposal["preparation"], "provider_receipt": public,
                      "download": downloaded,
                      "assembly_proposal_sha256": jobs.sha256(root / "assembly.json")}
        result = bridges.run({**job, "snapshot": {"transition_bridge": assembly}}, config, db, store,
                             progress, cancelled, generation=generation)
        result.update(generation_version=GENERATION_VERSION, provider="runway", model=proposal["preparation"]["provider"]["model"],
                      task_id=receipt["task_id"], estimated_credits=public.get("estimated_credits"),
                      actual_credits=public.get("actual_credits"), receipt_url=f"/lab/transitions/generations/{job['id']}/receipt")
        return result
    except BaseException as exc:
        _cancel(client, root, proposal, "user_cancelled" if isinstance(exc, JobCancelled) else "generation_failed_or_timed_out")
        raise
    finally:
        _save_public(root, proposal)


def receipt(config, job):
    if job.get("kind") != "transition-generate":
        raise ValueError("This is not an AI bridge generation")
    proposal = job["snapshot"]["transition_generation"]
    if proposal["job_id"] != job["id"]:
        raise ValueError("The generation belongs to another job")
    root = _owned(config, proposal)
    return _public_receipt(proposal, _submission(root, proposal))


def cancel_remote(config, job):
    """Cancel a confirmed interrupted/failed task without creating any task."""
    if job["status"] in {"queued", "running", "waiting_worker"}:
        raise ValueError("The active worker owns cancellation for this generation")
    receipt(config, job)  # Validate job ownership and immutable request first.
    proposal = job["snapshot"]["transition_generation"]
    root = _owned(config, proposal)
    from filelock import FileLock, Timeout
    lock_path = root / "provider-cancel.lock"
    _check_render_file(lock_path)
    try:
        with FileLock(lock_path, timeout=0):
            _cancel(providers.RunwayClient(), root, proposal, "explicit_reconciliation_cancel")
    except Timeout:
        # Another request owns the same cancellation; callers can read status.
        pass
    return receipt(config, job)


def artifact(config, job, kind):
    if job.get("kind") != "transition-generate":
        raise ValueError("This is not an AI bridge generation")
    proposal = job["snapshot"]["transition_generation"]
    if proposal["job_id"] != job["id"]:
        raise ValueError("The generation belongs to another job")
    root = _owned(config, proposal)
    if kind == "receipt":
        # HTTP receipt reads use receipt() directly. This cached file is useful
        # only after the worker published it, without mutating shared artifacts.
        path = root / "provider-public.json"
        if _read(path) != receipt(config, job):
            raise ValueError("Read the current generation receipt endpoint")
        return path
    if kind == "original" and job["status"] in {"completed", "failed", "interrupted"} and not job["cancel_requested"]:
        return _downloaded(root, proposal, _submission(root, proposal))
    if job["status"] != "completed" or job["cancel_requested"] or not job.get("result"):
        raise ValueError("This generated bridge audition is not ready")
    manifest_path = root / "manifest.json"
    manifest = _read(manifest_path)
    if jobs.sha256(manifest_path) != job["result"]["manifest_sha256"]:
        raise ValueError("The generated bridge manifest changed")
    generation = manifest["generation"]
    if (generation["request"] != proposal["request"] or generation["preparation_sha256"] != proposal["preparation_sha256"]
            or generation["assembly_proposal_sha256"] != jobs.sha256(root / "assembly.json")
            or generation["provider_receipt"] != _public_receipt(proposal, _submission(root, proposal))):
        raise ValueError("The generated bridge receipt changed or belongs to another request")
    if kind in {"provider-a", "provider-b"}:
        return root / f"{kind}.jpg"
    assembly = _read(root / "assembly.json")
    return bridges.artifact(config, {**job, "kind": "transition-bridge", "snapshot": {"transition_bridge": assembly}}, kind)
