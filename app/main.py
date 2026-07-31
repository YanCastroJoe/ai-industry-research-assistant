from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from dotenv import load_dotenv

from .analysis import analyze
from .docflow import AgentRuntime
from .docflow_repository import DocflowRepository
from .document_text import extract_uploaded_text
from .repository import TaskRepository

ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = ROOT / "static"
load_dotenv(ROOT / ".env")
repository = TaskRepository(ROOT / "data" / "research.db")
docflow_repository = DocflowRepository(ROOT / "data" / "research.db")
docflow_runtime = AgentRuntime()

app = FastAPI(title="AI 产业研究助手", version="0.1.0")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


class ResearchRequest(BaseModel):
    title: str = Field(default="未命名研究", max_length=100)
    text: str = Field(min_length=10, max_length=30000)
    material_type: str = Field(default="auto", pattern="^(auto|company|industry|macro)$")


class ReviewRequest(BaseModel):
    action: str = Field(pattern="^(approve|reject)$")
    note: str = Field(default="", max_length=1000)


class DocflowTaskRequest(BaseModel):
    title: str = Field(default="未命名协作任务", max_length=100)
    goal: str = Field(min_length=5, max_length=1000)
    text: str = Field(min_length=20, max_length=60000)


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
def create_docflow_task(request: DocflowTaskRequest) -> dict:
    return _run_docflow_task(request.title, request.goal, request.text)


def _run_docflow_task(title_value: str, goal_value: str, text: str) -> dict:
    title = title_value.strip() or "未命名协作任务"
    goal = goal_value.strip()
    task = docflow_repository.create_task(title, goal, text)
    run_id = docflow_repository.create_run(task["id"])
    try:
        result = docflow_runtime.execute(
            goal,
            text,
            trace_callback=lambda step: docflow_repository.record_step(run_id, step),
        )
        return docflow_repository.complete_run(task["id"], run_id, result["plan"], result)
    except Exception as error:
        docflow_repository.fail_run(task["id"], run_id, str(error))
        raise HTTPException(status_code=500, detail="Agent 运行失败，请查看运行轨迹。") from error


@app.post("/api/docflow/tasks/file", status_code=201)
async def create_docflow_task_from_file(
    file: UploadFile = File(...),
    title: str = Form(default="未命名协作任务"),
    goal: str = Form(...),
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
    return _run_docflow_task(title.strip() or fallback_title, goal, text)


@app.get("/api/docflow/tasks")
def list_docflow_tasks() -> list[dict]:
    return docflow_repository.list_tasks()


@app.get("/api/docflow/tasks/{task_id}")
def get_docflow_task(task_id: int) -> dict:
    try:
        return docflow_repository.get_task(task_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="协作任务不存在") from error


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
    return {"status": "ok"}
