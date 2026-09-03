import tempfile
import unittest
from pathlib import Path

from app.docflow import AgentRuntime, _normalize_insights, build_plan, retrieve_documents, verify_citations
from app.docflow_repository import DocflowRepository


SOURCE = """项目组本周完成需求澄清并确认三个交付里程碑。\n当前风险是测试环境尚未开放，可能影响联调排期。\n产品负责人计划在周五确认验收范围，并由研发补充接口文档。"""

BUSINESS_SOURCE = """项目：企业知识库智能客服升级
本周完成120条知识清洗，其中8条知识适用范围待确认。
新旧退货政策内容冲突。
批量评测接口偶尔超时。
2条固定回归用例失败。
生产测试账号权限未审批。
安全审核尚未完成。
张浩：9月5日前完成连接池调整和压力测试。
李婷：9月3日前补充报销规则并统一版本标识。
王晨：9月6日前提交回归测试报告。
计划于9月15日正式上线。"""


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

    def test_rule_mode_preserves_entities_and_covers_business_risks(self) -> None:
        result = AgentRuntime().execute("生成本周项目周报、风险清单和三页汇报大纲", BUSINESS_SOURCE)
        insights = result["insights"]
        actions = {(item["owner"], item["due"]) for item in insights["actions"]}
        risk_text = "\n".join(item["risk"] for item in insights["risks"])
        progress_text = "\n".join(item["content"] for item in insights["progress"])

        self.assertIn(("张浩", "9月5日前"), actions)
        self.assertIn(("李婷", "9月3日前"), actions)
        self.assertIn(("王晨", "9月6日前"), actions)
        self.assertIn("批量评测接口偶尔超时", risk_text)
        self.assertIn("2条固定回归用例失败", risk_text)
        self.assertIn("生产测试账号权限未审批", risk_text)
        self.assertIn("安全审核尚未完成", risk_text)
        self.assertNotIn("超时", progress_text)
        self.assertNotIn("失败", progress_text)
        self.assertEqual(insights["milestones"][0]["due"], "9月15日")
        self.assertIn("安全审核", result["artifacts"]["slide_outline_markdown"])
        self.assertTrue(result["verification"]["content_quality_passed"])

    def test_goal_prefixed_launch_date_is_a_milestone_not_an_owned_action(self) -> None:
        result = AgentRuntime().execute(
            "生成项目周报、风险清单和三页汇报大纲",
            "项目：企业知识库升级。\n目标：9月15日正式上线。",
        )
        self.assertEqual(result["insights"]["milestones"][0]["due"], "9月15日")
        self.assertFalse(any(item["owner"] == "目标" for item in result["insights"]["actions"]))

    def test_spaced_dates_and_action_objects_stay_bound_to_their_risks(self) -> None:
        source = """项目：支付系统上线准备。
退款接口仍有 2 个用例失败，张浩需在 9 月 5 日前修复。
生产账号权限尚未审批，李梅需在 9 月 8 日前完成。
王芳需在 9 月 6 日前提交回归测试报告。
计划于 9 月 15 日正式上线。"""
        result = AgentRuntime().execute("生成项目周报、风险清单和三页汇报大纲", source)
        risks = {item["risk"]: item for item in result["insights"]["risks"]}
        actions = {item["owner"]: item for item in result["insights"]["actions"]}

        self.assertEqual(risks["退款接口仍有 2 个用例失败"]["owner"], "张浩")
        self.assertEqual(risks["退款接口仍有 2 个用例失败"]["due"], "9月5日前")
        self.assertEqual(risks["生产账号权限尚未审批"]["owner"], "李梅")
        self.assertEqual(risks["生产账号权限尚未审批"]["due"], "9月8日前")
        self.assertIn("退款接口", actions["张浩"]["content"])
        self.assertIn("生产账号权限", actions["李梅"]["content"])
        self.assertEqual(actions["王芳"]["due"], "9月6日前")
        self.assertEqual(result["insights"]["milestones"][0]["due"], "9月15日")

    def test_rule_risk_impact_does_not_reuse_cross_domain_boilerplate(self) -> None:
        result = AgentRuntime().execute(
            "生成项目风险清单",
            "退款接口仍有2个用例失败，张浩需在9月5日前修复。",
        )
        risk = result["insights"]["risks"][0]
        self.assertEqual(risk["impact"], "影响待确认")
        self.assertNotIn("批量评测", result["artifacts"]["risk_register_markdown"])

    def test_verifier_rejects_fields_and_impact_not_supported_by_cited_evidence(self) -> None:
        evidence = [{"id": "E1", "excerpt": "退款接口仍有2个用例失败。"}]
        artifacts = {
            "weekly_report_markdown": "## 关键风险\n- 退款接口仍有2个用例失败 [E1]",
            "risk_register_markdown": "| 风险 | 等级 | 影响 | 负责人 | 截止时间 | 证据 |\n| --- | --- | --- | --- | --- | --- |\n| 退款接口仍有2个用例失败 | 高 | 影响批量评测稳定性 | 张浩 | 9月5日前 | [E1] |",
        }
        verification = verify_citations(artifacts, evidence)
        self.assertTrue(verification["citation_check"]["passed"])
        self.assertFalse(verification["field_consistency_check"]["passed"])
        self.assertFalse(verification["semantic_support_check"]["passed"])
        self.assertFalse(verification["overall_passed"])

    def test_verifier_detects_missing_entities_risks_and_slide_priority(self) -> None:
        evidence = retrieve_documents("生成项目周报、风险清单和三页汇报大纲", BUSINESS_SOURCE)
        artifacts = {
            "weekly_report_markdown": "# 项目周报\n\n## 关键进展\n- 完成知识清洗 [E1]",
            "risk_register_markdown": "| 风险 | 证据 |\n| --- | --- |\n| 政策冲突 | [E1] |",
            "slide_outline_markdown": "1. 背景\n2. 进展\n3. 普通进展 [E1]",
        }
        verification = verify_citations(artifacts, evidence)
        warning_text = "\n".join(verification["warnings"])

        self.assertTrue(verification["passed"])
        self.assertFalse(verification["content_quality_passed"])
        self.assertIn("明确负责人", warning_text)
        self.assertIn("风险主题", warning_text)
        self.assertIn("高优先级风险", warning_text)

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
