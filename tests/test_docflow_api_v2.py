import os
import base64
import tempfile
import threading
import time
import unittest
from pathlib import Path

from fastapi.testclient import TestClient


os.environ["MODEL_API_KEY"] = ""

from app import main
from app.config import DemoSecurityConfig
from app.docflow import AgentRuntime
from app.docflow_repository import DocflowRepository
from app.job_coordinator import JobCoordinator
from app.planning import RulePlanner


SOURCE = "项目组完成需求澄清并确认交付里程碑。测试环境尚未开放，可能影响联调排期。产品负责人计划周五确认验收范围。"


def session_client(session_id: str) -> TestClient:
    return TestClient(main.app, headers={"X-DocFlow-Session": session_id})


class DocflowApiV2Tests(unittest.TestCase):
    def test_restricted_demo_requires_basic_auth_and_reports_safe_boundary(self) -> None:
        original_security = main.demo_security
        original_repository = main.docflow_repository
        with tempfile.TemporaryDirectory() as directory:
            main.demo_security = DemoSecurityConfig(True, "interviewer", "demo-secret", 60)
            main.docflow_repository = DocflowRepository(Path(directory) / "security-ready.db")
            main._demo_rate_buckets.clear()
            try:
                client = TestClient(main.app)
                self.assertEqual(client.get("/").status_code, 401)
                self.assertEqual(client.get("/health").status_code, 200)
                token = base64.b64encode(b"interviewer:demo-secret").decode("ascii")
                authenticated = TestClient(main.app, headers={"Authorization": f"Basic {token}"})
                self.assertEqual(authenticated.get("/").status_code, 200)
                boundaries = authenticated.get("/ready").json()["boundaries"]
                self.assertTrue(boundaries["demo_authentication"])
                self.assertTrue(boundaries["public_demo_safe"])
                self.assertEqual(boundaries["demo_rate_limit_per_minute"], 60)
            finally:
                main.demo_security = original_security
                main.docflow_repository = original_repository
                main._demo_rate_buckets.clear()

    def test_restricted_demo_rate_limits_api_requests(self) -> None:
        original_security = main.demo_security
        original_repository = main.docflow_repository
        with tempfile.TemporaryDirectory() as directory:
            main.demo_security = DemoSecurityConfig(True, "interviewer", "demo-secret", 1)
            main.docflow_repository = DocflowRepository(Path(directory) / "security-rate.db")
            main._demo_rate_buckets.clear()
            try:
                token = base64.b64encode(b"interviewer:demo-secret").decode("ascii")
                client = TestClient(
                    main.app,
                    headers={
                        "Authorization": f"Basic {token}",
                        "X-DocFlow-Session": "rate-limit-session",
                    },
                )
                self.assertEqual(client.get("/api/docflow/tasks").status_code, 200)
                limited = client.get("/api/docflow/tasks")
                self.assertEqual(limited.status_code, 429)
                self.assertEqual(limited.headers["Retry-After"], "60")
            finally:
                main.demo_security = original_security
                main.docflow_repository = original_repository
                main._demo_rate_buckets.clear()

    def test_browser_sessions_isolate_tasks_memories_and_mutations(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            main.docflow_repository = DocflowRepository(Path(directory) / "session-isolation.db")
            main.docflow_runtime = AgentRuntime(planner=RulePlanner())
            owner = session_client("visitor-owner-0001")
            stranger = session_client("visitor-stranger-0002")

            memory = owner.post(
                "/api/docflow/memories",
                json={"session_id": "visitor-owner-0001", "memory_key": "协作偏好", "content": "风险优先"},
            ).json()
            task = owner.post(
                "/api/docflow/tasks",
                json={"title": "私有周报", "goal": "生成项目周报和风险清单", "text": SOURCE, "session_id": "visitor-owner-0001"},
            ).json()

            self.assertEqual(len(owner.get("/api/docflow/tasks").json()), 1)
            self.assertEqual(stranger.get("/api/docflow/tasks").json(), [])
            self.assertEqual(stranger.get("/api/docflow/memories/visitor-stranger-0002").json(), [])
            self.assertEqual(stranger.get(f"/api/docflow/tasks/{task['id']}").status_code, 404)
            self.assertEqual(stranger.post(f"/api/docflow/tasks/{task['id']}/review", json={"action": "approve"}).status_code, 404)
            self.assertEqual(stranger.delete(f"/api/docflow/tasks/{task['id']}").status_code, 404)
            self.assertEqual(stranger.get(f"/api/docflow/tasks/{task['id']}/export").status_code, 404)
            self.assertEqual(stranger.patch(f"/api/docflow/memories/{memory['id']}", json={"enabled": False}).status_code, 404)
            self.assertEqual(stranger.delete(f"/api/docflow/memories/{memory['id']}").status_code, 404)

            self.assertEqual(owner.get(f"/api/docflow/tasks/{task['id']}").status_code, 200)
            self.assertEqual(owner.get("/api/docflow/memories/visitor-owner-0001").json()[0]["content"], "风险优先")

    def test_missing_or_mismatched_browser_session_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            main.docflow_repository = DocflowRepository(Path(directory) / "session-auth.db")
            anonymous = TestClient(main.app)
            response = anonymous.get("/api/docflow/tasks")
            self.assertEqual(response.status_code, 401)
            mismatched = session_client("visitor-one-0001").post(
                "/api/docflow/tasks",
                json={"title": "越权提交", "goal": "生成项目周报和风险清单", "text": SOURCE, "session_id": "visitor-two-0002"},
            )
            self.assertEqual(mismatched.status_code, 404)

    def test_memory_can_be_updated_disabled_and_deleted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            main.docflow_repository = DocflowRepository(Path(directory) / "memory-admin.db")
            client = session_client("memory-admin")
            created = client.post(
                "/api/docflow/memories",
                json={"session_id": "memory-admin", "memory_key": "协作偏好", "content": "风险优先"},
            ).json()
            updated = client.patch(
                f"/api/docflow/memories/{created['id']}",
                json={"content": "进展优先", "enabled": False},
            )
            self.assertEqual(updated.status_code, 200)
            self.assertFalse(updated.json()["enabled"])
            self.assertEqual(main.docflow_repository.recall_memories("memory-admin", "生成项目周报"), [])
            listed = client.get("/api/docflow/memories/memory-admin").json()
            self.assertEqual(listed[0]["content"], "进展优先")
            self.assertIn("updated_at", listed[0])
            deleted = client.delete(f"/api/docflow/memories/{created['id']}")
            self.assertEqual(deleted.status_code, 200)
            self.assertEqual(client.get("/api/docflow/memories/memory-admin").json(), [])

    def test_custom_template_can_be_created_and_listed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            main.docflow_repository = DocflowRepository(Path(directory) / "template.db")
            client = session_client("template-admin")
            payload = {
                "name": "上线复盘",
                "title": "智能客服上线复盘",
                "goal": "生成上线结论、风险和下一步行动",
                "source_text": "项目已完成灰度上线，仍有两个边界问题等待负责人确认并补充回归测试。",
                "audience": "管理层",
                "focus": "risk",
            }
            created = client.post("/api/docflow/templates", json=payload)
            self.assertEqual(created.status_code, 201)
            templates = client.get("/api/docflow/templates").json()
            self.assertEqual(len(templates), 1)
            self.assertEqual(templates[0]["name"], "上线复盘")

    def test_terminal_task_can_be_deleted_with_trace_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            coordinator = JobCoordinator(max_workers=1, max_pending=3)
            main.docflow_repository = DocflowRepository(Path(directory) / "delete.db")
            main.docflow_runtime = AgentRuntime(planner=RulePlanner())
            main.job_coordinator = coordinator
            client = session_client("delete-test")
            response = client.post(
                "/api/docflow/tasks",
                json={"title": "待删除任务", "goal": "生成项目周报", "text": SOURCE, "session_id": "delete-test"},
            )
            task = response.json()
            main.docflow_repository.add_memory("delete-test", "测试记忆", "保留内容", task["id"])

            deleted = client.delete(f"/api/docflow/tasks/{task['id']}")
            self.assertEqual(deleted.status_code, 200)
            self.assertEqual(deleted.json()["status"], "deleted")
            self.assertEqual(client.get(f"/api/docflow/tasks/{task['id']}").status_code, 404)
            memories = main.docflow_repository.list_memories("delete-test")
            self.assertEqual(len(memories), 1)
            self.assertIsNone(memories[0]["source_task_id"])
            with main.docflow_repository._connect() as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM docflow_runs").fetchone()[0], 0)
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM docflow_steps").fetchone()[0], 0)

            queued = main.docflow_repository.create_task("排队任务", "生成周报", SOURCE, "delete-test")
            blocked = client.delete(f"/api/docflow/tasks/{queued['id']}")
            self.assertEqual(blocked.status_code, 409)
            coordinator.shutdown()

    def test_ten_sequential_runs_keep_results_isolated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            coordinator = JobCoordinator(max_workers=2, max_pending=12)
            main.docflow_repository = DocflowRepository(Path(directory) / "multi-run.db")
            main.docflow_runtime = AgentRuntime(planner=RulePlanner())
            main.job_coordinator = coordinator
            client = session_client("multi-browser")
            owners = ["张甲", "李乙", "王丙", "赵丁", "周戊", "吴己", "郑庚", "孙辛", "钱壬", "冯癸"]
            task_ids = []
            for index, owner in enumerate(owners, start=1):
                marker = f"批次{index}接口超时"
                response = client.post(
                    "/api/docflow/tasks",
                    json={
                        "title": f"多轮回测-{index}",
                        "goal": "生成项目周报、风险清单和汇报大纲",
                        "text": f"项目：压力测试{index}。\n风险：{marker}。\n{owner}：9月{index}日前提交回归报告。",
                        "session_id": "multi-browser",
                    },
                )
                self.assertEqual(response.status_code, 201)
                task = response.json()
                task_ids.append(task["id"])
                artifact_text = "\n".join(task["result"]["artifacts"].values())
                self.assertIn(marker, artifact_text)
                self.assertIn(owner, artifact_text)
            self.assertEqual(len(set(task_ids)), 10)
            self.assertEqual(len(main.docflow_repository.list_tasks()), 10)
            coordinator.shutdown()

    def test_six_concurrent_runs_complete_without_cross_contamination(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            coordinator = JobCoordinator(max_workers=2, max_pending=8)
            main.docflow_repository = DocflowRepository(Path(directory) / "concurrent-runs.db")
            main.docflow_runtime = AgentRuntime(planner=RulePlanner())
            main.job_coordinator = coordinator
            client = session_client("concurrent-browser")
            submitted = []
            for index in range(1, 7):
                marker = f"并发批次{index}专属风险"
                response = client.post(
                    "/api/docflow/jobs",
                    json={
                        "title": f"并发回测-{index}",
                        "goal": "生成项目周报和风险清单",
                        "text": f"项目：并发回测{index}。\n风险：{marker}尚未解决。\n测试负责人计划9月{index}日前完成复核。",
                        "session_id": "concurrent-browser",
                    },
                    headers={"Idempotency-Key": f"concurrent-run-{index}"},
                )
                self.assertEqual(response.status_code, 202)
                submitted.append((response.json()["task_id"], marker))

            deadline = time.time() + 8
            pending = {task_id for task_id, _ in submitted}
            while pending and time.time() < deadline:
                for task_id in list(pending):
                    if client.get(f"/api/docflow/jobs/{task_id}").json()["terminal"]:
                        pending.remove(task_id)
                time.sleep(0.03)
            self.assertFalse(pending)
            for task_id, marker in submitted:
                task = client.get(f"/api/docflow/tasks/{task_id}").json()
                artifact_text = "\n".join(task["result"]["artifacts"].values())
                self.assertIn(marker, artifact_text)
                for _, other_marker in submitted:
                    if other_marker != marker:
                        self.assertNotIn(other_marker, artifact_text)
            coordinator.shutdown()

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
            self.assertTrue(payload["service"]["ok"])
            self.assertFalse(payload["runtime"]["model_configured"])
            self.assertEqual(payload["runtime"]["model_reachability"], "not_configured")
            self.assertTrue(payload["boundaries"]["queued_jobs_durable"])
            self.assertFalse(payload["boundaries"]["running_jobs_resumable"])
            self.assertTrue(payload["boundaries"]["public_access_control"])
            self.assertEqual(payload["boundaries"]["visitor_session_scope"], "browser_token")
            self.assertTrue(payload["boundaries"]["demo_data_only"])
            self.assertFalse(payload["boundaries"]["production_authentication"])
            self.assertFalse(payload["boundaries"]["public_demo_safe"])
            coordinator.shutdown()

    def test_corrupted_pdf_returns_422_for_sync_and_async_uploads(self) -> None:
        client = session_client("broken-pdf")
        data = {"title": "损坏文件", "goal": "生成项目周报和风险清单", "session_id": "broken-pdf"}
        for endpoint in ("/api/docflow/tasks/file", "/api/docflow/jobs/file"):
            response = client.post(
                endpoint,
                data=data,
                files={"file": ("broken.pdf", b"%PDF-1.7\nthis is not a valid pdf", "application/pdf")},
            )
            self.assertEqual(response.status_code, 422)
            self.assertEqual(response.json()["detail"], "PDF 文件损坏或无法解析，请重新上传有效文件。")

    def test_idempotency_key_reuses_task_and_rejects_changed_payload(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            coordinator = JobCoordinator(max_workers=1, max_pending=3)
            main.docflow_repository = DocflowRepository(Path(directory) / "idempotency.db")
            main.docflow_runtime = AgentRuntime(planner=RulePlanner())
            main.job_coordinator = coordinator
            client = session_client("idem-session")
            payload = {"title": "幂等任务", "goal": "生成项目周报和风险清单", "text": SOURCE, "session_id": "idem-session"}
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

    def test_repository_preserves_queued_jobs_and_fails_interrupted_running_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = DocflowRepository(Path(directory) / "recovery.db")
            queued = repository.create_task("等待任务", "生成项目周报", SOURCE)
            running = repository.create_task("运行任务", "生成项目周报", SOURCE)
            repository.mark_task_running(running["id"])
            repository.create_run(running["id"])

            recovery = repository.recover_interrupted_tasks()
            self.assertEqual([task["id"] for task in recovery["queued_tasks"]], [queued["id"]])
            self.assertEqual(recovery["failed_running"], 1)
            self.assertEqual(repository.get_task(queued["id"])["status"], "queued")
            recovered = repository.get_task(running["id"])
            self.assertEqual(recovered["status"], "failed")
            self.assertEqual(recovered["runs"][0]["status"], "failed")
            self.assertIn("进程重启", recovered["runs"][0]["error"])

    def test_restart_requeues_durable_queued_job(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            coordinator = JobCoordinator(max_workers=1, max_pending=3)
            main.docflow_repository = DocflowRepository(Path(directory) / "durable-queue.db")
            main.docflow_runtime = AgentRuntime(planner=RulePlanner())
            main.job_coordinator = coordinator
            task = main.docflow_repository.create_task("重启恢复任务", "生成项目周报", SOURCE)

            report = main._recover_persisted_jobs()
            self.assertEqual(report["recovered_queued"], 1)
            self.assertEqual(report["failed_running"], 0)

            deadline = time.time() + 5
            recovered = main.docflow_repository.get_task(task["id"])
            while time.time() < deadline and recovered["status"] not in {"awaiting_review", "failed"}:
                time.sleep(0.05)
                recovered = main.docflow_repository.get_task(task["id"])
            self.assertEqual(recovered["status"], "awaiting_review")
            self.assertEqual(recovered["runs"][0]["status"], "completed")
            coordinator.shutdown()

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
            client = session_client("async-session")
            started = time.perf_counter()
            response = client.post(
                "/api/docflow/jobs",
                json={"title": "异步任务", "goal": "生成项目周报和风险清单", "text": SOURCE, "session_id": "async-session"},
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
                json={"title": "超出队列", "goal": "生成项目周报和风险清单", "text": SOURCE, "session_id": "async-session"},
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
            client = session_client("team-a-session")

            memory_response = client.post(
                "/api/docflow/memories",
                json={"session_id": "team-a-session", "memory_key": "汇报偏好", "content": "优先展示风险和截止时间"},
            )
            self.assertEqual(memory_response.status_code, 201)
            task_response = client.post(
                "/api/docflow/tasks",
                json={"title": "周报", "goal": "生成项目周报和风险清单", "text": SOURCE, "session_id": "team-a-session"},
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
            client = session_client("team-a-session")
            client.post(
                "/api/docflow/memories",
                json={"session_id": "team-a-session", "memory_key": "汇报偏好", "content": "优先展示风险"},
            )
            response = client.post(
                "/api/docflow/tasks",
                json={
                    "title": "上下文测试",
                    "goal": "生成项目周报和风险清单",
                    "text": SOURCE * 4,
                    "session_id": "team-a-session",
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

    def test_approved_task_exports_complete_artifact_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            main.docflow_repository = DocflowRepository(Path(directory) / "export.db")
            main.docflow_runtime = AgentRuntime(planner=RulePlanner())
            client = session_client("export-session")
            task = client.post(
                "/api/docflow/tasks",
                json={"title": "完整产出", "goal": "生成项目周报、风险清单和三页汇报大纲", "text": SOURCE, "session_id": "export-session"},
            ).json()

            blocked = client.get(f"/api/docflow/tasks/{task['id']}/export")
            self.assertEqual(blocked.status_code, 409)
            client.post(f"/api/docflow/tasks/{task['id']}/review", json={"action": "approve", "note": "内容已核对"})
            exported = client.get(f"/api/docflow/tasks/{task['id']}/export")

            self.assertEqual(exported.status_code, 200)
            self.assertIn("attachment", exported.headers["content-disposition"])
            self.assertIn("## 周报内容", exported.text)
            self.assertIn("## 风险与行动清单", exported.text)
            self.assertIn("## 三页汇报大纲", exported.text)
            self.assertIn("## 引用证据", exported.text)

    def test_agentops_summary_aggregates_quality_gates_and_review_feedback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            main.docflow_repository = DocflowRepository(Path(directory) / "agentops.db")
            main.docflow_runtime = AgentRuntime(planner=RulePlanner())
            client = session_client("eval-session")
            first = client.post(
                "/api/docflow/tasks",
                json={"title": "评测任务一", "goal": "生成项目周报和风险清单", "text": SOURCE, "session_id": "eval-session"},
            ).json()
            client.post(f"/api/docflow/tasks/{first['id']}/review", json={"action": "approve", "note": "引用完整"})
            client.post(
                "/api/docflow/tasks",
                json={"title": "评测任务二", "goal": "生成项目周报", "text": SOURCE, "session_id": "eval-session"},
            )

            response = client.get("/api/docflow/evaluations/summary")
            self.assertEqual(response.status_code, 200)
            summary = response.json()
            self.assertEqual(summary["scope"], "session_task_history")
            self.assertEqual(summary["metrics"]["task_count"], 2)
            self.assertEqual(summary["metrics"]["terminal_count"], 2)
            self.assertEqual(summary["metrics"]["successful_task_count"], 2)
            self.assertEqual(summary["metrics"]["execution_success_rate"], 1.0)
            self.assertEqual(summary["metrics"]["evaluated_count"], 2)
            self.assertEqual(summary["metrics"]["reviewed_count"], 1)
            self.assertEqual(summary["metrics"]["approval_rate"], 1.0)
            self.assertEqual(summary["metrics"]["citation_pass_rate"], 1.0)
            self.assertEqual(summary["metrics"]["verifier_human_miss_count"], 0)
            self.assertEqual(summary["mode_breakdown"][0]["mode"], "rules")
            self.assertEqual(summary["mode_breakdown"][0]["task_count"], 2)
            self.assertEqual(summary["mode_breakdown"][0]["latency_p50_ms"], summary["metrics"]["latency_p50_ms"])
            self.assertEqual(summary["mode_breakdown"][0]["total_tokens"], 0)
            self.assertIsNone(summary["metrics"]["estimated_cost_total"])
            self.assertEqual(summary["model_stage_breakdown"], [])
            self.assertTrue(all(gate["passed"] is not None for gate in summary["quality_gates"]))
            self.assertEqual(len(summary["recent_tasks"]), 2)


if __name__ == "__main__":
    unittest.main()
