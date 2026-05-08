from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from app.settings import settings


class StateStore:
    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = db_path or settings.database_path
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self.connection() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS goals (
                    goal_id TEXT PRIMARY KEY,
                    objective TEXT NOT NULL,
                    constraints_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS missions (
                    mission_id TEXT PRIMARY KEY,
                    goal_id TEXT NOT NULL,
                    objective TEXT NOT NULL,
                    constraints_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(goal_id) REFERENCES goals(goal_id)
                );

                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY,
                    mission_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    type TEXT NOT NULL,
                    executor TEXT NOT NULL,
                    working_directory TEXT,
                    payload_json TEXT NOT NULL,
                    priority INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    result_json TEXT,
                    error TEXT,
                    FOREIGN KEY(mission_id) REFERENCES missions(mission_id)
                );
                """
            )

    def insert_goal(self, goal_id: str, objective: str, constraints: dict[str, Any], created_at: str) -> None:
        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO goals (goal_id, objective, constraints_json, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (goal_id, objective, json.dumps(constraints), created_at),
            )

    def get_goal(self, goal_id: str) -> dict[str, Any] | None:
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM goals WHERE goal_id = ?", (goal_id,)).fetchone()
            return self._row_to_goal(row) if row else None

    def list_goals(self) -> list[dict[str, Any]]:
        with self.connection() as conn:
            rows = conn.execute("SELECT * FROM goals ORDER BY created_at DESC").fetchall()
            return [self._row_to_goal(row) for row in rows]

    def insert_mission(
        self,
        mission_id: str,
        goal_id: str,
        objective: str,
        constraints: dict[str, Any],
        created_at: str,
    ) -> None:
        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO missions (mission_id, goal_id, objective, constraints_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (mission_id, goal_id, objective, json.dumps(constraints), created_at),
            )

    def get_mission(self, mission_id: str) -> dict[str, Any] | None:
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM missions WHERE mission_id = ?", (mission_id,)).fetchone()
            return self._row_to_mission(row) if row else None

    def list_missions(self, goal_id: str | None = None) -> list[dict[str, Any]]:
        with self.connection() as conn:
            if goal_id:
                rows = conn.execute(
                    "SELECT * FROM missions WHERE goal_id = ? ORDER BY created_at DESC",
                    (goal_id,),
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM missions ORDER BY created_at DESC").fetchall()
            return [self._row_to_mission(row) for row in rows]

    def insert_task(
        self,
        task_id: str,
        mission_id: str,
        title: str,
        task_type: str,
        executor: str,
        working_directory: str | None,
        payload: dict[str, Any],
        priority: int,
        status: str,
        created_at: str,
    ) -> None:
        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO tasks (
                    task_id, mission_id, title, type, executor, working_directory,
                    payload_json, priority, status, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    mission_id,
                    title,
                    task_type,
                    executor,
                    working_directory,
                    json.dumps(payload),
                    priority,
                    status,
                    created_at,
                ),
            )

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
            return self._row_to_task(row) if row else None

    def list_tasks(
        self,
        mission_id: str | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM tasks"
        clauses: list[str] = []
        params: list[Any] = []
        if mission_id:
            clauses.append("mission_id = ?")
            params.append(mission_id)
        if status:
            clauses.append("status = ?")
            params.append(status)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY priority ASC, created_at ASC"

        with self.connection() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
            return [self._row_to_task(row) for row in rows]

    def acquire_next_queued_task(self) -> dict[str, Any] | None:
        with self.connection() as conn:
            row = conn.execute(
                """
                SELECT * FROM tasks
                WHERE status = 'queued'
                ORDER BY priority ASC, created_at ASC
                LIMIT 1
                """
            ).fetchone()
            if not row:
                return None

            conn.execute(
                """
                UPDATE tasks
                SET status = 'running', started_at = CURRENT_TIMESTAMP
                WHERE task_id = ? AND status = 'queued'
                """,
                (row["task_id"],),
            )
            refreshed = conn.execute("SELECT * FROM tasks WHERE task_id = ?", (row["task_id"],)).fetchone()
            return self._row_to_task(refreshed) if refreshed else None

    def complete_task(self, task_id: str, result: dict[str, Any], error: str | None = None) -> None:
        status = "succeeded" if error is None else "failed"
        with self.connection() as conn:
            conn.execute(
                """
                UPDATE tasks
                SET status = ?, finished_at = CURRENT_TIMESTAMP, result_json = ?, error = ?
                WHERE task_id = ?
                """,
                (status, json.dumps(result), error, task_id),
            )

    def _row_to_goal(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "goal_id": row["goal_id"],
            "objective": row["objective"],
            "constraints": json.loads(row["constraints_json"]),
            "created_at": row["created_at"],
        }

    def _row_to_mission(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "mission_id": row["mission_id"],
            "goal_id": row["goal_id"],
            "objective": row["objective"],
            "constraints": json.loads(row["constraints_json"]),
            "created_at": row["created_at"],
        }

    def _row_to_task(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "task_id": row["task_id"],
            "mission_id": row["mission_id"],
            "title": row["title"],
            "type": row["type"],
            "executor": row["executor"],
            "working_directory": row["working_directory"],
            "payload": json.loads(row["payload_json"]),
            "priority": row["priority"],
            "status": row["status"],
            "created_at": row["created_at"],
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
            "result": json.loads(row["result_json"]) if row["result_json"] else None,
            "error": row["error"],
        }
