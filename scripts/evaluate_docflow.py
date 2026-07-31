"""Run the fixed Agent regression set without calling an external model."""

from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.docflow import AgentRuntime


CASES = ROOT / "tests" / "fixtures" / "docflow_eval_cases.json"
SOURCE = """项目组完成需求澄清并确认三个交付里程碑。
测试环境尚未开放，可能影响联调排期。
产品负责人计划周五确认验收范围，并由研发补充接口文档。"""


def main() -> int:
    cases = json.loads(CASES.read_text(encoding="utf-8"))
    runtime = AgentRuntime()
    failures: list[str] = []
    for case in cases:
        result = runtime.execute(case["goal"], SOURCE)
        actual_tools = {step["tool_name"] for step in result["plan"]}
        missing = set(case["required_tools"]) - actual_tools
        if missing or not result["verification"]["passed"]:
            failures.append(f"{case['id']}: missing={sorted(missing)}, verified={result['verification']['passed']}")
    print(f"cases={len(cases)} passed={len(cases) - len(failures)} failed={len(failures)}")
    for failure in failures:
        print(f"- {failure}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
