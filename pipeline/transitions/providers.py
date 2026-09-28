"""Bounded Runway endpoint-bridge models; credentials never enter receipts.

Readiness and quotes are local. The low-level client does not retry submissions,
purchase credit, choose another model, or treat reference videos as keyframes.
The generation module owns the durable job lifecycle and spend authorization.
"""
from __future__ import annotations

import base64
from copy import deepcopy
import hashlib
import http.client
import json
import os
from pathlib import Path
import queue
import socket
import threading
import time
from typing import Literal
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

from pydantic import Field, model_validator

from pipeline.lab.media import _check_render_directory, _check_render_file
from pipeline.transitions import bridges, jobs
from pipeline.transitions.contracts import StrictModel

MODEL = "seedance2_5"
DEFAULT_MODEL = "seedance2_5"
API_VERSION = "2024-11-06"
PRICE_VERSION = "runway-seedance2_5-2026-09-16"
KEY_ENV = "RUNWAYML_API_SECRET"
# Runway's current OpenAPI limits the entire data URI, not raw image bytes.
MAX_IMAGE_URI_LENGTH = 5 * 1024 * 1024
DOWNLOAD_SECONDS = 120
DOWNLOAD_CONNECT_SECONDS = 30
DOWNLOAD_ADDRESS_SECONDS = 3
_DOWNLOAD_RESOLVERS = threading.BoundedSemaphore(2)
RATES = {"480p": 20, "720p": 30, "1080p": 68}
RATIOS = {"480p": {"landscape": "854:480", "portrait": "480:854", "square": "640:640"},
          "720p": {"landscape": "1280:720", "portrait": "720:1280", "square": "960:960"},
          "1080p": {"landscape": "1920:1080", "portrait": "1080:1920", "square": "1440:1440"}}

MODEL_SPECS = {
    "seedance2_5": {"label": "Seedance 2.5", "price_version": PRICE_VERSION, "rates": RATES,
                    "min_credits": 80, "durations": list(range(4, 9)), "max_prompt_length": 15000,
                    "seed": True, "prompt_expansion_modes": [], "input_dimensions": RATIOS,
                    "min_image_side": 300, "ratio_mode": "explicit", "defaults": {"duration": 4, "resolution": "720p"}},
    "h3_max": {"label": "MiniMax H3 Max", "price_version": "runway-h3_max-2026-09-16",
               "rates": {"480p": 5, "768p": 8}, "min_credits": 0, "durations": list(range(5, 9)),
               "max_prompt_length": 6000, "seed": True,
               "prompt_expansion_modes": ["disabled", "balanced", "quality"],
               "input_dimensions": {
                   "480p": {"landscape": "854:480", "portrait": "480:854", "square": "480:480"},
                   "768p": {"landscape": "1366:768", "portrait": "768:1366", "square": "768:768"}},
               "min_image_side": 256, "ratio_mode": "input",
               "defaults": {"duration": 5, "resolution": "768p", "prompt_expansion_mode": "disabled"}},
    "wan3": {"label": "Wan 3", "price_version": "runway-wan3-2026-09-16",
             "rates": {"480p": 5, "720p": 10, "1080p": 20}, "min_credits": 0, "durations": list(range(2, 9)),
             "max_prompt_length": 20000, "seed": False, "prompt_expansion_modes": [],
             "input_dimensions": RATIOS, "min_image_side": 300, "ratio_mode": "auto",
             "defaults": {"duration": 2, "resolution": "720p"}},
}


def model_spec(model=DEFAULT_MODEL):
    try:
        return MODEL_SPECS[model]
    except KeyError:
        raise ValueError("Choose a supported endpoint-bridge model") from None


def estimate(model, resolution, duration):
    spec = model_spec(model)
    return max(spec["min_credits"], duration * spec["rates"][resolution])


def input_ratio(model, resolution, aspect):
    return model_spec(model)["input_dimensions"][resolution][aspect]


