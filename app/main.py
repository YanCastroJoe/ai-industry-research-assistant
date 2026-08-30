from __future__ import annotations

import hashlib
import json
import logging
import re
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from dotenv import load_dotenv

from .analysis import analyze
from .config import load_runtime_config
from .docflow import AgentRuntime
from .docflow_repository import DocflowRepository
from .document_text import extract_uploaded_text
from .job_coordinator import JobCoordinator, QueueCapacityError
from .observability import configure_event_logger, log_event
from .repository import TaskRepository

ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = ROOT / "static"
load_dotenv(ROOT / ".env")
runtime_config = load_runtime_config()
event_logger = configure_event_logger()
repository = TaskRepository(ROOT / "data" / "research.db")
docflow_repository = DocflowRepository(ROOT / "data" / "research.db")
docflow_runtime = AgentRuntime()
job_coordinator = JobCoordinator(
    max_workers=runtime_config.max_workers,
    max_pending=runtime_config.max_pending,
)

app = FastAPI(title="AI 产业研究助手", version="0.1.0")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.middleware("http")
async def request_observability(request: Request, call_next):
    supplied_id = request.headers.get("X-Request-ID", "")
    request_id = supplied_id if re.fullmatch(r"[A-Za-z0-9._-]{1,64}", supplied_id) else uuid.uuid4().hex
    request.state.request_id = request_id
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception as error:
        log_event(
            event_logger,
            "http_request_failed",
            level=logging.ERROR,
            request_id=request_id,
            method=request.method,
            path=request.url.path,
            elapsed_ms=round((time.perf_counter() - started) * 1000),
            detail=type(error).__name__,
        )
        raise
    response.headers["X-Request-ID"] = request_id
    log_event(
        event_logger,
        "http_request_completed",
        request_id=request_id,
        method=request.method,
        path=request.url.path,
        status=response.status_code,
        elapsed_ms=round((time.perf_counter() - started) * 1000),
    )
    return response


class ResearchRequest(BaseModel):
    title: str = Field(default="未命名研究", max_length=100)
    text: str = Field(min_length=10, max_length=30000)
    material_type: str = Field(default="auto", pattern="^(auto|company|industry|macro)$")


class ReviewRequest(BaseModel):
    action: str = Field(pattern="^(approve|reject)$")
    note: str = Field(default="", max_length=1000)


class ContextConfigRequest(BaseModel):
    audience: str = Field(default="项目团队", min_length=2, max_length=100)
    focus: str = Field(default="balanced", pattern="^(balanced|risk|progress|actions)$")
    evidence_limit: int = Field(default=12, ge=4, le=12)
    memory_enabled: bool = True
    citation_policy: str = Field(default="strict", pattern="^(strict|standard)$")


class DocflowTaskRequest(BaseModel):
    title: str = Field(default="未命名协作任务", max_length=100)
    goal: str = Field(min_length=5, max_length=1000)
    text: str = Field(min_length=20, max_length=60000)
    session_id: str = Field(default="default", min_length=1, max_length=100)
    context_config: ContextConfigRequest = Field(default_factory=ContextConfigRequest)


class MemoryRequest(BaseModel):
    session_id: str = Field(default="default", min_length=1, max_length=100)
    memory_key: str = Field(min_length=2, max_length=100)
    content: str = Field(min_length=2, max_length=1000)


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.post("/api/research", status_code=201)
def create_research(request: ResearchRequest) -> dict:
    try:
        card = analyze(request.text, request.material_type).to_dict()
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return repository.create(request.title.strip() or "未命名研究", request.text, card["material_type"], card)


@app.post("/api/research/file", status_code=201)
async def create_research_from_file(
    file: UploadFile = File(...),
    title: str = Form(default="未命名研究"),
    material_type: str = Form(default="auto"),
) -> dict:
    if material_type not in {"auto", "company", "industry", "macro"}:
        raise HTTPException(status_code=422, detail="材料类型不合法")
    content = await file.read()
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="文件不能超过 10 MB。")
    try:
        text = extract_uploaded_text(file.filename or "upload", content)
        card = analyze(text, material_type).to_dict()
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    fallback_title = (file.filename or "未命名研究").rsplit(".", 1)[0]
    return repository.create(title.strip() or fallback_title, text, card["material_type"], card)


