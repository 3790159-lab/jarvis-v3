from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parent.parent
STATE_DIR = BASE_DIR / "state"
STATE_DIR.mkdir(parents=True, exist_ok=True)

MISSIONS_FILE = STATE_DIR / "missions.json"
TASKS_FILE = STATE_DIR / "tasks.json"
AGENTS_FILE = STATE_DIR / "agents.json"
LOG_FILE = STATE_DIR / "runtime.log"
LOCK_FILE = STATE_DIR / "api.lock"


DEFAULT_AGENTS = [
    {
        "name": "planner_agent",
        "role": "Plans tasks and routes work",
        "capabilities": ["plan", "route", "mission_plan_file"],
        "enabled": True,
        "registered_at": 0,
    },
    {
        "name": "executor_agent",
        "role": "Executes standard file and utility tasks",
        "capabilities": ["echo", "write_file", "read_file", "list_dir", "run_python_tests"],
        "enabled": True,
        "registered_at": 0,
    },
    {
        "name": "shell_agent",
        "role": "Runs controlled shell commands in safe mode",
        "capabilities": ["run_shell_command"],
        "enabled": True,
        "registered_at": 0,
    },
    {
        "name": "critic_agent",
        "role": "Validates outputs and execution results",
        "capabilities": ["critic_check"],
        "enabled": True,
        "registered_at": 0,
    },
]


class MissionStatus(str, Enum):
    CREATED = "created"
    QUEUED = "queued"
    RUNNING = "running"
    RETRYING = "retrying"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    RETRYING = "retrying"
    CANCELLED = "cancelled"
    FAILED_TIMEOUT = "failed_timeout"


@dataclass
class MissionRecord:
    mission_id: str
    title: str
    status: str
    created_at: float
    updated_at: float
    attempt_count: int = 0
    error: str | None = None
    payload: dict[str, Any] | None = None


@dataclass
class TaskRecord:
    task_id: str
    mission_id: str | None
    task_type: str
    status: str
    created_at: float
    updated_at: float
    attempt_count: int = 0
    payload: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    assigned_agent: str | None = None
    parent_task_id: str | None = None
    max_attempts: int = 3
    timeout_seconds: int = 60
    started_at: float | None = None
    finished_at: float | None = None


def append_runtime_log(message: str) -> None:
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    with LOG_FILE.open("a", encoding="utf-8") as file:
        file.write(f"[{timestamp}] {message}\n")


class JsonStore:
    def __init__(self, path: Path, model_cls):
        self.path = path
        self.model_cls = model_cls
        self.lock = threading.RLock()
        self.items: dict[str, Any] = {}
        self._load()

    def _detect_key(self, item: Any) -> str:
        if hasattr(item, "task_id") and getattr(item, "task_id", None):
            return item.task_id
        if hasattr(item, "mission_id") and getattr(item, "mission_id", None):
            return item.mission_id
        raise ValueError("Unsupported or empty model key")

    def _load(self) -> None:
        if not self.path.exists():
            self.items = {}
            return

        try:
            text = self.path.read_text(encoding="utf-8").strip()
            if not text:
                self.items = {}
                return

            raw = json.loads(text)
            loaded = {}
            for item in raw.get("items", []):
                try:
                    model = self.model_cls(**item)
                    loaded[self._detect_key(model)] = model
                except Exception:
                    continue
            self.items = loaded
        except Exception:
            broken = self.path.with_suffix(f".broken.{int(time.time())}.json")
            try:
                self.path.replace(broken)
            except Exception:
                pass
            self.items = {}

    def refresh_from_disk(self) -> None:
        with self.lock:
            self._load()

    def _save(self) -> None:
        temp = self.path.with_suffix(".tmp")
        payload = {"items": [asdict(item) for item in self.items.values()]}
        temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(self.path)

    def get(self, item_id: str, refresh: bool = False):
        with self.lock:
            if refresh:
                self._load()
            return self.items.get(item_id)

    def list_all(self, refresh: bool = False) -> list[Any]:
        with self.lock:
            if refresh:
                self._load()
            return sorted(self.items.values(), key=lambda x: x.updated_at, reverse=True)

    def upsert(self, item: Any):
        with self.lock:
            item.updated_at = time.time()
            self.items[self._detect_key(item)] = item
            self._save()
            return item

    def clear(self) -> None:
        with self.lock:
            self.items = {}
            self._save()

    def prune_completed(self, keep_last: int = 50) -> int:
        with self.lock:
            completed = [item for item in self.items.values() if getattr(item, "status", None) == TaskStatus.COMPLETED.value]
            completed.sort(key=lambda x: x.updated_at, reverse=True)
            to_keep = {self._detect_key(item) for item in completed[:keep_last]}
            removed = 0

            keys = list(self.items.keys())
            for key in keys:
                item = self.items[key]
                if getattr(item, "status", None) == TaskStatus.COMPLETED.value and key not in to_keep:
                    del self.items[key]
                    removed += 1

            if removed:
                self._save()
            return removed


missions_store = JsonStore(MISSIONS_FILE, MissionRecord)
tasks_store = JsonStore(TASKS_FILE, TaskRecord)


