from __future__ import annotations
import app.core.bootstrap_utf8  # noqa: F401

from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Any
from uuid import uuid4
import json

from app.services.executor_registry import ExecutorRegistry
from app.services.planner import plan_task

BASE_DIR = Path(__file__).resolve().parent.parent.parent
STATE_DIR = BASE_DIR / "state"
STATE_DIR.mkdir(parents=True, exist_ok=True)

ARTIFACTS_DIR = BASE_DIR / "artifacts"
ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)

LOGS_DIR = ARTIFACTS_DIR / "logs"
LOGS_DIR.mkdir(parents=True, exist_ok=True)

DB_FILE = STATE_DIR / "goals_db.json"
MISSIONS_INDEX_FILE = STATE_DIR / "missions_index.json"
DB_LOCK = Lock()


def utc_now_iso() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def empty_db() -> dict[str, Any]:
    return {"goals": {}, "missions": {}}


def load_json_file(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json_atomic(path: Path, data: Any) -> None:
    tmp_file = path.with_suffix(path.suffix + ".tmp")
    tmp_file.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp_file.replace(path)


def load_db() -> dict[str, Any]:
    data = load_json_file(DB_FILE, empty_db())
    if not isinstance(data, dict):
        data = empty_db()
    data.setdefault("goals", {})
    data.setdefault("missions", {})
    return data


def save_db(data: dict[str, Any]) -> None:
    save_json_atomic(DB_FILE, data)


def load_missions_index() -> dict[str, Any]:
    data = load_json_file(MISSIONS_INDEX_FILE, {})
    if not isinstance(data, dict):
        data = {}
    return data


def save_missions_index(data: dict[str, Any]) -> None:
    save_json_atomic(MISSIONS_INDEX_FILE, data)


def append_log(mission_id: str, line: str) -> None:
    log_file = LOGS_DIR / f"{mission_id}.log"
    with log_file.open("a", encoding="utf-8") as f:
        f.write(f"{utc_now_iso()} | {line}\n")


class MissionEngine:
    def __init__(self) -> None:
        self.executor = ExecutorRegistry()

    def _ensure_mission_index(self, mission_record: dict[str, Any]) -> None:
        index = load_missions_index()
        index[mission_record["mission_id"]] = {
            "mission_id": mission_record["mission_id"],
            "goal_id": mission_record.get("goal_id"),
            "objective": mission_record.get("objective"),
            "status": mission_record.get("status"),
            "created_at": mission_record.get("created_at"),
        }
        save_missions_index(index)

    def _recover_mission_if_possible(self, mission_id: str) -> dict[str, Any] | None:
        db = load_db()
        mission = db.get("missions", {}).get(mission_id)
        if mission:
            return mission

        index = load_missions_index().get(mission_id)
        log_file = LOGS_DIR / f"{mission_id}.log"

        if index and log_file.exists():
            recovered = {
                "goal_id": index.get("goal_id"),
                "mission_id": mission_id,
                "objective": index.get("objective", "Recovered mission"),
                "constraints": {},
                "mission_summary": "Recovered mission from missions index.",
                "status": index.get("status", "planned"),
                "created_at": index.get("created_at", utc_now_iso()),
                "planned_at": utc_now_iso(),
                "tasks": plan_task(index.get("objective", "Recovered mission")),
                "task_results": [],
                "attempt_count": 0,
                "last_error": None,
                "recovered": True,
            }
            db.setdefault("missions", {})[mission_id] = recovered
            save_db(db)
            append_log(mission_id, "Mission recovered from missions index")
            return recovered

        return None

    def create_goal(self, objective: str, constraints: dict | None = None) -> dict[str, Any]:
        objective = (objective or "").strip()
        constraints = constraints or {}

        if not objective:
            raise ValueError("Objective is empty")

        goal_id = f"goal_{uuid4().hex[:8]}"
        mission_id = f"mission_{uuid4().hex[:8]}"
        tasks = plan_task(objective)

        goal_record = {
            "goal_id": goal_id,
            "mission_id": mission_id,
            "objective": objective,
            "constraints": constraints,
            "status": "created",
            "created_at": utc_now_iso(),
        }

        mission_record = {
            "goal_id": goal_id,
            "mission_id": mission_id,
            "objective": objective,
            "constraints": constraints,
            "mission_summary": "Mission created and planned by MissionEngine.",
            "status": "planned",
            "created_at": utc_now_iso(),
            "planned_at": utc_now_iso(),
            "tasks": tasks,
            "task_results": [],
            "attempt_count": 0,
            "last_error": None,
        }

        with DB_LOCK:
            db = load_db()
            db["goals"][goal_id] = goal_record
            db["missions"][mission_id] = mission_record
            save_db(db)
            self._ensure_mission_index(mission_record)

            verify_db = load_db()
            verified = verify_db.get("missions", {}).get(mission_id)

        append_log(mission_id, f"Goal created for objective: {objective}")

        if not verified:
            raise RuntimeError(f"Mission persistence verification failed for {mission_id}")

        return {
            "goal_id": goal_id,
            "mission_id": mission_id,
            "summary": mission_record["mission_summary"],
            "status": mission_record["status"],
            "tasks_planned": len(tasks),
        }

    def list_missions(self) -> list[dict[str, Any]]:
        with DB_LOCK:
            db = load_db()
            missions = list(db.get("missions", {}).values())

        missions.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        return missions

    def get_mission(self, mission_id: str) -> dict[str, Any] | None:
        with DB_LOCK:
            db = load_db()
            mission = db.get("missions", {}).get(mission_id)

        if mission:
            return mission

        with DB_LOCK:
            return self._recover_mission_if_possible(mission_id)

    def run_mission(self, mission_id: str) -> dict[str, Any]:
        with DB_LOCK:
            db = load_db()
            mission = db.get("missions", {}).get(mission_id)

        if not mission:
            with DB_LOCK:
                mission = self._recover_mission_if_possible(mission_id)

        if not mission:
            raise KeyError(f"Mission '{mission_id}' not found")

        with DB_LOCK:
            db = load_db()
            mission = db.get("missions", {}).get(mission_id, mission)

            if mission.get("status") == "completed":
                return {
                    "mission_id": mission_id,
                    "status": "completed",
                    "summary": "Mission already completed",
                    "task_results": mission.get("task_results", []),
                }

            mission["status"] = "running"
            mission["started_at"] = utc_now_iso()
            mission["attempt_count"] = int(mission.get("attempt_count", 0)) + 1
            db["missions"][mission_id] = mission
            save_db(db)
            self._ensure_mission_index(mission)

        append_log(mission_id, "Mission started")

        task_results: list[dict[str, Any]] = []
        final_status = "completed"
        last_error = None

        for task in mission.get("tasks", []):
            append_log(mission_id, f"Executing task: {task.get('task_id')} type={task.get('type')}")
            result = self.executor.execute(task)
            task_results.append(result)

            if result.get("status") != "completed":
                final_status = "failed"
                last_error = result.get("message")
                append_log(mission_id, f"Task failed: {result}")
                break

            append_log(mission_id, f"Task completed: {result}")

        with DB_LOCK:
            db = load_db()
            mission = db.get("missions", {}).get(mission_id, mission)

            mission["status"] = final_status
            mission["completed_at"] = utc_now_iso()
            mission["task_results"] = task_results
            mission["last_error"] = last_error
            db["missions"][mission_id] = mission

            goal_id = mission.get("goal_id")
            if goal_id and goal_id in db.get("goals", {}):
                db["goals"][goal_id]["status"] = final_status

            save_db(db)
            self._ensure_mission_index(mission)

        append_log(mission_id, f"Mission finished with status={final_status}")

        return {
            "mission_id": mission_id,
            "status": final_status,
            "summary": f"Mission finished with status '{final_status}'. Total tasks: {len(task_results)}.",
            "task_results": task_results,
        }

    def get_logs(self, mission_id: str) -> str:
        log_file = LOGS_DIR / f"{mission_id}.log"
        if not log_file.exists():
            return "No logs found."
        return log_file.read_text(encoding="utf-8")
