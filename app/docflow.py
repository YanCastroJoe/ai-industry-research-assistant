from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .execution import ExecutionPolicy, ToolExecutionFailed, invoke_with_policy
from .planning import SafePlanner, build_rule_plan


DEFAULT_CONTEXT_CONFIG = {
    "audience": "项目团队",
    "focus": "balanced",
    "evidence_limit": 12,
    "memory_enabled": True,
    "citation_policy": "strict",
}

FOCUS_LABELS = {
    "balanced": "均衡呈现",
    "risk": "风险优先",
    "progress": "进展优先",
    "actions": "行动项优先",
}


def normalize_context_config(value: dict[str, Any] | None) -> dict[str, Any]:
    config = {**DEFAULT_CONTEXT_CONFIG, **(value or {})}
    focus = str(config.get("focus", "balanced"))
    config["focus"] = focus if focus in FOCUS_LABELS else "balanced"
    config["audience"] = str(config.get("audience", "项目团队")).strip()[:100] or "项目团队"
    config["evidence_limit"] = max(4, min(int(config.get("evidence_limit", 12)), 12))
    config["memory_enabled"] = bool(config.get("memory_enabled", True))
    config["citation_policy"] = "strict" if config.get("citation_policy") == "strict" else "standard"
    return config


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    handler: Callable[..., Any]
    input_schema: dict[str, Any]
    source: str = "local"


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

    def register(
        self,
        name: str,
        description: str,
        handler: Callable[..., Any],
        input_schema: dict[str, Any] | None = None,
        source: str = "local",
    ) -> None:
        self._tools[name] = ToolDefinition(
            name=name,
            description=description,
            handler=handler,
            input_schema=input_schema or {"type": "object", "properties": {}},
            source=source,
        )

    def call(self, name: str, **kwargs: Any) -> Any:
        if name not in self._tools:
            raise KeyError(f"Unknown tool: {name}")
        return self._tools[name].handler(**kwargs)

    def names(self) -> list[str]:
        return list(self._tools)

    def input_schema(self, name: str) -> dict[str, Any]:
        if name not in self._tools:
            raise KeyError(name)
        return self._tools[name].input_schema

    def describe(self) -> list[dict[str, Any]]:
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.input_schema,
                "source": tool.source,
            }
            for tool in self._tools.values()
        ]


def build_plan(goal: str) -> list[dict[str, str]]:
    """Create an inspectable plan; deliverable tools are chosen from the user's goal."""
    return build_rule_plan(goal)


def _sentences(text: str) -> list[str]:
    normalized = re.sub(r"\s+", " ", text).strip()
    chunks = re.split(r"(?<=[。！？.!?；;])\s*|\n+", normalized)
    return [chunk.strip(" -•\t") for chunk in chunks if len(chunk.strip(" -•\t")) >= 12]


def _query_keywords(query: str) -> set[str]:
    """Build useful Chinese search terms instead of treating a whole request as one term."""
    known_terms = {
        "周报", "进展", "风险", "问题", "行动", "清单", "里程碑", "计划", "截止", "负责人",
        "验收", "上线", "发布", "接口", "依赖", "审核", "安全", "测试", "环境", "版本",
        "数据", "指标", "召回", "客户", "汇报", "大纲", "PPT", "表格",
    }
    terms = {term for term in known_terms if term.lower() in query.lower()}
    terms.update(token.lower() for token in re.findall(r"[A-Za-z0-9]{2,}", query))
    return terms


def _candidate_categories(text: str) -> set[str]:
    rules = {
        "risk": ("风险", "问题", "尚未", "未完成", "未通过", "可能", "延期", "不一致", "缺失", "阻塞", "审核"),
        "action": ("计划", "确认", "补充", "提供", "推进", "跟进", "下周", "负责", "修复", "优化"),
        "milestone": ("上线", "发布", "验收", "截止", "目标", "完成", "交付", "日期", "月", "日"),
        "metric": ("率", "个", "份", "%", "Top-", "指标", "数据"),
    }
    return {name for name, words in rules.items() if any(word.lower() in text.lower() for word in words)}


