from __future__ import annotations

import json
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
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    completed_at TEXT,
                    token_estimate INTEGER NOT NULL DEFAULT 0,
                    cost_estimate REAL NOT NULL DEFAULT 0,
                    FOREIGN KEY(task_id) REFERENCES docflow_tasks(id)
                )"""
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS docflow_steps (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id INTEGER NOT NULL,
                    sequence INTEGER NOT NULL,
                    phase TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    input_json TEXT NOT NULL DEFAULT '{}',
                    output_json TEXT NOT NULL DEFAULT '{}',
                    error TEXT NOT NULL DEFAULT '',
                    elapsed_ms INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(run_id) REFERENCES docflow_runs(id)
                )"""
            )

    def create_task(self, title: str, goal: str, source_text: str) -> dict:
        with self._connect() as connection:
            cursor = connection.execute(
                """INSERT INTO docflow_tasks (title, goal, source_text, status)
                   VALUES (?, ?, ?, 'queued')""",
                (title, goal, source_text),
            )
            task_id = cursor.lastrowid
        return self.get_task(task_id)

    def create_run(self, task_id: int) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                "INSERT INTO docflow_runs (task_id, status) VALUES (?, 'running')",
                (task_id,),
            )
            run_id = int(cursor.lastrowid)
        return run_id

    def record_step(self, run_id: int, step: dict) -> None:
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO docflow_steps
                (run_id, sequence, phase, tool_name, status, input_json, output_json, error, elapsed_ms)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    run_id,
                    step["sequence"],
                    step["phase"],
                    step["tool_name"],
                    step["status"],
                    json.dumps(step.get("input", {}), ensure_ascii=False),
                    json.dumps(step.get("output", {}), ensure_ascii=False),
                    step.get("error", ""),
                    step.get("elapsed_ms", 0),
                ),
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
                "UPDATE docflow_runs SET status = 'failed', completed_at = CURRENT_TIMESTAMP WHERE id = ?", (run_id,)
            )

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
                steps = connection.execute("SELECT * FROM docflow_steps WHERE run_id = ? ORDER BY sequence", (run["id"],)).fetchall()
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
