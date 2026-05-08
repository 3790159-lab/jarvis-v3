import hashlib
import json
import os
import threading
import time
from collections import OrderedDict
from typing import Any, Dict, Tuple

import requests

from app.services.identity_core import get_system_prompt
from app.services.policy import get_policy


def env_str(name: str, default: str) -> str:
    value = os.getenv(name, default)
    if value is None:
        return default
    return str(value).strip()


def env_int(name: str, default: int) -> int:
    try:
        return int(env_str(name, str(default)))
    except Exception:
        return default


def env_float(name: str, default: float) -> float:
    try:
        return float(env_str(name, str(default)))
    except Exception:
        return default


def env_bool(name: str, default: bool) -> bool:
    raw = env_str(name, "true" if default else "false").lower()
    return raw in ("1", "true", "yes", "on")


OLLAMA_ENABLED = env_bool("OLLAMA_ENABLED", True)
OLLAMA_BASE_URL = env_str("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
OLLAMA_MODEL = env_str("OLLAMA_MODEL", "llama3.2:latest")
OLLAMA_TIMEOUT_SECONDS = env_float("OLLAMA_TIMEOUT_SECONDS", 9.0)
OLLAMA_CONNECT_TIMEOUT_SECONDS = env_float("OLLAMA_CONNECT_TIMEOUT_SECONDS", 2.5)
OLLAMA_NUM_PREDICT = env_int("OLLAMA_NUM_PREDICT", 260)
OLLAMA_TEMPERATURE = env_float("OLLAMA_TEMPERATURE", 0.2)
OLLAMA_KEEP_ALIVE = env_str("OLLAMA_KEEP_ALIVE", "10m")
MAX_INPUT_CHARS = env_int("MAX_INPUT_CHARS", 2200)
CACHE_ENABLED = env_bool("CACHE_ENABLED", True)
CACHE_MAX_ITEMS = env_int("CACHE_MAX_ITEMS", 200)
CACHE_TTL_SECONDS = env_int("CACHE_TTL_SECONDS", 300)

SYSTEM_PROMPT = get_system_prompt("supervisor", lang="en")

ROUTER_PROMPT = """Classify the user message.
Return strict JSON only:
{"route":"chat|capabilities|status|planning|technical|task|mission","reason":"..."}"""

_session = requests.Session()
_adapter = requests.adapters.HTTPAdapter(pool_connections=10, pool_maxsize=10, max_retries=0)
_session.mount("http://", _adapter)
_session.mount("https://", _adapter)

_cache_lock = threading.Lock()
_cache: "OrderedDict[str, Tuple[float, Dict[str, Any]]]" = OrderedDict()
_stats_lock = threading.Lock()
_stats = {
    "requests_total": 0,
    "llm_success_total": 0,
    "llm_fallback_total": 0,
    "cache_hit_total": 0,
    "cache_miss_total": 0,
    "last_route": "unknown",
    "last_mode": "unknown",
    "last_error": "",
    "last_duration_ms": 0,
}


def now_ts() -> float:
    return time.time()


def incr_stat(name: str, delta: int = 1) -> None:
    with _stats_lock:
        _stats[name] = int(_stats.get(name, 0)) + delta


def update_stats(**kwargs: Any) -> None:
    with _stats_lock:
        for key, value in kwargs.items():
            _stats[key] = value


def get_stats() -> Dict[str, Any]:
    with _stats_lock:
        return dict(_stats)


def detect_language(text: str) -> str:
    for ch in text or "":
        if "\u0400" <= ch <= "\u04FF":
            return "ru"
    return "en"


def normalize_text(text: str) -> str:
    return " ".join((text or "").strip().split())


def normalize_lower(text: str) -> str:
    return normalize_text(text).lower()


def trim_input(text: str) -> str:
    text = (text or "").strip()
    if len(text) <= MAX_INPUT_CHARS:
        return text
    return text[:MAX_INPUT_CHARS] + "\n\n[truncated]"


def make_cache_key(text: str) -> str:
    return hashlib.sha256(normalize_lower(text).encode("utf-8")).hexdigest()


def cache_get(key: str) -> Dict[str, Any] | None:
    if not CACHE_ENABLED:
        return None
    with _cache_lock:
        item = _cache.get(key)
        if not item:
            incr_stat("cache_miss_total")
            return None
        ts, value = item
        if now_ts() - ts > CACHE_TTL_SECONDS:
            try:
                del _cache[key]
            except Exception:
                pass
            incr_stat("cache_miss_total")
            return None
        _cache.move_to_end(key)
        incr_stat("cache_hit_total")
        return dict(value)


def cache_put(key: str, value: Dict[str, Any]) -> None:
    if not CACHE_ENABLED:
        return
    with _cache_lock:
        _cache[key] = (now_ts(), dict(value))
        _cache.move_to_end(key)
        while len(_cache) > CACHE_MAX_ITEMS:
            _cache.popitem(last=False)


def build_result(reply: str, mode: str, source: str, confidence: float, route: str) -> Dict[str, Any]:
    return {
        "reply": reply.strip(),
        "mode": mode,
        "source": source,
        "confidence": float(confidence),
        "route": route,
    }


def quick_route(user_text: str) -> Dict[str, Any] | None:
    t = trim_input(user_text)
    tl = normalize_lower(t)
    is_ru = detect_language(t) == "ru"

    if not t:
        return build_result("Пустое сообщение. Напишите запрос ещё раз." if is_ru else "Empty message. Please send your request again.", "smart_local", "quick_rule", 0.98, "chat")

    if is_ru and any(x in tl for x in ["привет", "здравствуй", "добрый день", "добрый вечер"]):
        return build_result("Привет! Я работаю стабильно.\n\nСейчас активен Phase 5 supervisor-memory режим: быстрые правила, planner, dependencies, priorities, queue, worker pool и fallback.", "smart_local", "quick_rule", 0.97, "chat")

    if is_ru and ("что ты умеешь" in tl or "каковы твои возможности" in tl or "что умеешь" in tl):
        return build_result(
            "Сейчас доступны:\n- Telegram-бот;\n- backend /api/respond;\n- быстрые стабильные ответы;\n- planner;\n- missions storage;\n- dependencies и priorities;\n- queue + worker pool;\n- базовое execution layer;\n- mission memory/context;\n- policy/retry/backoff;\n- Ollama-слой с fallback.",
            "smart_local",
            "quick_rule",
            0.98,
            "capabilities",
        )

    return None


def smart_local_fallback(user_text: str) -> Dict[str, Any]:
    t = trim_input(user_text)
    tl = normalize_lower(t)
    is_ru = detect_language(t) == "ru"

    if is_ru:
        if any(x in tl for x in ["план", "этап", "roadmap", "дорожн", "следующ"]):
            return build_result("Безопасный следующий план: memory/context -> dependencies/priorities -> worker pool -> richer mission lifecycle -> tool adapters.", "smart_local", "local_fallback", 0.87, "planning")
        if any(x in tl for x in ["мисси", "цель", "goal", "task", "задач"]):
            return build_result("Я распознал запрос как mission/task intent. Система может создать mission draft, сохранить контекст, поставить задачи в очередь и выполнить ready-задачи по зависимостям.", "smart_local", "local_fallback", 0.86, "mission")
        return build_result(f"Я получил ваше сообщение: {t}\n\nСейчас система работает в supervisor-memory режиме с queue, worker pool и fallback.", "smart_local", "local_fallback", 0.80, "chat")

    return build_result(f"Message received: {t}\n\nSupervisor fallback mode is active.", "smart_local", "local_fallback", 0.78, "chat")


def call_ollama(prompt: str, system: str = SYSTEM_PROMPT) -> str:
    url = f"{OLLAMA_BASE_URL}/api/generate"
    payload = {
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "system": system,
        "stream": False,
        "keep_alive": OLLAMA_KEEP_ALIVE,
        "options": {
            "temperature": OLLAMA_TEMPERATURE,
            "num_predict": OLLAMA_NUM_PREDICT,
        },
    }
    response = _session.post(url, json=payload, timeout=(OLLAMA_CONNECT_TIMEOUT_SECONDS, OLLAMA_TIMEOUT_SECONDS))
    response.raise_for_status()
    data = response.json()
    text = (data.get("response") or "").strip()
    if not text:
        raise RuntimeError("Empty Ollama response")
    return text


def classify_route(user_text: str) -> Dict[str, Any]:
    raw = call_ollama(prompt=f"User message:\n{trim_input(user_text)}\n\nReturn JSON only.", system=ROUTER_PROMPT)
    try:
        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end >= start:
            raw = raw[start:end + 1]
        data = json.loads(raw)
    except Exception:
        return {"route": "chat", "reason": "classification_parse_fallback"}

    route = str(data.get("route", "chat")).strip().lower()
    if route not in {"chat", "capabilities", "status", "planning", "technical", "task", "mission"}:
        route = "chat"
    return {"route": route, "reason": str(data.get("reason", "")).strip() or "classified"}


def build_route_prompt(user_text: str, route: str) -> str:
    if route == "planning":
        return f"Provide a phased practical roadmap with stability-first sequencing.\n\nUser: {trim_input(user_text)}"
    if route == "technical":
        return f"Answer with robust architecture, performance and reliability guidance.\n\nUser: {trim_input(user_text)}"
    if route == "mission":
        return f"Answer as a supervisor architect with lifecycle and execution thinking.\n\nUser: {trim_input(user_text)}"
    return f"User: {trim_input(user_text)}"


def handle_with_llm(user_text: str) -> Dict[str, Any]:
    route_data = classify_route(user_text)
    route = route_data["route"]
    reply = call_ollama(prompt=build_route_prompt(user_text, route), system=SYSTEM_PROMPT)
    return build_result(reply, "ollama_llm", "ollama", 0.93, route)


def ollama_health() -> Dict[str, Any]:
    try:
        response = _session.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=(OLLAMA_CONNECT_TIMEOUT_SECONDS, min(OLLAMA_TIMEOUT_SECONDS, 4)))
        response.raise_for_status()
        data = response.json()
        models = [m.get("name", "") for m in data.get("models", []) if isinstance(m, dict)]
        return {
            "enabled": OLLAMA_ENABLED,
            "policy_llm_enabled": bool(get_policy().get("llm_enabled", True)),
            "reachable": True,
            "configured_model": OLLAMA_MODEL,
            "available_models": models,
            "configured_model_present": OLLAMA_MODEL in models if models else False,
        }
    except Exception as e:
        return {
            "enabled": OLLAMA_ENABLED,
            "policy_llm_enabled": bool(get_policy().get("llm_enabled", True)),
            "reachable": False,
            "configured_model": OLLAMA_MODEL,
            "available_models": [],
            "configured_model_present": False,
            "error": f"{type(e).__name__}: {e}",
        }


