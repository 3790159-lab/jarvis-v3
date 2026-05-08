from __future__ import annotations

from pathlib import Path
from datetime import datetime
import shutil

PROJECT_ROOT = Path.cwd()
BACKUP_DIR = PROJECT_ROOT / ("backup_smart_routing_" + datetime.now().strftime("%Y%m%d_%H%M%S"))
BACKUP_DIR.mkdir(parents=True, exist_ok=True)

RESPONSES_FILE = PROJECT_ROOT / "app" / "api" / "responses.py"
BOT_FILE = PROJECT_ROOT / "app" / "telegram_bot.py"

for path in [RESPONSES_FILE, BOT_FILE]:
    if path.exists():
        shutil.copy2(path, BACKUP_DIR / path.name)

responses_code = r'''from __future__ import annotations
import app.core.bootstrap_utf8  # noqa: F401

from typing import Any

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


def simple_local_reply(text: str) -> tuple[str, str, str, float]:
    normalized = text.strip().lower()

    greetings = ("привет", "здравствуйте", "добрый день", "добрый вечер", "hello", "hi")
    if any(token in normalized for token in greetings):
        return (
            "Привет! Я Jarvis V3. Сейчас я умею проверять backend, принимать цели, обрабатывать обычные сообщения через /api/respond и работать как Telegram-интерфейс для supervisor. Дальше меня можно усилить умным роутингом, mission flow и LLM-мозгом.",
            "qa",
            "local_qa",
            0.93,
        )

    if "что ты умеешь" in normalized or "what can you do" in normalized:
        return (
            "Сейчас я умею: 1) проверять здоровье backend; 2) принимать обычные сообщения из Telegram; 3) создавать goal через /goal; 4) принимать задачи через /jarvis; 5) автоматически распознавать часть задач из обычного текста. Следующий шаг — подключить полноценный AI-routing и LLM.",
            "qa",
            "local_qa",
            0.95,
        )

    if "какие улучшения" in normalized or "улучшения" in normalized or "improve" in normalized:
        return (
            "Я бы усилил себя в трёх направлениях: 1) умный роутинг — различать вопрос, задачу и mission; 2) надёжность — retries, lock, health-restart, structured logs; 3) интеллект — подключение OpenAI или Ollama, память и многошаговое планирование.",
            "qa",
            "local_qa",
            0.91,
        )

    if "сможешь" in normalized or "можешь" in normalized or "реализуешь" in normalized or "подключишь" in normalized:
        return (
            "Да, смогу. Базовая инфраструктура уже работает. Следующий шаг — либо сразу создать goal на выполнение задачи, либо подключить более умный AI-routing для автоматической реализации.",
            "qa",
            "local_capability",
            0.9,
        )

    if normalized in {"2+2", "сколько будет 2+2", "what is 2+2"}:
        return ("2 + 2 = 4.", "qa", "local_qa", 0.99)

    task_markers = (
        "создай", "сделай", "запусти", "напиши", "подключи", "исправь", "настрой",
        "реализуй", "улучши", "добавь", "create", "make", "run", "write", "fix",
        "configure", "implement", "improve", "add"
    )
    if any(marker in normalized for marker in task_markers):
        return (
            "Похоже на задачу. Такой текст лучше автоматически превращать в goal и отправлять в supervisor на выполнение.",
            "task",
            "local_router",
            0.9,
        )

    return (
        f"Я получил сообщение: {text}\n\nСейчас /api/respond уже работает в локальном режиме. Если это задача, я могу автоматически передать её в goal-routing. Если это вопрос — отвечу как ассистент.",
        "qa",
        "local_fallback",
        0.72,
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

    reply, mode, source, confidence = simple_local_reply(text)

    intent = "qa"
    if mode == "task":
        intent = "task"

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
            status, data = post_json("/api/respond", payload, timeout=20)
            if status == 200:
                return format_respond_response(data), data
            last_error = f"/api/respond returned {status}: {data}"
        except Exception as exc:
            last_error = str(exc)

    return f"Не удалось получить ответ от backend: {last_error}", {}


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
        "- автоматически распознавать часть задач из обычного текста\n\n"
        "Примеры:\n"
        "/health\n"
        "/goal Create test file\n"
        "/jarvis Создай файл artifacts/output/test.txt и запиши туда: hello\n"
        "Привет, что ты умеешь?\n"
        "Подключи это сам"
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
    reply, _ = ask_backend_to_respond(text)
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
        reply = create_goal(text)
        bot.reply_to(message, reply)
        return

    reply, data = ask_backend_to_respond(text)

    if isinstance(data, dict) and data.get("intent") == "task":
        reply = create_goal(text)

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
