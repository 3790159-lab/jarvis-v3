from __future__ import annotations

from pathlib import Path
from datetime import datetime
import shutil

PROJECT_ROOT = Path.cwd()
BACKUP_DIR = PROJECT_ROOT / ("backup_stage1_stable_core_" + datetime.now().strftime("%Y%m%d_%H%M%S"))
BACKUP_DIR.mkdir(parents=True, exist_ok=True)

APP_DIR = PROJECT_ROOT / "app"
API_DIR = APP_DIR / "api"
SERVICES_DIR = APP_DIR / "services"
STATE_DIR = PROJECT_ROOT / "state"
ARTIFACTS_DIR = PROJECT_ROOT / "artifacts"
LOGS_DIR = ARTIFACTS_DIR / "logs"

for d in [API_DIR, SERVICES_DIR, STATE_DIR, ARTIFACTS_DIR, LOGS_DIR]:
    d.mkdir(parents=True, exist_ok=True)

MAIN_FILE = APP_DIR / "main.py"
BOT_FILE = APP_DIR / "telegram_bot.py"
GOALS_FILE = API_DIR / "goals_router.py"
PLANNER_FILE = SERVICES_DIR / "planner.py"
EXECUTOR_FILE = SERVICES_DIR / "executor_registry.py"
ENGINE_FILE = SERVICES_DIR / "mission_engine.py"

for path in [MAIN_FILE, BOT_FILE, GOALS_FILE, PLANNER_FILE, EXECUTOR_FILE, ENGINE_FILE]:
    if path.exists():
        shutil.copy2(path, BACKUP_DIR / path.name)

planner_code = r'''from __future__ import annotations
import app.core.bootstrap_utf8  # noqa: F401

from typing import Any


def plan_task(objective: str) -> list[dict[str, Any]]:
    text = (objective or "").strip()
    normalized = text.lower()

    tasks: list[dict[str, Any]] = []

    if not text:
        return tasks

    if "создай файл" in normalized or "create file" in normalized or "write file" in normalized:
        tasks.append({
            "task_id": "task_write_file",
            "title": "Write file",
            "type": "file_write_stub",
            "status": "planned",
            "params": {
                "objective": text,
            },
        })
        return tasks

    if "исправь" in normalized or "fix" in normalized:
        tasks.append({
            "task_id": "task_repair_stub",
            "title": "Repair stub",
            "type": "repair_stub",
            "status": "planned",
            "params": {
                "objective": text,
            },
        })
        return tasks

    tasks.append({
        "task_id": "task_analysis_stub",
        "title": "Analyze objective",
        "type": "analysis_stub",
        "status": "planned",
        "params": {
            "objective": text,
        },
    })
    return tasks
'''

executor_code = r'''from __future__ import annotations
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
                newline="\\n",
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

engine_code = r'''from __future__ import annotations
import app.core.bootstrap_utf8  # noqa: F401

from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Any
import json

from app.services.executor_registry import ExecutorRegistry
from app.services.planner import plan_task


BASE_DIR = Path(__file__).resolve().parent.parent.parent
STATE_DIR = BASE_DIR / "state"
STATE_DIR.mkdir(parents=True, exist_ok=True)

DB_FILE = STATE_DIR / "goals_db.json"
LOGS_DIR = BASE_DIR / "artifacts" / "logs"
LOGS_DIR.mkdir(parents=True, exist_ok=True)

DB_LOCK = Lock()


def utc_now_iso() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def load_db() -> dict[str, Any]:
    if not DB_FILE.exists():
        return {"goals": {}, "missions": {}}
    try:
        return json.loads(DB_FILE.read_text(encoding="utf-8"))
    except Exception:
        broken = DB_FILE.with_suffix(".broken.json")
        try:
            DB_FILE.replace(broken)
        except Exception:
            pass
        return {"goals": {}, "missions": {}}


def save_db(data: dict[str, Any]) -> None:
    DB_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
        newline="\\n",
    )


def append_log(mission_id: str, line: str) -> None:
    log_file = LOGS_DIR / f"{mission_id}.log"
    with log_file.open("a", encoding="utf-8", newline="\\n") as f:
        f.write(f"{utc_now_iso()} | {line}\\n")