def ensure_default_agents() -> list[dict[str, Any]]:
    try:
        if not AGENTS_FILE.exists():
            AGENTS_FILE.write_text(json.dumps({"agents": DEFAULT_AGENTS}, ensure_ascii=False, indent=2), encoding="utf-8")
            return DEFAULT_AGENTS

        text = AGENTS_FILE.read_text(encoding="utf-8").strip()
        if not text:
            AGENTS_FILE.write_text(json.dumps({"agents": DEFAULT_AGENTS}, ensure_ascii=False, indent=2), encoding="utf-8")
            return DEFAULT_AGENTS

        raw = json.loads(text)
        agents = raw.get("agents", [])
        if not agents:
            AGENTS_FILE.write_text(json.dumps({"agents": DEFAULT_AGENTS}, ensure_ascii=False, indent=2), encoding="utf-8")
            return DEFAULT_AGENTS

        return agents
    except Exception:
        AGENTS_FILE.write_text(json.dumps({"agents": DEFAULT_AGENTS}, ensure_ascii=False, indent=2), encoding="utf-8")
        return DEFAULT_AGENTS


def load_agents() -> list[dict[str, Any]]:
    return ensure_default_agents()


def save_agents(agents: list[dict[str, Any]]) -> None:
    AGENTS_FILE.write_text(json.dumps({"agents": agents}, ensure_ascii=False, indent=2), encoding="utf-8")


def get_runtime_meta() -> dict[str, Any]:
    return {
        "state_dir": str(STATE_DIR),
        "missions_file": str(MISSIONS_FILE),
        "tasks_file": str(TASKS_FILE),
        "agents_file": str(AGENTS_FILE),
        "log_file": str(LOG_FILE),
        "lock_file": str(LOCK_FILE),
        "pid": os.getpid(),
    }


def create_lock() -> None:
    payload = {"pid": os.getpid(), "created_at": time.time()}
    LOCK_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def remove_lock() -> None:
    if LOCK_FILE.exists():
        try:
            LOCK_FILE.unlink()
        except Exception:
            pass


def create_mission_record(mission_id: str, title: str, payload: dict[str, Any] | None = None) -> MissionRecord:
    now = time.time()
    mission = MissionRecord(
        mission_id=mission_id,
        title=title,
        status=MissionStatus.CREATED.value,
        created_at=now,
        updated_at=now,
        payload=payload or {},
    )
    return missions_store.upsert(mission)


def transition_mission(mission_id: str, new_status: MissionStatus, error: str | None = None) -> MissionRecord:
    mission = missions_store.get(mission_id, refresh=True)
    if not mission:
        raise KeyError(f"Mission not found: {mission_id}")

    mission.status = new_status.value
    mission.error = error
    if new_status in {MissionStatus.RUNNING, MissionStatus.RETRYING}:
        mission.attempt_count += 1
    return missions_store.upsert(mission)


def create_task_record(
    task_id: str,
    mission_id: str | None,
    task_type: str,
    payload: dict[str, Any] | None = None,
    assigned_agent: str | None = None,
    parent_task_id: str | None = None,
    max_attempts: int = 3,
    timeout_seconds: int = 60,
) -> TaskRecord:
    now = time.time()
    task = TaskRecord(
        task_id=task_id,
        mission_id=mission_id,
        task_type=task_type,
        status=TaskStatus.QUEUED.value,
        created_at=now,
        updated_at=now,
        payload=payload or {},
        assigned_agent=assigned_agent,
        parent_task_id=parent_task_id,
        max_attempts=max_attempts,
        timeout_seconds=timeout_seconds,
    )
    return tasks_store.upsert(task)


def transition_task(
    task_id: str,
    new_status: TaskStatus,
    result: dict[str, Any] | None = None,
    error: str | None = None,
) -> TaskRecord | None:
    task = tasks_store.get(task_id, refresh=True)
    if not task:
        append_runtime_log(f"WARN transition_task skipped missing task_id={task_id}")
        return None

    task.status = new_status.value
    task.result = result
    task.error = error

    now = time.time()
    if new_status in {TaskStatus.RUNNING, TaskStatus.RETRYING}:
        task.attempt_count += 1
        if task.started_at is None:
            task.started_at = now

    if new_status in {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.FAILED_TIMEOUT, TaskStatus.CANCELLED}:
        task.finished_at = now

    return tasks_store.upsert(task)


def get_next_queued_task() -> TaskRecord | None:
    tasks = tasks_store.list_all(refresh=True)
    queued = [t for t in tasks if t.status == TaskStatus.QUEUED.value]
    queued.sort(key=lambda item: item.created_at)
    return queued[0] if queued else None


def recover_stale_running_tasks(stale_after_seconds: int = 120) -> list[str]:
    now = time.time()
    recovered: list[str] = []

    for task in tasks_store.list_all(refresh=True):
        if task.status == TaskStatus.RUNNING.value and (now - task.updated_at) > stale_after_seconds:
            task.status = TaskStatus.RETRYING.value
            task.error = "Recovered after stale RUNNING task detected on startup"
            task.attempt_count += 1
            tasks_store.upsert(task)
            recovered.append(task.task_id)

    return recovered


def fail_timed_out_tasks() -> list[str]:
    now = time.time()
    timed_out: list[str] = []

    for task in tasks_store.list_all(refresh=True):
        if task.status == TaskStatus.RUNNING.value and task.started_at is not None:
            if (now - task.started_at) > task.timeout_seconds:
                task.status = TaskStatus.FAILED_TIMEOUT.value
                task.error = "Task exceeded timeout_seconds"
                task.finished_at = now
                tasks_store.upsert(task)
                timed_out.append(task.task_id)

    return timed_out


ensure_default_agents()
