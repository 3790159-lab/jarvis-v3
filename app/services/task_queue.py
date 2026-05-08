import json
import os
import tempfile
import threading
import time
from typing import Any, Dict, List, Optional


BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
STATE_DIR = os.path.join(BASE_DIR, "state")
QUEUE_FILE = os.path.join(STATE_DIR, "task_queue.json")

_queue_lock = threading.Lock()
_PRIORITY_SCORE = {"high": 0, "normal": 1, "low": 2}


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
    fd, temp_path = tempfile.mkstemp(prefix="queue_", suffix=".tmp", dir=STATE_DIR)
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


def _load_queue() -> Dict[str, Any]:
    data = _safe_read_json(QUEUE_FILE, {"items": []})
    if not isinstance(data, dict):
        return {"items": []}
    items = data.get("items", [])
    if not isinstance(items, list):
        items = []
    return {"items": items}


def _save_queue(data: Dict[str, Any]) -> None:
    _safe_write_json(QUEUE_FILE, data)


def enqueue_tasks(mission_id: str, tasks: List[Dict[str, Any]]) -> Dict[str, Any]:
    with _queue_lock:
        data = _load_queue()
        now = int(time.time())
        existing = {(str(i.get("mission_id")), str(i.get("task_id"))) for i in data.get("items", [])}
        added = 0
        for task in tasks:
            key = (str(mission_id), str(task.get("task_id")))
            if key in existing:
                continue
            data["items"].append({
                "mission_id": mission_id,
                "task_id": task.get("task_id"),
                "title": task.get("title", ""),
                "type": task.get("type", "generic"),
                "status": "queued",
                "details": task.get("details", ""),
                "payload": task.get("payload", {}),
                "depends_on": task.get("depends_on", []),
                "priority": task.get("priority", "normal"),
                "created_at": now,
                "updated_at": now,
                "next_run_at": now,
                "attempt_count": 0,
                "max_retries": int(task.get("max_retries", 2)),
                "last_error": "",
                "worker_id": "",
            })
            added += 1
        _save_queue(data)
        return {"queued": added}


def _completed_task_ids(items: List[Dict[str, Any]], mission_id: str) -> set:
    return {
        str(i.get("task_id"))
        for i in items
        if i.get("mission_id") == mission_id and i.get("status") == "completed"
    }


def dequeue_next_task(worker_id: str = "worker") -> Optional[Dict[str, Any]]:
    with _queue_lock:
        data = _load_queue()
        items = data.get("items", [])
        now = int(time.time())
        completed_lookup_cache = {}

        def sort_key(item: Dict[str, Any]):
            return (
                _PRIORITY_SCORE.get(str(item.get("priority", "normal")).lower(), 1),
                int(item.get("next_run_at", 0)),
                int(item.get("created_at", 0)),
            )

        candidates = [i for i in items if i.get("status") == "queued" and int(i.get("next_run_at", 0)) <= now]
        candidates.sort(key=sort_key)

        for item in candidates:
            mission_id = str(item.get("mission_id", ""))
            if mission_id not in completed_lookup_cache:
                completed_lookup_cache[mission_id] = _completed_task_ids(items, mission_id)
            completed = completed_lookup_cache[mission_id]
            depends_on = [str(x) for x in item.get("depends_on", [])]
            if any(dep not in completed for dep in depends_on):
                continue

            item["status"] = "running"
            item["updated_at"] = now
            item["attempt_count"] = int(item.get("attempt_count", 0)) + 1
            item["worker_id"] = worker_id
            _save_queue(data)
            return item
    return None


def complete_task(mission_id: str, task_id: str, result_status: str, last_error: str = "") -> Optional[Dict[str, Any]]:
    with _queue_lock:
        data = _load_queue()
        for item in data.get("items", []):
            if item.get("mission_id") == mission_id and item.get("task_id") == task_id and item.get("status") == "running":
                item["status"] = result_status
                item["updated_at"] = int(time.time())
                item["last_error"] = last_error
                _save_queue(data)
                return item
    return None


def requeue_task(mission_id: str, task_id: str, backoff_seconds: float, last_error: str = "") -> Optional[Dict[str, Any]]:
    with _queue_lock:
        data = _load_queue()
        now = int(time.time())
        for item in data.get("items", []):
            if item.get("mission_id") == mission_id and item.get("task_id") == task_id:
                item["status"] = "queued"
                item["updated_at"] = now
                item["next_run_at"] = now + max(1, int(backoff_seconds))
                item["last_error"] = last_error
                item["worker_id"] = ""
                _save_queue(data)
                return item
    return None


def cancel_mission_queue_items(mission_id: str) -> Dict[str, int]:
    with _queue_lock:
        data = _load_queue()
        changed = 0
        running = 0
        for item in data.get("items", []):
            if str(item.get("mission_id")) != str(mission_id):
                continue
            if item.get("status") == "queued":
                item["status"] = "cancelled"
                item["updated_at"] = int(time.time())
                changed += 1
            elif item.get("status") == "running":
                item["status"] = "cancel_requested"
                item["updated_at"] = int(time.time())
                running += 1
        _save_queue(data)
        return {"cancelled_queued": changed, "cancel_requested_running": running}


def retry_failed_tasks(mission_id: str) -> Dict[str, int]:
    with _queue_lock:
        data = _load_queue()
        changed = 0
        now = int(time.time())
        for item in data.get("items", []):
            if str(item.get("mission_id")) != str(mission_id):
                continue
            if item.get("status") == "failed":
                item["status"] = "queued"
                item["next_run_at"] = now
                item["updated_at"] = now
                item["last_error"] = ""
                item["worker_id"] = ""
                changed += 1
        _save_queue(data)
        return {"requeued_failed": changed}


def list_queue(limit: int = 200) -> List[Dict[str, Any]]:
    with _queue_lock:
        data = _load_queue()
        items = sorted(data.get("items", []), key=lambda x: x.get("created_at", 0), reverse=True)
        return items[:limit]


def mission_queue_items(mission_id: str) -> List[Dict[str, Any]]:
    with _queue_lock:
        data = _load_queue()
        items = [i for i in data.get("items", []) if str(i.get("mission_id")) == str(mission_id)]
        items.sort(key=lambda x: (int(x.get("created_at", 0)), str(x.get("task_id", ""))))
        return items


def queue_stats() -> Dict[str, Any]:
    with _queue_lock:
        data = _load_queue()
        items = data.get("items", [])
        return {
            "queue_total": len(items),
            "queued_total": sum(1 for i in items if i.get("status") == "queued"),
            "running_total": sum(1 for i in items if i.get("status") == "running"),
            "completed_total": sum(1 for i in items if i.get("status") == "completed"),
            "failed_total": sum(1 for i in items if i.get("status") == "failed"),
            "cancelled_total": sum(1 for i in items if i.get("status") in {"cancelled", "cancel_requested"}),
            "queue_file": QUEUE_FILE,
        }