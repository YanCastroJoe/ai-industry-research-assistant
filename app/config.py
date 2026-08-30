from __future__ import annotations

import os
from dataclasses import asdict, dataclass


def _bounded_int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as error:
        raise RuntimeError(f"{name} 必须是整数") from error
    if not minimum <= value <= maximum:
        raise RuntimeError(f"{name} 必须在 {minimum} 到 {maximum} 之间")
    return value


@dataclass(frozen=True)
class RuntimeConfig:
    max_workers: int
    max_pending: int
    model_mode: str
    model_name: str

    def public_dict(self) -> dict:
        return asdict(self)


def load_runtime_config() -> RuntimeConfig:
    max_workers = _bounded_int("DOCFLOW_MAX_WORKERS", 2, 1, 8)
    max_pending = _bounded_int("DOCFLOW_MAX_PENDING", 20, max_workers, 100)
    model_key = os.getenv("MODEL_API_KEY", "").strip()
    return RuntimeConfig(
        max_workers=max_workers,
        max_pending=max_pending,
        model_mode="openai_compatible" if model_key else "local_rules",
        model_name=os.getenv("MODEL_NAME", "deepseek-chat").strip() or "deepseek-chat",
    )
