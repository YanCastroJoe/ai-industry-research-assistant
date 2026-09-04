from __future__ import annotations

import json
import math
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path


class DocflowRepository:
    """Persistent storage for the document-agent workspace and its run trace."""

    def __init__(self, database_path: Path):
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.database_path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = NORMAL")
            connection.execute(
                """CREATE TABLE IF NOT EXISTS docflow_tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    goal TEXT NOT NULL,
                    source_text TEXT NOT NULL,
                    session_id TEXT NOT NULL DEFAULT 'default',
                    context_config_json TEXT NOT NULL DEFAULT '{}',
                    idempotency_key TEXT,
                    request_fingerprint TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL,
                    reviewer_note TEXT NOT NULL DEFAULT '',
                    plan_json TEXT NOT NULL DEFAULT '[]',
                    result_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )"""
            )
            columns = {row[1] for row in connection.execute("PRAGMA table_info(docflow_tasks)").fetchall()}
            if "reviewer_note" not in columns:
                connection.execute("ALTER TABLE docflow_tasks ADD COLUMN reviewer_note TEXT NOT NULL DEFAULT ''")
            if "context_config_json" not in columns:
                connection.execute("ALTER TABLE docflow_tasks ADD COLUMN context_config_json TEXT NOT NULL DEFAULT '{}'")
            if "idempotency_key" not in columns:
                connection.execute("ALTER TABLE docflow_tasks ADD COLUMN idempotency_key TEXT")
            if "request_fingerprint" not in columns:
                connection.execute("ALTER TABLE docflow_tasks ADD COLUMN request_fingerprint TEXT NOT NULL DEFAULT ''")
            connection.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_docflow_tasks_idempotency_key ON docflow_tasks(idempotency_key) WHERE idempotency_key IS NOT NULL"
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS docflow_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id INTEGER NOT NULL,
                    parent_run_id INTEGER,
                    status TEXT NOT NULL,
                    planner_mode TEXT NOT NULL DEFAULT '',
                    started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    completed_at TEXT,
                    token_estimate INTEGER NOT NULL DEFAULT 0,
                    cost_estimate REAL NOT NULL DEFAULT 0,
                    checkpoint_json TEXT NOT NULL DEFAULT '{}',
                    next_sequence INTEGER NOT NULL DEFAULT 1,
                    error TEXT NOT NULL DEFAULT '',
                    FOREIGN KEY(task_id) REFERENCES docflow_tasks(id)
                )"""
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS docflow_steps (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id INTEGER NOT NULL,
                    sequence INTEGER NOT NULL,
                    attempt INTEGER NOT NULL DEFAULT 1,
                    phase TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    input_json TEXT NOT NULL DEFAULT '{}',
                    output_json TEXT NOT NULL DEFAULT '{}',
                    error TEXT NOT NULL DEFAULT '',
                    error_type TEXT NOT NULL DEFAULT '',
                    retryable INTEGER NOT NULL DEFAULT 0,
                    elapsed_ms INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(run_id) REFERENCES docflow_runs(id)
                )"""
            )

            task_columns = {row[1] for row in connection.execute("PRAGMA table_info(docflow_tasks)").fetchall()}
            if "session_id" not in task_columns:
                connection.execute("ALTER TABLE docflow_tasks ADD COLUMN session_id TEXT NOT NULL DEFAULT 'default'")
            run_columns = {row[1] for row in connection.execute("PRAGMA table_info(docflow_runs)").fetchall()}
            run_migrations = {
                "parent_run_id": "INTEGER",
                "checkpoint_json": "TEXT NOT NULL DEFAULT '{}'",
                "next_sequence": "INTEGER NOT NULL DEFAULT 1",
                "error": "TEXT NOT NULL DEFAULT ''",
                "planner_mode": "TEXT NOT NULL DEFAULT ''",
            }
            for column, definition in run_migrations.items():
                if column not in run_columns:
                    connection.execute(f"ALTER TABLE docflow_runs ADD COLUMN {column} {definition}")
            step_columns = {row[1] for row in connection.execute("PRAGMA table_info(docflow_steps)").fetchall()}
            step_migrations = {
                "attempt": "INTEGER NOT NULL DEFAULT 1",
                "error_type": "TEXT NOT NULL DEFAULT ''",
                "retryable": "INTEGER NOT NULL DEFAULT 0",
            }
            for column, definition in step_migrations.items():
                if column not in step_columns:
                    connection.execute(f"ALTER TABLE docflow_steps ADD COLUMN {column} {definition}")
            connection.execute(
                """CREATE TABLE IF NOT EXISTS docflow_memories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    memory_key TEXT NOT NULL,
                    content TEXT NOT NULL,
                    source_task_id INTEGER,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(source_task_id) REFERENCES docflow_tasks(id)
                )"""
            )
            memory_columns = {row[1] for row in connection.execute("PRAGMA table_info(docflow_memories)").fetchall()}
            if "enabled" not in memory_columns:
                connection.execute("ALTER TABLE docflow_memories ADD COLUMN enabled INTEGER NOT NULL DEFAULT 1")
            if "updated_at" not in memory_columns:
                connection.execute("ALTER TABLE docflow_memories ADD COLUMN updated_at TEXT")
            connection.execute("UPDATE docflow_memories SET updated_at = created_at WHERE updated_at IS NULL")
            connection.execute(
                """CREATE TABLE IF NOT EXISTS docflow_templates (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    title TEXT NOT NULL,
                    goal TEXT NOT NULL,
                    source_text TEXT NOT NULL,
                    audience TEXT NOT NULL DEFAULT '项目团队',
                    focus TEXT NOT NULL DEFAULT 'balanced',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )"""
            )

    def create_task(
        self,
        title: str,
        goal: str,
        source_text: str,
        session_id: str = "default",
        context_config: dict | None = None,
    ) -> dict:
        with self._connect() as connection:
            cursor = connection.execute(
                """INSERT INTO docflow_tasks (title, goal, source_text, session_id, context_config_json, status)
                   VALUES (?, ?, ?, ?, ?, 'queued')""",
                (title, goal, source_text, session_id, json.dumps(context_config or {}, ensure_ascii=False)),
            )
            task_id = cursor.lastrowid
        return self.get_task(task_id)

    def create_or_get_task(
        self,
        title: str,
        goal: str,
        source_text: str,
        session_id: str,
        context_config: dict,
        idempotency_key: str,
        request_fingerprint: str,
    ) -> tuple[dict, bool]:
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT id, request_fingerprint FROM docflow_tasks WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            if existing is not None:
                if existing["request_fingerprint"] != request_fingerprint:
                    raise ValueError("相同 Idempotency-Key 不能用于不同任务内容")
                task_id = existing["id"]
                created = False
            else:
                try:
                    cursor = connection.execute(
                        """INSERT INTO docflow_tasks
                           (title, goal, source_text, session_id, context_config_json, idempotency_key, request_fingerprint, status)
                           VALUES (?, ?, ?, ?, ?, ?, ?, 'queued')""",
                        (
                            title,
                            goal,
                            source_text,
                            session_id,
                            json.dumps(context_config, ensure_ascii=False),
                            idempotency_key,
                            request_fingerprint,
                        ),
                    )
                    task_id = int(cursor.lastrowid)
                    created = True
                except sqlite3.IntegrityError:
                    existing = connection.execute(
                        "SELECT id, request_fingerprint FROM docflow_tasks WHERE idempotency_key = ?",
                        (idempotency_key,),
                    ).fetchone()
                    if existing is None or existing["request_fingerprint"] != request_fingerprint:
                        raise ValueError("相同 Idempotency-Key 不能用于不同任务内容")
                    task_id = existing["id"]
                    created = False
        return self.get_task(task_id), created

    def recover_interrupted_tasks(self) -> dict:
        """Return durable queued jobs and close jobs interrupted while running."""
        message = "应用进程重启导致执行中的后台任务中断，请从检查点重试或重新提交任务。"
        with self._connect() as connection:
            queued = connection.execute(
                "SELECT id FROM docflow_tasks WHERE status = 'queued' ORDER BY id"
            ).fetchall()
            running = connection.execute(
                "SELECT id FROM docflow_tasks WHERE status = 'running' ORDER BY id"
            ).fetchall()
            running_ids = [row["id"] for row in running]
            if running_ids:
                placeholders = ",".join("?" for _ in running_ids)
                connection.execute(
                    f"UPDATE docflow_tasks SET status = 'failed', reviewer_note = ?, updated_at = CURRENT_TIMESTAMP WHERE id IN ({placeholders})",
                    (message, *running_ids),
                )
                connection.execute(
                    f"UPDATE docflow_runs SET status = 'failed', completed_at = CURRENT_TIMESTAMP, error = ? WHERE status = 'running' AND task_id IN ({placeholders})",
                    (message, *running_ids),
                )
        return {
            "queued_tasks": [self.get_task(row["id"]) for row in queued],
            "failed_running": len(running_ids),
        }

    def create_run(self, task_id: int, parent_run_id: int | None = None) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                "INSERT INTO docflow_runs (task_id, parent_run_id, status) VALUES (?, ?, 'running')",
                (task_id, parent_run_id),
            )
            run_id = int(cursor.lastrowid)
        return run_id

    def mark_task_running(self, task_id: int) -> None:
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE docflow_tasks SET status = 'running', updated_at = CURRENT_TIMESTAMP WHERE id = ? AND status = 'queued'",
                (task_id,),
            )
            if cursor.rowcount == 0:
                row = connection.execute("SELECT status FROM docflow_tasks WHERE id = ?", (task_id,)).fetchone()
                if row is None:
                    raise KeyError(task_id)

    def fail_queued_task(self, task_id: int, message: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE docflow_tasks SET status = 'failed', reviewer_note = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ? AND status = 'queued'",
                (message, task_id),
            )

    def fail_task(self, task_id: int, message: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE docflow_tasks SET status = 'failed', reviewer_note = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (message, task_id),
            )

    def job_status(self, task_id: int) -> dict:
        task = self.get_task(task_id)
        runs = task.get("runs", [])
        latest_run = runs[0] if runs else None
        steps = latest_run.get("steps", []) if latest_run else []
        completed_sequences = {step["sequence"] for step in steps if step["status"] == "completed"}
        planned_steps = len(task.get("plan", []))
        current_tool = steps[-1]["tool_name"] if steps else ""
        retry_count = max(0, len(steps) - len({step["sequence"] for step in steps}))
        terminal = task["status"] in {"awaiting_review", "approved", "rejected", "failed"}
        if terminal:
            progress = 100
        elif task["status"] == "queued":
            progress = 0
        elif planned_steps:
            progress = min(95, max(5, round(len(completed_sequences) / planned_steps * 100)))
        else:
            progress = 5
        return {
            "task_id": task_id,
            "status": task["status"],
            "terminal": terminal,
            "progress_percent": progress,
            "planned_steps": planned_steps,
            "completed_steps": len(completed_sequences),
            "current_tool": current_tool,
            "retry_count": retry_count,
            "error": latest_run.get("error", "") if latest_run else task.get("reviewer_note", ""),
            "updated_at": task["updated_at"],
        }

    def operational_status(self) -> dict:
        with self._connect() as connection:
            quick_check = connection.execute("PRAGMA quick_check").fetchone()[0]
            journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
            busy_timeout = connection.execute("PRAGMA busy_timeout").fetchone()[0]
        return {
            "ok": quick_check == "ok",
            "quick_check": quick_check,
            "journal_mode": journal_mode,
            "busy_timeout_ms": busy_timeout,
        }

    def record_step(self, run_id: int, step: dict) -> None:
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO docflow_steps
                (run_id, sequence, attempt, phase, tool_name, status, input_json, output_json, error, error_type, retryable, elapsed_ms)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    run_id,
                    step["sequence"],
                    step.get("attempt", 1),
                    step["phase"],
                    step["tool_name"],
                    step["status"],
                    json.dumps(step.get("input", {}), ensure_ascii=False),
                    json.dumps(step.get("output", {}), ensure_ascii=False),
                    step.get("error", ""),
                    step.get("error_type", ""),
                    int(step.get("retryable", False)),
                    step.get("elapsed_ms", 0),
                ),
            )

    def save_checkpoint(self, run_id: int, state: dict, next_sequence: int) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE docflow_runs SET checkpoint_json = ?, next_sequence = ? WHERE id = ?",
                (json.dumps(state, ensure_ascii=False), next_sequence, run_id),
            )

    def save_plan(self, task_id: int, run_id: int, plan: list[dict], planner_mode: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE docflow_tasks SET plan_json = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (json.dumps(plan, ensure_ascii=False), task_id),
            )
            connection.execute(
                "UPDATE docflow_runs SET planner_mode = ? WHERE id = ?",
                (planner_mode, run_id),
            )

    def complete_run(self, task_id: int, run_id: int, plan: list[dict], result: dict) -> dict:
        execution = result.get("execution", {})
        actual_tokens = int(execution.get("model_usage", {}).get("total_tokens", 0) or 0)
        token_estimate = actual_tokens or len(json.dumps(result, ensure_ascii=False)) // 4
        actual_cost = execution.get("estimated_cost")
        cost_estimate = float(actual_cost) if actual_cost is not None else 0.0
        with self._connect() as connection:
            connection.execute(
                """UPDATE docflow_tasks
                   SET status = 'awaiting_review', plan_json = ?, result_json = ?, updated_at = CURRENT_TIMESTAMP
                   WHERE id = ?""",
                (json.dumps(plan, ensure_ascii=False), json.dumps(result, ensure_ascii=False), task_id),
            )
            connection.execute(
                """UPDATE docflow_runs
                   SET status = 'completed', completed_at = CURRENT_TIMESTAMP, token_estimate = ?, cost_estimate = ?
                   WHERE id = ?""",
                (token_estimate, cost_estimate, run_id),
            )
        return self.get_task(task_id)

    def fail_run(self, task_id: int, run_id: int, message: str) -> None:
        with self._connect() as connection:
            connection.execute("UPDATE docflow_tasks SET status = 'failed', updated_at = CURRENT_TIMESTAMP WHERE id = ?", (task_id,))
            connection.execute(
                "UPDATE docflow_runs SET status = 'failed', completed_at = CURRENT_TIMESTAMP, error = ? WHERE id = ?",
                (message, run_id),
            )

    def latest_failed_checkpoint(self, task_id: int) -> dict | None:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT id, checkpoint_json, next_sequence
                   FROM docflow_runs WHERE task_id = ? AND status = 'failed'
                   ORDER BY id DESC LIMIT 1""",
                (task_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "run_id": row["id"],
            "checkpoint": json.loads(row["checkpoint_json"] or "{}"),
            "next_sequence": row["next_sequence"],
        }

    def add_memory(
        self,
        session_id: str,
        memory_key: str,
        content: str,
        source_task_id: int | None = None,
    ) -> dict:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """SELECT * FROM docflow_memories
                   WHERE session_id = ? AND memory_key = ?
                   ORDER BY id DESC LIMIT 1""",
                (session_id, memory_key),
            ).fetchone()
            if existing is None:
                cursor = connection.execute(
                    """INSERT INTO docflow_memories (session_id, memory_key, content, source_task_id, updated_at)
                       VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)""",
                    (session_id, memory_key, content, source_task_id),
                )
                memory_id = int(cursor.lastrowid)
                operation = "created"
            else:
                memory_id = int(existing["id"])
                unchanged = existing["content"] == content and bool(existing["enabled"])
                operation = "unchanged" if unchanged else "updated"
                if operation == "updated" or (source_task_id is not None and existing["source_task_id"] != source_task_id):
                    connection.execute(
                        """UPDATE docflow_memories
                           SET content = ?, source_task_id = ?, enabled = 1, updated_at = CURRENT_TIMESTAMP
                           WHERE id = ?""",
                        (content, source_task_id, memory_id),
                    )
            row = connection.execute("SELECT * FROM docflow_memories WHERE id = ?", (memory_id,)).fetchone()
        result = dict(row)
        result["operation"] = operation
        return result

    def recall_memories(self, session_id: str, query: str, limit: int = 5) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM docflow_memories WHERE session_id = ? AND enabled = 1 ORDER BY id DESC LIMIT 50",
                (session_id,),
            ).fetchall()
        terms = set(re.findall(r"[A-Za-z0-9]{2,}", query.lower()))
        for sequence in re.findall(r"[\u4e00-\u9fff]{2,}", query):
            terms.update(sequence[index : index + 2] for index in range(len(sequence) - 1))
        ranked = []
        unique_rows = []
        seen_keys = set()
        for row in rows:
            if row["memory_key"] in seen_keys:
                continue
            seen_keys.add(row["memory_key"])
            unique_rows.append(row)
        for recency, row in enumerate(unique_rows):
            item = dict(row)
            haystack = f"{item['memory_key']} {item['content']}".lower()
            overlap_score = sum(term in haystack for term in terms)
            preference_score = 2 if any(
                marker in haystack
                for marker in (
                    "协作偏好", "输出偏好", "表达偏好", "结构偏好", "汇报偏好", "受众偏好",
                    "风险优先", "高风险", "进展优先", "里程碑", "行动项优先", "负责人", "截止时间",
                )
            ) else 0
            score = preference_score + min(overlap_score, 3)
            ranked.append((score, -recency, item))
        ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return [item for score, _, item in ranked[:limit] if score >= 2]

    def list_memories(self, session_id: str, limit: int = 20) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM docflow_memories WHERE session_id = ? ORDER BY id DESC",
                (session_id,),
            ).fetchall()
        result = []
        seen_keys = set()
        for row in rows:
            if row["memory_key"] in seen_keys:
                continue
            seen_keys.add(row["memory_key"])
            result.append(dict(row))
            if len(result) >= limit:
                break
        return result

    def get_memory(self, memory_id: int) -> dict:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM docflow_memories WHERE id = ?",
                (memory_id,),
            ).fetchone()
        if row is None:
            raise KeyError(memory_id)
        return dict(row)

    def update_memory(
        self,
        memory_id: int,
        *,
        memory_key: str | None = None,
        content: str | None = None,
        enabled: bool | None = None,
    ) -> dict:
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT * FROM docflow_memories WHERE id = ?",
                (memory_id,),
            ).fetchone()
            if existing is None:
                raise KeyError(memory_id)
            next_key = memory_key if memory_key is not None else existing["memory_key"]
            next_content = content if content is not None else existing["content"]
            next_enabled = int(enabled) if enabled is not None else existing["enabled"]
            connection.execute(
                """UPDATE docflow_memories
                   SET memory_key = ?, content = ?, enabled = ?, updated_at = CURRENT_TIMESTAMP
                   WHERE id = ?""",
                (next_key, next_content, next_enabled, memory_id),
            )
            row = connection.execute("SELECT * FROM docflow_memories WHERE id = ?", (memory_id,)).fetchone()
        return dict(row)

    def delete_memory(self, memory_id: int) -> dict:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT id, session_id, memory_key FROM docflow_memories WHERE id = ?",
                (memory_id,),
            ).fetchone()
            if row is None:
                raise KeyError(memory_id)
            connection.execute("DELETE FROM docflow_memories WHERE id = ?", (memory_id,))
        return {"id": row["id"], "session_id": row["session_id"], "memory_key": row["memory_key"], "status": "deleted"}

    def create_template(
        self,
        name: str,
        title: str,
        goal: str,
        source_text: str,
        audience: str,
        focus: str,
    ) -> dict:
        with self._connect() as connection:
            cursor = connection.execute(
                """INSERT INTO docflow_templates (name, title, goal, source_text, audience, focus)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (name, title, goal, source_text, audience, focus),
            )
            row = connection.execute(
                "SELECT * FROM docflow_templates WHERE id = ?",
                (int(cursor.lastrowid),),
            ).fetchone()
        return dict(row)

    def list_templates(self) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM docflow_templates ORDER BY updated_at DESC, id DESC"
            ).fetchall()
        return [dict(row) for row in rows]

    def review_task(self, task_id: int, action: str, note: str) -> dict:
        status = "approved" if action == "approve" else "rejected"
        with self._connect() as connection:
            cursor = connection.execute(
                """UPDATE docflow_tasks
                   SET status = ?, reviewer_note = ?, updated_at = CURRENT_TIMESTAMP
                   WHERE id = ?""",
                (status, note, task_id),
            )
            if cursor.rowcount == 0:
                raise KeyError(task_id)
        return self.get_task(task_id)

    def list_tasks(self, session_id: str | None = None) -> list[dict]:
        with self._connect() as connection:
            if session_id is None:
                rows = connection.execute("SELECT * FROM docflow_tasks ORDER BY id DESC").fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM docflow_tasks WHERE session_id = ? ORDER BY id DESC",
                    (session_id,),
                ).fetchall()
        return [self._task_to_dict(row, include_source=False) for row in rows]

    def delete_task(self, task_id: int) -> dict:
        """Delete one terminal task and its trace without erasing shared session memory."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT id, title, status FROM docflow_tasks WHERE id = ?",
                (task_id,),
            ).fetchone()
            if row is None:
                raise KeyError(task_id)
            if row["status"] in {"queued", "running"}:
                raise ValueError("运行中或排队中的任务不能删除")
            connection.execute(
                "UPDATE docflow_memories SET source_task_id = NULL WHERE source_task_id = ?",
                (task_id,),
            )
            connection.execute(
                "DELETE FROM docflow_steps WHERE run_id IN (SELECT id FROM docflow_runs WHERE task_id = ?)",
                (task_id,),
            )
            connection.execute("DELETE FROM docflow_runs WHERE task_id = ?", (task_id,))
            connection.execute("DELETE FROM docflow_tasks WHERE id = ?", (task_id,))
        return {"id": row["id"], "title": row["title"], "status": "deleted"}

    def evaluation_summary(self, recent_limit: int = 8, session_id: str | None = None) -> dict:
        """Aggregate persisted outcomes and model telemetry into an auditable snapshot."""
        with self._connect() as connection:
            if session_id is None:
                rows = connection.execute("SELECT * FROM docflow_tasks ORDER BY updated_at DESC, id DESC").fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM docflow_tasks WHERE session_id = ? ORDER BY updated_at DESC, id DESC",
                    (session_id,),
                ).fetchall()
        tasks = [self._task_to_dict(row, include_source=False) for row in rows]
        evaluated = [task for task in tasks if task.get("result", {}).get("metrics")]
        reviewed = [task for task in tasks if task["status"] in {"approved", "rejected"}]
        terminal = [task for task in tasks if task["status"] in {"awaiting_review", "approved", "rejected", "failed"}]
        latencies = sorted(int(task["result"]["metrics"].get("elapsed_ms", 0)) for task in evaluated)

        def rate(numerator: int, denominator: int) -> float | None:
            return round(numerator / denominator, 4) if denominator else None

        def percentile(values: list[int], proportion: float) -> int | None:
            if not values:
                return None
            index = max(0, math.ceil(len(values) * proportion) - 1)
            return values[index]

        def execution(task: dict) -> dict:
            return task.get("result", {}).get("execution", {})

        def task_mode(task: dict) -> str:
            result = task.get("result", {})
            return str(execution(task).get("content_mode") or result.get("insights", {}).get("mode") or "unknown")

        def telemetry_summary(group: list[dict]) -> dict:
            group_latencies = sorted(int(task["result"]["metrics"].get("elapsed_ms", 0)) for task in group)
            model_group = [task for task in group if int(execution(task).get("model_call_count", 0) or 0) > 0]
            model_latencies = sorted(int(execution(task).get("model_latency_ms", 0) or 0) for task in model_group)
            priced = [task for task in model_group if execution(task).get("estimated_cost") is not None]
            currencies = {str(execution(task).get("cost_currency")) for task in priced if execution(task).get("cost_currency")}
            cost_rate_labels = sorted({
                str(execution(task).get("cost_rate_label"))
                for task in priced
                if execution(task).get("cost_rate_label")
            })
            model_calls = [
                call
                for task in model_group
                for call in execution(task).get("model_calls", [])
                if isinstance(call, dict)
            ]
            tokens = sum(int(execution(task).get("model_usage", {}).get("total_tokens", 0) or 0) for task in model_group)
            return {
                "task_count": len(group),
                "latency_p50_ms": percentile(group_latencies, 0.50),
                "latency_p95_ms": percentile(group_latencies, 0.95),
                "model_latency_p50_ms": percentile(model_latencies, 0.50),
                "model_latency_p95_ms": percentile(model_latencies, 0.95),
                "degraded_task_rate": rate(sum(bool(execution(task).get("degraded")) for task in group), len(group)),
                "retry_task_rate": rate(sum(int(task["result"]["metrics"].get("retry_count", 0)) > 0 for task in group), len(group)),
                "model_path_complete_rate": rate(sum(bool(execution(task).get("model_path_complete")) for task in model_group), len(model_group)),
                "model_call_count": len(model_calls),
                "model_call_success_rate": rate(sum(call.get("status") == "succeeded" for call in model_calls), len(model_calls)),
                "total_tokens": tokens,
                "average_tokens_per_model_task": round(tokens / len(model_group), 2) if model_group else None,
                "estimated_cost_total": round(sum(float(execution(task)["estimated_cost"]) for task in priced), 8) if priced else None,
                "cost_coverage_rate": rate(len(priced), len(model_group)),
                "cost_currency": currencies.pop() if len(currencies) == 1 else None,
                "cost_rate_labels": cost_rate_labels,
            }

        citation_passed = sum(bool(task["result"].get("verification", {}).get("passed")) for task in evaluated)
        content_quality_evaluated = [
            task
            for task in evaluated
            if "content_quality_passed" in task["result"].get("verification", {})
        ]
        content_quality_passed = sum(
            bool(task["result"].get("verification", {}).get("content_quality_passed"))
            for task in content_quality_evaluated
        )
        tool_success_values = [float(task["result"]["metrics"].get("tool_success_rate", 0)) for task in evaluated]
        retry_tasks = sum(int(task["result"]["metrics"].get("retry_count", 0)) > 0 for task in evaluated)
        failed_tasks = sum(task["status"] == "failed" for task in terminal)
        approved_tasks = sum(task["status"] == "approved" for task in reviewed)
        verifier_human_misses = sum(
            task["status"] == "rejected"
            and bool(task.get("result", {}).get("verification", {}).get("overall_passed"))
            for task in reviewed
        )
        telemetry = telemetry_summary(evaluated)
        model_tasks = [task for task in evaluated if int(execution(task).get("model_call_count", 0) or 0) > 0]
        model_usage = {
            key: sum(int(execution(task).get("model_usage", {}).get(key, 0) or 0) for task in model_tasks)
            for key in (
                "prompt_tokens",
                "completion_tokens",
                "total_tokens",
                "prompt_cache_hit_tokens",
                "prompt_cache_miss_tokens",
            )
        }
        metrics = {
            "task_count": len(tasks),
            "terminal_count": len(terminal),
            "successful_task_count": len(evaluated),
            "evaluated_count": len(evaluated),
            "reviewed_count": len(reviewed),
            "execution_success_rate": rate(len(evaluated), len(terminal)),
            "citation_pass_rate": rate(citation_passed, len(evaluated)),
            "content_quality_pass_rate": rate(content_quality_passed, len(content_quality_evaluated)),
            "content_quality_evaluated_count": len(content_quality_evaluated),
            "tool_success_rate": round(sum(tool_success_values) / len(tool_success_values), 4) if tool_success_values else None,
            "approval_rate": rate(approved_tasks, len(reviewed)),
            "verifier_human_miss_rate": rate(verifier_human_misses, len(reviewed)),
            "verifier_human_miss_count": verifier_human_misses,
            "retry_task_rate": rate(retry_tasks, len(evaluated)),
            "degraded_task_rate": telemetry["degraded_task_rate"],
            "failed_task_rate": rate(failed_tasks, len(terminal)),
            "latency_p50_ms": percentile(latencies, 0.50),
            "latency_p95_ms": percentile(latencies, 0.95),
            "model_latency_p50_ms": telemetry["model_latency_p50_ms"],
            "model_latency_p95_ms": telemetry["model_latency_p95_ms"],
            "model_task_count": len(model_tasks),
            "model_call_count": telemetry["model_call_count"],
            "model_call_success_rate": telemetry["model_call_success_rate"],
            "model_usage": model_usage,
            "average_tokens_per_model_task": telemetry["average_tokens_per_model_task"],
            "estimated_cost_total": telemetry["estimated_cost_total"],
            "cost_coverage_rate": telemetry["cost_coverage_rate"],
            "cost_currency": telemetry["cost_currency"],
            "cost_rate_labels": telemetry["cost_rate_labels"],
        }
        gate_specs = [
            ("任务执行成功率", "execution_success_rate", ">=", 0.95, "只统计已进入终态的任务，不把排队或运行中任务算作失败"),
            ("引用通过率", "citation_pass_rate", ">=", 0.95, "生成结果中的 Evidence ID 可追溯"),
            ("内容质量通过率", "content_quality_pass_rate", ">=", 0.95, "负责人、日期、风险覆盖与汇报选材通过规则检查"),
            ("工具成功率", "tool_success_rate", ">=", 0.98, "计划内工具步骤成功完成"),
            ("失败任务率", "failed_task_rate", "<=", 0.05, "任务未因运行异常终止"),
        ]
        gates = []
        for name, key, operator, target, description in gate_specs:
            value = metrics[key]
            passed = None if value is None else (value >= target if operator == ">=" else value <= target)
            gates.append({"name": name, "metric": key, "value": value, "operator": operator, "target": target, "passed": passed, "description": description})

        mode_breakdown = []
        for mode in sorted({task_mode(task) for task in evaluated}):
            group = [task for task in evaluated if task_mode(task) == mode]
            mode_metrics = telemetry_summary(group)
            mode_metrics.update(
                {
                    "mode": mode,
                    "citation_pass_rate": rate(sum(bool(task["result"].get("verification", {}).get("passed")) for task in group), len(group)),
                    "content_quality_pass_rate": rate(sum(bool(task["result"].get("verification", {}).get("content_quality_passed")) for task in group), len(group)),
                }
            )
            mode_breakdown.append(mode_metrics)
            latency_target = 15000 if mode == "model" else 3000
            latency_value = mode_metrics["latency_p95_ms"]
            gates.append(
                {
                    "name": f"{mode} P95 运行耗时",
                    "metric": f"{mode}_latency_p95_ms",
                    "value": latency_value,
                    "operator": "<=",
                    "target": latency_target,
                    "passed": None if latency_value is None else latency_value <= latency_target,
                    "description": "固定演示环境诊断阈值，不是生产 SLA",
                }
            )

        model_calls = [
            call
            for task in model_tasks
            for call in execution(task).get("model_calls", [])
            if isinstance(call, dict)
        ]
        stage_breakdown = []
        for stage in sorted({str(call.get("stage") or "unknown") for call in model_calls}):
            calls = [call for call in model_calls if str(call.get("stage") or "unknown") == stage]
            call_latencies = sorted(int(call.get("latency_ms", 0) or 0) for call in calls)
            priced_calls = [call for call in calls if call.get("estimated_cost") is not None]
            currencies = {str(call.get("cost_currency")) for call in priced_calls if call.get("cost_currency")}
            stage_breakdown.append(
                {
                    "stage": stage,
                    "call_count": len(calls),
                    "success_rate": rate(sum(call.get("status") == "succeeded" for call in calls), len(calls)),
                    "latency_p50_ms": percentile(call_latencies, 0.50),
                    "latency_p95_ms": percentile(call_latencies, 0.95),
                    "total_tokens": sum(int(call.get("usage", {}).get("total_tokens", 0) or 0) for call in calls),
                    "estimated_cost_total": round(sum(float(call["estimated_cost"]) for call in priced_calls), 8) if priced_calls else None,
                    "cost_coverage_rate": rate(len(priced_calls), len(calls)),
                    "cost_currency": currencies.pop() if len(currencies) == 1 else None,
                }
            )

        recent = []
        for task in tasks[: max(1, min(recent_limit, 20))]:
            result = task.get("result", {})
            run_metrics = result.get("metrics", {})
            issues = []
            if task["status"] == "failed":
                issues.append("运行失败")
            if result and not result.get("verification", {}).get("passed", False):
                issues.append("引用校验未通过")
            if result and result.get("verification", {}).get("content_quality_passed") is False:
                issues.append("内容质量检查未通过")
            if int(run_metrics.get("retry_count", 0)) > 0:
                issues.append(f"发生 {run_metrics['retry_count']} 次重试")
            if bool(result.get("execution", {}).get("degraded")):
                issues.append("模型路径降级")
            if task["status"] == "rejected":
                issues.append("人工审核驳回")
            task_execution = result.get("execution", {})
            recent.append(
                {
                    "task_id": task["id"],
                    "title": task["title"],
                    "status": task["status"],
                    "updated_at": task["updated_at"],
                    "elapsed_ms": run_metrics.get("elapsed_ms"),
                    "executed_steps": run_metrics.get("executed_steps"),
                    "retry_count": run_metrics.get("retry_count"),
                    "citation_passed": result.get("verification", {}).get("passed") if result else None,
                    "planner_mode": result.get("planner", {}).get("mode", ""),
                    "execution_mode": task_execution.get("content_mode", result.get("insights", {}).get("mode", "")),
                    "degraded": bool(task_execution.get("degraded")),
                    "model_call_count": int(task_execution.get("model_call_count", 0) or 0),
                    "model_latency_ms": task_execution.get("model_latency_ms"),
                    "total_tokens": int(task_execution.get("model_usage", {}).get("total_tokens", 0) or 0),
                    "estimated_cost": task_execution.get("estimated_cost"),
                    "cost_currency": task_execution.get("cost_currency"),
                    "cost_basis": task_execution.get("cost_basis", "unconfigured"),
                    "cost_rate_label": task_execution.get("cost_rate_label", ""),
                    "issues": issues,
                }
            )
        return {
            "scope": "session_task_history" if session_id is not None else "local_task_history",
            "metrics": metrics,
            "quality_gates": gates,
            "mode_breakdown": mode_breakdown,
            "model_stage_breakdown": stage_breakdown,
            "recent_tasks": recent,
            "notice": "指标仅来自当前访客 Session 的 SQLite 运行历史；成本只汇总已配置费率的调用，诊断阈值不是生产准确率或 SLA。",
        }

    def get_task(self, task_id: int) -> dict:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM docflow_tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            raise KeyError(task_id)
        task = self._task_to_dict(row, include_source=True)
        task["runs"] = self._runs_for_task(task_id)
        return task

    def _runs_for_task(self, task_id: int) -> list[dict]:
        with self._connect() as connection:
            runs = connection.execute("SELECT * FROM docflow_runs WHERE task_id = ? ORDER BY id DESC", (task_id,)).fetchall()
            result = []
            for run in runs:
                item = dict(run)
                steps = connection.execute("SELECT * FROM docflow_steps WHERE run_id = ? ORDER BY sequence, attempt", (run["id"],)).fetchall()
                item["checkpoint"] = json.loads(item.pop("checkpoint_json") or "{}")
                item["steps"] = [self._step_to_dict(step) for step in steps]
                result.append(item)
        return result

    @staticmethod
    def _task_to_dict(row: sqlite3.Row, include_source: bool) -> dict:
        task = dict(row)
        task["plan"] = json.loads(task.pop("plan_json"))
        task["result"] = json.loads(task.pop("result_json"))
        task["context_config"] = json.loads(task.pop("context_config_json", "{}") or "{}")
        if not include_source:
            task.pop("source_text")
        return task

    @staticmethod
    def _step_to_dict(row: sqlite3.Row) -> dict:
        step = dict(row)
        step["input"] = json.loads(step.pop("input_json"))
        step["output"] = json.loads(step.pop("output_json"))
        return step