@app.get("/api/tasks")
def list_tasks() -> list[dict]:
    return repository.list()


@app.get("/api/tasks/{task_id}")
def get_task(task_id: int) -> dict:
    try:
        return repository.get(task_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="任务不存在") from error


@app.post("/api/tasks/{task_id}/review")
def review_task(task_id: int, request: ReviewRequest) -> dict:
    try:
        return repository.review(task_id, request.action, request.note)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="任务不存在") from error


@app.get("/api/tasks/{task_id}/export")
def export_task(task_id: int) -> PlainTextResponse:
    try:
        task = repository.get(task_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="任务不存在") from error
    if task["status"] != "approved":
        raise HTTPException(status_code=409, detail="请先完成人工审核，再导出简报。")
    card = task["card"]
    lines = [
        f"# {task['title']}",
        "",
        f"> 材料类型：{card['material_type']}｜审核状态：已通过",
        "",
        "## 事实摘要",
        card["summary"],
        "",
        "## 事实与证据",
    ]
    for fact in card["facts"]:
        lines.extend([f"- **事实：** {fact['claim']}", f"  - **原文证据：** {fact['evidence']}", f"  - **位置：** {fact['source_location']}"])
    industry = card.get("industry_analysis", {})
    lines.extend(["", "## 产业链推演", industry.get("industry_judgment", "未生成产业推演。")])
    lines.extend(["", "### 传导链路", *[f"- {item}" for item in industry.get("causal_chain", [])]])
    lines.extend(["", "### 产业方向与验证条件", *[f"- {item}" for item in industry.get("direction_analysis", [])]])
    lines.extend(["", "### 风险与反转条件", *[f"- {item}" for item in industry.get("risk_reversals", [])]])
    lines.extend(["", "## 影响维度", *[f"- {item}" for item in card["impact_dimensions"]]])
    lines.extend(["", "## 影响链路", *[f"- {item}" for item in card["impact_chain"]]])
    lines.extend(["", "## 待验证项", *[f"- {item}" for item in card["verification_items"]]])
    lines.extend(["", "## 风险提示", card["risk_notice"]])
    return PlainTextResponse("\n".join(lines), headers={"Content-Disposition": f'attachment; filename="research-{task_id}.md"'})


@app.post("/api/docflow/tasks", status_code=201)
def create_docflow_task(payload: DocflowTaskRequest, request: Request) -> dict:
    return _run_docflow_task(
        payload.title,
        payload.goal,
        payload.text,
        payload.session_id,
        payload.context_config.model_dump(),
        request_id=request.state.request_id,
    )


def _run_docflow_task(
    title_value: str,
    goal_value: str,
    text: str,
    session_id: str = "default",
    context_config: dict | None = None,
    request_id: str = "",
) -> dict:
    title = title_value.strip() or "未命名协作任务"
    goal = goal_value.strip()
    task = docflow_repository.create_task(title, goal, text, session_id.strip() or "default", context_config)
    return _execute_docflow_task(task, request_id=request_id)


