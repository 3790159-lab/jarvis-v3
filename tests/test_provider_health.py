from __future__ import annotations

"""Phase 9: Provider health auto-fallback tests.

Verifies:
1. check_*_health() returns bool based on API response
2. get_healthy_provider() follows priority: anthropic → openai → ollama
3. check_all_providers() returns dict for all three
4. Cache works (doesn't re-check within TTL)
5. invalidate_provider_cache() removes cached entry
6. /status command sends provider status message
"""

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.provider_health import (
    check_all_providers,
    get_healthy_provider,
    invalidate_provider_cache,
    is_provider_healthy,
    PROVIDER_PRIORITY,
)
import app.services.provider_health as ph


# ---------------------------------------------------------------------------
# check_anthropic_health
# ---------------------------------------------------------------------------

def test_check_anthropic_returns_false_when_no_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert ph.check_anthropic_health() is False


def test_check_anthropic_returns_true_on_valid_response(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(ph, "_post_json", lambda *a, **kw: {"content": [{"text": "pong"}], "id": "msg_01"})

    assert ph.check_anthropic_health() is True


def test_check_anthropic_returns_false_on_error_response(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(ph, "_post_json", lambda *a, **kw: {"error": "rate_limit"})

    assert ph.check_anthropic_health() is False


# ---------------------------------------------------------------------------
# check_openai_health
# ---------------------------------------------------------------------------

def test_check_openai_returns_false_when_no_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert ph.check_openai_health() is False


def test_check_openai_returns_true_on_valid_response(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(ph, "_get_json", lambda *a, **kw: {"data": [{"id": "gpt-4"}], "object": "list"})

    assert ph.check_openai_health() is True


def test_check_openai_returns_false_on_error(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(ph, "_get_json", lambda *a, **kw: {"error": "unauthorized"})

    assert ph.check_openai_health() is False


# ---------------------------------------------------------------------------
# check_ollama_health
# ---------------------------------------------------------------------------

def test_check_ollama_returns_false_on_connection_refused(monkeypatch):
    monkeypatch.setattr(ph, "_get_json", lambda *a, **kw: {"error": "Connection refused"})
    assert ph.check_ollama_health() is False


def test_check_ollama_returns_true_on_valid_response(monkeypatch):
    monkeypatch.setattr(ph, "_get_json", lambda *a, **kw: {"models": [{"name": "llama3.2"}]})
    assert ph.check_ollama_health() is True


# ---------------------------------------------------------------------------
# get_healthy_provider — priority order
# ---------------------------------------------------------------------------

def test_get_healthy_provider_returns_anthropic_first(monkeypatch):
    monkeypatch.setattr(ph, "_CHECK_FNS", {
        "anthropic": lambda: True,
        "openai": lambda: True,
        "ollama": lambda: True,
    })
    ph._health_cache.clear()

    provider = ph.get_healthy_provider(use_cache=False)
    assert provider == "anthropic"


def test_get_healthy_provider_falls_back_to_openai(monkeypatch):
    monkeypatch.setattr(ph, "_CHECK_FNS", {
        "anthropic": lambda: False,
        "openai": lambda: True,
        "ollama": lambda: False,
    })
    ph._health_cache.clear()

    provider = ph.get_healthy_provider(use_cache=False)
    assert provider == "openai"


def test_get_healthy_provider_falls_back_to_ollama(monkeypatch):
    monkeypatch.setattr(ph, "_CHECK_FNS", {
        "anthropic": lambda: False,
        "openai": lambda: False,
        "ollama": lambda: True,
    })
    ph._health_cache.clear()

    provider = ph.get_healthy_provider(use_cache=False)
    assert provider == "ollama"


def test_get_healthy_provider_returns_none_when_all_down(monkeypatch):
    monkeypatch.setattr(ph, "_CHECK_FNS", {
        "anthropic": lambda: False,
        "openai": lambda: False,
        "ollama": lambda: False,
    })
    ph._health_cache.clear()

    provider = ph.get_healthy_provider(use_cache=False)
    assert provider is None


# ---------------------------------------------------------------------------
# check_all_providers
# ---------------------------------------------------------------------------

def test_check_all_providers_returns_dict_for_all(monkeypatch):
    monkeypatch.setattr(ph, "_CHECK_FNS", {
        "anthropic": lambda: True,
        "openai": lambda: False,
        "ollama": lambda: True,
    })
    ph._health_cache.clear()

    result = ph.check_all_providers(use_cache=False)

    assert set(result.keys()) == {"anthropic", "openai", "ollama"}
    assert result["anthropic"] is True
    assert result["openai"] is False
    assert result["ollama"] is True


# ---------------------------------------------------------------------------
# Cache + invalidation
# ---------------------------------------------------------------------------

def test_cache_prevents_recheck(monkeypatch):
    call_count = {"n": 0}

    def mock_check():
        call_count["n"] += 1
        return True

    monkeypatch.setattr(ph, "_CHECK_FNS", {"anthropic": mock_check, "openai": mock_check, "ollama": mock_check})
    ph._health_cache.clear()

    ph.is_provider_healthy("anthropic", use_cache=False)  # first call, no cache
    ph.is_provider_healthy("anthropic", use_cache=True)   # should hit cache

    assert call_count["n"] == 1  # second call used cache


def test_invalidate_clears_cache(monkeypatch):
    ph._health_cache["anthropic"] = (True, time.time())

    invalidate_provider_cache("anthropic")

    with ph._cache_lock:
        assert "anthropic" not in ph._health_cache


# ---------------------------------------------------------------------------
# /status command integration
# ---------------------------------------------------------------------------

def test_status_command_shows_providers(monkeypatch):
    import tools.jarvis_smart_telegram_control as mod

    monkeypatch.setattr(ph, "_CHECK_FNS", {
        "anthropic": lambda: True,
        "openai": lambda: True,
        "ollama": lambda: False,
    })
    ph._health_cache.clear()

    sent = []
    monkeypatch.setattr(mod, "send", lambda cid, txt, **kw: sent.append(txt))

    state = mod.default_state()
    mod.handle_command("123", "/status", "", state)

    assert len(sent) == 1
    text = sent[0]
    assert "anthropic" in text.lower()
    assert "openai" in text.lower()
    assert "ollama" in text.lower()
    assert "✅" in text
    assert "🔴" in text
