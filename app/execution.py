from __future__ import annotations

import concurrent.futures
import time
import urllib.error
from dataclasses import dataclass
from typing import Any, Callable


class RetryableToolError(RuntimeError):
    """Signal a transient tool failure that may succeed on another attempt."""


class ToolTimeoutError(RetryableToolError):
    pass


class ToolExecutionFailed(RuntimeError):
    def __init__(self, message: str, attempts: list[dict[str, Any]]) -> None:
        super().__init__(message)
        self.attempts = attempts


@dataclass(frozen=True)
class ExecutionPolicy:
    max_attempts: int = 2
    timeout_seconds: float = 45.0
    backoff_seconds: float = 0.05

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.backoff_seconds < 0:
            raise ValueError("backoff_seconds cannot be negative")


def _call_with_timeout(call: Callable[[], Any], timeout_seconds: float) -> Any:
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="docflow-tool")
    future = executor.submit(call)
    try:
        return future.result(timeout=timeout_seconds)
    except concurrent.futures.TimeoutError as error:
        future.cancel()
        raise ToolTimeoutError(f"tool timed out after {timeout_seconds:g}s") from error
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def _is_retryable(error: Exception) -> bool:
    return isinstance(error, (RetryableToolError, ConnectionError, urllib.error.URLError, OSError))


def invoke_with_policy(call: Callable[[], Any], policy: ExecutionPolicy) -> tuple[Any, list[dict[str, Any]]]:
    """Execute one tool with bounded timeout/retry and return attempt-level telemetry."""
    attempts: list[dict[str, Any]] = []
    for attempt in range(1, policy.max_attempts + 1):
        started = time.perf_counter()
        try:
            output = _call_with_timeout(call, policy.timeout_seconds)
            attempts.append(
                {
                    "attempt": attempt,
                    "status": "completed",
                    "error": "",
                    "elapsed_ms": round((time.perf_counter() - started) * 1000),
                }
            )
            return output, attempts
        except Exception as error:  # bounded runtime boundary
            retryable = _is_retryable(error)
            will_retry = retryable and attempt < policy.max_attempts
            attempts.append(
                {
                    "attempt": attempt,
                    "status": "retrying" if will_retry else "failed",
                    "error": str(error),
                    "error_type": type(error).__name__,
                    "retryable": retryable,
                    "elapsed_ms": round((time.perf_counter() - started) * 1000),
                }
            )
            if not will_retry:
                raise ToolExecutionFailed(str(error), attempts) from error
            if policy.backoff_seconds:
                time.sleep(policy.backoff_seconds * attempt)
    raise AssertionError("unreachable")
