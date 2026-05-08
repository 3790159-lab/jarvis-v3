from __future__ import annotations

import os
import json
import time
import urllib.request
import urllib.error
from typing import Any, Dict, Optional, List


JARVIS_SYSTEM_PROMPT = """
Ты — Jarvis V3 Supervisor.

Ты локальный AI-оператор Daniil-а.
Ты управляешь задачами через backend, Telegram, миссии, n8n, агентов и инструменты.

Ты НЕ Perplexity.
Ты НЕ Luxify Assistant.
Ты НЕ сторонний Jarvis из интернета.

Правила:
1. Отвечай как Jarvis V3 Supervisor.
2. Не выдумывай доступ к инструментам.
3. Если инструмент недоступен — дай operator-ready план.
4. Claude используй для архитектуры, кода, анализа, рефакторинга, ревью.
5. OpenAI используй для быстрых ответов, диалогов, классификации, кратких решений.
6. Local fallback используй, если внешние провайдеры недоступны.
7. Всегда давай практический следующий шаг.
"""


CLAUDE_HINT = """
Ты работаешь как Claude Architect/Coding Agent внутри Jarvis.
Твоя роль:
- глубокая архитектура;
- устойчивость backend;
- рефакторинг;
- анализ ошибок;
- production-grade код;
- оценка рисков;
- улучшение multi-agent системы.
"""


OPENAI_HINT = """
Ты работаешь как быстрый reasoning/dialogue agent внутри Jarvis.
Твоя роль:
- быстрый ответ;
- классификация задачи;
- краткий план;
- UX ответа;
- объяснение простым языком.
"""


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def detect_intent(text: str) -> str:
    t = (text or "").lower()

    if any(x in t for x in ["код", "исправь", "фикс", "ошибка", "traceback", "powershell", "python", "backend", "api", "router"]):
        return "engineer"

    if any(x in t for x in ["архитект", "продумай", "стратег", "улучши систему", "надёжность", "устойчивость", "рефакторинг"]):
        return "architect"

    if any(x in t for x in ["n8n", "workflow", "webhook", "пайплайн", "автоматизац"]):
        return "n8n"

    if any(x in t for x in ["миссия", "цель", "запусти", "выполни задачу"]):
        return "mission"

    if any(x in t for x in ["статус", "health", "ты тут", "джарвис тут", "работаешь"]):
        return "control"

    if any(x in t for x in ["найди", "исследуй", "сравни", "источники"]):
        return "research"

    return "chat"


def choose_provider(intent: str, text: str) -> str:
    preferred = _env("JARVIS_FORCE_PROVIDER", "").lower()
    if preferred in {"anthropic", "claude", "openai", "local"}:
        return "anthropic" if preferred == "claude" else preferred

    if intent in {"engineer", "architect", "n8n"}:
        if _env("ANTHROPIC_API_KEY"):
            return "anthropic"
        if _env("LLM_API_KEY") or _env("OPENAI_API_KEY"):
            return "openai"
        return "local"

    if intent in {"chat", "control", "mission", "research"}:
        if _env("LLM_API_KEY") or _env("OPENAI_API_KEY"):
            return "openai"
        if _env("ANTHROPIC_API_KEY"):
            return "anthropic"
        return "local"

    return "local"


def call_openai(text: str, intent: str, timeout: int = 60) -> Dict[str, Any]:
    api_key = _env("LLM_API_KEY") or _env("OPENAI_API_KEY")
    base_url = (_env("LLM_BASE_URL", "https://api.openai.com/v1")).rstrip("/")
    model = _env("OPENAI_MODEL") or _env("LLM_MODEL", "gpt-4o-mini")

    if not api_key:
        raise RuntimeError("OPENAI/LLM API key is missing")

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": JARVIS_SYSTEM_PROMPT},
            {"role": "system", "content": OPENAI_HINT},
            {"role": "system", "content": f"Jarvis intent: {intent}"},
            {"role": "user", "content": text},
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

    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    return {
        "provider": "openai",
        "model": model,
        "answer": data["choices"][0]["message"]["content"],
        "raw_ok": True,
    }


