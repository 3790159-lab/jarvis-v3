import json
import os
import tempfile
import threading
import time
from typing import Any, Dict, List


BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
STATE_DIR = os.path.join(BASE_DIR, "state")
ACTIVE_RUNS_FILE = os.path.join(STATE_DIR, "active_runs.json")

_lock = threading.Lock()


def _safe_read_json(path: str, default: Any) -> Any:
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _safe_write_json(path: str, data: Any) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix="active_runs_", suffix=".tmp", dir=os.path.dirname(path))
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


def _load() -> Dict[str, Any]:
    data = _safe_read_json(ACTIVE_RUNS_FILE, {"items": []})
    if not isinstance(data, dict):
        return {"items": []}
    items = data.get("items", [])
    if not isinstance(items, list):
        items = []
    return {"items": items}


def _save(data: Dict[str, Any]) -> None:
    _safe_write_json(ACTIVE_RUNS_FILE, data)


def create_active_snapshot(task: Dict[str, Any]) -> Dict[str, Any]:
    with _lock:
        data = _load()
        now = int(time.time())
        items = data.setdefault("items", [])

        mission_id = str(task.get("mission_id", ""))
        task_id = str(task.get("task_id", ""))

        items = [
            i for i in items
            if not (str(i.get("mission_id")) == mission_id and str(i.get("task_id")) == task_id)
        ]

        snapshot = {
            "mission_id": mission_id,
            "task_id": task_id,
            "worker_id": str(task.get("worker_id", "")),
            "status": "running",
            "created_at": now,
            "updated_at": now,
            "task": task,
        }
        items.append(snapshot)
        data["items"] = items
        _save(data)
        return snapshot


def heartbeat_active_snapshot(mission_id: str, task_id: str) -> Dict[str, Any] | None:
    with _lock:
        data = _load()
        for item in data.get("items", []):
            if str(item.get("mission_id")) == str(mission_id) and str(item.get("task_id")) == str(task_id):
                item["updated_at"] = int(time.time())
                _save(data)
                return item
    return None


def remove_active_snapshot(mission_id: str, task_id: str) -> bool:
    with _lock:
        data = _load()
        before = len(data.get("items", []))
        data["items"] = [
            i for i in data.get("items", [])
            if not (str(i.get("mission_id")) == str(mission_id) and str(i.get("task_id")) == str(task_id))
        ]
        after = len(data.get("items", []))
        _save(data)
        return after < before


def list_active_runs() -> List[Dict[str, Any]]:
    with _lock:
        data = _load()
        return list(data.get("items", []))


def stale_active_runs(timeout_seconds: int = 20) -> List[Dict[str, Any]]:
    now = int(time.time())
    stale: List[Dict[str, Any]] = []
    for item in list_active_runs():
        last = int(item.get("updated_at", 0))
        if now - last > int(timeout_seconds):
            stale.append(item)
    return stale


def active_run_stats() -> Dict[str, Any]:
    items = list_active_runs()
    return {
        "active_runs_total": len(items),
        "active_runs_file": ACTIVE_RUNS_FILE,
    }