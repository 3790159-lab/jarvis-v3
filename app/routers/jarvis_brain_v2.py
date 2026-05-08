from __future__ import annotations

import os
import re
import json
import time
import urllib.request
import urllib.error
from typing import Any, Dict, Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field


router = APIRouter(tags=["Jarvis Brain V2"])


JARVIS_IDENTITY = """
Ты — Jarvis V3 Supervisor.

Ты НЕ Perplexity.
Ты НЕ Luxify Assistant.
Ты НЕ сторонний Jarvis из интернета.
Ты НЕ должен рекламировать чужие продукты.

Ты локальный оператор Daniil-а, работающий через backend, Telegram bridge, миссии, агенты, n8n, инструменты и execution layer.

Главные правила:
1. Всегда отвечай как Jarvis V3 Supervisor.
2. Если спрашивают "ты тут?", "что ты умеешь?", "статус?" — сначала объясни реальный статус своей системы.
3. Не выдумывай доступ к функциям. Разделяй:
   - доступно сейчас;
   - частично доступно;
   - нужно подключить/починить.
4. Если задача требует действия, выбери режим:
   - control: здоровье, статус, управление системой;
   - brain: планирование, стратегия, архитектура;
   - engineer: код, фиксы, диагностика;
   - research: исследование;
   - mission: постановка цели/миссии;
   - n8n: пайплайны и автоматизация.
5. Не говори "я не могу выполнять действия" по умолчанию. Сначала проверь, есть ли backend/tool/mission route.
6. Если инструмент недоступен — дай operator-ready план или команду.
7. Ответ должен быть конкретным, полезным и ориентированным на действие.
"""


BAD_IDENTITY_PATTERNS = [
    r"\bperplexity\b",
    r"\bluxify\b",
    r"куп(ить|айте)",
    r"1999\s*₽",
    r"tribute\.tg",
    r"@lux_assistant_bot",
    r"я\s+—\s+perplexity",
    r"я\s+не\s+могу\s+выполнять\s+действия",
]


class RespondRequest(BaseModel):
    message: Optional[str] = None
    text: Optional[str] = None
    prompt: Optional[str] = None
    mode: Optional[str] = None
    user_id: Optional[str] = None
    source: Optional[str] = "telegram"


class BrainResponse(BaseModel):
    ok: bool = True
    mode: str
    intent: str
    answer: str
    meta: Dict[str, Any] = Field(default_factory=dict)


def _text(req: RespondRequest) -> str:
    return (req.message or req.text or req.prompt or "").strip()


def _detect_intent(text: str, forced_mode: Optional[str] = None) -> str:
    if forced_mode:
        return forced_mode.strip().lower()

    t = text.lower()

    if any(x in t for x in ["ты тут", "джарвис тут", "статус", "health", "живой", "онлайн", "работаешь"]):
        return "control"

    if any(x in t for x in ["на что ты способен", "что ты умеешь", "возможности", "способен"]):
        return "capabilities"

    if t.startswith("/brain") or any(x in t for x in ["стратег", "план", "архитектур", "улучши модель", "продумай"]):
        return "brain"

    if t.startswith("/engineer") or any(x in t for x in ["код", "ошибка", "фикс", "исправь", "powershell", "python", "backend"]):
        return "engineer"

    if t.startswith("/research") or any(x in t for x in ["найди", "исследуй", "сравни", "источники"]):
        return "research"

    if any(x in t for x in ["n8n", "пайплайн", "workflow", "webhook", "автоматизац"]):
        return "n8n"

    if any(x in t for x in ["миссия", "цель", "запусти задачу", "выполни задачу"]):
        return "mission"

    return "auto"


def _service_snapshot() -> Dict[str, Any]:
    return {
        "identity": "Jarvis V3 Supervisor",
        "backend": "online_if_this_endpoint_responds",
        "router": "jarvis_brain_v2",
        "modes": ["control", "capabilities", "brain", "engineer", "research", "mission", "n8n", "auto"],
        "timestamp": int(time.time()),
    }


