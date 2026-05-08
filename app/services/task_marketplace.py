from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.services.approval_store import approval_is_approved


BASE_DIR = Path("jarvis_stage3_artifacts") / "agent_runtime"
BASE_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = BASE_DIR / "task_marketplace.db"


def _conn() -> sqlite3.Connection:
    connection = sqlite3.connect(str(DB_PATH))
    connection.row_factory = sqlite3.Row
    return connection


def _utc_ts() -> float:
    return time.time()


def ensure_schema() -> None:
    with _conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS agents (
                agent_id TEXT PRIMARY KEY,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                display_name TEXT NOT NULL,
                adapter_name TEXT NOT NULL,
                capabilities_json TEXT NOT NULL,
                enabled INTEGER NOT NULL,
                weight REAL NOT NULL,
                metadata_json TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tasks (
                task_id TEXT PRIMARY KEY,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                status TEXT NOT NULL,
                title TEXT NOT NULL,
                description TEXT,
                payload_json TEXT NOT NULL,
                required_capabilities_json TEXT NOT NULL,
                risk_level TEXT NOT NULL,
                approval_required INTEGER NOT NULL,
                approval_id TEXT,
                leased_by TEXT,
                result_json TEXT,
                error_text TEXT,
                metadata_json TEXT NOT NULL
            )
            """
        )
        conn.commit()


ensure_schema()


def register_agent(
    agent_id: str,
    display_name: str,
    adapter_name: str,
    capabilities: List[str],
    enabled: bool = True,
    weight: float = 1.0,
    metadata: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    now = _utc_ts()
    with _conn() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO agents (
                agent_id, created_at, updated_at, display_name, adapter_name,
                capabilities_json, enabled, weight, metadata_json
            ) VALUES (
                ?,
                COALESCE((SELECT created_at FROM agents WHERE agent_id = ?), ?),
                ?, ?, ?, ?, ?, ?, ?
            )
            """,
            (
                agent_id,
                agent_id,
                now,
                now,
                display_name,
                adapter_name,
                json.dumps(capabilities, ensure_ascii=False),
                1 if enabled else 0,
                float(weight),
                json.dumps(metadata or {}, ensure_ascii=False),
            ),
        )
        conn.commit()
    return get_agent(agent_id) or {}


def get_agent(agent_id: str) -> Optional[Dict[str, Any]]:
    with _conn() as conn:
        row = conn.execute("SELECT * FROM agents WHERE agent_id = ?", (agent_id,)).fetchone()
    if not row:
        return None
    data = dict(row)
    data["capabilities"] = json.loads(data.pop("capabilities_json") or "[]")
    data["metadata"] = json.loads(data.pop("metadata_json") or "{}")
    data["enabled"] = bool(data.get("enabled"))
    return data


def list_agents() -> List[Dict[str, Any]]:
    with _conn() as conn:
        rows = conn.execute("SELECT * FROM agents ORDER BY updated_at DESC").fetchall()
    out: List[Dict[str, Any]] = []
    for row in rows:
        data = dict(row)
        data["capabilities"] = json.loads(data.pop("capabilities_json") or "[]")
        data["metadata"] = json.loads(data.pop("metadata_json") or "{}")
        data["enabled"] = bool(data.get("enabled"))
        out.append(data)
    return out


def create_task(
    title: str,
    description: str,
    payload: Dict[str, Any],
    required_capabilities: List[str],
    risk_level: str,
    approval_required: bool,
    approval_id: str | None,
    metadata: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    task_id = f"task_{uuid.uuid4().hex[:12]}"
    now = _utc_ts()
    with _conn() as conn:
        conn.execute(
            """
            INSERT INTO tasks (
                task_id, created_at, updated_at, status, title, description, payload_json,
                required_capabilities_json, risk_level, approval_required, approval_id,
                leased_by, result_json, error_text, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task_id,
                now,
                now,
                "queued",
                title,
                description,
                json.dumps(payload or {}, ensure_ascii=False),
                json.dumps(required_capabilities or [], ensure_ascii=False),
                risk_level,
                1 if approval_required else 0,
                approval_id,
                None,
                None,
                None,
                json.dumps(metadata or {}, ensure_ascii=False),
            ),
        )
        conn.commit()
    return get_task(task_id) or {}


def get_task(task_id: str) -> Optional[Dict[str, Any]]:
    with _conn() as conn:
        row = conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
    if not row:
        return None
    return _row_to_task(row)


def list_tasks(status: str | None = None) -> List[Dict[str, Any]]:
    query = "SELECT * FROM tasks"
    args: List[Any] = []
    if status:
        query += " WHERE status = ?"
        args.append(status)
    query += " ORDER BY created_at DESC"

    with _conn() as conn:
        rows = conn.execute(query, args).fetchall()
    return [_row_to_task(row) for row in rows]


def _row_to_task(row: sqlite3.Row) -> Dict[str, Any]:
    data = dict(row)
    data["payload"] = json.loads(data.pop("payload_json") or "{}")
    data["required_capabilities"] = json.loads(data.pop("required_capabilities_json") or "[]")
    data["metadata"] = json.loads(data.pop("metadata_json") or "{}")
    data["result"] = json.loads(data.pop("result_json") or "null")
    data["approval_required"] = bool(data.get("approval_required"))
    return data


def _capability_match(agent_caps: List[str], required_caps: List[str]) -> bool:
    normalized_agent = {str(x).strip().lower() for x in agent_caps}
    normalized_req = {str(x).strip().lower() for x in required_caps}
    return normalized_req.issubset(normalized_agent)


def lease_next_task(agent_id: str) -> Optional[Dict[str, Any]]:
    agent = get_agent(agent_id)
    if not agent or not agent.get("enabled"):
        return None

    agent_caps = agent.get("capabilities") or []

    queued = list_tasks(status="queued")
    selected: Optional[Dict[str, Any]] = None
    for task in queued:
        required_caps = task.get("required_capabilities") or []
        if not _capability_match(agent_caps, required_caps):
            continue

        if task.get("approval_required") and not approval_is_approved(task.get("approval_id")):
            continue

        selected = task
        break

    if not selected:
        return None

    now = _utc_ts()
    with _conn() as conn:
        conn.execute(
            """
            UPDATE tasks
            SET status = ?, leased_by = ?, updated_at = ?
            WHERE task_id = ?
            """,
            ("leased", agent_id, now, selected["task_id"]),
        )
        conn.commit()

    return get_task(selected["task_id"])


def complete_task(task_id: str, result: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    now = _utc_ts()
    with _conn() as conn:
        conn.execute(
            """
            UPDATE tasks
            SET status = ?, result_json = ?, updated_at = ?, error_text = NULL
            WHERE task_id = ?
            """,
            ("completed", json.dumps(result or {}, ensure_ascii=False), now, task_id),
        )
        conn.commit()
    return get_task(task_id)


def fail_task(task_id: str, error_text: str) -> Optional[Dict[str, Any]]:
    now = _utc_ts()
    with _conn() as conn:
        conn.execute(
            """
            UPDATE tasks
            SET status = ?, updated_at = ?, error_text = ?
            WHERE task_id = ?
            """,
            ("failed", now, error_text, task_id),
        )
        conn.commit()
    return get_task(task_id)