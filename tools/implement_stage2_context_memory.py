from __future__ import annotations

from pathlib import Path
from datetime import datetime
import shutil

PROJECT_ROOT = Path.cwd()
BACKUP_DIR = PROJECT_ROOT / ("backup_stage2_context_memory_" + datetime.now().strftime("%Y%m%d_%H%M%S"))
BACKUP_DIR.mkdir(parents=True, exist_ok=True)

APP_DIR = PROJECT_ROOT / "app"
SERVICES_DIR = APP_DIR / "services"

BOT_FILE = APP_DIR / "telegram_bot.py"
MEMORY_FILE = SERVICES_DIR / "conversation_memory.py"

for path in [BOT_FILE, MEMORY_FILE]:
    if path.exists():
        shutil.copy2(path, BACKUP_DIR / path.name)

SERVICES_DIR.mkdir(parents=True, exist_ok=True)

memory_code = '''from __future__ import annotations
import app.core.bootstrap_utf8  # noqa: F401

from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Any
import json

BASE_DIR = Path(__file__).resolve().parent.parent.parent
STATE_DIR = BASE_DIR / "state"
STATE_DIR.mkdir(parents=True, exist_ok=True)

MEMORY_FILE = STATE_DIR / "conversation_memory.json"
LOCK = Lock()


def utc_now_iso() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def default_memory() -> dict[str, Any]:
    return {
        "last_goal_id": None,
        "last_mission_id": None,
        "last_objective": None,
        "last_user_text": None,
        "last_reply": None,
        "last_intent": None,
        "history": [],
        "updated_at": utc_now_iso(),
    }


def load_memory() -> dict[str, Any]:
    if not MEMORY_FILE.exists():
        return default_memory()

    try:
        data = json.loads(MEMORY_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return default_memory()
        base = default_memory()
        base.update(data)
        if not isinstance(base.get("history"), list):
            base["history"] = []
        return base
    except Exception:
        broken = MEMORY_FILE.with_name(f"conversation_memory_broken_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.json")
        try:
            broken.write_text(MEMORY_FILE.read_text(encoding="utf-8", errors="ignore"), encoding="utf-8")
        except Exception:
            pass
        return default_memory()


def save_memory(data: dict[str, Any]) -> None:
    data["updated_at"] = utc_now_iso()
    tmp = MEMORY_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(MEMORY_FILE)


class ConversationMemory:
    def get(self) -> dict[str, Any]:
        with LOCK:
            return load_memory()

    def clear(self) -> dict[str, Any]:
        with LOCK:
            data = default_memory()
            save_memory(data)
            return data

    def update_fields(self, **kwargs: Any) -> dict[str, Any]:
        with LOCK:
            data = load_memory()
            for key, value in kwargs.items():
                data[key] = value
            save_memory(data)
            return data

    def append_history(
        self,
        user_text: str | None = None,
        reply: str | None = None,
        intent: str | None = None,
        goal_id: str | None = None,
        mission_id: str | None = None,
    ) -> dict[str, Any]:
        with LOCK:
            data = load_memory()
            history = data.get("history", [])
            history.append({
                "timestamp": utc_now_iso(),
                "user_text": user_text,
                "reply": reply,
                "intent": intent,
                "goal_id": goal_id,
                "mission_id": mission_id,
            })
            data["history"] = history[-20:]
            if user_text is not None:
                data["last_user_text"] = user_text
            if reply is not None:
                data["last_reply"] = reply
            if intent is not None:
                data["last_intent"] = intent
            if goal_id is not None:
                data["last_goal_id"] = goal_id
            if mission_id is not None:
                data["last_mission_id"] = mission_id
            save_memory(data)
            return data
'''