def _execute_docflow_task(task: dict, resume: dict | None = None, request_id: str = "") -> dict:
    started = time.perf_counter()
    log_event(event_logger, "docflow_job_started", request_id=request_id, task_id=task["id"], status="running")
    run_id = None
    try:
        docflow_repository.mark_task_running(task["id"])
        parent_run_id = resume["run_id"] if resume else None
        run_id = docflow_repository.create_run(task["id"], parent_run_id=parent_run_id)
        context_config = task.get("context_config", {})
        memory_context = (
            docflow_repository.recall_memories(task["session_id"], task["goal"])
            if context_config.get("memory_enabled", True)
            else []
        )
        result = docflow_runtime.execute(
            task["goal"],
            task["source_text"],
            trace_callback=lambda step: docflow_repository.record_step(run_id, step),
            checkpoint_callback=lambda state, next_sequence: docflow_repository.save_checkpoint(run_id, state, next_sequence),
            plan_callback=lambda plan, planner: docflow_repository.save_plan(task["id"], run_id, plan, planner["mode"]),
            plan=task["plan"] if resume else None,
            resume_state=resume["checkpoint"] if resume else None,
            start_sequence=resume["next_sequence"] if resume else 1,
            memory_context=memory_context,
            context_config=context_config,
        )
        completed = docflow_repository.complete_run(task["id"], run_id, result["plan"], result)
        log_event(
            event_logger,
            "docflow_job_completed",
            request_id=request_id,
            task_id=task["id"],
            status=completed["status"],
            elapsed_ms=round((time.perf_counter() - started) * 1000),
        )
        return completed
    except Exception as error:
        if run_id is None:
            docflow_repository.fail_task(task["id"], str(error))
        else:
            docflow_repository.fail_run(task["id"], run_id, str(error))
        log_event(
            event_logger,
            "docflow_job_failed",
            level=logging.ERROR,
            request_id=request_id,
            task_id=task["id"],
            status="failed",
            elapsed_ms=round((time.perf_counter() - started) * 1000),
            detail=type(error).__name__,
        )
        raise HTTPException(status_code=500, detail="Agent 运行失败，请查看运行轨迹。") from error