def seed_value(model, seed):
    if not model_spec(model)["seed"]:
        if seed is not None:
            raise ValueError("This model does not support a seed; omit it")
        return None
    return 42 if seed is None else seed


def request_identity(value):
    """Compare legacy omitted defaults without rewriting any saved receipt."""
    value = dict(value)
    value.setdefault("model", DEFAULT_MODEL)
    value.setdefault("prompt_expansion_mode", "disabled" if value["model"] == "h3_max" else None)
    value.setdefault("seed", None if value["model"] == "wan3" else 42)
    return value


class ModelOptions(StrictModel):
    model: Literal["seedance2_5", "h3_max", "wan3"] = DEFAULT_MODEL
    prompt: str = Field(min_length=1, max_length=20000)
    duration: int = Field(default=4, ge=2, le=8, strict=True)
    resolution: Literal["480p", "720p", "768p", "1080p"] = "720p"
    prompt_expansion_mode: Literal["disabled", "balanced", "quality"] | None = None

    @model_validator(mode="before")
    @classmethod
    def model_defaults(cls, value):
        if isinstance(value, dict) and isinstance(value.get("model", DEFAULT_MODEL), str):
            spec = MODEL_SPECS.get(value.get("model", DEFAULT_MODEL))
            if spec:
                value = {**spec["defaults"], **value}
        return value

    @model_validator(mode="after")
    def supported(self):
        spec = model_spec(self.model)
        if self.duration not in spec["durations"] or self.resolution not in spec["rates"]:
            raise ValueError("Choose a duration and resolution supported by the selected model")
        if not self.prompt.strip() or len(self.prompt) > spec["max_prompt_length"]:
            raise ValueError(f"Describe the bridge motion in at most {spec['max_prompt_length']} characters")
        if self.prompt_expansion_mode is not None and self.prompt_expansion_mode not in spec["prompt_expansion_modes"]:
            raise ValueError("This model does not support prompt expansion controls; omit that setting")
        if self.model == "h3_max" and self.prompt_expansion_mode is None:
            self.prompt_expansion_mode = "disabled"
        return self


class QuoteRequest(ModelOptions):
    parent_render_id: str

    @model_validator(mode="after")
    def parent(self):
        bridges.BridgeImportRequest(parent_render_id=self.parent_render_id)
        if not self.prompt.strip():
            raise ValueError("Describe the bridge motion")
        return self


class RunwayRequest(ModelOptions):
    aspect: Literal["landscape", "portrait", "square"] = "landscape"
    seed: int | None = Field(default=None, ge=0, le=4294967295, strict=True)
    max_credits: int = Field(ge=1, le=300, strict=True)

    @model_validator(mode="after")
    def spend(self):
        self.seed = seed_value(self.model, self.seed)
        if estimate(self.model, self.resolution, self.duration) > self.max_credits:
            raise ValueError("The quoted generation exceeds the explicit credit ceiling")
        if not self.prompt.strip():
            raise ValueError("Describe the bridge motion")
        return self


def provider_status():
    models = []
    for identity, spec in MODEL_SPECS.items():
        models.append({"id": identity, "label": spec["label"], "max_prompt_length": spec["max_prompt_length"],
                       "defaults": deepcopy(spec["defaults"]), "input_dimensions": deepcopy(spec["input_dimensions"]),
                       "ratios": deepcopy(spec["input_dimensions"]) if spec["ratio_mode"] == "explicit" else None,
                       "capabilities": {"first_last_frames": True, "min_seconds": min(spec["durations"]),
                                        "max_seconds": max(spec["durations"]), "durations": list(spec["durations"]),
                                        "resolutions": list(spec["rates"]), "seed": spec["seed"],
                                        "prompt_expansion_modes": list(spec["prompt_expansion_modes"])},
                       "pricing": {"verified_on": "2026-09-16", "version": spec["price_version"],
                                   "credits_per_second": dict(spec["rates"]), "min_credits": spec["min_credits"],
                                   "max_request_credits": 300},
                       "documentation_url": "https://docs.dev.runwayml.com/assets/inputs/"})
    return {"provider": "runway", "model": DEFAULT_MODEL, "default_model": DEFAULT_MODEL, "models": models,
            "configured": bool(os.environ.get(KEY_ENV, "").strip()),
            "credential_environment": KEY_ENV, "live_verified": False, "generation_enabled": True,
            "capabilities": {"first_last_frames": True, "min_seconds": 4, "max_seconds": 8},
            "pricing": {"verified_on": "2026-09-16", "version": PRICE_VERSION,
                        "credits_per_second": dict(RATES), "min_credits": 80, "max_request_credits": 300},
            "documentation_url": "https://docs.dev.runwayml.com/guides/pricing/"}


