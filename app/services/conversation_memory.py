from __future__ import annotations
import app.core.bootstrap_utf8  # noqa: F401

from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Any
import json

BASE_DIR = Path(__file__).resolve().parent.parent.parent
STATE_DIR = BASE_DIR / "state"
STATE_DIR.mkdir(parents=True, exist_ok=True)

MEMORY_FILE = STATE_DIR / "conversation_memory.json"
LOCK = Lock()


def utc_now_iso() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def default_memory() -> dict[str, Any]:
    return {
        "last_goal_id": None,
        "last_mission_id": None,
        "last_objective": None,
        "last_user_text": None,
        "last_reply": None,
        "last_intent": None,
        "history": [],
        "updated_at": utc_now_iso(),
    }


def load_memory() -> dict[str, Any]:
    if not MEMORY_FILE.exists():
        return default_memory()

    try:
        data = json.loads(MEMORY_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return default_memory()
        base = default_memory()
        base.update(data)
        if not isinstance(base.get("history"), list):
            base["history"] = []
        return base
    except Exception:
        broken = MEMORY_FILE.with_name(f"conversation_memory_broken_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.json")
        try:
            broken.write_text(MEMORY_FILE.read_text(encoding="utf-8", errors="ignore"), encoding="utf-8")
        except Exception:
            pass
        return default_memory()


def save_memory(data: dict[str, Any]) -> None:
    data["updated_at"] = utc_now_iso()
    tmp = MEMORY_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(MEMORY_FILE)


class ConversationMemory:
    def get(self) -> dict[str, Any]:
        with LOCK:
            return load_memory()

    def clear(self) -> dict[str, Any]:
        with LOCK:
            data = default_memory()
            save_memory(data)
            return data

    def update_fields(self, **kwargs: Any) -> dict[str, Any]:
        with LOCK:
            data = load_memory()
            for key, value in kwargs.items():
                data[key] = value
            save_memory(data)
            return data

    def append_history(
        self,
        user_text: str | None = None,
        reply: str | None = None,
        intent: str | None = None,
        goal_id: str | None = None,
        mission_id: str | None = None,
    ) -> dict[str, Any]:
        with LOCK:
            data = load_memory()
            history = data.get("history", [])
            history.append({
                "timestamp": utc_now_iso(),
                "user_text": user_text,
                "reply": reply,
                "intent": intent,
                "goal_id": goal_id,
                "mission_id": mission_id,
            })
            data["history"] = history[-20:]
            if user_text is not None:
                data["last_user_text"] = user_text
            if reply is not None:
                data["last_reply"] = reply
            if intent is not None:
                data["last_intent"] = intent
            if goal_id is not None:
                data["last_goal_id"] = goal_id
            if mission_id is not None:
                data["last_mission_id"] = mission_id
            save_memory(data)
            return data
