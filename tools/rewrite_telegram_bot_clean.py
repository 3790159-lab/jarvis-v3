from __future__ import annotations

from pathlib import Path
from datetime import datetime
import shutil

PROJECT_ROOT = Path.cwd()
APP_DIR = PROJECT_ROOT / "app"
BOT_FILE = APP_DIR / "telegram_bot.py"
BACKUP_FILE = PROJECT_ROOT / ("backup_telegram_bot_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".py")

if BOT_FILE.exists():
    shutil.copy2(BOT_FILE, BACKUP_FILE)

code = r'''
from __future__ import annotations
import app.core.bootstrap_utf8  # noqa: F401
from app.core.text_utils import clean_text, write_text_utf8

import logging
import os
import time
from pathlib import Path
from typing import Optional

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


def post_json(path: str, payload: dict, timeout: int = 20) -> tuple[int, dict | str]:
    url = f"{API_BASE_URL.rstrip('/')}{path}"
    response = requests.post(url, json=payload, timeout=timeout)
    try:
        data = response.json()
    except Exception:
        data = response.text
    return response.status_code, data


def get_json(path: str, timeout: int = 10) -> tuple[int, dict | str]:
    url = f"{API_BASE_URL.rstrip('/')}{path}"
    response = requests.get(url, timeout=timeout)
    try:
        data = response.json()
    except Exception:
        data = response.text
    return response.status_code, data


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


def ask_backend_to_respond(user_text: str) -> str:
    payload_variants = [
        {"text": user_text},
        {"message": user_text},
        {"user_text": user_text},
        {"input": user_text},
    ]

    last_error = None
    for payload in payload_variants:
        try:
            status, data = post_json("/api/respond", payload, timeout=20)
            if status == 200:
                return format_respond_response(data)
            last_error = f"/api/respond returned {status}: {data}"
        except Exception as exc:
            last_error = str(exc)

    return f"Не удалось получить ответ от backend: {last_error}"


def create_goal(objective: str) -> str:
    payload_variants = [
        {"objective": objective},
        {"objective": objective, "constraints": {}},
    ]

    last_error = None
    for payload in payload_variants:
        try:
            status, data = post_json("/api/goals", payload, timeout=25)
            if status in (200, 201):
                return format_goal_response(data)
            last_error = f"/api/goals returned {status}: {data}"
        except Exception as exc:
            last_error = str(exc)

    return f"Не удалось создать goal: {last_error}"


@bot.message_handler(commands=["start", "help"])
def handle_start(message):
    if not ensure_allowed(message):
        return

    text = (
        "Jarvis V3 online.\n\n"
        "Я умею:\n"
        "- отвечать на обычные сообщения\n"
        "- проверять backend через /health\n"
        "- создавать цель через /goal <текст>\n"
        "- обрабатывать задачу через /jarvis <текст>\n\n"
        "Примеры:\n"
        "/health\n"
        "/goal Create test file\n"
        "/jarvis Создай файл artifacts/output/test.txt и запиши туда: hello\n"
        "Привет, что ты умеешь?"
    )
    bot.reply_to(message, text)


@bot.message_handler(commands=["health"])
def handle_health(message):
    if not ensure_allowed(message):
        return

    try:
        status, data = get_json("/health", timeout=10)
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
    reply = ask_backend_to_respond(text)
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
    reply = ask_backend_to_respond(text)
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
BOT_FILE.write_text(code, encoding="utf-8", newline="\n")
print(f"[OK] Rewrote {BOT_FILE}")
print(f"[OK] Backup saved to {BACKUP_FILE}")
