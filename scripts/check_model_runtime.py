from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request
from typing import Any


SOURCE = """项目：智能客服知识库升级
本周进展：完成售后 FAQ 清洗与检索链路联调，24 条核心问法通过验收。
风险：退款政策文档仍有两个版本，负责人李明需在周五前确认最终口径。
行动：王芳负责补充退货运费边界案例，下周二完成回归测试。
会议结论：所有面向客户的回答必须附带当前知识库来源。"""
GOAL = "基于材料生成本周项目周报、风险清单和三页汇报大纲；每条结论标注来源。"


class AcceptanceError(RuntimeError):
    pass


def validate_readiness(payload: dict[str, Any]) -> dict[str, Any]:
    runtime = payload.get("runtime") if isinstance(payload.get("runtime"), dict) else {}
    if not runtime.get("model_configured"):
        raise AcceptanceError("MODEL_API_KEY is not configured on the target service")
    return runtime


def validate_model_run(task: dict[str, Any]) -> dict[str, Any]:
    if task.get("status") != "awaiting_review":
        raise AcceptanceError(f"task status is {task.get('status')!r}, expected 'awaiting_review'")
    result = task.get("result") if isinstance(task.get("result"), dict) else {}
    verification = result.get("verification") if isinstance(result.get("verification"), dict) else {}
    if not (verification.get("overall_passed") or verification.get("passed")):
        raise AcceptanceError("verification did not pass")

    execution = result.get("execution") if isinstance(result.get("execution"), dict) else {}
    calls = execution.get("model_calls") if isinstance(execution.get("model_calls"), list) else []
    usage = execution.get("model_usage") if isinstance(execution.get("model_usage"), dict) else {}
    if not execution.get("model_path_complete"):
        reasons = "; ".join(str(item) for item in execution.get("fallback_reasons", []))
        raise AcceptanceError(f"real model path was not completed: {reasons or 'no reason recorded'}")
    if len(calls) < 2 or int(execution.get("model_call_count") or 0) < 2:
        raise AcceptanceError("separate Planner and content model calls were not recorded")
    stages = {str(call.get("stage") or "") for call in calls}
    if not {"planner", "content"}.issubset(stages):
        raise AcceptanceError("Planner and content stages are not both present")
    if int(usage.get("total_tokens") or 0) < 1:
        raise AcceptanceError("provider token usage is missing")
    missing_request_ids = [str(call.get("stage") or "unknown") for call in calls if not call.get("request_id")]
    if missing_request_ids:
        raise AcceptanceError(f"provider request ID missing for: {', '.join(missing_request_ids)}")
    failed = [str(call.get("stage") or "unknown") for call in calls if call.get("status") != "succeeded"]
    if failed:
        raise AcceptanceError(f"model call did not succeed for: {', '.join(failed)}")
    return execution


class Client:
    def __init__(self, base_url: str, username: str, password: str, session_id: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.headers = {"X-DocFlow-Session": session_id}
        if bool(username) != bool(password):
            raise AcceptanceError("username and password must be provided together")
        if username and password:
            encoded = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
            self.headers["Authorization"] = f"Basic {encoded}"

    def request(self, method: str, path: str, body: dict[str, Any] | None = None) -> tuple[Any, int]:
        headers = dict(self.headers)
        data = None
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=utf-8"
        request = urllib.request.Request(f"{self.base_url}{path}", data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                raw = response.read()
                status = int(response.status)
                content_type = response.headers.get("Content-Type", "")
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")[:500]
            raise AcceptanceError(f"{method} {path} returned HTTP {error.code}: {detail}") from error
        if "application/json" in content_type:
            return json.loads(raw.decode("utf-8")), status
        return raw, status


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate the deployed DocFlow real-model path.")
    parser.add_argument("--base-url", default=os.getenv("DOCFLOW_BASE_URL", "http://127.0.0.1:8010"))
    parser.add_argument("--username", default=os.getenv("DOCFLOW_DEMO_USERNAME", ""))
    args = parser.parse_args()

    password = os.getenv("DOCFLOW_DEMO_PASSWORD", "")
    session_id = f"model-acceptance-{int(time.time())}"
    client = Client(args.base_url, args.username, password, session_id)

    health, _ = client.request("GET", "/health")
    if health.get("status") != "ok":
        raise AcceptanceError("health check failed")
    ready, _ = client.request("GET", "/ready")
    validate_readiness(ready)

    task, _ = client.request(
        "POST",
        "/api/docflow/tasks",
        {
            "title": "Real model acceptance",
            "session_id": session_id,
            "goal": GOAL,
            "text": SOURCE,
        },
    )
    execution = validate_model_run(task)

    review, _ = client.request(
        "POST",
        f"/api/docflow/tasks/{task['id']}/review",
        {"action": "approve", "note": "Automated real-model acceptance passed"},
    )
    if review.get("status") != "approved":
        raise AcceptanceError("human-review endpoint did not approve the task")
    exported, status = client.request("GET", f"/api/docflow/tasks/{task['id']}/export")
    if status != 200 or not isinstance(exported, bytes) or len(exported) < 20:
        raise AcceptanceError("approved artifact export failed")

    usage = execution["model_usage"]
    print("[PASS] DocFlow real-model acceptance completed")
    print(
        f"task={task['id']} calls={execution['model_call_count']} "
        f"tokens={usage['total_tokens']} model_latency_ms={execution['model_latency_ms']}"
    )
    for call in execution["model_calls"]:
        print(
            f"stage={call['stage']} provider={call['provider']} model={call['model']} "
            f"request_id={call['request_id']} tokens={call['usage']['total_tokens']} "
            f"latency_ms={call['latency_ms']}"
        )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AcceptanceError, urllib.error.URLError, json.JSONDecodeError) as error:
        print(f"[FAIL] {error}", file=sys.stderr)
        raise SystemExit(1)
