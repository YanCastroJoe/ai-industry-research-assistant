from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from scripts.evaluate_model_rules_ab import evaluation_mode, percentile, score_result, summarize


class ModelRulesAbEvaluationTests(unittest.TestCase):
    def test_rules_mode_restores_secret_without_exposing_or_changing_it(self) -> None:
        with patch.dict(os.environ, {"MODEL_API_KEY": "unit-test-secret"}, clear=False):
            with evaluation_mode("rules"):
                self.assertNotIn("MODEL_API_KEY", os.environ)
            self.assertEqual(os.environ["MODEL_API_KEY"], "unit-test-secret")

    def test_score_requires_grounded_binding_and_requested_mode(self) -> None:
        result = {
            "evidence": [
                {"id": "E1", "excerpt": "风险：证书尚未签发，负责人赵磊需在9月8日前完成申请。"},
                {"id": "E2", "excerpt": "行动：陈雪负责补齐回滚脚本，9月7日前完成演练。"},
            ],
            "insights": {
                "risks": [{"risk": "证书阻塞上线", "owner": "赵磊", "due": "9月8日前", "evidence_ids": ["E1"]}],
                "actions": [{"content": "补齐回滚脚本", "owner": "陈雪", "due": "9月7日前", "evidence_ids": ["E2"]}],
            },
            "artifacts": {"weekly_report_markdown": "## 关键风险\n证书阻塞 [E1]\n## 关键进展\n已完成 [E2]"},
            "verification": {"overall_passed": True, "content_quality_passed": True},
            "execution": {"model_path_complete": True, "degraded": False},
            "memory": {"applied": 1},
        }
        case = {
            "bindings": [
                {"kind": "risk", "evidence_fragment": "证书尚未签发", "owner": "赵磊", "due": "9月8日前"},
                {"kind": "action", "evidence_fragment": "补齐回滚脚本", "owner": "陈雪", "due": "9月7日前"},
            ]
        }
        score = score_result(result, case, "model")
        self.assertTrue(score["passed"])
        result["insights"]["risks"][0]["due"] = "9月8日"
        self.assertFalse(score_result(result, case, "model")["passed"])

    def test_summary_uses_nearest_rank_percentiles_and_does_not_invent_cost(self) -> None:
        rows = []
        for mode, latency in (("model", 5000), ("model", 6000), ("rules", 10), ("rules", 20)):
            rows.append({
                "requested_mode": mode,
                "score": {"passed": True, "gate_score": 100.0},
                "degraded": False,
                "retry_count": 0,
                "wall_ms": latency,
                "model_latency_ms": latency if mode == "model" else 0,
                "tokens": {"total_tokens": 100 if mode == "model" else 0},
                "estimated_cost": None,
                "cost_currency": "CNY" if mode == "model" else None,
            })
        report = summarize(rows)
        self.assertEqual(percentile([5000, 6000], 0.95), 6000)
        self.assertEqual(report["model"]["total_tokens"], 200)
        self.assertIsNone(report["model"]["estimated_cost_total"])
        self.assertIsNone(report["rules"]["estimated_cost_total"])


if __name__ == "__main__":
    unittest.main()
