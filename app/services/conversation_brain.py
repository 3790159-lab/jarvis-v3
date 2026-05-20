from __future__ import annotations
import app.core.bootstrap_utf8  # noqa: F401

import os
from typing import Any

import requests

from app.services.conversation_memory import ConversationMemory
from app.services.identity_core import get_system_prompt

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
    return "\n".join(lines)


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
            "Ты совмещаешь роль AI-ассистента и supervisor-системы.\n\n"
            f"Контекст:\n{build_context_block()}\n\n"
            f"Сообщение пользователя:\n{text}"
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
                        "content": get_system_prompt("supervisor"),
                    },
                    {
                        "role": "user",
                        "content": f"Контекст:\n{build_context_block()}\n\nСообщение пользователя:\n{text}",
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
            "reply": "Это похоже на задачу — переключаюсь в режим работы. Жду формулировку, что нужно сделать.",
            "intent": "task",
            "mode": "router",
            "source": "local_router",
            "confidence": 0.9,
        }

    if intent == "status":
        return {
            "reply": "Похоже на запрос статуса. Уточните, по какой системе нужна сводка, и я её соберу.",
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