class MissionEngine:
    def __init__(self) -> None:
        self.executor = ExecutorRegistry()

    def create_goal(self, objective: str, constraints: dict | None = None) -> dict[str, Any]:
        from uuid import uuid4

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
            db.setdefault("goals", {})[goal_id] = goal_record
            db.setdefault("missions", {})[mission_id] = mission_record
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

            current_status = mission.get("status")
            if current_status == "completed":
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

goals_router_code = r'''from __future__ import annotations
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


@router.get("/missions/{mission_id}/logs")
def get_logs(mission_id: str) -> dict[str, Any]:
    return {
        "mission_id": mission_id,
        "logs": engine.get_logs(mission_id),
    }
'''

bot_code = r'''from __future__ import annotations
import app.core.bootstrap_utf8  # noqa: F401
from app.core.text_utils import clean_text, write_text_utf8

import logging
import os
import time
from pathlib import Path
from typing import Optional, Any

import requests
import telebot
from dotenv import load_dotenv

load_dotenv()

LOG = logging.getLogger("jarvis")
if not LOG.handlers:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

BASE_DIR = Path(__file__).resolve().parent.parent
ARTIFACTS_DIR = BASE_DIR / "jarvis_stage3_artifacts" / "generated_projects" / "managed_autonomous_api" / "artifacts" / "output"
ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
API_BASE_URL = os.getenv("API_BASE_URL", "http://127.0.0.1:8010").strip()
ALLOWED_CHAT_ID_RAW = os.getenv("TELEGRAM_ALLOWED_CHAT_ID", "").strip()
POLL_INTERVAL = float(os.getenv("TELEGRAM_POLL_INTERVAL_SECONDS", "2").strip() or "2")

ALLOWED_CHAT_ID: Optional[int]
if ALLOWED_CHAT_ID_RAW:
    try:
        ALLOWED_CHAT_ID = int(ALLOWED_CHAT_ID_RAW)
    except ValueError:
        ALLOWED_CHAT_ID = None
        LOG.warning("Invalid TELEGRAM_ALLOWED_CHAT_ID value: %s", ALLOWED_CHAT_ID_RAW)
else:
    ALLOWED_CHAT_ID = None

if not TELEGRAM_BOT_TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN is empty in .env")

bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN, parse_mode=None)

LOG.info("Telegram bot initialized")
LOG.info("API base URL: %s", API_BASE_URL)
LOG.info("Allowed chat id configured: %s", "yes" if ALLOWED_CHAT_ID is not None else "no")


def is_allowed(message) -> bool:
    if ALLOWED_CHAT_ID is None:
        return True
    return int(message.chat.id) == ALLOWED_CHAT_ID


def ensure_allowed(message) -> bool:
    if is_allowed(message):
        return True
    try:
        bot.reply_to(message, "Этот чат не авторизован для работы с ботом.")
    except Exception:
        pass
    LOG.warning("Rejected message from unauthorized chat id=%s", getattr(message.chat, "id", None))
    return False


def save_debug_message(prefix: str, text: str) -> None:
    try:
        line = f"{prefix}: {clean_text(text)}\n"
        write_text_utf8(ARTIFACTS_DIR / "telegram_task_output.txt", line)
    except Exception as exc:
        LOG.warning("Failed to write debug message: %s", exc)


def post_json(path: str, payload: dict, timeout: int = 20, retries: int = 2) -> tuple[int, dict | str]:
    url = f"{API_BASE_URL.rstrip('/')}{path}"
    last_error: Exception | None = None

    for attempt in range(retries + 1):
        try:
            response = requests.post(url, json=payload, timeout=timeout)
            try:
                data = response.json()
            except Exception:
                data = response.text
            return response.status_code, data
        except Exception as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(1.0 * (attempt + 1))
            else:
                raise last_error


def get_json(path: str, timeout: int = 10, retries: int = 2) -> tuple[int, dict | str]:
    url = f"{API_BASE_URL.rstrip('/')}{path}"
    last_error: Exception | None = None

    for attempt in range(retries + 1):
        try:
            response = requests.get(url, timeout=timeout)
            try:
                data = response.json()
            except Exception:
                data = response.text
            return response.status_code, data
        except Exception as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(1.0 * (attempt + 1))
            else:
                raise last_error


def format_health(data: dict | str) -> str:
    if isinstance(data, dict):
        lines = [
            "Backend OK",
            f"service: {data.get('service', '-')}",
            f"status: {data.get('status', '-')}",
            f"pid: {data.get('pid', '-')}",
        ]
        return "\n".join(lines)
    return f"Backend response: {data}"


def format_goal_response(data: dict | str, status_code: int = 200) -> str:
    if isinstance(data, dict):
        if status_code not in (200, 201):
            if data.get("detail"):
                return f"Goal error: {status_code}\n{data['detail']}"
            return f"Goal error: {status_code}\n{data}"

        lines = ["Goal created"]
        if data.get("goal_id"):
            lines.append(f"goal_id: {data['goal_id']}")
        if data.get("mission_id"):
            lines.append(f"mission_id: {data['mission_id']}")
        if data.get("summary"):
            lines.append(f"summary: {data['summary']}")
        return "\n".join(lines)
    return f"Goal response: {data}"


def format_run_response(data: dict | str) -> str:
    if isinstance(data, dict):
        lines = ["Mission result"]
        for key in ("mission_id", "status", "summary"):
            if data.get(key):
                lines.append(f"{key}: {data[key]}")
        return "\n".join(lines)
    return f"Run response: {data}"


def format_respond_response(data: dict | str) -> str:
    if isinstance(data, dict):
        for key in ("reply", "message", "text", "response", "answer"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return str(data)
    if isinstance(data, str) and data.strip():
        return data.strip()
    return "Нет ответа от системы."


def format_mission_item(item: dict[str, Any]) -> str:
    return f"{item.get('mission_id', '-')} | {item.get('status', '-')} | {item.get('objective', '-')}"


def ask_backend_to_respond(user_text: str) -> tuple[str, dict | str]:
    payload_variants = [
        {"text": user_text},
        {"message": user_text},
        {"user_text": user_text},
        {"input": user_text},
    ]

    last_error = None
    for payload in payload_variants:
        try:
            status, data = post_json("/api/respond", payload, timeout=20, retries=2)
            if status == 200:
                return format_respond_response(data), data
            last_error = f"/api/respond returned {status}: {data}"
        except Exception as exc:
            last_error = str(exc)

    return f"Не удалось получить ответ от backend: {last_error}", {}


def create_goal_raw(objective: str) -> tuple[dict | str, int]:
    payload_variants = [
        {"objective": objective},
        {"objective": objective, "constraints": {}},
    ]

    last_data: dict | str = {}
    last_status = 0

    for payload in payload_variants:
        try:
            status, data = post_json("/api/goals", payload, timeout=25, retries=2)
            last_status, last_data = status, data
            if status in (200, 201):
                return data, status
        except Exception as exc:
            last_data = str(exc)

    return last_data, last_status


def create_goal(objective: str) -> str:
    data, status = create_goal_raw(objective)
    return format_goal_response(data, status)


def try_run_mission_from_goal_data(data: dict | str) -> str | None:
    if not isinstance(data, dict):
        return None

    mission_id = data.get("mission_id")
    if not mission_id:
        return None

    try:
        status, run_data = post_json(f"/api/missions/{mission_id}/run", {}, timeout=60, retries=1)
        if status in (200, 201):
            return format_run_response(run_data)
        return f"Mission auto-run error: {status}\n{run_data}"
    except Exception as exc:
        return f"Mission auto-run exception: {exc}"


def looks_like_task(text: str) -> bool:
    normalized = text.strip().lower()
    task_markers = (
        "создай", "сделай", "запусти", "напиши", "подключи", "исправь", "настрой",
        "реализуй", "улучши", "добавь", "create", "make", "run", "write",
        "fix", "configure", "implement", "improve", "add"
    )
    return any(marker in normalized for marker in task_markers)


@bot.message_handler(commands=["start", "help"])
def handle_start(message):
    if not ensure_allowed(message):
        return

    text = (
        "Jarvis V3 online.\n\n"
        "Команды:\n"
        "/health\n"
        "/goal <цель>\n"
        "/jarvis <задача>\n"
        "/missions\n"
        "/mission <mission_id>\n"
        "/run <mission_id>\n"
        "/logs <mission_id>\n\n"
        "Я также умею обрабатывать обычные сообщения."
    )
    bot.reply_to(message, text)


@bot.message_handler(commands=["health"])
def handle_health(message):
    if not ensure_allowed(message):
        return

    try:
        status, data = get_json("/health", timeout=10, retries=2)
        if status == 200:
            bot.reply_to(message, format_health(data))
        else:
            bot.reply_to(message, f"Backend health error: {status}\n{data}")
    except Exception as exc:
        bot.reply_to(message, f"Backend health exception: {exc}")


@bot.message_handler(commands=["goal"])
def handle_goal(message):
    if not ensure_allowed(message):
        return

    raw = clean_text(message.text or "")
    text = raw.replace("/goal", "", 1).strip()

    if not text:
        bot.reply_to(message, "Напиши цель после /goal")
        return

    save_debug_message("Telegram goal received", text)
    bot.reply_to(message, create_goal(text))


@bot.message_handler(commands=["missions"])
def handle_missions(message):
    if not ensure_allowed(message):
        return

    try:
        status, data = get_json("/api/missions", timeout=15, retries=2)
        if status != 200 or not isinstance(data, dict):
            bot.reply_to(message, f"Ошибка списка missions: {status}\n{data}")
            return

        items = data.get("items", [])
        if not items:
            bot.reply_to(message, "Missions пока нет.")
            return

        lines = ["Последние missions:"]
        for item in items[:10]:
            lines.append(format_mission_item(item))
        bot.reply_to(message, "\n".join(lines))
    except Exception as exc:
        bot.reply_to(message, f"Mission list exception: {exc}")


@bot.message_handler(commands=["mission"])
def handle_mission(message):
    if not ensure_allowed(message):
        return

    raw = clean_text(message.text or "")
    mission_id = raw.replace("/mission", "", 1).strip()

    if not mission_id:
        bot.reply_to(message, "Напиши mission_id после /mission")
        return

    try:
        status, data = get_json(f"/api/missions/{mission_id}", timeout=15, retries=2)
        if status != 200:
            bot.reply_to(message, f"Mission error: {status}\n{data}")
            return
        bot.reply_to(message, str(data))
    except Exception as exc:
        bot.reply_to(message, f"Mission exception: {exc}")


@bot.message_handler(commands=["run"])
def handle_run(message):
    if not ensure_allowed(message):
        return

    raw = clean_text(message.text or "")
    mission_id = raw.replace("/run", "", 1).strip()

    if not mission_id:
        bot.reply_to(message, "Напиши mission_id после /run")
        return

    try:
        status, data = post_json(f"/api/missions/{mission_id}/run", {}, timeout=60, retries=1)
        if status not in (200, 201):
            bot.reply_to(message, f"Run error: {status}\n{data}")
            return
        bot.reply_to(message, format_run_response(data))
    except Exception as exc:
        bot.reply_to(message, f"Run exception: {exc}")


@bot.message_handler(commands=["logs"])
def handle_logs(message):
    if not ensure_allowed(message):
        return

    raw = clean_text(message.text or "")
    mission_id = raw.replace("/logs", "", 1).strip()

    if not mission_id:
        bot.reply_to(message, "Напиши mission_id после /logs")
        return

    try:
        status, data = get_json(f"/api/missions/{mission_id}/logs", timeout=20, retries=2)
        if status != 200 or not isinstance(data, dict):
            bot.reply_to(message, f"Logs error: {status}\n{data}")
            return
        logs = str(data.get("logs", ""))
        if not logs.strip():
            logs = "No logs found."
        bot.reply_to(message, logs[:3500])
    except Exception as exc:
        bot.reply_to(message, f"Logs exception: {exc}")


@bot.message_handler(commands=["jarvis"])
def handle_jarvis(message):
    if not ensure_allowed(message):
        return

    raw = clean_text(message.text or "")
    text = raw.replace("/jarvis", "", 1).strip()

    if not text:
        bot.reply_to(message, "Напиши задачу после /jarvis")
        return

    save_debug_message("Telegram task received", text)
    reply, data = ask_backend_to_respond(text)

    if isinstance(data, dict) and data.get("intent") in ("task", "task_auto_run"):
        goal_data, goal_status = create_goal_raw(text)
        if goal_status not in (200, 201):
            bot.reply_to(message, format_goal_response(goal_data, goal_status))
            return

        if data.get("intent") == "task_auto_run":
            run_reply = try_run_mission_from_goal_data(goal_data)
            if run_reply:
                bot.reply_to(message, run_reply)
                return

        bot.reply_to(message, format_goal_response(goal_data, goal_status))
        return

    bot.reply_to(message, reply)


@bot.message_handler(func=lambda message: bool(getattr(message, "text", None)))
def handle_plain_text(message):
    if not ensure_allowed(message):
        return

    text = clean_text(message.text or "").strip()
    if not text:
        bot.reply_to(message, "Пустое сообщение.")
        return

    if text.startswith("/"):
        bot.reply_to(message, "Неизвестная команда. Используй /help")
        return

    save_debug_message("Telegram plain text received", text)

    if looks_like_task(text):
        goal_data, goal_status = create_goal_raw(text)
        if goal_status not in (200, 201):
            bot.reply_to(message, format_goal_response(goal_data, goal_status))
            return

        if "запусти" in text.lower() or "run" in text.lower() or "выполни" in text.lower():
            run_reply = try_run_mission_from_goal_data(goal_data)
            if run_reply:
                bot.reply_to(message, run_reply)
                return

        bot.reply_to(message, format_goal_response(goal_data, goal_status))
        return

    reply, data = ask_backend_to_respond(text)

    if isinstance(data, dict):
        intent = data.get("intent")
        if intent == "task":
            goal_data, goal_status = create_goal_raw(text)
            bot.reply_to(message, format_goal_response(goal_data, goal_status))
            return
        if intent == "task_auto_run":
            goal_data, goal_status = create_goal_raw(text)
            if goal_status not in (200, 201):
                bot.reply_to(message, format_goal_response(goal_data, goal_status))
                return
            run_reply = try_run_mission_from_goal_data(goal_data)
            if run_reply:
                bot.reply_to(message, run_reply)
                return

    bot.reply_to(message, reply)


def run_polling_forever() -> None:
    retry_delay = 2
    max_delay = 30

    while True:
        try:
            LOG.info("Starting Telegram polling loop")
            bot.infinity_polling(
                timeout=30,
                long_polling_timeout=30,
                skip_pending=True,
            )
        except Exception as exc:
            LOG.exception("Polling crashed: %s", exc)
            time.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, max_delay)
        else:
            retry_delay = 2
            time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    run_polling_forever()
'''

