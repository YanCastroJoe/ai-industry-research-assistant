import json
import importlib.util
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from app.docflow import AgentRuntime, extract_facts, retrieve_documents
from app.docflow_repository import DocflowRepository
from app.model_client import model_runtime_status
from app.planning import build_rule_plan


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "check_model_runtime.py"
SCRIPT_SPEC = importlib.util.spec_from_file_location("check_model_runtime", SCRIPT_PATH)
assert SCRIPT_SPEC and SCRIPT_SPEC.loader
CHECK_MODEL_RUNTIME = importlib.util.module_from_spec(SCRIPT_SPEC)
SCRIPT_SPEC.loader.exec_module(CHECK_MODEL_RUNTIME)


SOURCE = """项目组本周完成需求澄清并确认三个交付里程碑。
测试环境尚未开放，可能影响联调排期。
产品负责人计划周五确认验收范围，并由研发补充接口文档。"""
GOAL = "生成项目周报、风险清单和三页汇报大纲"


class FakeResponse:
    def __init__(self, content, *, request_id, prompt_tokens, completion_tokens):
        self.content = content
        self.headers = {"x-request-id": request_id}
        self.request_id = request_id
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return json.dumps(
            {
                "id": self.request_id,
                "model": "deepseek-chat",
                "choices": [{"message": {"content": json.dumps(self.content, ensure_ascii=False)}}],
                "usage": {
                    "prompt_tokens": self.prompt_tokens,
                    "completion_tokens": self.completion_tokens,
                    "total_tokens": self.prompt_tokens + self.completion_tokens,
                },
            },
            ensure_ascii=False,
        ).encode("utf-8")


def model_responses():
    evidence = retrieve_documents(GOAL, SOURCE)
    facts = extract_facts(evidence)
    planner = {"plan": build_rule_plan(GOAL)}
    content = {
        "weekly_summary": facts[0]["claim"],
        "weekly_summary_evidence_ids": [facts[0]["citation"]],
        "background": [],
        "progress": [{"content": facts[0]["claim"], "evidence_ids": [facts[0]["citation"]]}],
        "milestones": [],
        "risks": [],
        "actions": [],
        "proposals": [],
        "conflicts": [],
        "security_flags": [],
        "slide_outline": [],
    }
    return [
        FakeResponse(planner, request_id="planner-request", prompt_tokens=100, completion_tokens=20),
        FakeResponse(content, request_id="content-request", prompt_tokens=200, completion_tokens=40),
    ]


