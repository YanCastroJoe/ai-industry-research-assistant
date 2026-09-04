import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from app.docflow import AgentRuntime, derive_task_insights, extract_facts, retrieve_documents
from app.docflow_repository import DocflowRepository
from app.execution import ExecutionPolicy, RetryableToolError, ToolExecutionFailed
from app.mcp_adapter import MCPServerConfig, MCPStdioToolAdapter
from app.planning import PlanValidationError, build_rule_plan, compile_model_plan, normalize_model_plan, validate_plan


ROOT = Path(__file__).resolve().parent.parent
SOURCE = """项目组本周完成需求澄清并确认三个交付里程碑。
测试环境尚未开放，可能影响联调排期。
产品负责人计划周五确认验收范围，并由研发补充接口文档。"""


class DocflowV2Tests(unittest.TestCase):
    def test_model_plan_phase_is_derived_from_allowlisted_tool(self) -> None:
        raw_plan = build_rule_plan("生成项目周报和风险清单")
        raw_plan[0]["phase"] = "search"
        normalized = normalize_model_plan(raw_plan)
        self.assertEqual(normalized[0]["phase"], "retrieve")
        allowed = {step["tool_name"] for step in raw_plan}
        self.assertEqual(validate_plan(normalized, allowed)[0]["phase"], "retrieve")

    def test_plan_validator_rejects_unknown_tool(self) -> None:
        plan = build_rule_plan("生成项目周报")
        plan.insert(-1, {"phase": "format", "tool_name": "delete_workspace", "purpose": "越权操作"})
        with self.assertRaises(PlanValidationError):
            validate_plan(plan, {step["tool_name"] for step in build_rule_plan("生成项目周报")} | {"delete_workspace"})

    def test_model_plan_is_compiled_onto_mandatory_safe_skeleton(self) -> None:
        proposed = [
            {"phase": "format", "tool_name": "generate_slide_outline", "purpose": "生成汇报大纲"},
            {"phase": "retrieve", "tool_name": "retrieve_documents", "purpose": "检索材料"},
        ]
        allowed = {step["tool_name"] for step in build_rule_plan("生成项目周报、风险清单和三页汇报大纲")}

        compiled = compile_model_plan(proposed, "生成项目周报、风险清单和三页汇报大纲", allowed)

        self.assertEqual(
            [step["tool_name"] for step in compiled],
            [
                "retrieve_documents",
                "extract_facts",
                "derive_task_insights",
                "compose_document",
                "generate_risk_register",
                "generate_slide_outline",
                "verify_citations",
            ],
        )
        self.assertEqual(compiled[0]["purpose"], "检索材料")

    def test_model_plan_compiler_still_rejects_unknown_tools(self) -> None:
        proposed = [{"phase": "compose", "tool_name": "delete_workspace", "purpose": "删除材料"}]
        allowed = {step["tool_name"] for step in build_rule_plan("生成项目周报")} | {"delete_workspace"}
        with self.assertRaises(PlanValidationError):
            compile_model_plan(proposed, "生成项目周报", allowed)

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

    def test_session_memory_upserts_by_key_and_recall_deduplicates_legacy_rows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = DocflowRepository(Path(directory) / "agent.db")
            first = repository.add_memory("team-a", "汇报偏好", "风险优先")
            unchanged = repository.add_memory("team-a", "汇报偏好", "风险优先")
            updated = repository.add_memory("team-a", "汇报偏好", "行动项优先")
            self.assertEqual(first["id"], unchanged["id"])
            self.assertEqual(first["id"], updated["id"])
            self.assertEqual(unchanged["operation"], "unchanged")
            self.assertEqual(updated["operation"], "updated")
            self.assertEqual(repository.list_memories("team-a")[0]["content"], "行动项优先")

            with repository._connect() as connection:
                connection.execute(
                    "INSERT INTO docflow_memories (session_id, memory_key, content) VALUES (?, ?, ?)",
                    ("team-a", "汇报偏好", "旧重复项"),
                )
            recalled = repository.recall_memories("team-a", "生成汇报")
            self.assertEqual(sum(item["memory_key"] == "汇报偏好" for item in recalled), 1)

    def test_unrelated_memory_is_not_recalled_as_recent_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = DocflowRepository(Path(directory) / "agent.db")
            repository.add_memory("team-a", "临时备注", "明天下午购买咖啡和打印纸")
            self.assertEqual(repository.recall_memories("team-a", "生成项目周报和风险清单"), [])

    def test_fallback_memory_changes_structure_without_changing_evidence(self) -> None:
        source = """本周完成知识清洗和检索联调。\n接口偶尔超时，可能影响验收。\n张浩计划9月5日前提交回归报告。"""
        risk_memory = [{"id": 1, "memory_key": "协作偏好", "content": "先展示高风险事项，再展示负责人和截止时间"}]
        progress_memory = [{"id": 2, "memory_key": "协作偏好", "content": "先展示已完成进展和里程碑，风险放在最后"}]
        with patch.dict("os.environ", {"MODEL_API_KEY": ""}):
            risk_result = AgentRuntime().execute("生成项目周报、风险清单和三页汇报大纲", source, memory_context=risk_memory)
            progress_result = AgentRuntime().execute("生成项目周报、风险清单和三页汇报大纲", source, memory_context=progress_memory)
            default_result = AgentRuntime().execute(
                "生成项目周报、风险清单和三页汇报大纲",
                source,
                memory_context=[],
                context_config={"memory_enabled": False},
            )
        self.assertEqual(risk_result["evidence"], progress_result["evidence"])
        self.assertEqual(risk_result["facts"], progress_result["facts"])
        self.assertLess(risk_result["artifacts"]["weekly_report_markdown"].index("## 关键风险"), risk_result["artifacts"]["weekly_report_markdown"].index("## 关键进展"))
        self.assertLess(progress_result["artifacts"]["weekly_report_markdown"].index("## 关键进展"), progress_result["artifacts"]["weekly_report_markdown"].index("## 关键风险"))
        self.assertLess(default_result["artifacts"]["weekly_report_markdown"].index("## 关键进展"), default_result["artifacts"]["weekly_report_markdown"].index("## 关键风险"))
        self.assertNotEqual(risk_result["artifacts"]["weekly_report_markdown"], progress_result["artifacts"]["weekly_report_markdown"])
        self.assertEqual(risk_result["memory"]["recalled"], 1)
        self.assertEqual(risk_result["memory"]["applied"], 1)
        self.assertEqual(default_result["memory"]["applied"], 0)
        artifact_text = "\n".join(risk_result["artifacts"].values())
        self.assertNotIn(risk_memory[0]["content"], artifact_text)
        self.assertTrue(all(item["excerpt"] in source for item in risk_result["evidence"]))

    def test_model_mode_records_recalled_and_applied_memory(self) -> None:
        evidence = retrieve_documents("生成项目周报和风险清单", SOURCE)
        facts = extract_facts(evidence)
        model_payload = {
            "weekly_summary": "项目已完成需求澄清。",
            "weekly_summary_evidence_ids": ["E1"],
            "progress": [{"content": facts[0]["claim"], "evidence_ids": [facts[0]["citation"]]}],
            "risks": [], "actions": [], "background": [], "milestones": [], "slide_outline": [],
        }

        class FakeResponse:
            def __enter__(self): return self
            def __exit__(self, *_): return False
            def read(self):
                import json
                return json.dumps({"choices": [{"message": {"content": json.dumps(model_payload, ensure_ascii=False)}}]}, ensure_ascii=False).encode("utf-8")

        with patch.dict("os.environ", {"MODEL_API_KEY": "test-key"}), patch("urllib.request.urlopen", return_value=FakeResponse()):
            insights = derive_task_insights(
                "生成项目周报和风险清单",
                facts,
                [{"id": 1, "memory_key": "协作偏好", "content": "风险优先"}],
            )
        self.assertEqual(insights["mode"], "model")
        self.assertEqual(insights["memory_application"]["recalled"], 1)
        self.assertEqual(insights["memory_application"]["applied"], 1)

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
