from __future__ import annotations

import json
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
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS docflow_tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    goal TEXT NOT NULL,
                    source_text TEXT NOT NULL,
                    session_id TEXT NOT NULL DEFAULT 'default',
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
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(source_task_id) REFERENCES docflow_tasks(id)
                )"""
            )

    def create_task(self, title: str, goal: str, source_text: str, session_id: str = "default") -> dict:
        with self._connect() as connection:
            cursor = connection.execute(
                """INSERT INTO docflow_tasks (title, goal, source_text, session_id, status)
                   VALUES (?, ?, ?, ?, 'queued')""",
                (title, goal, source_text, session_id),
            )
            task_id = cursor.lastrowid
        return self.get_task(task_id)

    def create_run(self, task_id: int, parent_run_id: int | None = None) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                "INSERT INTO docflow_runs (task_id, parent_run_id, status) VALUES (?, ?, 'running')",
                (task_id, parent_run_id),
            )
            run_id = int(cursor.lastrowid)
        return run_id

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
        token_estimate = len(json.dumps(result, ensure_ascii=False)) // 4
        with self._connect() as connection:
            connection.execute(
                """UPDATE docflow_tasks
                   SET status = 'awaiting_review', plan_json = ?, result_json = ?, updated_at = CURRENT_TIMESTAMP
                   WHERE id = ?""",
                (json.dumps(plan, ensure_ascii=False), json.dumps(result, ensure_ascii=False), task_id),
            )
            connection.execute(
                """UPDATE docflow_runs
                   SET status = 'completed', completed_at = CURRENT_TIMESTAMP, token_estimate = ?
                   WHERE id = ?""",
                (token_estimate, run_id),
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
            cursor = connection.execute(
                """INSERT INTO docflow_memories (session_id, memory_key, content, source_task_id)
                   VALUES (?, ?, ?, ?)""",
                (session_id, memory_key, content, source_task_id),
            )
            memory_id = int(cursor.lastrowid)
            row = connection.execute("SELECT * FROM docflow_memories WHERE id = ?", (memory_id,)).fetchone()
        return dict(row)

    def recall_memories(self, session_id: str, query: str, limit: int = 5) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM docflow_memories WHERE session_id = ? ORDER BY id DESC LIMIT 50",
                (session_id,),
            ).fetchall()
        terms = set(re.findall(r"[A-Za-z0-9]{2,}", query.lower()))
        for sequence in re.findall(r"[\u4e00-\u9fff]{2,}", query):
            terms.update(sequence[index : index + 2] for index in range(len(sequence) - 1))
        ranked = []
        for recency, row in enumerate(rows):
            item = dict(row)
            haystack = f"{item['memory_key']} {item['content']}".lower()
            score = sum(term in haystack for term in terms)
            ranked.append((score, -recency, item))
        ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return [item for score, _, item in ranked[:limit] if score > 0] or [dict(row) for row in rows[:limit]]

    def list_memories(self, session_id: str, limit: int = 20) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM docflow_memories WHERE session_id = ? ORDER BY id DESC LIMIT ?",
                (session_id, limit),
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

    def list_tasks(self) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM docflow_tasks ORDER BY id DESC").fetchall()
        return [self._task_to_dict(row, include_source=False) for row in rows]

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
        if not include_source:
            task.pop("source_text")
        return task

    @staticmethod
    def _step_to_dict(row: sqlite3.Row) -> dict:
        step = dict(row)
        step["input"] = json.loads(step.pop("input_json"))
        step["output"] = json.loads(step.pop("output_json"))
        return step
