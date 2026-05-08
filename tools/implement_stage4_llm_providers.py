from __future__ import annotations

from pathlib import Path
from datetime import datetime
import shutil

PROJECT_ROOT = Path.cwd()
BACKUP_DIR = PROJECT_ROOT / ("backup_stage4_llm_integration_" + datetime.now().strftime("%Y%m%d_%H%M%S"))
BACKUP_DIR.mkdir(parents=True, exist_ok=True)

APP_DIR = PROJECT_ROOT / "app"
SERVICES_DIR = APP_DIR / "services"

BRAIN_FILE = SERVICES_DIR / "conversation_brain.py"
BOT_FILE = APP_DIR / "telegram_bot.py"
ENV_FILE = PROJECT_ROOT / ".env"

for path in [BRAIN_FILE, BOT_FILE]:
    if path.exists():
        shutil.copy2(path, BACKUP_DIR / path.name)

brain_code = '''from __future__ import annotations
import app.core.bootstrap_utf8  # noqa: F401

import os
from typing import Any

import requests

from app.services.conversation_memory import ConversationMemory

memory = ConversationMemory()

LLM_MODE = os.getenv("LLM_MODE", "auto").strip().lower()
OLLAMA_BASE_URL = os.getenv("LLM_BASE_URL", "http://127.0.0.1:11434").strip()
OLLAMA_MODEL = os.getenv("LLM_MODEL", "llama3.2:latest").strip()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip()


def looks_like_task(text: str) -> bool:
    t = text.lower()
    markers = (
        "создай", "сделай", "запусти", "напиши", "подключи", "исправь", "настрой",
        "реализуй", "улучши", "добавь", "create", "make", "run", "write",
        "fix", "configure", "implement", "improve", "add"
    )
    return any(m in t for m in markers)


def looks_like_status(text: str) -> bool:
    t = text.lower()
    markers = (
        "что с миссией", "статус", "status", "logs", "логи", "лог",
        "mission", "что по миссии", "покажи миссию"
    )
    return any(m in t for m in markers)


def looks_like_question(text: str) -> bool:
    t = text.lower().strip()
    if "?" in text:
        return True
    q_markers = (
        "как", "почему", "зачем", "что", "когда", "где", "кто",
        "можешь", "сможешь", "расскажи", "объясни",
        "how", "why", "what", "when", "where", "who", "can you", "explain"
    )
    return any(t.startswith(marker) or f" {marker} " in t for marker in q_markers)


def build_context_block() -> str:
    mem = memory.get()
    lines = []
    if mem.get("last_goal_id"):
        lines.append(f"last_goal_id={mem['last_goal_id']}")
    if mem.get("last_mission_id"):
        lines.append(f"last_mission_id={mem['last_mission_id']}")
    if mem.get("last_objective"):
        lines.append(f"last_objective={mem['last_objective']}")
    if mem.get("last_intent"):
        lines.append(f"last_intent={mem['last_intent']}")
    history = mem.get("history", [])[-5:]
    for idx, item in enumerate(history, start=1):
        lines.append(f"history_{idx}_user={item.get('user_text')}")
        lines.append(f"history_{idx}_reply={item.get('reply')}")
    return "\\n".join(lines)


def local_fallback_reply(text: str) -> str:
    t = text.lower().strip()

    if "что ты умеешь" in t or "что ты сейчас умеешь" in t or "на что ты способен" in t:
        return (
            "Сейчас я умею работать как Jarvis Supervisor: создавать goal, вести mission lifecycle, "
            "запускать mission, показывать логи, помнить контекст диалога и отвечать в conversational режиме. "
            "Если активен Ollama или OpenAI, мои ответы становятся глубже и естественнее."
        )

    if "на каком мы этапе" in t or "какой следующий этап" in t:
        return (
            "Сейчас у нас уже собраны supervisor lifecycle, context memory и conversational layer. "
            "Текущий этап — подключение реального LLM brain через Ollama/OpenAI."
        )

    if "что это даст" in t:
        return (
            "Это даст более свободное и естественное общение: я смогу лучше понимать вопросы, "
            "объяснять архитектуру, помогать с планированием и вести диалог не только через заранее заданные правила."
        )

    if "как успехи" in t:
        return (
            "Сейчас базовый supervisor уже работает. Дальше самое важное — перевести conversational brain "
            "с fallback-режима на реальный LLM через Ollama или OpenAI."
        )

    if "что улучшать дальше" in t:
        return (
            "Дальше лучше усиливать planner, execution logic, LLM routing и auto-repair. "
            "Но первым делом — подключить реальный brain, чтобы общение стало свободнее."
        )

    if "сможешь" in t or "можешь" in t:
        return (
            "Да, смогу. Основа уже собрана, и следующий шаг — перевести conversational layer "
            "на реальные LLM-провайдеры."
        )

    return (
        "Я понял твой вопрос. Сейчас у меня есть fallback-brain. Если активен Ollama или OpenAI, "
        "я отвечаю глубже и естественнее."
    )


def ollama_available() -> bool:
    try:
        r = requests.get(f"{OLLAMA_BASE_URL.rstrip('/')}/api/tags", timeout=5)
        return r.status_code == 200
    except Exception:
        return False


def openai_available() -> bool:
    return bool(OPENAI_API_KEY)


def get_provider_status() -> dict[str, Any]:
    return {
        "llm_mode": LLM_MODE,
        "ollama": {
            "base_url": OLLAMA_BASE_URL,
            "model": OLLAMA_MODEL,
            "available": ollama_available(),
        },
        "openai": {
            "model": OPENAI_MODEL,
            "available": openai_available(),
            "api_key_configured": bool(OPENAI_API_KEY),
        },
    }


def try_ollama_reply(text: str) -> str | None:
    try:
        prompt = (
            "Ты Jarvis V3 Supervisor Assistant. Отвечай по-русски, естественно, полезно и по делу. "
            "Ты совмещаешь роль AI-ассистента и supervisor-системы.\\n\\n"
            f"Контекст:\\n{build_context_block()}\\n\\n"
            f"Сообщение пользователя:\\n{text}"
        )
        response = requests.post(
            f"{OLLAMA_BASE_URL.rstrip('/')}/api/generate",
            json={
                "model": OLLAMA_MODEL,
                "prompt": prompt,
                "stream": False,
            },
            timeout=60,
        )
        if response.status_code != 200:
            return None
        data = response.json()
        answer = str(data.get("response", "")).strip()
        if not answer:
            return None
        return answer
    except Exception:
        return None


def try_openai_reply(text: str) -> str | None:
    if not OPENAI_API_KEY:
        return None

    try:
        response = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": OPENAI_MODEL,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "Ты Jarvis V3 Supervisor Assistant. Отвечай по-русски, "
                            "естественно, полезно, связно и инженерно-практично."
                        ),
                    },
                    {
                        "role": "user",
                        "content": f"Контекст:\\n{build_context_block()}\\n\\nСообщение пользователя:\\n{text}",
                    },
                ],
                "temperature": 0.4,
            },
            timeout=60,
        )
        if response.status_code != 200:
            return None
        data = response.json()
        choices = data.get("choices", [])
        if not choices:
            return None
        answer = choices[0].get("message", {}).get("content", "")
        answer = str(answer).strip()
        if not answer:
            return None
        return answer
    except Exception:
        return None


def classify_intent(text: str) -> str:
    if looks_like_task(text):
        return "task"
    if looks_like_status(text):
        return "status"
    if looks_like_question(text):
        return "conversation"
    return "conversation"


def generate_reply(text: str) -> dict[str, Any]:
    intent = classify_intent(text)

    if intent == "task":
        return {
            "reply": "Похоже на задачу. Такой текст лучше передать в supervisor goal-routing.",
            "intent": "task",
            "mode": "router",
            "source": "local_router",
            "confidence": 0.9,
        }

    if intent == "status":
        return {
            "reply": "Похоже на запрос статуса или логов. Такой текст лучше направить в mission/status-routing.",
            "intent": "status",
            "mode": "router",
            "source": "local_router",
            "confidence": 0.9,
        }

    answer = None
    source = "local_fallback"

    if LLM_MODE == "ollama":
        answer = try_ollama_reply(text)
        source = "ollama" if answer else "local_fallback"

    elif LLM_MODE == "openai":
        answer = try_openai_reply(text)
        source = "openai" if answer else "local_fallback"

    elif LLM_MODE == "auto":
        if ollama_available():
            answer = try_ollama_reply(text)
            if answer:
                source = "ollama"
        if not answer and openai_available():
            answer = try_openai_reply(text)
            if answer:
                source = "openai"
        if not answer:
            source = "local_fallback"

    else:
        source = "local_fallback"

    if not answer:
        answer = local_fallback_reply(text)

    return {
        "reply": answer,
        "intent": "conversation",
        "mode": "conversation",
        "source": source,
        "confidence": 0.9 if source in ("ollama", "openai") else 0.72,
    }
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
from app.services.conversation_brain import get_provider_status

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


def ask_responder(user_text: str):
    return post("/api/respond", {"text": user_text}, retries=2, timeout=60)


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
        "/forget\\n"
        "/brain\\n"
        "/providers\\n\\n"
        "Я также умею свободно общаться и понимать follow-up сообщения."
    )


@bot.message_handler(commands=["brain", "providers"])
def providers_cmd(msg):
    status = get_provider_status()
    reply = (
        "LLM providers\\n"
        f"llm_mode: {status['llm_mode']}\\n"
        f"ollama_available: {status['ollama']['available']}\\n"
        f"ollama_model: {status['ollama']['model']}\\n"
        f"ollama_base_url: {status['ollama']['base_url']}\\n"
        f"openai_available: {status['openai']['available']}\\n"
        f"openai_model: {status['openai']['model']}\\n"
        f"openai_key_configured: {status['openai']['api_key_configured']}"
    )
    bot.reply_to(msg, reply)


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

    current_memory = memory.get()
    has_context_mission = bool(current_memory.get("last_mission_id") or load_last_mission())

    if looks_like_logs_request(text_lower) and (looks_like_followup_reference(text_lower) or has_context_mission):
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

    if looks_like_run_request(text_lower) and (looks_like_followup_reference(text_lower) or has_context_mission):
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

    if looks_like_status_request(text_lower) and (looks_like_followup_reference(text_lower) or has_context_mission):
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

    status, data = ask_responder(user_text)
    if status == 200 and isinstance(data, dict):
        reply = str(data.get("reply", "Нет ответа."))
        intent = str(data.get("intent", "conversation"))
        source = str(data.get("source", "unknown"))
        memory.append_history(user_text=user_text, reply=reply, intent=intent)
        memory.update_fields(last_user_text=user_text, last_reply=reply, last_intent=intent)
        bot.reply_to(msg, reply)
        LOG.info("Conversational reply source=%s intent=%s", source, intent)
        return

    reply = "Не удалось получить conversational ответ."
    memory.append_history(user_text=user_text, reply=reply, intent="conversation_error")
    bot.reply_to(msg, reply)


LOG.info("Bot started")

while True:
    try:
        bot.infinity_polling(timeout=30, long_polling_timeout=30, skip_pending=True)
    except Exception as e:
        LOG.error("Polling crashed: %s", e)
        time.sleep(3)
'''