def quote(body, config, db, store):
    request = QuoteRequest.model_validate(body)
    parent = store.get_job(request.parent_render_id, private=True)
    source, _manifest = bridges._check_parent(parent, config, db)
    aspect = source["request"]["output"]["aspect"]
    spec = model_spec(request.model)
    credits = estimate(request.model, request.resolution, request.duration)
    width, height = map(int, input_ratio(request.model, request.resolution, aspect).split(":"))
    ratio = input_ratio(request.model, request.resolution, aspect) if spec["ratio_mode"] == "explicit" else (
        "auto_" + request.resolution if spec["ratio_mode"] == "auto" else None)
    return {"provider": "runway", "model": request.model, "request": request.model_dump(mode="json"),
            "aspect": aspect, "ratio": ratio, "input_dimensions": {"width": width, "height": height},
            "output_shape_guaranteed": False, "output_ratio_mode": spec["ratio_mode"],
            "requested_output_dimensions": {"width": width, "height": height} if spec["ratio_mode"] == "explicit" else None,
            "estimated_credits": credits, "estimated_usd": credits / 100,
            "within_request_ceiling": credits <= 300, "price_version": spec["price_version"],
            "configured": provider_status()["configured"], "generation_enabled": True,
            "audio": False, "requires_explicit_generation": True,
            "parent_manifest_sha256": parent["result"]["manifest_sha256"]}


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class _DownloadBudget:
    """One deadline also closes a socket stuck in headers or a slow body read."""
    def __init__(self, cancelled):
        self.deadline = time.monotonic() + DOWNLOAD_SECONDS
        self.cancelled = cancelled
        self.sockets = []
        self.lock = threading.Lock()
        self.expired = False
        self.timer = threading.Timer(DOWNLOAD_SECONDS, self.expire)
        self.timer.daemon = True

    def __enter__(self):
        self.timer.start()
        return self

    @staticmethod
    def _close(sock):
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            sock.close()
        except OSError:
            pass

    def expire(self):
        with self.lock:
            self.expired = True
            sockets = list(self.sockets)
        for sock in sockets:
            self._close(sock)

    def track(self, sock):
        with self.lock:
            self.sockets.append(sock)
            expired = self.expired
        if expired:
            self._close(sock)
        self.remaining()

    def remaining(self, maximum=30, deadline=None):
        if self.cancelled():
            raise bridges.JobCancelled("Generated bridge download cancelled")
        remaining = min(self.deadline, deadline or self.deadline) - time.monotonic()
        if self.expired or remaining <= 0:
            raise TimeoutError("Generated bridge download exceeded its time budget")
        return min(maximum, remaining)

    def __exit__(self, *_args):
        self.timer.cancel()
        for sock in self.sockets:
            self._close(sock)