def call_anthropic(text: str, intent: str, timeout: int = 90) -> Dict[str, Any]:
    api_key = _env("ANTHROPIC_API_KEY")
    model = _env("ANTHROPIC_MODEL", "claude-3-5-sonnet-20241022")

    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is missing")

    payload = {
        "model": model,
        "max_tokens": int(_env("ANTHROPIC_MAX_TOKENS", "4000")),
        "temperature": 0.2,
        "system": JARVIS_SYSTEM_PROMPT + "\n\n" + CLAUDE_HINT + f"\n\nJarvis intent: {intent}",
        "messages": [
            {"role": "user", "content": text}
        ],
    }

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        method="POST",
    )

    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    parts = data.get("content", [])
    answer = "\n".join([p.get("text", "") for p in parts if p.get("type") == "text"]).strip()

    return {
        "provider": "anthropic",
        "model": model,
        "answer": answer,
        "raw_ok": True,
    }


def local_fallback(text: str, intent: str, error: Optional[str] = None) -> Dict[str, Any]:
    answer = (
        "✅ Jarvis Multi-AI Orchestrator принял задачу.\n\n"
        f"Режим: {intent}\n"
        "Провайдер: local fallback\n\n"
        "Внешний AI-провайдер сейчас недоступен или не выбран. "
        "Но задача не потеряна: я могу выдать безопасный operator-ready план, кодовый блок или диагностический сценарий.\n\n"
        f"Запрос: {text}"
    )

    if error:
        answer += f"\n\n⚠️ Причина fallback: {error}"

    return {
        "provider": "local",
        "model": "fallback",
        "answer": answer,
        "raw_ok": True,
    }


def run_multi_ai(text: str, mode: Optional[str] = None) -> Dict[str, Any]:
    started = time.time()
    intent = mode or detect_intent(text)
    primary = choose_provider(intent, text)

    attempts: List[str] = []
    errors: List[str] = []

    provider_order = []
    if primary == "anthropic":
        provider_order = ["anthropic", "openai", "local"]
    elif primary == "openai":
        provider_order = ["openai", "anthropic", "local"]
    else:
        provider_order = ["local"]

    for provider in provider_order:
        attempts.append(provider)
        try:
            if provider == "anthropic":
                result = call_anthropic(text, intent)
            elif provider == "openai":
                result = call_openai(text, intent)
            else:
                result = local_fallback(text, intent, "; ".join(errors) if errors else None)

            result["ok"] = True
            result["intent"] = intent
            result["attempts"] = attempts
            result["errors"] = errors
            result["elapsed_seconds"] = round(time.time() - started, 3)
            result["identity"] = "Jarvis V3 Supervisor"
            return result

        except Exception as e:
            errors.append(f"{provider}: {type(e).__name__}: {e}")

    return local_fallback(text, intent, "; ".join(errors))


def health() -> Dict[str, Any]:
    return {
        "ok": True,
        "service": "jarvis_multi_ai_orchestrator_v1",
        "providers": {
            "openai_configured": bool(_env("LLM_API_KEY") or _env("OPENAI_API_KEY")),
            "anthropic_configured": bool(_env("ANTHROPIC_API_KEY")),
            "llm_base_url": _env("LLM_BASE_URL", "https://api.openai.com/v1"),
            "openai_model": _env("OPENAI_MODEL") or _env("LLM_MODEL", "gpt-4o-mini"),
            "anthropic_model": _env("ANTHROPIC_MODEL", "claude-3-5-sonnet-20241022"),
        },
        "routing": {
            "engineer": "claude_first",
            "architect": "claude_first",
            "n8n": "claude_first",
            "chat": "openai_first",
            "control": "openai_first",
            "fallback": "local",
        },
    }