from __future__ import annotations

from pathlib import Path
from datetime import datetime
import shutil

PROJECT_ROOT = Path.cwd()
BACKUP_DIR = PROJECT_ROOT / ("backup_router_v2_" + datetime.now().strftime("%Y%m%d_%H%M%S"))
BACKUP_DIR.mkdir(parents=True, exist_ok=True)

RESPONSES_FILE = PROJECT_ROOT / "app" / "api" / "responses.py"
BOT_FILE = PROJECT_ROOT / "app" / "telegram_bot.py"

for path in [RESPONSES_FILE, BOT_FILE]:
    if path.exists():
        shutil.copy2(path, BACKUP_DIR / path.name)

responses_code = r'''from __future__ import annotations
import app.core.bootstrap_utf8  # noqa: F401

from typing import Any
import re

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/api", tags=["responses"])


class RespondRequest(BaseModel):
    text: str | None = None
    message: str | None = None
    user_text: str | None = None
    input: str | None = None


def pick_text(payload: RespondRequest) -> str:
    for value in (payload.text, payload.message, payload.user_text, payload.input):
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def normalize_text(text: str) -> str:
    normalized = text.strip().lower()
    replacements = {
        "смодешь": "сможешь",
        "сможеш": "сможешь",
        "реализувать": "реализовать",
        "подключиш": "подключишь",
        "зделай": "сделай",
        "исправьь": "исправь",
        "чтотыумеешь": "что ты умеешь",
    }
    for src, dst in replacements.items():
        normalized = normalized.replace(src, dst)

    normalized = re.sub(r"\s+", " ", normalized)
    return normalized


def contains_any(text: str, variants: tuple[str, ...]) -> bool:
    return any(v in text for v in variants)


def simple_local_reply(text: str) -> tuple[str, str, str, float, str]:
    normalized = normalize_text(text)

    if contains_any(normalized, ("привет", "здравствуйте", "добрый день", "добрый вечер", "hello", "hi")):
        return (
            "Привет! Я Jarvis V3. Сейчас я умею проверять backend, принимать цели, обрабатывать обычные сообщения через /api/respond, автоматически распознавать часть задач и работать как Telegram-интерфейс для supervisor.",
            "qa",
            "local_qa",
            0.94,
            "qa",
        )

    if contains_any(normalized, ("что ты умеешь", "на что ты способен", "что умеешь", "what can you do")):
        return (
            "Сейчас я умею: 1) отвечать на обычные сообщения; 2) проверять backend; 3) создавать goal; 4) принимать задачи через /jarvis; 5) автоматически распознавать часть task-intent из обычного текста; 6) готов к следующему этапу — auto goal + auto run + LLM routing.",
            "qa",
            "local_capabilities",
            0.96,
            "qa",
        )

    capability = contains_any(normalized, ("сможешь", "можешь", "реализуешь", "подключишь", "настроишь"))
    benefits = contains_any(normalized, ("что это даст", "что это даёт", "что это даст мне", "зачем это", "какой эффект"))

    if capability and benefits:
        return (
            "Да, смогу это реализовать. Это даст системе более умное поведение: бот сможет лучше понимать обычные сообщения, автоматически отличать вопрос от задачи, создавать goal без ручных команд и в дальнейшем переходить к auto-run и LLM-routing.",
            "qa",
            "local_combined_reasoning",
            0.95,
            "qa",
        )

    if capability:
        return (
            "Да, смогу. Базовая инфраструктура уже работает, и следующий шаг — усилить роутинг, авто-обработку задач и интеллект системы.",
            "qa",
            "local_capability",
            0.91,
            "qa",
        )

    if benefits:
        return (
            "Это даст более автономную систему: меньше ручных команд, лучшее понимание намерения пользователя, автоматическое создание задач, выше надёжность и более удобную работу через Telegram.",
            "qa",
            "local_benefits",
            0.92,
            "qa",
        )

    if contains_any(normalized, ("какие улучшения", "улучшения", "как бы ты себя улучшил", "improve")):
        return (
            "Я бы усилил себя в четырёх направлениях: 1) smart routing v2; 2) auto goal + auto run; 3) structured logs, retries, lock и health supervision; 4) LLM-мозг и память.",
            "qa",
            "local_improvements",
            0.93,
            "qa",
        )

    task_markers = (
        "создай", "сделай", "запусти", "напиши", "подключи", "исправь", "настрой",
        "реализуй", "улучши", "добавь", "create", "make", "run", "write",
        "fix", "configure", "implement", "improve", "add"
    )

    if contains_any(normalized, task_markers):
        auto_run = contains_any(normalized, ("запусти", "run", "выполни", "execute"))
        return (
            "Похоже на задачу. Такой текст лучше автоматически превращать в goal и передавать в supervisor.",
            "task",
            "local_router",
            0.91,
            "task_auto_run" if auto_run else "task",
        )

    if normalized in {"2+2", "сколько будет 2+2", "what is 2+2"}:
        return ("2 + 2 = 4.", "qa", "local_qa", 0.99, "qa")

    return (
        f"Я получил сообщение: {text}\n\nСейчас я уже умею отвечать локально и распознавать часть задач. Следующий шаг — усилить AI-routing и автоматически запускать часть целей через supervisor.",
        "qa",
        "local_fallback",
        0.75,
        "qa",
    )


@router.post("/respond")
def respond(payload: RespondRequest) -> dict[str, Any]:
    text = pick_text(payload)
    if not text:
        return {
            "reply": "Пустой запрос.",
            "mode": "qa",
            "source": "local_validation",
            "confidence": 1.0,
            "intent": "empty",
        }

    reply, mode, source, confidence, intent = simple_local_reply(text)
    return {
        "reply": reply,
        "mode": mode,
        "source": source,
        "confidence": confidence,
        "intent": intent,
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


def format_goal_response(data: dict | str) -> str:
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
        return "\n".join(lines)
    return f"Goal response: {data}"


def format_run_response(data: dict | str) -> str:
    if isinstance(data, dict):
        lines = ["Mission started"]
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
    data, _ = create_goal_raw(objective)
    return format_goal_response(data)


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
        "Я умею:\n"
        "- отвечать на обычные сообщения\n"
        "- проверять backend через /health\n"
        "- создавать goal через /goal <текст>\n"
        "- обрабатывать задачу через /jarvis <текст>\n"
        "- автоматически распознавать часть задач из обычного текста\n"
        "- для части задач автоматически создавать goal и при явной команде пробовать auto-run\n\n"
        "Примеры:\n"
        "/health\n"
        "/goal Create test file\n"
        "/jarvis Создай файл artifacts/output/test.txt и запиши туда: hello\n"
        "Привет, что ты умеешь?\n"
        "Подключи это сам\n"
        "Исправь и запусти это"
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
        goal_data, _ = create_goal_raw(text)
        if data.get("intent") == "task_auto_run":
            run_reply = try_run_mission_from_goal_data(goal_data)
            if run_reply:
                bot.reply_to(message, run_reply)
                return
        bot.reply_to(message, format_goal_response(goal_data))
        return

    bot.reply_to(message, reply)


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
    reply = create_goal(text)
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
        goal_data, _ = create_goal_raw(text)
        if "запусти" in text.lower() or "run" in text.lower() or "выполни" in text.lower():
            run_reply = try_run_mission_from_goal_data(goal_data)
            if run_reply:
                bot.reply_to(message, run_reply)
                return
        bot.reply_to(message, format_goal_response(goal_data))
        return

    reply, data = ask_backend_to_respond(text)

    if isinstance(data, dict):
        intent = data.get("intent")
        if intent == "task":
            goal_data, _ = create_goal_raw(text)
            bot.reply_to(message, format_goal_response(goal_data))
            return
        if intent == "task_auto_run":
            goal_data, _ = create_goal_raw(text)
            run_reply = try_run_mission_from_goal_data(goal_data)
            if run_reply:
                bot.reply_to(message, run_reply)
                return
            bot.reply_to(message, format_goal_response(goal_data))
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

RESPONSES_FILE.write_text(responses_code, encoding="utf-8", newline="\n")
BOT_FILE.write_text(bot_code, encoding="utf-8", newline="\n")

print(f"[OK] Updated: {RESPONSES_FILE}")
print(f"[OK] Updated: {BOT_FILE}")
print(f"[OK] Backup dir: {BACKUP_DIR}")
