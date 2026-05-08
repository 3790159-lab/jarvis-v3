from __future__ import annotations

"""Provider health checks and auto-fallback logic for AI providers.

Priority order: anthropic → openai → ollama

Usage:
  from app.services.provider_health import get_healthy_provider, check_all_providers

  provider = get_healthy_provider()  # returns first healthy provider name
  status = check_all_providers()     # returns dict of {provider: bool}
"""

import json
import os
import threading
import time
import urllib.error
import urllib.request
from typing import Dict, Optional

PROVIDER_PRIORITY = ["anthropic", "openai", "ollama"]

_OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")

# Thread-safe cache: {provider: (is_healthy, checked_at)}
_health_cache: Dict[str, tuple[bool, float]] = {}
_cache_lock = threading.Lock()
_CACHE_TTL = 60.0  # seconds


def _post_json(url: str, payload: Dict, headers: Dict, timeout: int = 8) -> Dict:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={**headers, "Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", errors="replace"))
    except Exception as e:
        return {"error": str(e)}


def _get_json(url: str, headers: Dict, timeout: int = 8) -> Dict:
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", errors="replace"))
    except Exception as e:
        return {"error": str(e)}


def check_anthropic_health() -> bool:
    key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not key:
        return False
    resp = _post_json(
        "https://api.anthropic.com/v1/messages",
        {"model": "claude-haiku-4-5-20251001", "max_tokens": 5, "messages": [{"role": "user", "content": "ping"}]},
        {"x-api-key": key, "anthropic-version": "2023-06-01"},
        timeout=10,
    )
    return "error" not in resp and ("content" in resp or "id" in resp)


def check_openai_health() -> bool:
    key = os.getenv("OPENAI_API_KEY", "").strip()
    if not key:
        return False
    resp = _get_json(
        "https://api.openai.com/v1/models",
        {"Authorization": f"Bearer {key}"},
        timeout=10,
    )
    return "error" not in resp and ("data" in resp or "object" in resp)


def check_ollama_health() -> bool:
    resp = _get_json(f"{_OLLAMA_BASE_URL}/api/tags", {}, timeout=5)
    return "error" not in resp and ("models" in resp or "tags" in resp or resp == {})


_CHECK_FNS = {
    "anthropic": check_anthropic_health,
    "openai": check_openai_health,
    "ollama": check_ollama_health,
}


def _is_cached_healthy(provider: str) -> Optional[bool]:
    with _cache_lock:
        entry = _health_cache.get(provider)
        if entry and (time.time() - entry[1]) < _CACHE_TTL:
            return entry[0]
    return None


def _update_cache(provider: str, healthy: bool) -> None:
    with _cache_lock:
        _health_cache[provider] = (healthy, time.time())


def is_provider_healthy(provider: str, use_cache: bool = True) -> bool:
    if use_cache:
        cached = _is_cached_healthy(provider)
        if cached is not None:
            return cached
    fn = _CHECK_FNS.get(provider)
    if fn is None:
        return False
    result = fn()
    _update_cache(provider, result)
    return result


def check_all_providers(use_cache: bool = True) -> Dict[str, bool]:
    return {p: is_provider_healthy(p, use_cache=use_cache) for p in PROVIDER_PRIORITY}


def get_healthy_provider(use_cache: bool = True) -> Optional[str]:
    """Return the first healthy provider by priority, or None if all down."""
    for provider in PROVIDER_PRIORITY:
        if is_provider_healthy(provider, use_cache=use_cache):
            return provider
    return None


def invalidate_provider_cache(provider: str) -> None:
    with _cache_lock:
        _health_cache.pop(provider, None)


# Background health monitor — checks every 60s in daemon thread
_monitor_thread: Optional[threading.Thread] = None
_monitor_running = False


def start_health_monitor(interval: int = 60) -> None:
    global _monitor_thread, _monitor_running
    if _monitor_running:
        return
    _monitor_running = True

    def _loop():
        while _monitor_running:
            try:
                check_all_providers(use_cache=False)
            except Exception:
                pass
            time.sleep(interval)

    _monitor_thread = threading.Thread(target=_loop, daemon=True, name="provider-health-monitor")
    _monitor_thread.start()


def stop_health_monitor() -> None:
    global _monitor_running
    _monitor_running = False