def _download_addresses(address, budget, deadline):
    # getaddrinfo has no timeout parameter. Bound the caller's wait and the
    # number of lingering OS resolver calls; a daemon never delays shutdown.
    budget.remaining(deadline=deadline)
    if not _DOWNLOAD_RESOLVERS.acquire(blocking=False):
        raise OSError("Artifact name resolution is busy; try this saved download later")
    result = queue.Queue(maxsize=1)

    def resolve():
        try:
            result.put((socket.getaddrinfo(*address, type=socket.SOCK_STREAM), None))
        except Exception as exc:
            result.put((None, exc))
        finally:
            _DOWNLOAD_RESOLVERS.release()

    try:
        threading.Thread(target=resolve, daemon=True, name="transition-artifact-dns").start()
    except BaseException:
        _DOWNLOAD_RESOLVERS.release()
        raise
    while True:
        try:
            addresses, error = result.get(timeout=budget.remaining(.2, deadline))
            if error is not None:
                raise OSError("Artifact name resolution failed") from None
            budget.remaining(deadline=deadline)
            # Broken IPv6 routes can precede every working IPv4 address on
            # Windows. Keep IPv6 fallback, but try the IPv4 routes first.
            return sorted(addresses, key=lambda row: row[0] != socket.AF_INET)
        except queue.Empty:
            continue


def _download_connect(address, budget, deadline, source_address=None):
    for family, kind, protocol, _name, target in _download_addresses(address, budget, deadline):
        timeout = budget.remaining(DOWNLOAD_ADDRESS_SECONDS, deadline)
        sock = socket.socket(family, kind, protocol)
        try:
            budget.track(sock)
            sock.settimeout(timeout)
            if source_address:
                sock.bind(source_address)
            sock.connect(target)
            # TLS uses the remainder of the same connect budget, not a fresh
            # timeout for each resolved address or handshake stage.
            sock.settimeout(budget.remaining(deadline=deadline))
            return sock
        except OSError:
            sock.close()
    raise OSError("No artifact address connected within the download budget")


class _DownloadHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, *args, budget, **kwargs):
        super().__init__(*args, **kwargs)
        self._download_budget = budget
        self._connect_deadline = min(budget.deadline, time.monotonic() + DOWNLOAD_CONNECT_SECONDS)
        # CPython's HTTPConnection socket factory keeps HTTPSConnection's
        # standard proxy tunnelling, SNI and verified TLS context unchanged.
        self._create_connection = lambda address, timeout, source_address=None: _download_connect(
            address, budget, self._connect_deadline, source_address)

    def connect(self):
        super().connect()
        self._download_budget.track(self.sock)
        self.sock.settimeout(self._download_budget.remaining())


class _DownloadHTTPSHandler(HTTPSHandler):
    def __init__(self, budget):
        super().__init__()
        self.budget = budget

    def https_open(self, request):
        return self.do_open(_DownloadHTTPSConnection, request, budget=self.budget,
                            context=self._context)


def _atomic(path, value):
    _check_render_directory(path.parent)
    _check_render_file(path)
    temporary = path.with_suffix(path.suffix + ".partial")
    _check_render_file(temporary)
    with temporary.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


