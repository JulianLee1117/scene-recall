"""Small, explicitly invoked hosted comparison; ordinary search never calls it."""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import atexit
import hashlib
import json
import math
import os
from pathlib import Path
import time

from filelock import FileLock, Timeout

from pipeline.experiments.search_intent import _http_client, request_decisions

MAX_CALLS = 64
MAX_USD = 0.05
CALL_RESERVE_USD = 0.003
_CLIENT = None


def close_transport():
    global _CLIENT
    if _CLIENT is not None:
        _CLIENT.close()
        _CLIENT = None


atexit.register(close_transport)


def _write(path: Path, value: dict) -> None:
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, allow_nan=False)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def interpret(query: str, state_dir: Path, *, film_ids=(), transport=None, api_key=None):
    """Journal before one paid attempt, cache exact receipts, and never retry.

    The HTTP transport is bounded independently. The caller also imposes an
    application deadline; a late receipt is still recorded here for accounting.
    One process/file lock prevents a backlog of simultaneous paid attempts.
    """
    global _CLIENT
    from pipeline.search.intent import build_intent_request, parse_intent_response

    body = build_intent_request(query, film_ids=film_ids)
    identity = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
    root = Path(state_dir) / "intent-comparison"
    root.mkdir(parents=True, exist_ok=True)
    try:
        with FileLock(str(root / "provider.lock"), timeout=0):
            receipts = []
            for path in root.glob("*.json"):
                try:
                    receipt = json.loads(path.read_text(encoding="utf-8"))
                    cost = receipt.get("cost_usd") if isinstance(receipt, dict) else None
                    if (not isinstance(receipt, dict) or "cost_usd" not in receipt
                            or (cost is not None and (type(cost) not in (int, float)
                                or not math.isfinite(cost) or cost < 0))):
                        raise ValueError("Invalid cost receipt")
                    receipts.append(receipt)
                except (OSError, ValueError):
                    return None, {"status": "fallback", "reason": "unreadable_cost_receipt"}
            for receipt in receipts:
                if receipt.get("identity") == identity and receipt.get("status") == "completed":
                    try:
                        if receipt.get("request") != body:
                            raise ValueError("Cached request differs")
                        intent = parse_intent_response(receipt["response"], query=query, film_ids=film_ids)
                    except (ValueError, KeyError, TypeError):
                        return None, {"status": "fallback", "reason": "invalid_cached_receipt"}
                    return intent, {"status": "cached", "cost_usd": 0, "model": receipt.get("model")}
            if any(receipt.get("cost_usd") is None for receipt in receipts):
                return None, {"status": "fallback", "reason": "unknown_previous_cost"}
            if any(receipt["cost_usd"] > CALL_RESERVE_USD for receipt in receipts):
                return None, {"status": "fallback", "reason": "cost_exceeded_reserve"}
            total = sum(receipt["cost_usd"] for receipt in receipts)
            if len(receipts) >= MAX_CALLS or total + CALL_RESERVE_USD > MAX_USD:
                return None, {"status": "fallback", "reason": "comparison_budget_reached"}
            key = api_key if api_key is not None else os.getenv("OPENROUTER_API_KEY")
            if not key:
                return None, {"status": "fallback", "reason": "provider_not_configured"}
            path = root / f"{identity}.json"
            # A known-cost failed attempt also stays terminal for this input.
            if path.exists():
                return None, {"status": "fallback", "reason": "previous_attempt_failed"}
            receipt = {"identity": identity, "request": body, "status": "attempted",
                       "created_at": datetime.now(timezone.utc).isoformat(), "cost_usd": None}
            _write(path, receipt)
            started = time.perf_counter()
            intent = None
            try:
                if transport is None:
                    # The surrounding lock admits one provider request at a time.
                    # Reuse its connection instead of paying a cold TLS setup on
                    # every query; model/request identity stays receipt-scoped.
                    if _CLIENT is None:
                        _CLIENT = _http_client(1.5)
                    raw = request_decisions(body, api_key=key, timeout=1.5, client=_CLIENT)
                else:
                    raw = transport(body, api_key=key, timeout=1.5)
                receipt["response"] = raw
                usage = raw.get("usage", {}) if isinstance(raw, dict) else {}
                cost = usage.get("cost")
                if type(cost) in (int, float) and math.isfinite(cost) and cost >= 0:
                    receipt["cost_usd"] = cost
                intent = parse_intent_response(raw, query=query, film_ids=film_ids)
                if receipt["cost_usd"] is None:
                    raise ValueError("Provider cost unavailable")
                receipt.update(status="completed", model=raw.get("model"), intent=asdict(intent))
            except Exception as exc:
                intent = None
                receipt.update(status="failed", error_type=type(exc).__name__)
            finally:
                receipt["transport_seconds"] = time.perf_counter() - started
                _write(path, receipt)
            return intent, {"status": receipt["status"], "cost_usd": receipt["cost_usd"],
                            "model": receipt.get("model"), "seconds": receipt["transport_seconds"],
                            **({"reason": "provider_failed"} if intent is None else {})}
    except Timeout:
        return None, {"status": "fallback", "reason": "provider_busy"}
