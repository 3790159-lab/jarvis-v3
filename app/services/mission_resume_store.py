from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional


BASE_DIR = Path("jarvis_stage3_artifacts") / "resume_runtime"
SNAPSHOT_DIR = BASE_DIR / "snapshots"
LOCK_DIR = BASE_DIR / "locks"
LOG_DIR = BASE_DIR / "logs"

SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
LOCK_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)


def _utc_ts() -> float:
    return time.time()


def _read_json(path: Path, default: Any) -> Any:
    try:
        if not path.exists():
            return default
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _mission_snapshot_path(mission_id: str) -> Path:
    return SNAPSHOT_DIR / f"{mission_id}.json"


def _mission_lock_path(mission_id: str) -> Path:
    return LOCK_DIR / f"{mission_id}.lock"


def log_event(mission_id: str, message: str) -> None:
    log_path = LOG_DIR / f"{mission_id}.log"
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}\n"
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(line)


def list_snapshots() -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for path in sorted(SNAPSHOT_DIR.glob("*.json")):
        payload = _read_json(path, {})
        if isinstance(payload, dict):
            items.append(payload)
    return items


def get_snapshot(mission_id: str) -> Optional[Dict[str, Any]]:
    path = _mission_snapshot_path(mission_id)
    payload = _read_json(path, None)
    if isinstance(payload, dict):
        return payload
    return None


def create_or_update_snapshot(
    mission_id: str,
    objective: str,
    steps: List[Dict[str, Any]],
    status: str,
    active_step_id: Optional[str] = None,
    error: Optional[str] = None,
) -> Dict[str, Any]:
    now = _utc_ts()
    existing = get_snapshot(mission_id) or {}

    payload = {
        "mission_id": mission_id,
        "objective": objective,
        "steps": steps,
        "status": status,
        "active_step_id": active_step_id,
        "error": error,
        "created_at": existing.get("created_at", now),
        "updated_at": now,
        "last_heartbeat_at": now,
    }
    _write_json(_mission_snapshot_path(mission_id), payload)
    log_event(mission_id, f"snapshot updated: status={status}, active_step_id={active_step_id}, error={error}")
    return payload


def touch_heartbeat(mission_id: str) -> None:
    snapshot = get_snapshot(mission_id)
    if not snapshot:
        return
    snapshot["last_heartbeat_at"] = _utc_ts()
    snapshot["updated_at"] = _utc_ts()
    _write_json(_mission_snapshot_path(mission_id), snapshot)


def mark_status(mission_id: str, status: str, error: Optional[str] = None) -> Optional[Dict[str, Any]]:
    snapshot = get_snapshot(mission_id)
    if not snapshot:
        return None
    snapshot["status"] = status
    snapshot["error"] = error
    snapshot["updated_at"] = _utc_ts()
    snapshot["last_heartbeat_at"] = _utc_ts()
    _write_json(_mission_snapshot_path(mission_id), snapshot)
    log_event(mission_id, f"status changed: {status}, error={error}")
    return snapshot


def acquire_lock(mission_id: str) -> bool:
    path = _mission_lock_path(mission_id)
    if path.exists():
        return False
    payload = {
        "mission_id": mission_id,
        "created_at": _utc_ts(),
        "pid": os.getpid(),
    }
    _write_json(path, payload)
    log_event(mission_id, "lock acquired")
    return True


def release_lock(mission_id: str) -> None:
    path = _mission_lock_path(mission_id)
    try:
        if path.exists():
            path.unlink()
            log_event(mission_id, "lock released")
    except Exception:
        pass


def has_lock(mission_id: str) -> bool:
    return _mission_lock_path(mission_id).exists()


def detect_stale_snapshots(max_age_seconds: int = 300) -> List[Dict[str, Any]]:
    now = _utc_ts()
    stale: List[Dict[str, Any]] = []

    for item in list_snapshots():
        last_heartbeat_at = float(item.get("last_heartbeat_at") or item.get("updated_at") or 0)
        status = str(item.get("status") or "").strip().lower()

        if status in {"completed", "failed"}:
            continue

        age = now - last_heartbeat_at
        if age >= max_age_seconds:
            item["stale_age_seconds"] = age
            stale.append(item)

    return stale


def mark_stale_snapshots(max_age_seconds: int = 300) -> List[Dict[str, Any]]:
    stale = detect_stale_snapshots(max_age_seconds=max_age_seconds)
    out: List[Dict[str, Any]] = []

    for item in stale:
        mission_id = item.get("mission_id")
        if not mission_id:
            continue
        updated = mark_status(str(mission_id), "stale", error="Mission heartbeat expired")
        if updated:
            out.append(updated)

    return out


def resumable_steps(snapshot: Dict[str, Any]) -> List[Dict[str, Any]]:
    steps = snapshot.get("steps") or []
    result: List[Dict[str, Any]] = []
    for step in steps:
        status = str(step.get("status") or "").strip().lower()
        if status not in {"completed"}:
            result.append(step)
    return result