def retrieve_documents(
    query: str,
    source_text: str,
    evidence_limit: int = 12,
    focus: str = "balanced",
) -> list[dict[str, str]]:
    """Retrieve relevant evidence while reserving space for risks, actions and milestones."""
    candidates = _sentences(source_text) or [source_text[:500]]
    terms = _query_keywords(query)
    scored = []
    for index, text in enumerate(candidates):
        categories = _candidate_categories(text)
        relevance = sum(term in text.lower() for term in terms)
        scored.append({"index": index, "text": text, "categories": categories, "score": relevance * 10 + len(categories)})
    ranked = sorted(scored, key=lambda item: (-item["score"], item["index"]))
    selected: list[dict[str, Any]] = []
    selected_indexes: set[int] = set()

    def reserve(category: str, quota: int) -> None:
        for item in ranked:
            if len([picked for picked in selected if category in picked["categories"]]) >= quota:
                break
            if category in item["categories"] and item["index"] not in selected_indexes:
                selected.append(item)
                selected_indexes.add(item["index"])

    evidence_limit = max(4, min(int(evidence_limit), 12))
    quotas = {
        "balanced": {"risk": 3, "action": 3, "milestone": 2, "metric": 2},
        "risk": {"risk": 5, "action": 2, "milestone": 1, "metric": 1},
        "progress": {"risk": 2, "action": 2, "milestone": 3, "metric": 3},
        "actions": {"risk": 2, "action": 5, "milestone": 2, "metric": 1},
    }.get(focus, {"risk": 3, "action": 3, "milestone": 2, "metric": 2})
    # Reserve independent evidence by delivery focus, then fill remaining budget by relevance.
    for category, quota in quotas.items():
        reserve(category, min(quota, evidence_limit))
    for item in ranked:
        if len(selected) >= evidence_limit:
            break
        if item["index"] not in selected_indexes:
            selected.append(item)
            selected_indexes.add(item["index"])
    selected = sorted(selected[:evidence_limit], key=lambda item: (-item["score"], item["index"]))
    return [
        {"id": f"E{evidence_index}", "excerpt": item["text"][:360], "source_location": f"材料片段 {item['index'] + 1}"}
        for evidence_index, item in enumerate(selected, start=1)
    ]


def extract_facts(evidence: list[dict[str, str]]) -> list[dict[str, str]]:
    return [
        {"fact_id": f"F{index}", "claim": item["excerpt"], "citation": item["id"], "source_location": item["source_location"]}
        for index, item in enumerate(evidence, start=1)
    ]


def _extract_json(content: str) -> dict[str, Any]:
    match = re.search(r"\{[\s\S]*\}", content)
    if not match:
        raise ValueError("Model did not return a JSON object")
    payload = json.loads(match.group(0))
    if not isinstance(payload, dict):
        raise ValueError("Model response is not an object")
    return payload


def _citation_ids(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and re.fullmatch(r"E\d+", item)]


def _fallback_insights(goal: str, facts: list[dict[str, str]]) -> dict[str, Any]:
    progress = []
    risks = []
    actions = []
    risk_words = ("风险", "尚未", "未", "可能", "延期", "不一致", "审核", "待确认", "旧版本")
    action_words = ("计划", "完成", "确认", "补充", "组织", "提供", "下周")
    for fact in facts:
        text = fact["claim"]
        item = {"content": text, "evidence_ids": [fact["citation"]]}
        if any(word in text for word in risk_words):
            risks.append({
                "risk": text,
                "level": "高" if any(word in text for word in ("延期", "尚未", "未批准")) else "中",
                "impact": "可能影响上线质量、联调排期或信息准确性。",
                "owner": "待项目负责人确认",
                "due": "待确认",
                "evidence_ids": [fact["citation"]],
            })
        elif any(word in text for word in action_words):
            actions.append({**item, "owner": "待确认", "due": "待确认"})
        else:
            progress.append(item)
    if not progress:
        progress = [{"content": fact["claim"], "evidence_ids": [fact["citation"]]} for fact in facts[:3]]
    if not risks:
        risks = [{"risk": "材料未披露明确风险，需由项目负责人补充确认。", "level": "待确认", "impact": "无法判断风险暴露。", "owner": "项目负责人", "due": "待确认", "evidence_ids": []}]
    return {
        "mode": "rules",
        "weekly_summary": "当前材料已形成项目背景、进展和待确认事项，但仍需在上线前完成风险闭环。",
        "weekly_summary_evidence_ids": [fact["citation"] for fact in facts[:5]],
        "progress": progress[:4],
        "milestones": [],
        "risks": risks[:5],
        "actions": actions[:5],
        "slide_outline": [],
    }