def _deterministic_answer(intent: str, text: str) -> Optional[str]:
    if intent == "control":
        snap = _service_snapshot()
        return (
            "✅ Jarvis V3 Supervisor тут.\n\n"
            "Текущий статус:\n"
            f"- Identity: {snap['identity']}\n"
            f"- Backend route: активен\n"
            f"- Router: {snap['router']}\n"
            "- Режим контроля: включен\n\n"
            "Я больше не должен отвечать как Perplexity или искать сторонний Jarvis. "
            "Если ты спрашиваешь про меня — я проверяю локальную систему, а не интернет."
        )

    if intent == "capabilities":
        return (
            "✅ Я Jarvis V3 Supervisor, твой локальный AI-оператор.\n\n"
            "Что доступно сейчас:\n"
            "- отвечать через Telegram/backend;\n"
            "- анализировать задачи и выбирать режим работы;\n"
            "- готовить код, фиксы и инструкции для backend;\n"
            "- помогать с миссиями, n8n, агентами и архитектурой;\n"
            "- давать self-check по своей роли и маршрутизации.\n\n"
            "Что должно подключаться отдельными слоями:\n"
            "- реальное выполнение миссий через mission API;\n"
            "- создание/изменение n8n workflow через n8n API;\n"
            "- долгосрочная память и ночной режим;\n"
            "- tool-calling для Gmail/Calendar/Sheets/файлов.\n\n"
            "Главное: я не Perplexity и не сторонний Jarvis. Я работаю как управляющий слой твоей системы."
        )

    return None


def _sanitize_answer(answer: str) -> str:
    lowered = answer.lower()
    if any(re.search(p, lowered, flags=re.IGNORECASE) for p in BAD_IDENTITY_PATTERNS):
        return (
            "⚠️ Ответ был заблокирован защитой identity guard, потому что модель попыталась ответить "
            "как сторонний ассистент или выдумала нерелевантную информацию.\n\n"
            "✅ Корректный ответ:\n"
            "Я Jarvis V3 Supervisor, локальный оператор этой системы. "
            "Я должен проверять backend, режимы, миссии и инструменты, а не искать сторонние продукты."
        )
    return answer.strip()


def _call_openai_compatible(prompt: str, intent: str) -> Optional[str]:
    api_key = os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY")
    base_url = (os.getenv("LLM_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
    model = os.getenv("LLM_MODEL") or "gpt-4o-mini"

    if not api_key:
        return None

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": JARVIS_IDENTITY},
            {"role": "system", "content": f"Current Jarvis mode: {intent}"},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.25,
    }

    req = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data["choices"][0]["message"]["content"]
    except Exception as e:
        return f"⚠️ LLM provider недоступен: {type(e).__name__}: {e}"


def _fallback_answer(intent: str, text: str) -> str:
    return (
        f"✅ Jarvis Brain V2 принял задачу.\n\n"
        f"Режим: {intent}\n\n"
        "Сейчас я могу безопасно обработать запрос на уровне router/fallback. "
        "Для полного выполнения нужно, чтобы соответствующий исполнитель был подключен к backend "
        "(mission executor, n8n executor, coding agent или research agent).\n\n"
        f"Запрос: {text}"
    )


@router.post("/api/respond", response_model=BrainResponse)
async def api_respond(req: RespondRequest) -> BrainResponse:
    text = _text(req)
    intent = _detect_intent(text, req.mode)

    deterministic = _deterministic_answer(intent, text)
    if deterministic:
        answer = deterministic
    else:
        llm_answer = _call_openai_compatible(text, intent)
        answer = llm_answer or _fallback_answer(intent, text)

    answer = _sanitize_answer(answer)

    return BrainResponse(
        ok=True,
        mode=intent,
        intent=intent,
        answer=answer,
        meta={
            "identity": "Jarvis V3 Supervisor",
            "guard": "enabled",
            "source": req.source,
        },
    )


@router.get("/api/jarvis/self-check")
async def jarvis_self_check() -> Dict[str, Any]:
    return {
        "ok": True,
        "identity": "Jarvis V3 Supervisor",
        "router": "jarvis_brain_v2",
        "anti_hallucination_guard": True,
        "bad_identity_blocklist": BAD_IDENTITY_PATTERNS,
        "snapshot": _service_snapshot(),
    }


@router.get("/api/jarvis/identity")
async def jarvis_identity() -> Dict[str, Any]:
    return {
        "identity": "Jarvis V3 Supervisor",
        "not": ["Perplexity", "Luxify Assistant", "third-party Jarvis"],
        "role": "local AI operator for backend, missions, n8n, agents and tools",
    }