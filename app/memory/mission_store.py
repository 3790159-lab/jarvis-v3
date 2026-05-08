from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.settings import Settings


class MissionStore:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS missions (
                    mission_id TEXT PRIMARY KEY,
                    goal_id TEXT NOT NULL,
                    objective TEXT NOT NULL,
                    strategy TEXT NOT NULL,
                    status TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    constraints_json TEXT NOT NULL,
                    tasks_json TEXT NOT NULL,
                    warnings_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS mission_logs (
                    log_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    mission_id TEXT NOT NULL,
                    level TEXT NOT NULL,
                    message TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.commit()

    def save_mission(
        self,
        mission_id: str,
        goal_id: str,
        objective: str,
        strategy: str,
        status: str,
        summary: str,
        constraints: dict[str, Any],
        tasks: list[dict[str, Any]],
        warnings: list[str],
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()

        with self._connect() as conn:
            existing = conn.execute(
                "SELECT created_at FROM missions WHERE mission_id = ?",
                (mission_id,),
            ).fetchone()

            created_at = existing["created_at"] if existing else now

            conn.execute(
                """
                INSERT OR REPLACE INTO missions (
                    mission_id,
                    goal_id,
                    objective,
                    strategy,
                    status,
                    summary,
                    constraints_json,
                    tasks_json,
                    warnings_json,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    mission_id,
                    goal_id,
                    objective,
                    strategy,
                    status,
                    summary,
                    json.dumps(constraints, ensure_ascii=False),
                    json.dumps(tasks, ensure_ascii=False),
                    json.dumps(warnings, ensure_ascii=False),
                    created_at,
                    now,
                ),
            )
            conn.commit()

    def get_mission(self, mission_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM missions WHERE mission_id = ?",
                (mission_id,),
            ).fetchone()

        if row is None:
            return None

        return {
            "mission_id": row["mission_id"],
            "goal_id": row["goal_id"],
            "objective": row["objective"],
            "strategy": row["strategy"],
            "status": row["status"],
            "summary": row["summary"],
            "constraints": json.loads(row["constraints_json"]),
            "tasks": json.loads(row["tasks_json"]),
            "warnings": json.loads(row["warnings_json"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def list_missions(self, limit: int = 10) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 50))

        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    mission_id,
                    goal_id,
                    objective,
                    strategy,
                    status,
                    summary,
                    created_at,
                    updated_at
                FROM missions
                ORDER BY updated_at DESC, created_at DESC
                LIMIT ?
                """,
                (safe_limit,),
            ).fetchall()

        items: list[dict[str, Any]] = []
        for row in rows:
            items.append(
                {
                    "mission_id": row["mission_id"],
                    "goal_id": row["goal_id"],
                    "objective": row["objective"],
                    "strategy": row["strategy"],
                    "status": row["status"],
                    "summary": row["summary"],
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                }
            )
        return items

    def add_log(self, mission_id: str, level: str, message: str) -> None:
        now = datetime.now(timezone.utc).isoformat()

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO mission_logs (mission_id, level, message, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (mission_id, level, message, now),
            )
            conn.commit()

    def get_logs(self, mission_id: str, limit: int = 50) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 200))

        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT log_id, mission_id, level, message, created_at
                FROM mission_logs
                WHERE mission_id = ?
                ORDER BY log_id ASC
                LIMIT ?
                """,
                (mission_id, safe_limit),
            ).fetchall()

        return [
            {
                "log_id": row["log_id"],
                "mission_id": row["mission_id"],
                "level": row["level"],
                "message": row["message"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]


mission_store = MissionStore(Settings.DATABASE_PATH)
