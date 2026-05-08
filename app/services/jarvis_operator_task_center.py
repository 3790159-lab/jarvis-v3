from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def read_env_file(project_root: Path) -> Dict[str, str]:
    env_path = project_root / ".env"
    values: Dict[str, str] = {}
    if not env_path.exists():
        return values

    for line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        values[k.strip()] = v.strip().strip('"').strip("'")
    return values


@dataclass
class OperatorTask:
    task_id: str
    title: str
    objective: str
    status: str = "queued"
    priority: str = "normal"
    stage: str = "created"
    progress: int = 0
    plan: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    next_action: str = ""
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)


class JarvisOperatorTaskCenter:
    def __init__(self, project_root: str | Path):
        self.project_root = Path(project_root).resolve()
        self.root = ensure_dir(self.project_root / "jarvis_stage3_artifacts" / "operator_task_center")
        self.tasks_dir = ensure_dir(self.root / "tasks")
        self.reports_dir = ensure_dir(self.root / "reports")
        self.runtime_dir = ensure_dir(self.root / "runtime")

    def _write_json(self, path: Path, payload: Any) -> None:
        ensure_dir(path.parent)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _read_json(self, path: Path) -> Optional[Dict[str, Any]]:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None

    def create_task(self, title: str, objective: str, priority: str = "normal", plan: Optional[List[str]] = None) -> OperatorTask:
        task = OperatorTask(
            task_id="task_" + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8],
            title=title,
            objective=objective,
            priority=priority,
            plan=plan or [
                "Clarify objective and constraints",
                "Create safe execution plan",
                "Run through unified loop or specialized tool",
                "Validate result",
                "Report summary to operator",
            ],
            next_action="Waiting for execution slot.",
        )
        self._write_json(self.tasks_dir / f"{task.task_id}.json", asdict(task))
        return task

    def update_task(self, task_id: str, **updates: Any) -> Dict[str, Any]:
        path = self.tasks_dir / f"{task_id}.json"
        data = self._read_json(path)
        if not data:
            return {"ok": False, "reason": "task_not_found", "task_id": task_id}

        for k, v in updates.items():
            if v is not None and k in data:
                data[k] = v
        data["updated_at"] = utc_now_iso()
        self._write_json(path, data)
        return {"ok": True, "task": data}

    def list_tasks(self) -> List[Dict[str, Any]]:
        items = []
        for path in sorted(self.tasks_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            data = self._read_json(path)
            if data:
                items.append(data)
        return items

    def get_task(self, task_id: str) -> Dict[str, Any]:
        data = self._read_json(self.tasks_dir / f"{task_id}.json")
        return data or {"found": False, "task_id": task_id}

    def latest_unified_night_session(self) -> Dict[str, Any]:
        sessions = self.project_root / "jarvis_stage3_artifacts" / "unified_night_bridge" / "sessions"
        if not sessions.exists():
            return {"found": False, "reason": "sessions_dir_missing"}
        files = sorted(sessions.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not files:
            return {"found": False, "reason": "no_sessions"}
        data = self._read_json(files[0]) or {}
        data["_path"] = str(files[0])
        data["found"] = True
        return data

    def operator_status(self) -> Dict[str, Any]:
        tasks = self.list_tasks()
        latest = self.latest_unified_night_session()

        active = [t for t in tasks if t.get("status") in {"queued", "running", "paused"}]
        completed = [t for t in tasks if t.get("status") == "completed"]
        failed = [t for t in tasks if t.get("status") == "failed"]

        next_actions = []
        for t in active[:5]:
            if t.get("next_action"):
                next_actions.append(f"{t.get('title')}: {t.get('next_action')}")

        if latest.get("found") and latest.get("next_best_actions"):
            next_actions.extend(latest.get("next_best_actions", [])[:3])

        return {
            "status": "ok",
            "active_tasks": len(active),
            "completed_tasks": len(completed),
            "failed_tasks": len(failed),
            "latest_night_session": {
                "found": latest.get("found", False),
                "session_id": latest.get("session_id"),
                "status": latest.get("status"),
                "completed_count": latest.get("completed_count"),
                "failed_count": latest.get("failed_count"),
                "apply_count": latest.get("apply_count"),
                "operator_summary": latest.get("operator_summary"),
            },
            "next_actions": next_actions[:8],
            "updated_at": utc_now_iso(),
        }

    def build_telegram_report(self) -> str:
        status = self.operator_status()
        latest = status["latest_night_session"]

        lines = []
        lines.append("🤖 Отчёт Jarvis")
        lines.append("")
        lines.append(f"Активные задачи: {status['active_tasks']}")
        lines.append(f"Завершённые задачи: {status['completed_tasks']}")
        lines.append(f"Ошибки: {status['failed_tasks']}")
        lines.append("")

        if latest.get("found"):
            lines.append(f"Последняя night-сессия: {latest.get('status')}")
            lines.append(f"Успешных apply: {latest.get('apply_count')}")
            if latest.get("operator_summary"):
                lines.append(f"Итог: {latest.get('operator_summary')}")
        else:
            lines.append("Последняя night-сессия: не найдена")

        next_actions = []
        for item in status["next_actions"]:
            if item not in next_actions:
                next_actions.append(item)

        if next_actions:
            lines.append("")
            lines.append("Что дальше:")
            for item in next_actions[:5]:
                lines.append(f"• {item}")

        return "\n".join(lines)

    def send_telegram_message(self, text: str) -> Dict[str, Any]:
        env_file = read_env_file(self.project_root)
        token = os.environ.get("TELEGRAM_BOT_TOKEN") or env_file.get("TELEGRAM_BOT_TOKEN")
        chat_id = os.environ.get("TELEGRAM_ALLOWED_CHAT_ID") or env_file.get("TELEGRAM_ALLOWED_CHAT_ID")

        if not token or not chat_id:
            return {
                "ok": False,
                "reason": "telegram_not_configured",
                "token_present": bool(token),
                "chat_id_present": bool(chat_id),
            }

        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = urllib.parse.urlencode({
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": "true",
        }).encode("utf-8")

        try:
            req = urllib.request.Request(url, data=payload, method="POST")
            with urllib.request.urlopen(req, timeout=20) as resp:
                body = resp.read().decode("utf-8", errors="replace")
            result = {"ok": True, "response": json.loads(body)}
        except Exception as exc:
            result = {"ok": False, "reason": str(exc)}

        self._write_json(self.reports_dir / ("telegram_report_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".json"), {
            "text": text,
            "result": result,
            "created_at": utc_now_iso(),
        })
        return result

    def send_operator_report(self) -> Dict[str, Any]:
        text = self.build_telegram_report()
        result = self.send_telegram_message(text)
        return {"text": text, "telegram": result}