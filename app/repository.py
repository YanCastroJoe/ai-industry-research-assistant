from __future__ import annotations

import json
import sqlite3
from pathlib import Path


class TaskRepository:
    def __init__(self, database_path: Path):
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS research_tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    input_text TEXT NOT NULL,
                    material_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    card_json TEXT NOT NULL,
                    reviewer_note TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )"""
            )

    def create(self, title: str, input_text: str, material_type: str, card: dict) -> dict:
        with self._connect() as connection:
            cursor = connection.execute(
                """INSERT INTO research_tasks (title, input_text, material_type, status, card_json)
                   VALUES (?, ?, ?, 'pending_review', ?)""",
                (title, input_text, material_type, json.dumps(card, ensure_ascii=False)),
            )
            task_id = cursor.lastrowid
        return self.get(task_id)

    def list(self) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM research_tasks ORDER BY id DESC").fetchall()
        return [self._row_to_dict(row) for row in rows]

    def get(self, task_id: int) -> dict:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM research_tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            raise KeyError(task_id)
        return self._row_to_dict(row)

    def review(self, task_id: int, action: str, note: str, card: dict | None = None) -> dict:
        status = "approved" if action == "approve" else "rejected"
        with self._connect() as connection:
            connection.execute(
                """UPDATE research_tasks
                   SET status = ?, reviewer_note = ?, card_json = COALESCE(?, card_json), updated_at = CURRENT_TIMESTAMP
                   WHERE id = ?""",
                (status, note, json.dumps(card, ensure_ascii=False) if card else None, task_id),
            )
        return self.get(task_id)

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict:
        task = dict(row)
        task["card"] = json.loads(task.pop("card_json"))
        return task
