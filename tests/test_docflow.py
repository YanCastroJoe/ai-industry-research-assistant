import tempfile
import unittest
from pathlib import Path

from app.docflow import AgentRuntime, _normalize_insights, build_plan, retrieve_documents, verify_citations
from app.docflow_repository import DocflowRepository


SOURCE = """项目组本周完成需求澄清并确认三个交付里程碑。\n当前风险是测试环境尚未开放，可能影响联调排期。\n产品负责人计划在周五确认验收范围，并由研发补充接口文档。"""


class DocflowTests(unittest.TestCase):
    def test_planner_selects_requested_tools(self) -> None:
        tool_names = [step["tool_name"] for step in build_plan("生成本周项目周报、风险清单和三页汇报大纲")]
        self.assertIn("retrieve_documents", tool_names)
        self.assertIn("generate_risk_register", tool_names)
        self.assertIn("generate_slide_outline", tool_names)
        self.assertEqual(tool_names[-1], "verify_citations")

    def test_runtime_produces_cited_artifacts_and_trace(self) -> None:
        result = AgentRuntime().execute("生成本周项目周报、风险清单和三页汇报大纲", SOURCE)
        self.assertTrue(result["verification"]["passed"])
        self.assertIn("[E1]", result["artifacts"]["weekly_report_markdown"])
        self.assertIn("[E1]", result["artifacts"]["weekly_report_markdown"].split("## 关键进展", 1)[0])
        self.assertIn("risk_register_markdown", result["artifacts"])
        self.assertIn("slide_outline_markdown", result["artifacts"])
        self.assertIn("风险与行动清单", result["artifacts"]["risk_register_markdown"])
        self.assertIn("负责人", result["artifacts"]["risk_register_markdown"])
        self.assertTrue(all("owner" in item and "due" in item for item in result["insights"]["actions"]))
        self.assertEqual(result["trace"][0]["tool_name"], "retrieve_documents")

    def test_retrieval_preserves_late_risk_and_action_evidence(self) -> None:
        source = "\n".join([
            "本周完成知识库文档清洗并完成首轮检索调优。",
            "前端嵌入版本已完成来源引用和人工转接入口。",
            "测试集 Top-3 召回率为 86%。",
            "已确认 8 月 16 日完成试点上线。",
            "其余常规项目进展材料。",
            "门店营业时间接口尚未提供，可能影响实时问答能力，产品团队计划 8 月 5 日确认替代方案。",
            "企业微信安全审核尚未提交，审核周期可能影响试点排期，安全负责人待确认。",
            "测试环境账号尚未审批，联调可能延期 3 天，研发计划跟进。",
            "18 份历史文档版本不一致，运营团队需在本周确认最终版本。",
        ])
        evidence = retrieve_documents("生成项目周报、风险清单和汇报大纲", source)
        combined = "\n".join(item["excerpt"] for item in evidence)
        self.assertIn("营业时间接口尚未提供", combined)
        self.assertIn("企业微信安全审核尚未提交", combined)
        self.assertIn("测试环境账号尚未审批", combined)

    def test_near_duplicate_model_risks_are_merged(self) -> None:
        facts = [
            {"citation": "E1", "claim": "历史政策文档版本不一致，可能召回旧版本。"},
            {"citation": "E2", "claim": "版本不一致可能造成用户获得旧政策。"},
        ]
        raw = {
            "weekly_summary": "存在版本风险。",
            "progress": [{"content": "完成清洗", "evidence_ids": ["E1"]}],
            "risks": [
                {"risk": "历史政策文档版本不一致，可能召回旧版本", "level": "中", "impact": "政策错误", "owner": "运营团队", "due": "待确认", "evidence_ids": ["E1"]},
                {"risk": "文档版本不一致导致召回旧版本政策", "level": "中", "impact": "用户体验受影响", "owner": "运营团队", "due": "待确认", "evidence_ids": ["E2"]},
            ],
            "actions": [],
            "milestones": [],
            "slide_outline": [],
        }
        insights = _normalize_insights(raw, facts)
        self.assertEqual(len(insights["risks"]), 1)
        self.assertEqual(insights["risks"][0]["evidence_ids"], ["E1", "E2"])

    def test_verifier_warns_when_slides_overstate_unconfirmed_risks(self) -> None:
        artifacts = {
            "weekly_report_markdown": "本周完成联调 [E1]",
            "risk_register_markdown": "| 风险 | 负责人 | 截止时间 |\n| --- | --- | --- |\n| 账号待审批 | 待确认 | 待确认 | [E1]",
            "slide_outline_markdown": "2. 当前风险均已明确责任人和截止时间 [E1]",
        }
        verification = verify_citations(artifacts, [{"id": "E1", "excerpt": "测试账号尚未审批"}])
        self.assertTrue(verification["passed"])
        self.assertFalse(verification["consistency_passed"])
        self.assertTrue(verification["warnings"])

    def test_repository_persists_task_run_and_trace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = DocflowRepository(Path(directory) / "agent.db")
            task = repository.create_task("周报", "生成风险清单", SOURCE)
            run_id = repository.create_run(task["id"])
            result = AgentRuntime().execute("生成风险清单", SOURCE, trace_callback=lambda step: repository.record_step(run_id, step))
            completed = repository.complete_run(task["id"], run_id, result["plan"], result)
            self.assertEqual(completed["status"], "awaiting_review")
            self.assertEqual(len(completed["runs"][0]["steps"]), len(result["trace"]))


if __name__ == "__main__":
    unittest.main()
