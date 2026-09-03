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


def _boolean(name: str, default: bool = False) -> bool:
    raw = os.getenv(name, "true" if default else "false").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    raise RuntimeError(f"{name} 必须是 true 或 false")


@dataclass(frozen=True)
class RuntimeConfig:
    max_workers: int
    max_pending: int
    model_mode: str
    model_name: str
    model_configured: bool
    model_reachability: str

    def public_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class DemoSecurityConfig:
    demo_mode: bool
    username: str
    password: str
    rate_limit_per_minute: int

    @property
    def authentication_enabled(self) -> bool:
        return bool(self.username and self.password)

    @property
    def public_demo_safe(self) -> bool:
        return self.demo_mode and self.authentication_enabled and self.rate_limit_per_minute > 0

    def public_dict(self) -> dict:
        return {
            "demo_mode": self.demo_mode,
            "authentication_enabled": self.authentication_enabled,
            "rate_limit_per_minute": self.rate_limit_per_minute if self.demo_mode else 0,
            "public_demo_safe": self.public_demo_safe,
        }


def load_runtime_config() -> RuntimeConfig:
    max_workers = _bounded_int("DOCFLOW_MAX_WORKERS", 2, 1, 8)
    max_pending = _bounded_int("DOCFLOW_MAX_PENDING", 20, max_workers, 100)
    model_key = os.getenv("MODEL_API_KEY", "").strip()
    return RuntimeConfig(
        max_workers=max_workers,
        max_pending=max_pending,
        model_mode="openai_compatible" if model_key else "local_rules",
        model_name=os.getenv("MODEL_NAME", "deepseek-chat").strip() or "deepseek-chat",
        model_configured=bool(model_key),
        # A configured key is not proof that the remote model can be reached.
        # The actual run result exposes model vs rules_fallback separately.
        model_reachability="not_tested" if model_key else "not_configured",
    )


def load_demo_security_config() -> DemoSecurityConfig:
    demo_mode = _boolean("DOCFLOW_DEMO_MODE", False)
    username = os.getenv("DOCFLOW_DEMO_USERNAME", "").strip()
    password = os.getenv("DOCFLOW_DEMO_PASSWORD", "").strip()
    if bool(username) != bool(password):
        raise RuntimeError("DOCFLOW_DEMO_USERNAME 与 DOCFLOW_DEMO_PASSWORD 必须同时配置")
    if demo_mode and not (username and password):
        raise RuntimeError("公开 Demo 模式必须配置访问用户名和密码")
    return DemoSecurityConfig(
        demo_mode=demo_mode,
        username=username,
        password=password,
        rate_limit_per_minute=_bounded_int("DOCFLOW_RATE_LIMIT_PER_MINUTE", 60, 1, 600),
    )
