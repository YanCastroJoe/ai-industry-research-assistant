"""Run a controlled DocFlow model-vs-rules A/B evaluation.

The two arms use the same goal, source, Session Memory and context settings.
Only MODEL_API_KEY availability changes, and the original environment is restored
after every run. This is a small fixed-set regression, not production accuracy.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import statistics
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.docflow import AgentRuntime


DEFAULT_CASES = ROOT / "evaluation" / "docflow_model_rules_ab_cases.json"
SHARED_MEMORY = [
    {
        "id": "ab-shared-memory",
        "memory_key": "协作偏好",
        "content": "面向管理层，先展示高风险事项，再展示负责人和截止时间。",
    }
]
CONTEXT_CONFIG = {
    "audience": "项目负责人",
    "focus": "balanced",
    "evidence_limit": 12,
    "memory_enabled": True,
    "citation_policy": "strict",
}


class EvaluationError(RuntimeError):
    pass


@contextmanager
def evaluation_mode(mode: str) -> Iterator[None]:
    """Switch mode for this process only and restore the original key exactly."""
    if mode not in {"model", "rules"}:
        raise ValueError(f"unsupported mode: {mode}")
    original = os.environ.get("MODEL_API_KEY")
    if mode == "model":
        if not original:
            raise EvaluationError("MODEL_API_KEY is required for the model arm")
    else:
        os.environ.pop("MODEL_API_KEY", None)
    try:
        yield
    finally:
        if original is None:
            os.environ.pop("MODEL_API_KEY", None)
        else:
            os.environ["MODEL_API_KEY"] = original


def percentile(values: list[float], ratio: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(math.ceil(len(ordered) * ratio) - 1, 0)
    return round(ordered[index], 2)


def _item_text(item: dict[str, Any], kind: str) -> str:
    return str(item.get("risk" if kind == "risk" else "content") or "")


def _without_whitespace(value: str) -> str:
    return "".join(value.split())


def score_result(result: dict[str, Any], case: dict[str, Any], requested_mode: str) -> dict[str, Any]:
    evidence = result.get("evidence") if isinstance(result.get("evidence"), list) else []
    evidence_map = {
        str(item.get("id") or ""): str(item.get("excerpt") or "")
        for item in evidence
        if isinstance(item, dict)
    }
    insights = result.get("insights") if isinstance(result.get("insights"), dict) else {}
    artifacts = result.get("artifacts") if isinstance(result.get("artifacts"), dict) else {}
    artifact_text = "\n".join(str(value) for value in artifacts.values())
    verification = result.get("verification") if isinstance(result.get("verification"), dict) else {}
    execution = result.get("execution") if isinstance(result.get("execution"), dict) else {}

    binding_checks = []
    for requirement in case.get("bindings", []):
        kind = requirement["kind"]
        collection = insights.get("risks" if kind == "risk" else "actions", [])
        matched = None
        for item in collection if isinstance(collection, list) else []:
            if not isinstance(item, dict):
                continue
            cited_text = "\n".join(evidence_map.get(str(cid), "") for cid in item.get("evidence_ids", []))
            if requirement["evidence_fragment"] in cited_text:
                matched = item
                break
        binding_checks.append(
            {
                "kind": kind,
                "evidence_fragment": requirement["evidence_fragment"],
                "found": matched is not None,
                "owner_exact": bool(matched) and matched.get("owner") == requirement["owner"],
                "due_exact": bool(matched) and matched.get("due") == requirement["due"],
                "observed_owner": matched.get("owner") if matched else None,
                "observed_due": matched.get("due") if matched else None,
            }
        )

    cited_ids = set()
    for token in artifact_text.split("[")[1:]:
        candidate = token.split("]", 1)[0]
        if candidate.startswith("E") and candidate[1:].isdigit():
            cited_ids.add(candidate)
    citation_ids_valid = bool(cited_ids) and cited_ids.issubset(evidence_map)
    weekly = str(artifacts.get("weekly_report_markdown") or "")
    risk_before_progress = (
        "## 关键风险" in weekly
        and "## 关键进展" in weekly
        and weekly.index("## 关键风险") < weekly.index("## 关键进展")
    )
    if requested_mode == "model":
        mode_valid = bool(execution.get("model_path_complete")) and not execution.get("degraded")
    else:
        mode_valid = (
            execution.get("planner_mode") == "rules"
            and execution.get("content_mode") == "rules"
            and int(execution.get("model_call_count") or 0) == 0
        )
    compact_artifact = _without_whitespace(artifact_text)
    required_facts_preserved = all(
        _without_whitespace(str(fragment)) in compact_artifact
        for fragment in case.get("required_artifact_fragments", [])
    )

    dimensions = {
        "mode_valid": mode_valid,
        "verifier_overall": bool(verification.get("overall_passed") or verification.get("passed")),
        "content_quality": verification.get("content_quality_passed") is True,
        "citation_ids_valid": citation_ids_valid,
        "memory_applied": int(result.get("memory", {}).get("applied") or 0) == len(SHARED_MEMORY),
        "risk_first": risk_before_progress,
        "bindings_found": all(item["found"] for item in binding_checks),
        "owners_exact": all(item["owner_exact"] for item in binding_checks),
        "dates_exact": all(item["due_exact"] for item in binding_checks),
        "required_facts_preserved": required_facts_preserved,
    }
    passed_dimensions = sum(bool(value) for value in dimensions.values())
    return {
        "passed": passed_dimensions == len(dimensions),
        "gate_score": round(100 * passed_dimensions / len(dimensions), 2),
        "dimensions": dimensions,
        "bindings": binding_checks,
        "evidence_signature": [
            {"id": str(item.get("id") or ""), "excerpt": str(item.get("excerpt") or "")}
            for item in evidence
            if isinstance(item, dict)
        ],
        "artifact_characters": len(artifact_text),
        "artifact_signature": hashlib.sha256(artifact_text.encode("utf-8")).hexdigest(),
    }


def run_once(case: dict[str, Any], mode: str, repeat: int) -> dict[str, Any]:
    with evaluation_mode(mode):
        runtime = AgentRuntime()
        started = time.perf_counter()
        result = runtime.execute(
            case["goal"],
            case["source"],
            memory_context=SHARED_MEMORY,
            context_config=CONTEXT_CONFIG,
        )
        wall_ms = round((time.perf_counter() - started) * 1000, 2)
    execution = result["execution"]
    metrics = result["metrics"]
    return {
        "case_id": case["id"],
        "repeat": repeat,
        "requested_mode": mode,
        "actual_planner_mode": execution.get("planner_mode"),
        "actual_content_mode": execution.get("content_mode"),
        "degraded": bool(execution.get("degraded")),
        "fallback_reasons": execution.get("fallback_reasons", []),
        "wall_ms": wall_ms,
        "runtime_ms": float(metrics.get("elapsed_ms") or 0),
        "model_latency_ms": float(execution.get("model_latency_ms") or 0),
        "retry_count": int(metrics.get("retry_count") or 0),
        "model_calls": int(execution.get("model_call_count") or 0),
        "tokens": execution.get("model_usage", {}),
        "estimated_cost": execution.get("estimated_cost"),
        "cost_currency": execution.get("cost_currency"),
        "verification_warnings": result.get("verification", {}).get("warnings", []),
        "score": score_result(result, case, mode),
    }


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for mode in ("model", "rules"):
        arm = [row for row in rows if row["requested_mode"] == mode]
        latencies = [row["wall_ms"] for row in arm]
        model_latencies = [row["model_latency_ms"] for row in arm]
        costs = [row["estimated_cost"] for row in arm if row["estimated_cost"] is not None]
        summary[mode] = {
            "runs": len(arm),
            "passed": sum(row["score"]["passed"] for row in arm),
            "pass_rate": round(sum(row["score"]["passed"] for row in arm) / max(len(arm), 1), 4),
            "mean_gate_score": round(statistics.mean(row["score"]["gate_score"] for row in arm), 2) if arm else None,
            "degraded_runs": sum(row["degraded"] for row in arm),
            "retry_count": sum(row["retry_count"] for row in arm),
            "latency_ms": {
                "mean": round(statistics.mean(latencies), 2) if latencies else None,
                "p50": percentile(latencies, 0.5),
                "p95": percentile(latencies, 0.95),
            },
            "model_latency_ms": {
                "mean": round(statistics.mean(model_latencies), 2) if model_latencies else None,
                "p50": percentile(model_latencies, 0.5),
                "p95": percentile(model_latencies, 0.95),
            },
            "total_tokens": sum(int(row["tokens"].get("total_tokens") or 0) for row in arm),
            "estimated_cost_total": round(sum(float(value) for value in costs), 8) if len(costs) == len(arm) and arm else None,
            "cost_currency": next((row["cost_currency"] for row in arm if row["cost_currency"]), None),
        }
    return summary


def evaluate(cases: list[dict[str, Any]], repeats: int) -> dict[str, Any]:
    rows = []
    for repeat in range(1, repeats + 1):
        for case in cases:
            # Paired order is stable and every input other than mode is reused verbatim.
            rows.append(run_once(case, "model", repeat))
            rows.append(run_once(case, "rules", repeat))

    parity = []
    for repeat in range(1, repeats + 1):
        for case in cases:
            pair = [row for row in rows if row["case_id"] == case["id"] and row["repeat"] == repeat]
            model_row = next(row for row in pair if row["requested_mode"] == "model")
            rules_row = next(row for row in pair if row["requested_mode"] == "rules")
            parity.append(
                {
                    "case_id": case["id"],
                    "repeat": repeat,
                    "same_evidence": model_row["score"]["evidence_signature"] == rules_row["score"]["evidence_signature"],
                    "different_artifacts": model_row["score"]["artifact_signature"] != rules_row["score"]["artifact_signature"],
                }
            )

    summary = summarize(rows)
    paired_inputs_preserved = all(item["same_evidence"] for item in parity)
    overall_passed = (
        paired_inputs_preserved
        and summary["model"]["passed"] == summary["model"]["runs"]
        and summary["rules"]["passed"] == summary["rules"]["runs"]
    )
    return {
        "schema_version": "docflow_model_rules_ab_v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scope": "small_fixed_set_regression_not_production_accuracy_or_sla",
        "controlled_variables": {
            "same_goal_source_memory_context_and_evidence_budget": True,
            "changed_variable": "MODEL_API_KEY availability / execution mode",
            "memory": SHARED_MEMORY,
            "context_config": CONTEXT_CONFIG,
        },
        "case_count": len(cases),
        "repeats": repeats,
        "pair_count": len(parity),
        "paired_inputs_preserved": paired_inputs_preserved,
        "distinct_output_pairs": sum(item["different_artifacts"] for item in parity),
        "evidence_parity": parity,
        "summary": summary,
        "runs": rows,
        "overall_passed": overall_passed,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Controlled DocFlow model-vs-rules A/B evaluation")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.repeats < 1 or args.repeats > 10:
        raise EvaluationError("--repeats must be between 1 and 10")
    # Match the application startup convention for local runs without ever
    # printing the loaded secret. Existing process environment remains primary.
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env", override=False)
    cases = json.loads(args.cases.read_text(encoding="utf-8"))
    report = evaluate(cases, args.repeats)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "schema_version": report["schema_version"],
        "case_count": report["case_count"],
        "repeats": report["repeats"],
        "paired_inputs_preserved": report["paired_inputs_preserved"],
        "summary": report["summary"],
        "overall_passed": report["overall_passed"],
        "output": str(args.output) if args.output else None,
    }, ensure_ascii=False, indent=2))
    return 0 if report["overall_passed"] else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (EvaluationError, OSError, ValueError, json.JSONDecodeError) as error:
        print(f"[FAIL] {error}", file=sys.stderr)
        raise SystemExit(1)
