from __future__ import annotations

import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from app.critic import critic_check
from app.mission_utils import auto_complete_mission_if_done
from app.planner import plan_task
from app.safety import assert_safe_shell_command
from app.runtime import (
    TaskStatus,
    append_runtime_log,
    create_task_record,
    fail_timed_out_tasks,
    get_next_queued_task,
    load_agents,
    recover_stale_running_tasks,
    tasks_store,
    transition_task,
)


_WORKER_THREAD = None
_WORKER_STOP_EVENT = threading.Event()
_WORKER_LAST_HEARTBEAT = 0.0
_WORKER_LAST_ERROR = None
_WORKER_LAST_PROCESSED_TASK_ID = None


def get_enabled_agents() -> dict[str, dict[str, Any]]:
    agents = load_agents()
    enabled = {}
    for agent in agents:
        if agent.get("enabled", True):
            enabled[agent.get("name")] = agent
    return enabled


def get_worker_state() -> dict[str, Any]:
    global _WORKER_THREAD, _WORKER_LAST_HEARTBEAT, _WORKER_LAST_ERROR, _WORKER_LAST_PROCESSED_TASK_ID

    return {
        "thread_exists": _WORKER_THREAD is not None,
        "thread_alive": _WORKER_THREAD.is_alive() if _WORKER_THREAD else False,
        "last_heartbeat": _WORKER_LAST_HEARTBEAT,
        "last_error": _WORKER_LAST_ERROR,
        "last_processed_task_id": _WORKER_LAST_PROCESSED_TASK_ID,
        "stop_requested": _WORKER_STOP_EVENT.is_set(),
        "enabled_agents": sorted(list(get_enabled_agents().keys())),
    }


def validate_agent_for_task(assigned_agent: str | None, task_type: str) -> None:
    if not assigned_agent:
        return

    agents = get_enabled_agents()
    agent = agents.get(assigned_agent)
    if not agent:
        raise ValueError(f"Assigned agent not found or disabled: {assigned_agent}")


def execute_task(task_type: str, payload: dict[str, Any] | None = None, parent_task_id: str | None = None, mission_id: str | None = None) -> dict[str, Any]:
    payload = payload or {}

    if task_type == "plan":
        planned_tasks = plan_task(payload)
        created_task_ids = []

        for t in planned_tasks:
            new_task_id = f"task_{uuid.uuid4().hex[:8]}"
            create_task_record(
                task_id=new_task_id,
                mission_id=mission_id,
                task_type=t["task_type"],
                payload=t.get("payload", {}),
                assigned_agent=t.get("assigned_agent"),
                parent_task_id=parent_task_id,
            )
            created_task_ids.append(new_task_id)

        return {
            "planned_count": len(planned_tasks),
            "created_task_ids": created_task_ids,
            "planned_tasks": planned_tasks,
        }

    if task_type == "critic_check":
        return critic_check(payload)

    if task_type == "echo":
        return {"message": payload.get("message", "")}

    if task_type == "write_file":
        path = payload.get("path")
        content = payload.get("content", "")
        if not path:
            raise ValueError("path is required for write_file")
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return {"written": True, "path": str(target.resolve())}

    if task_type == "read_file":
        path = payload.get("path")
        if not path:
            raise ValueError("path is required for read_file")
        target = Path(path)
        if not target.exists():
            raise FileNotFoundError(f"File not found: {path}")
        return {"path": str(target.resolve()), "content": target.read_text(encoding="utf-8")}

    if task_type == "list_dir":
        path = payload.get("path", ".")
        target = Path(path)
        if not target.exists():
            raise FileNotFoundError(f"Directory not found: {path}")
        return {"path": str(target.resolve()), "items": sorted([item.name for item in target.iterdir()])}

    if task_type == "run_python_tests":
        venv_python = Path(".venv") / "Scripts" / "python.exe"
        cmd = payload.get("command")
        if not cmd:
            if venv_python.exists():
                cmd = [str(venv_python), "-m", "pytest", "-q"]
            else:
                cmd = ["python", "-m", "pytest", "-q"]

        completed = subprocess.run(cmd, capture_output=True, text=True, shell=False, timeout=120)
        return {
            "returncode": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
        }

    if task_type == "run_shell_command":
        command = payload.get("command")
        if not command:
            raise ValueError("command is required for run_shell_command")
        assert_safe_shell_command(command)
        completed = subprocess.run(command, capture_output=True, text=True, shell=True, timeout=60)
        return {
            "returncode": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "command": command,
        }

    raise ValueError(f"Unsupported task_type: {task_type}")


