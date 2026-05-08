from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict

import requests
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

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

def configured_secret(value: str) -> bool:
    value = (value or "").strip()
    return bool(value and not value.startswith("PUT_REAL"))

def module_config() -> Dict[str, Any]:
    return {
        "module_id": MODULE_ID,
        "version": MODULE_VERSION,
        "module_dir": str(MODULE_DIR),
        "module_port": int(env("MODULE_PORT", "8110") or "8110"),
        "supervisor_base_url": env("SUPERVISOR_BASE_URL", "http://127.0.0.1:8015"),
        "supervisor_response_path": env("SUPERVISOR_RESPONSE_PATH", "/api/respond"),
        "telegram_token_configured": configured_secret(env("TELEGRAM_BOT_TOKEN", "")),
        "allowed_chat_configured": configured_secret(env("TELEGRAM_ALLOWED_CHAT_ID", "")),
        "env_files": [str(p) for p in ENV_FILES],
    }

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

def ask_supervisor(text: str) -> Dict[str, Any]:
    base = env("SUPERVISOR_BASE_URL", "http://127.0.0.1:8015").rstrip("/")
    path = env("SUPERVISOR_RESPONSE_PATH", "/api/respond")
    url = f"{base}{path}"
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
                return {"ok": True, "status_code": resp.status_code, "reply": extract_reply(body), "raw": body}
            last_error = {"status_code": resp.status_code, "body": body}
        except Exception as exc:
            last_error = {"status_code": 0, "body": str(exc)}
    return {"ok": False, "status_code": (last_error or {}).get("status_code", 0), "reply": "", "raw": last_error}

class RespondRequest(BaseModel):
    text: str

app = FastAPI(title="Telegram SMS Responder v1", version=MODULE_VERSION)

@app.get("/health")
def health() -> Dict[str, Any]:
    return {"status": "healthy", "service": MODULE_ID, "version": MODULE_VERSION, "config": module_config()}

@app.get("/config")
def config() -> Dict[str, Any]:
    return {"status": "ok", "service": MODULE_ID, "config": module_config()}

@app.post("/respond-preview")
def respond_preview(req: RespondRequest) -> Dict[str, Any]:
    result = ask_supervisor(req.text)
    if not result.get("ok"):
        raise HTTPException(status_code=502, detail=result.get("raw"))
    return {"status": "ok", "service": MODULE_ID, "reply": result.get("reply"), "supervisor_status_code": result.get("status_code")}