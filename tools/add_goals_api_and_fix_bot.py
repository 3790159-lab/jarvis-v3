from __future__ import annotations

from pathlib import Path
from datetime import datetime
import shutil

PROJECT_ROOT = Path.cwd()
BACKUP_DIR = PROJECT_ROOT / ("backup_goals_api_fix_" + datetime.now().strftime("%Y%m%d_%H%M%S"))
BACKUP_DIR.mkdir(parents=True, exist_ok=True)

MAIN_FILE = PROJECT_ROOT / "app" / "main.py"
BOT_FILE = PROJECT_ROOT / "app" / "telegram_bot.py"
API_DIR = PROJECT_ROOT / "app" / "api"
GOALS_FILE = API_DIR / "goals_router.py"

for path in [MAIN_FILE, BOT_FILE]:
    if path.exists():
        shutil.copy2(path, BACKUP_DIR / path.name)

API_DIR.mkdir(parents=True, exist_ok=True)

goals_code = r'''from __future__ import annotations
import app.core.bootstrap_utf8  # noqa: F401

from datetime import datetime
from pathlib import Path
from threading import Lock
from uuid import uuid4
import json

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api", tags=["goals"])

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DATA_DIR = BASE_DIR / "state"
DATA_DIR.mkdir(parents=True, exist_ok=True)

GOALS_DB = DATA_DIR / "goals_db.json"
DB_LOCK = Lock()


class GoalCreateRequest(BaseModel):
    objective: str = Field(min_length=1)
    constraints: dict = Field(default_factory=dict)


def load_db() -> dict:
    if not GOALS_DB.exists():
        return {"goals": {}, "missions": {}}
    try:
        return json.loads(GOALS_DB.read_text(encoding="utf-8"))
    except Exception:
        return {"goals": {}, "missions": {}}


def save_db(data: dict) -> None:
    GOALS_DB.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
        newline="\n",
    )


def utc_now_iso() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


@router.post("/goals")
def create_goal(payload: GoalCreateRequest) -> dict:
    objective = payload.objective.strip()
    if not objective:
        raise HTTPException(status_code=400, detail="Objective is empty")

    goal_id = f"goal_{uuid4().hex[:8]}"
    mission_id = f"mission_{uuid4().hex[:8]}"

    mission_summary = (
        "Draft mission created by goals_router. "
        "Supervisor prepared initial execution stub."
    )

    goal_record = {
        "goal_id": goal_id,
        "mission_id": mission_id,
        "objective": objective,
        "constraints": payload.constraints or {},
        "created_at": utc_now_iso(),
        "status": "created",
    }

    mission_record = {
        "goal_id": goal_id,
        "mission_id": mission_id,
        "objective": objective,
        "constraints": payload.constraints or {},
        "mission_summary": mission_summary,
        "status": "draft",
        "created_at": utc_now_iso(),
        "task_results": [],
    }

    with DB_LOCK:
        db = load_db()
        db.setdefault("goals", {})[goal_id] = goal_record
        db.setdefault("missions", {})[mission_id] = mission_record
        save_db(db)

    return {
        "goal_id": goal_id,
        "mission_id": mission_id,
        "summary": mission_summary,
        "status": "created",
    }


@router.get("/missions/{mission_id}")
def get_mission(mission_id: str) -> dict:
    with DB_LOCK:
        db = load_db()
        mission = db.get("missions", {}).get(mission_id)

    if not mission:
        raise HTTPException(status_code=404, detail="Mission not found")

    return mission


@router.post("/missions/{mission_id}/run")
def run_mission(mission_id: str) -> dict:
    with DB_LOCK:
        db = load_db()
        mission = db.get("missions", {}).get(mission_id)

        if not mission:
            raise HTTPException(status_code=404, detail="Mission not found")

        mission["status"] = "completed"
        mission["completed_at"] = utc_now_iso()
        mission["task_results"] = [
            {
                "task_id": "task_auto_001",
                "title": "Auto-run placeholder task",
                "status": "completed",
                "message": "Mission auto-run stub completed successfully",
            }
        ]
        db["missions"][mission_id] = mission
        save_db(db)

    return {
        "mission_id": mission_id,
        "status": "completed",
        "summary": "Mission auto-run stub completed successfully",
        "task_results": mission["task_results"],
    }
'''
GOALS_FILE.write_text(goals_code, encoding="utf-8", newline="\n")

main_text = MAIN_FILE.read_text(encoding="utf-8")