def process_one_task() -> bool:
    global _WORKER_LAST_PROCESSED_TASK_ID

    tasks_store.refresh_from_disk()
    task = get_next_queued_task()
    if not task:
        return False

    _WORKER_LAST_PROCESSED_TASK_ID = task.task_id
    append_runtime_log(f"TASK picked task_id={task.task_id} type={task.task_type} assigned_agent={task.assigned_agent}")

    existing = tasks_store.get(task.task_id, refresh=True)
    if not existing:
        append_runtime_log(f"WARN stale task reference skipped task_id={task.task_id}")
        return True

    if existing.attempt_count >= existing.max_attempts and existing.status in [TaskStatus.QUEUED.value, TaskStatus.RETRYING.value]:
        transition_task(task.task_id, TaskStatus.FAILED, error="Max attempts exceeded")
        append_runtime_log(f"TASK failed task_id={task.task_id} reason=max_attempts_exceeded")
        return True

    try:
        validate_agent_for_task(task.assigned_agent, task.task_type)

        started = transition_task(task.task_id, TaskStatus.RUNNING)
        if not started:
            append_runtime_log(f"ERROR could not transition task to RUNNING task_id={task.task_id}")
            return True

        append_runtime_log(f"TASK started task_id={task.task_id}")

        result = execute_task(
            task.task_type,
            task.payload or {},
            parent_task_id=task.task_id,
            mission_id=task.mission_id,
        )

        completed = transition_task(task.task_id, TaskStatus.COMPLETED, result=result)

        if completed:
            append_runtime_log(f"TASK completed task_id={task.task_id}")
            if task.mission_id:
                mission_update = auto_complete_mission_if_done(task.mission_id)
                append_runtime_log(f"MISSION auto-check mission_id={task.mission_id} result={mission_update}")
        else:
            append_runtime_log(f"ERROR task disappeared before COMPLETED task_id={task.task_id}")

        return True

    except subprocess.TimeoutExpired as exc:
        transition_task(task.task_id, TaskStatus.FAILED_TIMEOUT, error=repr(exc))
        append_runtime_log(f"TASK timeout task_id={task.task_id} error={repr(exc)}")
        if task.mission_id:
            mission_update = auto_complete_mission_if_done(task.mission_id)
            append_runtime_log(f"MISSION auto-check mission_id={task.mission_id} result={mission_update}")
        return True

    except Exception as exc:
        task_now = tasks_store.get(task.task_id, refresh=True)
        if task_now and task_now.attempt_count < task_now.max_attempts:
            transition_task(task.task_id, TaskStatus.RETRYING, error=repr(exc))
            append_runtime_log(f"TASK retrying task_id={task.task_id} error={repr(exc)}")
        else:
            transition_task(task.task_id, TaskStatus.FAILED, error=repr(exc))
            append_runtime_log(f"TASK failed task_id={task.task_id} error={repr(exc)}")
            if task.mission_id:
                mission_update = auto_complete_mission_if_done(task.mission_id)
                append_runtime_log(f"MISSION auto-check mission_id={task.mission_id} result={mission_update}")
        return True


def worker_loop(poll_interval: float = 2.0) -> None:
    global _WORKER_LAST_HEARTBEAT, _WORKER_LAST_ERROR

    append_runtime_log("Worker loop started")

    try:
        recovered = recover_stale_running_tasks(stale_after_seconds=120)
        if recovered:
            append_runtime_log(f"Recovered stale tasks on startup: {recovered}")
    except Exception as exc:
        _WORKER_LAST_ERROR = repr(exc)
        append_runtime_log(f"Worker startup recovery failed: {repr(exc)}")

    while not _WORKER_STOP_EVENT.is_set():
        _WORKER_LAST_HEARTBEAT = time.time()

        try:
            timed_out = fail_timed_out_tasks()
            if timed_out:
                append_runtime_log(f"Watchdog timed out tasks: {timed_out}")

            processed = process_one_task()
            if not processed:
                time.sleep(poll_interval)
            else:
                time.sleep(0.2)
        except Exception as exc:
            _WORKER_LAST_ERROR = repr(exc)
            append_runtime_log(f"Worker loop iteration failed: {repr(exc)}")
            time.sleep(2.0)

    append_runtime_log("Worker loop stopped")


def start_worker() -> None:
    global _WORKER_THREAD, _WORKER_LAST_ERROR

    if _WORKER_THREAD and _WORKER_THREAD.is_alive():
        append_runtime_log("Worker start requested but worker already running")
        return

    _WORKER_LAST_ERROR = None
    _WORKER_STOP_EVENT.clear()
    _WORKER_THREAD = threading.Thread(target=worker_loop, name="worker_loop", daemon=True)
    _WORKER_THREAD.start()
    append_runtime_log("Worker thread launched")


def stop_worker() -> None:
    _WORKER_STOP_EVENT.set()
    append_runtime_log("Worker stop requested")
