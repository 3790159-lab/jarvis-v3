from __future__ import annotations

from pathlib import Path
from datetime import datetime
import shutil

PROJECT_ROOT = Path.cwd()
BACKUP_DIR = PROJECT_ROOT / ("backup_fix_stage1_newline_" + datetime.now().strftime("%Y%m%d_%H%M%S"))
BACKUP_DIR.mkdir(parents=True, exist_ok=True)

SERVICES_DIR = PROJECT_ROOT / "app" / "services"
API_DIR = PROJECT_ROOT / "app" / "api"

ENGINE_FILE = SERVICES_DIR / "mission_engine.py"
EXECUTOR_FILE = SERVICES_DIR / "executor_registry.py"
GOALS_FILE = API_DIR / "goals_router.py"

for path in [ENGINE_FILE, EXECUTOR_FILE, GOALS_FILE]:
    if path.exists():
        shutil.copy2(path, BACKUP_DIR / path.name)

executor_code = '''from __future__ import annotations
import app.core.bootstrap_utf8  # noqa: F401

from datetime import datetime
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent.parent
ARTIFACTS_DIR = BASE_DIR / "artifacts"
OUTPUT_DIR = ARTIFACTS_DIR / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def utc_now_iso() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


class ExecutorRegistry:
    def execute(self, task: dict[str, Any]) -> dict[str, Any]:
        task_type = task.get("type", "unknown")
        params = task.get("params", {}) or {}
        objective = str(params.get("objective", "")).strip()

        if task_type == "file_write_stub":
            out_file = OUTPUT_DIR / "stage1_file_write_stub.txt"
            out_file.write_text(
                f"Stub file execution for objective:\\n{objective}\\n",
                encoding="utf-8",
            )
            return {
                "task_id": task.get("task_id"),
                "status": "completed",
                "message": "Stub file write completed",
                "output": {
                    "path": str(out_file),
                    "objective": objective,
                    "executed_at": utc_now_iso(),
                },
            }

        if task_type == "repair_stub":
            return {
                "task_id": task.get("task_id"),
                "status": "completed",
                "message": "Repair stub completed",
                "output": {
                    "objective": objective,
                    "executed_at": utc_now_iso(),
                },
            }

        if task_type == "analysis_stub":
            return {
                "task_id": task.get("task_id"),
                "status": "completed",
                "message": "Analysis stub completed",
                "output": {
                    "objective": objective,
                    "executed_at": utc_now_iso(),
                },
            }

        return {
            "task_id": task.get("task_id"),
            "status": "failed",
            "message": f"Unsupported task type: {task_type}",
            "output": {
                "executed_at": utc_now_iso(),
            },
        }
'''

engine_code = '''from __future__ import annotations
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
DB_LOCK = Lock()


def utc_now_iso() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def empty_db() -> dict[str, Any]:
    return {"goals": {}, "missions": {}}


def load_db() -> dict[str, Any]:
    if not DB_FILE.exists():
        return empty_db()

    try:
        data = json.loads(DB_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return empty_db()
        data.setdefault("goals", {})
        data.setdefault("missions", {})
        return data
    except Exception:
        broken_file = STATE_DIR / f"goals_db_broken_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.json"
        try:
            broken_file.write_text(DB_FILE.read_text(encoding="utf-8", errors="ignore"), encoding="utf-8")
        except Exception:
            pass
        return empty_db()


def save_db(data: dict[str, Any]) -> None:
    tmp_file = DB_FILE.with_suffix(".tmp")
    tmp_file.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp_file.replace(DB_FILE)


def append_log(mission_id: str, line: str) -> None:
    log_file = LOGS_DIR / f"{mission_id}.log"
    with log_file.open("a", encoding="utf-8") as f:
        f.write(f"{utc_now_iso()} | {line}\\n")


class MissionEngine:
    def __init__(self) -> None:
        self.executor = ExecutorRegistry()

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

        append_log(mission_id, f"Goal created for objective: {objective}")

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
            return db.get("missions", {}).get(mission_id)

    def run_mission(self, mission_id: str) -> dict[str, Any]:
        with DB_LOCK:
            db = load_db()
            mission = db.get("missions", {}).get(mission_id)
            if not mission:
                raise KeyError("Mission not found")

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
            mission = db.get("missions", {}).get(mission_id)
            if not mission:
                raise KeyError("Mission not found after execution")

            mission["status"] = final_status
            mission["completed_at"] = utc_now_iso()
            mission["task_results"] = task_results
            mission["last_error"] = last_error
            db["missions"][mission_id] = mission

            goal_id = mission.get("goal_id")
            if goal_id and goal_id in db.get("goals", {}):
                db["goals"][goal_id]["status"] = final_status

            save_db(db)

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
'''

goals_code = '''from __future__ import annotations
import app.core.bootstrap_utf8  # noqa: F401

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services.mission_engine import MissionEngine

router = APIRouter(prefix="/api", tags=["goals"])

engine = MissionEngine()


class GoalCreateRequest(BaseModel):
    objective: str = Field(min_length=1)
    constraints: dict[str, Any] = Field(default_factory=dict)


@router.post("/goals")
def create_goal(payload: GoalCreateRequest) -> dict[str, Any]:
    try:
        return engine.create_goal(payload.objective, payload.constraints)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"create_goal_failed: {exc}")


@router.get("/missions")
def list_missions() -> dict[str, Any]:
    missions = engine.list_missions()
    return {
        "count": len(missions),
        "items": missions,
    }


@router.get("/missions/{mission_id}")
def get_mission(mission_id: str) -> dict[str, Any]:
    mission = engine.get_mission(mission_id)
    if not mission:
        raise HTTPException(status_code=404, detail="Mission not found")
    return mission


@router.post("/missions/{mission_id}/run")
def run_mission(mission_id: str) -> dict[str, Any]:
    try:
        return engine.run_mission(mission_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Mission not found")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"run_mission_failed: {exc}")


@router.get("/missions/{mission_id}/logs")
def get_logs(mission_id: str) -> dict[str, Any]:
    return {
        "mission_id": mission_id,
        "logs": engine.get_logs(mission_id),
    }
'''

EXECUTOR_FILE.write_text(executor_code, encoding="utf-8")
ENGINE_FILE.write_text(engine_code, encoding="utf-8")
GOALS_FILE.write_text(goals_code, encoding="utf-8")

print(f"[OK] Updated: {EXECUTOR_FILE}")
print(f"[OK] Updated: {ENGINE_FILE}")
print(f"[OK] Updated: {GOALS_FILE}")
print(f"[OK] Backups: {BACKUP_DIR}")
