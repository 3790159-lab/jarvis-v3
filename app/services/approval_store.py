from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional


BASE_DIR = Path("jarvis_stage3_artifacts") / "governance_runtime"
BASE_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = BASE_DIR / "approvals.db"


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
            CREATE TABLE IF NOT EXISTS approvals (
                approval_id TEXT PRIMARY KEY,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                status TEXT NOT NULL,
                mission_id TEXT,
                step_id TEXT,
                risk_level TEXT,
                summary TEXT,
                payload_json TEXT,
                operator_note TEXT
            )
            """
        )
        conn.commit()


ensure_schema()


def create_approval_request(
    summary: str,
    risk_level: str,
    payload: Dict[str, Any] | None = None,
    mission_id: str | None = None,
    step_id: str | None = None,
) -> Dict[str, Any]:
    approval_id = f"approval_{uuid.uuid4().hex[:12]}"
    now = _utc_ts()
    with _conn() as conn:
        conn.execute(
            """
            INSERT INTO approvals (
                approval_id, created_at, updated_at, status,
                mission_id, step_id, risk_level, summary, payload_json, operator_note
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                approval_id,
                now,
                now,
                "pending",
                mission_id,
                step_id,
                risk_level,
                summary,
                json.dumps(payload or {}, ensure_ascii=False),
                None,
            ),
        )
        conn.commit()
    return get_approval(approval_id) or {}


def get_approval(approval_id: str) -> Optional[Dict[str, Any]]:
    with _conn() as conn:
        row = conn.execute("SELECT * FROM approvals WHERE approval_id = ?", (approval_id,)).fetchone()
        if not row:
            return None
        data = dict(row)
        try:
            data["payload"] = json.loads(data.pop("payload_json") or "{}")
        except Exception:
            data["payload"] = {}
        return data


def list_approvals(status: str | None = None) -> List[Dict[str, Any]]:
    query = "SELECT * FROM approvals"
    args: List[Any] = []
    if status:
        query += " WHERE status = ?"
        args.append(status)
    query += " ORDER BY created_at DESC"

    with _conn() as conn:
        rows = conn.execute(query, args).fetchall()

    out: List[Dict[str, Any]] = []
    for row in rows:
        data = dict(row)
        try:
            data["payload"] = json.loads(data.pop("payload_json") or "{}")
        except Exception:
            data["payload"] = {}
        out.append(data)
    return out


def set_approval_status(approval_id: str, status: str, operator_note: str | None = None) -> Optional[Dict[str, Any]]:
    now = _utc_ts()
    with _conn() as conn:
        conn.execute(
            """
            UPDATE approvals
            SET status = ?, updated_at = ?, operator_note = ?
            WHERE approval_id = ?
            """,
            (status, now, operator_note, approval_id),
        )
        conn.commit()
    return get_approval(approval_id)


def approval_is_approved(approval_id: str | None) -> bool:
    if not approval_id:
        return False
    approval = get_approval(approval_id)
    if not approval:
        return False
    return str(approval.get("status") or "").strip().lower() == "approved"