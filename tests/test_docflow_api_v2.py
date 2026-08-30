import os
import tempfile
import threading
import time
import unittest
from pathlib import Path

from fastapi.testclient import TestClient


os.environ["MODEL_API_KEY"] = ""

from app import main
from app.docflow import AgentRuntime
from app.docflow_repository import DocflowRepository
from app.job_coordinator import JobCoordinator
from app.planning import RulePlanner


SOURCE = "项目组完成需求澄清并确认交付里程碑。测试环境尚未开放，可能影响联调排期。产品负责人计划周五确认验收范围。"


class DocflowApiV2Tests(unittest.TestCase):
    def test_readiness_reports_database_queue_and_request_id(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            coordinator = JobCoordinator(max_workers=1, max_pending=3)
            main.docflow_repository = DocflowRepository(Path(directory) / "ready.db")
            main.job_coordinator = coordinator
            client = TestClient(main.app)
            response = client.get("/ready", headers={"X-Request-ID": "resume-test-001"})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers["X-Request-ID"], "resume-test-001")
            payload = response.json()
            self.assertEqual(payload["status"], "ready")
            self.assertTrue(payload["database"]["ok"])
            self.assertEqual(payload["database"]["journal_mode"], "wal")
            self.assertEqual(payload["database"]["busy_timeout_ms"], 5000)
            self.assertEqual(payload["queue"]["max_pending"], 3)
            self.assertFalse(payload["boundaries"]["queued_jobs_durable"])
            coordinator.shutdown()

    def test_idempotency_key_reuses_task_and_rejects_changed_payload(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            coordinator = JobCoordinator(max_workers=1, max_pending=3)
            main.docflow_repository = DocflowRepository(Path(directory) / "idempotency.db")
            main.docflow_runtime = AgentRuntime(planner=RulePlanner())
            main.job_coordinator = coordinator
            client = TestClient(main.app)
            payload = {"title": "幂等任务", "goal": "生成项目周报和风险清单", "text": SOURCE, "session_id": "idem"}
            headers = {"Idempotency-Key": "docflow-idem-0001"}

            first = client.post("/api/docflow/jobs", json=payload, headers=headers)
            second = client.post("/api/docflow/jobs", json=payload, headers=headers)
            self.assertEqual(first.status_code, 202)
            self.assertEqual(second.status_code, 202)
            self.assertEqual(first.json()["task_id"], second.json()["task_id"])
            self.assertTrue(second.json()["reused"])
            self.assertEqual(len(main.docflow_repository.list_tasks()), 1)

            changed = {**payload, "text": SOURCE + "新增不同内容。"}
            conflict = client.post("/api/docflow/jobs", json=changed, headers=headers)
            self.assertEqual(conflict.status_code, 409)
            coordinator.shutdown()

    def test_repository_marks_interrupted_jobs_failed_on_restart_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = DocflowRepository(Path(directory) / "recovery.db")
            queued = repository.create_task("等待任务", "生成项目周报", SOURCE)
            running = repository.create_task("运行任务", "生成项目周报", SOURCE)
            repository.mark_task_running(running["id"])
            repository.create_run(running["id"])

            self.assertEqual(repository.recover_interrupted_tasks(), 2)
            self.assertEqual(repository.get_task(queued["id"])["status"], "failed")
            recovered = repository.get_task(running["id"])
            self.assertEqual(recovered["status"], "failed")
            self.assertEqual(recovered["runs"][0]["status"], "failed")
            self.assertIn("进程重启", recovered["runs"][0]["error"])

    def test_async_job_returns_immediately_and_exposes_lifecycle_progress(self) -> None:
        class BlockingRuntime(AgentRuntime):
            def __init__(self, release: threading.Event):
                super().__init__(planner=RulePlanner())
                self.release = release

            def execute(self, *args, **kwargs):
                self.release.wait(timeout=3)
                return super().execute(*args, **kwargs)

        with tempfile.TemporaryDirectory() as directory:
            release = threading.Event()
            coordinator = JobCoordinator(max_workers=1, max_pending=1)
            main.docflow_repository = DocflowRepository(Path(directory) / "async.db")
            main.docflow_runtime = BlockingRuntime(release)
            main.job_coordinator = coordinator
            client = TestClient(main.app)
            started = time.perf_counter()
            response = client.post(
                "/api/docflow/jobs",
                json={"title": "异步任务", "goal": "生成项目周报和风险清单", "text": SOURCE, "session_id": "async"},
            )
            elapsed = time.perf_counter() - started
            self.assertEqual(response.status_code, 202)
            self.assertLess(elapsed, 1.0)
            task_id = response.json()["task_id"]
            lifecycle = client.get(f"/api/docflow/jobs/{task_id}").json()
            self.assertIn(lifecycle["status"], {"queued", "running"})
            self.assertFalse(lifecycle["terminal"])
            self.assertLess(lifecycle["progress_percent"], 100)

            overflow = client.post(
                "/api/docflow/jobs",
                json={"title": "超出队列", "goal": "生成项目周报和风险清单", "text": SOURCE, "session_id": "async"},
            )
            self.assertEqual(overflow.status_code, 429)
            overflow_task = main.docflow_repository.list_tasks()[0]
            self.assertEqual(overflow_task["status"], "failed")
            self.assertIn("队列已满", overflow_task["reviewer_note"])

            release.set()
            deadline = time.time() + 5
            while time.time() < deadline:
                lifecycle = client.get(f"/api/docflow/jobs/{task_id}").json()
                if lifecycle["terminal"]:
                    break
                time.sleep(0.05)
            self.assertEqual(lifecycle["status"], "awaiting_review")
            self.assertEqual(lifecycle["progress_percent"], 100)
            self.assertGreater(lifecycle["completed_steps"], 0)
            task = client.get(lifecycle["task_url"]).json()
            self.assertEqual(task["runs"][0]["status"], "completed")
            coordinator.shutdown()

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
            self.assertEqual(task["result"]["context"]["strategy"], "layered_context_v1")
            self.assertEqual(task["result"]["context"]["layers"][2]["items"], 1)

    def test_context_config_controls_evidence_budget_and_memory_layer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            main.docflow_repository = DocflowRepository(Path(directory) / "context.db")
            main.docflow_runtime = AgentRuntime(planner=RulePlanner())
            client = TestClient(main.app)
            client.post(
                "/api/docflow/memories",
                json={"session_id": "team-a", "memory_key": "汇报偏好", "content": "优先展示风险"},
            )
            response = client.post(
                "/api/docflow/tasks",
                json={
                    "title": "上下文测试",
                    "goal": "生成项目周报和风险清单",
                    "text": SOURCE * 4,
                    "session_id": "team-a",
                    "context_config": {
                        "audience": "管理层",
                        "focus": "risk",
                        "evidence_limit": 4,
                        "memory_enabled": False,
                        "citation_policy": "strict",
                    },
                },
            )
            self.assertEqual(response.status_code, 201)
            task = response.json()
            context = task["result"]["context"]
            self.assertEqual(context["audience"], "管理层")
            self.assertEqual(context["focus"], "风险优先")
            self.assertEqual(context["evidence_budget"], 4)
            self.assertEqual(context["layers"][2]["items"], 0)
            self.assertLessEqual(context["layers"][3]["items"], 4)

    def test_agentops_summary_aggregates_quality_gates_and_review_feedback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            main.docflow_repository = DocflowRepository(Path(directory) / "agentops.db")
            main.docflow_runtime = AgentRuntime(planner=RulePlanner())
            client = TestClient(main.app)
            first = client.post(
                "/api/docflow/tasks",
                json={"title": "评测任务一", "goal": "生成项目周报和风险清单", "text": SOURCE, "session_id": "eval"},
            ).json()
            client.post(f"/api/docflow/tasks/{first['id']}/review", json={"action": "approve", "note": "引用完整"})
            client.post(
                "/api/docflow/tasks",
                json={"title": "评测任务二", "goal": "生成项目周报", "text": SOURCE, "session_id": "eval"},
            )

            response = client.get("/api/docflow/evaluations/summary")
            self.assertEqual(response.status_code, 200)
            summary = response.json()
            self.assertEqual(summary["scope"], "local_task_history")
            self.assertEqual(summary["metrics"]["task_count"], 2)
            self.assertEqual(summary["metrics"]["evaluated_count"], 2)
            self.assertEqual(summary["metrics"]["reviewed_count"], 1)
            self.assertEqual(summary["metrics"]["approval_rate"], 1.0)
            self.assertEqual(summary["metrics"]["citation_pass_rate"], 1.0)
            self.assertTrue(all(gate["passed"] is not None for gate in summary["quality_gates"]))
            self.assertEqual(len(summary["recent_tasks"]), 2)


if __name__ == "__main__":
    unittest.main()
