from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional

import requests

MODULE_ID = "telegram_sms_responder_v1"
MODULE_VERSION = "1.1.0"
MODULE_DIR = Path(__file__).resolve().parent
CONFIG_DIR = MODULE_DIR / "config"
ENV_FILES = [CONFIG_DIR / "module.local.env", CONFIG_DIR / "module.env"]

def load_env_files() -> None:
    for path in ENV_FILES:
        if not path.exists():
            continue
        for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()
            if key and key not in os.environ:
                os.environ[key] = value

load_env_files()

def env(name: str, default: str = "") -> str:
    return os.getenv(name, default)

def get_token() -> str:
    token = env("TELEGRAM_BOT_TOKEN", "").strip()
    return "" if token.startswith("PUT_REAL") else token

def get_allowed_chat() -> str:
    chat = env("TELEGRAM_ALLOWED_CHAT_ID", "").strip()
    return "" if chat.startswith("PUT_REAL") else chat

def supervisor_url() -> str:
    base = env("SUPERVISOR_BASE_URL", "http://127.0.0.1:8015").rstrip("/")
    path = env("SUPERVISOR_RESPONSE_PATH", "/api/respond")
    return f"{base}{path}"

def extract_reply(body: Any) -> str:
    if isinstance(body, str):
        return body.strip()
    if isinstance(body, dict):
        for key in ["response", "reply", "message", "text", "output", "body"]:
            value = body.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
            if isinstance(value, dict):
                nested = extract_reply(value)
                if nested:
                    return nested
        try:
            return json.dumps(body, ensure_ascii=False)
        except Exception:
            return str(body)
    if isinstance(body, list):
        try:
            return json.dumps(body, ensure_ascii=False)
        except Exception:
            return str(body)
    return str(body)

def ask_supervisor(text: str) -> str:
    url = supervisor_url()
    payloads = [
        {"message": text, "source": MODULE_ID},
        {"text": text, "source": MODULE_ID},
        {"query": text, "source": MODULE_ID},
    ]
    last_error = None
    for payload in payloads:
        try:
            resp = requests.post(url, json=payload, timeout=120)
            try:
                body = resp.json()
            except Exception:
                body = resp.text
            if 200 <= resp.status_code < 300:
                reply = extract_reply(body)
                if reply:
                    return reply
            last_error = body
        except Exception as exc:
            last_error = str(exc)
    return f"Jarvis Telegram responder could not get a valid reply from supervisor. Details: {last_error}"

def tg_api(method: str, payload: Dict[str, Any], timeout: int = 60) -> Dict[str, Any]:
    token = get_token()
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured in config/module.local.env")
    url = f"https://api.telegram.org/bot{token}/{method}"
    resp = requests.post(url, json=payload, timeout=timeout)
    data = resp.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram API error for {method}: {data}")
    return data

def send_text(chat_id: int, text: str) -> None:
    chunk_limit = int(env("TELEGRAM_SEND_CHUNK_LIMIT", "3500") or "3500")
    text = text or "(empty reply)"
    chunks = [text[i:i+chunk_limit] for i in range(0, len(text), chunk_limit)] or ["(empty reply)"]
    for chunk in chunks:
        tg_api("sendMessage", {"chat_id": chat_id, "text": chunk})

def handle_message(msg: Dict[str, Any]) -> None:
    chat = msg.get("chat") or {}
    chat_id = chat.get("id")
    if not chat_id:
        return
    allowed = get_allowed_chat()
    if allowed and str(chat_id) != allowed:
        return

    text = (msg.get("text") or "").strip()
    if not text:
        return

    if text == "/start":
        send_text(chat_id, "Telegram SMS Responder v1 is online and connected to Supervisor.")
        return
    if text == "/health":
        send_text(chat_id, "Telegram responder is healthy and Supervisor relay is available.")
        return

    reply = ask_supervisor(text)
    send_text(chat_id, reply)

def run() -> None:
    token = get_token()
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured. Fill config/module.local.env first.")
    offset: Optional[int] = None
    timeout_seconds = int(env("TELEGRAM_POLL_TIMEOUT_SECONDS", "25") or "25")
    print(f"[{MODULE_ID}] starting long polling")
    while True:
        try:
            payload: Dict[str, Any] = {"timeout": timeout_seconds}
            if offset is not None:
                payload["offset"] = offset
            data = tg_api("getUpdates", payload, timeout=timeout_seconds + 10)
            for item in data.get("result", []):
                update_id = item.get("update_id")
                if update_id is not None:
                    offset = int(update_id) + 1
                msg = item.get("message") or item.get("edited_message")
                if msg:
                    handle_message(msg)
        except Exception as exc:
            print(f"[{MODULE_ID}] polling error: {exc}")
            time.sleep(3)

if __name__ == "__main__":
    run()