import os
import unittest

from app.analysis import _normalize_industry_analysis, _remove_prohibited_text, analyze, route_material
from app.document_text import extract_uploaded_text


class AnalysisTests(unittest.TestCase):
    def setUp(self) -> None:
        self.previous_key = os.environ.pop("MODEL_API_KEY", None)

    def tearDown(self) -> None:
        if self.previous_key:
            os.environ["MODEL_API_KEY"] = self.previous_key

    def test_routes_industry_policy_material(self) -> None:
        text = "工信部门发布人工智能产业政策，支持算力产业链和上下游技术路线发展。"
        self.assertEqual(route_material(text), "industry")

    def test_company_card_contains_evidence(self) -> None:
        card = analyze("公司公告披露：2025 年营业收入同比增长 20%，净利润同比增长 15%。")
        self.assertEqual(card.material_type, "company")
        self.assertGreaterEqual(len(card.facts), 1)
        self.assertTrue(card.facts[0].evidence)
        self.assertEqual(card.analysis_mode, "demo")

    def test_requested_type_overrides_router(self) -> None:
        self.assertEqual(route_material("政策发布后产业需求发生变化。", "macro"), "macro")

    def test_extracts_utf8_text_file(self) -> None:
        text = extract_uploaded_text("industry.md", "产业政策支持算力基础设施建设。".encode("utf-8"))
        self.assertIn("算力", text)

    def test_filters_disclaimer_and_preserves_pdf_page(self) -> None:
        text = """[第 1 页]
本研究报告由某证券公司分析师编制，请仔细阅读免责声明。
• 三季度收入为 6.35 亿美元，同比增长 21%，环比增长 12%。
• 毛利率为 13.5%，高于此前指引上限和市场一致预期。
"""
        card = analyze(text, "company")
        joined_claims = " ".join(fact.claim for fact in card.facts)
        self.assertNotIn("免责声明", joined_claims)
        self.assertIn("毛利率", joined_claims)
        self.assertEqual(card.facts[0].source_location, "PDF 第 1 页")

    def test_local_card_has_industry_analysis(self) -> None:
        card = analyze("公司公告披露晶圆价格上行，产能利用率提升，毛利率改善。", "company")
        self.assertTrue(card.industry_analysis["industry_judgment"])
        self.assertTrue(card.industry_analysis["causal_chain"])

    def test_prohibited_investment_language_is_removed(self) -> None:
        self.assertNotIn("目标价", _remove_prohibited_text("公司目标价为 100 元。毛利率改善。"))
        normalized = _normalize_industry_analysis(
            {"industry_judgment": "买入评级。基于材料推演，供需改善。", "causal_chain": ["目标价上调", "依据：事实 1"], "direction_analysis": [], "risk_reversals": []},
            [],
        )
        self.assertNotIn("目标价", " ".join(normalized["causal_chain"]))


if __name__ == "__main__":
    unittest.main()