class ModelRuntimeTests(unittest.TestCase):
    def test_real_model_path_records_two_calls_tokens_cost_and_request_ids(self):
        environment = {
            "MODEL_API_KEY": "unit-test-secret",
            "MODEL_INPUT_COST_PER_MILLION": "1",
            "MODEL_OUTPUT_COST_PER_MILLION": "2",
            "MODEL_COST_CURRENCY": "CNY",
        }
        with patch.dict("os.environ", environment, clear=False), patch(
            "urllib.request.urlopen", side_effect=model_responses()
        ) as urlopen:
            result = AgentRuntime().execute(GOAL, SOURCE)

        execution = result["execution"]
        self.assertTrue(execution["model_path_complete"])
        self.assertFalse(execution["degraded"])
        self.assertEqual(execution["model_call_count"], 2)
        self.assertEqual(execution["model_success_count"], 2)
        self.assertEqual(execution["model_failure_count"], 0)
        self.assertEqual(execution["model_usage"]["total_tokens"], 360)
        self.assertAlmostEqual(execution["estimated_cost"], 0.00042)
        self.assertEqual(
            [call["request_id"] for call in execution["model_calls"]],
            ["planner-request", "content-request"],
        )
        self.assertNotIn("unit-test-secret", json.dumps(execution))
        self.assertEqual([call.kwargs["timeout"] for call in urlopen.call_args_list], [20, 40])
        self.assertEqual(model_runtime_status(True)["model_reachability"], "reachable")
        self.assertTrue(result["verification"]["overall_passed"])

    def test_model_failure_is_labeled_and_falls_back_without_false_model_claim(self):
        with patch.dict("os.environ", {"MODEL_API_KEY": "unit-test-secret"}, clear=False), patch(
            "urllib.request.urlopen", side_effect=urllib.error.URLError("network unavailable")
        ):
            result = AgentRuntime().execute(GOAL, SOURCE)

        execution = result["execution"]
        self.assertTrue(execution["degraded"])
        self.assertFalse(execution["model_path_complete"])
        self.assertEqual(execution["planner_mode"], "rules_fallback")
        self.assertEqual(execution["content_mode"], "rules_fallback")
        self.assertEqual(execution["model_call_count"], 2)
        self.assertEqual(execution["model_success_count"], 0)
        self.assertEqual(execution["model_failure_count"], 2)
        self.assertTrue(all(call["status"] == "failed" for call in execution["model_calls"]))
        self.assertEqual(model_runtime_status(True)["model_reachability"], "unavailable")
        self.assertTrue(result["verification"]["overall_passed"])

    def test_repository_prefers_provider_usage_and_cost_over_output_size_estimate(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = DocflowRepository(Path(directory) / "model.db")
            task = repository.create_task("模型任务", GOAL, SOURCE, "model-session")
            run_id = repository.create_run(task["id"])
            result = {
                "execution": {"model_usage": {"total_tokens": 321}, "estimated_cost": 0.0123},
                "metrics": {"elapsed_ms": 10},
                "verification": {"passed": True},
            }
            completed = repository.complete_run(task["id"], run_id, build_rule_plan(GOAL), result)
            run = completed["runs"][0]
            self.assertEqual(run["token_estimate"], 321)
            self.assertAlmostEqual(run["cost_estimate"], 0.0123)

    def test_portable_acceptance_rejects_fallback_and_accepts_complete_model_evidence(self):
        task = {
            "status": "awaiting_review",
            "result": {
                "verification": {"overall_passed": True},
                "artifacts": {
                    "weekly_report_markdown": (
                        "风险：退款政策文档仍有两个版本；负责人：李明；截止：周五前 [E1]\n"
                        "行动：王芳负责补充边界案例 [E2]"
                    )
                },
                "evidence": [{"id": "E1"}, {"id": "E2"}],
                "execution": {
                    "model_path_complete": True,
                    "model_call_count": 2,
                    "model_usage": {"total_tokens": 120},
                    "model_calls": [
                        {"stage": "planner", "status": "succeeded", "request_id": "req-1"},
                        {"stage": "content", "status": "succeeded", "request_id": "req-2"},
                    ],
                },
            },
        }
        self.assertTrue(
            CHECK_MODEL_RUNTIME.validate_readiness({"runtime": {"model_configured": True}})["model_configured"]
        )
        with self.assertRaises(CHECK_MODEL_RUNTIME.AcceptanceError):
            CHECK_MODEL_RUNTIME.validate_readiness({"runtime": {"model_configured": False}})
        self.assertTrue(CHECK_MODEL_RUNTIME.validate_model_run(task)["model_path_complete"])
        task["result"]["execution"]["model_path_complete"] = False
        task["result"]["execution"]["fallback_reasons"] = ["planner unavailable"]
        with self.assertRaises(CHECK_MODEL_RUNTIME.AcceptanceError):
            CHECK_MODEL_RUNTIME.validate_model_run(task)

    def test_portable_acceptance_rejects_missing_required_source_fact(self):
        task = {
            "status": "awaiting_review",
            "result": {
                "verification": {"overall_passed": True},
                "artifacts": {"weekly_report_markdown": "项目正常推进 [E1]"},
                "evidence": [{"id": "E1"}],
                "execution": {
                    "model_path_complete": True,
                    "model_call_count": 2,
                    "model_usage": {"total_tokens": 120},
                    "model_calls": [
                        {"stage": "planner", "status": "succeeded", "request_id": "req-1"},
                        {"stage": "content", "status": "succeeded", "request_id": "req-2"},
                    ],
                },
            },
        }
        with self.assertRaises(CHECK_MODEL_RUNTIME.AcceptanceError):
            CHECK_MODEL_RUNTIME.validate_model_run(task)


if __name__ == "__main__":
    unittest.main()