bot_code = '''from __future__ import annotations
import app.core.bootstrap_utf8  # noqa: F401

import os
import time
import logging
from pathlib import Path
from typing import Optional, Any

import requests
import telebot
from dotenv import load_dotenv

from app.services.conversation_memory import ConversationMemory

load_dotenv()

logging.basicConfig(level=logging.INFO)
LOG = logging.getLogger("jarvis")

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
API_BASE = os.getenv("API_BASE_URL", "http://127.0.0.1:8010").strip()

if not TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN is empty in .env")

bot = telebot.TeleBot(TOKEN)
memory = ConversationMemory()

STATE_DIR = Path("state")
STATE_DIR.mkdir(exist_ok=True)

LAST_MISSION_FILE = STATE_DIR / "last_mission.txt"


def save_last_mission(mid: str):
    try:
        LAST_MISSION_FILE.write_text(mid, encoding="utf-8")
    except Exception:
        pass


def load_last_mission() -> Optional[str]:
    try:
        if LAST_MISSION_FILE.exists():
            return LAST_MISSION_FILE.read_text(encoding="utf-8").strip()
    except Exception:
        pass
    return None


def normalize_text(text: str) -> str:
    normalized = (text or "").strip()
    return " ".join(normalized.split())


def post(path: str, payload: dict[str, Any], retries: int = 2, timeout: int = 20):
    last_error = None
    for attempt in range(retries + 1):
        try:
            r = requests.post(API_BASE + path, json=payload, timeout=timeout)
            try:
                return r.status_code, r.json()
            except Exception:
                return r.status_code, r.text
        except Exception as e:
            last_error = e
            if attempt < retries:
                time.sleep(1 + attempt)
            else:
                return 500, str(last_error)


def get(path: str, retries: int = 2, timeout: int = 20):
    last_error = None
    for attempt in range(retries + 1):
        try:
            r = requests.get(API_BASE + path, timeout=timeout)
            try:
                return r.status_code, r.json()
            except Exception:
                return r.status_code, r.text
        except Exception as e:
            last_error = e
            if attempt < retries:
                time.sleep(1 + attempt)
            else:
                return 500, str(last_error)


def format_goal_created(data: dict[str, Any]) -> str:
    lines = ["Goal created"]
    if data.get("goal_id"):
        lines.append(f"goal_id: {data['goal_id']}")
    if data.get("mission_id"):
        lines.append(f"mission_id: {data['mission_id']}")
    if data.get("summary"):
        lines.append(f"summary: {data['summary']}")
    return "\\n".join(lines)


def format_mission_info(data: dict[str, Any]) -> str:
    lines = ["Mission info"]
    for key in ("mission_id", "goal_id", "status", "objective", "mission_summary", "attempt_count", "last_error"):
        value = data.get(key)
        if value not in (None, "", []):
            lines.append(f"{key}: {value}")
    tasks = data.get("tasks", [])
    if isinstance(tasks, list):
        lines.append(f"tasks: {len(tasks)}")
    results = data.get("task_results", [])
    if isinstance(results, list):
        lines.append(f"task_results: {len(results)}")
    return "\\n".join(lines)


def format_run_result(data: dict[str, Any]) -> str:
    lines = ["Mission result"]
    for key in ("mission_id", "status", "summary"):
        value = data.get(key)
        if value not in (None, ""):
            lines.append(f"{key}: {value}")
    return "\\n".join(lines)


def looks_like_task(text: str) -> bool:
    t = text.lower()
    markers = (
        "создай", "сделай", "запусти", "напиши", "подключи", "исправь", "настрой",
        "реализуй", "улучши", "добавь", "create", "make", "run", "write",
        "fix", "configure", "implement", "improve", "add"
    )
    return any(m in t for m in markers)


def looks_like_run_request(text: str) -> bool:
    t = text.lower()
    markers = ("запусти", "выполни", "run", "execute", "стартуй", "запускай")
    return any(m in t for m in markers)


def looks_like_logs_request(text: str) -> bool:
    t = text.lower()
    markers = ("логи", "лог", "logs", "log", "покажи логи", "show logs")
    return any(m in t for m in markers)


def looks_like_status_request(text: str) -> bool:
    t = text.lower()
    markers = ("что с миссией", "статус", "status", "mission", "что по миссии", "покажи миссию")
    return any(m in t for m in markers)


def looks_like_followup_reference(text: str) -> bool:
    t = text.lower()
    refs = ("это", "её", "ее", "эту", "эту миссию", "прошлую", "предыдущую", "теперь")
    return any(r in t for r in refs)


def create_goal_from_text(text: str):
    status, data = post("/api/goals", {"objective": text}, retries=2, timeout=25)
    if status != 200 or not isinstance(data, dict):
        return status, data, None, None

    goal_id = data.get("goal_id")
    mission_id = data.get("mission_id")

    if mission_id:
        save_last_mission(mission_id)

    memory.append_history(
        user_text=text,
        reply=format_goal_created(data),
        intent="goal_create",
        goal_id=goal_id,
        mission_id=mission_id,
    )

    return status, data, goal_id, mission_id


def resolve_mission_id(raw_text: str) -> Optional[str]:
    raw_text = normalize_text(raw_text)
    if raw_text and "<" not in raw_text and raw_text.startswith("mission_"):
        return raw_text

    if raw_text:
        parts = raw_text.split()
        for part in parts:
            if part.startswith("mission_"):
                return part

    mem = memory.get()
    if mem.get("last_mission_id"):
        return mem["last_mission_id"]

    return load_last_mission()


def run_mission_by_id(mission_id: str):
    status, data = post(f"/api/missions/{mission_id}/run", {}, retries=1, timeout=60)
    return status, data


def get_mission_by_id(mission_id: str):
    status, data = get(f"/api/missions/{mission_id}", retries=2, timeout=20)
    return status, data


def get_logs_by_id(mission_id: str):
    status, data = get(f"/api/missions/{mission_id}/logs", retries=2, timeout=20)
    return status, data


def get_last_messages_summary() -> str:
    mem = memory.get()
    lines = ["Context memory"]
    for key in ("last_goal_id", "last_mission_id", "last_objective", "last_user_text", "last_reply", "last_intent", "updated_at"):
        value = mem.get(key)
        if value not in (None, "", []):
            lines.append(f"{key}: {value}")
    history = mem.get("history", [])
    lines.append(f"history_items: {len(history)}")
    return "\\n".join(lines)


@bot.message_handler(commands=["start", "help"])
def start(msg):
    bot.reply_to(
        msg,
        "Jarvis V3 online.\\n\\n"
        "Команды:\\n"
        "/goal <цель>\\n"
        "/missions\\n"
        "/mission [mission_id]\\n"
        "/run [mission_id]\\n"
        "/logs [mission_id]\\n"
        "/context\\n"
        "/forget\\n\\n"
        "Я также понимаю follow-up сообщения вроде:\\n"
        "- запусти теперь\\n"
        "- покажи логи\\n"
        "- что с миссией\\n"
        "- сделай это"
    )


@bot.message_handler(commands=["context"])
def context_cmd(msg):
    bot.reply_to(msg, get_last_messages_summary())


@bot.message_handler(commands=["forget"])
def forget_cmd(msg):
    memory.clear()
    try:
        if LAST_MISSION_FILE.exists():
            LAST_MISSION_FILE.unlink()
    except Exception:
        pass
    bot.reply_to(msg, "Контекст очищен.")


@bot.message_handler(commands=["health"])
def health(msg):
    status, data = get("/health", retries=2, timeout=10)
    if status != 200:
        bot.reply_to(msg, f"Health error: {status}\\n{data}")
        return

    if isinstance(data, dict):
        lines = [
            "Backend OK",
            f"service: {data.get('service', '-')}",
            f"status: {data.get('status', '-')}",
            f"pid: {data.get('pid', '-')}",
        ]
        reply = "\\n".join(lines)
    else:
        reply = str(data)

    memory.append_history(user_text="/health", reply=reply, intent="health")
    bot.reply_to(msg, reply)


@bot.message_handler(commands=["goal"])
def goal(msg):
    text = normalize_text(msg.text.replace("/goal", "", 1))
    if not text:
        bot.reply_to(msg, "Напиши цель после /goal")
        return

    status, data, goal_id, mission_id = create_goal_from_text(text)

    if status != 200 or not isinstance(data, dict):
        reply = f"Goal error: {status}\\n{data}"
        memory.append_history(user_text=text, reply=reply, intent="goal_error")
        bot.reply_to(msg, reply)
        return

    memory.update_fields(
        last_goal_id=goal_id,
        last_mission_id=mission_id,
        last_objective=text,
        last_intent="goal_create",
    )
    bot.reply_to(msg, format_goal_created(data))


@bot.message_handler(commands=["missions"])
def missions(msg):
    status, data = get("/api/missions", retries=2, timeout=20)

    if status != 200 or not isinstance(data, dict):
        reply = f"Ошибка получения missions\\n{status}\\n{data}"
        memory.append_history(user_text="/missions", reply=reply, intent="missions_error")
        bot.reply_to(msg, reply)
        return

    items = data.get("items", [])
    if not items:
        reply = "Missions пока нет."
        memory.append_history(user_text="/missions", reply=reply, intent="missions_empty")
        bot.reply_to(msg, reply)
        return

    lines = ["Последние missions:"]
    for item in items[:10]:
        lines.append(f"{item.get('mission_id')} | {item.get('status')} | {item.get('objective')}")
    reply = "\\n".join(lines)
    memory.append_history(user_text="/missions", reply=reply, intent="missions_list")
    bot.reply_to(msg, reply)


@bot.message_handler(commands=["mission"])
def mission(msg):
    raw = normalize_text(msg.text.replace("/mission", "", 1))
    mission_id = resolve_mission_id(raw)

    if not mission_id:
        bot.reply_to(msg, "Нет mission для просмотра.")
        return

    status, data = get_mission_by_id(mission_id)

    if status != 200 or not isinstance(data, dict):
        reply = f"Mission error: {status}\\n{data}"
        memory.append_history(user_text=f"/mission {mission_id}", reply=reply, intent="mission_error", mission_id=mission_id)
        bot.reply_to(msg, reply)
        return

    reply = format_mission_info(data)
    memory.update_fields(
        last_goal_id=data.get("goal_id"),
        last_mission_id=data.get("mission_id"),
        last_objective=data.get("objective"),
        last_intent="mission_info",
    )
    memory.append_history(user_text=f"/mission {mission_id}", reply=reply, intent="mission_info", goal_id=data.get("goal_id"), mission_id=data.get("mission_id"))
    bot.reply_to(msg, reply)


@bot.message_handler(commands=["run"])
def run(msg):
    raw = normalize_text(msg.text.replace("/run", "", 1))
    mission_id = resolve_mission_id(raw)

    if not mission_id:
        bot.reply_to(msg, "Нет mission для запуска.")
        return

    status, data = run_mission_by_id(mission_id)

    if status != 200 or not isinstance(data, dict):
        reply = f"Run error: {status}\\n{data}"
        memory.append_history(user_text=f"/run {mission_id}", reply=reply, intent="run_error", mission_id=mission_id)
        bot.reply_to(msg, reply)
        return

    reply = format_run_result(data)
    memory.update_fields(last_mission_id=mission_id, last_intent="mission_run")
    memory.append_history(user_text=f"/run {mission_id}", reply=reply, intent="mission_run", mission_id=mission_id)
    bot.reply_to(msg, reply)


@bot.message_handler(commands=["logs"])
def logs(msg):
    raw = normalize_text(msg.text.replace("/logs", "", 1))
    mission_id = resolve_mission_id(raw)

    if not mission_id:
        bot.reply_to(msg, "Нет mission для логов.")
        return

    status, data = get_logs_by_id(mission_id)

    if status != 200 or not isinstance(data, dict):
        reply = f"Logs error: {status}\\n{data}"
        memory.append_history(user_text=f"/logs {mission_id}", reply=reply, intent="logs_error", mission_id=mission_id)
        bot.reply_to(msg, reply)
        return

    reply = str(data.get("logs", "No logs found."))
    if not reply.strip():
        reply = "No logs found."

    memory.update_fields(last_mission_id=mission_id, last_intent="mission_logs")
    memory.append_history(user_text=f"/logs {mission_id}", reply=reply[:1000], intent="mission_logs", mission_id=mission_id)
    bot.reply_to(msg, reply[:3500])


@bot.message_handler(func=lambda m: True)
def all_msg(msg):
    user_text = normalize_text(msg.text)
    text_lower = user_text.lower()

    if not user_text:
        bot.reply_to(msg, "Пустое сообщение.")
        return

    if user_text.startswith("/"):
        bot.reply_to(msg, "Неизвестная команда. Используй /help")
        return

    if looks_like_logs_request(text_lower) and looks_like_followup_reference(text_lower):
        mission_id = resolve_mission_id("")
        if not mission_id:
            bot.reply_to(msg, "Не понимаю, для какой mission показать логи.")
            return
        status, data = get_logs_by_id(mission_id)
        if status == 200 and isinstance(data, dict):
            reply = str(data.get("logs", "No logs found."))[:3500]
        else:
            reply = f"Logs error: {status}\\n{data}"
        memory.append_history(user_text=user_text, reply=reply[:1000], intent="followup_logs", mission_id=mission_id)
        bot.reply_to(msg, reply)
        return

    if looks_like_run_request(text_lower) and looks_like_followup_reference(text_lower):
        mission_id = resolve_mission_id("")
        if not mission_id:
            bot.reply_to(msg, "Не понимаю, какую mission запускать.")
            return
        status, data = run_mission_by_id(mission_id)
        if status == 200 and isinstance(data, dict):
            reply = format_run_result(data)
        else:
            reply = f"Run error: {status}\\n{data}"
        memory.append_history(user_text=user_text, reply=reply, intent="followup_run", mission_id=mission_id)
        bot.reply_to(msg, reply)
        return

    if looks_like_status_request(text_lower) and looks_like_followup_reference(text_lower):
        mission_id = resolve_mission_id("")
        if not mission_id:
            bot.reply_to(msg, "Не понимаю, о какой mission речь.")
            return
        status, data = get_mission_by_id(mission_id)
        if status == 200 and isinstance(data, dict):
            reply = format_mission_info(data)
        else:
            reply = f"Mission error: {status}\\n{data}"
        memory.append_history(user_text=user_text, reply=reply, intent="followup_status", mission_id=mission_id)
        bot.reply_to(msg, reply)
        return

    if looks_like_task(text_lower):
        status, data, goal_id, mission_id = create_goal_from_text(user_text)
        if status != 200 or not isinstance(data, dict):
            reply = f"Goal error: {status}\\n{data}"
            memory.append_history(user_text=user_text, reply=reply, intent="goal_error")
            bot.reply_to(msg, reply)
            return

        memory.update_fields(
            last_goal_id=goal_id,
            last_mission_id=mission_id,
            last_objective=user_text,
            last_intent="goal_create",
        )
        bot.reply_to(msg, format_goal_created(data))
        return

    reply = (
        "Я понял сообщение. Сейчас я умею: создавать goal, запускать mission, "
        "показывать статус и логи, а также понимать follow-up команды вроде "
        "'запусти теперь' и 'покажи логи'."
    )
    memory.append_history(user_text=user_text, reply=reply, intent="generic_reply")
    bot.reply_to(msg, reply)


LOG.info("Bot started")

while True:
    try:
        bot.infinity_polling(timeout=30, long_polling_timeout=30, skip_pending=True)
    except Exception as e:
        LOG.error("Polling crashed: %s", e)
        time.sleep(3)
'''

MEMORY_FILE.write_text(memory_code, encoding="utf-8")
BOT_FILE.write_text(bot_code, encoding="utf-8")

print(f"[OK] Updated: {MEMORY_FILE}")
print(f"[OK] Updated: {BOT_FILE}")
print(f"[OK] Backups: {BACKUP_DIR}")
