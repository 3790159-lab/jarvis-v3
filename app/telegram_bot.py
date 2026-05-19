import logging
import os
import time
from typing import Any

import requests
import telebot
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

APP_HOST = os.getenv("APP_HOST", "127.0.0.1").strip() or "127.0.0.1"
APP_PORT = os.getenv("APP_PORT", "8010").strip() or "8010"
BACKEND_URL = os.getenv("TELEGRAM_BACKEND_URL", f"http://{APP_HOST}:{APP_PORT}").rstrip("/")

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
ALLOWED_CHAT_ID = os.getenv("TELEGRAM_ALLOWED_CHAT_ID", "").strip()
POLL_INTERVAL = float(os.getenv("TELEGRAM_POLL_INTERVAL_SECONDS", "3"))
BACKEND_WAIT = int(os.getenv("TELEGRAM_WAIT_FOR_COMPLETION_SECONDS", "60"))

if not BOT_TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN is empty in .env")

session = requests.Session()
retries = Retry(
    total=2,
    connect=2,
    read=2,
    backoff_factor=1,
    status_forcelist=[429, 500, 502, 503, 504],
    allowed_methods=["GET", "POST"]
)
adapter = HTTPAdapter(max_retries=retries)
session.mount("http://", adapter)
session.mount("https://", adapter)

bot = telebot.TeleBot(BOT_TOKEN, parse_mode=None)

def is_allowed(chat_id: Any) -> bool:
    if not ALLOWED_CHAT_ID:
        return True
    return str(chat_id).strip() == ALLOWED_CHAT_ID

def safe_get_json(resp: requests.Response) -> Any:
    try:
        return resp.json()
    except Exception:
        return {"raw_text": resp.text}