def _normalize_insights(raw: dict[str, Any], facts: list[dict[str, str]]) -> dict[str, Any]:
    allowed = {fact["citation"] for fact in facts}

    def normalize_items(value: Any, fields: tuple[str, ...]) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []
        normalized = []
        for item in value[:6]:
            if not isinstance(item, dict):
                continue
            result = {field: str(item.get(field, "")).strip() for field in fields}
            citations = [citation for citation in _citation_ids(item.get("evidence_ids")) if citation in allowed]
            if any(result.values()) or citations:
                result["evidence_ids"] = citations
                normalized.append(result)
        return normalized

    def deduplicate_risks(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Merge near-identical model risks while retaining every cited source."""
        unique: list[dict[str, Any]] = []
        for item in items:
            text = re.sub(r"\s+", "", item["risk"])
            current_grams = {text[position:position + 2] for position in range(max(len(text) - 1, 0))}
            duplicate = None
            for kept in unique:
                previous = re.sub(r"\s+", "", kept["risk"])
                previous_grams = {previous[position:position + 2] for position in range(max(len(previous) - 1, 0))}
                union = current_grams | previous_grams
                similarity = len(current_grams & previous_grams) / len(union) if union else 0
                risk_topics = ("版本", "旧版本", "政策", "接口", "审核", "环境", "账号", "测试", "排期", "文档", "数据")
                shared_topics = sum(topic in text and topic in previous for topic in risk_topics)
                if similarity >= 0.5 or (similarity >= 0.2 and shared_topics >= 2):
                    duplicate = kept
                    break
            if duplicate:
                duplicate["evidence_ids"] = list(dict.fromkeys(duplicate["evidence_ids"] + item["evidence_ids"]))
            else:
                unique.append(item)
        return unique[:5]

    insights = {
        "mode": "model",
        "weekly_summary": str(raw.get("weekly_summary", "")).strip(),
        "weekly_summary_evidence_ids": [citation for citation in _citation_ids(raw.get("weekly_summary_evidence_ids")) if citation in allowed],
        "progress": normalize_items(raw.get("progress"), ("content",)),
        "milestones": normalize_items(raw.get("milestones"), ("content", "due")),
        "risks": deduplicate_risks(normalize_items(raw.get("risks"), ("risk", "level", "impact", "owner", "due"))),
        "actions": normalize_items(raw.get("actions"), ("content", "owner", "due")),
        "slide_outline": normalize_items(raw.get("slide_outline"), ("title", "content")),
    }
    fallback = _fallback_insights("", facts)
    for key in ("progress", "risks", "actions"):
        if not insights[key]:
            insights[key] = fallback[key]
    if not insights["weekly_summary"]:
        insights["weekly_summary"] = fallback["weekly_summary"]
    if not insights["weekly_summary_evidence_ids"]:
        insights["weekly_summary_evidence_ids"] = fallback["weekly_summary_evidence_ids"]
    return insights


def derive_task_insights(
    goal: str,
    facts: list[dict[str, str]],
    memory_context: list[dict[str, str]] | None = None,
    context_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """One guarded LLM call; memory guides preferences but never acts as factual evidence."""
    memory_context = memory_context or []
    context_config = normalize_context_config(context_config)
    api_key = os.getenv("MODEL_API_KEY", "")
    if not api_key:
        fallback = _fallback_insights(goal, facts)
        fallback["memory_used"] = len(memory_context)
        return fallback
    evidence_payload = [{"id": fact["citation"], "fact": fact["claim"]} for fact in facts]
    system_prompt = """你是企业协作文档 Agent 的任务理解模块。仅能依据输入证据，不得补充外部事实。
请严格返回 JSON 对象，字段为 weekly_summary、weekly_summary_evidence_ids、progress、milestones、risks、actions、slide_outline。
progress 的元素为 {content,evidence_ids}；milestones 为 {content,due,evidence_ids}；
risks 为 {risk,level(高|中|低|待确认),impact,owner,due,evidence_ids}；
weekly_summary_evidence_ids 为支撑周报摘要的证据编号数组；actions 为 {content,owner,due,evidence_ids}；slide_outline 为 3 项 {title,content,evidence_ids}。
每个 evidence_ids 只能引用输入中存在的 E 编号；材料未出现的负责人或日期必须写“待确认”。
session_preferences 只用于输出结构和表达偏好，不能作为事实或引用来源。
如果用户要求风险清单，优先识别阻塞项、版本/质量、接口依赖、安全审核和排期风险；不要把项目目标误写成风险。"""
    system_prompt += " 当用户要求风险清单时，应尽量列出有独立根因的风险，避免把同一风险拆成重复条目；风险证据中已经出现负责人或日期时必须保留。"
    payload = {
        "model": os.getenv("MODEL_NAME", "deepseek-chat"),
        "temperature": 0.1,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "goal": goal,
                        "evidence": evidence_payload,
                        "session_preferences": memory_context,
                        "delivery_context": {
                            "audience": context_config["audience"],
                            "focus": FOCUS_LABELS[context_config["focus"]],
                            "citation_policy": context_config["citation_policy"],
                        },
                    },
                    ensure_ascii=False,
                ),
            },
        ],
    }
    base_url = os.getenv("MODEL_BASE_URL", "https://api.deepseek.com/v1").rstrip("/")
    request = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            response_payload = json.loads(response.read().decode("utf-8"))
        content = response_payload["choices"][0]["message"]["content"]
        insights = _normalize_insights(_extract_json(content), facts)
        insights["memory_used"] = len(memory_context)
        return insights
    except (urllib.error.URLError, urllib.error.HTTPError, KeyError, ValueError, json.JSONDecodeError):
        fallback = _fallback_insights(goal, facts)
        fallback["mode"] = "rules_fallback"
        fallback["memory_used"] = len(memory_context)
        return fallback


def _references(citations: list[str]) -> str:
    return " ".join(f"[{citation}]" for citation in citations)


def compose_document(goal: str, insights: dict[str, Any]) -> str:
    lines = ["# 项目周报", "", f"> 协作目标：{goal}", "", "## 本周摘要", f"{insights['weekly_summary']} {_references(insights['weekly_summary_evidence_ids'])}", "", "## 关键进展"]
    lines.extend(f"- {item['content']} {_references(item['evidence_ids'])}" for item in insights["progress"])
    if insights["milestones"]:
        lines.extend(["", "## 关键里程碑"])
        lines.extend(f"- {item['content']}；时间：{item['due']} {_references(item['evidence_ids'])}" for item in insights["milestones"])
    if insights["actions"]:
        lines.extend(["", "## 下周行动项"])
        lines.extend(f"- {item['content']}；负责人：{item['owner']}；截止：{item['due']} {_references(item['evidence_ids'])}" for item in insights["actions"])
    lines.extend(["", "## 待确认项", "- 未被当前材料直接支持的负责人、日期或决策结论均标记为“待确认”，需人工审核后对外使用。"])
    return "\n".join(lines)


def generate_risk_register(insights: dict[str, Any]) -> str:
    lines = ["## 风险与行动清单", "", "| 风险 | 等级 | 影响 | 负责人 | 截止时间 | 证据 |", "| --- | --- | --- | --- | --- | --- |"]
    for risk in insights["risks"]:
        lines.append(
            f"| {risk['risk']} | {risk['level']} | {risk['impact']} | {risk['owner']} | {risk['due']} | {_references(risk['evidence_ids']) or '待确认'} |"
        )
    return "\n".join(lines)


def generate_slide_outline(goal: str, insights: dict[str, Any]) -> str:
    slides = insights.get("slide_outline", [])
    if not slides:
        slides = [
            {"title": "背景与目标", "content": goal, "evidence_ids": []},
            {"title": "本周进展", "content": insights["progress"][0]["content"], "evidence_ids": insights["progress"][0]["evidence_ids"]},
            {"title": "风险与下一步", "content": insights["risks"][0]["risk"], "evidence_ids": insights["risks"][0]["evidence_ids"]},
        ]
    lines = ["## 三页汇报大纲", ""]
    for index, slide in enumerate(slides[:3], start=1):
        lines.append(f"{index}. {slide['title']}：{slide['content']} {_references(slide['evidence_ids'])}")
    return "\n".join(lines)


def verify_citations(artifacts: dict[str, str], evidence: list[dict[str, str]]) -> dict[str, Any]:
    allowed = {item["id"] for item in evidence}
    references = re.findall(r"\[(E\d+)\]", "\n".join(artifacts.values()))
    invalid = sorted(set(reference for reference in references if reference not in allowed))
    citation_coverage = bool(references) and not invalid
    warnings: list[str] = []
    risk_register = artifacts.get("risk_register_markdown", "")
    slide_outline = artifacts.get("slide_outline_markdown", "")
    overclaim_terms = ("均已明确", "全部明确", "均已落实", "责任人和截止时间已明确", "负责人和截止时间已明确")
    if "待确认" in risk_register and any(term in slide_outline for term in overclaim_terms):
        warnings.append("风险清单仍含“待确认”的负责人或截止时间，但汇报大纲将其表述为已全部明确；请人工核对后再导出。")
    return {
        "passed": citation_coverage,
        "citation_coverage": citation_coverage,
        "consistency_passed": not warnings,
        "warnings": warnings,
        "reference_count": len(references),
        "invalid_citations": invalid,
    }


class AgentRuntime:
    def __init__(
        self,
        registry: ToolRegistry | None = None,
        planner: Any | None = None,
        default_policy: ExecutionPolicy | None = None,
        tool_policies: dict[str, ExecutionPolicy] | None = None,
    ) -> None:
        self.registry = registry or ToolRegistry()
        if not self.registry.names():
            object_schema = {"type": "object", "properties": {}}
            self.registry.register(
                "retrieve_documents",
                "检索工作区原文证据",
                retrieve_documents,
                {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "source_text": {"type": "string"},
                        "evidence_limit": {"type": "integer", "minimum": 4, "maximum": 12},
                        "focus": {"type": "string", "enum": list(FOCUS_LABELS)},
                    },
                    "required": ["query", "source_text"],
                },
            )
            self.registry.register("extract_facts", "抽取可引用事实", extract_facts, object_schema)
            self.registry.register("derive_task_insights", "受证据约束地理解交付目标", derive_task_insights, object_schema)
            self.registry.register("compose_document", "生成结构化项目周报", compose_document, object_schema)
            self.registry.register("generate_risk_register", "生成风险与行动清单", generate_risk_register, object_schema)
            self.registry.register("generate_slide_outline", "生成三页汇报大纲", generate_slide_outline, object_schema)
            self.registry.register("verify_citations", "校验输出中的证据引用", verify_citations, object_schema)
        self.planner = planner or SafePlanner()
        self.default_policy = default_policy or ExecutionPolicy()
        self.tool_policies = tool_policies or {}

    def execute(
        self,
        goal: str,
        source_text: str,
        trace_callback: Callable[[dict], None] | None = None,
        checkpoint_callback: Callable[[dict[str, Any], int], None] | None = None,
        plan_callback: Callable[[list[dict[str, str]], dict[str, str]], None] | None = None,
        *,
        plan: list[dict[str, str]] | None = None,
        resume_state: dict[str, Any] | None = None,
        start_sequence: int = 1,
        memory_context: list[dict[str, str]] | None = None,
        context_config: dict[str, Any] | None = None,
    ) -> dict:
        started_run = time.perf_counter()
        context_config = normalize_context_config(context_config)
        if plan is None:
            planner_decision = self.planner.create_plan(goal, self.registry.describe())
            plan = planner_decision.plan
            planner_mode = planner_decision.mode
            planner_fallback_reason = planner_decision.fallback_reason
        else:
            planner_mode = "resume"
            planner_fallback_reason = ""
        if plan_callback:
            plan_callback(plan, {"mode": planner_mode, "fallback_reason": planner_fallback_reason})
        state: dict[str, Any] = {
            "goal": goal,
            "source_text": source_text,
            "memory_context": memory_context or [],
            "context_config": context_config,
            "evidence": [],
            "facts": [],
            "insights": {},
            "artifacts": {},
        }
        if resume_state:
            state.update(resume_state)
            state["goal"] = goal
            state["source_text"] = source_text
            state["memory_context"] = memory_context or state.get("memory_context", [])
            state["context_config"] = context_config
        trace: list[dict] = []
        for sequence, step in enumerate(plan, start=1):
            if sequence < start_sequence:
                continue
            tool_name = step["tool_name"]
            tool_input = self._tool_input(tool_name, state)
            try:
                output, attempts = invoke_with_policy(
                    lambda: self.registry.call(tool_name, **tool_input),
                    self.tool_policies.get(tool_name, self.default_policy),
                )
                self._save_output(tool_name, output, state)
                for attempt in attempts:
                    event = self._event(sequence, step, tool_input, attempt, output if attempt["status"] == "completed" else None)
                    trace.append(event)
                    if trace_callback:
                        trace_callback(event)
                if checkpoint_callback:
                    checkpoint_callback(self._checkpoint_state(state), sequence + 1)
            except ToolExecutionFailed as error:
                for attempt in error.attempts:
                    event = self._event(sequence, step, tool_input, attempt, None)
                    trace.append(event)
                    if trace_callback:
                        trace_callback(event)
                raise
        completed_steps = len({event["sequence"] for event in trace if event["status"] == "completed"})
        retry_count = sum(event["status"] == "retrying" for event in trace)
        return {
            "plan": plan,
            "planner": {"mode": planner_mode, "fallback_reason": planner_fallback_reason},
            "memory": {"items_used": len(state.get("memory_context", []))},
            "context": self._context_manifest(state),
            "evidence": state["evidence"],
            "facts": state["facts"],
            "insights": state["insights"],
            "artifacts": state["artifacts"],
            "verification": state["verification"],
            "trace": trace,
            "metrics": {
                "elapsed_ms": round((time.perf_counter() - started_run) * 1000),
                "executed_steps": completed_steps,
                "attempts": len(trace),
                "retry_count": retry_count,
                "tool_success_rate": round(completed_steps / max(len(plan) - start_sequence + 1, 1), 4),
            },
        }

    @staticmethod
    def _event(
        sequence: int,
        step: dict[str, str],
        tool_input: dict[str, Any],
        attempt: dict[str, Any],
        output: Any | None,
    ) -> dict[str, Any]:
        return {
            "sequence": sequence,
            "attempt": attempt["attempt"],
            "phase": step["phase"],
            "tool_name": step["tool_name"],
            "status": attempt["status"],
            "input": tool_input,
            "output": AgentRuntime._trace_output(output) if output is not None else {},
            "error": attempt.get("error", ""),
            "error_type": attempt.get("error_type", ""),
            "retryable": attempt.get("retryable", False),
            "elapsed_ms": attempt["elapsed_ms"],
        }

    @staticmethod
    def _checkpoint_state(state: dict[str, Any]) -> dict[str, Any]:
        return {
            key: value
            for key, value in state.items()
            if key in {"memory_context", "context_config", "evidence", "facts", "insights", "artifacts", "verification"}
        }

    def _tool_input(self, tool_name: str, state: dict[str, Any]) -> dict[str, Any]:
        if tool_name == "retrieve_documents":
            config = state.get("context_config", DEFAULT_CONTEXT_CONFIG)
            payload = {
                "query": state["goal"],
                "source_text": state["source_text"],
                "evidence_limit": config["evidence_limit"],
                "focus": config["focus"],
            }
            allowed = set(self.registry.input_schema(tool_name).get("properties", {}))
            if not allowed:
                return {"query": payload["query"], "source_text": payload["source_text"]}
            return {key: value for key, value in payload.items() if key in allowed}
        if tool_name == "extract_facts":
            return {"evidence": state["evidence"]}
        if tool_name == "derive_task_insights":
            return {
                "goal": state["goal"],
                "facts": state["facts"],
                "memory_context": state.get("memory_context", []),
                "context_config": state.get("context_config", DEFAULT_CONTEXT_CONFIG),
            }
        if tool_name == "compose_document":
            return {"goal": state["goal"], "insights": state["insights"]}
        if tool_name in {"generate_risk_register", "generate_slide_outline"}:
            return {"insights": state["insights"]} if tool_name == "generate_risk_register" else {"goal": state["goal"], "insights": state["insights"]}
        if tool_name == "verify_citations":
            return {"artifacts": state["artifacts"], "evidence": state["evidence"]}
        raise KeyError(tool_name)

    @staticmethod
    def _save_output(tool_name: str, output: Any, state: dict[str, Any]) -> None:
        if tool_name == "retrieve_documents":
            state["evidence"] = output
        elif tool_name == "extract_facts":
            state["facts"] = output
        elif tool_name == "derive_task_insights":
            state["insights"] = output
        elif tool_name == "compose_document":
            state["artifacts"]["weekly_report_markdown"] = output
        elif tool_name == "generate_risk_register":
            state["artifacts"]["risk_register_markdown"] = output
        elif tool_name == "generate_slide_outline":
            state["artifacts"]["slide_outline_markdown"] = output
        elif tool_name == "verify_citations":
            state["verification"] = output

    @staticmethod
    def _trace_output(output: Any) -> dict[str, Any]:
        if isinstance(output, list):
            return {"item_count": len(output), "preview": output[:2]}
        if isinstance(output, str):
            return {"characters": len(output), "preview": output[:220]}
        if isinstance(output, dict):
            summary = {key: value for key, value in output.items() if key in {"mode", "weekly_summary", "reference_count", "passed", "consistency_passed", "warnings", "invalid_citations"}}
            return summary or output
        return {"result": str(output)}

    @staticmethod
    def _context_manifest(state: dict[str, Any]) -> dict[str, Any]:
        config = normalize_context_config(state.get("context_config"))
        memories = state.get("memory_context", [])
        evidence = state.get("evidence", [])
        facts = state.get("facts", [])
        layers = [
            {
                "key": "instruction",
                "label": "任务指令",
                "items": 1,
                "characters": len(state.get("goal", "")),
                "role": "定义交付目标与输出边界",
            },
            {
                "key": "source",
                "label": "工作区材料",
                "items": len(_sentences(state.get("source_text", ""))),
                "characters": len(state.get("source_text", "")),
                "role": "仅作为检索语料，不直接进入最终结论",
            },
            {
                "key": "memory",
                "label": "Session Memory",
                "items": len(memories),
                "characters": sum(len(str(item.get("content", ""))) for item in memories),
                "role": "只影响表达偏好，不作为事实证据",
            },
            {
                "key": "evidence",
                "label": "Evidence Context",
                "items": len(evidence),
                "characters": sum(len(str(item.get("excerpt", ""))) for item in evidence),
                "role": "提供可追溯的事实与引用编号",
            },
        ]
        return {
            "strategy": "layered_context_v1",
            "audience": config["audience"],
            "focus": FOCUS_LABELS[config["focus"]],
            "evidence_budget": config["evidence_limit"],
            "citation_policy": config["citation_policy"],
            "layers": layers,
            "facts_created": len(facts),
            "policies": [
                "任务指令、材料、记忆与证据分层注入",
                "Session Memory 不作为事实或引用来源",
                "所有生成结论只能引用 Evidence ID",
                "缺少材料支持的负责人、日期和结论标记为待确认",
            ],
        }
