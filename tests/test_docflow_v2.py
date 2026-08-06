import sys
import tempfile
import unittest
from pathlib import Path

from app.docflow import AgentRuntime, retrieve_documents
from app.docflow_repository import DocflowRepository
from app.execution import ExecutionPolicy, RetryableToolError, ToolExecutionFailed
from app.mcp_adapter import MCPServerConfig, MCPStdioToolAdapter
from app.planning import PlanValidationError, build_rule_plan, validate_plan


ROOT = Path(__file__).resolve().parent.parent
SOURCE = """项目组本周完成需求澄清并确认三个交付里程碑。
测试环境尚未开放，可能影响联调排期。
产品负责人计划周五确认验收范围，并由研发补充接口文档。"""


class DocflowV2Tests(unittest.TestCase):
    def test_plan_validator_rejects_unknown_tool(self) -> None:
        plan = build_rule_plan("生成项目周报")
        plan.insert(-1, {"phase": "format", "tool_name": "delete_workspace", "purpose": "越权操作"})
        with self.assertRaises(PlanValidationError):
            validate_plan(plan, {step["tool_name"] for step in build_rule_plan("生成项目周报")} | {"delete_workspace"})

    def test_runtime_retries_transient_tool_and_records_attempts(self) -> None:
        runtime = AgentRuntime(default_policy=ExecutionPolicy(max_attempts=2, timeout_seconds=2, backoff_seconds=0))
        calls = {"count": 0}

        def flaky_retrieval(query: str, source_text: str):
            calls["count"] += 1
            if calls["count"] == 1:
                raise RetryableToolError("temporary retrieval outage")
            return retrieve_documents(query, source_text)

        runtime.registry.register("retrieve_documents", "flaky retrieval", flaky_retrieval)
        result = runtime.execute("生成项目周报和风险清单", SOURCE)
        retrieval_events = [event for event in result["trace"] if event["tool_name"] == "retrieve_documents"]
        self.assertEqual([event["status"] for event in retrieval_events], ["retrying", "completed"])
        self.assertEqual(result["metrics"]["retry_count"], 1)
        self.assertTrue(result["verification"]["passed"])

    def test_failed_run_resumes_from_last_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = DocflowRepository(Path(directory) / "agent.db")
            runtime = AgentRuntime(default_policy=ExecutionPolicy(max_attempts=1, timeout_seconds=2))
            calls = {"count": 0}
            original = runtime.registry._tools["generate_risk_register"].handler

            def fail_once(insights):
                calls["count"] += 1
                if calls["count"] == 1:
                    raise RetryableToolError("temporary formatter outage")
                return original(insights)

            runtime.registry.register("generate_risk_register", "risk register", fail_once)
            task = repository.create_task("周报", "生成项目周报和风险清单", SOURCE, "team-a")
            run_id = repository.create_run(task["id"])
            with self.assertRaises(ToolExecutionFailed):
                runtime.execute(
                    task["goal"],
                    task["source_text"],
                    trace_callback=lambda step: repository.record_step(run_id, step),
                    checkpoint_callback=lambda state, next_sequence: repository.save_checkpoint(run_id, state, next_sequence),
                    plan_callback=lambda plan, planner: repository.save_plan(task["id"], run_id, plan, planner["mode"]),
                )
            repository.fail_run(task["id"], run_id, "temporary formatter outage")
            resume = repository.latest_failed_checkpoint(task["id"])
            self.assertEqual(resume["next_sequence"], 5)

            failed_task = repository.get_task(task["id"])
            retry_run_id = repository.create_run(task["id"], parent_run_id=resume["run_id"])
            result = runtime.execute(
                failed_task["goal"],
                failed_task["source_text"],
                plan=failed_task["plan"],
                resume_state=resume["checkpoint"],
                start_sequence=resume["next_sequence"],
                trace_callback=lambda step: repository.record_step(retry_run_id, step),
                checkpoint_callback=lambda state, next_sequence: repository.save_checkpoint(retry_run_id, state, next_sequence),
            )
            completed = repository.complete_run(task["id"], retry_run_id, result["plan"], result)
            retry_run = completed["runs"][0]
            self.assertEqual(retry_run["parent_run_id"], run_id)
            self.assertEqual(min(step["sequence"] for step in retry_run["steps"]), 5)
            self.assertTrue(result["verification"]["passed"])

    def test_session_memory_isolated_and_recalled(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = DocflowRepository(Path(directory) / "agent.db")
            repository.add_memory("team-a", "汇报偏好", "管理层汇报优先展示风险和截止时间")
            repository.add_memory("team-b", "语言偏好", "使用英文输出")
            recalled = repository.recall_memories("team-a", "生成管理层风险汇报")
            self.assertEqual(len(recalled), 1)
            self.assertEqual(recalled[0]["memory_key"], "汇报偏好")

    def test_mcp_stdio_adapter_discovers_and_calls_real_server(self) -> None:
        runtime = AgentRuntime()
        adapter = MCPStdioToolAdapter(
            MCPServerConfig(
                name="docflow",
                command=sys.executable,
                args=["-m", "app.mcp_server"],
                cwd=str(ROOT),
            )
        )
        registered = adapter.register_tools(runtime.registry, aliases={"retrieve_project_evidence": "retrieve_documents"})
        self.assertIn("retrieve_documents", registered)
        result = runtime.execute("生成项目周报", SOURCE)
        self.assertTrue(result["verification"]["passed"])
        source = next(tool["source"] for tool in runtime.registry.describe() if tool["name"] == "retrieve_documents")
        self.assertEqual(source, "mcp:docflow")


if __name__ == "__main__":
    unittest.main()
