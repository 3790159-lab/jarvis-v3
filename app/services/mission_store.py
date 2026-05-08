import json
import os
import tempfile
import threading
import time
import uuid
from typing import Any, Dict, List, Optional


BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
STATE_DIR = os.path.join(BASE_DIR, "state")
MISSIONS_FILE = os.path.join(STATE_DIR, "missions.json")

_store_lock = threading.Lock()


def _ensure_state_dir() -> None:
    os.makedirs(STATE_DIR, exist_ok=True)


def _safe_read_json(path: str, default: Any) -> Any:
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _safe_write_json(path: str, data: Any) -> None:
    _ensure_state_dir()
    fd, temp_path = tempfile.mkstemp(prefix="missions_", suffix=".tmp", dir=STATE_DIR)
    os.close(fd)
    try:
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(temp_path, path)
    finally:
        try:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        except Exception:
            pass


def _load_all() -> Dict[str, Any]:
    data = _safe_read_json(MISSIONS_FILE, {"missions": []})
    if not isinstance(data, dict):
        return {"missions": []}
    missions = data.get("missions", [])
    if not isinstance(missions, list):
        missions = []
    return {"missions": missions}


def _save_all(data: Dict[str, Any]) -> None:
    _safe_write_json(MISSIONS_FILE, data)


def generate_mission_id() -> str:
    return f"mission_{uuid.uuid4().hex[:8]}"


def generate_goal_id() -> str:
    return f"goal_{uuid.uuid4().hex[:8]}"


def create_mission(
    objective: str,
    route: str,
    strategy_summary: str,
    tasks: List[Dict[str, Any]],
    source: str = "api",
    mode: str = "smart_local",
) -> Dict[str, Any]:
    with _store_lock:
        data = _load_all()
        now = int(time.time())

        mission = {
            "goal_id": generate_goal_id(),
            "mission_id": generate_mission_id(),
            "objective": objective,
            "route": route,
            "strategy_summary": strategy_summary,
            "tasks": tasks,
            "status": "draft",
            "source": source,
            "mode": mode,
            "created_at": now,
            "updated_at": now,
            "summary": "",
            "task_results": [],
            "context": {
                "priority": "normal",
                "notes": [],
                "tags": [],
            },
            "stats": {
                "queued": len(tasks),
                "running": 0,
                "completed": 0,
                "failed": 0,
                "retried": 0,
                "cancelled": 0,
            },
        }

        data["missions"].append(mission)
        _save_all(data)
        return mission


def list_missions(limit: int = 50) -> List[Dict[str, Any]]:
    with _store_lock:
        data = _load_all()
        missions = sorted(data.get("missions", []), key=lambda x: x.get("created_at", 0), reverse=True)
        return missions[:limit]


def get_mission(mission_id: str) -> Optional[Dict[str, Any]]:
    with _store_lock:
        data = _load_all()
        for mission in data.get("missions", []):
            if mission.get("mission_id") == mission_id:
                return mission
    return None


def update_mission_status(mission_id: str, status: str, summary: str | None = None) -> Optional[Dict[str, Any]]:
    with _store_lock:
        data = _load_all()
        for mission in data.get("missions", []):
            if mission.get("mission_id") == mission_id:
                mission["status"] = status
                mission["updated_at"] = int(time.time())
                if summary is not None:
                    mission["summary"] = summary
                _save_all(data)
                return mission
    return None


def append_task_result(mission_id: str, result: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    with _store_lock:
        data = _load_all()
        for mission in data.get("missions", []):
            if mission.get("mission_id") == mission_id:
                mission.setdefault("task_results", []).append(result)
                mission["updated_at"] = int(time.time())
                stats = mission.setdefault("stats", {})
                status = result.get("status")
                if status == "completed":
                    stats["completed"] = int(stats.get("completed", 0)) + 1
                elif status == "failed":
                    stats["failed"] = int(stats.get("failed", 0)) + 1
                elif status == "retried":
                    stats["retried"] = int(stats.get("retried", 0)) + 1
                elif status == "cancelled":
                    stats["cancelled"] = int(stats.get("cancelled", 0)) + 1
                _save_all(data)
                return mission
    return None


def mark_task_status(mission_id: str, task_id: str, status: str) -> Optional[Dict[str, Any]]:
    with _store_lock:
        data = _load_all()
        for mission in data.get("missions", []):
            if mission.get("mission_id") == mission_id:
                for task in mission.get("tasks", []):
                    if task.get("task_id") == task_id:
                        task["status"] = status
                mission["updated_at"] = int(time.time())
                _save_all(data)
                return mission
    return None


def update_mission_context(mission_id: str, key: str, value: Any) -> Optional[Dict[str, Any]]:
    with _store_lock:
        data = _load_all()
        for mission in data.get("missions", []):
            if mission.get("mission_id") == mission_id:
                mission.setdefault("context", {})[key] = value
                mission["updated_at"] = int(time.time())
                _save_all(data)
                return mission
    return None


def mission_stats() -> Dict[str, Any]:
    with _store_lock:
        data = _load_all()
        missions = data.get("missions", [])
        return {
            "missions_total": len(missions),
            "draft_total": sum(1 for m in missions if m.get("status") == "draft"),
            "planned_total": sum(1 for m in missions if m.get("status") == "planned"),
            "running_total": sum(1 for m in missions if m.get("status") == "running"),
            "completed_total": sum(1 for m in missions if m.get("status") == "completed"),
            "failed_total": sum(1 for m in missions if m.get("status") == "failed"),
            "cancelled_total": sum(1 for m in missions if m.get("status") == "cancelled"),
            "storage_file": MISSIONS_FILE,
        }