def _task_fingerprint(title: str, goal: str, text: str, session_id: str, context_config: dict) -> str:
    payload = json.dumps(
        {"title": title, "goal": goal, "text": text, "session_id": session_id, "context_config": context_config},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _create_job_task(
    title: str,
    goal: str,
    text: str,
    session_id: str,
    context_config: dict,
    idempotency_key: str | None,
) -> tuple[dict, bool]:
    if not idempotency_key:
        return docflow_repository.create_task(title, goal, text, session_id, context_config), True
    normalized_key = idempotency_key.strip()
    if not re.fullmatch(r"[A-Za-z0-9._:-]{8,100}", normalized_key):
        raise HTTPException(status_code=422, detail="Idempotency-Key 需为 8–100 位字母、数字或 . _ : -")
    fingerprint = _task_fingerprint(title, goal, text, session_id, context_config)
    try:
        return docflow_repository.create_or_get_task(
            title, goal, text, session_id, context_config, normalized_key, fingerprint
        )
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


def _queue_docflow_task(task: dict, created: bool = True, request_id: str = "") -> dict:
    if not created:
        status = docflow_repository.job_status(task["id"])
        log_event(
            event_logger,
            "docflow_job_reused",
            request_id=request_id,
            task_id=task["id"],
            status=status["status"],
        )
        return {
            **status,
            "poll_url": f"/api/docflow/jobs/{task['id']}",
            "queue": job_coordinator.snapshot(task["id"]),
            "reused": True,
        }
    try:
        job_coordinator.submit(task["id"], lambda: _execute_docflow_task(task, request_id=request_id))
    except QueueCapacityError as error:
        docflow_repository.fail_queued_task(task["id"], str(error))
        raise HTTPException(status_code=429, detail=str(error)) from error
    log_event(event_logger, "docflow_job_queued", request_id=request_id, task_id=task["id"], status="queued")
    return {
        "task_id": task["id"],
        "status": "queued",
        "terminal": False,
        "progress_percent": 0,
        "poll_url": f"/api/docflow/jobs/{task['id']}",
        "queue": job_coordinator.snapshot(task["id"]),
        "reused": False,
    }


@app.post("/api/docflow/jobs", status_code=202)
def create_docflow_job(
    payload: DocflowTaskRequest,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict:
    title = payload.title.strip() or "未命名协作任务"
    goal = payload.goal.strip()
    session_id = payload.session_id.strip() or "default"
    context_config = payload.context_config.model_dump()
    task, created = _create_job_task(
        title,
        goal,
        payload.text,
        session_id,
        context_config,
        idempotency_key,
    )
    return _queue_docflow_task(task, created=created, request_id=request.state.request_id)


@app.post("/api/docflow/tasks/file", status_code=201)
async def create_docflow_task_from_file(
    request: Request,
    file: UploadFile = File(...),
    title: str = Form(default="未命名协作任务"),
    goal: str = Form(...),
    session_id: str = Form(default="default"),
    audience: str = Form(default="项目团队"),
    focus: str = Form(default="balanced"),
    evidence_limit: int = Form(default=12),
    memory_enabled: bool = Form(default=True),
    citation_policy: str = Form(default="strict"),
) -> dict:
    content = await file.read()
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="文件不能超过 10 MB。")
    if len(goal.strip()) < 5:
        raise HTTPException(status_code=422, detail="请至少输入 5 个字的协作目标。")
    try:
        text = extract_uploaded_text(file.filename or "upload", content)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    fallback_title = (file.filename or "未命名协作任务").rsplit(".", 1)[0]
    context_config = ContextConfigRequest(
        audience=audience,
        focus=focus,
        evidence_limit=evidence_limit,
        memory_enabled=memory_enabled,
        citation_policy=citation_policy,
    ).model_dump()
    return _run_docflow_task(
        title.strip() or fallback_title,
        goal,
        text,
        session_id,
        context_config,
        request_id=request.state.request_id,
    )


@app.post("/api/docflow/jobs/file", status_code=202)
async def create_docflow_job_from_file(
    request: Request,
    file: UploadFile = File(...),
    title: str = Form(default="未命名协作任务"),
    goal: str = Form(...),
    session_id: str = Form(default="default"),
    audience: str = Form(default="项目团队"),
    focus: str = Form(default="balanced"),
    evidence_limit: int = Form(default=12),
    memory_enabled: bool = Form(default=True),
    citation_policy: str = Form(default="strict"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict:
    content = await file.read()
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="文件不能超过 10 MB。")
    if len(goal.strip()) < 5:
        raise HTTPException(status_code=422, detail="请至少输入 5 个字的协作目标。")
    try:
        text = extract_uploaded_text(file.filename or "upload", content)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    fallback_title = (file.filename or "未命名协作任务").rsplit(".", 1)[0]
    context_config = ContextConfigRequest(
        audience=audience,
        focus=focus,
        evidence_limit=evidence_limit,
        memory_enabled=memory_enabled,
        citation_policy=citation_policy,
    ).model_dump()
    normalized_title = title.strip() or fallback_title
    normalized_goal = goal.strip()
    normalized_session = session_id.strip() or "default"
    task, created = _create_job_task(
        normalized_title,
        normalized_goal,
        text,
        normalized_session,
        context_config,
        idempotency_key,
    )
    return _queue_docflow_task(task, created=created, request_id=request.state.request_id)


@app.get("/api/docflow/jobs/{task_id}")
def get_docflow_job(task_id: int) -> dict:
    try:
        status = docflow_repository.job_status(task_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="协作任务不存在") from error
    status["queue"] = job_coordinator.snapshot(task_id)
    status["task_url"] = f"/api/docflow/tasks/{task_id}" if status["terminal"] else None
    return status


@app.get("/api/docflow/tasks")
def list_docflow_tasks() -> list[dict]:
    return docflow_repository.list_tasks()


@app.get("/api/docflow/tasks/{task_id}")
def get_docflow_task(task_id: int) -> dict:
    try:
        return docflow_repository.get_task(task_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="协作任务不存在") from error


@app.post("/api/docflow/tasks/{task_id}/retry")
def retry_docflow_task(task_id: int, request: Request) -> dict:
    try:
        task = docflow_repository.get_task(task_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="协作任务不存在") from error
    resume = docflow_repository.latest_failed_checkpoint(task_id)
    if task["status"] != "failed" or resume is None:
        raise HTTPException(status_code=409, detail="当前任务没有可恢复的失败检查点")
    return _execute_docflow_task(task, resume=resume, request_id=request.state.request_id)


@app.post("/api/docflow/memories", status_code=201)
def add_docflow_memory(request: MemoryRequest) -> dict:
    return docflow_repository.add_memory(
        request.session_id.strip() or "default",
        request.memory_key.strip(),
        request.content.strip(),
    )


@app.get("/api/docflow/memories/{session_id}")
def list_docflow_memories(session_id: str) -> list[dict]:
    return docflow_repository.list_memories(session_id)


@app.get("/api/docflow/evaluations/summary")
def get_docflow_evaluation_summary() -> dict:
    return docflow_repository.evaluation_summary()


@app.post("/api/docflow/tasks/{task_id}/review")
def review_docflow_task(task_id: int, request: ReviewRequest) -> dict:
    try:
        return docflow_repository.review_task(task_id, request.action, request.note)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="协作任务不存在") from error


@app.get("/api/docflow/tasks/{task_id}/export")
def export_docflow_task(task_id: int) -> PlainTextResponse:
    try:
        task = docflow_repository.get_task(task_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="协作任务不存在") from error
    if task["status"] != "approved":
        raise HTTPException(status_code=409, detail="请先完成人工审核，再导出文档。")
    result = task["result"]
    artifacts = result.get("artifacts", {})
    lines = [f"# {task['title']}", "", f"> 协作目标：{task['goal']}", ""]
    weekly_report = artifacts.get("weekly_report_markdown", "")
    if weekly_report.startswith("# 项目周报"):
        weekly_report = weekly_report.replace("# 项目周报", "## 周报内容", 1)
    weekly_report = weekly_report.replace(f"> 协作目标：{task['goal']}\n", "", 1)
    if weekly_report:
        lines.append(weekly_report)
    lines.extend(value for key, value in artifacts.items() if key != "weekly_report_markdown" and value)
    lines.extend(["", "## 引用证据"])
    lines.extend(f"- [{item['id']}] {item['excerpt']}（{item['source_location']}）" for item in result.get("evidence", []))
    return PlainTextResponse("\n".join(lines), headers={"Content-Disposition": f'attachment; filename="docflow-{task_id}.md"'})


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "docflow"}