def extract_reply_text(data: Any) -> str:
    if data is None:
        return ""

    if isinstance(data, str):
        return data.strip()

    if isinstance(data, dict):
        for key in ("reply", "response", "answer", "text", "message", "content"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

        reply_obj = data.get("reply")
        if isinstance(reply_obj, dict):
            for key in ("text", "message", "content"):
                value = reply_obj.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()

        data_obj = data.get("data")
        if isinstance(data_obj, dict):
            for key in ("reply", "response", "answer", "text", "message", "content"):
                value = data_obj.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()

    return ""

def backend_health() -> str:
    url = f"{BACKEND_URL}/health"
    r = session.get(url, timeout=(10, 20))
    r.raise_for_status()
    data = safe_get_json(r)

    if isinstance(data, dict):
        status = data.get("status", "unknown")
        service = data.get("service", "unknown")
        return f"Backend OK\nstatus: {status}\nservice: {service}"

    return f"Backend OK\n{data}"

def ask_backend(user_text: str, chat_id: Any) -> str:
    payload = {
        "text": user_text,
        "chat_id": str(chat_id),
        "source": "telegram"
    }

    url = f"{BACKEND_URL}/api/respond"
    r = session.post(url, json=payload, timeout=(10, BACKEND_WAIT))
    r.raise_for_status()

    data = safe_get_json(r)
    reply = extract_reply_text(data)

    if reply:
        return reply

    return f"Backend ответил, но текст не найден.\nСырой ответ:\n{data}"

@bot.message_handler(commands=["start"])
def handle_start(message):
    if not is_allowed(message.chat.id):
        bot.reply_to(message, "Этот chat id не разрешён.")
        return

    text = (
        "Jarvis V3 online.\n\n"
        "Команды:\n"
        "/id — показать chat id\n"
        "/health — проверить backend\n\n"
        "Можно писать обычные сообщения."
    )
    bot.reply_to(message, text)

@bot.message_handler(commands=["id"])
def handle_id(message):
    bot.reply_to(message, f"chat_id: {message.chat.id}")

@bot.message_handler(commands=["health"])
def handle_health(message):
    if not is_allowed(message.chat.id):
        bot.reply_to(message, "Этот chat id не разрешён.")
        return

    try:
        text = backend_health()
        bot.reply_to(message, text)
    except Exception as e:
        logging.exception("Health check failed")
        bot.reply_to(message, f"Health check failed:\n{type(e).__name__}: {e}")

@bot.message_handler(func=lambda m: True, content_types=["text"])
def handle_text(message):
    if not is_allowed(message.chat.id):
        bot.reply_to(message, "Этот chat id не разрешён.")
        return

    user_text = (message.text or "").strip()

    google_reply = _handle_google_commands(user_text)
    if google_reply is not None:
        bot.reply_to(message, google_reply)
        return
    if not user_text:
        bot.reply_to(message, "Пустое сообщение.")
        return

    try:
        bot.send_chat_action(message.chat.id, "typing")
    except Exception:
        pass

    try:
        reply = ask_backend(user_text, message.chat.id)
        bot.reply_to(message, reply)
    except requests.exceptions.ReadTimeout:
        logging.exception("Backend timeout")
        bot.reply_to(
            message,
            "Backend слишком долго отвечает. Проверь /health и endpoint /api/respond."
        )
    except requests.exceptions.RequestException as e:
        logging.exception("Request to backend failed")
        bot.reply_to(
            message,
            f"Ошибка запроса к backend:\n{type(e).__name__}: {e}"
        )
    except Exception as e:
        logging.exception("Unexpected conversational error")
        bot.reply_to(
            message,
            f"Не удалось получить conversational ответ.\n{type(e).__name__}: {e}"
        )

def main():
    logging.info("Starting Telegram bot")
    logging.info("BACKEND_URL=%s", BACKEND_URL)

    while True:
        try:
            bot.infinity_polling(
                timeout=20,
                long_polling_timeout=20,
                skip_pending=True
            )
        except requests.exceptions.ReadTimeout:
            logging.warning("Telegram long polling timeout. Reconnecting...")
            time.sleep(max(POLL_INTERVAL, 2))
        except KeyboardInterrupt:
            logging.info("Bot stopped by user")
            break
        except Exception as e:
            logging.exception("Bot polling crashed: %s", e)
            time.sleep(max(POLL_INTERVAL, 5))




def _call_google_execute(action: str, payload: dict) -> dict:
    backend_url = os.getenv("TELEGRAM_BACKEND_URL", "http://127.0.0.1:8010").rstrip("/")
    url = f"{backend_url}/api/google/execute"

    response = requests.post(
        url,
        json={"action": action, "payload": payload},
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def _format_gmail_messages(result: dict) -> str:
    if not result.get("ok"):
        return f"Google Gmail error: {result.get('error', 'unknown error')}"

    messages = result.get("messages") or []
    if not messages:
        return "Последние письма не найдены."

    lines = ["Последние письма:"]
    for item in messages[:5]:
        lines.append(
            f"- {item.get('subject', '(без темы)')}\n"
            f"  От: {item.get('from', '-')}\n"
            f"  Дата: {item.get('date', '-')}"
        )
    return "\n".join(lines)


def _format_calendar_events(result: dict) -> str:
    if not result.get("ok"):
        return f"Google Calendar error: {result.get('error', 'unknown error')}"

    events = result.get("events") or []
    if not events:
        return "Ближайшие события не найдены."

    lines = ["Ближайшие события:"]
    for item in events[:5]:
        start = item.get("start", {})
        start_value = start.get("dateTime") or start.get("date") or "-"
        lines.append(
            f"- {item.get('summary', '(без названия)')}\n"
            f"  Начало: {start_value}\n"
            f"  Локация: {item.get('location', '-')}"
        )
    return "\n".join(lines)
def _handle_google_commands(text: str):
    text = (text or "").strip()

    if text.startswith("/gmail"):
        try:
            result = _call_google_execute(
                "gmail_list_messages",
                {"query": "", "max_results": 5}
            )
            return _format_gmail_messages(result)
        except Exception as e:
            return f"Ошибка Gmail: {str(e)}"

    if text.startswith("/calendar"):
        from datetime import datetime, timedelta, timezone

        try:
            now = datetime.now(timezone.utc).isoformat()
            later = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()

            result = _call_google_execute(
                "calendar_list_events",
                {
                    "time_min": now,
                    "time_max": later,
                    "max_results": 5
                }
            )
            return _format_calendar_events(result)
        except Exception as e:
            return f"Ошибка Calendar: {str(e)}"

    if text.startswith("/addevent"):
        from datetime import datetime, timedelta

        try:
            start = datetime.now().replace(microsecond=0) + timedelta(hours=1)
            end = start + timedelta(minutes=30)

            result = _call_google_execute(
                "calendar_create_event",
                {
                    "summary": "Event from Telegram",
                    "start_iso": start.isoformat(),
                    "end_iso": end.isoformat(),
                    "description": text,
                    "location": "Online"
                }
            )

            if result.get("ok"):
                return f"Событие создано:\n{result.get('html_link')}"
            return f"Ошибка создания события: {result}"
        except Exception as e:
            return f"Ошибка создания события: {str(e)}"

    return None

if __name__ == "__main__":
    main()