def route_message(user_text: str) -> Dict[str, Any]:
    started = time.perf_counter()
    incr_stat("requests_total")
    normalized = trim_input(user_text)
    cache_key = make_cache_key(normalized)

    quick = quick_route(normalized)
    if quick is not None:
        update_stats(last_route=quick["route"], last_mode=quick["mode"], last_error="", last_duration_ms=int((time.perf_counter() - started) * 1000))
        return quick

    cached = cache_get(cache_key)
    if cached is not None:
        update_stats(last_route=cached.get("route", "chat"), last_mode=cached.get("mode", "cache"), last_error="", last_duration_ms=int((time.perf_counter() - started) * 1000))
        return cached

    policy = get_policy()
    if not OLLAMA_ENABLED or not bool(policy.get("llm_enabled", True)):
        result = smart_local_fallback(normalized)
        cache_put(cache_key, result)
        update_stats(last_route=result["route"], last_mode=result["mode"], last_error="", last_duration_ms=int((time.perf_counter() - started) * 1000))
        return result

    try:
        result = handle_with_llm(normalized)
        cache_put(cache_key, result)
        incr_stat("llm_success_total")
        update_stats(last_route=result["route"], last_mode=result["mode"], last_error="", last_duration_ms=int((time.perf_counter() - started) * 1000))
        return result
    except Exception as e:
        incr_stat("llm_fallback_total")
        result = smart_local_fallback(normalized)
        cache_put(cache_key, result)
        update_stats(last_route=result["route"], last_mode=result["mode"], last_error=f"{type(e).__name__}: {e}", last_duration_ms=int((time.perf_counter() - started) * 1000))
        return result