from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any

from .model_client import ModelCallError, call_chat_completion


REQUIRED_PREFIX = ("retrieve_documents", "extract_facts", "derive_task_insights", "compose_document")
REQUIRED_SUFFIX = "verify_citations"
OPTIONAL_DELIVERABLES = {"generate_risk_register", "generate_slide_outline"}
ALLOWED_PHASES = {"retrieve", "extract", "reason", "compose", "format", "verify"}
TOOL_PHASES = {
    "retrieve_documents": "retrieve",
    "extract_facts": "extract",
    "derive_task_insights": "reason",
    "compose_document": "compose",
    "generate_risk_register": "format",
    "generate_slide_outline": "format",
    "verify_citations": "verify",
}

EXTERNAL_CAPABILITIES = {
    "search_web": (
        "联网搜索",
        "网络搜索",
        "上网搜索",
        "搜索最新",
        "最新行业新闻",
        "实时新闻",
    ),
    "send_email": ("发送邮件", "发邮件", "邮件发送", "邮件通知"),
}


@dataclass(frozen=True)
class PlannerDecision:
    plan: list[dict[str, str]]
    mode: str
    fallback_reason: str = ""
    model_call: dict[str, Any] | None = None


class PlanValidationError(ValueError):
    def __init__(self, message: str, model_call: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.model_call = model_call


def required_external_capabilities(goal: str) -> list[str]:
    """Extract explicit external actions that the local Tool Registry must support."""
    return [
        capability
        for capability, phrases in EXTERNAL_CAPABILITIES.items()
        if any(phrase in goal for phrase in phrases)
    ]


def validate_goal_capabilities(goal: str, allowed_tools: set[str]) -> None:
    missing = [name for name in required_external_capabilities(goal) if name not in allowed_tools]
    if missing:
        labels = {"search_web": "联网搜索", "send_email": "发送邮件"}
        readable = "、".join(labels.get(name, name) for name in missing)
        raise PlanValidationError(f"当前 Tool Registry 缺少：{readable}；任务未执行，也不会包装为已完成。")


def build_rule_plan(goal: str) -> list[dict[str, str]]:
    """Build the deterministic baseline plan used offline and as a safe fallback."""
    plan = [
        {"phase": "retrieve", "tool_name": "retrieve_documents", "purpose": "定位与任务相关的原文证据"},
        {"phase": "extract", "tool_name": "extract_facts", "purpose": "从证据中提取可引用事实"},
        {"phase": "reason", "tool_name": "derive_task_insights", "purpose": "在证据约束下识别进展、风险、行动项与汇报重点"},
        {"phase": "compose", "tool_name": "compose_document", "purpose": "生成结构化项目周报和待确认项"},
    ]
    lowered = goal.lower()
    if any(word in goal for word in ("风险", "清单", "问题", "表", "指标", "kpi", "数据")) or "table" in lowered:
        plan.append({"phase": "format", "tool_name": "generate_risk_register", "purpose": "生成含等级、影响、负责人和截止时间的风险/行动清单"})
    if any(word in goal for word in ("汇报", "ppt", "演示", "幻灯")) or "slides" in lowered:
        plan.append({"phase": "format", "tool_name": "generate_slide_outline", "purpose": "生成带证据的三页汇报大纲"})
    plan.append({"phase": "verify", "tool_name": "verify_citations", "purpose": "校验输出引用是否可追溯"})
    return plan


def _extract_json_object(content: str) -> dict[str, Any]:
    match = re.search(r"\{[\s\S]*\}", content)
    if not match:
        raise PlanValidationError("planner did not return a JSON object")
    payload = json.loads(match.group(0))
    if not isinstance(payload, dict):
        raise PlanValidationError("planner response is not an object")
    return payload


def validate_plan(raw_plan: Any, allowed_tools: set[str]) -> list[dict[str, str]]:
    """Allow-list and dependency validation before a model-generated plan can execute."""
    if not isinstance(raw_plan, list) or not raw_plan:
        raise PlanValidationError("plan must be a non-empty list")
    if len(raw_plan) > 8:
        raise PlanValidationError("plan exceeds the eight-step execution limit")

    plan: list[dict[str, str]] = []
    for index, raw_step in enumerate(raw_plan, start=1):
        if not isinstance(raw_step, dict):
            raise PlanValidationError(f"step {index} is not an object")
        tool_name = str(raw_step.get("tool_name", "")).strip()
        phase = str(raw_step.get("phase", "")).strip()
        purpose = str(raw_step.get("purpose", "")).strip()[:120]
        if tool_name not in allowed_tools:
            raise PlanValidationError(f"step {index} uses unknown tool: {tool_name}")
        if phase not in ALLOWED_PHASES:
            raise PlanValidationError(f"step {index} uses invalid phase: {phase}")
        if not purpose:
            raise PlanValidationError(f"step {index} has no purpose")
        plan.append({"phase": phase, "tool_name": tool_name, "purpose": purpose})

    tool_names = [step["tool_name"] for step in plan]
    if tool_names[: len(REQUIRED_PREFIX)] != list(REQUIRED_PREFIX):
        raise PlanValidationError("plan must preserve retrieve -> extract -> reason -> compose dependencies")
    if tool_names[-1] != REQUIRED_SUFFIX:
        raise PlanValidationError("citation verification must be the final step")
    if len(tool_names) != len(set(tool_names)):
        raise PlanValidationError("duplicate tool calls are not allowed in the V2 workflow")
    unexpected = set(tool_names[len(REQUIRED_PREFIX) : -1]) - OPTIONAL_DELIVERABLES
    if unexpected:
        raise PlanValidationError(f"unsupported optional tools: {sorted(unexpected)}")
    return plan


def normalize_model_plan(raw_plan: Any) -> Any:
    """Derive display phases from allow-listed tools before strict validation."""
    if not isinstance(raw_plan, list):
        return raw_plan
    normalized: list[Any] = []
    for raw_step in raw_plan:
        if not isinstance(raw_step, dict):
            normalized.append(raw_step)
            continue
        step = dict(raw_step)
        tool_name = str(step.get("tool_name", "")).strip()
        if tool_name in TOOL_PHASES:
            step["phase"] = TOOL_PHASES[tool_name]
        normalized.append(step)
    return normalized


def compile_model_plan(raw_plan: Any, goal: str, allowed_tools: set[str]) -> list[dict[str, str]]:
    """Compile a model proposal onto DocFlow's mandatory, allow-listed skeleton.

    The model may decide which optional deliverables are useful and may phrase
    their purposes, but it must not be able to remove or reorder the safety
    dependencies. Missing mandatory steps are restored locally instead of
    turning harmless model omissions into a full planner fallback.
    """
    normalized = normalize_model_plan(raw_plan)
    if not isinstance(normalized, list) or not normalized:
        raise PlanValidationError("plan must be a non-empty list")
    if len(normalized) > 8:
        raise PlanValidationError("plan exceeds the eight-step execution limit")

    proposed: dict[str, dict[str, str]] = {}
    for index, raw_step in enumerate(normalized, start=1):
        if not isinstance(raw_step, dict):
            raise PlanValidationError(f"step {index} is not an object")
        tool_name = str(raw_step.get("tool_name", "")).strip()
        if tool_name not in allowed_tools:
            raise PlanValidationError(f"step {index} uses unknown tool: {tool_name}")
        if tool_name in proposed:
            raise PlanValidationError("duplicate tool calls are not allowed in the V2 workflow")
        if tool_name not in set(REQUIRED_PREFIX) | OPTIONAL_DELIVERABLES | {REQUIRED_SUFFIX}:
            raise PlanValidationError(f"unsupported optional tool: {tool_name}")
        purpose = str(raw_step.get("purpose", "")).strip()[:120]
        if not purpose:
            raise PlanValidationError(f"step {index} has no purpose")
        proposed[tool_name] = {
            "phase": TOOL_PHASES[tool_name],
            "tool_name": tool_name,
            "purpose": purpose,
        }

    baseline = build_rule_plan(goal)
    baseline_by_tool = {step["tool_name"]: step for step in baseline}
    optional_tools = {
        name for name in proposed if name in OPTIONAL_DELIVERABLES
    } | {
        step["tool_name"] for step in baseline if step["tool_name"] in OPTIONAL_DELIVERABLES
    }
    ordered_tools = [*REQUIRED_PREFIX]
    ordered_tools.extend(name for name in ("generate_risk_register", "generate_slide_outline") if name in optional_tools)
    ordered_tools.append(REQUIRED_SUFFIX)
    compiled = [proposed.get(name, baseline_by_tool.get(name)) for name in ordered_tools]
    if any(step is None for step in compiled):
        raise PlanValidationError("planner skeleton could not be compiled")
    return validate_plan(compiled, allowed_tools)


class RulePlanner:
    def create_plan(self, goal: str, tools: list[dict[str, Any]]) -> PlannerDecision:
        allowed_tools = {str(tool["name"]) for tool in tools}
        validate_goal_capabilities(goal, allowed_tools)
        return PlannerDecision(validate_plan(build_rule_plan(goal), allowed_tools), mode="rules")


class OpenAICompatiblePlanner:
    """Generate a bounded plan through an OpenAI-compatible API, then validate it locally."""

    def __init__(self, api_key: str | None = None, base_url: str | None = None, model: str | None = None) -> None:
        self.api_key = api_key if api_key is not None else os.getenv("MODEL_API_KEY", "")
        self.base_url = (base_url or os.getenv("MODEL_BASE_URL", "https://api.deepseek.com/v1")).rstrip("/")
        self.model = model or os.getenv("MODEL_NAME", "deepseek-v4-flash")

    def create_plan(self, goal: str, tools: list[dict[str, Any]]) -> PlannerDecision:
        if not self.api_key:
            raise PlanValidationError("MODEL_API_KEY is not configured")
        allowed_tools = {str(tool["name"]) for tool in tools}
        validate_goal_capabilities(goal, allowed_tools)
        payload = {
            "model": self.model,
            # V4 enables high-effort thinking by default. Planning here is a
            # bounded JSON classification task, so disable long-form reasoning
            # and cap output to keep the request inside the runtime boundary.
            "thinking": {"type": "disabled"},
            "max_tokens": 1200,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你是 DocFlow 的 Planner。只返回 JSON 对象 {plan:[...]}。每一步必须包含 phase、tool_name、purpose。"
                        "固定依赖为 retrieve_documents -> extract_facts -> derive_task_insights -> compose_document，"
                        "中间只能按目标选择 generate_risk_register、generate_slide_outline，最后必须 verify_citations。"
                        "phase 必须按工具填写：retrieve_documents=retrieve，extract_facts=extract，"
                        "derive_task_insights=reason，compose_document=compose，"
                        "generate_risk_register/generate_slide_outline=format，verify_citations=verify。"
                        "不得创造工具、不得重复工具，总步骤不超过 8。"
                    ),
                },
                {"role": "user", "content": json.dumps({"goal": goal, "tools": tools}, ensure_ascii=False)},
            ],
        }
        try:
            content, model_call = call_chat_completion(
                payload,
                api_key=self.api_key,
                base_url=self.base_url,
                stage="planner",
                timeout=20,
            )
            raw_plan = _extract_json_object(content).get("plan")
            plan = compile_model_plan(raw_plan, goal, allowed_tools)
            return PlannerDecision(plan, mode="model", model_call=model_call)
        except ModelCallError as error:
            raise PlanValidationError(str(error), model_call=error.telemetry) from error
        except (KeyError, ValueError, json.JSONDecodeError, PlanValidationError) as error:
            if "model_call" in locals():
                model_call = {**model_call, "status": "invalid_output", "error_type": type(error).__name__}
            raise PlanValidationError(str(error), model_call=locals().get("model_call")) from error


class SafePlanner:
    """Use an LLM planner when configured and always retain a deterministic fallback."""

    def __init__(self, model_planner: OpenAICompatiblePlanner | None = None, rule_planner: RulePlanner | None = None) -> None:
        self.model_planner = model_planner or OpenAICompatiblePlanner()
        self.rule_planner = rule_planner or RulePlanner()

    def create_plan(self, goal: str, tools: list[dict[str, Any]]) -> PlannerDecision:
        if not self.model_planner.api_key:
            return self.rule_planner.create_plan(goal, tools)
        try:
            return self.model_planner.create_plan(goal, tools)
        except PlanValidationError as error:
            fallback = self.rule_planner.create_plan(goal, tools)
            return PlannerDecision(
                fallback.plan,
                mode="rules_fallback",
                fallback_reason=str(error),
                model_call=error.model_call,
            )
