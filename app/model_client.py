from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse


_STATUS_LOCK = threading.Lock()
_LAST_MODEL_CALL: dict[str, Any] | None = None


class ModelCallError(RuntimeError):
    def __init__(self, message: str, telemetry: dict[str, Any]) -> None:
        super().__init__(message)
        self.telemetry = telemetry


def _optional_non_negative_float(name: str) -> float | None:
    raw = os.getenv(name, "").strip()
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError as error:
        raise RuntimeError(f"{name} 必须是非负数字") from error
    if value < 0:
        raise RuntimeError(f"{name} 必须是非负数字")
    return value


def _safe_provider(base_url: str) -> str:
    return urlparse(base_url).hostname or "openai-compatible"


def _usage(payload: dict[str, Any]) -> dict[str, int]:
    raw = payload.get("usage") if isinstance(payload, dict) else None
    raw = raw if isinstance(raw, dict) else {}

    def as_int(value: Any) -> int:
        try:
            return max(int(value or 0), 0)
        except (TypeError, ValueError):
            return 0

    prompt_tokens = as_int(raw.get("prompt_tokens"))
    completion_tokens = as_int(raw.get("completion_tokens"))
    total_tokens = as_int(raw.get("total_tokens")) or prompt_tokens + completion_tokens
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
    }


def _estimated_cost(usage: dict[str, int]) -> tuple[float | None, str]:
    input_rate = _optional_non_negative_float("MODEL_INPUT_COST_PER_MILLION")
    output_rate = _optional_non_negative_float("MODEL_OUTPUT_COST_PER_MILLION")
    currency = os.getenv("MODEL_COST_CURRENCY", "CNY").strip().upper() or "CNY"
    if input_rate is None or output_rate is None:
        return None, currency
    cost = (
        usage["prompt_tokens"] * input_rate
        + usage["completion_tokens"] * output_rate
    ) / 1_000_000
    return round(cost, 8), currency


def _record_model_call(telemetry: dict[str, Any]) -> None:
    global _LAST_MODEL_CALL
    with _STATUS_LOCK:
        _LAST_MODEL_CALL = dict(telemetry)


def model_runtime_status(model_configured: bool) -> dict[str, Any]:
    if not model_configured:
        return {
            "model_reachability": "not_configured",
            "last_model_check": None,
        }
    with _STATUS_LOCK:
        latest = dict(_LAST_MODEL_CALL) if _LAST_MODEL_CALL else None
    if latest is None:
        return {
            "model_reachability": "not_tested",
            "last_model_check": None,
        }
    return {
        "model_reachability": "reachable" if latest.get("status") == "succeeded" else "unavailable",
        "last_model_check": {
            "status": latest.get("status"),
            "stage": latest.get("stage"),
            "checked_at": latest.get("checked_at"),
            "error_type": latest.get("error_type"),
        },
    }


def call_chat_completion(
    payload: dict[str, Any],
    *,
    api_key: str,
    base_url: str,
    stage: str,
    timeout: int,
) -> tuple[str, dict[str, Any]]:
    endpoint = f"{base_url.rstrip('/')}/chat/completions"
    started = time.perf_counter()
    base_telemetry: dict[str, Any] = {
        "stage": stage,
        "provider": _safe_provider(base_url),
        "model": str(payload.get("model") or ""),
        "status": "failed",
        "latency_ms": 0,
        "request_id": None,
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        "estimated_cost": None,
        "cost_currency": os.getenv("MODEL_COST_CURRENCY", "CNY").strip().upper() or "CNY",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "error_type": None,
    }
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw_body = response.read().decode("utf-8")
            headers = getattr(response, "headers", {})
            header_request_id = headers.get("x-request-id") if hasattr(headers, "get") else None
        response_payload = json.loads(raw_body)
        content = response_payload["choices"][0]["message"]["content"]
        if not isinstance(content, str) or not content.strip():
            raise ValueError("model response content is empty")
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as error:
        telemetry = {
            **base_telemetry,
            "latency_ms": round((time.perf_counter() - started) * 1000),
            "error_type": type(error).__name__,
        }
        _record_model_call(telemetry)
        status = getattr(error, "code", None)
        detail = f"{type(error).__name__}" + (f" HTTP {status}" if status else "")
        raise ModelCallError(detail, telemetry) from error

    usage = _usage(response_payload)
    cost, currency = _estimated_cost(usage)
    telemetry = {
        **base_telemetry,
        "status": "succeeded",
        "latency_ms": round((time.perf_counter() - started) * 1000),
        "request_id": str(response_payload.get("id") or header_request_id or "") or None,
        "model": str(response_payload.get("model") or payload.get("model") or ""),
        "usage": usage,
        "estimated_cost": cost,
        "cost_currency": currency,
        "error_type": None,
    }
    _record_model_call(telemetry)
    return content, telemetry