if "from app.api.goals_router import router as goals_router" not in main_text:
    fastapi_import = "from fastapi import FastAPI"
    replacement = fastapi_import + "\n\nfrom app.api.goals_router import router as goals_router"
    main_text = main_text.replace(fastapi_import, replacement)

if "app.include_router(goals_router)" not in main_text:
    marker = "app.include_router(responses_router)"
    if marker in main_text:
        main_text = main_text.replace(marker, marker + "\napp.include_router(goals_router)")
    else:
        main_text += "\napp.include_router(goals_router)\n"

MAIN_FILE.write_text(main_text, encoding="utf-8", newline="\n")

bot_text = BOT_FILE.read_text(encoding="utf-8")

old_format_goal = """def format_goal_response(data: dict | str) -> str:
    if isinstance(data, dict):
        lines = ["Goal created"]
        if data.get("goal_id"):
            lines.append(f"goal_id: {data['goal_id']}")
        if data.get("mission_id"):
            lines.append(f"mission_id: {data['mission_id']}")
        if data.get("summary"):
            lines.append(f"summary: {data['summary']}")
        elif data.get("mission_summary"):
            lines.append(f"summary: {data['mission_summary']}")
        return "\\n".join(lines)
    return f"Goal response: {data}"
"""

new_format_goal = """def format_goal_response(data: dict | str, status_code: int = 200) -> str:
    if isinstance(data, dict):
        if status_code not in (200, 201):
            if data.get("detail"):
                return f"Goal error: {status_code}\\n{data['detail']}"
            return f"Goal error: {status_code}\\n{data}"

        lines = ["Goal created"]
        if data.get("goal_id"):
            lines.append(f"goal_id: {data['goal_id']}")
        if data.get("mission_id"):
            lines.append(f"mission_id: {data['mission_id']}")
        if data.get("summary"):
            lines.append(f"summary: {data['summary']}")
        elif data.get("mission_summary"):
            lines.append(f"summary: {data['mission_summary']}")
        return "\\n".join(lines)
    return f"Goal response: {data}"
"""

if old_format_goal in bot_text:
    bot_text = bot_text.replace(old_format_goal, new_format_goal)

old_create_goal = """def create_goal(objective: str) -> str:
    data, _ = create_goal_raw(objective)
    return format_goal_response(data)
"""
new_create_goal = """def create_goal(objective: str) -> str:
    data, status = create_goal_raw(objective)
    return format_goal_response(data, status)
"""
if old_create_goal in bot_text:
    bot_text = bot_text.replace(old_create_goal, new_create_goal)

old_plain_1 = '        goal_data, _ = create_goal_raw(text)\n        if "запусти" in text.lower() or "run" in text.lower() or "выполни" in text.lower():'
new_plain_1 = '        goal_data, goal_status = create_goal_raw(text)\n        if goal_status not in (200, 201):\n            bot.reply_to(message, format_goal_response(goal_data, goal_status))\n            return\n        if "запусти" in text.lower() or "run" in text.lower() or "выполни" in text.lower():'
if old_plain_1 in bot_text:
    bot_text = bot_text.replace(old_plain_1, new_plain_1)

old_plain_2 = '        bot.reply_to(message, format_goal_response(goal_data))'
new_plain_2 = '        bot.reply_to(message, format_goal_response(goal_data, goal_status))'
if old_plain_2 in bot_text:
    bot_text = bot_text.replace(old_plain_2, new_plain_2, 1)

old_task_route = '            goal_data, _ = create_goal_raw(text)\n            bot.reply_to(message, format_goal_response(goal_data))\n            return'
new_task_route = '            goal_data, goal_status = create_goal_raw(text)\n            bot.reply_to(message, format_goal_response(goal_data, goal_status))\n            return'
if old_task_route in bot_text:
    bot_text = bot_text.replace(old_task_route, new_task_route)

old_autorun = '            goal_data, _ = create_goal_raw(text)\n            run_reply = try_run_mission_from_goal_data(goal_data)'
new_autorun = '            goal_data, goal_status = create_goal_raw(text)\n            if goal_status not in (200, 201):\n                bot.reply_to(message, format_goal_response(goal_data, goal_status))\n                return\n            run_reply = try_run_mission_from_goal_data(goal_data)'
if old_autorun in bot_text:
    bot_text = bot_text.replace(old_autorun, new_autorun)

BOT_FILE.write_text(bot_text, encoding="utf-8", newline="\n")

print(f"[OK] Created: {GOALS_FILE}")
print(f"[OK] Updated: {MAIN_FILE}")
print(f"[OK] Updated: {BOT_FILE}")
print(f"[OK] Backups: {BACKUP_DIR}")