@app.get("/ready")
def readiness() -> JSONResponse:
    database = docflow_repository.operational_status()
    payload = {
        "status": "ready" if database["ok"] else "not_ready",
        "database": database,
        "queue": job_coordinator.snapshot(),
        "runtime": runtime_config.public_dict(),
        "boundaries": {
            "queue_scope": "single_process",
            "queued_jobs_durable": True,
            "running_jobs_resumable": False,
            "task_history_persisted": True,
        },
    }
    return JSONResponse(status_code=200 if database["ok"] else 503, content=payload)


def _recover_persisted_jobs() -> dict:
    recovery = docflow_repository.recover_interrupted_tasks()
    recovered = 0
    failed_to_requeue = 0
    for task in recovery["queued_tasks"]:
        request_id = f"restart-recovery-{task['id']}"
        try:
            job_coordinator.submit(
                task["id"],
                lambda task=task, request_id=request_id: _execute_docflow_task(task, request_id=request_id),
            )
            recovered += 1
        except QueueCapacityError as error:
            failed_to_requeue += 1
            docflow_repository.fail_queued_task(
                task["id"],
                f"应用重启后恢复队列失败：{error}",
            )
    log_event(
        event_logger,
        "docflow_restart_recovery",
        recovered_queued=recovered,
        failed_running=recovery["failed_running"],
        failed_to_requeue=failed_to_requeue,
    )
    return {
        "recovered_queued": recovered,
        "failed_running": recovery["failed_running"],
        "failed_to_requeue": failed_to_requeue,
    }


_recover_persisted_jobs()
