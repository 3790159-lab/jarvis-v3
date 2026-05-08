from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List

from app.services.artifact_registry import get_mission_result


BASE_DIR = Path("jarvis_stage3_artifacts") / "memory_runtime"
BASE_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = BASE_DIR / "semantic_memory.db"
NOTES_DIR = BASE_DIR / "obsidian_notes"
NOTES_DIR.mkdir(parents=True, exist_ok=True)


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
            CREATE TABLE IF NOT EXISTS memories (
                memory_id TEXT PRIMARY KEY,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                mission_id TEXT,
                kind TEXT NOT NULL,
                title TEXT NOT NULL,
                text_body TEXT NOT NULL,
                tags_json TEXT NOT NULL,
                artifact_path TEXT,
                source TEXT
            )
            """
        )
        conn.commit()


ensure_schema()


def remember_memory(
    kind: str,
    title: str,
    text_body: str,
    tags: List[str] | None = None,
    artifact_path: str | None = None,
    mission_id: str | None = None,
    source: str | None = None,
) -> Dict[str, Any]:
    memory_id = f"mem_{uuid.uuid4().hex[:12]}"
    now = _utc_ts()
    tags = list(tags or [])

    with _conn() as conn:
        conn.execute(
            """
            INSERT INTO memories (
                memory_id, created_at, updated_at, mission_id, kind, title,
                text_body, tags_json, artifact_path, source
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                memory_id,
                now,
                now,
                mission_id,
                kind,
                title,
                text_body,
                json.dumps(tags, ensure_ascii=False),
                artifact_path,
                source,
            ),
        )
        conn.commit()

    return get_memory(memory_id)


def get_memory(memory_id: str) -> Dict[str, Any] | None:
    with _conn() as conn:
        row = conn.execute("SELECT * FROM memories WHERE memory_id = ?", (memory_id,)).fetchone()
    if not row:
        return None
    data = dict(row)
    data["tags"] = json.loads(data.pop("tags_json") or "[]")
    return data


def list_recent_memories(limit: int = 20) -> List[Dict[str, Any]]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM memories ORDER BY updated_at DESC LIMIT ?",
            (int(limit),),
        ).fetchall()
    out: List[Dict[str, Any]] = []
    for row in rows:
        data = dict(row)
        data["tags"] = json.loads(data.pop("tags_json") or "[]")
        out.append(data)
    return out


def search_memories(query: str, limit: int = 10) -> List[Dict[str, Any]]:
    q = str(query or "").strip()
    if not q:
        return []

    with _conn() as conn:
        rows = conn.execute(
            """
            SELECT * FROM memories
            WHERE title LIKE ? OR text_body LIKE ? OR tags_json LIKE ?
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (f"%{q}%", f"%{q}%", f"%{q}%", int(limit)),
        ).fetchall()

    tokens = [x for x in q.lower().split() if x]
    out: List[Dict[str, Any]] = []
    for row in rows:
        data = dict(row)
        data["tags"] = json.loads(data.pop("tags_json") or "[]")
        hay = f"{data.get('title','')} {data.get('text_body','')} {' '.join(data.get('tags') or [])}".lower()
        score = sum(1 for token in tokens if token in hay)
        data["score"] = score
        out.append(data)

    out.sort(key=lambda x: (x.get("score", 0), x.get("updated_at", 0)), reverse=True)
    return out[: int(limit)]


def _write_obsidian_note(mission_id: str, content: str) -> str:
    path = NOTES_DIR / f"{mission_id}.md"
    path.write_text(content, encoding="utf-8")
    return str(path.resolve())


def ingest_packaged_mission_result(mission_id: str) -> Dict[str, Any]:
    result = get_mission_result(mission_id)
    if not result:
        raise ValueError(f"Mission result not found for mission_id={mission_id}")

    objective = str(result.get("objective") or "")
    final_summary = str(result.get("final_summary") or "")
    manifest = result.get("artifact_manifest") or {}
    artifacts = manifest.get("artifacts") or []

    created: List[Dict[str, Any]] = []

    summary_memory = remember_memory(
        kind="mission_summary",
        title=f"Mission {mission_id} summary",
        text_body=f"Objective: {objective}\nSummary: {final_summary}",
        tags=["mission", "summary", mission_id],
        mission_id=mission_id,
        source="mission_result_packager",
    )
    created.append(summary_memory)

    for artifact in artifacts:
        artifact_path = str((artifact or {}).get("path") or "")
        artifact_name = str((artifact or {}).get("name") or "")
        artifact_role = str((artifact or {}).get("role") or "artifact")
        mem = remember_memory(
            kind="artifact",
            title=f"Artifact {artifact_name}",
            text_body=f"Artifact role: {artifact_role}\nArtifact path: {artifact_path}",
            tags=["artifact", mission_id, artifact_role],
            artifact_path=artifact_path,
            mission_id=mission_id,
            source="artifact_registry",
        )
        created.append(mem)

    note_content = f"# Mission {mission_id}\n\n## Objective\n{objective}\n\n## Final summary\n{final_summary}\n\n## Artifacts\n"
    for artifact in artifacts:
        note_content += f"- {artifact.get('name')} :: {artifact.get('path')} :: role={artifact.get('role')}\n"
    note_path = _write_obsidian_note(mission_id, note_content)

    return {
        "mission_id": mission_id,
        "created_count": len(created),
        "created": created,
        "note_path": note_path,
    }