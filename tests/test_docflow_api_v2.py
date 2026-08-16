import os
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient


os.environ["MODEL_API_KEY"] = ""

from app import main
from app.docflow import AgentRuntime
from app.docflow_repository import DocflowRepository
from app.planning import RulePlanner


SOURCE = "项目组完成需求澄清并确认交付里程碑。测试环境尚未开放，可能影响联调排期。产品负责人计划周五确认验收范围。"


class DocflowApiV2Tests(unittest.TestCase):
    def test_api_uses_session_memory_and_returns_runtime_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            main.docflow_repository = DocflowRepository(Path(directory) / "api.db")
            main.docflow_runtime = AgentRuntime(planner=RulePlanner())
            client = TestClient(main.app)

            memory_response = client.post(
                "/api/docflow/memories",
                json={"session_id": "team-a", "memory_key": "汇报偏好", "content": "优先展示风险和截止时间"},
            )
            self.assertEqual(memory_response.status_code, 201)
            task_response = client.post(
                "/api/docflow/tasks",
                json={"title": "周报", "goal": "生成项目周报和风险清单", "text": SOURCE, "session_id": "team-a"},
            )
            self.assertEqual(task_response.status_code, 201)
            task = task_response.json()
            self.assertEqual(task["status"], "awaiting_review")
            self.assertEqual(task["result"]["memory"]["items_used"], 1)
            self.assertEqual(task["result"]["planner"]["mode"], "rules")
            self.assertGreater(task["result"]["metrics"]["executed_steps"], 0)
            self.assertTrue(all("attempt" in step for step in task["runs"][0]["steps"]))


if __name__ == "__main__":
    unittest.main()