# update env defaults if missing
env_lines = []
if ENV_FILE.exists():
    env_lines = ENV_FILE.read_text(encoding="utf-8").splitlines()

needed = {
    "LLM_MODE": "auto",
    "LLM_BASE_URL": "http://127.0.0.1:11434",
    "LLM_MODEL": "llama3.2:latest",
    "OPENAI_MODEL": "gpt-4o-mini",
}
existing_keys = set()
for line in env_lines:
    if "=" in line and not line.strip().startswith("#"):
        existing_keys.add(line.split("=", 1)[0].strip())

for key, value in needed.items():
    if key not in existing_keys:
        env_lines.append(f"{key}={value}")

if "OPENAI_API_KEY" not in existing_keys:
    env_lines.append("OPENAI_API_KEY=")

ENV_FILE.write_text("\\n".join(env_lines).rstrip() + "\\n", encoding="utf-8")

BRAIN_FILE.write_text(brain_code, encoding="utf-8")
BOT_FILE.write_text(bot_code, encoding="utf-8")

print(f"[OK] Updated: {BRAIN_FILE}")
print(f"[OK] Updated: {BOT_FILE}")
print(f"[OK] Updated env defaults in: {ENV_FILE}")
print(f"[OK] Backups: {BACKUP_DIR}")