GOALS_FILE.write_text(goals_router_code, encoding="utf-8", newline="\n")
PLANNER_FILE.write_text(planner_code, encoding="utf-8", newline="\n")
EXECUTOR_FILE.write_text(executor_code, encoding="utf-8", newline="\n")
ENGINE_FILE.write_text(engine_code, encoding="utf-8", newline="\n")

main_text = MAIN_FILE.read_text(encoding="utf-8")

if "from app.api.goals_router import router as goals_router" not in main_text:
    fastapi_import = "from fastapi import FastAPI"
    replacement = fastapi_import + "\n\nfrom app.api.goals_router import router as goals_router"
    main_text = main_text.replace(fastapi_import, replacement)

if "app.include_router(goals_router)" not in main_text:
    if "app.include_router(responses_router)" in main_text:
        main_text = main_text.replace("app.include_router(responses_router)", "app.include_router(responses_router)\napp.include_router(goals_router)")
    elif "app.include_router(missions_router)" in main_text:
        main_text = main_text.replace("app.include_router(missions_router)", "app.include_router(missions_router)\napp.include_router(goals_router)")
    else:
        main_text += "\napp.include_router(goals_router)\n"

MAIN_FILE.write_text(main_text, encoding="utf-8", newline="\n")
BOT_FILE.write_text(bot_code, encoding="utf-8", newline="\n")

print(f"[OK] Updated: {PLANNER_FILE}")
print(f"[OK] Updated: {EXECUTOR_FILE}")
print(f"[OK] Updated: {ENGINE_FILE}")
print(f"[OK] Updated: {GOALS_FILE}")
print(f"[OK] Updated: {MAIN_FILE}")
print(f"[OK] Updated: {BOT_FILE}")
print(f"[OK] Backups: {BACKUP_DIR}")