class RunwayClient:
    """One-shot submissions require a caller-owned immutable job receipt path.

    A receipt left in submitting/uncertain state must be reconciled by an
    operator; calling submit again always fails instead of billing twice.
    Poll/cancel are read-or-cancel-only and cannot create another generation.
    """
    def __init__(self, key=None):
        self._key = key if key is not None else os.environ.get(KEY_ENV, "")
        self._opener = build_opener(_NoRedirect())

    def _request(self, method, path, body=None):
        if not self._key.strip():
            raise ValueError(f"Configure {KEY_ENV} on the server before generation")
        data = json.dumps(body, allow_nan=False).encode() if body is not None else None
        request = Request("https://api.dev.runwayml.com/v1/" + path, data=data, method=method,
                          headers={"Authorization": "Bearer " + self._key, "X-Runway-Version": API_VERSION,
                                   "Content-Type": "application/json"})
        try:
            with self._opener.open(request, timeout=45) as response:
                raw = response.read(1024 * 1024 + 1)
        except HTTPError as exc:
            # Provider responses may echo prompts, URLs or account details.
            raise ValueError(f"Runway request returned HTTP {exc.code}") from None
        except (URLError, OSError):
            raise ValueError("Runway could not be reached; reconcile any submitted task before retrying") from None
        if len(raw) > 1024 * 1024:
            raise ValueError("Runway returned an oversized task response")
        return json.loads(raw) if raw else {}

    def submit(self, body, first_frame: Path, last_frame: Path, receipt_path: Path):
        request = RunwayRequest.model_validate(body)
        spec = model_spec(request.model)
        if not self._key.strip():
            raise ValueError(f"Configure {KEY_ENV} on the server before generation")
        receipt_path = Path(receipt_path)
        _check_render_directory(receipt_path.parent)
        if receipt_path.exists() or receipt_path.with_suffix(receipt_path.suffix + ".partial").exists():
            raise ValueError("This generation already has a submission receipt; reconcile it instead of resubmitting")
        images, inputs = [], []
        from PIL import Image
        for path, position in ((Path(first_frame), "first"), (Path(last_frame), "last")):
            _check_render_file(path)
            if not path.is_file() or path.stat().st_size > (MAX_IMAGE_URI_LENGTH - 23) * 3 // 4:
                raise ValueError("Each generation endpoint must fit Runway's 5 MiB encoded-image limit")
            with Image.open(path) as image:
                if image.format not in {"JPEG", "PNG"} or min(image.size) < spec["min_image_side"] or not .4 <= image.width / image.height <= 4:
                    raise ValueError(f"Generation endpoints must be JPEG/PNG, at least {spec['min_image_side']} pixels per side, within the model aspect limits")
                mime = "image/jpeg" if image.format == "JPEG" else "image/png"
            raw = path.read_bytes()
            uri = f"data:{mime};base64," + base64.b64encode(raw).decode()
            if len(uri) > MAX_IMAGE_URI_LENGTH:
                raise ValueError("Each generation endpoint must fit Runway's 5 MiB encoded-image limit")
            images.append({"uri": uri, "position": position})
            inputs.append({"position": position, "sha256": hashlib.sha256(raw).hexdigest()})
        payload = {"model": request.model, "promptText": request.prompt, "promptImage": images, "duration": request.duration}
        if request.model == "h3_max":
            payload.update(resolution=request.resolution, promptExpansionMode=request.prompt_expansion_mode, seed=request.seed)
        elif request.model == "wan3":
            payload.update(ratio="auto_" + request.resolution, audio=False)
        else:
            payload.update(ratio=input_ratio(request.model, request.resolution, request.aspect), audio=False, seed=request.seed)
        receipt = {"schema_version": 1, "provider": "runway", "model": request.model, "api_version": API_VERSION,
                   "price_version": spec["price_version"], "request": request.model_dump(mode="json"), "inputs": inputs,
                   "state": "submitting", "created_at": time.time(),
                   "request_sha256": hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()}
        # Exclusive creation is the cross-thread/process guard before any charge.
        with receipt_path.open("x", encoding="utf-8") as stream:
            json.dump(receipt, stream, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            response = self._request("POST", "image_to_video", payload)
            identity = str(__import__("uuid").UUID(response["id"]))
        except BaseException:
            receipt["state"] = "submission_uncertain"
            _atomic(receipt_path, receipt)
            raise
        estimated = response.get("estimatedCost")
        actual = estimated.get("credits") if isinstance(estimated, dict) else None
        valid_cost = isinstance(actual, (int, float)) and math_is_finite(actual) and actual >= 0
        receipt.update(state="submitted", task_id=identity,
                       estimated_cost={"credits": actual} if valid_cost else None, accepted_at=time.time())
        _atomic(receipt_path, receipt)
        # A price change cannot be undone; preserve and immediately attempt cancel.
        if not valid_cost or actual > request.max_credits:
            receipt.update(state="cost_changed_cancel_requested", cancellation_requested=True,
                           cancellation_confirmed=False, cancellation_reason="cost_ceiling_unavailable_or_exceeded")
            _atomic(receipt_path, receipt)
            try:
                outcome = self.cancel(identity)
                receipt["cancellation_confirmed"] = bool(outcome.get("cancelled"))
                receipt["cancel_observed_status"] = outcome.get("status")
                if receipt["cancellation_confirmed"]:
                    receipt["state"] = "cancelled"
            except Exception:
                pass  # A failed cancellation remains explicitly unconfirmed.
            _atomic(receipt_path, receipt)
            raise ValueError("Runway returned an unavailable or excessive cost; cancellation was requested")
        return receipt

    def poll(self, task_id):
        identity = str(__import__("uuid").UUID(task_id))
        result = self._request("GET", f"tasks/{identity}")
        if result.get("id") != identity or result.get("status") not in {
            "PENDING", "THROTTLED", "RUNNING", "SUCCEEDED", "FAILED", "CANCELLED"}:
            raise ValueError("Runway returned an unexpected task identity or status")
        return result

    def poll_receipt(self, receipt_path):
        receipt_path = Path(receipt_path)
        _check_render_directory(receipt_path.parent)
        _check_render_file(receipt_path)
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        if not receipt.get("task_id"):
            raise ValueError("This submission has no confirmed task identity; reconcile it before continuing")
        status = self.poll(receipt["task_id"])
        receipt.update(state=status["status"].lower(), last_polled_at=time.time(), task=status)
        _atomic(receipt_path, receipt)
        return receipt

    def cancel(self, task_id):
        current = self.poll(task_id)
        if current["status"] not in {"PENDING", "THROTTLED", "RUNNING"}:
            return {"status": current["status"], "cancelled": False}
        self._request("DELETE", f"tasks/{current['id']}")
        return {"status": "CANCELLED", "cancelled": True}

    def download(self, url, destination, *, cancelled=lambda: False):
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        if (parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in {None, 443}
                or not any(host.endswith("." + domain) or host == domain for domain in
                           ("cloudfront.net", "runwayml.com", "runwaycdn.com"))):
            raise ValueError("Runway output is outside the supported HTTPS artifact hosts")
        destination = Path(destination)
        _check_render_directory(destination.parent)
        _check_render_file(destination)
        size, created = 0, False
        try:
            with _DownloadBudget(cancelled) as budget:
                # An isolated opener never forwards API Authorization to CDN
                # hosts, changes the API transport, or follows redirects.
                opener = build_opener(_NoRedirect(), _DownloadHTTPSHandler(budget))
                with opener.open(Request(url), timeout=budget.remaining()) as response:
                    budget.remaining()
                    length = response.headers.get("Content-Length")
                    if length is not None and (not length.isdecimal() or int(length) > bridges.MAX_UPLOAD_BYTES):
                        raise ValueError("Generated bridge exceeds the 128 MiB import limit")
                    with destination.open("xb") as stream:
                        created = True
                        while True:
                            budget.remaining()
                            # read1 returns after at most one underlying read,
                            # so a trickling stream cannot hide inside read(1MiB).
                            chunk = response.read1(1024 * 1024)
                            budget.remaining()
                            if not chunk:
                                break
                            size += len(chunk)
                            if size > bridges.MAX_UPLOAD_BYTES:
                                raise ValueError("Generated bridge exceeds the 128 MiB import limit")
                            stream.write(chunk)
                        if length is not None and size != int(length):
                            raise ValueError("Generated bridge download ended before its declared length")
        except BaseException:
            # Never delete a pre-existing file when exclusive creation fails.
            if created:
                destination.unlink(missing_ok=True)
            raise
        return {"size": size, "sha256": jobs.sha256(destination)}


def math_is_finite(value):
    import math
    return not isinstance(value, bool) and math.isfinite(value)
