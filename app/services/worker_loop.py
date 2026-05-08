import json
import os
import tempfile
import threading
import time
from typing import Any, Dict

from app.services.active_run_store import create_active_snapshot, heartbeat_active_snapshot, remove_active_snapshot
from app.services.continuity import recover_stale_active_runs
from app.services.mission_memory import append_memory
from app.services.mission_store import append_task_result, get_mission, mark_task_status, update_mission_status
from app.services.policy import get_policy
from app.services.retry_engine import retry_allowed, retry_meta
from app.services.task_executor import execute_task
from app.services.task_queue import complete_task, dequeue_next_task, queue_stats, requeue_task
from app.services.worker_registry import heartbeat_worker, register_worker, stop_worker


BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
STATE_DIR = os.path.join(BASE_DIR, "state")
WORKER_STATE_FILE = os.path.join(STATE_DIR, "worker_state.json")

_worker_lock = threading.Lock()
_worker_state = {
    "is_running": False,
    "last_heartbeat": 0,
    "last_task_id": "",
    "last_mission_id": "",
    "last_status": "idle",
    "last_error": "",
    "loop_count": 0,
    "worker_id": "worker",
}


def _safe_write_text(path: str, content: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix="worker_", suffix=".tmp", dir=os.path.dirname(path))
    os.close(fd)
    try:
        with open(temp_path, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(temp_path, path)
    finally:
        try:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        except Exception:
            pass


def _persist_state() -> None:
    with _worker_lock:
        _safe_write_text(WORKER_STATE_FILE, json.dumps(_worker_state, ensure_ascii=False, indent=2))


def get_worker_state() -> Dict[str, Any]:
    with _worker_lock:
        return dict(_worker_state)


def _update_state(**kwargs: Any) -> None:
    with _worker_lock:
        for key, value in kwargs.items():
            _worker_state[key] = value
    _persist_state()


def _refresh_mission_status(mission_id: str) -> None:
    mission = get_mission(mission_id)
    if not mission:
        return

    results = mission.get("task_results", [])
    tasks = mission.get("tasks", [])

    if not tasks:
        update_mission_status(mission_id, "completed", "Mission finished: no tasks.")
        return

    completed = sum(1 for r in results if r.get("status") == "completed")
    failed = sum(1 for r in results if r.get("status") == "failed")
    total = len(tasks)

    if completed + failed >= total:
        if failed > 0:
            update_mission_status(mission_id, "failed", f"Mission finished with failures. Total tasks: {total}. Completed: {completed}. Failed: {failed}.")
        else:
            update_mission_status(mission_id, "completed", f"Mission finished successfully. Total tasks: {total}. Completed: {completed}. Failed: {failed}.")


def run_worker_iteration(worker_id: str = "worker") -> Dict[str, Any]:
    _update_state(
        worker_id=worker_id,
        last_heartbeat=int(time.time()),
        last_status="polling",
        loop_count=int(get_worker_state().get("loop_count", 0)) + 1,
    )
    heartbeat_worker(worker_id, status="polling")

    recover_stale_active_runs(
        worker_timeout_seconds=int(get_policy().get("worker_stale_timeout_seconds", 15)),
        active_timeout_seconds=int(get_policy().get("active_run_stale_timeout_seconds", 20)),
    )

    task = dequeue_next_task(worker_id=worker_id)
    if not task:
        _update_state(last_status="idle", last_error="")
        heartbeat_worker(worker_id, status="idle")
        return {"status": "idle", "queue": queue_stats(), "worker_id": worker_id}

    mission_id = task.get("mission_id", "")
    task_id = task.get("task_id", "")

    _update_state(last_task_id=task_id, last_mission_id=mission_id, last_status="running_task", last_error="")
    heartbeat_worker(worker_id, status="running_task")
    create_active_snapshot(task)

    if mission_id:
        update_mission_status(mission_id, "running")
        append_memory(mission_id, "task_started", {"task_id": task_id, "worker_id": worker_id})

    if mission_id and task_id:
        mark_task_status(mission_id, task_id, "running")

    result = execute_task(task)
    heartbeat_active_snapshot(mission_id, task_id)

    if result.get("status") == "completed":
        complete_task(mission_id, task_id, "completed", "")
        remove_active_snapshot(mission_id, task_id)
        if mission_id and task_id:
            mark_task_status(mission_id, task_id, "completed")
            append_task_result(mission_id, result)
            append_memory(mission_id, "task_completed", result)
            _refresh_mission_status(mission_id)
        _update_state(last_status="completed", last_error="")
        heartbeat_worker(worker_id, status="completed")
        return {"status": "completed", "task": task, "result": result, "queue": queue_stats(), "worker_id": worker_id}

    attempt_count = int(task.get("attempt_count", 1))
    max_retries = int(task.get("max_retries", get_policy().get("max_retries", 2)))
    retry_info = retry_meta(attempt_count, max_retries=max_retries)

    if retry_allowed(attempt_count, max_retries=max_retries):
        requeue_task(mission_id=mission_id, task_id=task_id, backoff_seconds=float(retry_info["backoff_seconds"]), last_error=result.get("message", ""))
        remove_active_snapshot(mission_id, task_id)
        retry_result = dict(result)
        retry_result["status"] = "retried"
        retry_result["retry"] = retry_info
        if mission_id and task_id:
            mark_task_status(mission_id, task_id, "queued")
            append_task_result(mission_id, retry_result)
            append_memory(mission_id, "task_retried", retry_result)
        _update_state(last_status="retried", last_error=result.get("message", ""))
        heartbeat_worker(worker_id, status="retried", last_error=result.get("message", ""))
        return {"status": "retried", "task": task, "result": retry_result, "queue": queue_stats(), "worker_id": worker_id}

    complete_task(mission_id, task_id, "failed", result.get("message", ""))
    remove_active_snapshot(mission_id, task_id)
    if mission_id and task_id:
        mark_task_status(mission_id, task_id, "failed")
        append_task_result(mission_id, result)
        append_memory(mission_id, "task_failed", result)
        _refresh_mission_status(mission_id)
    _update_state(last_status="failed", last_error=result.get("message", ""))
    heartbeat_worker(worker_id, status="failed", last_error=result.get("message", ""))
    return {"status": "failed", "task": task, "result": result, "queue": queue_stats(), "worker_id": worker_id}


def worker_loop(worker_id: str = "worker", poll_interval_seconds: float | None = None) -> None:
    policy = get_policy()
    interval = float(poll_interval_seconds if poll_interval_seconds is not None else policy.get("worker_poll_interval_seconds", 2.0))
    register_worker(worker_id, pid=os.getpid(), status="started")
    _update_state(is_running=True, worker_id=worker_id, last_status="started", last_error="")
    try:
        while True:
            run_worker_iteration(worker_id=worker_id)
            heartbeat_worker(worker_id, status="heartbeat")
            time.sleep(max(0.2, interval))
    except KeyboardInterrupt:
        _update_state(is_running=False, last_status="stopped", last_error="KeyboardInterrupt")
        stop_worker(worker_id, status="stopped", last_error="KeyboardInterrupt")
    except Exception as e:
        _update_state(is_running=False, last_status="crashed", last_error=f"{type(e).__name__}: {e}")
        stop_worker(worker_id, status="crashed", last_error=f"{type(e).__name__}: {e}")
        raise