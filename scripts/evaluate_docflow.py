"""Evaluate planning, citation quality, latency and transient-failure recovery offline."""

from __future__ import annotations

import json
import statistics
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.docflow import AgentRuntime, retrieve_documents
from app.execution import ExecutionPolicy, RetryableToolError


CASES = ROOT / "tests" / "fixtures" / "docflow_eval_cases.json"
SOURCE = """项目组完成需求澄清并确认三个交付里程碑。
测试环境尚未开放，可能影响联调排期。
产品负责人计划周五确认验收范围，并由研发补充接口文档。"""
OPTIONAL_TOOLS = {"generate_risk_register", "generate_slide_outline"}


def percentile(values: list[float], ratio: float) -> float:
    ordered = sorted(values)
    index = min(round((len(ordered) - 1) * ratio), len(ordered) - 1)
    return round(ordered[index], 2)


def evaluate_fixed_set() -> dict:
    cases = json.loads(CASES.read_text(encoding="utf-8"))
    runtime = AgentRuntime()
    failures: list[dict] = []
    latencies: list[float] = []
    citation_passes = 0
    content_quality_passes = 0
    true_positive = false_positive = false_negative = 0
    planner_modes: dict[str, int] = {}

    for case in cases:
        started = time.perf_counter()
        result = runtime.execute(case["goal"], SOURCE)
        latencies.append((time.perf_counter() - started) * 1000)
        actual_tools = {step["tool_name"] for step in result["plan"]}
        required_tools = set(case["required_tools"])
        missing = required_tools - actual_tools
        expected_optional = required_tools & OPTIONAL_TOOLS
        actual_optional = actual_tools & OPTIONAL_TOOLS
        true_positive += len(expected_optional & actual_optional)
        false_positive += len(actual_optional - expected_optional)
        false_negative += len(expected_optional - actual_optional)
        citation_passes += int(result["verification"]["passed"])
        content_quality_passes += int(result["verification"].get("content_quality_passed", False))
        mode = result["planner"]["mode"]
        planner_modes[mode] = planner_modes.get(mode, 0) + 1
        if missing or not result["verification"]["passed"] or not result["verification"].get("content_quality_passed", False):
            failures.append(
                {
                    "case_id": case["id"],
                    "missing_tools": sorted(missing),
                    "citation_passed": result["verification"]["passed"],
                    "content_quality_passed": result["verification"].get("content_quality_passed", False),
                }
            )

    precision = true_positive / max(true_positive + false_positive, 1)
    recall = true_positive / max(true_positive + false_negative, 1)
    return {
        "dataset": "docflow_eval_cases_v1",
        "case_count": len(cases),
        "passed": len(cases) - len(failures),
        "failed": len(failures),
        "optional_tool_precision": round(precision, 4),
        "optional_tool_recall": round(recall, 4),
        "citation_pass_rate": round(citation_passes / len(cases), 4),
        "content_quality_pass_rate": round(content_quality_passes / len(cases), 4),
        "latency_ms": {
            "mean": round(statistics.mean(latencies), 2),
            "p50": percentile(latencies, 0.5),
            "p95": percentile(latencies, 0.95),
        },
        "planner_modes": planner_modes,
        "failures": failures,
    }


def evaluate_recovery() -> dict:
    runtime = AgentRuntime(default_policy=ExecutionPolicy(max_attempts=2, timeout_seconds=2, backoff_seconds=0))
    calls = {"count": 0}

    def flaky_retrieval(query: str, source_text: str):
        calls["count"] += 1
        if calls["count"] == 1:
            raise RetryableToolError("synthetic transient outage")
        return retrieve_documents(query, source_text)

    runtime.registry.register("retrieve_documents", "synthetic flaky retrieval", flaky_retrieval)
    result = runtime.execute("生成项目周报和风险清单", SOURCE)
    return {
        "scenario": "retrieval_fails_once_then_recovers",
        "passed": result["verification"]["passed"] and result["metrics"]["retry_count"] == 1,
        "attempts": calls["count"],
        "retry_count": result["metrics"]["retry_count"],
        "citation_passed": result["verification"]["passed"],
    }


def main() -> int:
    report = {"fixed_set": evaluate_fixed_set(), "recovery": evaluate_recovery()}
    report["overall_passed"] = report["fixed_set"]["failed"] == 0 and report["recovery"]["passed"]
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["overall_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
