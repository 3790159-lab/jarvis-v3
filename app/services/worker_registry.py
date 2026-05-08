import json
import os
import tempfile
import threading
import time
from typing import Any, Dict, List


BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
STATE_DIR = os.path.join(BASE_DIR, "state")
REGISTRY_FILE = os.path.join(STATE_DIR, "worker_registry.json")

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
    fd, temp_path = tempfile.mkstemp(prefix="worker_registry_", suffix=".tmp", dir=os.path.dirname(path))
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
    data = _safe_read_json(REGISTRY_FILE, {"workers": {}})
    if not isinstance(data, dict):
        return {"workers": {}}
    workers = data.get("workers", {})
    if not isinstance(workers, dict):
        workers = {}
    return {"workers": workers}


def _save(data: Dict[str, Any]) -> None:
    _safe_write_json(REGISTRY_FILE, data)


def register_worker(worker_id: str, pid: int, status: str = "started") -> Dict[str, Any]:
    with _lock:
        data = _load()
        now = int(time.time())
        workers = data.setdefault("workers", {})
        workers[worker_id] = {
            "worker_id": worker_id,
            "pid": int(pid),
            "status": status,
            "started_at": now,
            "last_heartbeat": now,
            "last_error": "",
        }
        _save(data)
        return workers[worker_id]


def heartbeat_worker(worker_id: str, status: str = "heartbeat", last_error: str = "") -> Dict[str, Any]:
    with _lock:
        data = _load()
        now = int(time.time())
        workers = data.setdefault("workers", {})
        item = workers.setdefault(worker_id, {
            "worker_id": worker_id,
            "pid": 0,
            "status": status,
            "started_at": now,
            "last_heartbeat": now,
            "last_error": "",
        })
        item["status"] = status
        item["last_heartbeat"] = now
        item["last_error"] = last_error or ""
        _save(data)
        return item


def stop_worker(worker_id: str, status: str = "stopped", last_error: str = "") -> Dict[str, Any] | None:
    with _lock:
        data = _load()
        workers = data.setdefault("workers", {})
        item = workers.get(worker_id)
        if not item:
            return None
        item["status"] = status
        item["last_heartbeat"] = int(time.time())
        item["last_error"] = last_error or ""
        _save(data)
        return item


def list_workers() -> List[Dict[str, Any]]:
    with _lock:
        data = _load()
        workers = list(data.get("workers", {}).values())
        workers.sort(key=lambda x: x.get("worker_id", ""))
        return workers


def stale_worker_ids(timeout_seconds: int = 15) -> List[str]:
    now = int(time.time())
    stale: List[str] = []
    for item in list_workers():
        last = int(item.get("last_heartbeat", 0))
        status = str(item.get("status", ""))
        if status in {"stopped", "crashed"}:
            stale.append(str(item.get("worker_id")))
            continue
        if now - last > int(timeout_seconds):
            stale.append(str(item.get("worker_id")))
    return stale


def registry_stats() -> Dict[str, Any]:
    workers = list_workers()
    return {
        "workers_total": len(workers),
        "running_like_total": sum(1 for w in workers if str(w.get("status", "")) not in {"stopped", "crashed"}),
        "registry_file": REGISTRY_FILE,
    }